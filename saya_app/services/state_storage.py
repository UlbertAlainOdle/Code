"""Atomic JSON replacement and serialized, rollback-safe state mutations."""

import copy
from functools import wraps
import json
import os
from pathlib import Path
import tempfile


class StateSaveError(OSError):
    """The requested state was not committed to disk."""


def atomic_save_json(path, value):
    target = Path(path)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as handle:
            staged = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, target)
    except (OSError, TypeError, ValueError) as exc:
        raise StateSaveError("保存失败，修改未写入磁盘。请检查磁盘空间或文件权限后重试。") from exc
    finally:
        if staged is not None and staged.exists():
            try:
                staged.unlink()
            except OSError:
                pass


def state_transaction(field):
    """Hold one manager's lock across mutation and persistence, including rollback."""
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            with self._state_lock:
                before = copy.deepcopy(getattr(self, field))
                try:
                    return method(self, *args, **kwargs)
                except Exception:
                    setattr(self, field, before)
                    raise
        return wrapped
    return decorate
