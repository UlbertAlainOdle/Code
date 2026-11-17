"""Game-event memory bridge: links the Minecraft task layer and the
long-term memory store (2026-09-06 deep-linkage round).

Before this module the two memory systems only met through a MANUAL archive
endpoint: game chats lived in per-session JSONL files, the persona LLM saw
keyword-retrieved knowledge-base memories, and nothing a task accomplished
ever reached long-term memory.  Now:

- WRITE  every COMPLETED plan becomes a compact episodic memory
         (source="game_task") via the knowledge base, deduped by task and recording window;
- READ   the persona prompt gains a [近期游戏] block built from the plan
         executor's in-process history, so Saya can reference what she just
         did even when the knowledge base has no matching entry;
- BRIDGE when the bot disconnects, the current game session is auto-archived
         (the manual endpoint's logic, fired automatically, fail-silent).

Everything here is fail-silent by design: memory enrichment must never break
a chat or a plan report.
"""

from __future__ import annotations

import os
from datetime import datetime

def _event_recording_enabled():
    """SAYA_GAME_MEMORY=0 disables task-event recording (test isolation:
    planner-executor tests complete plans, and without this switch every
    pytest run wrote REAL game_task memories into knowledge.json)."""
    return os.environ.get("SAYA_GAME_MEMORY", "1").strip() != "0"


def _knowledge_base():
    from core_services import kb_system  # noqa: WPS433 (lazy, heavy module)

    return kb_system


def task_event_content(plan):
    """Compact user-facing memory text for one finished plan."""
    goal = str(plan.get("goal_description") or "任务").strip()
    status = str(plan.get("status") or "")
    steps = len(plan.get("steps") or [])
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if status == "completed":
        return f"{stamp} 在Minecraft里，我帮Doctor完成了「{goal}」（共{steps}步）。"
    return f"{stamp} 在Minecraft里，任务「{goal}」未能完成（{status}）。"


def record_task_event(plan, knowledge_base=None):
    """Record a completed planner event once; enrichment never breaks plans.

    Replays and uncertain writes return None, preserving the original wrapper
    contract. game_task_writes.result_for reads the durable actual outcome.
    """
    try:
        if not _event_recording_enabled() or not isinstance(plan,dict) or plan.get('status')!='completed':return None
        from .game_task_writes import record
        result=record(knowledge_base or _knowledge_base(),plan)
        if result and result.fresh and result.committed:return result.items[0]['memory_id']
    except Exception:
        # A game completion is independent of whether memory storage succeeded.
        return None
    return None


def recent_game_events(limit=3, plan_executor=None):
    """Compact [近期游戏] lines from the plan executor's in-process history.

    Completed plans read naturally ("已完成：…"); recent failures surface as
    "没做成" so the persona can react to them honestly.  Returns [] on any
    problem.
    """
    try:
        if plan_executor is None:
            from .minecraft_planner import plan_executor  # noqa: WPS433

            plan_executor = plan_executor
        history = list(getattr(plan_executor, "_history", None) or [])
        lines = []
        for entry in reversed(history):
            if len(lines) >= max(1, int(limit or 3)):
                break
            goal = str(entry.get("goal_description") or "").strip()
            if not goal:
                continue
            status = str(entry.get("status") or "")
            finished = float(entry.get("finished_at") or 0)
            when = datetime.fromtimestamp(finished).strftime("%H:%M") if finished else ""
            if status == "completed":
                lines.append(f"- {when} 已完成「{goal}」".replace("  ", " "))
            else:
                lines.append(f"- {when} 「{goal}」没做成（{status}）")
        return lines
    except Exception:
        return []


def auto_archive_on_disconnect(session_id, archiver):
    """Archive one game session through ConversationService, fail-silent.

    Called from the mineflayer supervisor's lifecycle-end handler so every
    world visit lands in long-term memory even when nobody remembers to
    press archive.  Returns the archive result dict or None.
    """
    try:
        result = archiver(session_id)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        try:
            print(f"[game-memory] auto archive failed: {exc}")
        except Exception:
            pass
        return None
