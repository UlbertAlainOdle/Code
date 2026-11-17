import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
import httpx
import pytest

from saya_app.routers import qq as routes
from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore
from saya_app.services.qq_controls import QQControlsBusy, QQControlsConflict, QQControlsUnsupported
from saya_app.services.shared_chat_config import SharedChatConfigStore
from saya_app.services.state_storage import StateSaveError


class Client:
    async def close(self):
        pass


def test_malformed_common_credentials_do_not_crash_resolution(tmp_path, monkeypatch):
    from saya_app.services import conversation
    path = tmp_path / "credentials.json"
    path.write_text('["private-invalid-input"]', encoding="utf-8")
    monkeypatch.setattr(conversation, "CREDENTIALS_FILE", path)
    monkeypatch.delenv("API_KEY", raising=False)
    result = conversation.ConversationService().credentials()
    assert not result["configured"] and result["api_source"] == "unconfigured"
    assert "private-invalid-input" not in json.dumps(result)


def make_bridge(path):
    settings = QQSettings(enabled=True, bot_id="10000", access_token="fake-out", webhook_token="fake-in",
        admin_token="fake-admin", owner_ids={"20000"}, shared_memory=True)
    return QQBridge(settings, QQStore(path / "bridge.sqlite3"), SimpleNamespace(settings=settings), Client())


def test_legacy_values_remain_overrides_and_web_opt_in_is_explicit(tmp_path):
    async def run():
        bridge = make_bridge(tmp_path)
        await bridge.start()
        old = bridge.controls()["settings"] | {"thinking_effort": "none", "agent_mode": False}
        await bridge.update_controls(old)
        global_before = bridge.shared_config.load()
        await bridge.update_shared_controls({"thinking_effort": "high", "agent_mode": True}, True, global_before["revision"])
        assert bridge.settings.thinking_effort == "none" and not bridge.settings.agent_mode
        assert bridge.controls()["value_sources"]["thinking_effort"] == "qq_override"
        assert bridge.shared_config.web_snapshot()[0]["thinking_effort"] == "high"
        assert bridge.shared_config.web_snapshot({"thinking_effort": "auto"})[0]["thinking_effort"] == "auto"
        await bridge.stop()
    asyncio.run(run())


def test_inherit_then_detach_roundtrip_and_restart(tmp_path):
    async def run():
        bridge = make_bridge(tmp_path)
        await bridge.start()
        global_before = bridge.shared_config.load()
        await bridge.update_shared_controls({"thinking_effort": "high", "recall_memory_count": 50}, False, global_before["revision"])
        first = bridge.controls()
        await bridge.update_controls(first["settings"], first["revision"], inherit_fields=["thinking_effort", "recall_memory_count"])
        assert bridge.settings.thinking_effort == "high" and bridge.settings.recall_memory_count == 50
        assert bridge.controls()["value_sources"]["thinking_effort"] == "global"
        old_revision = bridge.controls()["revision"]
        await bridge.update_shared_controls({"thinking_effort": "none", "recall_memory_count": 60}, False, bridge.shared_config.load()["revision"])
        assert bridge.settings.thinking_effort == "none" and bridge.settings.recall_memory_count == 60
        with pytest.raises(QQControlsConflict):
            await bridge.update_controls(first["settings"], old_revision, inherit_fields=[])
        await bridge.stop()
        restarted = make_bridge(tmp_path)
        await restarted.start()
        assert restarted.settings.thinking_effort == "none" and restarted.settings.recall_memory_count == 60
        values = restarted.controls()
        await restarted.update_controls(values["settings"] | {"thinking_effort": "auto"}, values["revision"], inherit_fields=[])
        assert restarted.controls()["value_sources"]["thinking_effort"] == "qq_override"
        assert restarted.settings.thinking_effort == "auto"
        await restarted.stop()
    asyncio.run(run())


def test_forbidden_inheritance_and_credential_fields_cannot_widen_scope(tmp_path):
    store = SharedChatConfigStore(tmp_path / "global.json")
    for values in ({"shared_memory": True}, {"owner_ids": ["30000"]}, {"api_key": "fake-secret"}):
        with pytest.raises(ValueError):
            store.save(values, True, store.load()["revision"])
    assert not store.path.exists()
    with pytest.raises(ValueError):
        store.web_snapshot({"temporary_chat": False})


