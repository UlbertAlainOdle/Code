"""Persistent, content-free time events exposed to Saya's read-only Agent."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ALLOWED_EVENT_TYPES = {
    "backend.started",
    "session.created",
    "session.opened",
    "session.branched",
    "turn.started",
}

try:
    _LOCAL_ZONE = ZoneInfo(os.environ.get("SAYA_TIMEZONE", "Asia/Shanghai"))
except ZoneInfoNotFoundError:
    _LOCAL_ZONE = datetime.now().astimezone().tzinfo


def _local_now() -> datetime:
    return datetime.now(_LOCAL_ZONE)


def _as_local_iso(value: datetime | str | None = None) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            parsed = _local_now()
    else:
        parsed = _local_now()
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(_LOCAL_ZONE).isoformat(timespec="seconds")


def _safe_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep event metadata small and guarantee that message text is never stored."""
    allowed = {"source", "source_session_id", "project_id", "role_mode", "temporary"}
    result: dict[str, Any] = {}
    for key, value in (details or {}).items():
        key = str(key)
        if key not in allowed:
            continue
        if isinstance(value, bool):
            result[key] = value
        elif isinstance(value, (str, int, float)):
            result[key] = str(value)[:160]
    return result


class TimeContextService:
    """Append-only event ledger with bounded, privacy-safe Agent queries."""

    def __init__(self, path: str | Path, *, max_events: int = 2000):
        self.path = Path(path)
        self.max_events = max(100, min(10000, int(max_events)))
        self._lock = threading.RLock()
        self.backend_started_at = ""
        for event in reversed(self._read_unlocked()):
            if event.get("type") == "backend.started":
                self.backend_started_at = str(event.get("occurred_at") or "")
                break

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(event, dict) and event.get("type") in ALLOWED_EVENT_TYPES:
                        events.append(event)
        except OSError:
            return []
        return events[-self.max_events:]

    def _rewrite_unlocked(self, events: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events[-self.max_events:]:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        temp_path.replace(self.path)

    def record(
        self,
        event_type: str,
        *,
        occurred_at: datetime | str | None = None,
        session_id: str = "",
        request_id: str = "",
        details: Mapping[str, Any] | None = None,
        dedupe_seconds: float = 0.0,
    ) -> dict[str, Any] | None:
        event_type = str(event_type or "").strip().lower()
        if event_type not in ALLOWED_EVENT_TYPES:
            return None
        event = {
            "id": uuid4().hex,
            "type": event_type,
            "occurred_at": _as_local_iso(occurred_at),
            "session_id": str(session_id or "")[:180],
            "request_id": str(request_id or "")[:80],
            "details": _safe_details(details),
        }
        with self._lock:
            events = self._read_unlocked()
            if dedupe_seconds > 0:
                event_time = datetime.fromisoformat(event["occurred_at"])
                for previous in reversed(events[-50:]):
                    if previous.get("type") != event_type:
                        continue
                    if str(previous.get("session_id") or "") != event["session_id"]:
                        continue
                    try:
                        previous_time = datetime.fromisoformat(str(previous.get("occurred_at")))
                        if abs((event_time - previous_time).total_seconds()) <= dedupe_seconds:
                            return previous
                    except (TypeError, ValueError):
                        pass
                    break
            events.append(event)
            self._rewrite_unlocked(events)
        return dict(event)

    def record_backend_started(self, started_at: datetime | str | None = None) -> dict[str, Any] | None:
        self.backend_started_at = _as_local_iso(started_at)
        return self.record(
            "backend.started",
            occurred_at=self.backend_started_at,
            details={"source": "uvicorn_startup"},
        )

    def ensure_session_created(self, session_id: str, *, source: str = "chat") -> dict[str, Any] | None:
        session_id = str(session_id or "")
        if not session_id:
            return None
        with self._lock:
            for event in reversed(self._read_unlocked()):
                if event.get("type") == "session.created" and event.get("session_id") == session_id:
                    return dict(event)
        occurred_at = None
        match = re.match(
            r"^session_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6})-[a-f0-9]{8}\.jsonl$",
            session_id,
            flags=re.IGNORECASE,
        )
        if match:
            try:
                occurred_at = datetime.strptime(
                    match.group(1), "%Y-%m-%d_%H-%M-%S-%f"
                ).replace(tzinfo=_LOCAL_ZONE)
            except ValueError:
                occurred_at = None
        return self.record(
            "session.created",
            occurred_at=occurred_at,
            session_id=session_id,
            details={"source": source},
        )

    def query(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments if isinstance(arguments, Mapping) else {}
        session_id = str(arguments.get("session_id") or "").strip()
        raw_types = arguments.get("event_types")
        if isinstance(raw_types, str):
            raw_types = [raw_types]
        selected_types = {
            str(value).strip().lower()
            for value in (raw_types or [])
            if str(value).strip().lower() in ALLOWED_EVENT_TYPES
        }
        try:
            limit = max(1, min(50, int(arguments.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20
        since = None
        if arguments.get("since"):
            try:
                since = datetime.fromisoformat(str(arguments["since"]))
                if since.tzinfo is None:
                    since = since.replace(tzinfo=_LOCAL_ZONE)
            except ValueError:
                since = None

        with self._lock:
            events = self._read_unlocked()
        filtered = []
        for event in reversed(events):
            if session_id and str(event.get("session_id") or "") != session_id:
                continue
            if selected_types and event.get("type") not in selected_types:
                continue
            if since is not None:
                try:
                    occurred = datetime.fromisoformat(str(event.get("occurred_at")))
                    if occurred.tzinfo is None:
                        occurred = occurred.replace(tzinfo=_LOCAL_ZONE)
                    if occurred < since:
                        continue
                except ValueError:
                    continue
            filtered.append(event)
            if len(filtered) >= limit:
                break

        now = _local_now()
        uptime_seconds = None
        if self.backend_started_at:
            try:
                uptime_seconds = max(
                    0,
                    int((now - datetime.fromisoformat(self.backend_started_at)).total_seconds()),
                )
            except ValueError:
                uptime_seconds = None
        return {
            "status": "ready",
            "current_time": now.isoformat(timespec="seconds"),
            "timezone": str(now.tzinfo),
            "backend_started_at": self.backend_started_at,
            "backend_uptime_seconds": uptime_seconds,
            "event_count": len(filtered),
            "events": filtered,
        }
