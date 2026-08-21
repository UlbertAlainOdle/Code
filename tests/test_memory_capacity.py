import shutil
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_services import KnowledgeBase


class MemoryCapacityGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-capacity-tests-"))
        self.kb = KnowledgeBase(str(self.root))
        self.kb.settings.update({
            "memory_capacity_warning_ratio": 0.5,
            "memory_compression_min_group": 2,
            "memory_compression_group_size": 20,
            "memory_compression_summary_chars": 600,
            "memory_compression_age_days": 1,
            "memory_cold_archive_enabled": True,
        })

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _add_old_compressible(self, index):
        return self.kb.add_memory(
            f"Old autonomous memory {index} with a stable piece of context.",
            source="auto_autonomous",
            category="general",
            metadata={"date": "2020-01-01", "memory_layer": "semantic"},
        )

    def test_warning_is_recorded_before_automatic_threshold(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 8,
            "memory_active_hard_limit": 10,
            "memory_archive_soft_limit": 100,
            "memory_archive_hard_limit": 120,
        })
        for index in range(5):
            self.kb.add_memory(f"Durable warning memory {index}", source="manual")

        status = self.kb.get_memory_maintenance_status()
        self.assertEqual(status["capacity"]["band"], "warning")
        self.assertTrue(status["pending_events"])
        self.assertEqual(status["pending_events"][-1]["code"], "capacity_warning")
        self.assertEqual(status["pending_events"][-1]["token_usage"], 0)

    def test_soft_limit_automatically_compacts_with_notice(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 6,
            "memory_active_hard_limit": 8,
            "memory_archive_soft_limit": 100,
            "memory_archive_hard_limit": 120,
            "memory_capacity_auto_compact_enabled": True,
        })
        originals = [self._add_old_compressible(index) for index in range(6)]

        result = self.kb.last_maintenance_result
        self.assertEqual(result["status"], "auto_compaction")
        self.assertTrue(result["notify_user"])
        self.assertEqual(result["token_usage"], 0)
        self.assertLess(self.kb._memory_capacity_snapshot()["active"], 6)
        archived_ids = {
            memory["id"] for memory in self.kb.memories if memory.get("is_archived")
        }
        self.assertTrue(archived_ids.intersection({memory["id"] for memory in originals}))
        self.assertTrue(any(
            memory.get("source_detail", {}).get("compression_engine") == "deterministic_local"
            for memory in self.kb.memories
        ))

    def test_hard_limit_forces_compaction_even_when_auto_is_disabled(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 5,
            "memory_active_hard_limit": 6,
            "memory_archive_soft_limit": 100,
            "memory_archive_hard_limit": 120,
            "memory_capacity_auto_compact_enabled": False,
        })
        for index in range(6):
            self.kb.add_memory(
                f"Protected durable memory {index}",
                source="manual",
                is_important=True,
                metadata={"is_locked": True, "memory_layer": "semantic"},
            )

        result = self.kb.last_maintenance_result
        self.assertEqual(result["status"], "forced_compaction")
        self.assertEqual(result["token_usage"], 0)
        self.assertLess(self.kb._memory_capacity_snapshot()["active"], 6)
        self.assertEqual(result["event"]["code"], "hard_limit_forced_compaction")

    def test_archive_hard_limit_moves_full_originals_to_recoverable_cold_storage(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 100,
            "memory_active_hard_limit": 120,
            "memory_archive_soft_limit": 4,
            "memory_archive_hard_limit": 5,
            "memory_capacity_auto_compact_enabled": False,
        })
        for index in range(5):
            self.kb.add_memory(
                f"Archived source record {index}",
                source="session_archive",
                category="conversation_summary",
                metadata={
                    "id": f"archive-{index}",
                    "is_archived": True,
                    "memory_layer": "archive",
                },
            )

        result = self.kb.last_maintenance_result
        self.assertEqual(result["status"], "forced_compaction")
        self.assertGreater(result["cold_archived_count"], 0)
        self.assertLess(self.kb._memory_capacity_snapshot()["archived"], 5)
        cold = self.kb.list_cold_archive_memories(limit=20)
        self.assertTrue(cold)
        restored = self.kb.restore_cold_archive_memory(cold[0]["id"])
        self.assertIsNotNone(restored)
        self.assertTrue(restored["is_archived"])
        self.assertIsNotNone(self.kb._find_memory(restored["id"]))

    def test_cold_archive_rolls_back_when_active_store_save_fails(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 100,
            "memory_active_hard_limit": 120,
            "memory_archive_soft_limit": 100,
            "memory_archive_hard_limit": 120,
        })
        memory = self.kb.add_memory(
            "Archived record that must not be orphaned.",
            metadata={"is_archived": True, "memory_layer": "archive"},
            run_capacity_check=False,
        )
        with patch.object(self.kb, "save_memories", return_value=False):
            result = self.kb._cold_store_memories([memory], reason="test", force=True)
        self.assertEqual(result["moved_count"], 0)
        self.assertIsNotNone(self.kb._find_memory(memory["id"]))
        self.assertNotIn(memory["id"], self.kb.deleted_memory_ids)
        self.assertEqual(self.kb.list_cold_archive_memories(limit=20), [])

    def test_cold_restore_rolls_back_if_archive_rewrite_fails(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 100,
            "memory_active_hard_limit": 120,
            "memory_archive_soft_limit": 100,
            "memory_archive_hard_limit": 120,
        })
        memory = self.kb.add_memory(
            "Cold record whose restore transaction will fail.",
            metadata={"is_archived": True, "memory_layer": "archive"},
            run_capacity_check=False,
        )
        moved = self.kb._cold_store_memories([memory], reason="test", force=True)
        self.assertEqual(moved["moved_count"], 1)
        real_replace = os.replace

        def fail_cold_finalize(source, target):
            if str(source) == self.kb.cold_archive_path + ".tmp":
                raise OSError("simulated cold archive rewrite failure")
            return real_replace(source, target)

        with patch("core_services.os.replace", side_effect=fail_cold_finalize):
            restored = self.kb.restore_cold_archive_memory(memory["id"])
        self.assertIsNone(restored)
        self.assertIsNone(self.kb._find_memory(memory["id"]))
        self.assertIn(memory["id"], self.kb.deleted_memory_ids)
        self.assertEqual(self.kb.list_cold_archive_memories(limit=20)[0]["id"], memory["id"])

    def test_hard_limits_cannot_be_saved_below_soft_limits(self):
        settings = self.kb.update_settings({
            "memory_active_soft_limit": 500,
            "memory_active_hard_limit": 100,
            "memory_archive_soft_limit": 900,
            "memory_archive_hard_limit": 100,
        })
        self.assertEqual(settings["memory_active_hard_limit"], 500)
        self.assertEqual(settings["memory_archive_hard_limit"], 900)


if __name__ == "__main__":
    unittest.main()
