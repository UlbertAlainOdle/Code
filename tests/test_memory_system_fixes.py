"""Memory-system fixes (2026-09-07): game_task/event episodic layering,
aged-candidate auto-adoption, and the games-memory purge contract.
"""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_services import KnowledgeBase  # noqa: E402


def _kb(tmp_path):
    """Real constructor against a temp base_dir - far fewer harness lies
    than __new__ stubbing (the write lock and settings load for real)."""
    kb = KnowledgeBase(base_dir=str(tmp_path))
    kb.settings.update({
        "layered_memory_enabled": True,
        "candidate_auto_adopt_enabled": True,
        "candidate_auto_adopt_days": 3,
        "candidate_auto_adopt_min_confidence": 0.8,
    })
    kb.sqlite_store = None
    kb.embedding_provider = None
    return kb


class LayerRuleTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.kb = _kb(Path(self.tmp.name))
        self.kb.memories = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_game_task_events_layer_as_episodic(self):
        memory = self.kb.add_memory(
            "2026-09-07 01:00 在Minecraft里，我帮Doctor完成了「准备2个牛排」（共3步）。",
            title="游戏任务：准备2个牛排", source="game_task", category="event",
        )
        self.assertEqual(self.kb._memory_layer(memory), "episodic")

    def test_event_category_layers_as_episodic(self):
        memory = self.kb.add_memory(
            "发生了某件事", title="事件", source="manual", category="event")
        self.assertEqual(self.kb._memory_layer(memory), "episodic")

    def test_ordinary_semantic_memories_stay_semantic(self):
        memory = self.kb.add_memory(
            "Doctor 的项目使用 FastAPI", title="项目", source="manual", category="project")
        self.assertEqual(self.kb._memory_layer(memory), "semantic")


class AutoAdoptTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.kb = _kb(Path(self.tmp.name))
        self.kb.memories = []

    def tearDown(self):
        self.tmp.cleanup()

    def _candidate(self, days_old, confidence, status="pending"):
        created = datetime.now() - timedelta(days=days_old)
        return {
            "id": f"c-{days_old}-{confidence}",
            "creation_time": created.strftime("%Y-%m-%d %H:%M:%S"),
            "date": created.strftime("%Y-%m-%d"),
            "title": "candidate",
            "content": f"Doctor 喜欢在 {days_old} 天前说的东西 conf={confidence}",
            "category": "preference",
            "tags": None,
            "confidence": confidence,
            "importance_score": None,
            "status": status,
            "source": "auto_autonomous",
            "source_detail": {"type": "conversation"},
        }

    def test_aged_confident_candidate_adopts_itself(self):
        self.kb.memory_candidates = [
            self._candidate(days_old=5, confidence=0.9),
        ]
        result = self.kb.auto_adopt_pending_candidates()
        self.assertEqual(result["adopted"], 1)
        self.assertEqual(self.kb.memory_candidates[0]["status"], "confirmed")
        self.assertEqual(len(self.kb.memories), 1)

    def test_young_or_unconfident_candidates_stay_pending(self):
        self.kb.memory_candidates = [
            self._candidate(days_old=1, confidence=0.95),   # too young
            self._candidate(days_old=5, confidence=0.5),    # too unsure
        ]
        result = self.kb.auto_adopt_pending_candidates()
        self.assertEqual(result["adopted"], 0)
        self.assertTrue(all(c["status"] == "pending" for c in self.kb.memory_candidates))

    def test_duplicate_content_is_not_double_adopted(self):
        self.kb.memory_candidates = [
            dict(self._candidate(days_old=5, confidence=0.9)),
            dict(self._candidate(days_old=5, confidence=0.9)),
        ]
        self.kb.memory_candidates[1]["id"] = "c-dup"
        self.kb.memory_candidates[1]["content"] = self.kb.memory_candidates[0]["content"]
        result = self.kb.auto_adopt_pending_candidates()
        self.assertEqual(len(self.kb.memories), 1)

    def test_disabled_flag_leaves_candidates_alone(self):
        self.kb.settings["candidate_auto_adopt_enabled"] = False
        self.kb.memory_candidates = [self._candidate(days_old=9, confidence=0.95)]
        result = self.kb.auto_adopt_pending_candidates()
        self.assertEqual(result["adopted"], 0)
        self.assertEqual(self.kb.memory_candidates[0]["status"], "pending")

    def test_corrupt_timestamps_never_crash_adoption(self):
        self.kb.memory_candidates = [self._candidate(days_old=5, confidence=0.9)]
        self.kb.memory_candidates[0]["creation_time"] = "not-a-date"
        result = self.kb.auto_adopt_pending_candidates()
        self.assertEqual(result["adopted"], 0)


class PurgeScriptTest(unittest.TestCase):
    def _store(self, root):
        kb_dir = root / "knowledge_base"
        kb_dir.mkdir(exist_ok=True)
        (kb_dir / "knowledge.json").write_text(json.dumps([
            {"id": "1", "source": "games", "title": "Games: 国际象棋",
             "content": "对局", "memory_layer": "semantic"},
            {"id": "2", "source": "game_task", "title": "游戏任务：牛排",
             "content": "完成了", "memory_layer": "semantic"},
            {"id": "3", "source": "manual", "title": "普通记忆",
             "content": "知识", "memory_layer": "semantic"},
        ], ensure_ascii=False), encoding="utf-8")
        return kb_dir

    def test_purge_drops_games_keeps_minecraft_and_relayers(self):
        import subprocess
        import tempfile

        script = Path(__file__).resolve().parents[1] / "scripts" / "purge_games_memories.py"
        with tempfile.TemporaryDirectory() as tmp:
            kb_dir = self._store(Path(tmp))
            result = subprocess.run(
                [sys.executable, "-B", str(script), "--apply", "--kb-dir", str(kb_dir)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("purge games: 1", result.stdout)
            self.assertIn("re-layer to episodic: 1", result.stdout)
            rows = json.loads((kb_dir / "knowledge.json").read_text(encoding="utf-8"))
            self.assertEqual([r["source"] for r in rows], ["game_task", "manual"])
            self.assertEqual(rows[0]["memory_layer"], "episodic")
            self.assertEqual(rows[1]["memory_layer"], "semantic")
            # a backup was taken before surgery
            self.assertTrue(list(kb_dir.glob("knowledge.before-games-purge.*.json")))

    def test_purge_is_idempotent(self):
        import subprocess
        import tempfile

        script = Path(__file__).resolve().parents[1] / "scripts" / "purge_games_memories.py"
        with tempfile.TemporaryDirectory() as tmp:
            kb_dir = self._store(Path(tmp))
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, "-B", str(script), "--apply", "--kb-dir", str(kb_dir)],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads((kb_dir / "knowledge.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
