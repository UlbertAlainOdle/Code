import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
import httpx
import pytest

from saya_app.routers import qq as routes
from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore
from saya_app.services.qq_controls import QQControlsBusy
from saya_app.services.state_storage import StateSaveError


def test_old_web_form_preserves_omitted_delivery_options_with_current_revision(tmp_path):
    async def run():
        bridge=bridge_at(tmp_path,delivery_mode='legacy',bubble_strategy='natural',reply_reference=False,bubble_chars=180,reply_max_messages=2)
        await bridge.start()
        app=FastAPI();app.include_router(routes.router)
        with patch.object(routes,'qq_bridge',bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,
                headers={'Authorization':'Bearer admin-secret'}) as web:
                original=(await web.get('/api/qq/settings')).json()
                old={key:value for key,value in original['settings'].items()
                     if key not in {'delivery_mode','bubble_strategy','reply_reference','bubble_chars','reply_max_messages'}}
                saved=await web.put('/api/qq/settings',json=old|{'cooldown_seconds':4,'revision':original['revision']})
                assert saved.status_code==200
                values=saved.json()['settings']
                assert values['cooldown_seconds']==4 and values['delivery_mode']=='legacy'
                assert values['bubble_strategy']=='natural'
                assert values['reply_reference'] is False and values['bubble_chars']==180 and values['reply_max_messages']==2
                stale=await web.put('/api/qq/settings',json=old|{'revision':original['revision']})
                assert stale.status_code==409
        await bridge.stop()
    asyncio.run(run())


ORIGIN = "http://127.0.0.1:8000"
HEADERS = {"Origin": ORIGIN, "X-Saya-QQ-UI": "1"}


class Client:
    def __init__(self):
        self.sent = []

    async def call(self, action, payload):
        return [] if action == "get_group_list" else {"user_id": 10000}

    async def close(self):
        pass

    async def send(self, message, text):
        self.sent.append((message, text))


def bridge_at(tmp_path, **changes):
    settings = QQSettings(**(dict(enabled=True, bot_id="10000", access_token="outbound-secret",
        webhook_token="inbound-secret", admin_token="admin-secret", owner_ids={"20000"},
        shared_memory=True) | changes))
    return QQBridge(settings, QQStore(tmp_path / "bridge.sqlite3"), SimpleNamespace(settings=settings), Client())


def test_apply_persist_pause_resume_and_restart(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path)
        await bridge.start()
        original_task = bridge.task
        controls = bridge.controls()["settings"] | {"shared_memory": False, "history_turns": 7}
        await bridge.update_controls(controls)
        assert bridge.task is original_task  # Active consumer is kept for ordinary toggles.
        assert not bridge.responder.settings.shared_memory
        assert bridge.responder.settings.history_turns == 7
        assert json.loads(bridge.controls_store.path.read_text()) == controls
        assert "secret" not in json.dumps(bridge.controls())
        await bridge.update_controls(controls | {"enabled": False})
        assert bridge.task is None and not bridge.status()["running"]
        restarted = bridge_at(tmp_path)
        await restarted.start()
        assert not restarted.settings.enabled and restarted.task is None
        assert restarted.settings.history_turns == 7
        with patch("saya_app.services.qq_bridge.OneBotClient", return_value=Client()):
            await restarted.update_controls(controls)
        assert restarted.task and restarted.status()["running"]
        assert restarted.responder.settings is restarted.settings
        await restarted.stop()
    asyncio.run(run())


def test_failed_disk_save_and_busy_reply_preserve_runtime(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path)
        await bridge.start()
        settings, task = bridge.settings, bridge.task
        values = bridge.controls()["settings"] | {"shared_memory": False}
        with patch.object(bridge.controls_store, "save", side_effect=StateSaveError("disk full")):
            with pytest.raises(StateSaveError):
                await bridge.update_controls(values)
        assert bridge.settings is settings and bridge.task is task
        assert not bridge.controls_store.path.exists()
        with patch.object(bridge.store, "counts", return_value={"generating": 1}):
            with pytest.raises(QQControlsBusy):
                await bridge.update_controls(values)
        assert bridge.settings is settings and not bridge.controls_store.path.exists()
        await bridge.stop()
    asyncio.run(run())


def test_corrupt_saved_controls_fail_closed(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path)
        bridge.controls_store.path.write_text('{"enabled": false}')
        await bridge.start()
        assert bridge.config_error and bridge.task is None
    asyncio.run(run())


