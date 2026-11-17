"""Chat-area memory confirmation payload + persistence contracts.

Covers the 2026-09-30 fix: explicit "记住" saves and autonomous candidates
both produce a bounded, credential-free payload that is attached to the AI
message (update_last_message_field) so the confirmation box travels with the
reply instead of floating across chats.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_services import KnowledgeBase, MemoryManager  # noqa: E402


def load_server_module():
    import importlib.util
    import io
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_confirm_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()


class BuildMemoryConfirmPayloadTests(unittest.TestCase):
    def test_versions_bind_full_source_before_bounded_projection(self):
        from saya_app.services.memory_versions import record_version
        original={"id":"large", "title":"original", "content":"C"*1800, "status":"pending"}
        p=SERVER.build_memory_confirm_payload(explicit_memory=original,candidates=[original])
        for item in [p['explicit'][0],p['candidates'][0]]:
            self.assertEqual(item['content_version'],record_version(original))
            self.assertTrue(item['content_truncated'])
            self.assertEqual(len(item['content']),600)
        small={"id":"small","title":"title","content":"full"}
        self.assertFalse(SERVER.build_memory_confirm_payload(explicit_memory=small)['explicit'][0]['content_truncated'])

    def test_explicit_save_produces_keep_delete_payload(self):
        payload = SERVER.build_memory_confirm_payload(
            explicit_memory={"id": "m-1", "title": "Explicit Memory", "content": "Doctor喜欢清淡绿茶。"},
        )
        self.assertEqual(len(payload["explicit"]), 1)
        self.assertEqual(payload["explicit"][0]["memory_id"], "m-1")
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["auto_confirmed"], [])

    def test_candidates_and_auto_confirmed_are_bounded(self):
        payload = SERVER.build_memory_confirm_payload(
            candidates=[{
                "id": "c-1", "title": "T" * 300, "content": "C" * 2000, "category": "preference",
                "tags": ["a"], "confidence": 0.9, "importance_score": 0.8, "occurrence_count": 2,
            }],
            auto_confirmed=[{"id": "c-2", "title": "auto"}],
        )
        self.assertEqual(payload["explicit"], [])
        self.assertEqual(payload["candidates"][0]["title"], "T" * 120)
        self.assertEqual(payload["candidates"][0]["content"], "C" * 600)
        self.assertEqual(payload["candidates"][0]["importance_score"], 0.8)
        self.assertEqual(payload["auto_confirmed"][0]["id"], "c-2")

    def test_explicit_item_carries_importance_for_the_slider(self):
        payload = SERVER.build_memory_confirm_payload(
            explicit_memory={"id": "m-9", "title": "t", "content": "c", "importance_score": 0.66},
        )
        self.assertEqual(payload["explicit"][0]["importance_score"], 0.66)

    def test_nothing_worth_confirming_returns_none(self):
        self.assertIsNone(SERVER.build_memory_confirm_payload())
        self.assertIsNone(SERVER.build_memory_confirm_payload(explicit_memory={"id": ""}))
        self.assertIsNone(SERVER.build_memory_confirm_payload(candidates=[{"no_id": 1}]))

    def test_payload_is_json_serializable_and_secret_free(self):
        payload = SERVER.build_memory_confirm_payload(
            explicit_memory={"id": "m-1", "title": "t", "content": "c", "api_key": "sk-secret"},
        )
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("sk-secret", rendered)


class UpdateLastMessageFieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = MemoryManager(str(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_attaches_field_to_last_ai_message_only(self):
        session_id = self.manager.create_session(make_current=True)
        self.manager.save_message("user", "记住一下：我喜欢喝清淡的绿茶。", session_id=session_id)
        self.manager.save_message("ai", "好的，我记住了。", session_id=session_id)
        ok = self.manager.update_last_message_field(
            session_id, "memory_confirm", {"explicit": [{"memory_id": "m-1"}]}, "ai"
        )
        self.assertTrue(ok)
        entries = [json.loads(line) for line in
                   Path(self.manager._safe_session_path(session_id)).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(entries), 2)
        self.assertNotIn("memory_confirm", entries[0])
        self.assertEqual(entries[1].get("memory_confirm"), {"explicit": [{"memory_id": "m-1"}]})
        self.assertEqual(entries[1].get("role"), "ai")

    def test_role_guard_skips_user_tail(self):
        session_id = self.manager.create_session(make_current=True)
        self.manager.save_message("ai", "reply", session_id=session_id)
        self.manager.save_message("user", "next question", session_id=session_id)
        self.assertFalse(self.manager.update_last_message_field(
            session_id, "memory_confirm", {"x": 1}, "ai"
        ))

    def test_missing_session_returns_false(self):
        self.assertFalse(self.manager.update_last_message_field(
            "no-such-session", "memory_confirm", {"x": 1}, "ai"
        ))


if __name__ == "__main__":
    unittest.main()
