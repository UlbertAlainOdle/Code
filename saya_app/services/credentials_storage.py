"""Process-serialized atomic credential updates; values never form public status."""

import json
from pathlib import Path
import threading
import secrets

from .state_storage import atomic_save_json

CREDENTIALS_LOCK = threading.RLock()
_REVISION_KEY = secrets.token_bytes(32)


class CredentialsConflict(ValueError):
    pass


def credentials_revision(values):
    # Opaque per-process token; never publish a guessable digest of a secret.
    import hashlib
    import hmac
    return hmac.new(_REVISION_KEY, json.dumps(values, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()


def read_credentials(path):
    path = Path(path)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid credential store")
    return value


def update_credentials(path, updates, expected_revision=None):
    with CREDENTIALS_LOCK:
        current = read_credentials(path)
        if expected_revision is not None and expected_revision != credentials_revision(current):
            raise CredentialsConflict('本机配置已更新，请重新读取后再保存；浏览器草稿已保留。')
        current.update(updates)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_save_json(target, current)
