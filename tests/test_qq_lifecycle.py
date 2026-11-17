import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore
from saya_app.services.qq_health import inspect_health
from saya_app.services.qq_lifecycle import (InstanceLease, InstanceInUse, RestartPolicy,
    ServiceIntent, process_identity, matches_identity, supervise_decision)

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return QQSettings(enabled=True, bot_id="10000", access_token="fake-out", webhook_token="fake-in",
                      admin_token="fake-admin", allowed_users={"20000"}, cooldown_seconds=0)


class Client:
    def __init__(self, status=None, error=None):
        self.status = status if status is not None else {"online": True}
        self.error = error
        self.actions = []
        self.closed = False

    async def call(self, action, payload):
        self.actions.append(action)
        if self.error:
            raise self.error
        return self.status if action == "get_status" else {"user_id": "10000"}

    async def close(self):
        self.closed = True


def test_os_lease_contends_across_processes_and_recovers_after_exit(tmp_path):
    path = tmp_path / "lease"
    path.write_text("stale-owner-content")
    child_code = ("from saya_app.services.qq_lifecycle import InstanceLease; import sys; "
                  "lease=InstanceLease(sys.argv[1]); lease.acquire(); print('locked',flush=True); sys.stdin.readline()")
    child = subprocess.Popen([sys.executable, "-c", child_code, str(path)], cwd=ROOT,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        # Bounded wait: do not wait indefinitely for a child startup failure.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            assert pool.submit(child.stdout.readline).result(timeout=10).strip() == "locked"
        with pytest.raises(InstanceInUse):
            InstanceLease(path).acquire()
    finally:
        child.communicate("done\n", timeout=10)
    with InstanceLease(path):
        with pytest.raises(InstanceInUse):
            InstanceLease(path).acquire()


def test_runner_rejects_second_instance_before_importing_models():
    path = ROOT / ".runtime" / "qq-service" / "backend.lock"
    # If a production runner already owns this lock, a second runner must also refuse.
    lease = InstanceLease(path)
    try:
        lease.acquire()
    except InstanceInUse:
        pass
    try:
        result = subprocess.run([sys.executable, str(ROOT / "scripts/run_qq_backend.py")], cwd=ROOT,
                                capture_output=True, timeout=10)
        assert result.returncode == 2
        assert b"Uvicorn" not in result.stdout and b"torch" not in result.stdout
    finally:
        lease.release()


def test_policy_distinguishes_manual_stop_foreign_port_and_bounded_crashes():
    policy = RestartPolicy(max_attempts=3, initial_delay=2, stable_seconds=120)
    def decide(now, **extra):
        return supervise_decision(**({"mode":"running", "backend_alive":False, "launcher_alive":False,
            "port_busy":False, "had_process":False, "policy":policy, "now":now, "runtime":1} | extra))
    assert decide(0) == "launch"
    assert decide(1, had_process=True) == "backoff"
    assert decide(2) == "backoff" and decide(3) == "launch"
    assert decide(4, had_process=True) == "backoff"
    assert decide(7) == "backoff" and decide(8) == "launch"
    assert decide(9, had_process=True) == "retry_exhausted"
    assert decide(999) == "retry_exhausted"
    for mode in ("manual_stop", "maintenance"):
        assert decide(999, mode=mode, had_process=True) == "stopped"
    assert policy.failures == 3
    policy.exited(1000, 121)
    assert policy.failures == 1
    assert decide(1003, port_busy=True) == "blocked_foreign_port"
    assert decide(1003, backend_alive=True, port_busy=True) == "ready"
    assert decide(1003, launcher_alive=True) == "starting"


def test_intent_defaults_to_stopped_and_is_persisted_atomically(tmp_path):
    store = ServiceIntent(tmp_path / "intent.json")
    assert store.load()["mode"] == "manual_stop" and not store.path.exists()
    assert store.set("running")["revision"] == 1
    with patch("saya_app.services.qq_lifecycle.atomic_save_json", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            store.set("maintenance")
    assert store.load()["mode"] == "running"
    assert store.set("manual_stop")["revision"] == 2
    store.path.write_text('{"mode":"corrupted"}')
    with pytest.raises(ValueError):
        store.load()


def test_identity_requires_creation_time_workspace_and_exact_runner():
    record = process_identity(os.getpid())
    fake_script = ROOT / "scripts/run_qq_backend.py"
    candidate = record | {"command":[sys.executable, str(fake_script)]}
    with patch("saya_app.services.qq_lifecycle.process_identity", return_value=candidate):
        assert matches_identity(record, ROOT, fake_script)
        assert not matches_identity(record | {"created":record["created"]-100}, ROOT, fake_script)
        assert not matches_identity(record, ROOT.parent, fake_script)
        assert not matches_identity(record, ROOT, ROOT / "foreign.py")
    assert not matches_identity({"pid":0}, ROOT, fake_script)


def test_consumer_single_owner_release_on_failed_start_and_pause(tmp_path):
    async def run():
        path = tmp_path / "bridge.sqlite3"
        a = QQBridge(settings(), QQStore(path), client=Client())
        b = QQBridge(settings(), QQStore(path), client=Client())
        with patch.object(a.store, "initialize", side_effect=OSError("disk")):
            with pytest.raises(OSError):
                await a.start()
        await b.start()
        with pytest.raises(InstanceInUse):
            await a.start()
        await b.stop()
        await a.start()
        a.draining = True
        assert a.submit({}) == ("unavailable", "service_draining")
        assert a.status()["draining"]
        await a.stop()
        with InstanceLease(tmp_path / "consumer.lock"):
            pass
    asyncio.run(run())


@pytest.mark.parametrize("status,error,expected,auth", [
    ({"online":True}, None, "matched", "accepted"),
    ({"online":False}, None, "login_required", "accepted"),
    ({}, ConnectionError("private upstream details"), "unknown", "unknown"),
    ({}, asyncio.TimeoutError(), "unknown", "unknown"),
    ({}, httpx.HTTPStatusError("private-token", request=httpx.Request("POST","http://test"),
        response=httpx.Response(401)), "unknown", "rejected"),
])
def test_health_works_while_paused_and_does_not_probe_paid_apis(tmp_path, status, error, expected, auth):
    async def run():
        client = Client(status, error)
        bridge = QQBridge(settings(), QQStore(tmp_path / "bridge.sqlite3"))
        result = await inspect_health(bridge, {"api_key":"private-key", "api_source":"server_file"}, lambda _: client)
        assert result["consumer"] == "paused" and bridge.task is None
        assert result["qq_login"] == expected and result["onebot_auth"] == auth
        assert result["model"] == "configured_unverified" and result["search"] == "not_configured"
        assert set(client.actions) <= {"get_status", "get_login_info"}
        assert client.closed and "private" not in json.dumps(result)
        assert not bridge.store.path.exists()
    asyncio.run(run())


def manager_module():
    spec = importlib.util.spec_from_file_location("qq_manager_test", ROOT / "scripts/manage_qq_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manager_stop_preserves_busy_and_unknown_owner_and_persists_intent_first(tmp_path, monkeypatch):
    module = manager_module()
    monkeypatch.setattr(module, "DIRECTORY", tmp_path)
    monkeypatch.setattr(module, "read_record", lambda _: {"pid": 424242})
    monkeypatch.setattr(module, "owns", lambda *args: False)
    assert module.stop("manual_stop", 8000)["result"] == "no_owned_backend"
    assert ServiceIntent(tmp_path / "intent.json").load()["mode"] == "manual_stop"
    monkeypatch.setattr(module, "owns", lambda *args: True)
    def drain(port, enabled):
        assert ServiceIntent(tmp_path / "intent.json").load()["mode"] == "maintenance"
        assert enabled
        return {"counts":{"generating":1}}
    monkeypatch.setattr(module, "set_drain", drain)
    with patch("psutil.Process") as process:
        assert module.stop("maintenance", 8000)["result"] == "busy_preserved"
        process.assert_not_called()
    monkeypatch.setattr(module, "set_drain", lambda *_: (_ for _ in ()).throw(ConnectionError()))
    assert module.stop("maintenance", 8000)["result"] == "status_unknown_preserved"


def test_manager_stops_only_idle_owned_runner_after_identity_recheck(tmp_path, monkeypatch):
    module = manager_module()
    monkeypatch.setattr(module, "DIRECTORY", tmp_path)
    monkeypatch.setattr(module, "read_record", lambda _: {"pid":424242, "created":100.})
    monkeypatch.setattr(module, "owns", lambda *args: True)
    monkeypatch.setattr(module, "set_drain", lambda *_: {"counts":{"sent":17}})
    with patch("psutil.Process") as process:
        process.return_value.create_time.return_value=100.
        assert module.stop("manual_stop", 8000)["result"] == "owned_backend_stopping"
        process.assert_called_once_with(424242)
        process.return_value.terminate.assert_called_once()
    calls = iter([True, False])
    monkeypatch.setattr(module, "owns", lambda *args: next(calls))
    with patch("psutil.Process") as process:
        assert module.stop("manual_stop", 8000)["result"] == "owner_changed_preserved"
        process.assert_not_called()


def test_watchdog_repeated_crash_stops_at_budget_without_busy_loop(tmp_path, monkeypatch):
    module = manager_module()
    monkeypatch.setattr(module, "DIRECTORY", tmp_path)
    monkeypatch.setattr(module, "read_record", lambda _: {})
    monkeypatch.setattr(module, "owns", lambda *args: False)
    monkeypatch.setattr(module, "port_open", lambda *_: False)
    clock = [100.]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda delay: clock.__setitem__(0, clock[0]+delay))
    monkeypatch.setattr(module, "logger", lambda: SimpleNamespace(info=lambda *args: None))
    monkeypatch.setattr(module, "probe_health", lambda _: {'saya':'unknown'})
    launches = []
    def launch(_):
        launches.append(clock[0])
        return SimpleNamespace(poll=lambda: 1)
    monkeypatch.setattr(module, "launch_backend", launch)
    ServiceIntent(tmp_path / "intent.json").set("running")
    module.watch(8000)
    report = json.loads((tmp_path / "supervisor-status.json").read_text())
    assert len(launches) == 5 and report["phase"] == "retry_exhausted"
    assert report["retry_exhausted"] and report["failures"] == 5
    assert all(b-a >= 4 for a,b in zip(launches, launches[1:]))
    ServiceIntent(tmp_path / "intent.json").set("manual_stop")
    module.watch(8000)
    assert len(launches) == 5


def test_health_and_drain_endpoints_require_admin_and_work_while_paused(tmp_path):
    from fastapi import FastAPI
    from saya_app.routers import qq as routes
    from saya_app.services.conversation import conversation_service
    async def run():
        bridge = QQBridge(settings(), QQStore(tmp_path / "bridge.sqlite3"))
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(routes, "qq_bridge", bridge), patch.object(conversation_service, "credentials", return_value={}), \
             patch("saya_app.services.qq_bridge.OneBotClient", return_value=Client()), \
             patch("saya_app.services.qq_network_health.public_network_probe.probe", new_callable=AsyncMock, return_value='reachable') as network:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as web:
                assert (await web.get("/api/qq/health")).status_code == 401
                assert (await web.post("/api/qq/lifecycle/drain", json={"draining":True})).status_code == 401
                assert not bridge.draining
                web.headers["Authorization"] = "Bearer fake-admin"
                data = (await web.get("/api/qq/health")).json()
                assert data["qq_login"] == "matched" and data["consumer"] == "paused"
                assert data['network'] == 'reachable'
                network.assert_awaited_once()
                assert (await web.post("/api/qq/lifecycle/drain", json={"draining":True})).status_code == 200
                assert bridge.submit({}) == ("unavailable", "service_draining")
                assert (await web.post("/api/qq/lifecycle/drain", json={"draining":False})).status_code == 200
                assert not bridge.draining
        assert bridge.task is None and not bridge.store.path.exists()
    asyncio.run(run())
