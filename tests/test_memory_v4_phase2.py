"""Phase 2 write-pipeline contracts and deterministic safety tests."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from saya_app.services.memory_v4 import (
    DeterministicValidator,
    EvidenceCollector,
    MemoryEvidence,
    MemoryV4Store,
    StructuredExtractor,
    WriteIntentGate,
    WritePipeline,
)


class WriteIntentTests(unittest.TestCase):
    def test_gate_is_anchored_and_rejects_question_quote_and_secret(self):
        gate = WriteIntentGate()
        self.assertEqual(gate.detect("我们讨论你会不会记住我").intent, "none")
        self.assertEqual(gate.detect("记住我喜欢咖啡。？").reason_code, "question")
        self.assertEqual(gate.detect("‘记住我喜欢咖啡’，这是引用").intent, "none")
        self.assertEqual(gate.detect("记住：我的密码是 abc").reason_code, "secret_content")
        self.assertEqual(gate.detect("记住：我喜欢咖啡").intent, "explicit_save")
        self.assertEqual(gate.detect(action={"operation": "update", "content": "我喜欢茶"}).intent, "explicit_correct")

    def test_collector_keeps_current_message_and_two_completed_pairs(self):
        evidence = EvidenceCollector().collect(
            "记住：我喜欢咖啡。",
            context=[
                {"id": "u1", "role": "user", "content": "我在学习日语", "status": "completed"},
                {"id": "a1", "role": "assistant", "content": "收到", "status": "completed"},
                {"id": "u2", "role": "user", "content": "我在上海", "status": "completed"},
                {"id": "a2", "role": "assistant", "content": "好的", "status": "completed"},
                {"id": "u3", "role": "user", "content": "未完成的上下文", "status": "streaming"},
            ],
            message_id="current",
        )
        self.assertEqual(evidence[0].source_type, "user_message")
        self.assertEqual(len(evidence), 5)
        self.assertNotIn("未完成", " ".join(item.quote for item in evidence))


class WritePipelineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-phase2-"))
        self.store = MemoryV4Store(self.root / "memory4.sqlite3")
        self.pipeline = WritePipeline(self.store)

    def test_explicit_save_has_evidence_and_memory_updated_event(self):
        result = self.pipeline.process("记住：我喜欢简洁回答。", turn_id="turn-1", message_id="message-1")
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["committed"]), 1)
        item = self.store.list_memories()[0]
        self.assertEqual(len(self.store.list_evidence(item.id)), 1)
        self.assertEqual(self.store.list_events("turn-1")[0]["type"], "memory.updated")
        self.assertFalse(item.locked)

    def test_passive_observation_stays_candidate_and_repeated_observation_counts(self):
        first = self.pipeline.process("我通常先看任务再写代码。", intent="passive_candidate", turn_id="t1")
        second = self.pipeline.process("我通常先看任务再写代码。", intent="passive_candidate", turn_id="t2")
        self.assertFalse(self.store.list_memories())
        self.assertEqual(first["candidates"][0]["status"], "pending")
        self.assertEqual(second["candidates"][0]["occurrence_count"], 2)
        self.assertFalse(second["candidates"][0]["proposal"]["suggest_pin"])

    def test_same_property_updates_one_current_fact_and_different_value_supersedes(self):
        slot = {"intent": "explicit_save", "predicate": "answer_detail"}
        self.pipeline.process("记住：我喜欢简洁回答。", action=slot, turn_id="t1")
        same = self.pipeline.process("记住：我喜欢简洁回答。", action=slot, turn_id="t2")
        self.assertEqual(len(self.store.list_memories()), 1)
        self.assertEqual(same["committed"][0]["status"], "updated")
        changed = self.pipeline.process("记住：我喜欢详细回答。", action=slot, turn_id="t3")
        self.assertEqual(len(self.store.list_memories()), 1)
        self.assertEqual(changed["committed"][0]["operation"], "supersede")
        self.assertEqual(len(self.store.list_versions(self.store.list_memories()[0].id)), 2)

    def test_passive_conflict_does_not_create_second_current_fact(self):
        self.pipeline.process("记住：我喜欢简洁回答。", action={"intent":"explicit_save","predicate":"answer_detail"}, turn_id="t1")
        result = self.pipeline.process("我喜欢详细回答。", action={"intent":"passive_candidate","predicate":"answer_detail"}, turn_id="t2")
        self.assertEqual(len(self.store.list_memories()), 1)
        self.assertEqual(result["candidates"][0]["proposal"]["metadata"]["conflict_with"], [self.store.list_memories()[0].id])

    def test_structured_correction_and_forget_are_versioned_and_audited(self):
        created = self.pipeline.process(
            "记住：我的常用编辑器是 VS Code。",
            action={"intent": "explicit_save", "proposal": {"canonical_text": "我的常用编辑器是 VS Code。", "predicate": "uses_editor"}},
            turn_id="t1",
        )
        memory_id = created["committed"][0]["memory"]["id"]
        corrected = self.pipeline.process(
            "",
            action={"intent": "explicit_correct", "memory_id": memory_id, "proposal": {"canonical_text": "我的常用编辑器是 Neovim。", "predicate": "uses_editor"}},
            turn_id="t2",
        )
        self.assertEqual(corrected["committed"][0]["operation"], "supersede")
        forgotten = self.pipeline.process("忘记这条", action={"intent": "explicit_forget", "memory_id": memory_id}, turn_id="t3")
        self.assertEqual(forgotten["forgotten"], [memory_id])
        self.assertTrue(self.store.is_tombstoned(memory_id))
        self.assertEqual(self.store.list_events("t3")[0]["type"], "memory.updated")


class ValidatorTests(unittest.TestCase):
    def test_validator_rejects_protocol_residue_and_assistant_only_evidence(self):
        gate = WriteIntentGate().detect("记住：Title: 这是残留")
        evidence = EvidenceCollector().collect("记住：Title: 这是残留")
        proposals = StructuredExtractor().extract(gate.content, gate, evidence)
        report = DeterministicValidator().validate(proposals[0], evidence, intent=gate.intent)
        self.assertIn("protocol_residue", report.reason_codes)

        proposal = proposals[0].proposal
        assistant_only = [MemoryEvidence(id="a", memory_id="x", source_type="assistant_context", source_id="a", role="context", quote="assistant said it")]
        self.assertIn("missing_user_assertion", DeterministicValidator().validate(proposal, assistant_only, intent="explicit_save").reason_codes)

    def test_evidence_hash_mismatch_is_rejected_at_contract_boundary(self):
        with self.assertRaises(ValueError):
            MemoryEvidence(id="e", memory_id="m", source_type="chat", source_id="s", quote="quote", content_hash="bad")

    def test_knowledge_base_capture_is_a_compatibility_adapter_when_flagged(self):
        from core_services import KnowledgeBase

        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-adapter-")) / "legacy-runtime"
        knowledge_base = KnowledgeBase(str(root))
        knowledge_base.update_settings({"memory_v4_write_enabled": True})
        legacy = knowledge_base.capture_explicit_memory("记住：我喜欢安静的工作环境。", session_id="turn-adapter")
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy["source"], "write_pipeline")
        self.assertIsNotNone(knowledge_base.memory_v4_store.get_memory(legacy["id"]))


if __name__ == "__main__":
    unittest.main()
