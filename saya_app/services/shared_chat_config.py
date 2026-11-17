"""Explicit, revisioned defaults shared by web and QQ; no credential values."""

import hashlib
import json
from pathlib import Path
import threading

from saya_app.config import KNOWLEDGE_BASE_DIR
from .qq_controls import QQControls, QQControlsConflict, SHARED_FIELDS
from .state_storage import atomic_save_json


def revision_of(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_shared(values):
    if not isinstance(values, dict) or set(values) - SHARED_FIELDS:
        raise ValueError("unsupported shared setting")
    defaults = QQControls(enabled=False, shared_memory=False, group_mention_only=True,
        auto_accept_owner_invites=False, owner_group_auto_enable=False,
        cooldown_seconds=3, history_turns=12, reply_timeout=180).model_dump()
    validated = QQControls.model_validate(defaults | values).model_dump()
    return {key: validated[key] for key in values}


class SharedChatConfigStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {
                "schema_version": 1, "web_follow_global": False, "values": {}}
            if (not isinstance(raw, dict) or set(raw) != {"schema_version", "web_follow_global", "values"}
                    or raw["schema_version"] != 1 or not isinstance(raw["web_follow_global"], bool)):
                raise ValueError("invalid shared config")
            document = dict(raw, values=validate_shared(raw["values"]))
            return document | {"revision": revision_of(document), "fields": sorted(SHARED_FIELDS)}

    def save(self, values, web_follow_global, expected_revision):
        with self._lock:
            if self.load()["revision"] != expected_revision:
                raise QQControlsConflict("共享默认已被修改，请重新读取后再保存。")
            if not isinstance(web_follow_global, bool):
                raise ValueError("invalid web inheritance")
            document = {"schema_version": 1, "web_follow_global": web_follow_global,
                        "values": validate_shared(values)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_save_json(self.path, document)
            return self.load()

    def web_snapshot(self, overrides=None):
        document = self.load()
        values = dict(document["values"]) if document["web_follow_global"] else {}
        values.update(validate_shared(overrides or {}))
        return values, {"revision": document["revision"], "inherited": document["web_follow_global"],
                        "overridden_fields": sorted(overrides or {})}


shared_chat_config = SharedChatConfigStore(KNOWLEDGE_BASE_DIR / "qq" / "shared_settings.json")
