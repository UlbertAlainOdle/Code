import asyncio

from fastapi import APIRouter, HTTPException

from saya_app.services.learner import learner_service


router = APIRouter(prefix="/api/learner", tags=["learner"])


def search_snapshot(credentials):
    from saya_app.services.conversation import conversation_service
    effective=conversation_service.credentials()
    if 'search_provider' in effective.get('local_fields',[]):
        credentials.update({key:effective.get(key,'') for key in ('search_provider','brave_search_api_key','deepseek_search_api_key')})
    return credentials


@router.get("/state")
def get_learner_state():
    return learner_service.get_state()


@router.get("/runs/{run_id}")
def get_learner_run(run_id: str):
    try:
        return learner_service.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/runs/read-all")
def mark_all_learner_runs_read():
    return {"status": "success", "updated": learner_service.mark_all_runs_read()}


@router.post("/runs/{run_id}/read")
def set_learner_run_read(run_id: str, req: dict):
    try:
        run = learner_service.set_run_read(run_id, bool((req or {}).get("is_read", True)))
        return {"status": "success", "run": run}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/runs/{run_id}/delete-preview")
def preview_learner_run_delete(run_id: str):
    try:
        return learner_service.preview_run_deletion([run_id])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/runs/{run_id}")
def delete_learner_run(run_id: str, confirmation_token: str = ""):
    try:
        result = learner_service.delete_runs([run_id], confirmation_token=confirmation_token)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result


@router.post("/runs/delete-preview")
def preview_clear_learner_runs(req: dict):
    try:
        return learner_service.preview_clear_runs((req or {}).get("scope", "read"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/runs")
def clear_learner_runs(scope: str = "read", confirmation_token: str = ""):
    try:
        return learner_service.delete_runs_by_confirmation(confirmation_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/memories/orphans/delete-preview")
def preview_orphaned_learner_memory_delete():
    try:
        return learner_service.preview_orphaned_memory_deletion()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/memories/orphans")
def delete_orphaned_learner_memories(confirmation_token: str = ""):
    try:
        return learner_service.delete_orphaned_memories(confirmation_token)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/tasks")
def create_learner_task(req: dict):
    try:
        return {"status": "success", "task": learner_service.create_task(req or {})}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}")
def update_learner_task(task_id: str, req: dict):
    try:
        return {"status": "success", "task": learner_service.update_task(task_id, req or {})}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/delete-preview")
def preview_learner_task_delete(task_id: str):
    try:
        return learner_service.preview_task_deletion(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/tasks/{task_id}")
def delete_learner_task(task_id: str, confirmation_token: str = ""):
    try:
        return learner_service.delete_task_with_history(
            task_id, confirmation_token=confirmation_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/tasks/{task_id}/run")
async def run_learner_task(task_id: str, req: dict):
    try:
        credentials = search_snapshot(dict(req or {}))
        credentials["_event_loop"] = asyncio.get_running_loop()
        run = learner_service.start_task(task_id, credentials=credentials, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not run:
        raise HTTPException(status_code=409, detail="This learning task is already running")
    return {"status": "success", "run": run}


@router.post("/start")
async def start_due_learner_tasks(req: dict):
    credentials = search_snapshot(dict(req or {}))
    credentials["_event_loop"] = asyncio.get_running_loop()
    return learner_service.start_due_tasks(credentials=credentials)
