"""Auto-save memory upgrade: local-model triage, recurrence ledger, auto-confirm.

Covers the 2026-09-29 strengthening pass:
- autonomous memory triage works without an API key via a locally loaded model;
- repeated observations merge into one candidate and track distinct sessions;
- only cross-session recurrence (never age) may auto-confirm a candidate,
  with sensitive categories, secret-like content and low scores excluded;
- importance_score survives candidate -> memory confirmation;
- the Agent planner prompt teaches explicit tool-selection policy.
"""

import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_services import KnowledgeBase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_server_module():
    """Import server5-22B----.py, bypassing its interactive model prompt."""
    import importlib.util

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_auto_memory_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()

TRIAGE_PAYLOAD = {
    "candidates": [
        {
            "title": "回答风格偏好",
            "content": "Doctor喜欢简洁但保留必要推理的回答。",
            "evidence": "我喜欢简洁但保留必要推理的回答",
            "category": "preference",
            "tags": ["style"],
            "confidence": 0.9,
            "importance_score": 0.8,
        }
    ]
}

USER_TEXT = "以后注意一下，我喜欢简洁但保留必要推理的回答，太长的解释我一般不看。"
ASSISTANT_TEXT = "好的，我会按照这个偏好来调整以后的回答方式。"


class _Req:
    api_key = ""
    api_model_name = ""
    autonomous_memory_model_name = ""


class LocalTriageRequestTests(unittest.TestCase):
    def test_extracts_text_from_llama_style_mapping(self):
        def fake_model(prompt, **kwargs):
            self.assertEqual(kwargs.get("stream"), False)
            return {"choices": [{"text": json.dumps(TRIAGE_PAYLOAD, ensure_ascii=False)}]}

        output = SERVER._autonomous_memory_local_request("prompt", fake_model)
        self.assertEqual(json.loads(output)["candidates"][0]["category"], "preference")

    def test_extracts_text_from_message_style_and_plain_string(self):
        message_model = lambda prompt, **kwargs: {"choices": [{"message": {"content": "abc"}}]}
        self.assertEqual(SERVER._autonomous_memory_local_request("p", message_model), "abc")
        self.assertEqual(SERVER._autonomous_memory_local_request("p", lambda p, **k: "raw"), "raw")

    def test_none_model_returns_empty(self):
        self.assertEqual(SERVER._autonomous_memory_local_request("p", None), "")


class MaybeCreateLocalPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(str(Path(self.tmp.name)))
        self.kb.settings["autonomous_memory_enabled"] = True
        self._original_kb = SERVER.kb_system
        SERVER.kb_system = self.kb

    def tearDown(self):
        SERVER.kb_system = self._original_kb
        self.tmp.cleanup()

    def test_local_model_used_when_no_api_key(self):
        async def fake_invoke(prompt):
            self.assertIn("autonomous memory triage helper", prompt)
            return json.dumps(TRIAGE_PAYLOAD, ensure_ascii=False)

        def fail_api(*args, **kwargs):
            raise AssertionError("API path must not be used when a local model is supplied")

        with patch.dict(os.environ, {"API_KEY": ""}), \
                patch.object(SERVER, "_autonomous_memory_api_request", fail_api):
            created = asyncio.run(SERVER.maybe_create_autonomous_memory_candidates(
                _Req(), USER_TEXT, ASSISTANT_TEXT,
                session_id="session-local-1",
                local_model_invoke=fake_invoke,
                local_model_name="test_local",
            ))
        self.assertEqual(len(created), 1)
        candidate = created[0]
        self.assertEqual(candidate["status"], "pending")
        self.assertEqual(candidate["occurrence_count"], 1)
        self.assertEqual(candidate["occurrence_sessions"], ["session-local-1"])
        self.assertTrue(str(candidate["source_detail"].get("model", "")).startswith("local:"))
        self.assertEqual(len(self.kb.memory_candidates), 1)

    def test_no_key_and_no_local_model_returns_empty(self):
        with patch.dict(os.environ, {"API_KEY": ""}):
            created = asyncio.run(SERVER.maybe_create_autonomous_memory_candidates(
                _Req(), USER_TEXT, ASSISTANT_TEXT, session_id="s"
            ))
        self.assertEqual(created, [])
        self.assertEqual(self.kb.memory_candidates, [])

    def test_local_invoke_failure_fails_open(self):
        async def broken_invoke(prompt):
            raise RuntimeError("model exploded")

        with patch.dict(os.environ, {"API_KEY": ""}):
            created = asyncio.run(SERVER.maybe_create_autonomous_memory_candidates(
                _Req(), USER_TEXT, ASSISTANT_TEXT,
                local_model_invoke=broken_invoke,
            ))
        self.assertEqual(created, [])


class RecurrenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(str(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_repeated_observation_merges_into_one_candidate(self):
        first = self.kb.add_memory_candidate(
            "Doctor喜欢简洁但保留必要推理的回答。", confidence=0.9,
            importance_score=0.8, session_id="s1",
        )
        second = self.kb.add_memory_candidate(
            "Doctor喜欢简洁但保留必要推理的回答。", confidence=0.85,
            importance_score=0.7, session_id="s2",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.kb.memory_candidates), 1)
        self.assertEqual(second["occurrence_count"], 2)
        self.assertEqual(sorted(second["occurrence_sessions"]), ["s1", "s2"])
        self.assertEqual(second["confidence"], 0.9)  # keeps the stronger score
        self.assertEqual(second["importance_score"], 0.8)
        self.assertTrue(second["last_seen_at"] >= second["first_seen_at"])

    def test_near_duplicate_rewording_also_merges(self):
        first = self.kb.add_memory_candidate(
            "Doctor prefers concise answers with necessary reasoning steps included",
            confidence=0.9, importance_score=0.8, session_id="s1",
        )
        second = self.kb.add_memory_candidate(
            "Doctor prefers concise answers with the necessary reasoning steps",
            confidence=0.9, importance_score=0.8, session_id="s2",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["occurrence_count"], 2)

    def test_unrelated_content_stays_separate(self):
        self.kb.add_memory_candidate(
            "Doctor prefers concise answers with necessary reasoning steps included",
            confidence=0.9, importance_score=0.8, session_id="s1",
        )
        self.kb.add_memory_candidate(
            "The project deadline for the compiler rewrite moved to next month",
            confidence=0.9, importance_score=0.8, session_id="s2",
        )
        self.assertEqual(len(self.kb.memory_candidates), 2)

    def test_same_session_repetition_does_not_count_as_recurrence(self):
        candidate = None
        for _ in range(3):
            candidate = self.kb.add_memory_candidate(
                "Doctor喜欢简洁但保留必要推理的回答。", confidence=0.9,
                importance_score=0.8, session_id="same-session",
            )
        self.assertEqual(candidate["occurrence_count"], 3)
        self.assertEqual(candidate["occurrence_sessions"], ["same-session"])
        self.assertEqual(self.kb.maybe_auto_confirm_recurring_candidates(), [])
        self.assertEqual(candidate["status"], "pending")


class RecurrenceAutoConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(str(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_recurring(self, category="preference", confidence=0.9, importance=0.8,
                        content="Doctor喜欢简洁但保留必要推理的回答。"):
        for session in ("s1", "s2", "s3"):
            self.kb.add_memory_candidate(
                content, confidence=confidence, importance_score=importance,
                category=category, session_id=session,
                source_detail={"evidence_type":"user_assertion", "user_evidence":[{"session_id":session,"quote":content}]},
            )

    def test_cross_session_recurrence_auto_confirms_without_star(self):
        self._seed_recurring()
        confirmed = self.kb.maybe_auto_confirm_recurring_candidates()
        self.assertEqual(len(confirmed), 1)
        candidate = confirmed[0]
        self.assertTrue(candidate.get("auto_confirmed"))
        self.assertEqual(candidate["auto_confirm_reason"], "cross_session_recurrence")
        self.assertEqual(candidate["status"], "confirmed")
        memories = self.kb.get_memories(include_archived=False)
        self.assertEqual(len(memories), 1)
        memory = memories[0]
        self.assertEqual(memory["importance_score"], 0.8)
        self.assertFalse(memory["is_important"])  # auto-confirm never stars
        self.assertFalse(memory["is_locked"])
        # Re-running must be idempotent: nothing pending remains.
        self.assertEqual(self.kb.maybe_auto_confirm_recurring_candidates(), [])

    def test_health_category_stays_manual(self):
        self._seed_recurring(category="health")
        self.assertEqual(self.kb.maybe_auto_confirm_recurring_candidates(), [])
        self.assertEqual(self.kb.memory_candidates[0]["status"], "pending")

    def test_secret_like_content_stays_manual(self):
        self._seed_recurring(content="Doctor的API密钥存放位置是办公桌抽屉。")
        self.assertEqual(self.kb.maybe_auto_confirm_recurring_candidates(), [])

    def test_low_confidence_or_importance_stays_manual(self):
        self._seed_recurring(confidence=0.6)
        self._seed_recurring(importance=0.3, content="Doctor每周三去图书馆自习。")
        self.assertEqual(self.kb.maybe_auto_confirm_recurring_candidates(), [])
        self.assertEqual(len([c for c in self.kb.memory_candidates if c["status"] == "pending"]), 2)

    def test_flag_off_disables_auto_confirm(self):
        self._seed_recurring()
        self.kb.settings["candidate_recurrence_confirm_enabled"] = False
        self.assertEqual(self.kb.maybe_auto_confirm_recurring_candidates(), [])
        self.assertEqual(self.kb.memory_candidates[0]["status"], "pending")

    def test_thresholds_respect_settings_overrides(self):
        self.kb.settings["candidate_recurrence_min_sessions"] = 4
        self._seed_recurring()
        self.assertEqual(self.kb.maybe_auto_confirm_recurring_candidates(), [])
        self.kb.add_memory_candidate(
            "Doctor喜欢简洁但保留必要推理的回答。", confidence=0.9,
            importance_score=0.8, session_id="s4",
            source_detail={"evidence_type":"user_assertion", "user_evidence":[{"session_id":"s4","quote":"我喜欢简洁但保留必要推理的回答。"}]},
        )
        self.assertEqual(len(self.kb.maybe_auto_confirm_recurring_candidates()), 1)


class ImportanceScoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(str(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_memory_stores_and_clamps_importance(self):
        memory = self.kb.add_memory("稳定事实", importance_score=1.7)
        self.assertEqual(memory["importance_score"], 1.0)
        memory = self.kb.add_memory("另一个稳定事实", importance_score=-0.5)
        self.assertEqual(memory["importance_score"], 0.0)
        # Without an explicit value the legacy rule-based scorer still applies;
        # an explicit structured value always takes precedence over it.
        memory = self.kb.add_memory("普通事实")
        self.assertIsInstance(memory.get("importance_score"), float)
        self.assertTrue(0.0 <= memory["importance_score"] <= 1.0)

    def test_confirm_propagates_candidate_importance(self):
        candidate = self.kb.add_memory_candidate(
            "Doctor喜欢简洁但保留必要推理的回答。", confidence=0.9,
            importance_score=0.77, session_id="s1",
        )
        result = self.kb.confirm_memory_candidate(candidate["id"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["memory"]["importance_score"], 0.77)


class AgentPlannerPolicyTests(unittest.TestCase):
    def test_planner_system_prompt_teaches_tool_selection(self):
        from saya_app.services.memory_v4.model_adapters import build_agent_messages

        memory_only = [{"name": "memory.query", "description": "query memory",
                        "input_schema": {"type": "object"}, "availability": "available",
                        "permission": "auto", "max_calls_per_turn": 2}]
        system = build_agent_messages(
            [{"role": "user", "content": "hello"}], memory_only, json_contract=True,
        )[0]["content"]
        self.assertIn("Tool selection policy", system)
        self.assertIn("memory.query", system)
        self.assertIn("ALWAYS call memory.query", system)
        self.assertIn("Answer directly", system)
        # Tools absent from the capability list must not be named at all, so
        # the model is never tempted to attempt them.
        self.assertNotIn("web.search", system)
        self.assertNotIn("weather.query", system)

        with_web = memory_only + [{"name": "web.search", "description": "search the web",
                                   "input_schema": {"type": "object"}, "availability": "available",
                                   "permission": "auto", "max_calls_per_turn": 2}]
        system_web = build_agent_messages(
            [{"role": "user", "content": "hello"}], with_web, json_contract=True,
        )[0]["content"]
        self.assertIn("web.search", system_web)
        self.assertIn("abstract the query", system_web)
        self.assertIn("unavailable in this session", system_web)


if __name__ == "__main__":
    unittest.main()
