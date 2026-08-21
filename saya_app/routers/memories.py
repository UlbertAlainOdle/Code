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
            memory_layer=req.get("memory_layer") or None,
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
    return {
        "status": "success",
        "memory": memory,
        "maintenance": kb_system.last_maintenance_result,
    }


@router.get("/candidates")
def get_memory_candidates(status: str = "pending", limit: int = 20):
    return {
        "candidates": kb_system.get_memory_candidates(
            status=status,
            limit=limit,
        )
    }


@router.post("/candidates/confirm")
def confirm_memory_candidate(req: dict):
    result = kb_system.confirm_memory_candidate(
        req.get("id"),
        req.get("updates") if isinstance(req.get("updates"), dict) else {},
    )
    if not result:
        raise HTTPException(status_code=404, detail="Memory candidate not found")
    if isinstance(result, dict):
        return {**result, "maintenance": kb_system.last_maintenance_result}
    return {
        "status": "success",
        "memory": result,
        "maintenance": kb_system.last_maintenance_result,
    }


@router.post("/candidates/ignore")
def ignore_memory_candidate(req: dict):
    if kb_system.ignore_memory_candidate(req.get("id")):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Memory candidate not found")


@router.get("/settings")
def get_memory_settings():
    return kb_system.settings


@router.get("/stats")
def get_memory_stats(context_window_tokens: Optional[int] = None):
    return kb_system.get_memory_stats(context_window_tokens=context_window_tokens)


@router.get("/maintenance/status")
def get_memory_maintenance_status():
    return kb_system.get_memory_maintenance_status()


@router.post("/maintenance/run")
def run_memory_maintenance():
    return kb_system.run_memory_capacity_maintenance(trigger="user_request")


@router.post("/maintenance/acknowledge")
def acknowledge_memory_maintenance(req: dict):
    return kb_system.acknowledge_memory_maintenance(req.get("event_id"))


