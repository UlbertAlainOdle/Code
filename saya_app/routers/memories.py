from typing import Optional

from fastapi import APIRouter, HTTPException

from core_services import assistant_role_manager, kb_system, memory_manager


router = APIRouter(prefix="/api/kb", tags=["memories"])


@router.get("/memories")
def get_memories(include_archived: bool = False, category: Optional[str] = None):
    memories = kb_system.get_memories(include_archived=include_archived)
    if category:
        memories = [memory for memory in memories if memory.get("category") == category]
    return {"memories": memories}


@router.post("/search")
def search_memories(req: dict):
    return {
        "memories": kb_system.search_memories(
            query=req.get("query", ""),
            category=req.get("category") or None,
            include_archived=bool(req.get("include_archived", False)),
            limit=req.get("limit", 50),
        )
    }


@router.post("/rebuild_metadata")
def rebuild_memory_metadata():
    kb_system.normalize_all_memories()
    return {"status": "success", "count": len(kb_system.memories)}


@router.post("/add")
def add_memory(req: dict):
    content = req.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Content required")
    memory = kb_system.add_memory(
        content,
        title=req.get("title"),
        source=req.get("source", "manual"),
        is_important=bool(req.get("is_important", False)),
        category=req.get("category"),
        tags=req.get("tags"),
        confidence=req.get("confidence"),
        source_detail=req.get("source_detail"),
        metadata=req.get("metadata"),
    )
    return {"status": "success", "memory": memory}


@router.get("/settings")
def get_memory_settings():
    return kb_system.settings


@router.post("/settings")
def update_memory_settings(req: dict):
    return {"status": "success", "settings": kb_system.update_settings(req)}


@router.post("/merge")
def merge_memories(req: dict):
    days = req.get("days", kb_system.settings.get("auto_merge_days", 7))
    return kb_system.merge_memories(days)


@router.post("/update")
def update_memory(req: dict):
    mem_id = req.get("id")
    updates = req.get("updates")
    if kb_system.update_memory(mem_id, updates):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Memory not found")


@router.post("/delete")
def delete_memory(req: dict):
    if kb_system.delete_memory(req.get("id")):
        return {"status": "success"}
    return {"status": "error", "message": "Cannot delete locked memory"}


@router.get("/about_you/personality")
def get_about_you_personality():
    return kb_system.get_about_you_personality()


@router.post("/about_you/personality")
def save_about_you_personality(req: dict):
    return {"status": "success", **kb_system.save_about_you_personality_entry(req)}


@router.post("/about_you/personality/delete")
def delete_about_you_personality(req: dict):
    return {"status": "success", **kb_system.delete_about_you_personality_entry(req.get("id"))}


@router.get("/about_you/possessions")
def get_about_you_possessions():
    return kb_system.get_about_you_possessions()


@router.post("/about_you/possessions")
def save_about_you_possession(req: dict):
    return {
        "status": "success",
        **kb_system.save_about_you_possession(
            req.get("slot"),
            req.get("title"),
            req.get("content")
        )
    }


@router.post("/about_you/possessions/delete")
def delete_about_you_possession(req: dict):
    return {"status": "success", **kb_system.delete_about_you_possession(req.get("slot"))}


@router.get("/roles/assistant/state")
def get_assistant_role_state():
    return assistant_role_manager.get_state(memory_manager)


@router.post("/roles/assistant/read")
def read_assistant_role_vault(req: dict):
    try:
        return assistant_role_manager.read_vault(
            vault_path=req.get("vault_path"),
            detail=req.get("detail", "rough")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/assistant/browse")
def browse_assistant_role_vault(req: dict):
    try:
        return assistant_role_manager.browse_vault(
            vault_path=req.get("vault_path"),
            relative_path=req.get("relative_path", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/assistant/path-memory")
def add_assistant_role_path_memory(req: dict):
    try:
        return assistant_role_manager.add_path_memory(
            vault_path=req.get("vault_path"),
            relative_path=req.get("relative_path", ""),
            description=req.get("description", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/assistant/exit")
def exit_assistant_role():
    return {"status": "success", **assistant_role_manager.update_normal_mode_memory(kb_system)}
