import shutil
import tempfile
import unittest
from pathlib import Path

from core_services import KnowledgeBase


class MemoryAiCompressionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-ai-compression-tests-"))
        self.kb = KnowledgeBase(str(self.root))
        self.kb.settings.update({
            "memory_compression_min_group": 2,
            "memory_compression_group_size": 20,
            "memory_compression_summary_chars": 900,
            "memory_compression_max_generations": 2,
            "memory_ai_compression_enabled": True,
            "memory_ai_compression_max_memories": 50,
            "memory_ai_compression_max_generations": 2,
        })

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _add_repeated_summary(self, index, content=None):
        return self.kb.add_memory(
            content or f"On 2026-08-11 the project chose 16 candidates for search batch {index}.",
            title=f"Search summary {index}",
            source="capacity_compaction",
            category="project",
            tags=["search", "quality"],
            metadata={
                "date": "2020-01-01",
                "memory_layer": "semantic",
                "retention_policy": "compressible",
                "compression_generation": 2,
                "compressed_from": [f"older-{index}"],
            },
        )

    def test_local_compression_stops_at_generation_limit_and_recommends_ai(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 8,
            "memory_active_hard_limit": 10,
            "memory_archive_soft_limit": 100,
            "memory_archive_hard_limit": 120,
            "memory_capacity_warning_ratio": 0.5,
        })
        for index in range(5):
            self._add_repeated_summary(index)

        merge = self.kb.merge_memories(days=0, force=True)
        self.assertEqual(merge["status"], "skipped")
        self.assertEqual(merge["blocked_generation_count"], 5)
        self.assertTrue(merge["all_candidates_at_generation_limit"])
        event = self.kb.get_memory_maintenance_status()["pending_events"][-1]
        self.assertEqual(event["code"], "ai_compression_recommended")
        self.assertTrue(event["details"]["ai_recommended"])
        self.assertEqual(event["token_usage"], 0)

    def test_permanent_memories_are_never_automatically_compacted_or_cold_moved(self):
        self.kb.settings.update({
            "memory_active_soft_limit": 3,
            "memory_active_hard_limit": 4,
            "memory_archive_soft_limit": 100,
            "memory_archive_hard_limit": 120,
            "memory_capacity_auto_compact_enabled": False,
        })
        permanent = [
            self.kb.add_memory(
                f"Permanent user memory {index}",
                source="manual",
                metadata={"memory_layer": "semantic", "retention_policy": "permanent"},
            )
            for index in range(4)
        ]

        result = self.kb.last_maintenance_result
        self.assertEqual(result["event"]["code"], "hard_limit_blocked")
        self.assertTrue(all(not memory.get("is_archived") for memory in permanent))
        self.assertEqual(self.kb.get_memory_maintenance_status()["cold_archive_count"], 0)

    def test_local_compression_preserves_structured_facts_and_topic_boundaries(self):
        records = [
            ("project", "search", "On 2026-08-11 we decided to use 16 sources and not use 4."),
            ("project", "search", "Doctor prefers official sources and plans 5 final citations."),
            ("preference", "voice", "On 2026-08-10 Doctor decided the voice volume should be 60%."),
            ("preference", "voice", "Doctor does not like automatic playback and prefers manual playback."),
        ]
        for index, (category, tag, content) in enumerate(records):
            self.kb.add_memory(
                content,
                source="auto_autonomous",
                category=category,
                tags=[tag],
                metadata={"date": "2020-01-01", "memory_layer": "semantic"},
            )

        result = self.kb.merge_memories(days=1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary_count"], 2)
        active_summaries = [
            memory for memory in self.kb.memories
            if not memory.get("is_archived") and memory.get("compressed_from")
        ]
        self.assertEqual(len(active_summaries), 2)
        self.assertEqual({memory["category"] for memory in active_summaries}, {"project", "preference"})
        for summary in active_summaries:
            self.assertEqual(summary["compression_generation"], 1)
            self.assertTrue(summary.get("structured_facts"))
            self.assertIn("topic_key", summary["compression_scope"])

    def test_preview_is_scoped_expiring_and_token_bounded(self):
        self._add_repeated_summary(1)
        self._add_repeated_summary(2)

        preview = self.kb.build_ai_compression_preview(scope="repeated", limit=10)

        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["candidate_count"], 2)
        self.assertEqual(preview["group_count"], 1)
        self.assertTrue(preview["plan_id"])
        self.assertFalse(preview["token_estimate"]["exact"])
        self.assertGreater(preview["token_estimate"]["input_tokens"], 0)
        self.assertGreaterEqual(
            preview["token_estimate"]["likely_max_tokens"],
            preview["token_estimate"]["estimated_total_tokens"],
        )

    def test_ai_plan_revalidates_sources_before_execution(self):
        first = self._add_repeated_summary(1)
        self._add_repeated_summary(2)
        preview = self.kb.build_ai_compression_preview(scope="repeated", limit=10)
        first["content"] += " changed"

        with self.assertRaisesRegex(ValueError, "changed after preview"):
            self.kb.get_ai_compression_plan(preview["plan_id"])

    def test_ai_output_with_unsupported_number_changes_nothing(self):
        sources = [self._add_repeated_summary(1), self._add_repeated_summary(2)]
        preview = self.kb.build_ai_compression_preview(scope="repeated", limit=10)
        group_id = preview["groups"][0]["group_id"]

        with self.assertRaisesRegex(ValueError, "conservative validation"):
            self.kb.apply_ai_compression_plan(
                preview["plan_id"],
                [{
                    "group_id": group_id,
                    "title": "Search decision",
                    "summary": "The project decided to use 999 official sources for search.",
                    "preserved_facts": {},
                }],
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                model_name="deepseek-chat",
            )
        self.assertTrue(all(not memory.get("is_archived") for memory in sources))

    def test_confirmed_ai_compression_records_actual_usage_and_lineage(self):
        sources = [self._add_repeated_summary(1), self._add_repeated_summary(2)]
        preview = self.kb.build_ai_compression_preview(scope="repeated", limit=10)
        group_id = preview["groups"][0]["group_id"]

        result = self.kb.apply_ai_compression_plan(
            preview["plan_id"],
            [{
                "group_id": group_id,
                "title": "Search source decision",
                "summary": "On 2026-08-11 the project chose 16 candidates for search.",
                "preserved_facts": {"dates": ["2026-08-11"], "numeric_facts": ["16 candidates"]},
            }],
            usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
            model_name="deepseek-chat",
        )

        self.assertEqual(result["status"], "ai_compression_completed")
        self.assertEqual(result["usage"]["total_tokens"], 160)
        self.assertTrue(all(memory.get("is_archived") for memory in sources))
        summary = result["memories"][0]
        self.assertEqual(summary["compression_generation"], 3)
        self.assertEqual(summary["ai_compression_generation"], 1)
        self.assertEqual(summary["source_detail"]["compression_engine"], "ai_deepseek")
        self.assertEqual(summary["source_detail"]["token_usage"], 160)
        self.assertEqual(set(summary["compressed_from"]), {memory["id"] for memory in sources})


if __name__ == "__main__":
    unittest.main()
