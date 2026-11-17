"""Capability registry and budget/policy boundary for the V4 agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Callable, Mapping

from .models import ValidationError
from ..qq_tasks import checkpoint_current, phase_current


AVAILABILITIES = {"available", "unconfigured", "disabled", "error"}
ACCESS = {"read", "write"}
RISKS = {"low", "medium", "high"}
PERMISSIONS = {"auto", "notify", "confirm_each_time", "forbidden"}
ALLOWED_TIME_EVENT_TYPES = {
    "backend.started", "session.created", "session.opened", "session.branched", "turn.started",
}


def _safe_public(value: Any) -> Any:
    """Remove credential/private keys recursively before event/UI exposure."""
    forbidden = {"token", "api_key", "password", "secret", "private_payload", "chain_of_thought", "thoughts", "system_prompt"}
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if str(key).lower() in forbidden else _safe_public(child)
            for key, child in value.items()
            if str(key).lower() not in {"chain_of_thought", "thoughts", "system_prompt"}
        }
    if isinstance(value, list):
        return [_safe_public(child) for child in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) else value[:4000]
    return str(value)[:4000]


def _coerce_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Forgive the loose argument typing local models commonly produce.

    Numeric strings become integers for integer-typed fields and a single
    scalar becomes a one-element list for array-typed fields. Anything that
    cannot be losslessly coerced is left untouched so strict validation
    still rejects genuinely malformed input.
    """
    properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    if not isinstance(arguments, Mapping):
        return dict(arguments or {})
    coerced = dict(arguments)
    for name, value in coerced.items():
        definition = properties.get(name)
        if not isinstance(definition, Mapping):
            continue
        expected = definition.get("type")
        if expected == "integer" and isinstance(value, str):
            try:
                coerced[name] = int(value.strip())
            except ValueError:
                pass
        elif expected == "number" and isinstance(value, str):
            try:
                coerced[name] = float(value.strip())
            except ValueError:
                pass
        elif expected == "array" and isinstance(value, (str, int, float)):
            coerced[name] = [value]
    return coerced


def _validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str | None:
    """Validate the small, public JSON schemas used by tool contracts.

    This intentionally covers the primitive constraints needed by the built-in
    capabilities without pulling a general-purpose schema engine into the
    runtime.  Handlers still apply their own bounds as a second line of defence.
    """
    if not isinstance(arguments, Mapping):
        return "arguments_must_be_object"
    if str(schema.get("type") or "object") != "object":
        return None
    properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    for name in schema.get("required") or []:
        if str(name) not in arguments:
            return f"missing_required:{name}"
    for name, value in arguments.items():
        definition = properties.get(name)
        if not isinstance(definition, Mapping):
            continue
        expected = definition.get("type")
        if expected == "string" and not isinstance(value, str):
            return f"invalid_type:{name}"
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"invalid_type:{name}"
        if expected == "array" and not isinstance(value, list):
            return f"invalid_type:{name}"
        if isinstance(value, str) and definition.get("minLength") is not None and len(value) < int(definition["minLength"]):
            return f"invalid_length:{name}"
        if isinstance(value, list) and definition.get("maxItems") is not None and len(value) > int(definition["maxItems"]):
            return f"too_many_items:{name}"
        if definition.get("enum") is not None and value not in definition.get("enum"):
            return f"invalid_enum:{name}"
    return None


@dataclass
class Capability:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    availability: str = "available"
    access: str = "read"
    risk: str = "low"
    permission: str = "auto"
    estimated_latency_ms: int = 0
    estimated_cost: float | None = None
    max_calls_per_turn: int = 1
    handler: Callable[[Mapping[str, Any]], Any] | None = None

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        self.description = str(self.description or "")[:500]
        self.availability = str(self.availability or "unconfigured")
        self.access = str(self.access or "read")
        self.risk = str(self.risk or "low")
        self.permission = str(self.permission or "auto")
        if not self.name or self.availability not in AVAILABILITIES or self.access not in ACCESS or self.risk not in RISKS or self.permission not in PERMISSIONS:
            raise ValidationError("invalid capability contract")
        self.estimated_latency_ms = max(0, int(self.estimated_latency_ms))
        self.estimated_cost = None if self.estimated_cost is None else max(0.0, float(self.estimated_cost))
        self.max_calls_per_turn = max(0, min(100, int(self.max_calls_per_turn)))

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "input_schema": _safe_public(self.input_schema),
            "output_schema": _safe_public(self.output_schema), "availability": self.availability,
            "access": self.access, "risk": self.risk, "permission": self.permission,
            "estimated_latency_ms": self.estimated_latency_ms, "estimated_cost": self.estimated_cost,
            "max_calls_per_turn": self.max_calls_per_turn,
        }


