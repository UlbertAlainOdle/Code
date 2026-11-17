"""Feature flags for the V4 rollout and their auditable local store."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any, Mapping


FEATURE_FLAG_DEFAULTS = {
    "memory_v4_write_enabled": False,
    "memory_v4_read_enabled": False,
    "memory_v4_shadow_read_enabled": True,
    "memory_background_synthesis_enabled": False,
    "prompt_compiler_v2_enabled": False,
    "daily_state_v2_enabled": False,
    "agent_loop_enabled": False,
    "web_tools_v2_enabled": False,
    "connector_discord_enabled": False,
    "connector_x_enabled": False,
    "external_write_tools_enabled": False,
}


class FeatureFlagStore:
    """Persist only known boolean flags and record every actual toggle."""

    def __init__(self, settings_path: str | Path, audit_path: str | Path | None = None):
        self.settings_path = Path(settings_path)
        self.audit_path = Path(audit_path or self.settings_path.with_name("memory_v4_flag_audit.jsonl"))
        self._lock = threading.RLock()
        self._flags = self._load()

    def _load(self) -> dict[str, bool]:
        result = dict(FEATURE_FLAG_DEFAULTS)
        if self.settings_path.is_file():
            try:
                loaded = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    for key in result:
                        if key in loaded:
                            value = loaded[key]
                            if isinstance(value, bool):
                                result[key] = value
                            else:
                                result[key] = str(value).strip().lower() not in {"", "0", "false", "no", "off"}
            except (OSError, ValueError):
                pass
        return result

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._flags)

    def get(self, name: str) -> bool:
        if name not in FEATURE_FLAG_DEFAULTS:
            raise KeyError(name)
        return bool(self._flags[name])

    def update(self, updates: Mapping[str, Any], *, actor: str = "user", reason: str = "") -> dict[str, bool]:
        changed = []
        with self._lock:
            for key, value in (updates or {}).items():
                if key not in FEATURE_FLAG_DEFAULTS:
                    continue
                if isinstance(value, bool):
                    next_value = value
                else:
                    next_value = str(value).strip().lower() not in {"", "0", "false", "no", "off"}
                if next_value != self._flags[key]:
                    changed.append({"name": key, "old": self._flags[key], "new": next_value})
                    self._flags[key] = next_value
            if changed:
                self._write_settings()
                self._write_audit(changed, actor=actor, reason=reason)
            return self.snapshot()

    def _write_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if self.settings_path.is_file():
            try:
                value = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
                existing = value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                existing = {}
        existing.update(self._flags)
        temporary = self.settings_path.with_name(self.settings_path.name + ".tmp-v4-flags")
        temporary.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.settings_path)

    def _write_audit(self, changed: list[dict[str, Any]], *, actor: str, reason: str) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "actor": str(actor or "user")[:80],
            "reason": str(reason or "")[:300],
            "changes": changed,
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
