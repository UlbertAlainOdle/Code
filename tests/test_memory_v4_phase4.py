"""Phase 4 capability and Agent Loop safety-seam tests."""

from __future__ import annotations

from pathlib import Path
from threading import Event
import tempfile
import unittest

from saya_app.services.memory_v4 import (
    AgentLoop,
    Capability,
    CapabilityPolicy,
    CapabilityRegistry,
    MemoryV4Store,
    register_memory_capabilities,
)


class Phase4Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-phase4-"))
        self.store = MemoryV4Store(self.root / "memory4.sqlite3")

    def test_registry_reports_unconfigured_and_requires_approval_for_risky_write(self):
        registry = CapabilityRegistry()
        registry.register(Capability(
            "read.test",
            "safe read",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            handler=lambda args: {"value": args.get("value")},
        ))
        registry.register(Capability("write.test", "external write", access="write", risk="high", permission="confirm_each_time", handler=lambda args: {"sent": True}))
        self.assertEqual(registry.availability("missing"), "unconfigured")
        self.assertEqual(registry.execute("read.test", {"value": "ok"})["status"], "success")
        self.assertEqual(registry.execute("read.test", {})["status"], "invalid_arguments")
        self.assertEqual(registry.execute("write.test", {})["status"], "approval_required")

    def test_readonly_names_are_registered_without_fabricating_provider_availability(self):
        registry = register_memory_capabilities(CapabilityRegistry(), memory_query=lambda args: {"items": []})
        self.assertEqual(registry.availability("memory.query"), "available")
        self.assertEqual(registry.availability("web.search"), "unconfigured")
        self.assertEqual(registry.availability("connector.read_feed"), "unconfigured")
        self.assertEqual(registry.get("web.search").access, "read")

    def test_agent_can_choose_no_tool_and_persists_public_events_only(self):
        registry = CapabilityRegistry()
        result = AgentLoop(registry, store=self.store).run(
            "turn-final",
            lambda context, capabilities: {"final_answer": "直接回答", "chain_of_thought": "must not persist"},
        )
        self.assertEqual(result.status, "completed")
        self.assertIn("turn.completed", [event["type"] for event in result.events])
        self.assertNotIn("chain_of_thought", str(result.events))
        self.assertNotIn("chain_of_thought", str(self.store.list_events("turn-final", include_private=True)))

    def test_agent_executes_tool_then_final_with_dedup_and_safe_argument_hash(self):
        calls = []
        registry = CapabilityRegistry()
        registry.register(Capability("memory.query", "query", max_calls_per_turn=2, handler=lambda args: calls.append(args) or {"summary": "found"}))
        outputs = [
            {"tool_calls": [{"id": "s1", "name": "memory.query", "arguments": {"query": "preference", "token": "secret"}}, {"id": "s2", "name": "memory.query", "arguments": {"query": "preference", "token": "secret"}}]},
            {"final_answer": "找到相关偏好"},
        ]
        def model(context, capabilities):
            return outputs.pop(0)
        result = AgentLoop(registry, store=self.store).run("turn-tool", model)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(calls), 1, "identical tool calls must be deduplicated")
        self.assertTrue(any(event["type"] == "tool.call.completed" for event in result.events))
        self.assertNotIn("secret", str(self.store.list_events("turn-tool", include_private=True)))

    def test_agent_context_preserves_native_tool_call_envelope(self):
        registry = CapabilityRegistry()
        registry.register(Capability("read.test", "safe read", handler=lambda args: {"ok": True}))
        seen = []

        def model(context, capabilities):
            seen.append([dict(item) for item in context])
            if len(seen) == 1:
                return {"tool_calls": [{"id": "call-1", "name": "read.test", "arguments": {}}]}
            return {"final_answer": "完成"}

        result = AgentLoop(registry).run("turn-envelope", model)
        self.assertEqual(result.status, "completed")
        assistant = next(item for item in seen[1] if item.get("role") == "model")
        tool = next(item for item in seen[1] if item.get("role") == "tool")
        self.assertEqual(assistant["tool_calls"][0]["id"], "call-1")
        self.assertEqual(tool["tool_call_id"], "call-1")

    def test_agent_stops_at_budget_and_supports_cancel(self):
        registry = CapabilityRegistry()
        registry.register(Capability("read.test", "safe read", max_calls_per_turn=10, handler=lambda args: {"ok": True}))
        result = AgentLoop(registry).run(
            "turn-budget", lambda context: {"tool_calls": [{"name": "read.test", "arguments": {}}]},
            policy=CapabilityPolicy(max_steps=2),
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "max_steps")
        cancel = Event()
        cancel.set()
        cancelled = AgentLoop(registry).run("turn-cancel", lambda context: {"final_answer": "never"}, cancel_event=cancel)
        self.assertEqual(cancelled.status, "cancelled")


if __name__ == "__main__":
    unittest.main()
