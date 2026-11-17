"""Phase 3 retrieval, DayState, and Prompt Compiler seam tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from saya_app.services.memory_v4 import (
    DayStateManager,
    MemoryEvidence,
    MemoryItemV4,
    MemoryNeedPlanner,
    MemoryQuery,
    MemoryRetriever,
    MemoryV4Store,
    PromptCompiler,
)


class Phase3Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-phase3-"))
        self.store = MemoryV4Store(self.root / "memory4.sqlite3")

    def add(self, memory_id, text, *, predicate="has_memory", kind="knowledge", valid_to=None, activation_policy="query", pinned=False):
        item = MemoryItemV4(
            id=memory_id, subject="user", predicate=predicate, value=text, canonical_text=text,
            kind=kind, authority="user_confirmed", sensitivity="normal", valid_to=valid_to,
            activation_policy=activation_policy, pinned=pinned,
        )
        evidence = MemoryEvidence(id=f"e-{memory_id}", memory_id=memory_id, source_type="user_message", source_id="m", role="assertion", quote=text)
        self.store.upsert_memory(item, [evidence])
        return item

    def test_need_planner_is_a_relevance_gate(self):
        self.add("m", "Doctor prefers concise answers.", predicate="prefers")
        no_need = MemoryNeedPlanner().plan("请写一个排序算法")
        self.assertEqual(MemoryRetriever(self.store).search(no_need).items, [])
        need = MemoryNeedPlanner().plan("you remember my preferred answers")
        self.assertTrue(MemoryRetriever(self.store).search(need).items)

    def test_retrieval_filters_expired_and_event_memories_and_returns_summaries_then_full(self):
        self.add("fresh", "Doctor prefers concise answers.", predicate="prefers", pinned=True)
        self.add("old", "Doctor planned a trip.", predicate="trip", valid_to="2020-01-01T00:00:00")
        self.add("event", "Doctor attended a museum event.", predicate="event", activation_policy="event")
        result = MemoryRetriever(self.store).search(MemoryQuery(query="Doctor prefers answers"), turn_id="t1")
        self.assertEqual([item.id for item in result.items], ["fresh"])
        self.assertEqual(result.candidates[0].id, "fresh")
        self.assertTrue(any(entry["id"] == "old" for entry in result.omitted))
        self.assertTrue(any(entry["id"] == "event" for entry in result.omitted))

    def test_actual_use_receipt_is_separate_from_candidate_selection(self):
        self.add("m", "Doctor prefers concise answers.", predicate="prefers")
        retriever = MemoryRetriever(self.store)
        retriever.search(MemoryQuery(query="my preference"), turn_id="turn")
        self.assertFalse(self.store.list_memory_usage("m", limit=10)[0]["used"])
        receipts = retriever.acknowledge_used(["m"], turn_id="turn", used=True)
        self.assertTrue(receipts[0]["used"])
        self.assertTrue(any(row["used"] for row in self.store.list_memory_usage("m", limit=10)))
        scores = self.store.memory_usage_scores()
        self.assertAlmostEqual(scores["m"], 0.5)
        scores = self.store.memory_usage_scores()
        self.assertAlmostEqual(scores["m"], 0.5)

    def test_day_state_has_separate_counters_first_contact_and_d3_ring(self):
        manager = DayStateManager(self.store, timezone="Asia/Shanghai")
        today = "2026-09-14"
        manager.record_page_load(today)
        manager.record_page_load(today)
        manager.record_backend_start(today)
        manager.record_conversation(today)
        first = manager.first_contact_today(today)
        second = manager.first_contact_today(today)
        self.assertTrue(first["first_contact_today"])
        self.assertFalse(second["first_contact_today"])
        state = self.store.get_day_state(today)
        self.assertEqual((state.page_load_count, state.backend_start_count, state.conversation_count), (2, 1, 1))
        manager.ensure("2026-09-13")
        manager.ensure("2026-09-12")
        manager.ensure("2026-09-11")
        self.assertEqual([entry["date"] for entry in manager.ring(today)], [today, "2026-09-13", "2026-09-12", "2026-09-11"])

    def test_rollover_keeps_d3_and_purges_d4(self):
        manager = DayStateManager(self.store)
        manager.ensure("2026-09-10")
        manager.ensure("2026-09-11")
        result = manager.rollover("2026-09-14", tasks=[{"id": "task-1", "status": "pending"}, {"id": "task-2", "status": "done"}])
        self.assertEqual(result["carryover_task_ids"], ["task-1"])
        self.assertIsNone(self.store.get_day_state("2026-09-10"))
        self.assertIsNotNone(self.store.get_day_state("2026-09-11"))

    def test_prompt_compiler_only_injects_time_and_daily_when_triggered(self):
        item = self.add("m", "Doctor prefers concise answers.", predicate="prefers", pinned=True)
        daily = [{"date": "2026-09-14", "state": {"carryover_task_ids": ["task-1"]}}]
        plain = PromptCompiler().compile("帮我写一封邮件", memory_items=[item], daily_ring=daily, first_contact_today=False)
        self.assertNotIn("Time context", plain.prompt)
        self.assertNotIn("daily state", plain.prompt)
        self.assertIn("Doctor prefers concise answers", plain.prompt)
        timed = PromptCompiler().compile("今天几点开会？", memory_items=[item], daily_ring=daily, first_contact_today=True)
        self.assertIn("Time context", timed.prompt)
        self.assertIn("task-1", timed.prompt)
        self.assertNotIn("memory:m", timed.prompt.split("current_request")[0])

    def test_prompt_audit_has_hash_and_omits_full_prompt(self):
        result = PromptCompiler().compile("What is the date today?", token_budget=300)
        self.assertIn("prompt_hash", result.audit)
        self.assertNotIn("prompt", result.audit)
        self.assertLessEqual(result.audit["estimated_tokens"], 300)

    def test_knowledge_base_v4_context_adapter_is_flagged_and_memory_only(self):
        from core_services import KnowledgeBase

        root = self.root / "runtime"
        knowledge_base = KnowledgeBase(str(root))
        store = MemoryV4Store(root / "memory4.sqlite3")
        self.add("v4-main", "Doctor prefers V4 concise answers.", predicate="prefers")
        source_store = self.store
        item = source_store.get_memory("v4-main")
        evidence = source_store.list_evidence("v4-main")
        store.upsert_memory(item, [entry for entry in evidence])

        knowledge_base.settings["memory_v4_read_enabled"] = False
        disabled = knowledge_base.build_memory_v4_context_with_audit("my preference")
        self.assertEqual(disabled["status"], "disabled")
        knowledge_base.settings["memory_v4_read_enabled"] = True
        ready = knowledge_base.build_memory_v4_context_with_audit("my preference", turn_id="v4-turn")
        self.assertEqual(ready["status"], "ready")
        self.assertIn("Doctor prefers V4 concise answers", ready["context"])
        self.assertNotIn("prompt", ready["audit"])
        status = knowledge_base.get_memory_v4_status()
        self.assertEqual(status["status"], "ready")
        self.assertTrue(status["read_enabled"])
        self.assertTrue(status["write_enabled"])


if __name__ == "__main__":
    unittest.main()
