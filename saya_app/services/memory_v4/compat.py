"""Explicit compatibility boundary for legacy JSON and V4 dual writes."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping

from .models import MemoryEvidence, MemoryItemV4, validate_memory_item
from .store import MemoryV4Store


class DualWriteCoordinator:
    """Write one logical item to V4 and a caller-owned legacy sink.

    The legacy sink is injected so the coordinator cannot silently mutate
    ``knowledge.json``.  Its optional return value is retained only in the
    consistency result; it is not treated as proof that the V4 transaction
    succeeded.
    """

    def __init__(self, store: MemoryV4Store, *, enabled: bool = False):
        self.store = store
        self.enabled = bool(enabled)

    def write(
        self,
        memory: MemoryItemV4 | Mapping[str, Any],
        evidence: Iterable[MemoryEvidence | Mapping[str, Any]] | None = None,
        *,
        legacy_sink: Callable[[dict[str, Any]], Any] | None = None,
        reason: str = "dual_write",
        actor: str = "system",
    ) -> dict[str, Any]:
        item = validate_memory_item(memory)
        if not self.enabled:
            return {"status": "disabled", "v4_written": False, "legacy_written": False, "memory_id": item.id}
        result = self.store.upsert_memory(item, evidence, operation="dual_write", reason=reason, actor=actor)
        legacy_result = None
        if legacy_sink is not None:
            legacy_result = legacy_sink(deepcopy(item.to_dict()))
        return {
            "status": "success",
            "v4_written": result.get("status") in {"created", "updated", "unchanged"},
            "legacy_written": legacy_sink is not None,
            "legacy_result": legacy_result,
            "memory_id": item.id,
            "revision": result.get("revision", item.revision),
        }

    @staticmethod
    def _legacy_projection(item: Mapping[str, Any]) -> dict[str, Any]:
        """Project V4 fields into the old shape for comparison only."""
        return {
            "id": str(item.get("id") or ""),
            "content": str(item.get("canonical_text") or ""),
            "source": str(item.get("legacy_source") or "manual"),
            "is_locked": bool(item.get("locked")),
            "is_important": bool(item.get("pinned")),
            "memory_layer": str(item.get("legacy_layer") or "semantic"),
        }

    def consistency_check(self, legacy_memories: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        legacy = {
            str(memory.get("id")): memory
            for memory in (legacy_memories or [])
            if isinstance(memory, Mapping) and memory.get("id")
        }
        v4 = {item.id: item for item in self.store.list_memories(limit=100000)}
        missing_in_v4 = sorted(set(legacy) - set(v4))
        missing_in_legacy = sorted(set(v4) - set(legacy))
        mismatches = []
        for memory_id in sorted(set(legacy) & set(v4)):
            projected = self._legacy_projection(v4[memory_id].to_dict())
            source = legacy[memory_id]
            differences = {
                key: {"legacy": source.get(key), "v4": value}
                for key, value in projected.items()
                if source.get(key) != value
            }
            if differences:
                mismatches.append({"id": memory_id, "fields": differences})
        return {
            "ok": not missing_in_v4 and not mismatches,
            "legacy_count": len(legacy),
            "v4_count": len(v4),
            "missing_in_v4": missing_in_v4,
            "missing_in_legacy": missing_in_legacy,
            "mismatches": mismatches,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }

