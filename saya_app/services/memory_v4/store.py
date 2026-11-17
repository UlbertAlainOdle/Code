"""SQLite repository for the V4 memory contracts.

This database is a source-of-truth candidate for V4 only.  The legacy
``memory3.sqlite3`` remains untouched by this module; a caller explicitly
chooses when to create the V4 database and when to enable a dual write.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any, Iterable, Mapping
from ..qq_tasks import guarded_store_write

from .models import (
    DayState,
    MemoryCandidateV4,
    MemoryEvidence,
    MemoryItemV4,
    MemoryVersion,
    TurnEvent,
    ValidationError,
    _validate_timestamp,
    validate_memory_item,
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MemoryV4Store:
    """Transactional repository with immutable version rows and tombstones."""

    SCHEMA_VERSION = 4

    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(Path(path).resolve())
        self._lock = threading.RLock()
        self._write_context = threading.local()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def _connection(self):
        active = getattr(self._write_context, 'connection', None)
        if active is not None:
            yield active
            return
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def write_versions(self):
        """Snapshot native targets before extraction or validation can wait."""
        from ..memory_health import native_version
        with self._lock, self._connection() as connection:
            connection.execute('BEGIN')
            rows = connection.execute('SELECT * FROM memory_items').fetchall()
            tombstones = {row[0] for row in connection.execute('SELECT id FROM memory_tombstones')}
            result = {row['id']: native_version(self._row_to_item(row), row['id'] in tombstones) for row in rows}
            for identifier in tombstones - result.keys():
                result[identifier] = native_version(None, True)
            return result

    @contextmanager
    def checked_write(self, versions, authorize):
        """Compare and mutate on one SQLite transaction, including nested writes."""
        from ..memory_health import native_version
        from ..memory_versions import MemoryVersionConflict
        with self._lock:
            if getattr(self._write_context, 'connection', None) is not None:
                raise RuntimeError('nested_native_write_plan')
            connection = self._connect()
            try:
                connection.execute('BEGIN IMMEDIATE')
                self._write_context.connection = connection
                authorize()
                for identifier, expected in versions.items():
                    row = connection.execute('SELECT * FROM memory_items WHERE id=?', (identifier,)).fetchone()
                    tombstone = connection.execute('SELECT 1 FROM memory_tombstones WHERE id=?', (identifier,)).fetchone()
                    actual = native_version(self._row_to_item(row) if row else None, tombstone)
                    if actual != expected:
                        raise MemoryVersionConflict('native_write_target_changed')
                yield
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._write_context.connection = None
                connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    canonical_text TEXT NOT NULL,
                    language TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT,
                    authority TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    last_confirmed_at TEXT,
                    lifecycle TEXT NOT NULL,
                    supersedes_id TEXT,
                    confidence REAL NOT NULL,
                    salience REAL NOT NULL,
                    pinned INTEGER NOT NULL,
                    pin_source TEXT,
                    locked INTEGER NOT NULL,
                    activation_policy TEXT NOT NULL,
                    candidate_shown_count INTEGER NOT NULL,
                    used_count INTEGER NOT NULL,
                    last_used_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    legacy_layer TEXT,
                    legacy_source TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v4_items_lifecycle
                    ON memory_items(lifecycle, scope_type, scope_id);
                CREATE INDEX IF NOT EXISTS idx_v4_items_key
                    ON memory_items(predicate, scope_type, scope_id, subject);
                CREATE TABLE IF NOT EXISTS memory_evidence (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    role TEXT,
                    quote TEXT NOT NULL,
                    quote_start INTEGER,
                    quote_end INTEGER,
                    content_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v4_evidence_memory
                    ON memory_evidence(memory_id, observed_at);
                CREATE TABLE IF NOT EXISTS memory_versions (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v4_versions_memory
                    ON memory_versions(memory_id, revision DESC);
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    id TEXT PRIMARY KEY,
                    proposal_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    review_reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v4_candidates_status
                    ON memory_candidates(status, last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS day_states (
                    date TEXT PRIMARY KEY,
                    timezone TEXT NOT NULL,
                    first_contact_at TEXT,
                    last_contact_at TEXT,
                    page_load_count INTEGER NOT NULL,
                    backend_start_count INTEGER NOT NULL,
                    conversation_count INTEGER NOT NULL,
                    task_snapshot_json TEXT NOT NULL,
                    carryover_task_ids_json TEXT NOT NULL,
                    weather_snapshot_json TEXT,
                    external_attention_items_json TEXT NOT NULL,
                    frozen_at TEXT,
                    purge_after TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turn_events (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    public_payload_json TEXT NOT NULL,
                    private_payload_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v4_events_turn
                    ON turn_events(turn_id, sequence);
                CREATE TABLE IF NOT EXISTS memory_usage_events (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    turn_id TEXT,
                    stage TEXT NOT NULL,
                    prompt_included INTEGER NOT NULL DEFAULT 0,
                    used INTEGER NOT NULL DEFAULT 0,
                    feedback TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v4_usage_memory
                    ON memory_usage_events(memory_id, created_at);
                CREATE TABLE IF NOT EXISTS connector_events (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    author_display TEXT NOT NULL,
                    channel_id TEXT,
                    thread_id TEXT,
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    raw_url TEXT,
                    trust TEXT NOT NULL,
                    permission_scope TEXT NOT NULL,
                    read_state TEXT NOT NULL,
                    dedupe_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_connector_source_event
                    ON connector_events(source, source_event_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_connector_dedupe
                    ON connector_events(dedupe_hash);
                CREATE TABLE IF NOT EXISTS memory_tombstones (
                    id TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL
                );
                """
            )
            self._set_meta(connection, "schema_version", str(self.SCHEMA_VERSION))

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            "INSERT INTO memory_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(key), str(value)),
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _loads(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _item_values(item: MemoryItemV4) -> tuple[Any, ...]:
        return (
            item.id, item.schema_version, item.subject, item.predicate, MemoryV4Store._json(item.value),
            item.canonical_text, item.language, item.kind, item.scope_type, item.scope_id,
            item.authority, item.sensitivity, item.valid_from, item.valid_to, item.last_confirmed_at,
            item.lifecycle, item.supersedes_id, item.confidence, item.salience, int(item.pinned),
            item.pin_source, int(item.locked), item.activation_policy, item.candidate_shown_count,
            item.used_count, item.last_used_at, item.created_at, item.updated_at, item.revision,
            item.legacy_layer, item.legacy_source, MemoryV4Store._json(item.metadata),
        )

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> MemoryItemV4:
        data = dict(row)
        data["value"] = MemoryV4Store._loads(data.pop("value_json"), "")
        data["metadata"] = MemoryV4Store._loads(data.pop("metadata_json"), {})
        data["pinned"] = bool(data["pinned"])
        data["locked"] = bool(data["locked"])
        return MemoryItemV4.from_dict(data)

    @staticmethod
    def _evidence(evidence: MemoryEvidence | Mapping[str, Any], memory_id: str) -> MemoryEvidence:
        item = evidence if isinstance(evidence, MemoryEvidence) else MemoryEvidence.from_dict(evidence)
        if item.memory_id != memory_id:
            raise ValidationError("evidence memory_id does not match the memory")
        return item

    @staticmethod
    def _same_logical_item(left: MemoryItemV4, right: MemoryItemV4) -> bool:
        """Ignore write timestamps/revision when checking an idempotent replay."""
        left_data, right_data = left.to_dict(), right.to_dict()
        for data in (left_data, right_data):
            data.pop("revision", None)
            data.pop("created_at", None)
            data.pop("updated_at", None)
        return left_data == right_data

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT value FROM memory_meta WHERE key=?", (str(key),)).fetchone()
        return default if row is None else row["value"]

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock, self._connection() as connection:
            self._set_meta(connection, key, value)

    def is_tombstoned(self, memory_id: str) -> bool:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT 1 FROM memory_tombstones WHERE id=?", (str(memory_id),)).fetchone()
        return row is not None

    def get_memory(self, memory_id: str) -> MemoryItemV4 | None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM memory_items WHERE id=?", (str(memory_id),)).fetchone()
        return self._row_to_item(row) if row else None

    def list_memories(self, lifecycle: str | None = None, limit: int = 1000, offset: int = 0) -> list[MemoryItemV4]:
        limit = max(1, min(100000, int(limit)))
        offset = max(0, int(offset))
        sql = "SELECT * FROM memory_items"
        params: list[Any] = []
        if lifecycle:
            sql += " WHERE lifecycle=?"
            params.append(str(lifecycle))
        sql += " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get_memories(self, memory_ids: Iterable[str]) -> list[MemoryItemV4]:
        ids = list(dict.fromkeys(str(i) for i in memory_ids if i))[:900]
        if not ids:
            return []
        with self._lock, self._connection() as connection:
            rows = connection.execute('SELECT * FROM memory_items WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
        return [self._row_to_item(row) for row in rows]

    def search_native_candidate_ids(self,query,*,limit=80,scope_type=None,scope_id=None):
        """Supplement the legacy FTS pool with unmirrored V4 rows, read-only."""
        from .retrieval import _tokens
        terms=sorted(_tokens(query),key=lambda term:(-len(term),term))[:24]
        if not terms:return []
        # All text is bound data; instr has no wildcard or query-language syntax.
        clauses=['CASE WHEN instr(lower(canonical_text),?)>0 THEN 1 ELSE 0 END' for _ in terms]
        score=' + '.join(clauses);params=list(terms)
        where="json_extract(metadata_json,'$.legacy_id') IS NULL"
        if scope_type:
            where+=' AND scope_type IN (?,?)';params.extend(['global',scope_type])
        else:
            where+=" AND (scope_type='global' OR (scope_type='connector' AND kind='knowledge' AND subject='world' AND sensitivity='normal'))"
        if scope_id:
            where+=" AND (scope_type='global' OR scope_id=?)";params.append(scope_id)
        params.append(max(1,min(1000,int(limit))))
        with self._lock,self._connection() as db:
            rows=db.execute(f'SELECT id,({score}) AS matched FROM memory_items WHERE {where} AND lifecycle=\'active\' '
                            'ORDER BY matched DESC,updated_at DESC,id LIMIT ?',params).fetchall()
        return [row['id'] for row in rows if row['matched']>0]

    def find_by_key(self, predicate: str, scope_type: str = "global", scope_id: str | None = None, subject: str = "user") -> MemoryItemV4 | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE predicate=? AND scope_type=? AND "
                "COALESCE(scope_id, '')=COALESCE(?, '') AND subject=? "
                "ORDER BY revision DESC LIMIT 1",
                (str(predicate), str(scope_type), scope_id, str(subject)),
            ).fetchone()
        return self._row_to_item(row) if row else None

    @guarded_store_write
    def upsert_memory(
        self,
        memory: MemoryItemV4 | Mapping[str, Any],
        evidence: Iterable[MemoryEvidence | Mapping[str, Any]] | None = None,
        *,
        operation: str = "upsert",
        reason: str = "",
        actor: str = "system",
        allow_resurrect: bool = False,
    ) -> dict[str, Any]:
        item = validate_memory_item(memory)
        evidence_items = [self._evidence(entry, item.id) for entry in (evidence or [])]
        with self._lock, self._connection() as connection:
            if connection.execute("SELECT 1 FROM memory_tombstones WHERE id=?", (item.id,)).fetchone() and not allow_resurrect:
                raise ValueError("memory id is tombstoned; explicit restore is required")
            row = connection.execute("SELECT * FROM memory_items WHERE id=?", (item.id,)).fetchone()
            existing = self._row_to_item(row) if row else None
            if existing is None and not evidence_items:
                raise ValidationError("a new memory requires at least one evidence record")
            if existing and self._same_logical_item(existing, item):
                existing_evidence = {
                    str(entry[0]) for entry in connection.execute(
                        "SELECT id FROM memory_evidence WHERE memory_id=?", (item.id,)
                    ).fetchall()
                }
                missing_evidence = [entry for entry in evidence_items if entry.id not in existing_evidence]
                if not missing_evidence:
                    return {"status": "unchanged", "memory": existing.to_dict(), "revision": existing.revision}
                for evidence_item in missing_evidence:
                    connection.execute(
                        "INSERT OR IGNORE INTO memory_evidence(" 
                        "id,memory_id,source_type,source_id,role,quote,quote_start,quote_end,content_hash,observed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence_item.id, evidence_item.memory_id, evidence_item.source_type, evidence_item.source_id,
                            evidence_item.role, evidence_item.quote, evidence_item.quote_start, evidence_item.quote_end,
                            evidence_item.content_hash, evidence_item.observed_at,
                        ),
                    )
                return {
                    "status": "unchanged", "memory": existing.to_dict(), "revision": existing.revision,
                    "evidence_added": len(missing_evidence),
                }
            if existing:
                item.created_at = existing.created_at
                item.revision = existing.revision + 1
                if item.updated_at == existing.updated_at:
                    item.updated_at = _now()
            if not existing and item.revision < 1:
                item.revision = 1
            connection.execute(
                "INSERT INTO memory_items VALUES(" + ",".join("?" for _ in range(32)) + ") "
                "ON CONFLICT(id) DO UPDATE SET " + ",".join(
                    f"{column}=excluded.{column}" for column in (
                        "schema_version", "subject", "predicate", "value_json", "canonical_text", "language",
                        "kind", "scope_type", "scope_id", "authority", "sensitivity", "valid_from", "valid_to",
                        "last_confirmed_at", "lifecycle", "supersedes_id", "confidence", "salience", "pinned",
                        "pin_source", "locked", "activation_policy", "candidate_shown_count", "used_count",
                        "last_used_at", "created_at", "updated_at", "revision", "legacy_layer", "legacy_source",
                        "metadata_json",
                    )
                ),
                self._item_values(item),
            )
            for evidence_item in evidence_items:
                connection.execute(
                    "INSERT OR IGNORE INTO memory_evidence(" 
                    "id,memory_id,source_type,source_id,role,quote,quote_start,quote_end,content_hash,observed_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        evidence_item.id, evidence_item.memory_id, evidence_item.source_type, evidence_item.source_id,
                        evidence_item.role, evidence_item.quote, evidence_item.quote_start, evidence_item.quote_end,
                        evidence_item.content_hash, evidence_item.observed_at,
                    ),
                )
            version = MemoryVersion(
                id=str(uuid.uuid4()), memory_id=item.id, revision=item.revision,
                operation=str(operation or "upsert"), snapshot=item.to_dict(), reason=reason, actor=actor,
            )
            connection.execute(
                "INSERT INTO memory_versions(id,memory_id,revision,operation,snapshot_json,reason,actor,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    version.id, version.memory_id, version.revision, version.operation,
                    self._json(version.snapshot), version.reason, version.actor, version.created_at,
                ),
            )
            if allow_resurrect:
                connection.execute("DELETE FROM memory_tombstones WHERE id=?", (item.id,))
        return {"status": "created" if existing is None else "updated", "memory": item.to_dict(), "revision": item.revision}

    def add_memory(self, memory: MemoryItemV4 | Mapping[str, Any], evidence: Iterable[MemoryEvidence | Mapping[str, Any]]) -> MemoryItemV4:
        return MemoryItemV4.from_dict(self.upsert_memory(memory, evidence, operation="create", actor="user")["memory"])

    def update_memory(
        self,
        memory_id: str,
        updates: Mapping[str, Any],
        evidence: Iterable[MemoryEvidence | Mapping[str, Any]] | None = None,
        *,
        operation: str = "update",
        reason: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        current = self.get_memory(memory_id)
        if current is None:
            raise KeyError(f"memory not found: {memory_id}")
        data = current.to_dict()
        for key, value in updates.items():
            if key not in data:
                raise ValidationError(f"unknown memory field: {key}")
            data[key] = value
        return self.upsert_memory(data, evidence, operation=operation, reason=reason, actor=actor)

    def confirm_memory(self, memory_id: str, confirmed_at: str | None = None) -> MemoryItemV4:
        """Refresh confirmation metadata without creating a content revision.

        Replaying an identical explicit save is evidence/confirmation, not a
        new fact.  Keeping this metadata-only transition outside the immutable
        content-version ledger makes duplicate writes idempotent while still
        allowing retrieval and diagnostics to see the latest confirmation.
        """
        current = self.get_memory(memory_id)
        if current is None:
            raise KeyError(f"memory not found: {memory_id}")
        timestamp = str(confirmed_at or _now())
        _validate_timestamp(timestamp, "confirmed_at")
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE memory_items SET last_confirmed_at=?, updated_at=? WHERE id=?",
                (timestamp, timestamp, str(memory_id)),
            )
        current.last_confirmed_at = timestamp
        current.updated_at = timestamp
        return current

    def list_evidence(self, memory_id: str) -> list[MemoryEvidence]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_evidence WHERE memory_id=? ORDER BY observed_at, id", (str(memory_id),)
            ).fetchall()
        return [MemoryEvidence.from_dict(dict(row)) for row in rows]

    def add_evidence(self, evidence: MemoryEvidence | Mapping[str, Any]) -> MemoryEvidence:
        item = self._evidence(evidence, str(evidence.memory_id if isinstance(evidence, MemoryEvidence) else evidence.get("memory_id")))
        if self.get_memory(item.memory_id) is None:
            raise KeyError(f"memory not found: {item.memory_id}")
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO memory_evidence(" 
                "id,memory_id,source_type,source_id,role,quote,quote_start,quote_end,content_hash,observed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    item.id, item.memory_id, item.source_type, item.source_id, item.role, item.quote,
                    item.quote_start, item.quote_end, item.content_hash, item.observed_at,
                ),
            )
        return item

    def list_versions(self, memory_id: str, limit: int = 1000) -> list[MemoryVersion]:
        limit = max(1, min(100000, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_versions WHERE memory_id=? ORDER BY revision DESC, created_at DESC LIMIT ?",
                (str(memory_id), limit),
            ).fetchall()
        return [MemoryVersion(
            id=row["id"], memory_id=row["memory_id"], revision=row["revision"], operation=row["operation"],
            snapshot=self._loads(row["snapshot_json"], {}), reason=row["reason"], actor=row["actor"], created_at=row["created_at"],
        ) for row in rows]

    def restore_version(self, memory_id: str, revision: int, *, actor: str = "user", reason: str = "restore") -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM memory_versions WHERE memory_id=? AND revision=? "
                "ORDER BY created_at DESC LIMIT 1", (str(memory_id), int(revision)),
            ).fetchone()
        if row is None:
            raise KeyError(f"version not found: {memory_id}@{revision}")
        snapshot = self._loads(row["snapshot_json"], None)
        if not isinstance(snapshot, dict):
            raise ValidationError("stored version snapshot is invalid")
        from ..memory_origin import apply_origin
        current = self.get_memory(memory_id)
        metadata = dict(snapshot.get('metadata') or {})
        apply_origin(metadata, previous=current.metadata if current else snapshot.get('metadata'), reason='edit')
        snapshot['metadata'] = metadata
        from ..memory_importance import update as update_importance
        snapshot['salience']=update_importance(metadata,previous=current.metadata if current else None,
            score_before=current.salience if current else snapshot['salience'],score_after=snapshot['salience'],reason='restore',manual=True)
        if current:
            snapshot.update(pinned=current.pinned,pin_source=current.pin_source,locked=current.locked)
        evidence = MemoryEvidence(
            id=f"restore-evidence:{memory_id}:{int(revision)}",
            memory_id=str(memory_id),
            source_type="system",
            source_id=f"memory-version:{memory_id}:{int(revision)}",
            role="system",
            quote=str(snapshot.get("canonical_text") or "")[:2000],
            observed_at=str(snapshot.get("updated_at") or _now()),
        )
        return self.upsert_memory(
            snapshot, [evidence], operation="restore", reason=reason, actor=actor, allow_resurrect=True,
        )

    restore_memory = restore_version

    def supersede(self, memory_id: str, superseding_id: str, *, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.update_memory(memory_id, {"lifecycle": "superseded", "supersedes_id": str(superseding_id)}, reason=reason, actor=actor)

    def pin_memory(self, memory_id: str, *, source: str = "user", reason: str = "") -> dict[str, Any]:
        return self.update_memory(memory_id, {"pinned": True, "pin_source": source}, reason=reason, actor="user")

    def unpin_memory(self, memory_id: str, *, reason: str = "") -> dict[str, Any]:
        return self.update_memory(memory_id, {"pinned": False, "pin_source": None}, reason=reason, actor="user")

    @guarded_store_write
    def tombstone_memory(self, memory_id: str, *, reason: str = "user_delete", actor: str = "user", purge: bool = False, allow_missing: bool = False) -> bool:
        memory_id = str(memory_id)
        current = self.get_memory(memory_id)
        if current is None and not allow_missing and not self.is_tombstoned(memory_id):
            return False
        if current is not None and not self.is_tombstoned(memory_id):
            # Deletion is a versioned state transition.  The optional purge
            # removes the current row only after the archived snapshot exists.
            self.update_memory(
                memory_id, {"lifecycle": "archived"},
                reason=str(reason or "user_delete"), actor=str(actor or "user"),
            )
        with self._lock, self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM memory_items WHERE id=?", (memory_id,)).fetchone()
            if exists is None and connection.execute("SELECT 1 FROM memory_tombstones WHERE id=?", (memory_id,)).fetchone() is None and not allow_missing:
                return False
            now = _now()
            connection.execute(
                "INSERT INTO memory_tombstones(id,deleted_at,reason,actor) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (memory_id, now, str(reason or "user_delete"), str(actor or "user")),
            )
            if exists and purge:
                connection.execute("DELETE FROM memory_items WHERE id=?", (memory_id,))
        return True

    delete_memory = tombstone_memory

    def clear_tombstone(self, memory_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM memory_tombstones WHERE id=?", (str(memory_id),))
        return cursor.rowcount > 0

    def list_tombstones(self) -> list[dict[str, str]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT * FROM memory_tombstones ORDER BY deleted_at, id").fetchall()
        return [dict(row) for row in rows]

    @guarded_store_write
    def upsert_candidate(self, candidate: MemoryCandidateV4 | Mapping[str, Any]) -> dict[str, Any]:
        item = candidate if isinstance(candidate, MemoryCandidateV4) else MemoryCandidateV4(**dict(candidate))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO memory_candidates VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET proposal_json=excluded.proposal_json, "
                "evidence_ids_json=excluded.evidence_ids_json, status=excluded.status, "
                "occurrence_count=excluded.occurrence_count, first_seen_at=excluded.first_seen_at, "
                "last_seen_at=excluded.last_seen_at, review_reason=excluded.review_reason",
                (
                    item.id, self._json(item.proposal), self._json(item.evidence_ids), item.status,
                    item.occurrence_count, item.first_seen_at, item.last_seen_at, item.review_reason,
                ),
            )
        return {"status": "success", "candidate": item.to_dict()}

    def get_candidate(self, candidate_id: str) -> MemoryCandidateV4 | None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM memory_candidates WHERE id=?", (str(candidate_id),)).fetchone()
        if row is None:
            return None
        return MemoryCandidateV4(
            id=row["id"], proposal=self._loads(row["proposal_json"], {}),
            evidence_ids=self._loads(row["evidence_ids_json"], []), status=row["status"],
            occurrence_count=row["occurrence_count"], first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"], review_reason=row["review_reason"],
        )

    @guarded_store_write
    def update_candidate_status(self, candidate_id: str, status: str, *, review_reason: str = "") -> bool:
        """Move a candidate through its auditable review lifecycle."""
        status = str(status or "").strip().lower()
        if status not in {"pending", "accepted", "rejected", "merged", "expired"}:
            raise ValidationError("unsupported candidate status")
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE memory_candidates SET status=?, review_reason=?, last_seen_at=? WHERE id=?",
                (status, str(review_reason or "")[:500], _now(), str(candidate_id)),
            )
        return cursor.rowcount > 0

    def list_candidates(self, status: str | None = None, limit: int = 1000) -> list[MemoryCandidateV4]:
        limit = max(1, min(100000, int(limit)))
        sql = "SELECT * FROM memory_candidates"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(str(status))
        sql += " ORDER BY last_seen_at DESC, id LIMIT ?"
        params.append(limit)
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [MemoryCandidateV4(
            id=row["id"], proposal=self._loads(row["proposal_json"], {}),
            evidence_ids=self._loads(row["evidence_ids_json"], []), status=row["status"],
            occurrence_count=row["occurrence_count"], first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"], review_reason=row["review_reason"],
        ) for row in rows]

    def upsert_day_state(self, state: DayState | Mapping[str, Any]) -> dict[str, Any]:
        item = state if isinstance(state, DayState) else DayState(**dict(state))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO day_states VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET timezone=excluded.timezone, first_contact_at=excluded.first_contact_at, "
                "last_contact_at=excluded.last_contact_at, page_load_count=excluded.page_load_count, "
                "backend_start_count=excluded.backend_start_count, conversation_count=excluded.conversation_count, "
                "task_snapshot_json=excluded.task_snapshot_json, carryover_task_ids_json=excluded.carryover_task_ids_json, "
                "weather_snapshot_json=excluded.weather_snapshot_json, external_attention_items_json=excluded.external_attention_items_json, "
                "frozen_at=excluded.frozen_at, purge_after=excluded.purge_after",
                (
                    item.date, item.timezone, item.first_contact_at, item.last_contact_at, item.page_load_count,
                    item.backend_start_count, item.conversation_count, self._json(item.task_snapshot),
                    self._json(item.carryover_task_ids), self._json(item.weather_snapshot) if item.weather_snapshot is not None else None,
                    self._json(item.external_attention_items), item.frozen_at, item.purge_after,
                ),
            )
        return {"status": "success", "day_state": item.to_dict()}

    def get_day_state(self, date: str) -> DayState | None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM day_states WHERE date=?", (str(date),)).fetchone()
        if not row:
            return None
        return DayState(
            date=row["date"], timezone=row["timezone"], first_contact_at=row["first_contact_at"],
            last_contact_at=row["last_contact_at"], page_load_count=row["page_load_count"],
            backend_start_count=row["backend_start_count"], conversation_count=row["conversation_count"],
            task_snapshot=self._loads(row["task_snapshot_json"], []),
            carryover_task_ids=self._loads(row["carryover_task_ids_json"], []),
            weather_snapshot=self._loads(row["weather_snapshot_json"], None) if row["weather_snapshot_json"] else None,
            external_attention_items=self._loads(row["external_attention_items_json"], []),
            frozen_at=row["frozen_at"], purge_after=row["purge_after"],
        )

    def list_day_states(self, limit: int = 10) -> list[DayState]:
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT date FROM day_states ORDER BY date DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [state for row in rows if (state := self.get_day_state(row["date"])) is not None]

    def purge_day_states_before(self, date: str) -> int:
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM day_states WHERE date<?", (str(date),))
        return max(0, cursor.rowcount)

    def record_event(self, event: TurnEvent | Mapping[str, Any]) -> dict[str, Any]:
        item = event if isinstance(event, TurnEvent) else TurnEvent(**dict(event))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO turn_events VALUES(?,?,?,?,?,?,?,?)",
                (
                    item.id, item.turn_id, item.sequence, item.type, self._json(item.public_payload),
                    self._json(item.private_payload) if item.private_payload is not None else None,
                    item.status, item.created_at,
                ),
            )
        return {"status": "success", "event": item.to_dict()}

    def list_events(self, turn_id: str, include_private: bool = False) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM turn_events WHERE turn_id=? ORDER BY sequence, created_at", (str(turn_id),)
            ).fetchall()
        result = []
        for row in rows:
            item = {
                "id": row["id"], "turn_id": row["turn_id"], "sequence": row["sequence"], "type": row["type"],
                "public_payload": self._loads(row["public_payload_json"], {}), "status": row["status"],
                "created_at": row["created_at"],
            }
            if include_private:
                item["private_payload"] = self._loads(row["private_payload_json"], None) if row["private_payload_json"] else None
            result.append(item)
        return result

    def list_events_after(
        self,
        turn_id: str,
        *,
        after_sequence: int = -1,
        limit: int = 100,
        include_private: bool = False,
    ) -> dict[str, Any]:
        """Read a bounded public event page for SSE resume/retry."""
        safe_after = max(-1, int(after_sequence))
        safe_limit = max(1, min(500, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM turn_events WHERE turn_id=? AND sequence>? "
                "ORDER BY sequence, created_at LIMIT ?",
                (str(turn_id), safe_after, safe_limit),
            ).fetchall()
        result = []
        for row in rows:
            item = {
                "id": row["id"], "turn_id": row["turn_id"], "sequence": row["sequence"], "type": row["type"],
                "public_payload": self._loads(row["public_payload_json"], {}), "status": row["status"],
                "created_at": row["created_at"], "private_payload": None,
            }
            if include_private:
                item["private_payload"] = self._loads(row["private_payload_json"], None) if row["private_payload_json"] else None
            result.append(item)
        next_sequence = safe_after
        if result:
            next_sequence = max(int(item.get("sequence", safe_after)) for item in result)
        return {
            "status": "success", "turn_id": str(turn_id), "events": result,
            "next_sequence": next_sequence, "has_more": len(result) >= safe_limit,
        }

    def record_memory_usage(
        self,
        memory_id: str,
        *,
        turn_id: str | None = None,
        stage: str = "retrieval",
        prompt_included: bool = False,
        used: bool = False,
        feedback: str | None = None,
    ) -> dict[str, Any]:
        """Record selection, prompt inclusion, and actual-use receipts separately."""
        memory_id = str(memory_id or "").strip()
        if not memory_id:
            raise ValidationError("memory_id is required for usage receipt")
        receipt = {
            "id": f"usage:{memory_id}:{_now()}:{uuid.uuid4().hex[:12]}",
            "memory_id": memory_id,
            "turn_id": str(turn_id or "") or None,
            "stage": str(stage or "retrieval")[:80],
            "prompt_included": bool(prompt_included),
            "used": bool(used),
            "feedback": str(feedback or "")[:80] or None,
            "created_at": _now(),
        }
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO memory_usage_events VALUES(?,?,?,?,?,?,?,?)",
                (
                    receipt["id"], receipt["memory_id"], receipt["turn_id"], receipt["stage"],
                    int(receipt["prompt_included"]), int(receipt["used"]), receipt["feedback"], receipt["created_at"],
                ),
            )
        return receipt

    def list_memory_usage(self, memory_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        limit = max(1, min(100000, int(limit)))
        sql = "SELECT * FROM memory_usage_events"
        params: list[Any] = []
        if memory_id:
            sql += " WHERE memory_id=?"
            params.append(str(memory_id))
        sql += " ORDER BY created_at DESC, id LIMIT ?"
        params.append(limit)
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"], "memory_id": row["memory_id"], "turn_id": row["turn_id"],
                "stage": row["stage"], "prompt_included": bool(row["prompt_included"]),
                "used": bool(row["used"]), "feedback": row["feedback"], "created_at": row["created_at"],
            }
            for row in rows
        ]

    def memory_usage_scores(self) -> dict[str, float]:
        """Return validated usage ratios in one aggregate query.

        Retrieval may inspect thousands of memories.  Keeping this as a
        grouped query avoids one usage-table round trip per candidate while
        preserving the same used/total ratio as ``_validated_usage``.
        """
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT memory_id, SUM(used) AS used_count, COUNT(*) AS total_count "
                "FROM memory_usage_events GROUP BY memory_id"
            ).fetchall()
        return {
            str(row["memory_id"]): min(1.0, float(row["used_count"] or 0) / max(1, int(row["total_count"] or 0)))
            for row in rows
        }

    def upsert_connector_event(self, event: Any) -> dict[str, Any]:
        from .connectors import ConnectorEvent
        item = event if isinstance(event, ConnectorEvent) else ConnectorEvent.from_dict(event)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO connector_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item.id, item.source, item.source_event_id, item.author_id, item.author_display,
                    item.channel_id, item.thread_id, item.occurred_at, item.received_at, item.content,
                    self._json(item.attachments), item.raw_url, item.trust, item.permission_scope,
                    item.read_state, item.dedupe_hash, item.expires_at,
                ),
            )
        return {"status": "created" if cursor.rowcount else "duplicate", "event": item.to_dict()}

    def list_connector_events(self, *, unread_only: bool = False, limit: int = 1000) -> list[dict[str, Any]]:
        limit = max(1, min(100000, int(limit)))
        sql = "SELECT * FROM connector_events"
        params: list[Any] = []
        if unread_only:
            sql += " WHERE read_state='unread'"
        sql += " ORDER BY occurred_at DESC, id LIMIT ?"
        params.append(limit)
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"], "source": row["source"], "source_event_id": row["source_event_id"],
                "author_id": row["author_id"], "author_display": row["author_display"],
                "channel_id": row["channel_id"], "thread_id": row["thread_id"],
                "occurred_at": row["occurred_at"], "received_at": row["received_at"],
                "content": row["content"], "attachments": self._loads(row["attachments_json"], []),
                "raw_url": row["raw_url"], "trust": row["trust"], "permission_scope": row["permission_scope"],
                "read_state": row["read_state"], "dedupe_hash": row["dedupe_hash"], "expires_at": row["expires_at"],
            }
            for row in rows
        ]

    def mark_connector_event(self, event_id: str, *, read_state: str = "read") -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE connector_events SET read_state=? WHERE id=?", (str(read_state or "read"), str(event_id))
            )
        return cursor.rowcount > 0

    def purge_connector_events(self, before: str | None = None) -> int:
        cutoff = str(before or _now())
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM connector_events WHERE expires_at<=?", (cutoff,))
        return max(0, cursor.rowcount)

    def integrity_check(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {
                "memory_count": connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0],
                "evidence_count": connection.execute("SELECT COUNT(*) FROM memory_evidence").fetchone()[0],
                "version_count": connection.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0],
                "candidate_count": connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0],
                "day_state_count": connection.execute("SELECT COUNT(*) FROM day_states").fetchone()[0],
                "tombstone_count": connection.execute("SELECT COUNT(*) FROM memory_tombstones").fetchone()[0],
                "usage_event_count": connection.execute("SELECT COUNT(*) FROM memory_usage_events").fetchone()[0],
                "connector_event_count": connection.execute("SELECT COUNT(*) FROM connector_events").fetchone()[0],
            }
        return {"ok": result == "ok", "message": result, "schema_version": self.SCHEMA_VERSION, "path": self.path, **counts}

    def export_state(self) -> dict[str, Any]:
        memories = self.list_memories(limit=100000)
        memory_ids = {item.id for item in memories}
        memory_ids.update(str(row[0]) for row in self._version_memory_ids())
        return {
            "schema_version": self.SCHEMA_VERSION,
            "memories": [item.to_dict() for item in memories],
            "evidence": [evidence.to_dict() for item in memories for evidence in self.list_evidence(item.id)],
            "versions": [version.to_dict() for memory_id in sorted(memory_ids) for version in self.list_versions(memory_id)],
            "candidates": [candidate.to_dict() for candidate in self.list_candidates(limit=100000)],
            "day_states": [state.to_dict() for state in self.list_day_states(limit=100000)],
            "tombstones": self.list_tombstones(),
            "events": [event for turn_id in self._event_turn_ids() for event in self.list_events(turn_id, include_private=True)],
            "usage": self.list_memory_usage(limit=100000),
            "connector_events": self.list_connector_events(limit=100000),
        }

    def _event_turn_ids(self) -> list[str]:
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT DISTINCT turn_id FROM turn_events ORDER BY turn_id").fetchall()
        return [str(row[0]) for row in rows]

    def _version_memory_ids(self) -> list[tuple[str]]:
        with self._lock, self._connection() as connection:
            return connection.execute("SELECT DISTINCT memory_id FROM memory_versions ORDER BY memory_id").fetchall()

    def import_state(self, state: Mapping[str, Any], *, actor: str = "restore") -> dict[str, Any]:
        """Restore an exported V4 snapshot through normal versioned writes."""
        if not isinstance(state, Mapping) or int(state.get("schema_version", 0)) != self.SCHEMA_VERSION:
            raise ValidationError("unsupported V4 export")
        evidence_by_memory: dict[str, list[dict[str, Any]]] = {}
        for raw in state.get("evidence") or []:
            if isinstance(raw, Mapping) and raw.get("memory_id"):
                evidence_by_memory.setdefault(str(raw["memory_id"]), []).append(dict(raw))
        changed = 0
        skipped = 0
        for raw in state.get("memories") or []:
            if not isinstance(raw, Mapping):
                continue
            item = MemoryItemV4.from_dict(raw)
            evidence = evidence_by_memory.get(item.id) or [{
                "id": f"export-restore-evidence:{item.id}", "memory_id": item.id,
                "source_type": "system", "source_id": f"export:{item.id}",
                "role": "system", "quote": item.canonical_text,
            }]
            result = self.upsert_memory(
                item, evidence, operation="restore_export", reason="restore exported V4 snapshot", actor=actor,
                allow_resurrect=True,
            )
            if result["status"] == "unchanged":
                skipped += 1
            else:
                changed += 1
        with self._lock, self._connection() as connection:
            for raw in state.get("versions") or []:
                if not isinstance(raw, Mapping):
                    continue
                version = MemoryVersion.from_dict(raw)
                connection.execute(
                    "INSERT OR IGNORE INTO memory_versions(id,memory_id,revision,operation,snapshot_json,reason,actor,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        version.id, version.memory_id, version.revision, version.operation,
                        self._json(version.snapshot), version.reason, version.actor, version.created_at,
                    ),
                )
        for raw in state.get("candidates") or []:
            if isinstance(raw, Mapping):
                self.upsert_candidate(raw)
        for raw in state.get("day_states") or []:
            if isinstance(raw, Mapping):
                self.upsert_day_state(raw)
        for raw in state.get("events") or []:
            if isinstance(raw, Mapping):
                self.record_event(raw)
        for raw in state.get("usage") or []:
            if not isinstance(raw, Mapping) or not raw.get("memory_id"):
                continue
            with self._lock, self._connection() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO memory_usage_events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(raw.get("id") or uuid.uuid4()), str(raw["memory_id"]),
                        str(raw.get("turn_id") or "") or None, str(raw.get("stage") or "restore")[:80],
                        int(bool(raw.get("prompt_included"))), int(bool(raw.get("used"))),
                        str(raw.get("feedback") or "")[:80] or None, str(raw.get("created_at") or _now()),
                    ),
                )
        for raw in state.get("connector_events") or []:
            if isinstance(raw, Mapping):
                self.upsert_connector_event(raw)
        for raw in state.get("tombstones") or []:
            if isinstance(raw, Mapping) and raw.get("id"):
                self.tombstone_memory(
                    raw["id"], reason=raw.get("reason", "restored_tombstone"),
                    actor=actor, purge=True, allow_missing=True,
                )
        return {"status": "success", "changed_count": changed, "skipped_count": skipped, "integrity": self.integrity_check()}

    export = export_state

    def close(self) -> None:
        """Compatibility no-op; connections are short-lived and transaction-scoped."""
