"""Optional local Saya watchdog. No QQ messages or provider requests are sent."""

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.cookiejar import CookieJar
from urllib.request import build_opener, ProxyHandler, HTTPCookieProcessor, Request
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from saya_app.services.qq_lifecycle import (InstanceLease, InstanceInUse, RestartPolicy, ServiceIntent,
    process_identity, matches_identity, supervise_decision)
from saya_app.services.state_storage import atomic_save_json
from saya_app.services.qq_napcat import NapcatSupervisor
from saya_app.services.qq_observation import ObservationLedger

DIRECTORY = ROOT / ".runtime" / "qq-service"
RUNNER = ROOT / "scripts" / "run_qq_backend.py"
SCRIPT = Path(__file__).resolve()
_LOCAL_OPENERS = {}


def read_record(name):
    try:
        value = json.loads((DIRECTORY / name).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def owns(record, script):
    return matches_identity(record, ROOT, script)


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.5):
            return True
    except OSError:
        return False


def process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def logger():
    output = logging.getLogger("qq-service")
    if not output.handlers:
        handler = RotatingFileHandler(DIRECTORY / "service.log", maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        output.addHandler(handler)
        output.setLevel(logging.INFO)
    return output


def launch_backend(port):
    # Keep the existing .env.qq data parser as the only environment loader.
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise RuntimeError("PowerShell is required by the existing QQ launcher")
    child = subprocess.Popen([shell, "-NoLogo", "-NoProfile", "-File", str(ROOT / "scripts" / "start_qq.ps1"),
        "-Python", sys.executable, "-Port", str(port)], cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **process_options())

    def collect():
        with child.stdout:
            while True:
                line = child.stdout.readline(8192)
                if not line:
                    break
                logger().info("backend %s", line.decode("utf-8", errors="replace").rstrip())
    threading.Thread(target=collect, daemon=True, name="qq-private-log").start()
    return child


def watch(port):
    with InstanceLease(DIRECTORY / "supervisor.lock"):
        atomic_save_json(DIRECTORY / "supervisor-owner.json", process_identity(os.getpid()))
        intent = ServiceIntent(DIRECTORY / "intent.json")
        policy = RestartPolicy()
        launcher = None
        had_process = False
        started = time.time()
        previous_phase = None
        napcat = NapcatSupervisor(ROOT, DIRECTORY)
        observation = ObservationLedger(DIRECTORY / 'observation.json')
        next_probe = 0
        napcat_error = False
        while True:
            now = time.time()
            mode = intent.load()["mode"]
            backend = read_record("backend-owner.json")
            backend_alive = owns(backend, RUNNER)
            launcher_alive = bool(launcher and launcher.poll() is None)
            phase = supervise_decision(mode=mode, backend_alive=backend_alive, launcher_alive=launcher_alive,
                port_busy=port_open(port), had_process=had_process, policy=policy, now=now, runtime=now-started)
            if backend_alive:
                if not had_process:
                    started = float(backend["created"])
                had_process = True
            elif not launcher_alive:
                had_process = False
                launcher = None
            if phase == "launch":
                try:
                    with InstanceLease(intent.path.with_suffix('.lock')):
                        if intent.load()['mode'] != 'running':
                            phase = 'stopped'
                            mode = intent.load()['mode']
                        else:
                            launcher = launch_backend(port)
                            started = now
                            had_process = True
                            phase = "starting"
                except InstanceInUse:
                    phase = 'control_update_pending'
                except Exception:
                    policy.exited(now, 0)
                    phase = "launch_failed"
            if phase == "starting" and now-started > 600:
                # Never kill a process merely because startup is slow.
                phase = "startup_timeout"
            atomic_save_json(DIRECTORY / "supervisor-status.json", {"updated": now, "phase": phase,
                "mode": mode, "port": port, **policy.public()})
            try:
                napcat.tick(now, mode)
                napcat_error = False
            except Exception:
                # Broken policy/ownership state is never permission to launch.
                if not napcat_error:
                    logger().warning('napcat supervision unavailable; dedicated processes preserved')
                napcat_error = True
            if now >= next_probe or phase == 'stopped':
                try:
                    sample = probe_health(port)
                    observation.record(sample | {'service_mode': intent.load()['mode']}, now)
                except Exception:
                    logger().warning('observation unavailable; no availability credit recorded')
                next_probe = now + 60
            if phase != previous_phase:
                logger().info("supervisor phase=%s failures=%s", phase, policy.failures)
                previous_phase = phase
            if phase in {"stopped", "retry_exhausted", "blocked_foreign_port", "startup_timeout"}:
                return
            time.sleep(2)


def start(port):
    with InstanceLease(DIRECTORY / "control.lock"):
        ServiceIntent(DIRECTORY / "intent.json").set("running")
        if owns(read_record("backend-owner.json"), RUNNER):
            set_drain(port, False)
        if owns(read_record("supervisor-owner.json"), SCRIPT):
            return {"result": "supervisor_already_running"}
        # Duplicate start races still cannot cross the supervisor OS lock.
        subprocess.Popen([sys.executable, str(SCRIPT), "watch", "--port", str(port)], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options())
        return {"result": "supervisor_started"}


def local_request(port, path, body=None, timeout=3):
    origin = f"http://127.0.0.1:{port}"
    opener = _LOCAL_OPENERS.setdefault(port, build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar())))
    headers = {"Origin": origin, "X-Saya-QQ-UI": "1", "Content-Type": "application/json"}
    request = Request(origin + '/api/qq/' + path, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code != 401:
            raise
    with opener.open(Request(origin + "/api/qq/ui-session", data=b"{}", headers=headers), timeout=3):
        pass
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def set_drain(port, enabled):
    return local_request(port, 'lifecycle/drain', {'draining': enabled})


def probe_health(port):
    try:
        return local_request(port, 'health')
    except Exception:
        return {'saya': 'unknown', 'consumer': 'unknown', 'napcat': 'unknown', 'qq_login': 'unknown'}


def stop(mode, port):
    with InstanceLease(DIRECTORY / "control.lock"):
        # Persist first: a deliberate stop must never trigger crash recovery.
        ServiceIntent(DIRECTORY / "intent.json").set(mode)
        owner = read_record("backend-owner.json")
        if not owns(owner, RUNNER):
            return {"result": "no_owned_backend", "mode": mode, "foreign_processes_preserved": True}
        try:
            status = set_drain(port, True)
            counts = status.get("counts", {})
            if int(status.get('active_web_requests', 0)) or sum(int(counts.get(key, 0)) for key in ("queued", "generating", "sending", "voice_rendering", "active_tasks")):
                return {"result": "busy_preserved", "mode": mode, "message": "自动重启已停用；任务结束后再次执行停止。"}
        except Exception:
            # Unknown task state is not permission to interrupt a working model.
            return {"result": "status_unknown_preserved", "mode": mode}
        if not owns(owner, RUNNER):
            return {"result": "owner_changed_preserved", "mode": mode}
        import psutil
        process = psutil.Process(owner["pid"])
        if abs(process.create_time() - float(owner['created'])) >= .01 or not owns(owner, RUNNER):
            return {"result": "owner_changed_preserved", "mode": mode}
        process.terminate()
        return {"result": "owned_backend_stopping", "mode": mode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "resume", "watch", "status", "stop", "maintenance",
        "napcat-start", "napcat-enable", "napcat-pause", "napcat-status"])
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    os.chdir(ROOT)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    try:
        if args.command in {"start", "resume"}:
            result = start(args.port)
        elif args.command == "watch":
            watch(args.port)
            return 0
        elif args.command in {"stop", "maintenance"}:
            result = stop("manual_stop" if args.command == "stop" else "maintenance", args.port)
        elif args.command.startswith('napcat-'):
            napcat = NapcatSupervisor(ROOT)
            policy = napcat.policy.load()
            if args.command in {'napcat-enable', 'napcat-pause'}:
                policy = napcat.policy.save(args.command == 'napcat-enable', policy['revision'])
            if args.command == 'napcat-start':
                result = napcat.start_once()
            else:
                result = {'policy': policy, 'last_observation': napcat.read_status()}
        else:
            result = {"intent": ServiceIntent(DIRECTORY / "intent.json").load(),
                "supervisor_alive": owns(read_record("supervisor-owner.json"), SCRIPT),
                "backend_owned": owns(read_record("backend-owner.json"), RUNNER),
                "port_open": port_open(args.port), "last_observation": read_record("supervisor-status.json"),
                "napcat_policy": NapcatSupervisor(ROOT).policy.load(), "napcat": read_record('napcat-status.json'),
                "observation": ObservationLedger(DIRECTORY / 'observation.json').summary()}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except InstanceInUse:
        print(json.dumps({"result": "existing_operation_preserved"}))
        return 2
    except Exception:
        # Raw exceptions can contain config contents or local credentials.
        print(json.dumps({"result": "service_operation_failed_check_private_logs"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
