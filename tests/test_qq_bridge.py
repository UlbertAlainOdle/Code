import asyncio
from dataclasses import replace
import hashlib
import hmac
import json
import time
from unittest.mock import patch

from fastapi import FastAPI
import httpx
import pytest

from saya_app.routers import qq as routes
from saya_app.services.qq_bridge import (
    QQBridge, QQMessage, QQSettings, QQStore, OneBotClient, normalize_event, split_reply,
)
from saya_app.services.qq_chat import QQChatResponder


def settings(**kwargs):
    defaults = dict(enabled=True, bot_id="10000", access_token="outbound-secret",
                      webhook_token="inbound-secret", admin_token="admin-secret",
                      allowed_users=frozenset({"20000", "30000"}),
                      allowed_groups=frozenset({"90000"}))
    return QQSettings(**(defaults | kwargs))


def event(message="你好", **kwargs):
    return dict(time=int(time.time()), self_id=10000, post_type="message", message_type="private",
                sub_type="friend", user_id=20000, message_id=1, message=message, **kwargs)


def changed_event(**kwargs):
    value = event()
    value.update(kwargs)
    return value


def test_configuration_fails_closed_and_disabled_has_no_side_effects(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        QQSettings(enabled=True)
    with pytest.raises(ValueError):
        settings(shared_memory=True)
    with pytest.raises(ValueError):
        settings(api_url="http://secret@localhost:3000/?token=secret")
    with pytest.raises(ValueError):
        settings(allowed_users=frozenset({"invalid"}))
    monkeypatch.setenv("SAYA_QQ_ENABLED", "1")
    monkeypatch.setenv("SAYA_QQ_ACCESS_TOKEN", "very-private-token")
    store = QQStore(tmp_path / "invalid.sqlite3")
    bridge = QQBridge(store=store)
    asyncio.run(bridge.start())
    assert bridge.status()["configuration_error"]
    assert "very-private-token" not in json.dumps(bridge.status())
    assert not store.path.exists()
    disabled = QQBridge(QQSettings(), store)
    asyncio.run(disabled.start())
    assert not store.path.exists()


def test_env_lists_and_validation(monkeypatch):
    monkeypatch.setenv("SAYA_QQ_ALLOWED_USERS", "20000，30000 40000")
    assert QQSettings.from_env().allowed_users == frozenset({"20000", "30000", "40000"})


@pytest.mark.parametrize("changes,reason", [
    ({"user_id": 10000}, "wrong_account_or_self"),
    ({"self_id": 12345}, "wrong_account_or_self"),
    ({"user_id": 99999}, "user_not_allowed"),
    ({"time": 1}, "stale_event"),
    ({"time": "nan"}, "stale_event"),
    ({"sub_type": "group"}, "not_friend_private"),
    ({"message_id": "bad"}, "invalid_message_id"),
    ({"message": [{"type": "image", "data": {"url": "http://localhost/private"}}]}, "no_text"),
    ({"message": "a" * 4001}, "text_too_long"),
    ({"post_type": "message_sent"}, "not_message"),
])
def test_event_filters(changes, reason):
    message, actual = normalize_event(changed_event(**changes), settings())
    assert message is None and actual == reason


def test_group_mentions_are_structural_and_owner_is_never_shared_in_group():
    config = settings(shared_memory=True, owner_ids=frozenset({"20000"}))
    text = [{"type": "at", "data": {"qq": "10000"}}, {"type": "text", "data": {"text": " 问题 "}}]
    group_event = changed_event(message_type="group", group_id=90000, message=text)
    message, reason = normalize_event(group_event, config)
    assert reason == "accepted" and message.text == "问题" and not message.shared
    private, _ = normalize_event(event(), config)
    assert private.shared
    assert private.scope != message.scope
    other, _ = normalize_event({**group_event, "user_id": 30000}, config)
    assert other.scope != message.scope
    assert normalize_event({**group_event, "message": [{"type": "text", "data": {"text": "[CQ:at,qq=10000]问题"}}]}, config)[1] == "not_mentioned"
    assert normalize_event({**group_event, "group_id": 80000}, config)[1] == "group_not_allowed"
    assert normalize_event({**group_event, "anonymous": {"id": 1}}, config)[1] == "anonymous"
    cq, _ = normalize_event({**group_event, "message": "[CQ:at,qq=10000] A&#91;CQ:at,qq=2&#93;&amp;"}, config)
    assert cq.text == "A[CQ:at,qq=2]&"


class Client:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, message, text):
        self.sent.append((message, text))
        if self.fail:
            raise RuntimeError("provider secret should never be public")

    async def call(self, action, payload):
        return {"user_id": 10000}

    async def close(self):
        pass


