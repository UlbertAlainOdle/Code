from fastapi import APIRouter, HTTPException

from core_services import project_manager


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

