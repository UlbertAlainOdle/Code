"""Evidence-backed background synthesis patches and memory controls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from .models import MemoryEvidence, MemoryItemV4, ValidationError
from .store import MemoryV4Store
from ..qq_tasks import checkpoint_current, TaskCancelled
from ..memory_use import MemoryUseChanged


_SECRET_RE = re.compile(r"(?i)(?:password|passwd|api[ _-]?key|access[ _-]?token|bearer\s+|secret\s*[:=]|credential|sk-[a-z0-9]{12,})")


@dataclass
class SynthesisPatch:
    operations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"operations": deepcopy(self.operations)}


class BackgroundSynthesizer:
    """Apply only small JSON-pointer patches supported by cited evidence."""

    META_KEY = "synthesis:default"

    def __init__(self, store: MemoryV4Store, *, permitted=None, use_epoch=None):
        self.store = store
        self.permitted,self.use_epoch=permitted,use_epoch

    def current(self) -> dict[str, Any]:
        if self.use_epoch is not None:
            epoch=self.use_epoch();saved=self.store.get_meta(self.META_KEY+':use_epoch','0')
            if saved!=epoch:return {}
        raw = self.store.get_meta(self.META_KEY, "{}")
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def validate_patch(self, patch: SynthesisPatch | Mapping[str, Any]) -> list[dict[str, Any]]:
        operations = patch.operations if isinstance(patch, SynthesisPatch) else patch.get("operations", []) if isinstance(patch, Mapping) else []
        if not isinstance(operations, list) or len(operations) > 50:
            raise ValidationError("synthesis patch operations must be a list of at most 50")
        validated = []
        for operation in operations:
            if not isinstance(operation, Mapping):
                raise ValidationError("synthesis operation must be an object")
            op = str(operation.get("op") or "")
            path = str(operation.get("path") or "")
            refs = [str(item) for item in (operation.get("evidence_refs") or []) if str(item).strip()]
            if op not in {"replace", "remove"} or not path.startswith("/") or ".." in path or not refs:
                raise ValidationError("synthesis operation requires replace/remove, safe path, and evidence_refs")
            if op == "replace" and _SECRET_RE.search(json.dumps(operation.get("value"), ensure_ascii=False)):
                raise ValidationError("secret data cannot enter synthesis")
            for ref in refs:
                if not self._evidence_exists(ref):
                    raise ValidationError(f"synthesis evidence ref not found: {ref}")
            validated.append({
                "op": op, "path": path, "evidence_refs": refs,
                **({"value": deepcopy(operation.get("value"))} if op == "replace" else {}),
                **({"reason": str(operation.get("reason") or "")[:300]} if operation.get("reason") else {}),
            })
        return validated

    def apply_patch(self, patch: SynthesisPatch | Mapping[str, Any], *, actor: str = "synthesis") -> dict[str, Any]:
        checkpoint_current()
        epoch_at_start=self.use_epoch() if self.use_epoch is not None else None
        operations = self.validate_patch(patch)
        summary = self.current()
        before = deepcopy(summary)
        applied = []
        for operation in operations:
            self._apply_one(summary, operation)
            applied.append({"op": operation["op"], "path": operation["path"], "evidence_refs": operation["evidence_refs"]})
        checkpoint_current()
        self.store.set_meta(self.META_KEY, json.dumps(summary, ensure_ascii=False, sort_keys=True))
        if self.use_epoch is not None:self.store.set_meta(self.META_KEY+':use_epoch',epoch_at_start)
        if applied:
            event = {
                "id": f"synthesis.updated:{hashlib.sha256(json.dumps(applied, sort_keys=True).encode()).hexdigest()[:24]}",
                "turn_id": "background-synthesis", "sequence": int(datetime.now().timestamp()) % 1000000,
                "type": "memory.synthesis.updated",
                "public_payload": {"operation_count": len(applied), "paths": [item["path"] for item in applied], "actor": str(actor or "synthesis")[:80]},
                "private_payload": None, "status": "completed", "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.store.record_event(event)
        return {"status": "success", "changed": before != summary, "applied": applied, "summary": summary}

    def expire_memory(self, memory_id: str, *, reason: str = "expired", actor: str = "synthesis") -> dict[str, Any]:
        current = self.store.get_memory(memory_id)
        if current is None:
            raise KeyError(memory_id)
        return self.store.update_memory(memory_id, {"lifecycle": "expired"}, operation="expire", reason=reason, actor=actor)

    def _evidence_exists(self, evidence_id: str) -> bool:
        for item in self.store.list_memories(limit=100000):
            if self.permitted is not None and not self.permitted(item):continue
            if any(entry.id == evidence_id for entry in self.store.list_evidence(item.id)):
                return True
        return False

    @staticmethod
    def _parts(path: str) -> list[str]:
        return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/") if part]

    @classmethod
    def _apply_one(cls, document: dict[str, Any], operation: Mapping[str, Any]) -> None:
        parts = cls._parts(str(operation["path"]))
        if not parts:
            raise ValidationError("root synthesis replacement is forbidden")
        target: Any = document
        for part in parts[:-1]:
            if not isinstance(target, dict):
                raise ValidationError("synthesis path traverses a non-object")
            target = target.setdefault(part, {})
        if not isinstance(target, dict):
            raise ValidationError("synthesis path target is not an object")
        leaf = parts[-1]
        if operation["op"] == "remove":
            target.pop(leaf, None)
        else:
            target[leaf] = deepcopy(operation.get("value"))


class BackgroundSynthesisScheduler:
    """Run bounded candidate synthesis only when an explicit producer exists."""

    def __init__(self, store: MemoryV4Store, producer=None, *, enabled: bool = False, permitted=None, use_epoch=None):
        self.store = store
        self.producer = producer
        self.enabled = bool(enabled)
        self.synthesizer = BackgroundSynthesizer(store,permitted=permitted,use_epoch=use_epoch)
        self.use_epoch=use_epoch

    def run_once(self, *, limit: int = 10, actor: str = "background_synthesis") -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "processed": 0, "applied": 0, "rejected": 0}
        if not callable(self.producer):
            return {"status": "unconfigured", "processed": 0, "applied": 0, "rejected": 0}
        candidates = self.store.list_candidates(status="pending", limit=max(1, min(50, int(limit))))
        processed = applied = rejected = 0
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            checkpoint_current()
            processed += 1
            try:
                if self.use_epoch is not None and self.use_epoch()!='0' and (
                        not candidate.evidence_ids or len(candidate.evidence_ids)>128
                        or not all(self.synthesizer._evidence_exists(ref) for ref in candidate.evidence_ids)):
                    rejected+=1;results.append({'candidate_id':candidate.id,'status':'blocked','reason':'memory_use_policy'})
                    continue
                patch = self.producer(candidate.to_dict())
                checkpoint_current()
                if not isinstance(patch, (SynthesisPatch, Mapping)):
                    raise ValidationError("synthesis producer must return a patch object")
                result = self.synthesizer.apply_patch(patch, actor=actor)
                self.store.update_candidate_status(candidate.id, "accepted", review_reason="synthesis patch applied")
                applied += len(result.get("applied") or [])
                results.append({"candidate_id": candidate.id, "status": "applied", "operation_count": len(result.get("applied") or [])})
            except (MemoryUseChanged,TaskCancelled):raise
            except Exception as exc:
                self.store.update_candidate_status(candidate.id, "rejected", review_reason=str(exc)[:500])
                rejected += 1
                results.append({"candidate_id": candidate.id, "status": "rejected", "reason": str(exc)[:180]})
        return {"status": "success", "processed": processed, "applied": applied, "rejected": rejected, "results": results}


class MemoryControls:
    """User-facing controls over the V4 repository; all actions remain auditable."""

    def __init__(self, store: MemoryV4Store):
        self.store = store

    def summary(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id, "canonical_text": item.canonical_text, "kind": item.kind,
                "scope_type": item.scope_type, "pinned": item.pinned, "locked": item.locked,
                "lifecycle": item.lifecycle, "updated_at": item.updated_at,
                "revision": item.revision, "metadata": dict(item.metadata),
            }
            for item in self.store.list_memories(limit=limit)
        ]

    def why_used(self, memory_id: str, *, turn_id: str | None = None) -> dict[str, Any]:
        usage = self.store.list_memory_usage(memory_id, limit=1000)
        if turn_id:
            usage = [item for item in usage if item.get("turn_id") == turn_id]
        return {"memory_id": str(memory_id), "usage": usage, "count": len(usage)}

    def correct(self, memory_id: str, updates: Mapping[str, Any], *, actor: str = "user") -> dict[str, Any]:
        current = self.store.get_memory(memory_id)
        if current is None:
            raise KeyError(memory_id)
        if not isinstance(updates, Mapping) or not updates:
            raise ValidationError("correction updates are required")
        allowed = {
            "canonical_text", "value", "predicate", "kind", "scope_type", "scope_id",
            "authority", "sensitivity", "valid_from", "valid_to", "confidence", "salience",
            "activation_policy", "metadata",
        }
        unknown = sorted(set(str(key) for key in updates) - allowed)
        if unknown:
            raise ValidationError(f"correction field is not user-editable: {unknown[0]}")
        safe_updates = {str(key): value for key, value in updates.items() if str(key) in allowed}
        from ..memory_origin import apply_origin
        metadata = {**current.metadata, **(safe_updates.get('metadata') or {})}
        apply_origin(metadata, previous=current.metadata, reason='explicit_correct')
        from ..memory_importance import update as update_importance
        safe_updates['salience']=update_importance(metadata,previous=current.metadata,
            score_before=current.salience,score_after=safe_updates.get('salience',current.salience),reason='manual_score',manual=True)
        safe_updates['metadata'] = metadata
        quote = str(safe_updates.get("canonical_text") or current.canonical_text)[:2000]
        evidence = MemoryEvidence(
            id=f"user-correction:{memory_id}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}", memory_id=memory_id,
            source_type="user_control", source_id=f"control:{memory_id}", role="assertion", quote=quote,
        )
        result = self.store.update_memory(memory_id, safe_updates, evidence=[evidence], operation="correct", reason="user correction", actor=actor)
        return result

    def pin(self, memory_id: str, *, actor: str = "user") -> dict[str, Any]:
        return self.store.update_memory(memory_id, {"pinned": True, "pin_source": "user"}, operation="pin", reason="user pin", actor=actor)

    def unpin(self, memory_id: str, *, actor: str = "user") -> dict[str, Any]:
        return self.store.update_memory(memory_id, {"pinned": False, "pin_source": None}, operation="unpin", reason="user unpin", actor=actor)

    def do_not_mention(self, memory_id: str, enabled: bool = True, *, actor: str = "user") -> dict[str, Any]:
        current = self.store.get_memory(memory_id)
        if current is None:
            raise KeyError(memory_id)
        metadata = dict(current.metadata)
        metadata["do_not_mention"] = bool(enabled)
        return self.store.update_memory(memory_id, {"metadata": metadata}, operation="policy", reason="user do not mention preference", actor=actor)

    def forget(self, memory_id: str, *, actor: str = "user", purge: bool = False) -> bool:
        return self.store.tombstone_memory(memory_id, reason="user_forget", actor=actor, purge=purge)

    def restore(self, memory_id: str, revision: int, *, actor: str = "user") -> dict[str, Any]:
        return self.store.restore_version(memory_id, revision, actor=actor, reason="user restore")


class TemporaryChat:
    """Explicit no-read/no-write memory boundary for temporary conversations."""

    def query(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "disabled", "reason": "temporary_chat", "items": [], "candidates": []}

    def write(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "blocked", "reason": "temporary_chat"}

    def prompt_context(self) -> list[dict[str, Any]]:
        return []


class ExternalWriteGate:
    """Preview/approval/idempotency shell; disabled until a separate review."""

    def __init__(self, handler=None, *, enabled: bool = False):
        self.handler = handler
        self.enabled = bool(enabled)
        self._previews: dict[str, dict[str, Any]] = {}
        self._executed: set[str] = set()
        self.audit_log: list[dict[str, Any]] = []

    def preview(self, capability: str, arguments: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValidationError("idempotency_key is required")
        digest = hashlib.sha256(json.dumps(dict(arguments or {}), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        preview = {"status": "approval_required", "capability": str(capability), "idempotency_key": key, "arguments_hash": digest, "arguments": {str(k): "[REDACTED]" for k in (arguments or {})}}
        self._previews[key] = preview
        self.audit_log.append({"operation": "preview", "capability": str(capability), "idempotency_key": key, "arguments_hash": digest, "status": preview["status"]})
        return preview

    def execute(self, capability: str, arguments: Mapping[str, Any], *, idempotency_key: str, approved: bool = False) -> dict[str, Any]:
        key = str(idempotency_key or "")
        if not self.enabled or self.handler is None:
            self.audit_log.append({"operation": "execute", "capability": str(capability), "idempotency_key": key, "status": "disabled"})
            return {"status": "disabled", "reason": "external_write_tools_disabled"}
        if not approved:
            self.audit_log.append({"operation": "execute", "capability": str(capability), "idempotency_key": key, "status": "approval_required"})
            return {"status": "approval_required", "idempotency_key": key}
        preview = self._previews.get(key)
        if preview is None:
            self.audit_log.append({"operation": "execute", "capability": str(capability), "idempotency_key": key, "status": "preview_required"})
            return {"status": "preview_required", "idempotency_key": key}
        if key in self._executed:
            self.audit_log.append({"operation": "execute", "capability": str(capability), "idempotency_key": key, "status": "already_executed"})
            return {"status": "already_executed", "idempotency_key": key}
        digest = hashlib.sha256(json.dumps(dict(arguments or {}), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if digest != preview["arguments_hash"] or str(capability) != preview["capability"]:
            self.audit_log.append({"operation": "execute", "capability": str(capability), "idempotency_key": key, "status": "preview_mismatch"})
            return {"status": "preview_mismatch", "idempotency_key": key}
        try:
            result = self.handler(str(capability), dict(arguments))
        except Exception as exc:
            self.audit_log.append({"operation": "execute", "capability": str(capability), "idempotency_key": key, "status": "error"})
            return {"status": "error", "idempotency_key": key, "error": str(exc)[:160]}
        self._executed.add(key)
        self.audit_log.append({"operation": "execute", "capability": str(capability), "idempotency_key": key, "status": "success"})
        return {"status": "success", "idempotency_key": key, "result": str(result)[:500]}
