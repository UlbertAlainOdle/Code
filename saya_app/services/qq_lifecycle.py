"""Cross-process leases and conservative local service supervision policies."""

from contextlib import suppress
import json
import os
from pathlib import Path
import time

from .state_storage import atomic_save_json


class InstanceInUse(RuntimeError):
    pass


class InstanceLease:
    """OS-owned lock. A stale file never means a stale lock or a live owner."""
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise InstanceInUse("当前工作区已有运行实例，请使用现有服务。") from None
        self.handle = handle

    def release(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        with suppress(OSError):
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def __enter__(self):
        self.acquire(); return self

    def __exit__(self, *args):
        self.release()


class ServiceIntent:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return {"mode": "manual_stop", "revision": 0}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("mode") not in {"running", "manual_stop", "maintenance"}:
            raise ValueError("service intent is invalid")
        return value

    def set(self, mode):
        if mode not in {"running", "manual_stop", "maintenance"}:
            raise ValueError("invalid service mode")
        with InstanceLease(self.path.with_suffix(".lock")):
            previous = self.load()
            value = {"mode": mode, "revision": int(previous.get("revision", 0)) + 1, "updated": time.time()}
            atomic_save_json(self.path, value)
            return value


class RestartPolicy:
    def __init__(self, max_attempts=5, initial_delay=2, max_delay=60, stable_seconds=120):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.stable_seconds = stable_seconds
        self.failures = 0
        self.next_attempt = 0.0

    def exited(self, now, runtime):
        if runtime >= self.stable_seconds:
            self.failures = 0
        self.failures += 1
        self.next_attempt = now + min(self.max_delay, self.initial_delay * 2 ** max(0, self.failures - 1))

    def may_start(self, now, mode):
        return mode == "running" and self.failures < self.max_attempts and now >= self.next_attempt

    def public(self):
        return {"failures": self.failures, "next_attempt": self.next_attempt, "retry_exhausted": self.failures >= self.max_attempts}


def process_identity(pid):
    import psutil
    process = psutil.Process(pid)
    return {"pid": pid, "created": process.create_time(), "cwd": str(Path(process.cwd()).resolve()),
            "command": process.cmdline()}


def matches_identity(record, root, expected_script):
    """Validate PID reuse, workspace and runner before any stop/adoption."""
    try:
        actual = process_identity(int(record["pid"]))
        script = str(Path(expected_script).resolve()).casefold()
        return (abs(actual["created"] - float(record["created"])) < .01
            and Path(actual["cwd"]).resolve() == Path(root).resolve()
            and any(str(Path(arg).resolve()).casefold() == script for arg in actual["command"] if not arg.startswith("-")))
    except (OSError, ValueError, KeyError, TypeError, ProcessLookupError):
        return False
    except Exception:
        return False


def supervise_decision(*, mode, backend_alive, launcher_alive, port_busy, had_process, policy, now, runtime):
    """Pure recovery policy, shared by the CLI and fault-injection tests."""
    if mode != "running":
        return "stopped"
    if backend_alive:
        return "ready" if port_busy else "starting"
    if launcher_alive:
        return "starting"
    if had_process:
        policy.exited(now, runtime)
    if port_busy:
        return "blocked_foreign_port"
    if policy.failures >= policy.max_attempts:
        return "retry_exhausted"
    return "launch" if policy.may_start(now, mode) else "backoff"
