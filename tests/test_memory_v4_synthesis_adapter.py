from pathlib import Path
import tempfile
import unittest

from core_services import KnowledgeBase
from saya_app.services.memory_v4 import MemoryCandidateV4, MemoryEvidence, MemoryItemV4


class MemoryV4SynthesisAdapterTests(unittest.TestCase):
    def test_adapter_is_disabled_without_flags_and_does_not_create_v4_store(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-synthesis-disabled-"))
        kb = KnowledgeBase(str(root))
        result = kb.run_memory_v4_synthesis_once()
        self.assertEqual(result["status"], "disabled")
        self.assertTrue((root / "memory4.sqlite3").exists())  # versioned memory is the default store

    def test_adapter_uses_configured_producer_and_safe_scheduler(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-synthesis-enabled-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({
            "memory_v4_write_enabled": True,
            "memory_background_synthesis_enabled": True,
        })
        store = kb.memory_v4_store
        item = MemoryItemV4(
            id="synthesis-evidence-memory", subject="user", predicate="prefers", value="short",
            canonical_text="Doctor may prefer short answers.", kind="preference",
            authority="user_confirmed", sensitivity="normal",
        )
        evidence = MemoryEvidence(
            id="synthesis-evidence", memory_id=item.id, source_type="user_message",
            source_id="synthesis-source", role="assertion", quote=item.canonical_text,
        )
        store.upsert_memory(item, [evidence])
        store.upsert_candidate(MemoryCandidateV4(
            id="synthesis-candidate", proposal={"canonical_text": item.canonical_text},
            evidence_ids=[evidence.id],
        ))
        kb.memory_v4_synthesis_producer = lambda candidate: {
            "operations": [{
                "op": "replace", "path": "/profile/answer_style", "value": "short",
                "evidence_refs": [evidence.id],
            }]
        }

        result = kb.run_memory_v4_synthesis_once(limit=1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(kb.memory_v4_store.get_meta("synthesis:default"), '{"profile": {"answer_style": "short"}}')
        self.assertEqual(kb.memory_v4_store.get_candidate("synthesis-candidate").status, "accepted")
        repeated = kb.run_memory_v4_synthesis_once(limit=1)
        self.assertEqual(repeated["processed"], 0)

    def test_enabled_scheduler_without_producer_is_explicitly_unconfigured(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-synthesis-unconfigured-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({
            "memory_v4_write_enabled": True,
            "memory_background_synthesis_enabled": True,
        })
        result = kb.run_memory_v4_synthesis_once()
        self.assertEqual(result["status"], "unconfigured")

    def test_background_schedule_is_bounded_and_non_blocking(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-synthesis-background-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"memory_v4_write_enabled": True, "memory_background_synthesis_enabled": True})
        kb.memory_v4_synthesis_producer = lambda candidate: {"operations": []}
        scheduled = kb.schedule_memory_v4_synthesis(limit=2)
        self.assertIn(scheduled["status"], {"scheduled", "already_running"})
        thread = getattr(kb, "_memory_v4_synthesis_thread", None)
        if thread is not None:
            thread.join(timeout=2)
        self.assertIn(kb.memory_v4_synthesis_status()["status"], {"idle", "running"})


if __name__ == "__main__":
    unittest.main()
