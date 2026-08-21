import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.backup_manager import BackupManager
from saya_app.services.chat_runtime import ChatRuntimeManager


class SessionRuntimeTests(unittest.TestCase):
    def test_explicit_sessions_do_not_follow_global_current_file(self):
        with tempfile.TemporaryDirectory() as root:
            manager = MemoryManager(root)
            first = manager.create_session(make_current=True)
            second = manager.create_session(make_current=True)
            manager.save_message("user", "first only", session_id=first)
            manager.save_message("user", "second only", session_id=second)

            self.assertIn("first only", "\n".join(manager.load_session(first, make_current=False)))
            self.assertNotIn("second only", "\n".join(manager.load_session(first, make_current=False)))
            self.assertIn("second only", "\n".join(manager.load_session(second, make_current=False)))

    def test_runtime_uses_requested_session_snapshot_and_distinct_locks(self):
        with tempfile.TemporaryDirectory() as root:
            memory = MemoryManager(root)
            first = memory.create_session()
            second = memory.create_session()
            memory.save_message("user", "alpha", session_id=first)
            memory.save_message("user", "beta", session_id=second)
            runtimes = ChatRuntimeManager()

            first_runtime = runtimes.create(memory, first)
            second_runtime = runtimes.create(memory, second)
            self.assertEqual(first_runtime.session_id, first)
            self.assertIn("alpha", "\n".join(first_runtime.history))
            self.assertNotIn("beta", "\n".join(first_runtime.history))

            async def get_locks():
                return await runtimes.session_lock(first), await runtimes.session_lock(second)

            first_lock, second_lock = asyncio.run(get_locks())
            self.assertIsNot(first_lock, second_lock)


class MemoryRecallAuditTests(unittest.TestCase):
    def test_recall_audit_explains_selection_and_filters_superseded_memory(self):
        with tempfile.TemporaryDirectory() as root:
            kb = KnowledgeBase(root)
            old = kb.add_memory(
                "Doctor prefers the old blue notebook.",
                title="Old notebook preference",
                category="preference",
            )
            new = kb.add_memory(
                "Doctor now prefers the new red notebook.",
                title="Current notebook preference",
                category="preference",
                is_important=True,
            )
            old["conflicts_with"] = [new["id"]]
            old["superseded_by"] = new["id"]
            new["supersedes"] = [old["id"]]
            new["conflicts_with"] = [old["id"]]

            context, audit = kb.build_prompt_context_with_audit("Which notebook do I prefer?")

            self.assertIn("Current notebook preference", context)
            self.assertNotIn("Old notebook preference", context)
            self.assertEqual(audit["status"], "selected")
            self.assertEqual(audit["selected_count"], len(audit["selected"]))
            self.assertGreaterEqual(audit["candidate_summary"]["superseded_filtered"], 1)
            selected = next(item for item in audit["selected"] if item["title"] == "Current notebook preference")
            self.assertEqual(selected["reason"], "protected_priority")
            self.assertIn(selected["conflict"]["status"], {"needs_review", "updates_older"})


class BackupRestoreTests(unittest.TestCase):
    def test_selective_restore_previews_changes_and_creates_safety_backup(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            knowledge = root_path / "knowledge"
            chats = root_path / "chats"
            backups = root_path / "backups"
            knowledge.mkdir()
            chats.mkdir()
            (knowledge / "knowledge.json").write_text(json.dumps({"state": "before"}), encoding="utf-8")
            (knowledge / "memory_graph.json").write_text(json.dumps({"graph": "before"}), encoding="utf-8")
            (knowledge / "youtube_api_key.txt").write_text("secret-must-stay", encoding="utf-8")
            session = chats / "session_test.jsonl"
            session.write_text(json.dumps({"role": "user", "content": "before"}) + "\n", encoding="utf-8")

            manager = BackupManager(knowledge, chats, backups)
            backup = manager.create_backup(["memories", "conversations"])
            (knowledge / "knowledge.json").write_text(json.dumps({"state": "after"}), encoding="utf-8")
            (knowledge / "memory_graph.json").write_text(json.dumps({"graph": "after"}), encoding="utf-8")
            session.write_text(json.dumps({"role": "user", "content": "after"}) + "\n", encoding="utf-8")

            preview = manager.preview_restore(backup["backup_id"], ["memories"])
            self.assertEqual(preview["summary"]["replace"], 2)
            result = manager.restore(
                backup["backup_id"],
                ["memories"],
                preview["confirmation_token"],
            )

            restored = json.loads((knowledge / "knowledge.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["state"], "before")
            restored_graph = json.loads((knowledge / "memory_graph.json").read_text(encoding="utf-8"))
            self.assertEqual(restored_graph["graph"], "before")
            self.assertIn("after", session.read_text(encoding="utf-8"))
            self.assertEqual((knowledge / "youtube_api_key.txt").read_text(encoding="utf-8"), "secret-must-stay")
            self.assertTrue(result["safety_backup_id"].startswith("saya-backup-"))
            self.assertGreaterEqual(len(manager.list_backups()), 2)


if __name__ == "__main__":
    unittest.main()
