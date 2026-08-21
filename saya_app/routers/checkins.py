from typing import Optional

from fastapi import APIRouter, HTTPException

from core_services import (
    checkin_manager,
    goal_manager,
    memory_manager,
    refresh_daily_activity_memory,
    sync_anki_checkin_state,
)
from saya_app.services.anki import anki_service


router = APIRouter(prefix="/api/checkins", tags=["checkins"])


def _refresh_anki_checkin(force_refresh=False):
    """Sync the latest in-memory Anki state without blocking Daily reads.

    Taking an online SQLite snapshot can be slow while Anki owns the live
    collection.  Daily's status endpoint is part of the page-start path, so it
    must never wait for that optional external refresh.
    """
    try:
        state = anki_service.get_state()
        if force_refresh and state.get("configured"):
            state = anki_service.refresh()
        return sync_anki_checkin_state(state)
    except Exception:
        return None


def _refresh_today_memory(date_str=None):
    if date_str and str(date_str)[:10] != checkin_manager._today():
        return None
    try:
        return refresh_daily_activity_memory()
    except Exception:
        # The check-in write remains successful even if optional memory
        # synchronization encounters a transient storage issue.
        return None


@router.get("/status")
def get_checkin_status(
    year: Optional[int] = None,
    month: Optional[int] = None,
    selected_date: Optional[str] = None,
):
    _refresh_anki_checkin()
    state = checkin_manager.get_state(year=year, month=month, selected_date=selected_date)
    state["goals"] = [
        {
            "id": goal.get("id"),
            "title": goal.get("title", ""),
            "status": goal.get("status", "active"),
        }
        for goal in goal_manager.goals
        if isinstance(goal, dict)
    ]
    return state


@router.post("/activity/enter")
def record_app_entry():
    """Count one main-page entry and immediately refresh today's memory."""
    try:
        result = refresh_daily_activity_memory(record_entry=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to update daily activity: {exc}")
    return {
        "status": "success",
        "date": result.get("date"),
        "entry_count": result.get("activity", {}).get("entry_count", 0),
        "conversation_count": result.get("chat", {}).get("conversation_count", 0),
        "checkin": result.get("checkin", {}),
    }


@router.post("/tasks/add")
def add_checkin_task(req: dict):
    try:
        task = checkin_manager.add_task(
            req.get("title"), req.get("goal_id"), req.get("preset"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not task:
        raise HTTPException(status_code=400, detail="Task title required")
    if task.get("preset") == "anki":
        _refresh_anki_checkin(force_refresh=True)
    _refresh_today_memory()
    return {"status": "success", "task": task}


@router.post("/tasks/update")
def update_checkin_task(req: dict):
    if checkin_manager.update_task(req.get("id"), req.get("updates", {})):
        _refresh_today_memory()
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/reorder")
def reorder_checkin_tasks(req: dict):
    if checkin_manager.reorder_tasks(req.get("ordered_ids", [])):
        return {"status": "success"}
    raise HTTPException(status_code=400, detail="Invalid order")


@router.post("/tasks/delete")
def delete_checkin_task(req: dict):
    if checkin_manager.delete_task(req.get("id")):
        _refresh_today_memory()
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/day")
def save_checkin_day(req: dict):
    try:
        record = checkin_manager.save_day(
            date_str=req.get("date"),
            completions=req.get("completions", {}),
            note=req.get("note", ""),
            daily_tasks=req.get("daily_tasks"),
            mood=req.get("mood"),
            energy=req.get("energy"),
            difficulty=req.get("difficulty"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _refresh_today_memory(req.get("date"))
    return {"status": "success", "record": record}


@router.post("/context")
def get_checkin_context(req: dict):
    return {
        "status": "success",
        "context": checkin_manager.build_dialogue_context(req.get("date")),
    }


@router.post("/chat_session")
def get_checkin_chat_session(req: dict):
    try:
        session_id, created = checkin_manager.get_or_create_chat_session(
            req.get("date"),
            memory_manager,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "session_id": session_id, "created": created}


@router.post("/export")
def export_checkin_report(req: dict):
    return checkin_manager.export_report(
        scope=req.get("scope", "month"),
        year=req.get("year"),
        month=req.get("month"),
    )
