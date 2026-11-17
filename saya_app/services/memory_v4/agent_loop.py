"""Bounded, evented Agent Loop for read-only capability tools."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import json
from threading import Event
from typing import Any, Callable, Mapping

from .capabilities import CapabilityPolicy, CapabilityRegistry, _safe_public
from .store import MemoryV4Store
from ..qq_tasks import checkpoint_current


@dataclass
class AgentLoopResult:
    status: str
    answer: str = ""
    steps: int = 0
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "answer": self.answer, "steps": self.steps,
            "tool_results": self.tool_results, "events": self.events, "reason": self.reason,
        }


class AgentLoop:
    """Ask the model for a final answer or bounded tool calls until completion."""

    def __init__(self, registry: CapabilityRegistry, *, store: MemoryV4Store | None = None):
        self.registry = registry
        self.store = store

    def run(
        self,
        turn_id: str,
        model_step: Callable[..., Mapping[str, Any]],
        *,
        initial_context: list[Mapping[str, Any]] | None = None,
        policy: CapabilityPolicy | None = None,
        cancel_event: Event | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentLoopResult:
        policy = policy or CapabilityPolicy()
        cancel_event = cancel_event or Event()
        events: list[dict[str, Any]] = []
        context: list[dict[str, Any]] = [dict(item) for item in (initial_context or []) if isinstance(item, Mapping)]
        tool_results: list[dict[str, Any]] = []
        seen_calls: set[str] = set()
        call_counts: dict[str, int] = {}
        total_cost = 0.0
        total_latency_ms = 0
        self._emit(events, turn_id, 0, "turn.started", {"turn_id": turn_id}, event_sink=event_sink)
        for step in range(1, policy.max_steps + 1):
            checkpoint_current()
            if cancel_event.is_set():
                self._emit(events, turn_id, step, "turn.failed", {"reason": "cancelled"}, event_sink=event_sink)
                return AgentLoopResult("cancelled", steps=step - 1, tool_results=tool_results, events=events, reason="cancelled")
            try:
                output = self._model_step(model_step, context, policy)
                checkpoint_current()
            except Exception as exc:
                self._emit(
                    events,
                    turn_id,
                    step,
                    "turn.failed",
                    {"reason": "model_step_error", "error": str(exc)[:240]},
                    event_sink=event_sink,
                )
                return AgentLoopResult(
                    "failed",
                    steps=step,
                    tool_results=tool_results,
                    events=events,
                    reason="model_step_error",
                )
            if not isinstance(output, Mapping):
                self._emit(events, turn_id, step, "turn.failed", {"reason": "invalid_model_output"}, event_sink=event_sink)
                return AgentLoopResult("failed", steps=step, tool_results=tool_results, events=events, reason="invalid_model_output")
            if str(output.get("final_answer") or "").strip():
                answer = str(output["final_answer"]).strip()
                self._emit(events, turn_id, step, "assistant.message.delta", {"step_id": f"step-{step}", "message": answer}, event_sink=event_sink)
                self._emit(events, turn_id, step + 1, "turn.completed", {"steps": step}, event_sink=event_sink)
                return AgentLoopResult("completed", answer=answer, steps=step, tool_results=tool_results, events=events)
            permission = output.get("permission_request")
            if isinstance(permission, Mapping):
                permission_name = str(permission.get("name") or "unknown")
                permission_capability = self.registry.get(permission_name)
                if permission_capability is None or permission_capability.availability != "available":
                    # Asking permission for a tool that cannot run in this
                    # session would stall the turn forever. Feed back an
                    # honest unavailable result so the model can answer or
                    # degrade gracefully on the next step.
                    feedback = {
                        "status": permission_capability.availability if permission_capability else "unconfigured",
                        "name": permission_name,
                        "reason": "permission_requested_unavailable_tool",
                    }
                    self._emit(events, turn_id, step, "tool.call.failed", {
                        "step_id": f"step-{step}-permission",
                        "name": permission_name,
                        "status": feedback["status"],
                        "summary": feedback["reason"],
                    }, event_sink=event_sink)
                    context.append({
                        "role": "tool",
                        "name": permission_name,
                        "tool_call_id": f"step-{step}-permission",
                        "status": feedback["status"],
                        "content": json.dumps(_safe_public(feedback), ensure_ascii=False),
                        "result": _safe_public(feedback),
                    })
                    continue
                payload = {"step_id": f"step-{step}", "name": permission_name, "reason": str(permission.get("reason") or "confirmation required")[:240]}
                self._emit(events, turn_id, step, "approval.required", payload, event_sink=event_sink)
                return AgentLoopResult("needs_approval", steps=step, tool_results=tool_results, events=events, reason="approval_required")
            calls = output.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                partial = str(output.get("partial_answer") or "").strip()
                if not partial:
                    partial = "我无法在当前工具预算内完成这一步。"
                self._emit(events, turn_id, step, "assistant.message.delta", {"step_id": f"step-{step}", "message": partial}, event_sink=event_sink)
                self._emit(events, turn_id, step + 1, "turn.completed", {"steps": step, "partial": True}, event_sink=event_sink)
                return AgentLoopResult("partial", answer=partial, steps=step, tool_results=tool_results, events=events, reason="no_final_answer")
            # Preserve the exact assistant tool-call envelope.  The previous
            # implementation only stored ``tool_call_count`` which made the
            # next provider request look like an empty assistant message and
            # dropped the call ids required by native function calling APIs.
            public_calls = []
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                public_calls.append({
                    "id": str(call.get("id") or "")[:120],
                    "name": str(call.get("name") or call.get("tool") or "")[:120],
                    "arguments": _safe_public(
                        call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
                    ),
                })
            context.append({
                "role": "model",
                "content": str(output.get("content") or ""),
                "tool_calls": public_calls,
                "step": step,
            })
            for index, call in enumerate(calls):
                if cancel_event.is_set():
                    self._emit(events, turn_id, step, "turn.failed", {"reason": "cancelled"}, event_sink=event_sink)
                    return AgentLoopResult("cancelled", steps=step, tool_results=tool_results, events=events, reason="cancelled")
                if not isinstance(call, Mapping):
                    continue
                name = str(call.get("name") or call.get("tool") or "").strip()
                arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
                step_id = str(call.get("id") or f"step-{step}-tool-{index}")[:120]
                key = name + ":" + json.dumps(_safe_public(arguments), sort_keys=True, ensure_ascii=False)
                if key in seen_calls:
                    result = {"status": "skipped", "name": name, "reason": "duplicate_call"}
                    tool_results.append(result)
                    context.append({
                        "role": "tool",
                        "name": name,
                        "tool_call_id": step_id,
                        "status": result.get("status"),
                        "content": json.dumps(_safe_public(result), ensure_ascii=False),
                        "result": _safe_public(result),
                    })
                    continue
                seen_calls.add(key)
                self._emit(events, turn_id, step, "tool.call.started", {"step_id": step_id, "name": name, "arguments_hash": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]}, event_sink=event_sink)
                capability = self.registry.get(name)
                if capability is not None:
                    estimated_cost = float(capability.estimated_cost or 0.0)
                    estimated_latency = int(capability.estimated_latency_ms or 0)
                else:
                    estimated_cost = 0.0
                    estimated_latency = 0
                if total_cost + estimated_cost > policy.max_total_cost:
                    result = {"status": "budget_exhausted", "name": name, "reason": "cost_limit"}
                elif total_latency_ms + estimated_latency > policy.max_total_latency_ms:
                    result = {"status": "budget_exhausted", "name": name, "reason": "latency_limit"}
                else:
                    result = self.registry.execute(name, arguments, policy=policy, calls=call_counts)
                    total_cost += estimated_cost
                    total_latency_ms += estimated_latency
                call_counts[name] = call_counts.get(name, 0) + 1
                safe_result = _safe_public(result)
                tool_results.append(safe_result)
                if result.get("status") == "approval_required":
                    self._emit(events, turn_id, step, "approval.required", {"step_id": step_id, "name": name, "risk": result.get("risk"), "permission": result.get("permission")}, event_sink=event_sink)
                    return AgentLoopResult("needs_approval", steps=step, tool_results=tool_results, events=events, reason="approval_required")
                self._emit(events, turn_id, step, "tool.call.completed" if result.get("status") == "success" else "tool.call.failed", {"step_id": step_id, "name": name, "status": result.get("status"), "summary": self._summary(result)}, event_sink=event_sink)
                context.append({
                    "role": "tool",
                    "name": name,
                    "tool_call_id": step_id,
                    "status": result.get("status"),
                    "content": json.dumps(safe_result, ensure_ascii=False),
                    "result": safe_result.get("result") if isinstance(safe_result, Mapping) else None,
                })
        partial = "我已达到本轮工具步骤上限，以上是目前能确认的部分结果。"
        self._emit(events, turn_id, policy.max_steps + 1, "assistant.message.delta", {"step_id": f"step-{policy.max_steps}", "message": partial}, event_sink=event_sink)
        self._emit(events, turn_id, policy.max_steps + 2, "turn.completed", {"steps": policy.max_steps, "partial": True, "reason": "max_steps"}, event_sink=event_sink)
        return AgentLoopResult("partial", answer=partial, steps=policy.max_steps, tool_results=tool_results, events=events, reason="max_steps")

    def _model_step(
        self,
        model_step: Callable[..., Mapping[str, Any]],
        context: list[Mapping[str, Any]],
        policy: CapabilityPolicy,
    ) -> Mapping[str, Any]:
        try:
            parameters = inspect.signature(model_step).parameters
            if len(parameters) >= 2:
                capabilities = self.registry.list_public()
                # Show the planner only what it can actually execute this
                # turn: unconfigured entries invite dead calls that the policy
                # would reject anyway.
                capabilities = [
                    item for item in capabilities
                    if item.get("availability") == "available"
                ]
                if policy.mode == "deny":
                    capabilities = []
                elif policy.allowed_names is not None:
                    capabilities = [item for item in capabilities if item.get("name") in policy.allowed_names]
                if policy.denied_names:
                    capabilities = [item for item in capabilities if item.get("name") not in policy.denied_names]
                return model_step(context, capabilities)
        except (TypeError, ValueError):
            pass
        return model_step(context)

    @staticmethod
    def _summary(result: Mapping[str, Any]) -> str:
        if result.get("status") == "success":
            value = result.get("result")
            return str(value if isinstance(value, str) else "completed")[:240]
        return str(result.get("reason") or result.get("error") or result.get("status") or "failed")[:240]

    def _emit(self, events: list[dict[str, Any]], turn_id: str, sequence: int, event_type: str, payload: Mapping[str, Any], *, event_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        event = {
            "id": f"{turn_id}:{sequence}:{event_type}", "turn_id": str(turn_id), "sequence": int(sequence),
            "type": event_type, "public_payload": _safe_public(dict(payload)), "private_payload": None,
            "status": "completed", "created_at": datetime_now(),
        }
        events.append(event)
        if self.store is not None:
            self.store.record_event(event)
        if event_sink is not None:
            try:
                event_sink(dict(event))
            except Exception:
                # A disconnected SSE consumer must never break tool execution.
                pass


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
