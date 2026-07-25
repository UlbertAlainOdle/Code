from typing import Optional

from fastapi import APIRouter, HTTPException

from core_services import checkin_manager, memory_manager


router = APIRouter(prefix="/api/checkins", tags=["checkins"])


@router.get("/status")
def get_checkin_status(
    year: Optional[int] = None,
    month: Optional[int] = None,
    selected_date: Optional[str] = None,
):
    return checkin_manager.get_state(year=year, month=month, selected_date=selected_date)


@router.post("/tasks/add")
def add_checkin_task(req: dict):
    task = checkin_manager.add_task(req.get("title"))
    if not task:
        raise HTTPException(status_code=400, detail="Task title required")
    return {"status": "success", "task": task}


@router.post("/tasks/update")
def update_checkin_task(req: dict):
    if checkin_manager.update_task(req.get("id"), req.get("updates", {})):
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

