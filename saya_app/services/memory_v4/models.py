"""Small, serialisable V4 data contracts.

Dataclasses are used here instead of coupling the storage contract to a
particular Pydantic major version.  ``from_dict`` performs the same boundary
validation for JSON and SQLite callers and ``to_dict`` is stable JSON output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Mapping


class ValidationError(ValueError):
    """Raised when a V4 contract cannot safely cross the storage boundary."""


MEMORY_SCHEMA_VERSION = 4
LIFECYCLES = {"candidate", "active", "superseded", "expired", "archived", "quarantined"}
KINDS = {"identity", "preference", "relationship", "goal", "event", "procedure", "knowledge", "reflection"}
SCOPE_TYPES = {"global", "project", "session", "connector"}
AUTHORITIES = {"user_confirmed", "observed", "inferred", "external"}
SENSITIVITIES = {"normal", "personal", "sensitive", "secret"}
ACTIVATION_POLICIES = {"query", "event", "brief_only", "always_small"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _bounded_float(value: Any, name: str, default: float = 0.0) -> float:
    try:
        number = float(default if value is None or value == "" else value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a number") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValidationError(f"{name} must be between 0 and 1")
    return round(number, 6)


def _nonnegative_int(value: Any, name: str, default: int = 0) -> int:
    try:
        number = int(default if value is None or value == "" else value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an integer") from exc
    if number < 0:
        raise ValidationError(f"{name} must not be negative")
    return number


def _check_enum(value: str, name: str, allowed: set[str]) -> str:
    if value not in allowed:
        raise ValidationError(f"{name} has unsupported value: {value!r}")
    return value


def _json_safe(value: Any, field_name: str) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be JSON serialisable") from exc
    return value


def _validate_timestamp(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{name} must be an ISO or legacy datetime") from exc
    return text


def _contains_secret(value: Any) -> bool:
    return bool(re.search(
        r"(?i)(?:password|passwd|api[ _-]?key|access[ _-]?token|bearer\s+[a-z0-9._-]+|secret\s*[:=]|sk-[a-z0-9]{12,})",
        str(value or ""),
    ))


def _contains_forbidden_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        if any(str(key).strip().lower() in forbidden for key in value):
            return True
        return any(_contains_forbidden_key(child, forbidden) for child in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child, forbidden) for child in value)
    return False


@dataclass
class MemoryItemV4:
    id: str
    schema_version: int = MEMORY_SCHEMA_VERSION
    subject: str = "user"
    predicate: str = "has_memory"
    value: Any = ""
    canonical_text: str = ""
    language: str = "und"
    kind: str = "knowledge"
    scope_type: str = "global"
    scope_id: str | None = None
    authority: str = "observed"
    sensitivity: str = "normal"
    valid_from: str | None = None
    valid_to: str | None = None
    last_confirmed_at: str | None = None
    lifecycle: str = "active"
    supersedes_id: str | None = None
    confidence: float = 0.75
    salience: float = 0.4
    pinned: bool = False
    pin_source: str | None = None
    locked: bool = False
    activation_policy: str = "query"
    candidate_shown_count: int = 0
    used_count: int = 0
    last_used_at: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    revision: int = 1
    legacy_layer: str | None = None
    legacy_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _text(self.id)
        if not self.id:
            raise ValidationError("id is required")
        try:
            self.schema_version = int(self.schema_version)
        except (TypeError, ValueError) as exc:
            raise ValidationError("schema_version must be an integer") from exc
        if self.schema_version != MEMORY_SCHEMA_VERSION:
            raise ValidationError(f"schema_version must be {MEMORY_SCHEMA_VERSION}")
        self.subject = _text(self.subject, default="user")
        self.predicate = _text(self.predicate, default="has_memory")
        self.canonical_text = _text(self.canonical_text)
        if not self.canonical_text:
            raise ValidationError("canonical_text is required")
        self.language = _text(self.language, default="und")
        self.kind = _check_enum(_text(self.kind, default="knowledge"), "kind", KINDS)
        self.scope_type = _check_enum(_text(self.scope_type, default="global"), "scope_type", SCOPE_TYPES)
        self.scope_id = _optional_text(self.scope_id)
        self.authority = _check_enum(_text(self.authority, default="observed"), "authority", AUTHORITIES)
        self.sensitivity = _check_enum(_text(self.sensitivity, default="normal"), "sensitivity", SENSITIVITIES)
        self.valid_from = _validate_timestamp(self.valid_from, "valid_from")
        self.valid_to = _validate_timestamp(self.valid_to, "valid_to")
        self.last_confirmed_at = _validate_timestamp(self.last_confirmed_at, "last_confirmed_at")
        self.lifecycle = _check_enum(_text(self.lifecycle, default="active"), "lifecycle", LIFECYCLES)
        self.supersedes_id = _optional_text(self.supersedes_id)
        self.confidence = _bounded_float(self.confidence, "confidence", 0.75)
        self.salience = _bounded_float(self.salience, "salience", 0.4)
        self.pinned = bool(self.pinned)
        self.pin_source = _optional_text(self.pin_source)
        if self.pin_source and self.pin_source not in {"user", "system", "policy"}:
            raise ValidationError("pin_source has unsupported value")
        self.locked = bool(self.locked)
        self.activation_policy = _check_enum(
            _text(self.activation_policy, default="query"), "activation_policy", ACTIVATION_POLICIES
        )
        self.candidate_shown_count = _nonnegative_int(self.candidate_shown_count, "candidate_shown_count")
        self.used_count = _nonnegative_int(self.used_count, "used_count")
        self.last_used_at = _validate_timestamp(self.last_used_at, "last_used_at")
        self.created_at = _validate_timestamp(self.created_at, "created_at") or _now()
        self.updated_at = _validate_timestamp(self.updated_at, "updated_at") or self.created_at
        self.revision = max(1, _nonnegative_int(self.revision, "revision", 1))
        self.legacy_layer = _optional_text(self.legacy_layer)
        self.legacy_source = _optional_text(self.legacy_source)
        if not isinstance(self.metadata, dict):
            raise ValidationError("metadata must be an object")
        self.metadata = _json_safe(dict(self.metadata), "metadata")
        self.value = _json_safe(self.value, "value")
        if self.sensitivity == "secret" or _contains_secret(self.canonical_text):
            raise ValidationError("secret data must not enter V4 memory正文")
        if self.scope_type == "global" and self.scope_id:
            raise ValidationError("global memories cannot have scope_id")
        if self.pinned and not self.pin_source:
            self.pin_source = "user"

    @property
    def canonical_key(self) -> tuple[str, str, str, str]:
        return (self.predicate, self.scope_type, self.scope_id or "", self.subject)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryItemV4":
        if not isinstance(data, Mapping):
            raise ValidationError("memory item must be an object")
        names = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in names if key in data})


@dataclass
class MemoryEvidence:
    id: str
    memory_id: str
    source_type: str
    source_id: str
    role: str | None = None
    quote: str = ""
    quote_start: int | None = None
    quote_end: int | None = None
    content_hash: str = ""
    observed_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.id, self.memory_id = _text(self.id), _text(self.memory_id)
        self.source_type, self.source_id = _text(self.source_type), _text(self.source_id)
        self.role = _optional_text(self.role)
        self.quote = _text(self.quote)[:2000]
        if not self.id or not self.memory_id or not self.source_type or not self.source_id:
            raise ValidationError("evidence id, memory_id, source_type and source_id are required")
        if not self.quote:
            raise ValidationError("evidence quote is required")
        self.quote_start = None if self.quote_start is None else _nonnegative_int(self.quote_start, "quote_start")
        self.quote_end = None if self.quote_end is None else _nonnegative_int(self.quote_end, "quote_end")
        if self.quote_start is not None and self.quote_end is not None and self.quote_end < self.quote_start:
            raise ValidationError("quote_end must not precede quote_start")
        expected_hash = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        supplied_hash = _text(self.content_hash)
        if supplied_hash and supplied_hash != expected_hash:
            raise ValidationError("evidence content_hash does not match quote")
        self.content_hash = supplied_hash or expected_hash
        self.observed_at = _validate_timestamp(self.observed_at, "observed_at") or _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryEvidence":
        names = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in names if key in data})


@dataclass
class MemoryVersion:
    id: str
    memory_id: str
    revision: int
    operation: str
    snapshot: dict[str, Any]
    reason: str = ""
    actor: str = "system"
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.id, self.memory_id = _text(self.id), _text(self.memory_id)
        self.revision = max(1, _nonnegative_int(self.revision, "revision", 1))
        self.operation, self.actor = _text(self.operation), _text(self.actor, default="system")
        if not self.id or not self.memory_id or not self.operation:
            raise ValidationError("version id, memory_id and operation are required")
        if not isinstance(self.snapshot, dict):
            raise ValidationError("version snapshot must be an object")
        self.snapshot = _json_safe(dict(self.snapshot), "snapshot")
        self.reason = _text(self.reason)[:500]
        self.created_at = _validate_timestamp(self.created_at, "created_at") or _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryVersion":
        names = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in names if key in data})


@dataclass
class MemoryProposal:
    """Portable candidate proposal; future write-pipeline fields may live in metadata."""

    canonical_text: str
    subject: str = "user"
    predicate: str = "has_memory"
    value: Any = ""
    language: str = "und"
    kind: str = "knowledge"
    scope_type: str = "global"
    scope_id: str | None = None
    authority: str = "inferred"
    sensitivity: str = "normal"
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float = 0.5
    salience: float = 0.4
    suggest_pin: bool = False
    future_use: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.canonical_text = _text(self.canonical_text)
        if not self.canonical_text:
            raise ValidationError("proposal canonical_text is required")
        self.kind = _check_enum(_text(self.kind, default="knowledge"), "kind", KINDS)
        self.scope_type = _check_enum(_text(self.scope_type, default="global"), "scope_type", SCOPE_TYPES)
        self.authority = _check_enum(_text(self.authority, default="inferred"), "authority", AUTHORITIES)
        self.sensitivity = _check_enum(_text(self.sensitivity, default="normal"), "sensitivity", SENSITIVITIES)
        if self.sensitivity == "secret" or _contains_secret(self.canonical_text) or _contains_secret(self.value):
            raise ValidationError("secret data must not enter candidate storage")
        self.valid_from = _validate_timestamp(self.valid_from, "valid_from")
        self.valid_to = _validate_timestamp(self.valid_to, "valid_to")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError("valid_to must not precede valid_from")
        self.confidence = _bounded_float(self.confidence, "confidence", 0.5)
        self.salience = _bounded_float(self.salience, "salience", 0.4)
        self.suggest_pin = bool(self.suggest_pin)
        self.future_use = _text(self.future_use)[:500]
        if not isinstance(self.metadata, dict):
            raise ValidationError("proposal metadata must be an object")
        self.metadata = _json_safe(dict(self.metadata), "proposal metadata")
        self.value = _json_safe(self.value, "proposal value")
        if self.scope_type == "global" and self.scope_id:
            raise ValidationError("global proposals cannot have scope_id")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryProposal":
        names = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in names if key in data})


@dataclass
class MemoryCandidateV4:
    id: str
    proposal: dict[str, Any]
    evidence_ids: list[str] = field(default_factory=list)
    status: str = "pending"
    occurrence_count: int = 1
    first_seen_at: str = field(default_factory=_now)
    last_seen_at: str = field(default_factory=_now)
    review_reason: str = ""

    def __post_init__(self) -> None:
        self.id = _text(self.id)
        if isinstance(self.proposal, MemoryProposal):
            self.proposal = self.proposal.to_dict()
        if not self.id or not isinstance(self.proposal, dict):
            raise ValidationError("candidate id and proposal are required")
        self.proposal = _json_safe(dict(self.proposal), "proposal")
        # Re-validate the nested proposal so a caller cannot bypass secret,
        # enum, scope, or timestamp checks by constructing the candidate from
        # a plain dictionary.
        self.proposal = MemoryProposal.from_dict(self.proposal).to_dict()
        self.evidence_ids = [str(item) for item in (self.evidence_ids or []) if str(item).strip()]
        self.status = _text(self.status, default="pending")
        if self.status not in {"pending", "accepted", "rejected", "merged", "expired"}:
            raise ValidationError("unsupported candidate status")
        self.occurrence_count = max(1, _nonnegative_int(self.occurrence_count, "occurrence_count", 1))
        self.first_seen_at = _validate_timestamp(self.first_seen_at, "first_seen_at") or _now()
        self.last_seen_at = _validate_timestamp(self.last_seen_at, "last_seen_at") or self.first_seen_at
        self.review_reason = _text(self.review_reason)[:500]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryCandidateV4":
        names = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in names if key in data})


@dataclass
class DayState:
    date: str
    timezone: str = "UTC"
    first_contact_at: str | None = None
    last_contact_at: str | None = None
    page_load_count: int = 0
    backend_start_count: int = 0
    conversation_count: int = 0
    task_snapshot: list[dict[str, Any]] = field(default_factory=list)
    carryover_task_ids: list[str] = field(default_factory=list)
    weather_snapshot: dict[str, Any] | None = None
    external_attention_items: list[str] = field(default_factory=list)
    frozen_at: str | None = None
    purge_after: str = ""

    def __post_init__(self) -> None:
        self.date = _text(self.date)
        if not self.date:
            raise ValidationError("day state date is required")
        try:
            datetime.strptime(self.date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError("day state date must be YYYY-MM-DD") from exc
        self.timezone = _text(self.timezone, default="UTC")
        self.first_contact_at = _validate_timestamp(self.first_contact_at, "first_contact_at")
        self.last_contact_at = _validate_timestamp(self.last_contact_at, "last_contact_at")
        self.frozen_at = _validate_timestamp(self.frozen_at, "frozen_at")
        self.page_load_count = _nonnegative_int(self.page_load_count, "page_load_count")
        self.backend_start_count = _nonnegative_int(self.backend_start_count, "backend_start_count")
        self.conversation_count = _nonnegative_int(self.conversation_count, "conversation_count")
        if not isinstance(self.task_snapshot, list) or not all(isinstance(item, dict) for item in self.task_snapshot):
            raise ValidationError("task_snapshot must be a list of objects")
        if not isinstance(self.carryover_task_ids, list):
            raise ValidationError("carryover_task_ids must be a list")
        self.carryover_task_ids = [str(item) for item in self.carryover_task_ids if str(item).strip()]
        if self.weather_snapshot is not None and not isinstance(self.weather_snapshot, dict):
            raise ValidationError("weather_snapshot must be an object or null")
        if not isinstance(self.external_attention_items, list):
            raise ValidationError("external_attention_items must be a list")
        self.external_attention_items = [str(item) for item in self.external_attention_items if str(item).strip()]
        self.purge_after = _text(self.purge_after)
        _json_safe(self.task_snapshot, "task_snapshot")
        _json_safe(self.weather_snapshot, "weather_snapshot")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DayState":
        names = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in names if key in data})


@dataclass
class TurnEvent:
    id: str
    turn_id: str
    sequence: int
    type: str
    public_payload: dict[str, Any]
    private_payload: dict[str, Any] | None = None
    status: str = "completed"
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.id, self.turn_id, self.type = _text(self.id), _text(self.turn_id), _text(self.type)
        self.sequence = _nonnegative_int(self.sequence, "sequence")
        if not self.id or not self.turn_id or not self.type:
            raise ValidationError("event id, turn_id and type are required")
        if not isinstance(self.public_payload, dict):
            raise ValidationError("public_payload must be an object")
        if self.private_payload is not None and not isinstance(self.private_payload, dict):
            raise ValidationError("private_payload must be an object or null")
        forbidden = {"chain_of_thought", "thoughts", "api_key", "token", "secret", "system_prompt"}
        if _contains_forbidden_key(self.public_payload, forbidden):
            raise ValidationError("public_payload contains private fields")
        self.public_payload = _json_safe(dict(self.public_payload), "public_payload")
        self.private_payload = _json_safe(dict(self.private_payload), "private_payload") if self.private_payload is not None else None
        self.status = _text(self.status, default="completed")
        self.created_at = _validate_timestamp(self.created_at, "created_at") or _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnEvent":
        names = {item.name for item in fields(cls)}
        return cls(**{key: data[key] for key in names if key in data})


def validate_memory_item(value: MemoryItemV4 | Mapping[str, Any]) -> MemoryItemV4:
    """Validate and return a V4 memory item for repository callers."""

    return value if isinstance(value, MemoryItemV4) else MemoryItemV4.from_dict(value)