def test_shared_save_failure_and_busy_state_do_not_change_runtime(tmp_path):
    async def run():
        bridge = make_bridge(tmp_path)
        await bridge.start()
        before = bridge.controls()
        await bridge.update_controls(before["settings"], before["revision"], inherit_fields=["thinking_effort"])
        current = bridge.shared_config.load()
        with patch.object(bridge.store, "counts", return_value={"generating": 1}):
            with pytest.raises(QQControlsBusy):
                await bridge.update_shared_controls({"thinking_effort": "high"}, True, current["revision"])
        assert bridge.shared_config.load()["revision"] == current["revision"]
        with patch("saya_app.services.shared_chat_config.atomic_save_json", side_effect=StateSaveError("disk full")):
            with pytest.raises(StateSaveError):
                await bridge.update_shared_controls({"thinking_effort": "high"}, True, current["revision"])
        assert bridge.settings.thinking_effort == "auto" and bridge.status()["running"]
        await bridge.stop()
    asyncio.run(run())


def test_unsupported_thinking_is_rejected_before_saving(tmp_path):
    async def run():
        bridge = make_bridge(tmp_path)
        bridge.responder.capabilities = lambda: {"thinking_supported": False}
        await bridge.start()
        before = bridge.controls()
        with pytest.raises(QQControlsUnsupported):
            await bridge.update_controls(before["settings"] | {"thinking_effort": "high"}, before["revision"])
        assert not bridge.controls_store.path.exists() and bridge.settings.thinking_effort == "auto"
        await bridge.stop()
    asyncio.run(run())


def test_shared_routes_are_authenticated_and_versioned(tmp_path):
    async def run():
        bridge = make_bridge(tmp_path)
        await bridge.start()
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(routes, "qq_bridge", bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as web:
                assert (await web.get("/api/qq/shared-settings")).status_code == 401
                web.headers["Authorization"] = "Bearer fake-admin"
                read = (await web.get("/api/qq/shared-settings")).json()
                body = {"values": {"agent_mode": True}, "web_follow_global": True, "revision": read["revision"]}
                assert (await web.put("/api/qq/shared-settings", json=body)).status_code == 200
                assert (await web.put("/api/qq/shared-settings", json=body)).status_code == 409
                assert "fake-admin" not in (await web.get("/api/qq/settings")).text
        await bridge.stop()
    asyncio.run(run())


def test_credentials_are_atomic_and_environment_bundle_has_priority(tmp_path, monkeypatch):
    from saya_app.services import conversation
    from saya_app.services.qq_controls import QQSearchCredentialsStore
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(conversation, "CREDENTIALS_FILE", path)
    for name in ("API_KEY", "API_URL", "API_MODEL", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    service = conversation.ConversationService()
    service.save_credentials(api_key="fake-key", api_url="http://mock/v1", api_model_name="model-a", brave_search_api_key="fake-brave")
    key = QQSearchCredentialsStore(tmp_path / "legacy.json", path)
    assert key.key() == "fake-brave"
    key.save("new-fake-brave")
    assert service.credentials()["brave_search_api_key"] == "new-fake-brave"
    assert service.credentials()["model"] == "model-a"
    before = path.read_bytes()
    with patch("saya_app.services.credentials_storage.atomic_save_json", side_effect=StateSaveError("disk full")):
        with pytest.raises(StateSaveError):
            service.save_credentials(api_key="not-committed", brave_search_api_key="not-committed")
    assert path.read_bytes() == before
    assert "new-fake-brave" not in json.dumps(service.credentials_status())
    monkeypatch.setenv("API_KEY", "environment-fake")
    monkeypatch.setenv("API_URL", "http://environment/v1")
    monkeypatch.setenv("API_MODEL", "environment-model")
    resolved = service.credentials()
    assert resolved["api_key"] == "environment-fake" and resolved["api_url"] == "http://environment/v1"
    assert resolved["model"] == "environment-model" and resolved["api_source"] == "environment"


def test_missing_version_and_old_client_cannot_erase_inheritance(tmp_path):
    async def run():
        bridge = make_bridge(tmp_path)
        await bridge.start()
        before = bridge.controls()
        await bridge.update_controls(before["settings"], before["revision"], inherit_fields=["thinking_effort"])
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(routes, "qq_bridge", bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", headers={"Authorization":"Bearer fake-admin"}) as web:
                current = (await web.get("/api/qq/settings")).json()
                assert (await web.put("/api/qq/settings", json=current["settings"] | {"revision":current["revision"]})).status_code == 409
                assert bridge.controls()["inherit_fields"] == ["thinking_effort"]
        await bridge.stop()
    asyncio.run(run())