def test_queue_dedupe_history_reset_and_restart(tmp_path):
    async def run():
        store = QQStore(tmp_path / "bridge.sqlite3")
        client = Client()
        received = []

        async def respond(message, history, session):
            received.append((message.text, list(history), session))
            return "回复" + message.text

        bridge = QQBridge(settings(cooldown_seconds=0), store, respond, client)
        await bridge.start()
        assert bridge.submit(event())[0] == "queued"
        assert bridge.submit(event())[0] == "duplicate"
        await bridge.queue.join()
        assert bridge.submit(changed_event(message_id=2, message="第二次"))[0] == "queued"
        await bridge.queue.join()
        assert received[1][1] == ["Doctor: 你好", "Saya: 回复你好"]
        assert bridge.submit(changed_event(user_id=30000, message_id=3))[0] == "queued"
        await bridge.queue.join()
        assert received[2][1] == []
        bridge.submit(changed_event(message_id=4, message="/reset"))
        await bridge.queue.join()
        bridge.submit(changed_event(message_id=5))
        await bridge.queue.join()
        assert received[-1][1] == [] and received[-1][2] != received[0][2]
        assert len(client.sent) == 5
        await bridge.stop()
        restarted = QQBridge(settings(cooldown_seconds=0), store, respond, client)
        await restarted.start()
        assert restarted.submit(event())[0] == "duplicate"
        await restarted.stop()
    asyncio.run(run())


def test_rate_limit_and_backpressure_are_not_permanent_dedupe(tmp_path):
    store = QQStore(tmp_path / "queue.sqlite3")
    store.initialize(7)
    config = settings(cooldown_seconds=60, queue_size=2)
    message, _ = normalize_event(event(), config)
    assert store.admit(message, config)[0] == "queued"
    assert store.admit(message, config)[0] == "duplicate"
    second = replace(message, key="second")
    assert store.admit(second, config)[0] == "rate_limited"
    assert store.admit(second, config.model_copy(update={"cooldown_seconds": 0}))[0] == "queued"
    assert store.admit(replace(message, key="third"), config)[0] == "busy"


def test_recovery_marks_uncertain_sends_and_stale_jobs(tmp_path):
    async def run():
        store = QQStore(tmp_path / "recovery.sqlite3")
        store.initialize(7)
        config = settings(cooldown_seconds=0)
        message, _ = normalize_event(event(), config)
        for number in range(4):
            store.admit(replace(message, key=str(number)), config)
        with store.connect() as db:
            db.execute("UPDATE inbox SET state='sending' WHERE event_key='0'")
            db.execute("UPDATE inbox SET state='generating' WHERE event_key='1'")
            db.execute("UPDATE inbox SET created=1 WHERE event_key='2'")
        client = Client()
        async def reply(*args):
            return "recovered"
        bridge = QQBridge(config, store, reply, client)
        await bridge.start()
        await bridge.queue.join()
        assert store.counts() == {"delivery_unknown": 1, "interrupted": 1, "expired": 1, "sent": 1}
        assert len(client.sent) == 1
        with store.connect() as db:
            assert all(row[0] == "{}" for row in db.execute("SELECT payload FROM inbox"))
        await bridge.stop()
    asyncio.run(run())


def test_shared_history_cannot_cross_privacy_mode_and_revoked_jobs_are_ignored(tmp_path):
    async def run():
        config = settings(cooldown_seconds=0, owner_ids=frozenset({"20000"}), shared_memory=True)
        store = QQStore(tmp_path / "privacy.sqlite3")
        observed = []
        async def reply(message, history, session):
            observed.append((message.shared, history))
            return "private-memory-marker"
        bridge = QQBridge(config, store, reply, Client())
        await bridge.start()
        bridge.submit(event())
        await bridge.queue.join()
        await bridge.stop()
        isolated = QQBridge(config.model_copy(update={"shared_memory": False}), store, reply, Client())
        await isolated.start()
        isolated.submit(changed_event(message_id=2))
        await isolated.queue.join()
        assert observed == [(True, []), (False, [])]
        await isolated.stop()
        message, _ = normalize_event(changed_event(message_id=3), config)
        store.admit(message, config)
        revoked = QQBridge(config.model_copy(update={"allowed_users": frozenset({"30000"}), "owner_ids": frozenset(), "shared_memory": False}), store, reply, Client())
        await revoked.start()
        await revoked.queue.join()
        assert store.counts()["ignored"] == 1
        assert len(observed) == 2
        await revoked.stop()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["generation", "delivery", "timeout"])
