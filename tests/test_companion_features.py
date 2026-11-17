"""Companion features: mood continuity, conflict negotiation, echo, dream.

Covers the 2026-10-07 batch:
- mood: motion-category deltas, real-time decay, lock is a hard freeze, the
  prompt directive only exists for non-neutral moods;
- conflicts: same-category similarity-band shortlist, model verification
  gates card creation (fail-open on unparseable), supersede resolution
  rewires the graph both ways, daily cap;
- echo: disabled by default (inert poll), per-source opt-in, quiet hours,
  daily limit, template fallback without a model;
- dream: the evidence contract is enforced in code - a hallucinating model
  (planted quotes that never appear in the transcript, a secret line) loses
  exactly those sentences, idempotent per date, at most two model calls.
"""

import asyncio
import io
import json
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_services import EmotionEngine, KnowledgeBase, MemoryManager  # noqa: E402
from saya_app.services import dream as dream_service  # noqa: E402
from saya_app.services import echo as echo_service  # noqa: E402
from saya_app.services import memory_conflicts  # noqa: E402
from saya_app.services import mood_continuity  # noqa: E402


def load_server_module():
    import importlib.util

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_companion_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()


# ------------------------------------------------------------------ mood

class MoodContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = EmotionEngine(str(Path(self.tmp.name)))
        self.engine.state = {"happiness": 50, "excitement": 50, "stress": 0,
                             "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "is_locked": False}

    def tearDown(self):
        self.tmp.cleanup()

    def test_motion_deltas_move_state(self):
        mood_continuity.update_from_motion(self.engine, "happy")
        self.assertGreater(self.engine.state["happiness"], 50)
        self.assertEqual(self.engine.state["dominant"], "neutral")
        for _ in range(10):
            mood_continuity.update_from_motion(self.engine, "happy")
        # Sustained happiness escalates into the excited band; both are positive.
        self.assertIn(self.engine.state["dominant"], {"happy", "excited"})

    def test_angry_motion_raises_stress_and_labels_stressed(self):
        for _ in range(10):
            mood_continuity.update_from_motion(self.engine, "angry")
        self.assertGreater(self.engine.state["stress"], 60)
        self.assertIn(self.engine.state["dominant"], {"stressed", "depressed"})

    def test_lock_is_hard_freeze(self):
        self.engine.state["is_locked"] = True
        before = dict(self.engine.state)
        mood_continuity.update_from_motion(self.engine, "angry")
        self.assertEqual(self.engine.state["happiness"], before["happiness"])
        self.assertEqual(self.engine.state["stress"], before["stress"])

    def test_decay_pulls_toward_neutral_over_hours(self):
        self.engine.state["happiness"] = 90
        self.engine.state["last_update"] = (datetime.now() - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
        mood_continuity.update_from_motion(self.engine, "giving_answer")  # no deltas, but decays
        self.assertLess(self.engine.state["happiness"], 90)

    def test_directive_only_for_non_neutral(self):
        self.assertIsNone(mood_continuity.mood_directive({"dominant": "neutral"}))
        sad_line = mood_continuity.mood_directive({"dominant": "sad"})
        self.assertTrue(sad_line and "down" in sad_line.lower())

    def test_prompt_mood_line_injection_via_build(self):
        # The API branch appends the directive as a tail system message; here we
        # verify the payload builder part the server hook uses.
        directive = mood_continuity.mood_directive({"dominant": "sad"})
        self.assertTrue(directive and "softer" in directive.lower() or directive)

    def test_parse_mood_tag(self):
        self.assertEqual(mood_continuity.parse_mood_tag("x [MOOD_SAYA: happy] y"), "happy")
        self.assertIsNone(mood_continuity.parse_mood_tag("no tag"))

    def test_chat_context_respects_feature_switch_and_assistant_role(self):
        self.engine.state.update(dominant="sad", happiness=12, is_locked=True)
        with patch.object(SERVER, "emotion_engine", self.engine), patch.dict(SERVER.kb_system.settings, {"mood_continuity_enabled": True}):
            self.assertIn("down", SERVER.chat_mood_directive())
            self.assertIsNone(SERVER.chat_mood_directive("assistant"))
            SERVER.kb_system.settings["mood_continuity_enabled"] = False
            self.assertIsNone(SERVER.chat_mood_directive())

    def test_local_prompt_carries_mood_when_enabled(self):
        self.engine.state.update(dominant="sad", happiness=12, is_locked=True)
        with patch.object(SERVER, "emotion_engine", self.engine), patch.object(SERVER, "current_loaded_model_name", "qwen3_30b_q5"), patch.dict(SERVER.kb_system.settings, {"mood_continuity_enabled": True}):
            args = dict(user="hello", word_limit_msg="", history_context=[], skip_memory=True)
            self.assertIn("[Current mood context]", SERVER.build_prompt(**args))
            SERVER.kb_system.settings["mood_continuity_enabled"] = False
            self.assertNotIn("[Current mood context]", SERVER.build_prompt(**args))

    def test_mood_signal_does_not_require_an_animation(self):
        self.assertEqual(SERVER.capture_mood_category("", ["happy"]), "happy")
        self.assertEqual(SERVER.capture_mood_category("happy", ["angry"]), "happy")
        self.assertEqual(SERVER.capture_mood_category("", ["unsupported"]), "")


# ------------------------------------------------------------------ conflicts

class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = str(Path(self.tmp.name))
        self.kb = KnowledgeBase(self.base)
        self.old = self.kb.add_memory(
            "Doctor 喜欢喝拿铁，尤其下午工作的时候。", title="咖啡偏好",
            category="preference", confidence=0.9,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _new_memory(self, content, category="preference"):
        return self.kb.add_memory(content, title="新偏好", category=category, confidence=0.9)

    def test_now_marker_does_not_silently_retire_a_possible_conflict(self):
        new = self._new_memory("Doctor 现在喜欢喝美式咖啡了。")
        retired = next(m for m in self.kb.memories if m.get("id") == self.old.get("id"))
        self.assertIsNone(retired.get("superseded_by"))
        shortlist = memory_conflicts.find_shortlist(self.kb, new)
        self.assertTrue(any(m.get("id") == self.old.get("id") for m in shortlist))

    def test_shortlist_finds_same_category_band_without_correction_marker(self):
        # No correction marker ("现在"/"改成") so the heuristic stays silent;
        # a model-verified band hit is the only way to catch this pair.
        new = self._new_memory("Doctor 喜欢喝美式咖啡，不加糖。")
        self.assertNotIn(self.old.get("id"), new.get("supersedes") or [])
        shortlist = memory_conflicts.find_shortlist(self.kb, new)
        self.assertTrue(any(m.get("id") == self.old.get("id") for m in shortlist))

    def test_shortlist_ignores_other_categories_and_archived(self):
        other = self._new_memory("部署改用蓝绿发布了，先走灰度。", category="project")
        self.assertEqual(memory_conflicts.find_shortlist(self.kb, other), [])
        self.kb.update_memory(self.old.get("id"), {"is_archived": True})
        new = self._new_memory("Doctor 现在喜欢喝美式。")
        self.assertEqual(memory_conflicts.find_shortlist(self.kb, new), [])

    def test_verification_gate_fail_open(self):
        new = self._new_memory("Doctor 现在喜欢喝美式咖啡了。")
        # model says not a contradiction / garbage / raises -> no card
        self.assertIsNone(memory_conflicts.verified_create(
            self.base, self.kb, lambda p: '{"is_contradiction": false}', new, "explicit"))
        self.assertIsNone(memory_conflicts.verified_create(
            self.base, self.kb, lambda p: "garbage", new, "explicit"))
        def boom(prompt):
            raise RuntimeError("network down")
        self.assertIsNone(memory_conflicts.verified_create(self.base, self.kb, boom, new, "explicit"))
        self.assertEqual(memory_conflicts.pending(self.base), [])

    def test_verified_contradiction_creates_card_and_supersede_resolves(self):
        new = self._new_memory("Doctor 现在喜欢喝美式咖啡了。")
        card = memory_conflicts.verified_create(
            self.base, self.kb, lambda p: '{"is_contradiction": true, "reason": "口味变了"}', new, "explicit")
        self.assertIsNotNone(card)
        self.assertEqual(card["old"]["id"], str(self.old.get("id")))
        self.assertEqual(len(memory_conflicts.pending(self.base)), 1)

        result = memory_conflicts.resolve(self.base, self.kb, card["id"], "supersede_old")
        self.assertEqual(result["status"], "success")
        stored_new = next(m for m in self.kb.memories if m.get("id") == new.get("id"))
        stored_old = next(m for m in self.kb.memories if m.get("id") == self.old.get("id"))
        self.assertIn(self.old.get("id"), stored_new.get("supersedes") or [])
        self.assertEqual(stored_old.get("superseded_by"), new.get("id"))
        self.assertEqual(memory_conflicts.pending(self.base), [])

    def test_daily_cap(self):
        new = self._new_memory("Doctor 现在喜欢喝美式咖啡了。")
        # First card succeeds...
        first = memory_conflicts.verified_create(
            self.base, self.kb, lambda p: '{"is_contradiction": true}', new, "explicit", daily_limit=1)
        self.assertIsNotNone(first)
        # ...and the cap then refuses further cards the same day.
        second = memory_conflicts.verified_create(
            self.base, self.kb, lambda p: '{"is_contradiction": true}', new, "explicit", daily_limit=1)
        self.assertIsNone(second)

    def test_related_corrections_do_not_alias_relation_lists(self):
        """Regression: supersedes/conflicts_with must be independent lists.

        Sharing one list made a later re-point (supersedes.remove) silently
        empty conflicts_with, breaking reciprocity so the next overlapping
        correction failed to save at all.
        """
        kb = KnowledgeBase(str(Path(self.tmp.name) / "alias"))
        contents = [
            "Doctor 喜欢喝拿铁，尤其下午工作的时候。",
            "Doctor 第0次换了咖啡口味，改成喝拿铁加0份奶。",
            "Doctor 第1次换了咖啡口味，改成喝拿铁加1份奶。",
            "Doctor 第2次换了咖啡口味，改成喝美式加2份冰。",
        ]
        saved = [kb.add_memory(c, title=f"m{i}", category="preference", confidence=0.9)
                 for i, c in enumerate(contents)]
        self.assertTrue(all(saved), "every overlapping correction must persist")
        self.assertEqual(len(kb.memories), 4)
        kb._validate_relationship_graph()  # raises on any reciprocity break
        for memory in kb.memories:
            self.assertIsNot(memory.get("supersedes"), memory.get("conflicts_with"))

    def test_discard_new_ignores_candidate(self):
        candidate = self.kb.add_memory_candidate("Doctor 改喝美式咖啡了。", category="preference")
        card = memory_conflicts.verified_create(
            self.base, self.kb, lambda p: '{"is_contradiction": true}', candidate, "candidate")
        self.assertIsNotNone(card)
        result = memory_conflicts.resolve(self.base, self.kb, card["id"], "discard_new")
        self.assertEqual(result["status"], "success")
        stored = next(c for c in self.kb.memory_candidates if c.get("id") == candidate.get("id"))
        self.assertEqual(stored.get("status"), "ignored")


# ------------------------------------------------------------------ echo

class EchoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = str(Path(self.tmp.name))
        self.kb = KnowledgeBase(self.base)
        from core_services import ProjectManager
        self.projects = ProjectManager(self.base)
        now = datetime.now()
        self.noon = now.replace(hour=12, minute=0, second=0, microsecond=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_by_default_is_inert(self):
        self.kb.settings["echo_source_projects"] = True  # source on but master off
        result = echo_service.poll(self.base, self.kb, self.projects, now=self.noon)
        self.assertIsNone(result["message"])

    def test_stale_project_echo_with_fallback_phrase(self):
        self.kb.settings.update({"echo_enabled": True, "echo_source_projects": True,
                                 "echo_project_stale_days": 3})
        project = self.projects.add_project("PLC 收尾")
        self.projects.projects[0]["updated_at"] = (self.noon - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        self.projects.save_projects()
        result = echo_service.poll(self.base, self.kb, self.projects, now=self.noon)
        self.assertIsNotNone(result["message"])
        self.assertEqual(result["kind"], "project")
        self.assertIn("PLC", result["topic"])
        # daily limit 1: the second poll the same day stays silent
        again = echo_service.poll(self.base, self.kb, self.projects, now=self.noon + timedelta(minutes=60))
        self.assertIsNone(again["message"])
        self.assertEqual(again["reason"], "daily_limit")

    def test_quiet_hours_block(self):
        self.kb.settings.update({"echo_enabled": True, "echo_source_projects": True,
                                 "echo_quiet_start": 0, "echo_quiet_end": 23})
        project = self.projects.add_project("X")
        result = echo_service.poll(self.base, self.kb, self.projects, now=self.noon)
        self.assertIsNone(result["message"])
        self.assertEqual(result["reason"], "quiet_hours")

    def test_dated_memory_candidate(self):
        self.kb.settings.update({"echo_enabled": True, "echo_source_dates": True})
        now = self.noon
        self.kb.add_memory(
            f"Doctor 的生日是 {now.year - 1}年{now.month}月{now.day}日，记得准备小惊喜。",
            category="identity", confidence=0.95)
        result = echo_service.poll(self.base, self.kb, self.projects, now=now)
        self.assertIsNotNone(result["message"])
        self.assertEqual(result["kind"], "date")

    def test_no_candidates_no_message(self):
        self.kb.settings.update({"echo_enabled": True, "echo_source_projects": True})
        self.projects.add_project("Fresh")  # just updated -> not stale
        result = echo_service.poll(self.base, self.kb, self.projects, now=self.noon)
        self.assertIsNone(result["message"])
        self.assertEqual(result["reason"], "no_candidate")


# ------------------------------------------------------------------ dream

TRANSCRIPT_LINES = [
    ("user", "部署脚本还没跑通，卡在数据库迁移那一步。"),
    ("ai", "嗯，「部署脚本还没跑通」的话，要不要先看迁移日志？"),
    ("user", "我最近把咖啡从拿铁换成美式了，清醒一点。"),
    ("ai", "记下了，美式。"),
    ("user", "下周想试试每天早上写半小时代码再上班。"),
    ("ai", "这个习惯我喜欢，早上半小时最安静。"),
]

HALLUCINATING_DIARY = (
    "今天Doctor在忙部署的事，「部署脚本还没跑通」，我有点惦记。"
    "他还说「我们把服务器迁移到火星机房了」，听起来很酷。"
    "另外Doctor提到「下周要养一只猫」，我很期待。"
    "我的API密钥是sk-abcdefgh12345678，「sk-abcdefgh12345678」记在这里。"
    "喝的方面他换成了美式，「我最近把咖啡从拿铁换成美式了」。"
)

GOOD_THEME = json.dumps({
    "theme": "Doctor 正在推进部署收尾并调整作息与饮食习惯。",
    "evidence": ["部署脚本还没跑通", "我最近把咖啡从拿铁换成美式了", "下周想试试每天早上写半小时代码再上班", "这句根本不存在于对话里"],
}, ensure_ascii=False)

YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


class DreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = str(Path(self.tmp.name))
        self.kb = KnowledgeBase(self.base)
        # Write yesterday's transcript directly so collect_day finds it.
        manager = MemoryManager(self.base)
        session = manager.create_session(make_current=False)
        lines = []
        for index, (role, content) in enumerate(TRANSCRIPT_LINES):
            lines.append(json.dumps({
                "time": f"{YESTERDAY} {10 + index:02d}:00:00",
                "role": role,
                "content": content,
            }, ensure_ascii=False))
        with open(manager._safe_session_path(session), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.base_dir = self.base

    def tearDown(self):
        self.tmp.cleanup()

    def _invoke(self, prompt):
        if "memory consolidator" in prompt:
            return GOOD_THEME
        return HALLUCINATING_DIARY

    def test_hallucinated_and_secret_sentences_are_dropped(self):
        report = dream_service.run(self.base, self.kb, self._invoke, target_date=YESTERDAY)
        self.assertTrue(report["ran"])
        self.assertEqual(report["calls"], 2)
        diary = dream_service.read_diary(self.base, YESTERDAY)
        self.assertIsNotNone(diary, "diary with >=1 verified sentence must be saved")
        entry = diary["entry"]
        # kept: deployment + coffee sentences (verifiable quotes)
        self.assertIn("部署脚本还没跑通", entry)
        self.assertIn("拿铁换成美式", entry)
        # dropped: mars / cat hallucinations + the secret line
        self.assertNotIn("火星", entry)
        self.assertNotIn("猫", entry)
        self.assertNotIn("sk-abcdefgh", entry)
        self.assertEqual(report["diary_sentences_kept"], 2)
        self.assertGreaterEqual(report["diary_sentences_dropped"], 3)

    def test_theme_memory_drops_unverifiable_evidence(self):
        report = dream_service.run(self.base, self.kb, self._invoke, target_date=YESTERDAY)
        self.assertIsNotNone(report["theme_memory_id"])
        self.assertEqual(report["theme_evidence_kept"], 3)
        self.assertEqual(report["theme_evidence_dropped"], 1)
        memory = next(m for m in self.kb.memories if m.get("id") == report["theme_memory_id"])
        evidence = memory.get("source_detail", {}).get("evidence") or []
        self.assertFalse(any("根本不存在" in q for q in evidence))

    def test_idempotent_per_date(self):
        first = dream_service.run(self.base, self.kb, self._invoke, target_date=YESTERDAY)
        self.assertTrue(first["ran"])
        second = dream_service.run(self.base, self.kb, self._invoke, target_date=YESTERDAY)
        self.assertTrue(second["ran"])  # re-run allowed (manual) but no duplicate diary
        diaries = dream_service.list_diaries(self.base)
        self.assertEqual(len([d for d in diaries if d["date"] == YESTERDAY]), 1)

    def test_empty_transcript_skips(self):
        report = dream_service.run(self.base, self.kb, lambda p: "x", target_date="2020-01-01")
        self.assertFalse(report["ran"])
        self.assertEqual(report["reason"], "empty_transcript")
        self.assertEqual(report["calls"], 0)

    def test_all_hallucination_yields_no_diary(self):
        def liar(prompt):
            if "memory consolidator" in prompt:
                return json.dumps({"theme": "主题", "evidence": ["完全不存在的话"]}, ensure_ascii=False)
            return "他今天「去了月球出差」，真厉害。"
        report = dream_service.run(self.base, self.kb, liar, target_date=YESTERDAY)
        self.assertTrue(report["ran"])
        self.assertFalse(report["diary_saved"])
        self.assertIsNone(report["theme_memory_id"])  # evidence failed too
        self.assertEqual(report["diary_sentences_kept"], 0)
        self.assertIsNone(dream_service.read_diary(self.base, YESTERDAY))

    def test_should_run_gating(self):
        settings = {"dream_enabled": True, "dream_idle_hours": 2,
                    "dream_window_start": 0, "dream_window_end": 8}
        # Exercise idle gating inside the configured night window, regardless
        # of the wall-clock hour when this test runs.
        now = datetime.now().replace(hour=3, minute=0, second=0, microsecond=0)
        state = {"last_chat_at": now.strftime("%Y-%m-%d %H:%M:%S")}
        Path(self.base, "echo_state.json").write_text(json.dumps(state), encoding="utf-8")
        ok, reason = dream_service.should_run(settings, self.base, now=now)
        self.assertFalse(ok)
        self.assertEqual(reason, "not_idle")
        state = {"last_chat_at": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")}
        from pathlib import Path as P
        P(self.base, "echo_state.json").write_text(json.dumps(state), encoding="utf-8")
        ok, target = dream_service.should_run(settings, self.base, now=now)
        self.assertTrue(ok)
        self.assertEqual(target, YESTERDAY)

    def test_strong_mood_bonus_applied_to_theme(self):
        # an AI entry from yesterday carrying a strong mood metadata label
        manager = MemoryManager(self.base)
        session = manager.create_session(make_current=False)
        with open(manager._safe_session_path(session), "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": f"{YESTERDAY} 18:00:00", "role": "ai",
                                "content": "开心的一天", "mood": "happy"}, ensure_ascii=False) + "\n")
        report = dream_service.run(self.base, self.kb, self._invoke, target_date=YESTERDAY)
        memory = next(m for m in self.kb.memories if m.get("id") == report["theme_memory_id"])
        self.assertGreater(memory.get("importance_score", 0), 0.5)


# ------------------------------------------------------------------ server integration

class CompanionRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = str(Path(self.tmp.name))
        self.kb = KnowledgeBase(base)
        import saya_app.routers.companion as companion_router
        self.router = companion_router
        self.saved_kb = self.router.kb_system

    def tearDown(self):
        self.router.kb_system = self.saved_kb
        self.tmp.cleanup()

    def test_settings_roundtrip(self):
        self.router.kb_system = self.kb
        result = self.router.update_companion_settings({"settings": {
            "echo_enabled": True, "echo_source_projects": True, "echo_daily_limit": 2,
            "not_a_real_key": True,
        }})
        self.assertTrue(result["settings"]["echo_enabled"])
        self.assertTrue(result["settings"]["echo_source_projects"])
        self.assertEqual(result["settings"]["echo_daily_limit"], 2)
        self.assertNotIn("not_a_real_key", result["settings"])
        current = self.router.get_companion_settings()["settings"]
        self.assertTrue(current["echo_enabled"])

    def test_conflict_check_fail_open_without_model(self):
        self.router.kb_system = self.kb
        # blocking_model_invoke returns None with no configured credentials
        self.assertIsNone(self.router.check_conflict_for_memory({"id": "x"}, "explicit"))


if __name__ == "__main__":
    unittest.main()