@router.post("/ai-compression/preview")
def preview_ai_memory_compression(req: dict):
    if not kb_system.settings.get("memory_ai_compression_enabled", False):
        raise HTTPException(
            status_code=409,
            detail="AI memory compression is disabled in Mode Setting.",
        )
    try:
        return kb_system.build_ai_compression_preview(
            scope=req.get("scope", "pressure"),
            limit=req.get("limit", 50),
            project_id=req.get("project_id", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/cold-archive")
def get_cold_archive(limit: int = 50, search: str = ""):
    return {
        "memories": kb_system.list_cold_archive_memories(limit=limit, search=search),
        "count": kb_system.get_memory_maintenance_status().get("cold_archive_count", 0),
    }


@router.post("/cold-archive/restore")
def restore_cold_archive_memory(req: dict):
    memory = kb_system.restore_cold_archive_memory(req.get("id"))
    if not memory:
        raise HTTPException(status_code=404, detail="Cold-archived memory not found")
    return {
        "status": "success",
        "memory": memory,
        "maintenance": kb_system.last_maintenance_result,
    }


@router.get("/store/status")
def get_memory_store_status():
    return kb_system.get_sqlite_store_status()


@router.post("/store/rebuild")
def rebuild_memory_store():
    return kb_system.rebuild_sqlite_store()


@router.get("/graph/status")
def get_memory_graph_status():
    return kb_system.get_memory_graph_status()


@router.post("/graph/rebuild")
def rebuild_memory_graph():
    return kb_system.ensure_memory_graph(force=True)


@router.get("/graph/view")
def get_memory_graph_view(
    search: str = "",
    memory_layer: str = "",
    include_archived: bool = False,
    limit: int = 80,
    center_memory_id: str = "",
    relation: str = "",
):
    return kb_system.get_memory_graph_view(
        search=search,
        memory_layer=memory_layer,
        include_archived=include_archived,
        limit=limit,
        center_memory_id=center_memory_id,
        relation=relation,
    )


@router.get("/graph/neighbors/{memory_id}")
def get_memory_graph_neighbors(memory_id: str):
    result = kb_system.get_memory_graph_neighbors(memory_id)
    if not result.get("center"):
        raise HTTPException(status_code=404, detail="Memory not found in relationship graph")
    return result


@router.post("/graph/edges")
def add_memory_graph_edge(req: dict):
    try:
        edge = kb_system.add_memory_graph_edge(
            req.get("source_memory_id"),
            req.get("target_memory_id"),
            relation=req.get("relation", "related_to"),
            confidence=req.get("confidence", 0.9),
        )
        return {"status": "success", "edge": edge}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/graph/edges/remove")
def remove_memory_graph_edge(req: dict):
    if kb_system.remove_memory_graph_edge(req.get("edge_id")):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Manual relationship not found")


@router.get("/embeddings/status")
def get_embedding_status():
    return kb_system.get_embedding_status()


@router.post("/embeddings/index")
def build_embedding_index(req: dict):
    return kb_system.build_embedding_index(
        force=bool(req.get("force", False)),
        limit=req.get("limit"),
    )


@router.post("/settings")
def update_memory_settings(req: dict):
    settings = kb_system.update_settings(req)
    return {
        "status": "success",
        "settings": settings,
        "maintenance": kb_system.last_maintenance_result,
    }


@router.post("/merge")
def merge_memories(req: dict):
    days = req.get(
        "days",
        kb_system.settings.get(
            "memory_compression_age_days",
            kb_system.settings.get("auto_merge_days", 30),
        ),
    )
    result = kb_system.merge_memories(days)
    result["maintenance"] = kb_system.run_memory_capacity_maintenance(
        trigger="manual_merge",
    )
    return result


@router.post("/update")
def update_memory(req: dict):
    mem_id = req.get("id")
    updates = req.get("updates")
    try:
        if kb_system.update_memory(mem_id, updates):
            return {"status": "success"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=404, detail="Memory not found")


@router.post("/delete")
def delete_memory(req: dict):
    memory_id = req.get("id")
    memory = kb_system._find_memory(memory_id)
    allowed, reason = kb_system.memory_delete_policy(memory)
    if not allowed:
        return {"status": "error", "message": reason}
    confirmation_token = str(req.get("confirmation_token") or "")
    if not confirmation_token:
        return kb_system.preview_memory_deletion([memory_id], reason="user_delete")
    result = kb_system.delete_memories(
        [memory_id], reason="user_delete", confirmation_token=confirmation_token,
    )
    return result


@router.post("/delete/bulk")
def delete_memories_bulk(req: dict):
    memory_ids = req.get("ids")
    if not isinstance(memory_ids, list) or not memory_ids:
        raise HTTPException(status_code=400, detail="Select at least one memory")
    confirmation_token = str(req.get("confirmation_token") or "")
    if not confirmation_token:
        return kb_system.preview_memory_deletion(memory_ids, reason="user_bulk_delete")
    result = kb_system.delete_memories(
        memory_ids,
        reason="user_bulk_delete",
        confirmation_token=confirmation_token,
    )
    if result.get("status") != "success":
        raise HTTPException(status_code=500, detail=result.get("message") or "Unable to delete memories")
    return result


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
        state = assistant_role_manager.read_vault(
            vault_path=req.get("vault_path"),
            detail=req.get("detail", "rough")
        )
        state["normal_memory_sync"] = assistant_role_manager.update_normal_mode_memory(kb_system)
        return state
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


@router.post("/roles/assistant/note-outline")
def get_assistant_role_note_outline(req: dict):
    try:
        return assistant_role_manager.get_note_outline(
            vault_path=req.get("vault_path"),
            relative_path=req.get("relative_path", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/assistant/path-memory")
def add_assistant_role_path_memory(req: dict):
    try:
        state = assistant_role_manager.add_path_memory(
            vault_path=req.get("vault_path"),
            relative_path=req.get("relative_path", ""),
            description=req.get("description", ""),
        )
        state["normal_memory_sync"] = assistant_role_manager.update_normal_mode_memory(kb_system)
        return state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/assistant/extraction/remove")
def remove_assistant_role_extraction(req: dict):
    result = assistant_role_manager.remove_curated_extraction(kb_system, req.get("memory_id"))
    if not result.get("removed"):
        raise HTTPException(status_code=404, detail="Curated Obsidian memory not found")
    return {"status": "success", **result, **assistant_role_manager.get_state(memory_manager)}


@router.post("/roles/assistant/exit")
def exit_assistant_role():
    return {"status": "success", **assistant_role_manager.update_normal_mode_memory(kb_system)}
