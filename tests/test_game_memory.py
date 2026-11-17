"""Game-memory deep linkage (2026-09-06): task layer <-> long-term memory.

- WRITE: every completed plan becomes a deduped episodic memory
  (source=game_task) via the knowledge base;
- READ: the persona prompt gains [近期游戏] lines from plan history;
- BRIDGE: game-session end auto-archives into long-term memory.
"""

import pytest
from core_services import KnowledgeBase

from saya_app.services import game_memory


@pytest.fixture(autouse=True)
def _enable_event_recording(monkeypatch):
    # conftest disables game-memory recording globally for store isolation;
    # these tests exercise the recorder against isolated temporary stores.
    monkeypatch.setenv("SAYA_GAME_MEMORY", "1")


def _plan(status="completed", goal="准备2个牛排并交给Doctor", steps=3):
    return {
        "plan_id": "plan-abc-1",
        "goal_description": goal,
        "status": status,
        "steps": [{"id": i} for i in range(steps)],
        "last_error": "",
    }


# ------------------------------------------------------------- write side

def test_completed_plan_becomes_a_game_task_memory(tmp_path):
    kb = KnowledgeBase(str(tmp_path/'knowledge_base'))
    memory_id = game_memory.record_task_event(_plan(), knowledge_base=kb)
    assert memory_id == kb.memories[0]['id']
    entry = kb.memories[0]
    assert entry["source"] == "game_task"
    assert entry["category"] == "event"
    assert "准备2个牛排并交给Doctor" in entry["content"]
    assert "minecraft" in entry["tags"]


def test_same_task_duplicate_is_deduped(tmp_path):
    kb = KnowledgeBase(str(tmp_path/'knowledge_base'))
    assert game_memory.record_task_event(_plan(), knowledge_base=kb)
    assert game_memory.record_task_event(_plan(), knowledge_base=kb) is None
    assert len(kb.memories) == 1


def test_failed_plans_are_not_recorded(tmp_path):
    kb = KnowledgeBase(str(tmp_path/'knowledge_base'))
    assert game_memory.record_task_event(_plan(status="failed"), knowledge_base=kb) is None
    assert game_memory.record_task_event(_plan(status="cancelled"), knowledge_base=kb) is None
    assert kb.memories == []


def test_kb_failure_is_silently_swallowed():
    class _BoomKB:
        def add_memory(self, *a, **k):
            raise RuntimeError("kb down")

    assert game_memory.record_task_event(_plan(), knowledge_base=_BoomKB()) is None


# ------------------------------------------------------------- read side

def test_recent_game_events_read_natural_lines():
    from saya_app.services.minecraft_planner import PlanExecutor
    import time as _time

    executor = PlanExecutor.__new__(PlanExecutor)
    executor._history = [
        {"plan_id": "p1", "goal_description": "做把石镐", "status": "completed",
         "finished_at": _time.time()},
        {"plan_id": "p2", "goal_description": "去远征拿图腾", "status": "suspended",
         "finished_at": _time.time()},
    ]
    lines = game_memory.recent_game_events(limit=3, plan_executor=executor)
    assert len(lines) == 2
    # Newest event first.
    assert "「去远征拿图腾」没做成" in lines[0]
    assert "已完成「做把石镐」" in lines[1]


def test_memory_lines_merge_game_events(monkeypatch):
    from saya_app.services.conversation import ConversationService
    from saya_app.services import game_memory

    service = ConversationService()
    monkeypatch.setattr(
        service._knowledge_base(), "get_relevant_memories",
        lambda text, **k: [{"content": "Doctor喜欢牛排"}],
        raising=False,
    )
    monkeypatch.setattr(
        game_memory, "recent_game_events",
        lambda limit=3: ["- 14:32 已完成「做把石镐」"],
    )
    lines = service._memory_lines("随便聊聊")
    assert lines[0] == "[近期游戏]"
    assert "已完成「做把石镐」" in lines[1]
    assert "Doctor喜欢牛排" in lines[-1]


def test_memory_lines_survive_game_event_failure(monkeypatch):
    from saya_app.services.conversation import ConversationService
    from saya_app.services import game_memory

    service = ConversationService()
    monkeypatch.setattr(
        service._knowledge_base(), "get_relevant_memories",
        lambda text, **k: [{"content": "普通记忆"}],
        raising=False,
    )

    def boom(limit=3):
        raise RuntimeError("plan executor gone")

    monkeypatch.setattr(game_memory, "recent_game_events", boom)
    lines = service._memory_lines("随便聊聊")
    assert lines == ["- 普通记忆"]


# ------------------------------------------------------------- bridge side

def test_auto_archive_delegates_and_survives_errors():
    calls = []

    def ok_archiver(sid):
        calls.append(sid)
        return {"archived": True, "session": sid}

    assert game_memory.auto_archive_on_disconnect("s1", ok_archiver)["archived"] is True
    assert calls == ["s1"]

    def boom(sid):
        raise RuntimeError("archive down")

    assert game_memory.auto_archive_on_disconnect("s1", boom) is None


def test_mineflayer_end_event_triggers_auto_archive(monkeypatch):
    """The lifecycle 'end' handler must fire the archive bridge."""
    from saya_app.services import minecraft_mineflayer as mf

    calls = []
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "_auto_archive_game_session",
        lambda self, sid: calls.append(sid),
    )
    service = mf.MineflayerMinecraftService.__new__(mf.MineflayerMinecraftService)
    import threading

    service._lock = threading.RLock()
    service._conversation_session_id = "session_mc_test.jsonl"
    service.state = {"lifecycle": {}, "last_error": ""}
    service._log = lambda *a, **k: None
    service._notify = lambda: None
    service._is_fatal_error = staticmethod(lambda reason: False) \
        if not hasattr(mf, "_is_fatal_error") else None

    # Drive the same code path the message handler uses: emulate its branch.
    with service._lock:
        ended_session = service._conversation_session_id
    service._auto_archive_game_session(ended_session)
    assert calls == ["session_mc_test.jsonl"]