@dataclass
class CapabilityPolicy:
    mode: str = "allow"
    max_steps: int = 6
    max_total_cost: float = 1.0
    max_total_latency_ms: int = 30000
    allowed_names: set[str] | None = None
    denied_names: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.mode = str(self.mode or "allow").lower()
        if self.mode not in {"allow", "deny"}:
            raise ValidationError("policy mode must be allow or deny")
        self.max_steps = max(0, min(100, int(self.max_steps)))
        self.max_total_cost = max(0.0, float(self.max_total_cost))
        self.max_total_latency_ms = max(0, int(self.max_total_latency_ms))
        self.allowed_names = None if self.allowed_names is None else {str(item) for item in self.allowed_names}
        self.denied_names = {str(item) for item in self.denied_names}


class CapabilityRegistry:
    def __init__(self):
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> Capability:
        if capability.name in self._capabilities:
            raise ValidationError(f"capability already registered: {capability.name}")
        self._capabilities[capability.name] = capability
        return capability

    def upsert(self, capability: Capability) -> Capability:
        self._capabilities[capability.name] = capability
        return capability

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(str(name or ""))

    def list_public(self) -> list[dict[str, Any]]:
        return [self._capabilities[name].public_dict() for name in sorted(self._capabilities)]

    def availability(self, name: str) -> str:
        capability = self.get(name)
        return capability.availability if capability else "unconfigured"

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None, *, policy: CapabilityPolicy | None = None, calls: Mapping[str, int] | None = None) -> dict[str, Any]:
        checkpoint_current()
        capability = self.get(name)
        policy = policy or CapabilityPolicy()
        calls = calls or {}
        if capability is None:
            return {"status": "unconfigured", "name": str(name)}
        if policy.mode == "deny" or capability.name in policy.denied_names or (policy.allowed_names is not None and capability.name not in policy.allowed_names):
            return {"status": "disabled", "name": capability.name, "reason": "policy_denied"}
        if capability.availability != "available":
            return {"status": capability.availability, "name": capability.name}
        if capability.permission == "forbidden":
            return {"status": "forbidden", "name": capability.name}
        if capability.permission == "confirm_each_time" or capability.risk == "high" or capability.access == "write":
            return {"status": "approval_required", "name": capability.name, "risk": capability.risk, "permission": capability.permission}
        if int(calls.get(capability.name, 0)) >= capability.max_calls_per_turn:
            return {"status": "budget_exhausted", "name": capability.name, "reason": "per_capability_limit"}
        if capability.handler is None:
            return {"status": "unconfigured", "name": capability.name}
        coerced = _coerce_arguments(capability.input_schema, arguments or {})
        validation_error = _validate_arguments(capability.input_schema, coerced)
        if validation_error:
            return {"status": "invalid_arguments", "name": capability.name, "reason": validation_error}
        try:
            phase_current('memory' if name.startswith('memory.') else 'search' if name=='web.search' else 'sources' if name=='web.read' else 'generating')
            value = capability.handler(dict(_safe_public(coerced)))
            checkpoint_current()
            return {"status": "success", "name": capability.name, "result": _safe_public(value)}
        except Exception as exc:
            return {"status": "error", "name": capability.name, "error": str(exc)[:300]}


