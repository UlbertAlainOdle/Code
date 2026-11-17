"""Read-only planning and idempotent application of legacy memory data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Mapping
import uuid

from .models import DayState, MemoryCandidateV4, MemoryEvidence, MemoryItemV4, MemoryProposal
from .store import MemoryV4Store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_date(value: Any, fallback: str | None = None) -> str:
    text = str(value or "")[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        return fallback or datetime.now().strftime("%Y-%m-%d")


def _bounded_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _nonnegative_number(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _safe_timestamp(value: Any, fallback: str | None = None) -> str:
    text = str(value or "").strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except ValueError:
        return fallback or _now()


def _slug(value: Any, default: str = "memory") -> str:
    text = re.sub(r"[^a-z0-9_]+", "_", str(value or "").lower()).strip("_")
    return text[:48] or default


def _is_secret(text: str) -> bool:
    return bool(re.search(
        r"(?i)(?:password|passwd|api[ _-]?key|access[ _-]?token|bearer\s+[a-z0-9._-]+|secret\s*[:=]|sk-[a-z0-9]{12,})",
        text,
    ))


@dataclass
class MigrationReport:
    source_count: int = 0
    unchanged: list[dict[str, Any]] = field(default_factory=list)
    converted: list[dict[str, Any]] = field(default_factory=list)
    split_proposals: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    suggest_unpin: list[dict[str, Any]] = field(default_factory=list)
    suggest_unlock: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    index_diff: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    tombstones: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    day_states: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(default_factory=_now)
    schema_version: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "source_count": self.source_count,
            "unchanged": self.unchanged,
            "converted": self.converted,
            "split_proposals": self.split_proposals,
            "quarantined": self.quarantined,
            "suggest_unpin": self.suggest_unpin,
            "suggest_unlock": self.suggest_unlock,
            "conflicts": self.conflicts,
            "index_diff": self.index_diff,
            "warnings": self.warnings,
            "tombstones": self.tombstones,
            "candidates": self.candidates,
            "day_states": self.day_states,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MigrationReport":
        names = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: data[key] for key in names if key in data})


class MemoryV4Migration:
    """Plan from legacy JSON without touching it, then apply to a V4 store."""

    SOURCE_FILES = (
        "knowledge.json", "memory_deletions.json", "memory_candidates.json", "memory_settings.json",
        "memory3.sqlite3",
    )

    def __init__(self, kb_dir: str | os.PathLike[str], v4_path: str | os.PathLike[str] | None = None):
        self.kb_dir = Path(kb_dir).resolve()
        self.v4_path = Path(v4_path or (self.kb_dir / "memory4.sqlite3")).resolve()
        self.backup_dir = self.kb_dir / "backups"
        self._last_report: dict[str, Any] | None = None

    def _load(self, name: str, default: Any) -> Any:
        path = self.kb_dir / name
        if not path.is_file():
            return default
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                return json.load(handle)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Unable to read {name}: {exc}") from exc

    def _records(self) -> list[dict[str, Any]]:
        records = self._load("knowledge.json", [])
        if records is None:
            return []
        if not isinstance(records, list):
            raise ValueError("knowledge.json must contain a list")
        return [record for record in records if isinstance(record, dict) and str(record.get("id") or "").strip()]

    def _deleted(self) -> dict[str, Any]:
        loaded = self._load("memory_deletions.json", {})
        if isinstance(loaded, dict) and isinstance(loaded.get("deleted"), dict):
            return {str(key): value for key, value in loaded["deleted"].items() if key}
        if isinstance(loaded, list):
            return {str(key): {} for key in loaded if key}
        return {}

    @staticmethod
    def _is_daily(record: Mapping[str, Any]) -> bool:
        return (
            str(record.get("id") or "") == "system-daily-activity-current"
            or str(record.get("managed_slot") or "") == "daily_activity:today"
            or str(record.get("memory_kind") or "") == "daily_activity"
        )

    @staticmethod
    def _is_anki(record: Mapping[str, Any]) -> bool:
        return (
            str(record.get("id") or "") == "system-anki-checkin-status"
            or str(record.get("managed_slot") or "") == "anki:checkin_status"
            or str(record.get("source") or "") == "anki"
        )

    @staticmethod
    def _dangling_reason(record: Mapping[str, Any], content: str) -> str | None:
        source = str(record.get("source") or "").lower()
        if source != "auto_explicit":
            return None
        if not content or len(content) < 12:
            return "dangling_short"
        if re.match(r"^(?:所以|但是|然后|而且|因此|因为|并且|你的人)", content):
            return "dangling_start"
        if content.endswith(("…", "...", "…")):
            return "dangling_ellipsis"
        if re.search(r"(?:了吗|呢|吗|多少|什么|是不是)[？?。]?$", content):
            return "residual_question_tail"
        return None

    def _day_state(self, record: Mapping[str, Any]) -> DayState:
        date = _safe_date(record.get("date") or record.get("creation_time"))
        now = str(record.get("last_updated") or record.get("creation_time") or _now())
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        source_detail = record.get("source_detail") if isinstance(record.get("source_detail"), dict) else {}
        stats = metadata.get("chat_stats") if isinstance(metadata.get("chat_stats"), dict) else {}
        if not stats:
            stats = source_detail.get("chat_stats") if isinstance(source_detail.get("chat_stats"), dict) else {}
        return DayState(
            date=date,
            timezone=str(metadata.get("timezone") or source_detail.get("timezone") or "Asia/Shanghai"),
            first_contact_at=_safe_timestamp(metadata.get("first_contact_at") or record.get("creation_time") or now, now),
            last_contact_at=_safe_timestamp(now, _now()),
            page_load_count=_nonnegative_number(metadata.get("page_load_count")),
            backend_start_count=_nonnegative_number(metadata.get("backend_start_count")),
            conversation_count=_nonnegative_number(stats.get("conversation_count") or metadata.get("conversation_count")),
            task_snapshot=metadata.get("task_snapshot") if isinstance(metadata.get("task_snapshot"), list) else [],
            carryover_task_ids=[str(item) for item in (metadata.get("carryover_task_ids") or [])],
            weather_snapshot=metadata.get("weather_snapshot") if isinstance(metadata.get("weather_snapshot"), dict) else None,
            external_attention_items=[str(item) for item in (metadata.get("external_attention_items") or [])],
            purge_after=(datetime.strptime(date, "%Y-%m-%d") + timedelta(days=4)).strftime("%Y-%m-%d"),
        )

    def _memory_item(self, record: Mapping[str, Any]) -> tuple[MemoryItemV4 | None, str | None, dict[str, Any]]:
        memory_id = str(record.get("id") or "").strip()
        content = str(record.get("content") or "").strip()
        risk = self._dangling_reason(record, content)
        if risk:
            return None, risk, {"id": memory_id, "source": record.get("source"), "preview": content[:240]}
        if _is_secret(content):
            return None, "secret_content", {"id": memory_id, "source": record.get("source"), "preview": "[REDACTED]"}
        if not content:
            return None, "empty_content", {"id": memory_id, "source": record.get("source"), "preview": ""}

        source = str(record.get("source") or "manual").strip().lower()
        category = _slug(record.get("category"), "general")
        legacy_layer = str(record.get("memory_layer") or "semantic")
        project_id = str(record.get("project_id") or "").strip()
        if project_id:
            scope_type, scope_id = "project", project_id
        elif source in {"learner", "web", "connector", "anki"} or "learner" in source:
            scope_type, scope_id = "connector", source
        else:
            scope_type, scope_id = "global", None

        if self._is_anki(record):
            kind, subject, authority, activation = "event", "study_state", "external", "event"
        elif source in {"learner", "web", "connector"} or "learner" in source:
            kind, subject, authority, activation = "knowledge", "world", "external", "query"
        elif source == "session_archive" or category == "conversation_summary" or legacy_layer == "episodic":
            kind, subject, authority, activation = "event", "user", "observed", "event"
        elif source == "about_you" or category in {"identity", "preference", "relationship"}:
            kind, subject, authority, activation = "identity" if category == "identity" else category, "user", "user_confirmed", "query"
        elif category == "goal" or "quest" in str(record.get("title") or "").lower():
            kind, subject, authority, activation = "goal", "user", "user_confirmed", "query"
        elif category in {"procedure", "reflection"}:
            kind, subject, authority, activation = category, "user", "observed", "query"
        else:
            kind, subject, authority, activation = "knowledge", "user", "observed", "query"

        title = _clean_text(record.get("title"))
        predicate = f"legacy_{category}_{_slug(title, 'memory')}"[:96]
        metadata = {
            "legacy_id": memory_id,
            "legacy_title": title,
            "legacy_category": record.get("category"),
            "legacy_tags": record.get("tags") if isinstance(record.get("tags"), list) else [],
            "legacy_status": record.get("status"),
        }
        if source == "assistant_role":
            metadata["role_mode"] = "assistant"
        if source in {"auto_autonomous", "autonomous", "candidate", "write_pipeline"}:
            authority = "user_confirmed" if record.get("user_confirmed") else "inferred"
        admission=(record.get('source_detail') or {}) if isinstance(record.get('source_detail'),dict) else {}
        qq_admitted=(source in {'qq_daily','qq_important'} and record.get('user_confirmed') is True
            and admission.get('type')=='qq_owner_admission' and re.fullmatch(r'[a-f0-9]{32}',str(admission.get('operation_id','')))
            and (record.get('origin') or {}).get('channel')=='qq' and (record.get('origin') or {}).get('verification')=='verified')
        if qq_admitted:authority='user_confirmed'
        if isinstance(record.get("source_detail"), dict):
            metadata["legacy_source_detail"] = dict(record["source_detail"])
        from ..memory_origin import projection
        metadata.update(projection(record))
        from ..memory_importance import projection as importance_projection
        metadata.update(importance_projection(record))
        created_at = str(record.get("creation_time") or record.get("date") or _now())
        updated_at = str(record.get("last_updated") or created_at)
        try:
            item = MemoryItemV4(
                id=memory_id,
                subject=subject,
                predicate=predicate,
                value=content,
                canonical_text=content,
                language="zh-CN" if re.search(r"[\u3400-\u9fff]", content) else "en",
                kind=kind,
                scope_type=scope_type,
                scope_id=scope_id,
                authority=authority,
                sensitivity="personal" if source == "about_you" else "normal",
                valid_from=_safe_date(record.get("date") or created_at),
                valid_to=(str(record["expires_at"])[:10] + "T23:59:59") if record.get("expires_at") else None,
                last_confirmed_at=_safe_timestamp(created_at) if authority == "user_confirmed" else None,
                lifecycle="archived" if record.get("is_archived") else "superseded" if record.get("superseded_by") else "active",
                confidence=_bounded_number(record.get("confidence", 0.75) or 0.75, 0.75),
                salience=_bounded_number(record.get("importance_score", 0.4) or 0.4, 0.4),
                pinned=bool(record.get("is_important")),
                pin_source="system" if source.startswith("system") else ("user" if record.get("is_important") else None),
                locked=bool(record.get("is_locked")),
                activation_policy=activation,
                candidate_shown_count=0,
                used_count=_nonnegative_number(record.get("access_count")),
                last_used_at=_safe_timestamp(record.get("last_accessed"), None) if record.get("last_accessed") else None,
                created_at=_safe_timestamp(created_at),
                updated_at=_safe_timestamp(updated_at, _safe_timestamp(created_at)),
                revision=1,
                legacy_layer=legacy_layer,
                legacy_source=source,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            return None, "schema_invalid", {"id": memory_id, "source": source, "preview": content[:240], "error": str(exc)[:180]}
        if qq_admitted:
            item.metadata['qq_owner_admission']=admission['operation_id']
            item.metadata['evidence_refs']=[e.id for e in self._evidence_for(record,item)]
        preview = {"id": memory_id, "source": source, "kind": kind, "lifecycle": item.lifecycle, "canonical_text": content[:240]}
        return item, None, preview

    def _evidence_for(self, record: Mapping[str, Any], item: MemoryItemV4) -> list[MemoryEvidence]:
        raw = record.get("evidence") if isinstance(record.get("evidence"), list) else []
        detail=record.get('source_detail') if isinstance(record.get('source_detail'),dict) else {}
        revision=detail.get('qq_confirmed_revision') if isinstance(detail.get('qq_confirmed_revision'),dict) else {}
        admitted=record.get('source') in {'qq_daily','qq_important'} and detail.get('type')=='qq_owner_admission'
        confirmed_revision=bool(re.fullmatch(r'[a-f0-9]{32}',str(revision.get('operation_id',''))))
        if confirmed_revision and isinstance(revision.get('evidence'),list):raw=[*raw,*revision['evidence']]
        if len(raw)>32 and (admitted or confirmed_revision):raw=[raw[0],*raw[-31:]]
        if not raw:
            raw = [{"quote": item.canonical_text, "type": "legacy_import"}]
        result = []
        for index, entry in enumerate(raw[:32 if admitted or confirmed_revision else 8]):
            entry = entry if isinstance(entry, dict) else {}
            quote = _clean_text(entry.get("quote") or item.canonical_text)[:2000]
            evidence_id = f"legacy-evidence:{item.id}:{index}:{hashlib.sha256(quote.encode('utf-8')).hexdigest()[:12]}"
            source_type = str(entry.get("source_type") or entry.get("type") or "system")
            try:
                quote_start = _nonnegative_number(entry.get("quote_start"), 0) if entry.get("quote_start") is not None else None
                quote_end = _nonnegative_number(entry.get("quote_end"), 0) if entry.get("quote_end") is not None else None
                if quote_start is not None and quote_end is not None and quote_end < quote_start:
                    quote_start, quote_end = None, None
                result.append(MemoryEvidence(
                id=evidence_id,
                memory_id=item.id,
                source_type=source_type,
                source_id=str(entry.get("source_id") or f"legacy:{item.id}"),
                role=entry.get("role"),
                quote=quote,
                quote_start=quote_start,
                quote_end=quote_end,
                observed_at=_safe_timestamp(entry.get("observed_at") or entry.get("created_at") or item.created_at, item.created_at),
                ))
            except ValueError:
                result.append(MemoryEvidence(
                    id=evidence_id, memory_id=item.id, source_type="system", source_id=f"legacy:{item.id}",
                    role=None, quote=quote, observed_at=item.created_at,
                ))
        return result

    def _candidate(self, raw: Mapping[str, Any]) -> MemoryCandidateV4 | None:
        content = _clean_text(raw.get("content"))
        if not content or _is_secret(content):
            return None
        proposal = MemoryProposal(
            canonical_text=content,
            subject="user",
            predicate=f"legacy_{_slug(raw.get('category'), 'general')}_{_slug(raw.get('title'), 'memory')}"[:96],
            value=content,
            language="zh-CN" if re.search(r"[\u3400-\u9fff]", content) else "en",
            kind="knowledge",
            authority="inferred",
            confidence=_bounded_number(raw.get("confidence", 0.5) or 0.5, 0.5),
            salience=_bounded_number(raw.get("importance_score", 0.4) or 0.4, 0.4),
            metadata={"legacy_source": raw.get("source", "auto_autonomous")},
        )
        status = str(raw.get("status") or "pending").lower()
        status = {"ignored": "rejected", "confirmed": "accepted", "adopted": "merged"}.get(status, status)
        if status not in {"pending", "accepted", "rejected", "merged", "expired"}:
            status = "pending"
        try:
            return MemoryCandidateV4(
                id=str(raw.get("id") or f"legacy-candidate:{hashlib.sha256(content.encode('utf-8')).hexdigest()[:20]}"),
                proposal=proposal.to_dict(),
                evidence_ids=[str(item) for item in (raw.get("evidence_ids") or [])],
                status=status,
                occurrence_count=max(1, _nonnegative_number(raw.get("occurrence_count"), 1)),
                first_seen_at=_safe_timestamp(raw.get("creation_time") or raw.get("first_seen_at") or _now()),
                last_seen_at=_safe_timestamp(raw.get("last_updated") or raw.get("last_seen_at") or raw.get("creation_time") or _now()),
                review_reason="legacy candidate; age alone does not accept it",
            )
        except (TypeError, ValueError):
            return None

    def _existing_ids(self) -> set[str]:
        if not self.v4_path.is_file():
            return set()
        try:
            connection = sqlite3.connect(f"file:{self.v4_path.as_posix()}?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT id FROM memory_items").fetchall()
            finally:
                connection.close()
            return {str(row[0]) for row in rows}
        except sqlite3.Error:
            return set()

    def preview(self) -> dict[str, Any]:
        report = MigrationReport()
        records = self._records()
        deleted = self._deleted()
        report.source_count = len(records)
        seen_keys: dict[tuple[str, str, str, str], str] = {}
        existing_ids = self._existing_ids()
        for record in records:
            memory_id = str(record.get("id") or "")
            if memory_id in deleted or record.get("is_deleted"):
                report.tombstones.append({"id": memory_id, "reason": "legacy_deleted", "source": "memory_deletions.json"})
                continue
            item, reason, details = self._memory_item(record)
            if item is None:
                report.quarantined.append({**details, "reason": reason or "invalid_record", "action": "hold_for_review"})
                continue
            if item.id in existing_ids:
                report.unchanged.append({"id": item.id, "reason": "already_present"})
            else:
                report.converted.append(details)
            if item.canonical_key in seen_keys and seen_keys[item.canonical_key] != item.id:
                report.conflicts.append({"key": list(item.canonical_key), "ids": [seen_keys[item.canonical_key], item.id], "reason": "duplicate_update_key"})
            else:
                seen_keys[item.canonical_key] = item.id
            source = str(record.get("source") or "").lower()
            if source == "session_archive" and record.get("is_important"):
                report.suggest_unpin.append({"id": item.id, "reason": "legacy_session_summary_starred"})
            if source == "session_archive" and record.get("is_locked"):
                report.suggest_unlock.append({"id": item.id, "reason": "legacy_session_summary_locked"})
            if self._is_daily(record):
                # Daily activity is a DayState, not a Core memory.  It is kept
                # in converted for a visible preview of the destination.
                report.converted = [entry for entry in report.converted if entry.get("id") != item.id]
                report.split_proposals.append({"id": item.id, "target": "day_states", "date": _safe_date(record.get("date")), "reason": "daily_activity_to_day_state"})
                report.day_states.append(self._day_state(record).to_dict())
            elif self._is_anki(record):
                details["target"] = "study_state_memory"

        raw_candidates = self._load("memory_candidates.json", [])
        if isinstance(raw_candidates, list):
            for raw in raw_candidates:
                if not isinstance(raw, dict):
                    continue
                candidate = self._candidate(raw)
                if candidate is None:
                    report.warnings.append(f"candidate {raw.get('id', '')} omitted: empty or secret content")
                    continue
                report.candidates.append(candidate.to_dict())

        for memory_id, details in deleted.items():
            if not any(entry.get("id") == memory_id for entry in report.tombstones):
                report.tombstones.append({"id": memory_id, "reason": (details or {}).get("reason", "legacy_deleted") if isinstance(details, dict) else "legacy_deleted", "source": "memory_deletions.json"})

        report.index_diff = {
            "v4_path": str(self.v4_path),
            "existing_memory_count": len(existing_ids),
            "would_add": len(report.converted),
            "would_update": 0,
            "would_skip": len(report.unchanged),
            "would_tombstone": len(report.tombstones),
            "would_create_day_states": len(report.day_states),
        }
        if not (self.kb_dir / "knowledge.json").is_file():
            report.warnings.append("knowledge.json is missing; no legacy memories were read")
        self._last_report = report.to_dict()
        return self._last_report

    def _backup_sources(self) -> str:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        target = self.backup_dir / f"memory-v4-migration.{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        target.mkdir()
        manifest: dict[str, Any] = {"schema_version": 1, "created_at": _now(), "files": []}
        for name in self.SOURCE_FILES:
            source = self.kb_dir / name
            if not source.is_file():
                continue
            destination = target / name
            shutil.copy2(source, destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            manifest["files"].append({"path": name, "size": destination.stat().st_size, "sha256": digest})
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(target)

    def apply(self, report: Mapping[str, Any] | None = None) -> dict[str, Any]:
        plan = dict(report or self.preview())
        backup_path = self._backup_sources()
        store = MemoryV4Store(self.v4_path)
        changed = 0
        skipped = 0
        records = self._records()
        deleted = self._deleted()
        for record in records:
            memory_id = str(record.get("id") or "")
            if memory_id in deleted or record.get("is_deleted"):
                continue
            item, reason, _ = self._memory_item(record)
            if item is None or self._is_daily(record):
                continue
            # A rerun must never overwrite a V4 revision that a user or a
            # later pipeline has edited.  The initial import is source-owned;
            # subsequent reconciliation belongs to an explicit tool.
            if store.get_memory(item.id) is not None:
                skipped += 1
                continue
            evidence = self._evidence_for(record, item)
            result = store.upsert_memory(item, evidence, operation="migrate", reason="legacy JSON migration", actor="migration")
            if result["status"] == "unchanged":
                skipped += 1
            else:
                changed += 1
        for raw in plan.get("candidates", []):
            if isinstance(raw, dict):
                store.upsert_candidate(raw)
        for raw in plan.get("day_states", []):
            if isinstance(raw, dict):
                store.upsert_day_state(raw)
        for tombstone in plan.get("tombstones", []):
            if isinstance(tombstone, dict) and tombstone.get("id"):
                if not store.is_tombstoned(tombstone["id"]):
                    store.tombstone_memory(tombstone["id"], reason=tombstone.get("reason", "legacy_deleted"), actor="migration", purge=True, allow_missing=True)
                    changed += 1
                else:
                    skipped += 1
        integrity = store.integrity_check()
        return {
            "status": "success",
            "schema_version": 4,
            "v4_path": str(self.v4_path),
            "backup_path": backup_path,
            "changed_count": changed,
            "skipped_count": skipped,
            "idempotent": skipped > 0 or changed == 0,
            "integrity": integrity,
            "report": plan,
        }

    def export(self) -> dict[str, Any]:
        return MemoryV4Store(self.v4_path).export_state()