def test_failures_do_not_leak_secrets_or_retry_sends(tmp_path, failure):
    async def run():
        async def reply(*args):
            if failure == "generation":
                raise RuntimeError("my-secret-api-key")
            if failure == "timeout":
                raise TimeoutError("my-secret-api-key")
            return "text"
        client = Client(fail=failure == "delivery")
        bridge = QQBridge(settings(cooldown_seconds=0), QQStore(tmp_path / "failure.sqlite3"), reply, client)
        await bridge.start()
        bridge.submit(event())
        await bridge.queue.join()
        bridge.submit(event())
        assert len(client.sent) == 1
        assert "secret" not in json.dumps(bridge.status())
        assert "secret" not in client.sent[0][1]
        state = "delivery_unknown" if failure == "delivery" else "failed"
        assert bridge.store.counts()[state] == 1
        await bridge.stop()
    asyncio.run(run())


def test_onebot_wire_contract_authentication_and_cq_literal():
    async def run():
        captured = []
        def handle(request):
            captured.append(request)
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 20}})
        config = settings()
        client = OneBotClient(config)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handle), headers={"Authorization": "Bearer " + config.access_token})
        message, _ = normalize_event(event(), config)
        await client.send(message, "[CQ:at,qq=all]")
        assert captured[0].headers["authorization"] == "Bearer outbound-secret"
        assert captured[0].url.path == "/send_private_msg"
        assert json.loads(captured[0].content)["message"] == [{"type": "text", "data": {"text": "[CQ:at,qq=all]"}}]
        await client.send(replace(message, group_id="90000"), "answer")
        assert captured[1].url.path == "/send_group_msg"
        assert json.loads(captured[1].content)["group_id"] == 90000
        await client.close()
    asyncio.run(run())


def test_split_reply_bounded_and_control_tags_removed():
    config = settings(chunk_chars=100, max_reply_chars=250)
    chunks = split_reply("[MC:attack]\n" + "测" * 400, config)
    assert all(len(text) <= 100 for text in chunks)
    assert len("".join(chunks)) <= 250
    assert "MC:" not in "".join(chunks)


def test_http_ingress_hmac_ack_size_limit_admin_and_no_secret_status(tmp_path):
    async def run():
        bridge = QQBridge(settings(webhook_secret="hmac-secret"), QQStore(tmp_path / "http.sqlite3"), client=Client())
        await bridge.start()
        app = FastAPI()
        app.include_router(routes.router)
        body = json.dumps(event("/help"), ensure_ascii=False).encode()
        signature = "sha1=" + hmac.new(b"hmac-secret", body, hashlib.sha1).hexdigest()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch.object(routes, "qq_bridge", bridge):
                assert (await client.post("/api/qq/events", content=body)).status_code == 401
                assert (await client.post("/api/qq/events", content=body, headers={"Authorization": "Bearer inbound-secret"})).status_code == 401
                accepted = await client.post("/api/qq/events", content=body, headers={"X-Signature": signature})
                assert accepted.json()["status"] == "queued" and "reply" not in accepted.json()
                assert (await client.post("/api/qq/events", content=body, headers={"X-Signature": signature})).json()["status"] == "duplicate"
                assert (await client.post("/api/qq/events", content=b"x" * (routes.MAX_EVENT_BYTES + 1))).status_code == 413
                assert (await client.post("/api/qq/diagnostics")).status_code == 401
                assert (await client.post("/api/qq/diagnostics", headers={"Authorization": "Bearer admin-secret"})).json()["account_matches"]
                public = (await client.get("/api/qq/status")).text
                assert "secret" not in public and "20000" not in public
        await bridge.queue.join()
        await bridge.stop()
    asyncio.run(run())


def test_bearer_auth_does_not_accept_query_or_wrong_token(tmp_path):
    bridge = QQBridge(settings(), QQStore(tmp_path / "auth.sqlite3"))
    assert bridge.authenticate(b"x", "Bearer inbound-secret", None)
    assert not bridge.authenticate(b"x", "Bearer wrong", None)
    assert not bridge.authenticate(b"x", None, None)


def test_adapter_uses_only_final_text_and_closes_on_cancel():
    from fastapi.responses import StreamingResponse
    from types import SimpleNamespace
    observed = []
    closed = []
    async def respond(req, channel_turn):
        observed.append((req, channel_turn))
        async def stream():
            try:
                yield 'event: thinking\ndata: {"text":"private reasoning"}\n\n'
                yield 'event: text\ndata: {"text":"partial"}\n\n'
                yield 'event: answer_complete\ndata: {"text":"final"}\n\n'
            finally:
                closed.append(True)
        return StreamingResponse(stream())
    adapter = QQChatResponder(settings(model="api"), SimpleNamespace, respond, lambda: "api",
                              lambda: {"configured": True, "api_key": "key", "api_url": "http://example/v1", "model": "test"})
    message, _ = normalize_event(event(), settings())
    assert asyncio.run(adapter(message, ["Doctor: previous"], "session_qq_test.jsonl")) == "final"
    req, context = observed[0]
    assert req.temporary_chat and req.session_id == "" and not req.agent_mode
    assert req.recall_mode == "off" and context["history"] == ["Doctor: previous"]
    assert closed == [True]
