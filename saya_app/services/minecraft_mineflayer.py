"""Process bridge and supervisor for Saya's Node.js/Mineflayer worker.

Split of responsibility (spec sections 3 and 5):

- Node worker: Minecraft protocol, physics, pathfinding, inventory, survival
  state machine and structured actions. It never calls an LLM and never
  reconnects on its own.
- This Python service: single-instance supervision (heartbeat, backoff
  reconnect, worker restart), lifecycle observability (death / respawn /
  kicked / end / worker exit are distinct events), persistent follow restore,
  and routing of game chat into the shared ConversationService. Game
  transcripts stay in per-join files; the web page no longer needs to poll
  /auto-reply for Saya to talk in game.
"""

from __future__ import annotations

import atexit
import collections
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .minecraft_intents import (
    BOT_CONFIG_PATH,
    DEFAULT_CONFIG,
    detect_player_action,
    normalize_auto_mine_ore_modes,
)
WORKER_DIR = Path(__file__).resolve().parents[2] / "minecraft_worker"
WORKER_ENTRY = WORKER_DIR / "worker.mjs"
LOG_LIMIT = 40

# Reconnect supervision (spec 3.2): first wait 3s, then 5/10/20/30s backoff;
# a connection that stays up for 60s resets the failure counter. Fatal
# conditions (auth/ban/version mismatch) never reconnect.
RECONNECT_BACKOFF_S = (3, 5, 10, 20, 30)
STABLE_RESET_S = 60.0
HEARTBEAT_TIMEOUT_S = 10.0
SUPERVISOR_TICK_S = 1.0

FATAL_ERROR_PATTERNS = (
    "invalid credentials",
    "authentication",
    "auth failed",
    "banned",
    "unsupported protocol",
    "version mismatch",
    "invalid username or password",
    "not premium",
    "failed to verify username",
    "no valid authentication",
)

SURVIVAL_DEFAULTS = {
    "mob_policy": "counter",        # counter | flee | report
    "auto_eat_food_level": 7,
}
MOB_POLICIES = {"counter", "flee", "report"}


def _is_fatal_error(text):
    value = str(text or "").casefold()
    return any(pattern in value for pattern in FATAL_ERROR_PATTERNS)