def register_memory_capabilities(
    registry: CapabilityRegistry,
    *,
    memory_query: Callable[[Mapping[str, Any]], Any] | None = None,
    memory_read: Callable[[Mapping[str, Any]], Any] | None = None,
    daily_query: Callable[[Mapping[str, Any]], Any] | None = None,
    time_query: Callable[[Mapping[str, Any]], Any] | None = None,
    weather_query: Callable[[Mapping[str, Any]], Any] | None = None,
    web_search: Callable[[Mapping[str, Any]], Any] | None = None,
    web_read: Callable[[Mapping[str, Any]], Any] | None = None,
    connector_read_feed: Callable[[Mapping[str, Any]], Any] | None = None,
    connector_mark_read: Callable[[Mapping[str, Any]], Any] | None = None,
) -> CapabilityRegistry:
    """Register read-only names; unavailable providers stay explicit in UI."""
    registry.upsert(Capability(
        name="memory.query", description="Query relevant saved memory summaries.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "depth": {"type": "string", "enum": ["quick", "normal", "deep"]},
                "kinds": {"type": "array", "items": {"type": "string"}},
                "scope_type": {"type": "string"},
                "scope_id": {"type": "string"},
                "time_from": {"type": "string"},
                "time_to": {"type": "string"},
                "max_candidates": {"type": "integer", "minimum": 1, "maximum": 1000},
                "max_items": {"type": "integer", "minimum": 1, "maximum": 1000},
                "token_budget": {"type": "integer", "minimum": 64, "maximum": 100000},
            },
            "required": ["query"],
        },
        availability="available" if memory_query else "unconfigured", max_calls_per_turn=2, handler=memory_query,
    ))
    registry.upsert(Capability(
        name="memory.read", description="Read selected saved memories by id after a search.",
        input_schema={
            "type": "object",
            "properties": {
                "memory_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
            },
            "required": ["memory_ids"],
        },
        availability="available" if memory_read else "unconfigured", max_calls_per_turn=2, handler=memory_read,
    ))
    registry.upsert(Capability(
        name="daily.query", description="Read the bounded D0-D3 daily state ring.",
        availability="available" if daily_query else "unconfigured", max_calls_per_turn=1, handler=daily_query,
    ))
    registry.upsert(Capability(
        name="time.query",
        description=(
            "Read the current local time, current backend start time and bounded session/turn time events. "
            "The event ledger never contains message text."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "event_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(ALLOWED_TIME_EVENT_TYPES)},
                    "maxItems": 5,
                },
                "since": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
        availability="available" if time_query else "unconfigured",
        max_calls_per_turn=2,
        handler=time_query,
    ))
    registry.upsert(Capability(
        name="weather.query", description="Get current weather and a three-day forecast by place name or coordinates.",
        input_schema={
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
            },
        },
        availability="available" if weather_query else "unconfigured", max_calls_per_turn=1,
        estimated_latency_ms=1500, handler=weather_query,
    ))
    registry.upsert(Capability(
        name="web.search", description="Search the web when configured and permitted.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "freshness": {"type": "string"},
                "domains": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
        availability="available" if web_search else "unconfigured", max_calls_per_turn=2, handler=web_search,
    ))
    registry.upsert(Capability(
        name="web.read", description="Read selected web pages through the SSRF-safe reader.",
        input_schema={
            "type": "object",
            "properties": {
                "source_ids": {
                    "type": "array",
                    "items": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
                    "maxItems": 5,
                },
                "urls": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "max_chars_each": {"type": "integer", "minimum": 500, "maximum": 12000},
            },
            "anyOf": [{"required": ["source_ids"]}, {"required": ["urls"]}],
        },
        availability="available" if web_read else "unconfigured", max_calls_per_turn=5, handler=web_read,
    ))
    registry.upsert(Capability(
        name="connector.read_feed", description="Read whitelisted external connector events into the local attention inbox.",
        availability="available" if connector_read_feed else "unconfigured", max_calls_per_turn=1,
        handler=connector_read_feed,
    ))
    registry.upsert(Capability(
        name="connector.mark_read", description="Mark local connector events as read after explicit confirmation.",
        input_schema={"type": "object", "properties": {"event_ids": {"type": "array", "items": {"type": "string"}}}},
        availability="available" if connector_mark_read else "unconfigured", access="write", risk="low",
        permission="confirm_each_time", max_calls_per_turn=1, handler=connector_mark_read,
    ))
    return registry
