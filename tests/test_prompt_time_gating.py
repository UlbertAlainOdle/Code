"""Prompt time-context gating tests (V4 plan sections 3.4 / 13.4 / 19-phase-0).

Timeless turns must not receive the per-turn timestamp, the daily-activity
block, or (by construction of the shared predicate) the API user-message
time prefix. Time-intent turns still get precise time.
"""

import io
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_server_module():
    """Import server5-22B----.py, bypassing its interactive model prompt."""
    import importlib.util

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")  # API mode: no local GGUF load
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_time_gating_under_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()

TIMELESS_QUERIES = [
    "帮我解释一下快速排序的时间复杂度",   # "时间" inside a concept, not clock intent
    "继续重构那个解析器吧",
    "用英文翻译这句话：生活愉快",
    "写一个防抖函数",
    "tell me a fun fact about octopuses",
]
TIME_INTENT_QUERIES = [
    "今天天气怎么样？",
    "现在几点了？",
    "明天记得提醒我去取快递",
    "这个月的截止日期是哪天？",
    "what's the weather forecast for tomorrow?",
]


class NeedsTimeContextTests(unittest.TestCase):
    def test_timeless_queries_false(self):
        for query in TIMELESS_QUERIES:
            self.assertFalse(SERVER.needs_time_context(query), query)

    def test_time_intent_queries_true(self):
        for query in TIME_INTENT_QUERIES:
            self.assertTrue(SERVER.needs_time_context(query), query)

    def test_empty_is_timeless(self):
        self.assertFalse(SERVER.needs_time_context(""))
        self.assertFalse(SERVER.needs_time_context(None))


class BuildPromptTimeGatingTests(unittest.TestCase):
    """Snapshot-style assertions on the local-model prompt branches."""

    def setUp(self):
        self._saved_model_name = SERVER.current_loaded_model_name
        SERVER.current_loaded_model_name = "mistral_22b_q5"
        self._saved_history = list(SERVER.history)
        SERVER.history = []

    def tearDown(self):
        SERVER.current_loaded_model_name = self._saved_model_name
        SERVER.history = self._saved_history

    def _prompt(self, user):
        return SERVER.build_prompt(
            user,
            "",
            mode="normal",
            checkin_context="",
            project_context="",
            project_id=None,
            style_settings={},
            role_mode="",
            context_window_tokens=8192,
            history_context=[],
            memory_context_override="",
        )

    def test_timeless_prompt_has_no_current_time(self):
        for query in TIMELESS_QUERIES:
            prompt = self._prompt(query)
            self.assertNotIn("Current time", prompt, query)
            self.assertNotIn("Current time is", prompt, query)

    def test_time_intent_prompt_contains_current_time(self):
        prompt = self._prompt("今天天气怎么样？")
        self.assertIn("Current time", prompt)

    def test_include_time_override_forces_time(self):
        prompt = self._prompt("帮我写一个排序函数")  # timeless text
        forced = SERVER.build_prompt(
            "帮我写一个排序函数",
            "",
            mode="normal",
            style_settings={},
            history_context=[],
            memory_context_override="",
            include_time=True,  # e.g. first-contact daily brief
        )
        self.assertNotIn("Current time", prompt)
        self.assertIn("Current time", forced)

    def test_user_message_text_unchanged(self):
        user = "帮我解释一下快速排序的时间复杂度"
        prompt = self._prompt(user)
        self.assertIn(f"Doctor: {user}", prompt)

    def test_daily_activity_block_gated_by_time_intent(self):
        # skip_memory branch (auto recall off-trigger): the daily snapshot is
        # itself time context and must not enter timeless prompts. The
        # kb_system singleton is only read here.
        def prompt_with(user):
            return SERVER.build_prompt(
                user,
                "",
                mode="normal",
                style_settings={},
                history_context=[],
                skip_memory=True,
                include_daily_activity=True,
            )

        self.assertNotIn("Today's Activity Memory", prompt_with("帮我写一个排序函数"))
        # A time-intent turn (or first-contact brief via include_time=True)
        # is the legitimate place for the daily snapshot.
        self.assertIn("Current time", prompt_with("今天我做了什么？"))


