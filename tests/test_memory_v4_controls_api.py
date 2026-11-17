"""KnowledgeBase adapter contracts for V4 controls and local API seams."""

from pathlib import Path
import tempfile
import unittest

from core_services import KnowledgeBase
from saya_app.services.memory_v4 import MemoryEvidence, MemoryItemV4


class MemoryV4ControlsAdapterTests(unittest.TestCase):
    def test_controls_are_flagged_auditable_and_restoreable(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-controls-"))
        kb = KnowledgeBase(str(root))
        self.assertEqual(kb.memory_v4_controls_summary()["status"], "ready")
        kb.update_settings({"memory_v4_write_enabled": True})
        store = kb.memory_v4_store
        item = MemoryItemV4(
            id="control-memory", subject="user", predicate="prefers", value="concise",
            canonical_text="Doctor prefers concise controls.", kind="preference",
            authority="user_confirmed", sensitivity="normal",
        )
        evidence = MemoryEvidence(
            id="control-evidence", memory_id=item.id, source_type="user_message",
            source_id="control-source", role="assertion", quote=item.canonical_text,
        )
        store.upsert_memory(item, [evidence])

        summary = kb.memory_v4_controls_summary()
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["memories"][0]["id"], item.id)
        pinned = kb.memory_v4_control("pin", memory_id=item.id)
        self.assertEqual(pinned["status"], "success")
        corrected = kb.memory_v4_control(
            "correct", memory_id=item.id,
            updates={"canonical_text": "Doctor prefers warm concise controls.", "value": "warm concise"},
        )
        self.assertEqual(corrected["status"], "success")
        self.assertEqual(corrected["memory"]["revision"], 3)
        forbidden = kb.memory_v4_control("correct", memory_id=item.id, updates={"revision": 99})
        self.assertEqual(forbidden["status"], "rejected")
        forgotten = kb.memory_v4_control("forget", memory_id=item.id)
        self.assertEqual(forgotten["status"], "success")
        restored = kb.memory_v4_control("restore", memory_id=item.id, revision=1)
        self.assertEqual(restored["status"], "success")
        self.assertEqual(restored["memory"]["canonical_text"], item.canonical_text)


if __name__ == "__main__":
    unittest.main()