class MineflayerMinecraftService:
    def __init__(self):
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._process = None
        self._reader_thread = None
        self._stderr_thread = None
        self._command_thread = None
        self._supervisor_thread = None
        self._conversation_thread = None
        self._stop_threads = threading.Event()
        self._stop_requested = False
        self._restarting = threading.Event()

        # supervision bookkeeping
        self._last_worker_message_at = 0.0
        self._reconnect_attempts = 0
        self._reconnect_at = None            # absolute time for in-worker reconnect
        self._restart_at = None              # absolute time for full worker restart
        self._restart_reason = ""
        self._stable_since = None
        self._fatal_reason = ""
        self._desired_follow = {"enabled": False, "target": ""}
        self._worker_started_at = 0.0
        self._initial_ready_seen = False

        # chat routing
        self._pending_player_chats = []      # non-owner lines kept for compat
        self._chat_log = []
        self._conversation_queue = collections.deque()
        self._conversation_wake = threading.Event()
        self._conversation_busy = False
        self._last_conversation = {}

        # websocket broadcast
        self._listeners = []
        self._broadcast_version = 0
        self._last_broadcast_at = 0.0

        self.config = dict(DEFAULT_CONFIG)
        self.config["survival"] = dict(SURVIVAL_DEFAULTS)
        self._load_config()
        self.state = self._empty_state()

    # ------------------------------------------------------------------ state
    @staticmethod
    def _empty_state():
        return {
            "engine": "mineflayer",
            "running": False,
            "connected": False,
            "username": None,
            "last_error": "",
            "last_chat": "",
            "last_status": "",
            "follow_target": "",
            "follow_mode": "off",
            "follow_enabled": False,
            "follow_watchdog": {},
            "movement": {},
            "activity": {},
            "activity_history": [],
            "environment": {},
            "last_damage": {},
            "lifecycle": {
                "stage": "idle",
                "last_event": "",
                "last_event_at": None,
                "death": None,
                "respawn": None,
                "disconnect": None,
                "kick": None,
                "worker": None,          # last worker process diagnostics
                "reconnect_attempts": 0,
                "fatal_reason": "",
            },
            "survival": {
                "mode": "normal",
                "reason": "",
                "since": 0,
                "actions": [],
                "oxygen_level": None,
                "health": None,
            },
            "automation": {
                "auto_discard": {
                    "enabled": False,
                    "busy": False,
                    "pending": [],
                    "last_action": None,
                    "last_error": "",
                },
                "combat": {
                    "auto_attack_hostiles": False,
                    "auto_hunt_animals": False,
                    "auto_counterattack": False,
                    "counterattack_owner": False,
                    "busy": False,
                    "mode": "idle",
                    "target": "",
                    "last_action": None,
                    "last_error": "",
                },
            },
            "conversation": {
                "busy": False,
                "queued": 0,
                "last": None,
                "api": {"configured": False, "api_url": "", "model": ""},
            },
            "runtime": {"position": None, "players": [], "entities": [], "inventory": []},
            "reply_api": {
                "configured": False,
                "backend": "local",
                "model": "",
                "last_source": "",
                "last_latency_ms": None,
                "last_at": None,
            },
            "log": [],
        }

    # ----------------------------------------------------------------- config
    def _load_config(self):
        try:
            if BOT_CONFIG_PATH.exists():
                stored = json.loads(BOT_CONFIG_PATH.read_text(encoding="utf-8"))
                self.config.update(
                    {key: stored.get(key, default) for key, default in DEFAULT_CONFIG.items()}
                )
                # One-time migration: configs saved before the FabricTailor
                # default carry the old SkinsRestorer template (no {model}
                # slot).  Upgrade only untouched values - a deliberately
                # customized template is never overwritten.
                if self.config.get("skin_command") == "/skin set {value}":
                    self.config["skin_command"] = DEFAULT_CONFIG["skin_command"]
                survival = stored.get("survival") or {}
                merged = dict(SURVIVAL_DEFAULTS)
                merged.update({
                    key: survival.get(key, value)
                    for key, value in SURVIVAL_DEFAULTS.items()
                })
                if merged["mob_policy"] not in MOB_POLICIES:
                    merged["mob_policy"] = SURVIVAL_DEFAULTS["mob_policy"]
                self.config["survival"] = merged
        except Exception:
            pass

    def public_config(self):
        with self._lock:
            return json.loads(json.dumps(self.config, ensure_ascii=False))

    def save_config(self, updates):
        with self._lock:
            survival_updates = {}
            if isinstance(updates.get("survival"), dict):
                survival_updates = updates["survival"]
            for key in DEFAULT_CONFIG:
                if key not in updates or updates[key] is None:
                    continue
                value = updates[key]
                if key == "port":
                    value = int(value)
                elif key == "status_interval":
                    value = max(1, int(value))
                elif key == "combat_detection_range":
                    value = max(8, min(128, int(value)))
                elif key == "follow_search_range":
                    value = max(16, min(128, int(value)))
                elif key == "auto_mine_range":
                    value = max(4, min(64, int(value)))
                elif key == "auto_mine_ore_modes":
                    value = normalize_auto_mine_ore_modes(value)
                self.config[key] = value
            survival = dict(self.config.get("survival") or SURVIVAL_DEFAULTS)
            for key in SURVIVAL_DEFAULTS:
                if key in survival_updates and survival_updates[key] is not None:
                    survival[key] = survival_updates[key]
            if survival.get("mob_policy") not in MOB_POLICIES:
                survival["mob_policy"] = SURVIVAL_DEFAULTS["mob_policy"]
            survival["auto_eat_food_level"] = max(1, min(19, int(survival.get("auto_eat_food_level", 7))))
            self.config["survival"] = survival
            BOT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            BOT_CONFIG_PATH.write_text(
                json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        current = self.public_config()
        # Connection fields still take effect on the next bot start, while
        # runtime-safe automation/survival toggles update immediately.
        self._send({"type": "config_update", "config": current})
        return current

    # ------------------------------------------------------------------- log
    def _log(self, text, level="info"):
        entry = {"ts": time.time(), "text": str(text)[:400], "level": str(level)}
        with self._lock:
            log = list(self.state.get("log") or [])
            log.append(entry)
            self.state["log"] = log[-LOG_LIMIT:]
        self._notify()

    # ------------------------------------------------------------- lifecycle
    @staticmethod
    def _node_executable():
        candidates = [
            Path(sys.prefix) / "node.exe",
            Path(sys.prefix) / "node",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        found = shutil.which("node")
        if found:
            return found
        raise RuntimeError(
            "Node.js is missing from the Saya environment; install Node.js 22 first"
        )

    def _validate_worker(self):
        if not WORKER_ENTRY.exists():
            raise RuntimeError(f"Mineflayer worker is missing: {WORKER_ENTRY}")
        if not (WORKER_DIR / "node_modules" / "mineflayer").exists():
            raise RuntimeError(
                "Mineflayer dependencies are not installed; run npm install in minecraft_worker"
            )

    def start(self):
        with self._lock:
            if self._process and self._process.poll() is None:
                raise RuntimeError("Mineflayer worker is already running")
        self._validate_worker()
        self._spawn_worker()

    def _spawn_worker(self):
        node = self._node_executable()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [node, str(WORKER_ENTRY)],
            cwd=str(WORKER_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        with self._lock:
            self._stop_requested = False
            self._restarting.clear()
            previous_log = list((self.state or {}).get("log") or [])[-20:]
            previous_reconnect_attempts = self._reconnect_attempts
            self.state = self._empty_state()
            self.state.update({
                "running": True,
                "username": self.config.get("username") or "Saya",
                "log": previous_log,
            })
            self.state["lifecycle"]["reconnect_attempts"] = previous_reconnect_attempts
            self._process = process
            self._pending_player_chats = []
            self._chat_log = []
            self._conversation_queue.clear()
            self._worker_started_at = time.time()
            self._last_worker_message_at = time.time()
            self._reconnect_at = None
            self._fatal_reason = ""
            self.state["lifecycle"]["worker"] = {
                "pid": process.pid,
                "started_at": self._worker_started_at,
                "exit_code": None,
                "note": "started",
            }
            self._stop_threads.clear()
        self._reader_thread = threading.Thread(
            target=self._read_stdout, args=(process,), name="mineflayer-reader", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, args=(process,), name="mineflayer-stderr", daemon=True
        )
        self._command_thread = threading.Thread(
            target=self._command_loop, args=(process,), name="mineflayer-commands", daemon=True
        )
        self._reader_thread.start()
        self._stderr_thread.start()
        self._command_thread.start()
        if self._supervisor_thread is None or not self._supervisor_thread.is_alive():
            self._supervisor_thread = threading.Thread(
                target=self._supervisor_loop, name="mineflayer-supervisor", daemon=True
            )
            self._supervisor_thread.start()
        if self._conversation_thread is None or not self._conversation_thread.is_alive():
            self._conversation_thread = threading.Thread(
                target=self._conversation_loop, name="mineflayer-conversation", daemon=True
            )
            self._conversation_thread.start()
        self._send({"type": "init", "config": self.public_config()})
        self._log(f"Mineflayer worker started with Node {node} (pid {process.pid})")

    def stop(self):
        self._stop_requested = True
        with self._lock:
            process = self._process
            self._reconnect_at = None
        if not process:
            return
        self._stop_threads.set()
        self._conversation_wake.set()
        self._send({"type": "shutdown"}, process=process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        with self._lock:
            if self._process is process:
                self._process = None
            self.state["running"] = False
            self.state["connected"] = False
        self._log("Mineflayer worker stopped")

    # ------------------------------------------------------------ worker pipe
    def _send(self, payload, *, process=None):
        with self._lock:
            target = process or self._process
        if not target or target.poll() is not None or not target.stdin:
            return False
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._write_lock:
                target.stdin.write(line + "\n")
                target.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._log(f"Worker command pipe failed: {exc}", "error")
            return False

    def _read_stdout(self, process):
        try:
            for raw in process.stdout or ():
                line = raw.strip()
                if not line:
                    continue
                self._last_worker_message_at = time.time()
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._log(f"Worker stdout: {line}", "warn")
                    continue
                self._handle_worker_message(message)
        finally:
            code = process.poll()
            with self._lock:
                if self._process is process:
                    self.state["running"] = False
                    self.state["connected"] = False
                    self.state["lifecycle"]["worker"] = {
                        **(self.state["lifecycle"].get("worker") or {}),
                        "exit_code": code,
                        "exited_at": time.time(),
                    }
                    if code not in (None, 0) and not self.state.get("last_error"):
                        self.state["last_error"] = f"Mineflayer worker exited with code {code}"
            self._log(f"Mineflayer worker exited ({code})", "warn" if code else "info")
            self._on_worker_exit(process, code)

    def _read_stderr(self, process):
        for raw in process.stderr or ():
            line = raw.strip()
            if line:
                self._log(f"Node: {line}", "warn")

    # ------------------------------------------------------- supervision core
    def _on_worker_exit(self, process, code):
        if self._stop_requested:
            return
        if self._restarting.is_set():
            return  # supervisor-driven restart in progress; don't double-schedule
        with self._lock:
            if self._process is not process:
                return  # a newer worker already took over
        # Collect diagnostics before restarting (spec 3.2).
        snapshot = {
            "exit_code": code,
            "last_error": self.state.get("last_error", ""),
            "stage": (self.state.get("lifecycle") or {}).get("stage", ""),
            "last_event": (self.state.get("lifecycle") or {}).get("last_event", ""),
        }
        if _is_fatal_error(snapshot["last_error"]):
            with self._lock:
                self._fatal_reason = snapshot["last_error"]
                self.state["lifecycle"]["fatal_reason"] = self._fatal_reason
            self._log(
                f"Worker exited with a fatal error, will not restart: {self._fatal_reason}", "error"
            )
            return
        delay = self._next_backoff()
        self._log(
            f"Node Worker abnormal exit (code {code}, stage {snapshot['stage']}); "
            f"restarting in {delay}s. Diagnostics: {json.dumps(snapshot, ensure_ascii=False)}"
        )
        self._schedule_restart(delay, reason=f"worker_exit:{code}")

    def _next_backoff(self):
        delay = RECONNECT_BACKOFF_S[
            min(self._reconnect_attempts, len(RECONNECT_BACKOFF_S) - 1)
        ]
        with self._lock:
            self._reconnect_attempts += 1
            self.state["lifecycle"]["reconnect_attempts"] = self._reconnect_attempts
        return delay

    def _schedule_restart(self, delay, reason):
        with self._lock:
            # A watchdog condition is checked every second.  Never keep moving
            # an already scheduled restart into the future, otherwise a live
            # but wedged worker can remain around forever.
            if self._restart_at is not None:
                return False
            self._restart_at = time.time() + delay
            self._restart_reason = reason
        # Supervisor loop performs the actual restart so only one instance
        # ever exists at a time.
        return True

    def _supervisor_loop(self):
        restart_at = None
        restart_reason = ""
        while not self._stop_threads.wait(SUPERVISOR_TICK_S):
            now = time.time()
            with self._lock:
                process = self._process
                last_message = self._last_worker_message_at
                reconnect_at = self._reconnect_at
                stable_since = self._stable_since
                restart_at = getattr(self, "_restart_at", None)
                restart_reason = getattr(self, "_restart_reason", "")
                fatal = self._fatal_reason
                desired_follow = dict(self._desired_follow)
                worker_alive = bool(process and process.poll() is None)

            if self._stop_requested:
                continue
            if fatal:
                continue

            # 1. stable connection resets the failure counter
            if (stable_since and now - stable_since >= STABLE_RESET_S
                    and now - last_message <= HEARTBEAT_TIMEOUT_S
                    and self._reconnect_attempts):
                with self._lock:
                    self._reconnect_attempts = 0
                    self._stable_since = None
                    self.state["lifecycle"]["reconnect_attempts"] = 0
                self._log("Connection stable for 60s; reconnect failure counter reset.")

            # 2. scheduled in-worker reconnect (server link dropped)
            if reconnect_at and now >= reconnect_at and worker_alive:
                with self._lock:
                    self._reconnect_at = None
                self._log(
                    f"Reconnecting Mineflayer (attempt {self._reconnect_attempts + 1}, "
                    f"{self.state.get('last_error') or 'connection lost'})."
                )
                self._send({"type": "reconnect"})
                continue

            # 3. scheduled full worker restart
            if restart_at and now >= restart_at:
                with self._lock:
                    self._restart_at = None
                if self._restarting.is_set():
                    continue
                self._restarting.set()
                try:
                    self._log(f"Restarting worker ({restart_reason}).")
                    if process:
                        self._stop_threads.clear()
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            try:
                                process.kill()
                            except Exception:
                                pass
                    self._spawn_worker()
                    # follow persists across restarts; interrupted collection
                    # tasks are intentionally NOT restored.
                    if desired_follow.get("enabled") and restart_reason != "heartbeat_timeout":
                        target = desired_follow.get("target") or "Doctor"
                        self._send({"type": "command", "text": f"follow {target}", "origin": "saya"})
                        self._log(f"Persistent follow restored after restart: {target}")
                    elif desired_follow.get("enabled"):
                        # A heartbeat timeout while following is commonly a
                        # pathfinder event-loop lock.  Restoring the same goal
                        # immediately creates an endless leave/rejoin cycle.
                        with self._lock:
                            self._desired_follow = {"enabled": False, "target": ""}
                        self._log(
                            "Follow circuit breaker opened after a worker hang; "
                            "follow stays off until Doctor enables it again.",
                            "warn",
                        )
                finally:
                    self._restarting.clear()
                continue

            # 4. heartbeat watchdog: silent worker gets diagnosed then restarted
            if (worker_alive and last_message
                    and now - last_message > HEARTBEAT_TIMEOUT_S
                    and restart_at is None):
                diagnostics = {
                    "silent_for_s": round(now - last_message, 1),
                    "stage": (self.state.get("lifecycle") or {}).get("stage"),
                    "last_error": self.state.get("last_error", ""),
                    "activity": self.state.get("activity", {}),
                }
                self._log(
                    "Worker heartbeat lost for over 10s; diagnostics: "
                    + json.dumps(diagnostics, ensure_ascii=False),
                    "error",
                )
                delay = self._next_backoff()
                self._schedule_restart(delay, reason="heartbeat_timeout")

    # ------------------------------------------------------ worker messages
    def _handle_worker_message(self, message):
        kind = str(message.get("type") or "")
        if kind == "state":
            incoming = dict(message.get("state") or {})
            with self._lock:
                reply_api = dict(self.state.get("reply_api") or {})
                conversation = dict(self.state.get("conversation") or {})
                local_log = list(self.state.get("log") or [])
                self.state.update(incoming)
                self.state["reply_api"] = reply_api
                self.state["conversation"] = conversation
                worker_log = list(incoming.get("log") or [])
                merged = []
                seen = set()
                for entry in local_log + worker_log:
                    try:
                        timestamp_bucket = round(float(entry.get("ts") or 0), 1)
                    except (TypeError, ValueError):
                        timestamp_bucket = 0
                    key = (timestamp_bucket, entry.get("text"))
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(entry)
                self.state["log"] = merged[-LOG_LIMIT:]
                self.state["running"] = bool(
                    self._process and self._process.poll() is None
                )
                # Track desired follow so restarts can restore it.
                if incoming.get("follow_enabled"):
                    self._desired_follow = {
                        "enabled": True,
                        "target": str(incoming.get("follow_target") or "Doctor"),
                    }
                elif incoming.get("follow_mode") == "off" and incoming.get("follow_enabled") is False:
                    # Worker reports follow explicitly off.
                    if not incoming.get("follow_target"):
                        self._desired_follow = {"enabled": False, "target": ""}
        elif kind == "log":
            self._log(message.get("text") or "", message.get("level") or "info")
        elif kind == "chat":
            self._handle_player_chat(
                str(message.get("username") or "player"),
                str(message.get("message") or ""),
            )
        elif kind == "ready":
            with self._lock:
                self._stable_since = time.time()
                self._initial_ready_seen = True
                # Storage separation: every world join starts its own
                # conversation session file, so chats from different worlds
                # and different game launches never mix into one history.
                self._conversation_session_id = self._new_game_session_id()
            self._log(
                f"Mineflayer ready as {message.get('username')} on {message.get('version')}; "
                f"conversation session {self._conversation_session_id}."
            )
            # Re-apply the configured skin after every join: skin-plugin
            # state is per-session, a reconnect would silently fall back to
            # the account default.  Best-effort with a short settle delay.
            if str(self.config.get("skin_value") or "").strip():
                def _reapply_skin():
                    time.sleep(5.0)
                    try:
                        self.apply_skin()
                    except Exception:
                        pass
                threading.Thread(target=_reapply_skin, daemon=True).start()
        elif kind == "worker_started":
            self._log(f"Node worker PID {message.get('pid')}")
        elif kind == "lifecycle":
            event = str(message.get("event") or "")
            if event == "end":
                # The worker stays alive; this Python supervisor decides when
                # to reconnect (single instance + backoff).
                reason = (message.get("disconnect") or {}).get("reason", "")
                # Deep memory linkage: archive the game session that just
                # ended into long-term memory BEFORE the next join creates a
                # fresh session id.  Fail-silent daemon thread - the archive
                # may call the summary API and must never block reconnect.
                with self._lock:
                    ended_session = self._conversation_session_id
                if ended_session:
                    threading.Thread(
                        target=self._auto_archive_game_session,
                        args=(ended_session,), daemon=True,
                    ).start()
                if _is_fatal_error(reason) or _is_fatal_error(self.state.get("last_error")):
                    with self._lock:
                        self._fatal_reason = reason or self.state.get("last_error")
                        self.state["lifecycle"]["fatal_reason"] = self._fatal_reason
                    self._log(
                        f"Connection ended with a fatal error, not reconnecting: {self._fatal_reason}",
                        "error",
                    )
                else:
                    delay = self._next_backoff()
                    with self._lock:
                        self._reconnect_at = time.time() + delay
                    self._log(
                        f"Connection interrupted: {reason or 'unknown'}, reconnecting in {delay}s."
                    )
            elif event == "kicked":
                reason = (message.get("kick") or {}).get("reason", "")
                self._log(f"Kicked by server: {reason}", "error")
            elif event in ("death", "respawn"):
                self._log(f"Lifecycle event: {event}")
            elif event == "connecting":
                pass
        self._notify()

    # ----------------------------------------------------------- chat routing
    def _handle_player_chat(self, username, text):
        from .game_bridge import game_bridge
        from .minecraft_intents import is_command_message

        # Slash commands (/home, /msg, /login ...) are addressed to the
        # server, not to Saya: drop them BEFORE any logging or routing so
        # they never reach the conversation layer (or leak credentials into
        # chat history / LLM prompts).
        if is_command_message(text):
            return

        line = f"<{username}> {text}"[:240]
        with self._lock:
            self.state["last_chat"] = line
            self._chat_log.append(line)
            self._chat_log = self._chat_log[-12:]
        game_bridge.push_status("minecraft", line, lane="chat")

        owner = str(self.config.get("owner_username") or "").casefold()
        if owner and username.casefold() != owner:
            # Non-owner players never trigger actions or Saya replies.
            with self._lock:
                self._pending_player_chats.append((time.time(), line))
                self._pending_player_chats = self._pending_player_chats[-20:]
            return
        # Every owner line, including deterministic commands, goes through the
        # same ConversationService as desktop/web.  This keeps one transcript
        # and one routing result instead of silently bypassing chat history.
        direct = detect_player_action(line)
        with self._lock:
            entry = (time.time(), username, text)
            if direct:
                self._conversation_queue.appendleft(entry)
            else:
                self._conversation_queue.append(entry)
            self.state["conversation"]["queued"] = len(self._conversation_queue)
        self._conversation_wake.set()

    def _new_game_session_id(self):
        host = str((self.config or {}).get("host") or "127.0.0.1").replace(".", "_")
        port = str((self.config or {}).get("port") or 25565)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._join_seq = getattr(self, "_join_seq", 0) + 1
        return f"session_mc_{stamp}_{host}_{port}_{self._join_seq:02d}.jsonl"

    def current_game_session_id(self):
        with self._lock:
            if not getattr(self, "_conversation_session_id", None):
                self._conversation_session_id = self._new_game_session_id()
            return self._conversation_session_id

    def _auto_archive_game_session(self, session_id):
        """Long-term memory bridge: archive the ended game session.

        Runs on a daemon thread from the lifecycle-end handler.  Empty
        sessions are skipped (the archiver reports them); any failure is
        logged and swallowed - reconnecting matters more than archiving.
        """
        try:
            from . import game_memory

            result = game_memory.auto_archive_on_disconnect(
                session_id,
                lambda sid: self._conversation_service().archive_game_session(sid),
            )
            if isinstance(result, dict) and result.get("archived"):
                self._log(f"Game session auto-archived into long-term memory: {session_id}")
        except Exception as exc:
            self._log(f"Game session auto-archive skipped: {exc}", "warn")

    def _conversation_service(self):
        from .conversation import conversation_service  # noqa: WPS433 (lazy)

        return conversation_service

    def _conversation_context(self):
        with self._lock:
            runtime = dict(self.state.get("runtime") or {})
            activity = dict(self.state.get("activity") or {})
            follow_mode = self.state.get("follow_mode")
            follow_target = self.state.get("follow_target")
            survival = dict(self.state.get("survival") or {})
        position = runtime.get("position") or {}
        vitals = runtime.get("vitals") or {}
        parts = []
        if position:
            parts.append(
                f"position=({position.get('x')},{position.get('y')},{position.get('z')})"
            )
        if vitals:
            parts.append(f"health={vitals.get('health')},food={vitals.get('food')}")
        if follow_mode and follow_mode != "off":
            parts.append(f"follow={follow_target}({follow_mode})")
        if activity.get("state") == "running":
            parts.append(f"task={activity.get('command')}:{activity.get('message')}")
        if survival.get("mode") and survival.get("mode") != "normal":
            parts.append(f"survival={survival.get('mode')}({survival.get('reason')})")
        return " ".join(parts)[:400]

    def game_state(self):
        """Structured game state for the conversation prompt (plan 7.2).

        Kept as a separate JSON block: never mixed into the plain chat
        history.  Includes vitals, position, follow/task state, aggregated
        inventory, held item + durability, nearby stations, players and
        hostiles, the active plan and the last task result.
        """
        from .minecraft_planner import plan_executor

        with self._lock:
            runtime = json.loads(json.dumps(self.state.get("runtime") or {}, ensure_ascii=False))
            activity = dict(self.state.get("activity") or {})
            survival = dict(self.state.get("survival") or {})
            last_craft = dict(self.state.get("last_craft") or {}) or None
            follow_enabled = bool(self.state.get("follow_enabled"))
            follow_target = str(self.state.get("follow_target") or "")
            follow_mode = str(self.state.get("follow_mode") or "off")
        vitals = runtime.get("vitals") or {}
        position = runtime.get("position") or {}
        stations = runtime.get("stations") or {}
        held = runtime.get("held_item_detail") or ({"name": runtime.get("held_item")} if runtime.get("held_item") else None)

        inventory_summary = {}
        for entry in runtime.get("inventory") or []:
            name = str(entry.get("name") or "")
            inventory_summary[name] = inventory_summary.get(name, 0) + int(entry.get("count") or 0)
        players = [
            {"name": p.get("name"), "distance": p.get("distance")}
            for p in (runtime.get("players") or [])[:6]
        ]
        hostiles = [
            {"name": e.get("name"), "distance": e.get("distance")}
            for e in (runtime.get("entities") or []) if e.get("hostile")
        ][:6]
        last_task = None
        if activity:
            last_task = {
                "command": activity.get("command"),
                "state": activity.get("state"),
                "message": activity.get("message"),
            }
        plan_state = plan_executor.state()
        return {
            "vitals": {
                "health": vitals.get("health"),
                "food": vitals.get("food"),
                "oxygen": survival.get("oxygen_level"),
            },
            "position": position or None,
            "dimension": runtime.get("dimension") or "",
            "held_item": held,
            "follow": {
                "enabled": bool(self.state.get("follow_enabled")),
                "target": self.state.get("follow_target") or "",
                "mode": self.state.get("follow_mode") or "off",
            },
            "survival_mode": survival.get("mode") or "normal",
            "inventory_summary": inventory_summary,
            "stations": {
                "crafting_tables": stations.get("crafting_tables") or [],
                "furnaces": stations.get("furnaces") or [],
            },
            "players_nearby": players,
            "hostiles_nearby": hostiles,
            "current_task": last_task,
            "last_craft": last_craft,
            "current_plan": {
                key: plan_state.get(key)
                for key in (
                    "active", "goal_description", "status", "current_step",
                    "total_steps", "current_step_description", "missing",
                    "waiting_for_doctor", "will_deliver", "last_error",
                )
            },
        }

    def _conversation_loop(self):
        while not self._stop_threads.is_set():
            self._conversation_wake.wait(timeout=1.0)
            self._conversation_wake.clear()
            while True:
                with self._lock:
                    if not self._conversation_queue:
                        self.state["conversation"]["queued"] = 0
                        self._conversation_busy = False
                        break
                    _, username, text = self._conversation_queue.popleft()
                    self.state["conversation"]["queued"] = len(self._conversation_queue)
                    self._conversation_busy = True
                self._run_conversation(username, text)
            if self._stop_threads.is_set():
                break

    def _run_conversation(self, username, text):
        from .conversation import conversation_service

        context = {
            "summary": self._conversation_context(),
            "game_state": self.game_state(),
            "mc_status": self.status(),
            "session_id": self.current_game_session_id(),
        }
        started = time.monotonic()
        try:
            result = conversation_service.handle_message(
                source="minecraft", speaker=username, text=text, context=context
            )
        except Exception as exc:  # never kill the thread
            self._log(f"Conversation service error: {exc}", "error")
            result = {
                "route": "chat",
                "reply": "（我这边出了点小问题，但我还在。）"
                if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "(Something went wrong on my side.)",
                "commands": [],
                "goal": None,
                "source": "local_fallback",
                "latency_ms": round((time.monotonic() - started) * 1000),
                "model": "",
                "confidence": 0.0,
                "error": str(exc)[:200],
            }
        if result.get("reply"):
            self._speak(result["reply"])
        # Command/goal execution happens inside conversation_service so every
        # frontend (game chat, desktop, web) behaves identically.
        self.record_conversation_result(username, result)

    def record_conversation_result(self, speaker, result):
        """Publish a unified chat result to WebSocket/status consumers.

        Game messages and Desktop Console messages both call this method, so
        the realtime route/model line never lags behind the shared history.
        """
        api_status = {}
        try:
            from .conversation import conversation_service

            api_status = conversation_service.credentials_status()
        except Exception:
            pass
        with self._lock:
            self._last_conversation = {
                "at": time.time(),
                "speaker": speaker,
                "route": result.get("route"),
                "source": result.get("source"),
                "latency_ms": result.get("latency_ms"),
                "model": result.get("model"),
                "memory_enabled": result.get("memory_enabled"),
                "commands": result.get("commands") or [],
                "goal": result.get("goal"),
                "error": result.get("error") or "",
            }
            self.state["conversation"]["last"] = dict(self._last_conversation)
            self.state["conversation"]["api"] = api_status or self.state["conversation"].get("api", {})
            self.state["reply_api"] = {
                "configured": api_status.get("configured", False),
                "backend": "unified_conversation",
                "model": api_status.get("model", ""),
                "last_source": result.get("source"),
                "last_latency_ms": result.get("latency_ms"),
                "last_at": time.time(),
            }
        self._log(
            f"Conversation routed ({result.get('source')}, {result.get('latency_ms')}ms, "
            f"route={result.get('route')}, commands={result.get('commands') or []})"
        )
        self._notify()

    # ------------------------------------------------------------- broadcast
    def subscribe(self, callback):
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)
        return lambda: self.unsubscribe(callback)

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify(self):
        now = time.time()
        with self._lock:
            if now - self._last_broadcast_at < 0.5:
                return
            self._last_broadcast_at = now
            listeners = list(self._listeners)
            snapshot = self.status()
        for callback in listeners:
            try:
                callback(snapshot)
            except Exception:
                self.unsubscribe(callback)

    # ------------------------------------------------------------------ views
    def _command_loop(self, process):
        from .game_bridge import game_bridge

        while not self._stop_threads.wait(0.2):
            if process.poll() is not None:
                return
            try:
                for command in game_bridge.pop_commands("minecraft", take=8):
                    self._send(
                        {"type": "command", "text": str(command.get("text") or ""), "origin": "saya"},
                        process=process,
                    )
            except Exception as exc:
                self._log(f"Bridge command polling failed: {exc}", "error")
                time.sleep(0.5)

    def status(self):
        with self._lock:
            result = json.loads(json.dumps(self.state, ensure_ascii=False))
            result["pending_chats"] = len(self._pending_player_chats)
            config = dict(self.config)
            if config.get("account"):
                config["account"] = "<configured>"
            result["config"] = config
            result["engine"] = "mineflayer"
            result["game_session_id"] = self.current_game_session_id()
            return result

    def send_command(self, text, origin="doctor", quiet=False, task_id=None):
        """Direct command injection (desktop console quick buttons, planner).

        Task envelope: the caller may supply the task id it already put into
        its reply; otherwise one is generated here, so every command that
        crosses the pipe carries a Python-owned lifecycle id.
        """
        text = str(text or "").strip()
        if not text:
            return False
        return self._send({
            "type": "command", "text": text[:400], "origin": origin,
            "quiet": bool(quiet),
            "task_id": str(task_id or f"task-{uuid.uuid4().hex[:8]}"),
        })

    def apply_skin(self):
        """Send the configured skin command (server-side skin mod, e.g.
        FabricTailor).  Java skins cannot be set from the bot's own client,
        so this is the only working channel - and it is a no-op unless the
        doctor actually configured a skin_value.

        Template placeholders: {value} (skin value), {model} (classic/slim).
        When the value is an existing local PNG the command switches to the
        FabricTailor upload form automatically - the server reads the file
        directly, no HTTP involved (works with any template style).
        """
        value = str(self.config.get("skin_value") or "").strip()
        template = str(self.config.get("skin_command") or "").strip()
        if not value or not template or "{value}" not in template:
            return False
        model = str(self.config.get("skin_model") or "classic").strip() or "classic"
        value_path = Path(value)
        if value_path.suffix.casefold() == ".png" and value_path.is_file():
            command = f"/skin set upload {model} {value}"
        else:
            command = template.replace("{value}", value).replace("{model}", model)
        sent = self.send_command(command, origin="doctor")
        if sent:
            self._log(f"皮肤命令已发送：{command}")
        return bool(sent)

    def send_plan_state(self, active):
        """Tell the worker whether a multi-step plan is in flight.

        Between two plan steps the worker's foreground slot is briefly empty;
        without this envelope, follow and hunt automation read that gap as
        idle and fight the next step for the pathfinder.  Re-asserted on
        every step so a worker restart cannot lose the flag.
        """
        return self._send({"type": "plan_state", "active": bool(active)})

    @staticmethod
    def _fallback_text(messages, chinese):
        if chinese:
            if "你好" in messages:
                return "你好呀，我在这里。"
            return "我在听。你是想聊天，还是要我在游戏里做一件事？"
        return "I'm listening. Is this chat, or a game task?"

    def _speak(self, text):
        return self._send({"type": "speak", "text": str(text)[:240]})

    def auto_reply(self, credentials=None):
        """Backward-compatible endpoint body.

        Game chat is now answered directly by the shared ConversationService
        (see _conversation_loop), so this only drains leftover non-owner lines
        that were recorded while the internal router was unavailable.
        """
        return None


minecraft_bot_service = MineflayerMinecraftService()
atexit.register(minecraft_bot_service.stop)