class DailyActivityGatingTests(unittest.TestCase):
    """kb-level: the daily snapshot can be excluded from recall candidates."""

    def setUp(self):
        from core_services import KnowledgeBase

        self.tmp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(base_dir=self.tmp.name)
        self.kb.sqlite_store = None
        self.kb.embedding_provider = None
        today = datetime.now().strftime("%Y-%m-%d")
        self.kb.upsert_daily_activity_memory(
            today,
            {"done": 1, "total": 2, "status": "partial",
             "task_details": [{"title": "Read", "done": True}]},
            1,
            {"conversation_count": 2, "session_count": 1},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _selected_ids(self, **kwargs):
        return {
            str(item.get("id"))
            for item in self.kb.get_relevant_memories("随便聊点什么", **kwargs)
        }

    def test_daily_snapshot_excluded_when_requested(self):
        daily_id = str(self.kb.DAILY_ACTIVITY_MEMORY_ID)
        self.assertNotIn(daily_id, self._selected_ids(exclude_daily_activity=True))
        self.assertIn(daily_id, self._selected_ids(exclude_daily_activity=False))


class AccessCountGatingTests(unittest.TestCase):
    """Selection alone must not bump access_count (V4 plan section 3.5)."""

    def setUp(self):
        from core_services import KnowledgeBase

        self.tmp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(base_dir=self.tmp.name)
        self.kb.sqlite_store = None
        self.kb.embedding_provider = None
        self.kb.memories = []
        self.memory = self.kb.add_memory(
            "Doctor prefers concise replies with necessary reasoning.",
            source="manual",
            category="preference",
            metadata={"is_locked": True},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_recall_does_not_mark_access(self):
        selected = self.kb.get_relevant_memories("What reply style does Doctor prefer?")
        self.assertTrue(selected)
        fresh = self.kb._find_memory(self.memory["id"])
        self.assertEqual(int(fresh.get("access_count", 0) or 0), 0)

    def test_explicit_mark_accessed_still_works(self):
        selected = self.kb.get_relevant_memories(
            "What reply style does Doctor prefer?", mark_accessed=True
        )
        self.assertTrue(selected)
        fresh = self.kb._find_memory(self.memory["id"])
        self.assertEqual(int(fresh.get("access_count", 0) or 0), 1)

    def test_prompt_context_audit_does_not_mark_access(self):
        self.kb.build_prompt_context_with_audit(
            "What reply style does Doctor prefer?"
        )
        fresh = self.kb._find_memory(self.memory["id"])
        self.assertEqual(int(fresh.get("access_count", 0) or 0), 0)


class CandidateAutoAdoptDefaultTests(unittest.TestCase):
    """Aged candidates must not auto-promote unless explicitly enabled."""

    def setUp(self):
        from core_services import KnowledgeBase
        from datetime import timedelta

        self.tmp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(base_dir=self.tmp.name)
        self.kb.sqlite_store = None
        self.kb.embedding_provider = None
        self.kb.memories = []
        old = datetime.now() - timedelta(days=9)
        self.kb.memory_candidates = [{
            "id": "aged-1",
            "creation_time": old.strftime("%Y-%m-%d %H:%M:%S"),
            "date": old.strftime("%Y-%m-%d"),
            "title": "candidate",
            "content": "Doctor 很久以前说过的偏好，conf=0.95",
            "category": "preference",
            "tags": None,
            "confidence": 0.95,
            "importance_score": None,
            "status": "pending",
            "source": "auto_autonomous",
            "source_detail": {"type": "conversation"},
        }]

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_settings_keep_aged_candidate_pending(self):
        result = self.kb.auto_adopt_pending_candidates()
        self.assertEqual(result["adopted"], 0)
        self.assertEqual(self.kb.memory_candidates[0]["status"], "pending")
        self.assertEqual(len(self.kb.memories), 0)

    def test_default_settings_dict_disables_auto_adopt(self):
        defaults = self.kb.load_settings()
        # Fresh store: settings file may not exist, so defaults decide.
        self.assertFalse(defaults.get("candidate_auto_adopt_enabled", True))


if __name__ == "__main__":
    unittest.main()
