import atexit
from concurrent.futures import ThreadPoolExecutor
import os
import shutil
import sys
import tempfile
import unittest
import json
from datetime import datetime
from pathlib import Path


TEST_ROOT = Path(tempfile.mkdtemp(prefix="saya-memory3-tests-"))
os.environ["PROJECT_ROOT"] = str(TEST_ROOT / "project")
os.environ["DEAR_ROOT"] = str(TEST_ROOT / "dear")
Path(os.environ["PROJECT_ROOT"]).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
atexit.register(lambda: shutil.rmtree(TEST_ROOT, ignore_errors=True))

from core_services import AssistantRoleManager, KnowledgeBase, MemoryManager, ProjectManager  # noqa: E402


class FakeEmbeddingProvider:
    provider_type = "fake"
    key = "fake:semantic-v1"

    def status(self):
        return {
            "ready": True,
            "provider": self.provider_type,
            "provider_key": self.key,
            "model": "deterministic-test-model",
        }

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = str(text).lower()
            vector = [0.0] * 64
            if "安静" in lowered or "sunset" in lowered or "without noise" in lowered:
                vector[3] = 1.0
                vector[11] = 0.25
            elif "ultraviolet" in lowered or "紫外" in lowered:
                vector[7] = 1.0
            else:
                vector[29] = 1.0
            vectors.append(vector)
        return vectors