def test_old_saved_controls_gain_new_defaults_without_losing_switches(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path)
        old_keys = {"enabled", "shared_memory", "group_mention_only", "auto_accept_owner_invites",
            "owner_group_auto_enable", "cooldown_seconds", "history_turns", "reply_timeout"}
        old = {key: value for key, value in bridge.controls()["settings"].items() if key in old_keys}
        old["shared_memory"] = False
        bridge.controls_store.path.write_text(json.dumps(old))
        await bridge.start()
        assert not bridge.config_error and not bridge.settings.shared_memory
        assert bridge.settings.thinking_effort == "auto" and bridge.settings.recall_memory_count == 30
        await bridge.stop()
    asyncio.run(run())


def test_search_key_sync_requires_auth_and_never_exposes_saved_key(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path)
        app = FastAPI()
        app.include_router(routes.router)
        await bridge.start()
        with patch.object(routes, "qq_bridge", bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                key = "fake-private-search-key"
                assert (await client.post("/api/qq/search-credentials", json={"api_key": key})).status_code == 401
                await client.post("/api/qq/ui-session", headers=HEADERS)
                response = await client.post("/api/qq/search-credentials", json={"api_key": key}, headers={"Origin": ORIGIN})
                assert response.status_code == 200 and response.json() == {"configured": True}
                assert key == bridge.search_credentials.key()
                assert key not in (await client.get("/api/qq/settings")).text
                assert (await client.post("/api/qq/search-credentials", json={"api_key": "bad\nkey"}, headers={"Origin": ORIGIN})).status_code == 400
                assert key == bridge.search_credentials.key()
        await bridge.stop()
    asyncio.run(run())


def event(text, message_id=1, **changes):
    return dict(time=int(time.time()), self_id=10000, post_type="message", message_type="private",
        sub_type="friend", user_id=20000, message_id=message_id, message=text) | changes


def test_qq_commands_and_web_share_persistence_and_reject_stale_forms(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path)
        await bridge.start()
        original_task = bridge.task
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(routes, "qq_bridge", bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                headers={"Authorization": "Bearer admin-secret"}) as web:
                original = (await web.get("/api/qq/settings")).json()
                assert bridge.submit(event("/set memory off"))[0] == "queued"
                await bridge.queue.join()
                assert "已保存并生效" in bridge.client.sent[-1][1]
                assert bridge.task is original_task and not bridge.responder.settings.shared_memory
                updated = (await web.get("/api/qq/settings")).json()
                assert not updated["settings"]["shared_memory"]
                assert updated["revision"] != original["revision"]
                stale = await web.put("/api/qq/settings", json=original["settings"] | {"revision": original["revision"]})
                assert stale.status_code == 409
                assert not bridge.settings.shared_memory
                fresh = await web.put("/api/qq/settings", json=updated["settings"] | {
                    "history_turns": 8, "revision": updated["revision"]})
                assert fresh.status_code == 200
                assert bridge.submit(event("/settings", 2))[0] == "queued"
                await bridge.queue.join()
                assert "上下文 8 轮" in bridge.client.sent[-1][1]
                assert "记忆共享：关闭" in bridge.client.sent[-1][1]
                assert json.loads(bridge.controls_store.path.read_text())["history_turns"] == 8
        await bridge.stop()
        restarted = bridge_at(tmp_path)
        await restarted.start()
        assert not restarted.settings.shared_memory and restarted.settings.history_turns == 8
        await restarted.stop()
    asyncio.run(run())


@pytest.mark.parametrize("command,key,value", [
    ("/设置 记忆 开启", "shared_memory", True),
    ("/set mention off", "group_mention_only", False),
    ("/set invites on", "auto_accept_owner_invites", True),
    ("/set owner_group on", "owner_group_auto_enable", True),
    ("/设置 冷却 1.5", "cooldown_seconds", 1.5),
    ("/set history 25", "history_turns", 25),
    ("/settings timeout 240", "reply_timeout", 240),
    ("/set thinking high", "thinking_effort", "high"),
    ("/set tools on", "agent_mode", True),
    ("/set recall on", "recall_mode", "on"),
    ("/set memories 50", "recall_memory_count", 50),
    ("/set budget 16000", "memory_token_budget", 16000),
    ("/set graph deep", "graph_recall_mode", "deep"),
    ("/set save off", "memory_write_enabled", False),
    ("/设置 搜索 自动", "web_search_mode", "auto"),
    ("/set output 8192", "max_output_tokens", 8192),
])
def test_settings_command_aliases(tmp_path, command, key, value):
    async def run():
        bridge = bridge_at(tmp_path)
        await bridge.start()
        assert bridge.submit(event(command))[0] == "queued"
        await bridge.queue.join()
        assert "已保存并生效" in bridge.client.sent[-1][1]
        assert getattr(bridge.settings, key) == value
        await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize("command", ["/set enabled off", "/set admin_token leak", "/set memory maybe",
    "/set history 51", "/set history 2.5", "/set timeout nan", "/set cooldown -1"])
def test_invalid_and_unsupported_chat_settings_preserve_state(tmp_path, command):
    async def run():
        bridge = bridge_at(tmp_path)
        await bridge.start()
        old = bridge.settings
        assert bridge.submit(event(command))[0] == "queued"
        await bridge.queue.join()
        assert bridge.settings is old and bridge.status()["running"]
        assert not bridge.controls_store.path.exists()
        assert "已保存并生效" not in bridge.client.sent[-1][1]
        await bridge.stop()
    asyncio.run(run())


def test_only_owner_friend_private_chat_can_change_or_read_controls(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path, allowed_users={"30000"}, allowed_groups={"90000"}, cooldown_seconds=0)
        await bridge.start()
        old = bridge.settings
        assert bridge.submit(event("/settings", user_id=30000))[0] == "queued"
        await bridge.queue.join()
        assert "仅供" in bridge.client.sent[-1][1] and "20000" not in bridge.client.sent[-1][1]
        group_message = [{"type": "at", "data": {"qq": "10000"}}, {"type": "text", "data": {"text": "/set memory off"}}]
        assert bridge.submit(event(group_message, 2, message_type="group", group_id=90000))[0] == "queued"
        await bridge.queue.join()
        assert "好友私聊" in bridge.client.sent[-1][1]
        assert bridge.settings is old and not bridge.controls_store.path.exists()
        await bridge.stop()
    asyncio.run(run())


def test_local_session_controls_and_paused_events(tmp_path):
    async def run():
        bridge = bridge_at(tmp_path)
        app = FastAPI()
        app.include_router(routes.router)
        await bridge.start()
        with patch.object(routes, "qq_bridge", bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                assert (await client.get("/api/qq/settings")).status_code == 401
                response = await client.post("/api/qq/ui-session", headers=HEADERS)
                assert response.status_code == 200
                assert "httponly" in response.headers["set-cookie"].lower()
                assert "samesite=strict" in response.headers["set-cookie"].lower()
                data = (await client.get("/api/qq/settings")).json()
                assert data["identity"]["owner_ids"] == ["20000"]
                assert "secret" not in json.dumps(data)
                values = data["settings"] | {"enabled": False, "revision": data["revision"]}
                assert (await client.put("/api/qq/settings", json=values)).status_code == 401
                assert (await client.put("/api/qq/settings", json=data["settings"], headers={"Origin": ORIGIN})).status_code == 409
                assert (await client.put("/api/qq/settings", json=values, headers={"Origin": ORIGIN})).status_code == 200
                assert not bridge.settings.enabled
                response = await client.post("/api/qq/events", json={}, headers={"Authorization": "Bearer inbound-secret"})
                assert response.status_code == 200 and response.json()["status"] == "disabled"
                assert (await client.post("/api/qq/events", json={})).status_code == 401
                assert (await client.get("/api/qq/settings")).json()["settings"]["enabled"] is False
                for invalid in ({"history_turns": 0}, {"reply_timeout": 901}, {"access_token": "tampered"}):
                    assert (await client.put("/api/qq/settings", json=values | invalid, headers={"Origin": ORIGIN})).status_code == 422
                # Existing standalone dashboard bearer authentication still works.
                assert (await client.get("/api/qq/settings", headers={"Authorization": "Bearer admin-secret"})).status_code == 200
        await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize("origin,host,peer", [
    ("https://evil.example", ORIGIN, "127.0.0.1"),
    ("http://127.0.0.1:9000", ORIGIN, "127.0.0.1"),
    (None, ORIGIN, "127.0.0.1"),
    ("null", ORIGIN, "127.0.0.1"),
    ("http://[bad", ORIGIN, "127.0.0.1"),
    ("http://evil.example:8000", "http://evil.example:8000", "127.0.0.1"),
    (ORIGIN, ORIGIN, "192.168.1.5"),
])
def test_ui_session_rejects_other_origins_and_remote_clients(origin, host, peer):
    async def run():
        app = FastAPI()
        app.include_router(routes.router)
        headers = {"X-Saya-QQ-UI": "1"}
        if origin is not None:
            headers["Origin"] = origin
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 12345)), base_url=host) as client:
            response = await client.post("/api/qq/ui-session", headers=headers)
            assert response.status_code == 403 and "set-cookie" not in response.headers
    asyncio.run(run())
