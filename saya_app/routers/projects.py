from fastapi import APIRouter, HTTPException

from core_services import project_manager, memory_manager, kb_system


router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects():
    return {"projects": project_manager.list_projects()}


@router.post("/add")
def add_project(req: dict):
    project = project_manager.add_project(req.get("name"))
    if not project:
        raise HTTPException(status_code=400, detail="Project name required")
    return {"status": "success", "project": project}


@router.post("/assign_session")
def assign_session_to_project(req: dict):
    if project_manager.add_session(req.get("project_id"), req.get("session_id")):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Project or session not found")


@router.post("/remove_session")
def remove_session_from_project(req: dict):
    # Detach a single conversation from its project so it falls back to the
    # global conversation list. The session file itself is preserved.
    session_id = req.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    project_manager.remove_session(session_id)
    return {"status": "success"}


@router.post("/delete")
def delete_project(req: dict):
    project_id = req.get("id") or req.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Project id required")

    active_session_id = None
    if memory_manager.current_session_file:
        active_session_id = memory_manager.current_session_file.replace("\\", "/").split("/")[-1]

    try:
        plan = project_manager.consume_project_deletion_plan(
            project_id, req.get("project_confirmation_token"), memory_manager,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    session_stages = []
    try:
        for session_id in plan.get("session_ids", []):
            signature = plan.get("session_signatures", {}).get(session_id)
            if not signature:
                continue
            session_stages.append(memory_manager.stage_session_deletion(
                session_id, expected_signature=signature,
            ))
        project_stage = project_manager.stage_project_deletion(project_id)
    except (ValueError, RuntimeError, OSError) as exc:
        for stage in reversed(session_stages):
            memory_manager.rollback_staged_session(stage)
        raise HTTPException(status_code=409, detail=str(exc))

    memory_ids = []
    for session_id in plan.get("session_ids", []):
        memory_ids.extend(
            memory.get("id") for memory in kb_system._auto_memories_for_session(session_id)
        )
    memory_ids = list(dict.fromkeys(str(memory_id) for memory_id in memory_ids if memory_id))
    memory_result = kb_system.delete_memories(
        memory_ids, reason="project_delete",
        confirmation_token=req.get("memory_confirmation_token"),
        require_all=True,
    ) if memory_ids else {"status": "success", "deleted_ids": [], "deleted_count": 0}
    if memory_result.get("status") != "success" or set(memory_result.get("deleted_ids") or []) != set(memory_ids):
        project_manager.rollback_staged_project(project_stage)
        for stage in reversed(session_stages):
            memory_manager.rollback_staged_session(stage)
        raise HTTPException(status_code=409, detail="Linked memories changed. Review project deletion again.")

    finalized = all(memory_manager.commit_staged_session(stage) for stage in session_stages)
    if not finalized:
        raise HTTPException(status_code=500, detail="Unable to finalize all conversation deletions")
    deleted_sessions = [stage["session_id"] for stage in session_stages]
    deleted_active_session = active_session_id in deleted_sessions

    if deleted_active_session:
        memory_manager.current_session_file = None

    return {
        "status": "success",
        "deleted_sessions": deleted_sessions,
        "was_active_session": deleted_active_session,
        "deleted_memories": memory_result.get("deleted_count", 0),
    }


@router.post("/delete-preview")
def preview_project_delete(req: dict):
    project_id = req.get("id") or req.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Project id required")
    try:
        project_preview = project_manager.preview_project_deletion(project_id, memory_manager)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    memory_preview = kb_system.preview_auto_memory_deletion(
        project_preview["session_ids"], reason="project_delete",
    )
    return {
        "status": "confirmation_required",
        "project_id": project_id,
        "session_count": len(project_preview["session_ids"]),
        **memory_preview,
        "project_confirmation_token": project_preview["confirmation_token"],
        "memory_confirmation_token": memory_preview["confirmation_token"],
    }