class Memory3Tests(unittest.TestCase):
    def setUp(self):
        self.base_dir = TEST_ROOT / self._testMethodName
        self.kb = KnowledgeBase(str(self.base_dir))

    def test_layers_separate_core_from_locked_session_summaries(self):
        profile = self.kb.add_memory(
            "Doctor prefers concise but warm replies.",
            source="about_you",
            category="preference",
            metadata={"is_locked": True},
        )
        summary = self.kb.add_memory(
            "A locked summary of one old conversation.",
            source="session_archive",
            category="conversation_summary",
            tags=["archive"],
            metadata={"is_locked": True},
        )
        self.assertEqual(profile["memory_layer"], "core")
        self.assertEqual(summary["memory_layer"], "episodic")
        self.assertFalse(summary["is_archived"])
        self.assertNotIn("archive", summary["tags"])
        self.assertEqual(summary["archive_semantics"], "active_summary_not_archived_record")

    def test_daily_activity_is_one_protected_high_priority_core_memory(self):
        today = datetime.now().strftime("%Y-%m-%d")
        first = self.kb.upsert_daily_activity_memory(
            today,
            {
                "done": 1,
                "total": 2,
                "status": "partial",
                "task_details": [
                    {"title": "Read", "done": True},
                    {"title": "Exercise", "done": False},
                ],
            },
            3,
            {
                "conversation_count": 4,
                "user_message_count": 4,
                "assistant_message_count": 4,
                "session_count": 2,
            },
        )
        second = self.kb.upsert_daily_activity_memory(
            today,
            {"done": 2, "total": 2, "status": "completed"},
            4,
            {"conversation_count": 5},
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["memory_layer"], "core")
        self.assertEqual(second["memory_kind"], "daily_activity")
        self.assertEqual(second["managed_slot"], "daily_activity:today")
        self.assertTrue(second["is_locked"])
        self.assertTrue(second["is_important"])
        self.assertIn("5", second["content"])
        self.assertIn("[System: Today's Activity Memory]", self.kb.build_daily_activity_prompt_context())
        self.assertEqual(
            sum(memory.get("managed_slot") == "daily_activity:today" for memory in self.kb.memories),
            1,
        )

    def test_anki_checkin_uses_one_fixed_updating_memory(self):
        state = {
            "profile": "User 1",
            "connected": True,
            "last_refreshed_at": "2026-08-14T18:00:00+08:00",
            "source_modified_at": "2026-08-14T17:59:00+08:00",
            "summary": {
                "daily_task_completed": False,
                "completed_decks_today": [],
                "today_reviews": 3,
                "today_cards": 3,
                "today_time_ms": 60000,
                "today_accuracy": 66.7,
                "current_streak": 2,
                "longest_streak": 8,
            },
            "daily_activity": [{"date": "2026-08-14", "reviews": 3}],
        }
        first = self.kb.upsert_anki_checkin_memory(state)
        unchanged_timestamp = first["last_updated"]
        unchanged = self.kb.upsert_anki_checkin_memory(state)
        self.assertEqual(unchanged["last_updated"], unchanged_timestamp)
        state["summary"].update({
            "daily_task_completed": True,
            "completed_decks_today": ["Japanese"],
            "today_reviews": 20,
        })
        second = self.kb.upsert_anki_checkin_memory(state)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["managed_slot"], "anki:checkin_status")
        self.assertEqual(second["memory_layer"], "core")
        self.assertTrue(second["is_locked"])
        self.assertTrue(second["anki_checkin"]["completed"])
        self.assertIn("Japanese", second["content"])
        self.assertEqual(
            sum(memory.get("managed_slot") == "anki:checkin_status" for memory in self.kb.memories),
            1,
        )

    def test_daily_conversation_stats_count_completed_pairs(self):
        manager = MemoryManager(str(self.base_dir / "daily-sessions"))
        manager.save_message("user", "First question")
        manager.save_message("ai", "First answer")
        manager.save_message("user", "Unanswered question")
        stats = manager.get_daily_conversation_stats()
        self.assertEqual(stats["conversation_count"], 1)
        self.assertEqual(stats["user_message_count"], 2)
        self.assertEqual(stats["assistant_message_count"], 1)
        self.assertEqual(stats["session_count"], 1)

    def test_archive_exchange_picker_uses_completed_qa_pairs_and_ignores_system_messages(self):
        manager = MemoryManager(str(self.base_dir / "sessions"))
        exchanges = manager.build_archive_exchanges([
            {"role": "system", "content": "Internal instruction"},
            {"role": "user", "content": "First question"},
            {"role": "ai", "content": "First answer"},
            {"role": "user", "content": "Part one"},
            {"role": "user", "content": "Part two"},
            {"role": "assistant", "content": "Combined answer"},
            {"role": "user", "content": "Unanswered question"},
        ])
        self.assertEqual([item["id"] for item in exchanges], ["qa-1-2", "qa-3-5"])
        self.assertEqual(exchanges[1]["question"], "Part one\nPart two")
        self.assertNotIn("Internal instruction", json.dumps(exchanges))
        self.assertNotIn("Unanswered question", json.dumps(exchanges))

    def test_life_quests_summary_is_a_protected_managed_core_memory(self):
        memory = self.kb.add_memory(
            "=== Life Quests Summary ===\n\n[Current Goals]\n- Finish the paper",
            title="Life Quests Summary",
            source="system",
            category="conversation_summary",
        )
        self.assertEqual(memory["managed_slot"], "life_quests:summary")
        self.assertEqual(memory["memory_layer"], "core")
        self.assertEqual(memory["memory_kind"], "goal_summary")
        self.assertEqual(memory["category"], "goal")
        self.assertTrue(memory["is_locked"])
        self.assertFalse(self.kb.delete_memory(memory["id"]))
        original_content = memory["content"]
        self.assertTrue(self.kb.update_memory(memory["id"], {"content": "tampered"}))
        self.assertEqual(self.kb._find_memory(memory["id"])["content"], original_content)

    def test_obsidian_learning_map_stays_one_broad_memory_until_curated(self):
        manager_dir = self.base_dir / "assistant-role"
        manager = AssistantRoleManager(str(manager_dir))
        manager.state.update({
            "chapter_summary": [
                {"name": "RL", "note_count": 2, "titles": ["Bandits", "Returns"], "headings": ["Value update"]},
            ],
            "memories": [
                {
                    "id": "note-a",
                    "title": "Bandits",
                    "file": "RL/Bandits.md",
                    "folder": "RL",
                    "top_group": "RL",
                    "headings": ["# Bandits"],
                    "tags": ["rl"],
                    "content": "File: RL/Bandits.md\nTitle: Bandits\nHeadings: # Bandits",
                },
                {
                    "id": "note-b",
                    "title": "Returns",
                    "file": "RL/Returns.md",
                    "folder": "RL",
                    "top_group": "RL",
                    "headings": ["# Returns"],
                    "tags": ["rl"],
                    "content": "File: RL/Returns.md\nTitle: Returns\nHeadings: # Returns",
                },
            ],
        })
        result = manager.update_normal_mode_memory(self.kb)
        index = self.kb._find_memory(manager.SUMMARY_MEMORY_ID)
        self.assertEqual(index["managed_slot"], "assistant_role:learning_map:index")
        self.assertEqual(index["memory_layer"], "semantic")
        self.assertIn("Bandits", index["content"])
        self.assertFalse(any(memory.get("memory_kind") == "knowledge_area" for memory in self.kb.memories))
        self.assertFalse(any(memory.get("memory_kind") == "knowledge_note" for memory in self.kb.memories))
        self.assertEqual(result["archived_split_count"], 0)

    def test_curated_obsidian_chapter_is_bounded_and_removable(self):
        manager_dir = self.base_dir / "assistant-curation"
        vault = manager_dir / "vault"
        note_path = vault / "RL" / "Returns.md"
        note_path.parent.mkdir(parents=True)
        note_path.write_text(
            "# Returns\nIntro text.\n## Definition\nReturn is cumulative reward.\n"
            "## Examples\nExample details.\n# Other topic\nNot part of Returns.",
            encoding="utf-8",
        )
        manager = AssistantRoleManager(str(manager_dir))
        outline = manager.get_note_outline(str(vault), "RL/Returns.md")
        definition = next(item for item in outline["chapters"] if item["title"] == "Definition")
        prepared = manager.prepare_note_extraction(
            str(vault),
            "RL/Returns.md",
            definition["selector"],
        )
        self.assertIn("Return is cumulative reward", prepared["source_text"])
        self.assertNotIn("Example details", prepared["source_text"])

        self.kb.update_settings({"memory_max_chars_knowledge_simple": 400})
        saved = manager.save_curated_extraction(
            self.kb,
            prepared,
            "A" * 650,
            mode="simple",
            model_name="test-model",
        )
        memory = saved["memory"]
        self.assertEqual(memory["memory_kind"], "knowledge_extract_simple")
        self.assertEqual(memory["memory_layer"], "semantic")
        self.assertTrue(memory["is_important"])
        self.assertEqual(self.kb._memory_tier(memory), "curated")
        self.assertGreaterEqual(memory["importance_score"], 0.8)
        self.assertLessEqual(len(memory["content"]), 400)
        self.assertTrue(memory["content_truncated"])
        self.assertFalse(self.kb.delete_memory(memory["id"]))
        removed = manager.remove_curated_extraction(self.kb, memory["id"])
        self.assertTrue(removed["removed"])
        self.assertTrue(self.kb._find_memory(memory["id"])["is_archived"])

    def test_learning_map_refresh_does_not_create_new_archives_or_touch_curated_memory(self):
        manager = AssistantRoleManager(str(self.base_dir / "assistant-migration"))
        manager.state.update({
            "chapter_summary": [{"name": "RL", "note_count": 1, "titles": ["Bandits"]}],
            "memories": [{"id": "note-a", "title": "Bandits", "file": "RL/Bandits.md"}],
        })
        self.kb.add_memory(
            "Old area index",
            title="Obsidian Area · RL",
            source="assistant_role",
            source_detail={"type": "assistant_role_learning_area"},
            metadata={
                "id": manager.AREA_MEMORY_PREFIX + "legacy",
                "memory_layer": "semantic",
                "memory_kind": "knowledge_area",
            },
        )
        self.kb.add_memory(
            "Old automatic note outline",
            title="Obsidian Note · Bandits",
            source="obsidian",
            source_detail={"type": "assistant_role_learning_note"},
            metadata={
                "id": manager.NOTE_MEMORY_PREFIX + "legacy",
                "memory_layer": "semantic",
                "memory_kind": "knowledge_note",
            },
        )
        curated = self.kb.add_memory(
            "A deliberately curated concept",
            title="Obsidian · Bandits",
            source="obsidian",
            source_detail={"type": "assistant_role_curated_extraction"},
            metadata={
                "id": manager.CURATED_MEMORY_PREFIX + "kept",
                "managed_slot": "assistant_role:curated:kept",
                "memory_layer": "semantic",
                "memory_kind": "knowledge_extract_simple",
            },
        )
        before_ids = {memory["id"] for memory in self.kb.memories}
        archived_before = {memory["id"] for memory in self.kb.memories if memory.get("is_archived")}

        result = manager.update_normal_mode_memory(self.kb)

        after_ids = {memory["id"] for memory in self.kb.memories}
        self.assertTrue(before_ids.issubset(after_ids))
        archived_after = {memory["id"] for memory in self.kb.memories if memory.get("is_archived")}
        self.assertEqual(result["archived_split_count"], 0)
        self.assertEqual(archived_before, archived_after)
        self.assertFalse(self.kb._find_memory(manager.AREA_MEMORY_PREFIX + "legacy").get("is_archived", False))
        self.assertFalse(self.kb._find_memory(manager.NOTE_MEMORY_PREFIX + "legacy").get("is_archived", False))
        self.assertFalse(self.kb._find_memory(curated["id"]).get("is_archived", False))

    def test_bulk_delete_respects_protection_and_persists_tombstones(self):
        deletable_a = self.kb.add_memory("Temporary semantic memory A", source="manual", category="general")
        deletable_b = self.kb.add_memory("Temporary semantic memory B", source="manual", category="general")
        protected = self.kb.add_memory(
            "Protected managed memory",
            source="assistant_role",
            metadata={
                "id": "protected-bulk-memory",
                "managed_slot": "assistant_role:learning_map:index",
                "memory_layer": "semantic",
            },
        )

        result = self.kb.delete_memories([deletable_a["id"], protected["id"], deletable_b["id"]])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual({item["id"] for item in result["blocked"]}, {protected["id"]})
        self.assertIsNotNone(self.kb._find_memory(protected["id"]))
        self.assertIsNone(self.kb._find_memory(deletable_a["id"]))
        self.assertIsNone(self.kb._find_memory(deletable_b["id"]))

        reloaded = KnowledgeBase(str(self.base_dir))
        reloaded_ids = {memory["id"] for memory in reloaded.memories}
        self.assertNotIn(deletable_a["id"], reloaded_ids)
        self.assertNotIn(deletable_b["id"], reloaded_ids)
        self.assertIn(protected["id"], reloaded_ids)

    def test_atomic_cascade_does_not_partially_delete_when_one_memory_is_protected(self):
        deletable = self.kb.add_memory("Delete only if the whole cascade is safe")
        protected = self.kb.add_memory(
            "Protected linked memory", metadata={"is_locked": True},
        )
        preview = self.kb.preview_memory_deletion(
            [deletable["id"], protected["id"]], reason="session_delete",
        )
        result = self.kb.delete_memories(
            [deletable["id"], protected["id"]], reason="session_delete",
            confirmation_token=preview["confirmation_token"], require_all=True,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIsNotNone(self.kb._find_memory(deletable["id"]))
        self.assertIsNotNone(self.kb._find_memory(protected["id"]))

    def test_memory_search_can_filter_by_layer(self):
        self.kb.add_memory("Stable profile fact", source="about_you", category="identity")
        semantic = self.kb.add_memory("A durable project fact", source="manual", category="project")
        self.kb.add_memory("An old session event", source="session_archive", category="conversation_summary")
        results = self.kb.search_memories(memory_layer="semantic", limit=20)
        self.assertEqual({item["memory_layer"] for item in results}, {"semantic"})
        self.assertIn(semantic["id"], {item["id"] for item in results})

    def test_retrieval_order_and_prompt_budget(self):
        self.kb.update_settings({
            "memory_core_limit": 4,
            "relevant_memory_count": 4,
            "memory_prompt_budget_tokens": 700,
            "memory_prompt_budget_ratio": 0.5,
            "memory_item_budget_tokens": 180,
        })
        self.kb.add_memory(
            "Doctor's stable communication preference is warm and direct.",
            source="about_you",
            category="preference",
        )
        self.kb.add_memory(
            "Doctor's telescope project uses a red status indicator.",
            source="manual",
            category="project",
            metadata={"memory_layer": "semantic"},
        )
        self.kb.add_memory(
            "During an old telescope discussion, the red status indicator was mentioned.",
            source="session_archive",
            category="conversation_summary",
        )
        selected = self.kb.get_relevant_memories(
            "What color is the telescope project status indicator?",
            mark_accessed=False,
        )
        self.assertEqual(selected[0]["_layer"], "core")
        selected_layers = [item["_layer"] for item in selected]
        self.assertLess(selected_layers.index("semantic"), selected_layers.index("episodic"))

        context = self.kb.build_prompt_context(
            "What color is the telescope project status indicator?",
            context_window_tokens=8192,
        )
        self.assertIn("[System: Memory 2.0]", context)
        self.assertLessEqual(self.kb.estimate_token_count(context), 740)

    def test_compression_is_bounded_and_never_crosses_projects(self):
        self.kb.update_settings({
            "memory_compression_min_group": 2,
            "memory_compression_group_size": 10,
            "memory_compression_summary_chars": 500,
        })
        old_date = "2020-01-05"
        global_ids = []
        project_ids = []
        for index in range(2):
            memory = self.kb.add_memory(
                f"Global preference event {index} with durable detail.",
                source="auto_autonomous",
                category="preference",
                metadata={"date": old_date, "memory_layer": "semantic"},
            )
            global_ids.append(memory["id"])
        for index in range(2):
            memory = self.kb.add_memory(
                f"Project-only preference event {index} with durable detail.",
                source="auto_autonomous",
                category="preference",
                metadata={
                    "date": old_date,
                    "memory_layer": "semantic",
                    "project_id": "project-a",
                    "project_name": "Project A",
                    "project_scoped": True,
                },
            )
            project_ids.append(memory["id"])

        result = self.kb.merge_memories(days=1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary_count"], 2)
        summaries = result["memories"]
        self.assertEqual({str(item.get("project_id") or "") for item in summaries}, {"", "project-a"})
        self.assertTrue(all(len(item["content"]) <= 501 for item in summaries))
        for memory_id in global_ids + project_ids:
            original = self.kb._find_memory(memory_id)
            self.assertTrue(original["is_archived"])
            self.assertEqual(original["memory_layer"], "archive")
            self.assertTrue(original.get("parent_memory_id"))

    def test_settings_are_clamped(self):
        settings = self.kb.update_settings({
            "api_context_window_tokens": 99999999,
            "memory_prompt_budget_ratio": 9,
            "memory_core_limit": -20,
            "memory_retrieval_threshold": -1,
        })
        self.assertEqual(settings["api_context_window_tokens"], 2000000)
        self.assertEqual(settings["memory_prompt_budget_ratio"], 0.5)
        self.assertEqual(settings["memory_core_limit"], 0)
        self.assertEqual(settings["memory_retrieval_threshold"], 0.0)

    def test_sqlite_migration_preserves_json_ids_and_creates_backup(self):
        migration_dir = TEST_ROOT / f"{self._testMethodName}-migration"
        migration_dir.mkdir(parents=True, exist_ok=True)
        legacy_memories = [
            {
                "id": "legacy-a",
                "creation_time": "2024-01-01 10:00:00",
                "date": "2024-01-01",
                "title": "Legacy preference",
                "content": "Doctor prefers jasmine tea.",
                "source": "manual",
                "is_locked": False,
                "is_important": True,
            },
            {
                "id": "legacy-b",
                "creation_time": "2024-01-02 10:00:00",
                "date": "2024-01-02",
                "title": "Legacy session",
                "content": "An old session summary about a museum visit.",
                "source": "session_archive",
                "is_locked": True,
                "is_important": False,
            },
        ]
        knowledge_path = migration_dir / "knowledge.json"
        knowledge_path.write_text(json.dumps(legacy_memories, ensure_ascii=False), encoding="utf-8")

        migrated = KnowledgeBase(str(migration_dir))
        self.assertEqual({item["id"] for item in migrated.memories}, {"legacy-a", "legacy-b"})
        status = migrated.get_sqlite_store_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["memory_count"], 2)
        self.assertEqual({item["id"] for item in migrated.sqlite_store.load_active_memories()}, {"legacy-a", "legacy-b"})
        migration_backups = list((migration_dir / "backups").glob("knowledge.pre-sqlite-migration.*.json"))
        self.assertTrue(migration_backups)

    def test_sqlite_tombstone_prevents_deleted_memory_from_returning(self):
        memory = self.kb.add_memory(
            "A temporary memory that will be explicitly deleted.",
            source="manual",
            category="general",
        )
        self.assertTrue(self.kb.delete_memory(memory["id"]))
        reloaded = KnowledgeBase(str(self.base_dir))
        self.assertNotIn(memory["id"], {item.get("id") for item in reloaded.memories})
        self.assertNotIn(memory["id"], {item.get("id") for item in reloaded.sqlite_store.load_active_memories()})

    def test_confirmed_delete_purges_sqlite_payload_and_keeps_tombstone(self):
        memory = self.kb.add_memory(
            "Sensitive temporary payload for confirmed deletion.",
            source="manual",
        )
        preview = self.kb.preview_memory_deletion([memory["id"]], reason="user_delete")
        result = self.kb.delete_memories(
            [memory["id"]], reason="user_delete",
            confirmation_token=preview["confirmation_token"],
        )
        self.assertEqual(result["status"], "success")
        with self.kb.sqlite_store._connection() as connection:
            payload_count = connection.execute(
                "SELECT COUNT(*) FROM memories WHERE id=?", (memory["id"],),
            ).fetchone()[0]
            tombstone_count = connection.execute(
                "SELECT COUNT(*) FROM memory_tombstones WHERE id=?", (memory["id"],),
            ).fetchone()[0]
        self.assertEqual(payload_count, 0)
        self.assertEqual(tombstone_count, 1)

    def test_delete_preview_reconnects_a_removed_supersession_chain(self):
        newest = self.kb.add_memory("Newest", title="Newest")
        middle_new = self.kb.add_memory("Middle new", title="Middle new")
        middle_old = self.kb.add_memory("Middle old", title="Middle old")
        oldest = self.kb.add_memory("Oldest", title="Oldest")
        self.assertTrue(self.kb.update_memory(middle_old["id"], {"supersedes": [oldest["id"]]}))
        self.assertTrue(self.kb.update_memory(middle_new["id"], {"supersedes": [middle_old["id"]]}))
        self.assertTrue(self.kb.update_memory(newest["id"], {"supersedes": [middle_new["id"]]}))

        preview = self.kb.preview_memory_deletion(
            [middle_new["id"], middle_old["id"]], reason="user_bulk_delete",
        )
        self.assertEqual(len(preview["rewired_links"]), 1)
        result = self.kb.delete_memories(
            [middle_new["id"], middle_old["id"]], reason="user_bulk_delete",
            confirmation_token=preview["confirmation_token"],
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.kb._find_memory(newest["id"])["supersedes"], [oldest["id"]])
        self.assertEqual(self.kb._find_memory(oldest["id"])["superseded_by"], newest["id"])

    def test_delete_preview_warns_about_manual_relationship_removal(self):
        first = self.kb.add_memory("First", title="First")
        second = self.kb.add_memory("Second", title="Second")
        self.kb.add_memory_graph_edge(first["id"], second["id"], relation="supports")
        preview = self.kb.preview_memory_deletion([first["id"]], reason="user_delete")
        self.assertEqual(preview["removed_manual_edge_count"], 1)
        self.assertEqual(preview["removed_manual_edges"][0]["relation"], "supports")

    def test_relationship_change_invalidates_an_old_delete_confirmation(self):
        first = self.kb.add_memory("First", title="First")
        second = self.kb.add_memory("Second", title="Second")
        preview = self.kb.preview_memory_deletion([first["id"]], reason="user_delete")
        self.kb.add_memory_graph_edge(first["id"], second["id"], relation="supports")
        result = self.kb.delete_memories(
            [first["id"]], reason="user_delete",
            confirmation_token=preview["confirmation_token"],
        )
        self.assertEqual(result["status"], "confirmation_required")
        self.assertIsNotNone(self.kb._find_memory(first["id"]))
        self.assertEqual(result["removed_manual_edge_count"], 1)

    def test_supersession_cycle_is_rejected_without_partial_mutation(self):
        newer = self.kb.add_memory("Newer", title="Newer")
        older = self.kb.add_memory("Older", title="Older")
        self.assertTrue(self.kb.update_memory(newer["id"], {"supersedes": [older["id"]]}))
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.kb.update_memory(older["id"], {"supersedes": [newer["id"]]})
        self.assertEqual(self.kb._find_memory(newer["id"])["supersedes"], [older["id"]])
        self.assertEqual(self.kb._find_memory(older["id"])["superseded_by"], newer["id"])

    def test_reparenting_supersession_removes_the_previous_newer_link(self):
        first_newer = self.kb.add_memory("First newer", title="First newer")
        second_newer = self.kb.add_memory("Second newer", title="Second newer")
        older = self.kb.add_memory("Older", title="Older")
        self.assertTrue(self.kb.update_memory(first_newer["id"], {"supersedes": [older["id"]]}))
        self.assertTrue(self.kb.update_memory(second_newer["id"], {"supersedes": [older["id"]]}))
        self.assertEqual(self.kb._find_memory(first_newer["id"])["supersedes"], [])
        self.assertEqual(self.kb._find_memory(second_newer["id"])["supersedes"], [older["id"]])
        self.assertEqual(self.kb._find_memory(older["id"])["superseded_by"], second_newer["id"])

    def test_concurrent_memory_creation_uses_unique_ids(self):
        def create(index):
            return self.kb.add_memory(
                f"Concurrent memory {index}", run_capacity_check=False,
            )["id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            memory_ids = list(executor.map(create, range(24)))
        self.assertEqual(len(memory_ids), len(set(memory_ids)))
        self.assertTrue(set(memory_ids).issubset({item["id"] for item in self.kb.memories}))

    def test_session_delete_confirmation_detects_changed_content(self):
        manager = MemoryManager(str(self.base_dir / "session-delete-change"))
        session_id = manager.create_session()
        manager.save_message("user", "Before preview", session_id=session_id)
        preview = manager.preview_session_deletion(session_id)
        manager.save_message("ai", "Changed after preview", session_id=session_id)
        with self.assertRaisesRegex(ValueError, "changed"):
            manager.stage_session_deletion(session_id, preview["confirmation_token"])
        self.assertTrue(os.path.exists(manager._safe_session_path(session_id)))

    def test_interrupted_staged_session_delete_is_recovered_on_startup(self):
        root = self.base_dir / "session-delete-recovery"
        manager = MemoryManager(str(root))
        session_id = manager.create_session()
        manager.save_message("user", "Recover me", session_id=session_id)
        preview = manager.preview_session_deletion(session_id)
        manager.stage_session_deletion(session_id, preview["confirmation_token"])
        self.assertFalse(os.path.exists(manager._safe_session_path(session_id)))

        recovered = MemoryManager(str(root))
        self.assertTrue(os.path.exists(recovered._safe_session_path(session_id)))
        self.assertEqual(recovered.load_session(session_id, make_current=False), ["Doctor: Recover me"])

    def test_project_delete_confirmation_detects_session_or_membership_change(self):
        root = self.base_dir / "project-delete-change"
        manager = MemoryManager(str(root / "sessions"))
        projects = ProjectManager(str(root / "knowledge"))
        project = projects.add_project("Audit project")
        session_id = manager.create_session()
        manager.save_message("user", "Initial content", session_id=session_id)
        projects.add_session(project["id"], session_id)
        preview = projects.preview_project_deletion(project["id"], manager)
        manager.save_message("ai", "Changed after preview", session_id=session_id)
        with self.assertRaisesRegex(ValueError, "changed"):
            projects.consume_project_deletion_plan(
                project["id"], preview["confirmation_token"], manager,
            )
        self.assertIsNotNone(projects.get_project(project["id"]))

    def test_file_deletion_ledger_blocks_stale_json_resurrection_without_sqlite(self):
        memory = self.kb.add_memory(
            "A deleted memory that still exists in an old JSON backup.",
            source="manual",
            category="general",
        )
        self.assertTrue(self.kb.delete_memory(memory["id"]))
        deletion_ledger = self.base_dir / "memory_deletions.json"
        self.assertTrue(deletion_ledger.exists())

        (self.base_dir / "knowledge.json").write_text(
            json.dumps([memory], ensure_ascii=False),
            encoding="utf-8",
        )
        sqlite_path = self.base_dir / "memory3.sqlite3"
        if sqlite_path.exists():
            sqlite_path.unlink()

        reloaded = KnowledgeBase(str(self.base_dir))
        self.assertNotIn(memory["id"], {item.get("id") for item in reloaded.memories})

    def test_only_retired_assistant_managed_archive_can_be_permanently_deleted(self):
        active_managed = self.kb.add_memory(
            "Active managed index",
            source="assistant_role",
            metadata={
                "id": "managed-active",
                "managed_slot": "assistant_role:learning_map:index",
                "memory_layer": "semantic",
            },
        )
        retired_managed = self.kb.add_memory(
            "Retired automatic note outline",
            source="obsidian",
            metadata={
                "id": "managed-retired",
                "managed_slot": "assistant_role:learning_note:legacy",
                "memory_layer": "archive",
                "is_archived": True,
            },
        )
        locked_archive = self.kb.add_memory(
            "A user-locked archived record",
            source="manual",
            metadata={
                "id": "locked-archive",
                "memory_layer": "archive",
                "is_archived": True,
                "is_locked": True,
            },
        )

        self.assertFalse(self.kb.delete_memory(active_managed["id"]))
        self.assertTrue(self.kb.delete_memory(retired_managed["id"]))
        self.assertFalse(self.kb.delete_memory(locked_archive["id"]))

    def test_fts_reduces_expensive_scoring_and_finds_target(self):
        self.kb.settings["memory_candidate_pool_size"] = 30
        for index in range(180):
            memory = {
                "id": f"noise-{index}",
                "creation_time": "2025-01-01 10:00:00",
                "date": "2025-01-01",
                "title": f"Unrelated record {index}",
                "content": f"Routine grocery inventory number {index} containing apples and rice.",
                "source": "auto_autonomous",
                "is_locked": False,
                "is_important": False,
                "category": "general",
            }
            normalized, _ = self.kb.normalize_memory(memory)
            self.kb.memories.append(normalized)
        target, _ = self.kb.normalize_memory({
            "id": "target-nebula",
            "creation_time": "2025-01-02 10:00:00",
            "date": "2025-01-02",
            "title": "Nebula project color",
            "content": "The Orion nebula project status indicator is ultraviolet.",
            "source": "manual",
            "is_locked": False,
            "is_important": False,
            "category": "project",
        })
        self.kb.memories.append(target)
        self.kb.save_memories()

        original_semantic_score = self.kb.semantic_memory_score
        calls = {"count": 0}

        def counted_score(memory, query):
            calls["count"] += 1
            return original_semantic_score(memory, query)

        self.kb.semantic_memory_score = counted_score
        selected = self.kb.get_relevant_memories(
            "What color is the Orion nebula project status indicator?",
            mark_accessed=False,
        )
        self.assertIn("target-nebula", {item.get("id") for item in selected})
        self.assertLess(calls["count"], 80)

    def test_rebuild_index_does_not_change_json_export(self):
        self.kb.add_memory("Persistent rebuild check.", source="manual", category="general")
        before = Path(self.kb.file_path).read_bytes()
        result = self.kb.rebuild_sqlite_store()
        after = Path(self.kb.file_path).read_bytes()
        self.assertEqual(result["status"], "success")
        self.assertEqual(before, after)

    def test_chinese_trigram_fts_finds_specific_memory(self):
        memories = []
        for index in range(120):
            memory, _ = self.kb.normalize_memory({
                "id": f"cn-noise-{index}",
                "creation_time": "2025-01-01 10:00:00",
                "date": "2025-01-01",
                "title": f"日常购物记录 {index}",
                "content": f"这是第 {index} 条普通生活记录，包含购物、天气和日程安排。",
                "source": "auto_autonomous",
                "category": "general",
                "is_locked": False,
                "is_important": False,
            })
            memories.append(memory)
        target, _ = self.kb.normalize_memory({
            "id": "cn-target",
            "creation_time": "2025-01-02 10:00:00",
            "date": "2025-01-02",
            "title": "猎户座星云项目",
            "content": "猎户座星云项目的状态指示灯颜色是紫外色。",
            "source": "manual",
            "category": "project",
            "is_locked": False,
            "is_important": False,
        })
        memories.append(target)
        self.kb.memories.extend(memories)
        self.kb.save_memories()
        candidate_ids = self.kb.sqlite_store.search_candidate_ids(
            "猎户座星云项目的指示灯是什么颜色？",
            limit=40,
        )
        self.assertIn("cn-target", candidate_ids)

    def test_semantic_embedding_recalls_without_shared_keywords(self):
        self.kb.settings.update({
            "embedding_enabled": True,
            "embedding_min_similarity": 0.5,
            "embedding_candidate_count": 20,
        })
        self.kb.embedding_provider = FakeEmbeddingProvider()
        target = self.kb.add_memory(
            "用户偏爱安静的夜间工作环境。",
            title="专注环境",
            source="manual",
            category="preference",
            metadata={"memory_layer": "semantic"},
        )
        self.kb.add_memory(
            "The weekly grocery list contains apples and rice.",
            title="Grocery list",
            source="manual",
            category="general",
            metadata={"memory_layer": "semantic"},
        )
        result = self.kb.build_embedding_index(force=False, limit=100)
        self.assertEqual(result["status"], "success")
        selected = self.kb.get_relevant_memories(
            "Doctor concentrates best after sunset without noise.",
            mark_accessed=False,
        )
        selected_by_id = {item.get("id"): item for item in selected}
        self.assertIn(target["id"], selected_by_id)
        self.assertGreater(selected_by_id[target["id"]].get("_embedding_score", 0), 0.9)

    def test_embedding_index_is_incremental_after_content_change(self):
        self.kb.settings["embedding_enabled"] = True
        self.kb.embedding_provider = FakeEmbeddingProvider()
        memory = self.kb.add_memory(
            "Initial ordinary note.",
            title="Incremental note",
            source="manual",
            category="general",
        )
        first = self.kb.build_embedding_index(force=False, limit=100)
        second = self.kb.build_embedding_index(force=False, limit=100)
        self.assertEqual(first["indexed_count"], 1)
        self.assertEqual(second["indexed_count"], 0)
        self.assertTrue(self.kb.update_memory(memory["id"], {"content": "Updated note about ultraviolet light."}))
        third = self.kb.build_embedding_index(force=False, limit=100)
        self.assertEqual(third["indexed_count"], 1)

    def test_missing_local_embedding_dependency_keeps_fts_available(self):
        self.kb.settings.update({
            "embedding_enabled": True,
            "embedding_provider": "sentence_transformers",
            "embedding_model": "BAAI/bge-m3",
        })
        init_status = self.kb.initialize_embedding_provider()
        self.assertEqual(init_status["status"], "dependency_missing")
        self.assertTrue(self.kb.sqlite_store.available)
        memory = self.kb.add_memory(
            "Fallback retrieval marker sapphire-lantern.",
            title="Fallback marker",
            source="manual",
            category="general",
        )
        selected = self.kb.get_relevant_memories("sapphire-lantern", mark_accessed=False)
        self.assertIn(memory["id"], {item.get("id") for item in selected})


if __name__ == "__main__":
    unittest.main()
