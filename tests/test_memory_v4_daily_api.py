from pathlib import Path
import tempfile
import unittest

from core_services import KnowledgeBase


class MemoryV4DailyAdapterTests(unittest.TestCase):
    def test_daily_adapter_is_enabled_by_default(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-daily-"))
        kb = KnowledgeBase(str(root))
        result = kb.memory_v4_daily_state(action="conversation", day="2026-09-14")
        self.assertEqual(result["status"], "ready")
        self.assertTrue((root / "memory4.sqlite3").exists())

    def test_daily_adapter_records_conversation_and_rollover_ring(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-daily-enabled-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"daily_state_v2_enabled": True})
        first = kb.memory_v4_daily_state(action="conversation", day="2026-09-14")
        second = kb.memory_v4_daily_state(action="conversation", day="2026-09-14")
        self.assertEqual(first["status"], "ready")
        self.assertTrue(first["first_contact_today"])
        self.assertFalse(second["first_contact_today"])
        self.assertEqual(second["state"]["conversation_count"], 2)
        rollover = kb.memory_v4_daily_state(
            action="rollover", day="2026-09-14", tasks=[{"id": "keep", "status": "pending"}, {"id": "done", "status": "done"}],
        )
        self.assertEqual(rollover["carryover_task_ids"], ["keep"])
        self.assertIn("2026-09-14", rollover["retained_days"])


if __name__ == "__main__":
    unittest.main()
