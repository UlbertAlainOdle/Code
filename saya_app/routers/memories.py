import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core_services import assistant_role_manager, kb_system, memory_manager
from saya_app.services.memory_versions import record_version, MemoryVersionConflict, CandidateStateSaveError
from saya_app.services.memory_origin import web_operation, unknown_origin


router = APIRouter(prefix="/api/kb", tags=["memories"])

@router.get('/write-result')
def memory_write_result(operation_id: str, session_id: str = ''):
    import re
    from saya_app.services.chat_memory_writes import identity, journal, public_result
    from saya_app.services.memory_origin import bind_origin, ingress
    if (not re.fullmatch(r'[a-f0-9]{32}',operation_id) or session_id.startswith('session_qq_')
            or (session_id and not memory_manager._safe_session_path(session_id))):
        raise HTTPException(400,'保存操作或会话无效')
    with bind_origin(ingress('web',session_id=session_id)):
        _,scope=identity('capture',session_id)
    result=journal(kb_system).lookup(scope,session_id,operation_id,kind='candidate' if not session_id else None)
    if result is None:raise HTTPException(404,'当前会话没有这次保存操作')
    return public_result(result)


def versioned(record):
    from saya_app.services.memory_importance import view
    return {**record, **view(record), 'origin': record.get('origin') or unknown_origin(), 'content_version': record_version(record)}


@router.get('/health')
def memory_health(offset: int=0, limit: int=100):
    from saya_app.services.memory_health import inspect
    return inspect(kb_system,offset=offset,limit=limit)


@router.post('/health/repair')
def repair_memory_projection(req: dict):
    from saya_app.services.memory_health import repair_index
    from saya_app.services.qq_tasks import checkpoint_current
    try:
        checkpoint_current()
        if req.get('action')=='legacy_index':return repair_index(kb_system,req.get('id'),req.get('expected_version'))
        if req.get('action')=='native_projection' and req.get('id') and isinstance(req.get('expected_version'),str) and len(req['expected_version'])==64:
            return kb_system.reconcile_v4_projections(memory_id=req['id'],expected_version=req['expected_version'],limit=1,authorize=checkpoint_current)
        raise ValueError('invalid_repair')
    except MemoryVersionConflict:raise HTTPException(409,'版本或落点已变化，请重新检查；不要另存事实') from None
    except ValueError:raise HTTPException(400,'修复目标或版本不可用') from None
    except OSError:raise HTTPException(503,'投影修复尚未完成，请保留原条目') from None


@router.get('/health/reverse')
def reverse_memory_health(source:str='legacy', after_id:str='', limit:int=100, expected_version:Optional[str]=None):
    from saya_app.services.memory_health import reverse_inspect
    try:return reverse_inspect(kb_system,source=source,after_id=after_id,limit=limit,expected_version=expected_version)
    except MemoryVersionConflict:raise HTTPException(409,'原记忆清单已变化，请从头重新核对；未删除或修复') from None
    except ValueError:raise HTTPException(400,'反向检查参数不可用') from None


@router.get('/importance/preview')
def importance_preview():
    from saya_app.services.memory_importance import preview
    return preview(kb_system)


@router.get('/importance/policy')
def importance_policy():
    from saya_app.services.memory_importance import policy_view
    return policy_view(kb_system)


@router.put('/importance/policy')
def set_importance_policy(req: dict):
    from saya_app.services.memory_importance import set_policy
    try:return set_policy(kb_system,req.get('enabled'),req.get('expected_version'))
    except MemoryVersionConflict:raise HTTPException(409,'策略已变化，请重新读取') from None
    except ValueError:raise HTTPException(400,'策略不可用，原设置未改') from None


@router.post('/importance/initialize')
@web_operation('importance_migration')
def initialize_importance(req: dict):
    from pathlib import Path
    import sqlite3
    from saya_app.services.memory_importance import view,projections_match
    from saya_app.services.memory_versions import check_version
    try:
        with kb_system._memory_write_lock:
            item=kb_system._find_memory(req.get('id'));check_version(item,req.get('expected_version') or '')
            if not item:raise ValueError('not_found')
            if view(item)['importance_grade_initialized']:
                if not projections_match(kb_system,item):raise HTTPException(503,'重要级投影待核对，请勿重复迁移')
                return {'status':'unchanged','memory':versioned(item)}
            # Per-item, version-bound initialization never recalculates a score.
            backup=Path(kb_system.base_dir)/'backups'/'importance-v1'
            backup.mkdir(parents=True,exist_ok=True)
            import shutil
            for name in ('knowledge.json','memory_settings.json','memory_importance_policy.json'):
                path=Path(kb_system.base_dir)/name
                if path.exists() and not (backup/name).exists():shutil.copy2(path,backup/name)
            for name in ('memory3.sqlite3','memory4.sqlite3'):
                path=Path(kb_system.base_dir)/name
                if path.exists() and not (backup/name).exists():
                    with sqlite3.connect(path) as src,sqlite3.connect(backup/name) as dst:src.backup(dst)
            if not kb_system.update_memory(item['id'],{},expected_version=req.get('expected_version'),importance_reason='migration'):
                raise OSError('not_saved')
            if not projections_match(kb_system,item):raise HTTPException(503,'重要级已提交，投影待核对，请勿重复迁移')
            return {'status':'success','memory':versioned(kb_system._find_memory(item['id']))}
    except MemoryVersionConflict:raise HTTPException(409,'条目已变化，请重新预览') from None
    except ValueError:raise HTTPException(400,'条目或版本不可用') from None


@router.post('/revisions/preview')
@web_operation('edit')
def preview_memory_revision(req: dict):
    from saya_app.services.memory_revisions import revision_service
    from saya_app.services.qq_tasks import checkpoint_current
    try:
        service=revision_service(kb_system)
        content=req.get('content')
        if req.get('operation')=='restore':
            history=service.history(req.get('id'),project_id=req.get('project_id') or '')
            selected=next((x for x in history['items'] if x['revision']==req.get('revision')),None)
            if selected is None:raise ValueError('revision_not_available')
            content=selected['content']
        return service.preview(req.get('id'),req.get('operation'),content,
            expected_version=req.get('expected_version'),scope='web:memory-manager',session_id='memory-manager',
            project_id=req.get('project_id') or '',authorize=checkpoint_current)
    except MemoryVersionConflict:
        raise HTTPException(409,'记忆版本已变化，请保留草稿并重新预览') from None
    except (TypeError,ValueError):
        raise HTTPException(400,'目标、范围或修改内容不可用；未确认更新') from None


@router.post('/revisions/confirm')
@web_operation('revision_confirm')
def confirm_memory_revision(req: dict):
    from saya_app.services.memory_revisions import revision_service
    from saya_app.services.qq_tasks import checkpoint_current
    try:
        return revision_service(kb_system).confirm(req.get('confirmation_token'),scope='web:memory-manager',
            session_id='memory-manager',authorize=checkpoint_current)
    except ValueError:
        raise HTTPException(400,'修改服务或预览不可用，请重新读取核对') from None


@router.get('/revisions/history')
def memory_revision_history(id: str, project_id: str=''):
    from saya_app.services.memory_revisions import revision_service
    try:return revision_service(kb_system).history(id,project_id=project_id)
    except ValueError:raise HTTPException(404,'修改历史不可用') from None


def candidate_error(error):
    if isinstance(error, MemoryVersionConflict):
        raise HTTPException(409, '候选已变化，请重新读取后处理') from None
    if isinstance(error, CandidateStateSaveError):
        raise HTTPException(503, '记忆已保存，候选状态待核对；请重新读取，勿重复创建' if error.committed_memory_id
            else '候选状态未保存，请重新读取后重试') from None
    raise HTTPException(400, '候选版本参数无效') from None


@router.get("/memories")
def get_memories(include_archived: bool = False, category: Optional[str] = None):
    memories = kb_system.get_memories(include_archived=include_archived)
    if category:
        memories = [memory for memory in memories if memory.get("category") == category]
    return {"memories": [versioned(m) for m in memories]}


@router.post("/search")
def search_memories(req: dict):
    return {
        "memories": [versioned(m) for m in kb_system.search_memories(
            query=req.get("query", ""),
            category=req.get("category") or None,
            memory_layer=req.get("memory_layer") or None,
            include_archived=bool(req.get("include_archived", False)),
            limit=req.get("limit", 50),
        )]
    }


@router.post("/rebuild_metadata")
def rebuild_memory_metadata():
    kb_system.normalize_all_memories()
    return {"status": "success", "count": len(kb_system.memories)}


@router.post("/add")
@web_operation('explicit_save')
def add_memory(req: dict):
    from saya_app.services.manual_memory_writes import save
    try:return save(kb_system,req)
    except ValueError as error:raise HTTPException(400,str(error)) from None


@router.get('/manual-result')
@web_operation('manual_result')
def manual_memory_result(operation_id: str):
    import re
    from saya_app.services.chat_memory_writes import identity,journal,public_result
    if not re.fullmatch(r'[a-f0-9]{32}',operation_id):raise HTTPException(400,'保存操作编号无效')
    _,scope=identity('manual','')
    result=journal(kb_system).lookup(scope,'',operation_id,kind='manual')
    if result is None:raise HTTPException(404,'没有该手动保存操作')
    return public_result(result)


@router.get("/candidates")
@web_operation('candidate_read')
def get_memory_candidates(status: str = "pending", limit: int = 20):
    from saya_app.services.web_candidate_writes import pending_operation
    records=kb_system.get_memory_candidates(status=status,limit=limit)
    return {"candidates":[{**versioned(c),'confirmation_operation':pending_operation(kb_system,c)} for c in records]}


@router.post("/candidates/confirm")
@web_operation('candidate_confirm')
def confirm_memory_candidate(req: dict):
    try:
        from saya_app.services.web_candidate_writes import confirm
        operation,result=confirm(kb_system,req.get('id'),
            req.get('updates') if isinstance(req.get('updates'),dict) else {},req.get('expected_version'))
    except (MemoryVersionConflict, CandidateStateSaveError, ValueError) as error:
        candidate_error(error)
    item=operation.items[0]
    if item.get('error')=='version':candidate_error(MemoryVersionConflict())
    if item.get('status')=='not_found':
        raise HTTPException(status_code=404, detail="Memory candidate not found")
    if item['state'] in {'unknown','pending'}:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503,content={**result,'detail':'候选保存结果待核对，请核对原操作，勿重复创建。'})
    # Conflict negotiation: a confirmed candidate may contradict an existing
    # memory; the returned card lets the user retire the old one. Fail-open.
    conflict_card = None
    try:
        from saya_app.routers.companion import check_conflict_for_memory
        if operation.fresh and result.get('memory'):
            conflict_card = check_conflict_for_memory(result, "candidate")
    except Exception as conflict_error:
        from core_services import log_to_file
        log_to_file(f"Candidate conflict negotiation skipped: {conflict_error}")
    extra = {"conflict": conflict_card} if conflict_card else {}
    if isinstance(result, dict):
        return {**result, "maintenance": kb_system.last_maintenance_result, **extra}
    return {
        "status": "success",
        "memory": result,
        "maintenance": kb_system.last_maintenance_result,
        **extra,
    }


@router.post('/candidates/reconcile')
@web_operation('candidate_reconcile')
def reconcile_memory_candidate(req: dict):
    from saya_app.services.web_candidate_writes import reconcile
    try:operation,result=reconcile(kb_system,req.get('operation_id'))
    except ValueError:raise HTTPException(400,'候选操作编号无效') from None
    if operation is None:raise HTTPException(404,'没有对应的候选保存操作')
    return result


@router.post("/candidates/ignore")
def ignore_memory_candidate(req: dict):
    try:
        ignored = kb_system.ignore_memory_candidate(req.get("id"), expected_version=req.get('expected_version'))
    except (MemoryVersionConflict, CandidateStateSaveError, ValueError) as error:
        candidate_error(error)
    if ignored:
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Memory candidate not found")


@router.get("/settings")
def get_memory_settings():
    return kb_system.settings


@router.get("/v4/status")
def get_memory_v4_status():
    return kb_system.get_memory_v4_status()


@router.get("/v4/capabilities")
def get_memory_v4_capabilities():
    return kb_system.memory_v4_capabilities()


@router.post("/v4/query")
def query_memory_v4(req: dict):
    return kb_system.query_memory_v4(
        req.get("query", ""),
        depth=req.get("depth", "normal"),
        scope_type=req.get("scope_type"),
        scope_id=req.get("scope_id"),
        turn_id=req.get("turn_id"),
        max_candidates=req.get("max_candidates", 80),
        max_items=req.get("max_items", 8),
        token_budget=req.get("token_budget", 1200),
        include_expired=bool(req.get("include_expired", False)),
        allow_do_not_mention=bool(req.get("allow_do_not_mention", False)),
    )


@router.get("/v4/daily")
def get_memory_v4_daily_state(day: str = "", action: str = "ring"):
    return kb_system.memory_v4_daily_state(action=action, day=day or None)


@router.post("/v4/daily")
def update_memory_v4_daily_state(req: dict):
    return kb_system.memory_v4_daily_state(
        action=req.get("action", "conversation"),
        day=req.get("day"),
        tasks=req.get("tasks") if isinstance(req.get("tasks"), list) else [],
        timezone=req.get("timezone", "Asia/Shanghai"),
    )


@router.post("/v4/synthesis/run")
def run_memory_v4_synthesis(req: dict):
    """Trigger one bounded pass using only the server-configured producer."""
    return kb_system.run_memory_v4_synthesis_once(
        limit=req.get("limit", 10),
        actor=req.get("actor", "background_synthesis"),
    )


@router.post("/v4/synthesis/schedule")
def schedule_memory_v4_synthesis(req: dict):
    return kb_system.schedule_memory_v4_synthesis(
        limit=req.get("limit", 10),
        actor=req.get("actor", "background_synthesis"),
    )


@router.get("/v4/synthesis/status")
def get_memory_v4_synthesis_status():
    return kb_system.memory_v4_synthesis_status()


@router.post("/v4/agent/run")
def run_memory_v4_agent(req: dict):
    """Run one bounded step loop using the server-registered model adapter."""
    return kb_system.run_memory_v4_agent_once(
        req.get("turn_id") or "memory-v4-agent-turn",
        initial_context=req.get("initial_context") if isinstance(req.get("initial_context"), list) else [],
        policy_options=req.get("policy") if isinstance(req.get("policy"), dict) else {},
    )


@router.post("/v4/agent/cancel")
def cancel_memory_v4_agent(req: dict):
    return kb_system.cancel_memory_v4_agent(req.get("turn_id"))


@router.get("/v4/agent/events")
def get_memory_v4_agent_events(turn_id: str, after_sequence: int = -1, limit: int = 100):
    """Return public, cursor-addressable Agent Loop events for reconnects."""
    return kb_system.memory_v4_events(turn_id, after_sequence=after_sequence, limit=limit)


@router.post("/v4/agent/stream")
async def stream_memory_v4_agent(req: dict):
    """Replay a server-configured Agent Loop as public SSE events.

    The HTTP request can provide conversation context, but cannot provide an
    API URL, credential, callable, or arbitrary provider. The application must
    register ``kb_system.memory_v4_model_step`` during server configuration.
    """
    if not kb_system.settings.get("agent_loop_enabled", False):
        raise HTTPException(status_code=409, detail="agent_loop_enabled is false")
    if not callable(getattr(kb_system, "memory_v4_model_step", None)):
        kb_system.configure_memory_v4_model_from_env()
    if not callable(getattr(kb_system, "memory_v4_model_step", None)):
        raise HTTPException(status_code=409, detail="memory_v4_model_step is not configured")
    context = req.get("initial_context") if isinstance(req.get("initial_context"), list) else []
    policy_options = req.get("policy") if isinstance(req.get("policy"), dict) else {}
    policy = kb_system.memory_v4_agent_policy(policy_options)
    turn_id = str(req.get("turn_id") or "memory-v4-agent-turn")
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def publish(event):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, ("event", event))
        except RuntimeError:
            # The client disconnected while the worker was finishing.
            pass

    async def run_worker():
        try:
            result = await asyncio.to_thread(
                kb_system.run_memory_v4_agent_loop,
                turn_id,
                kb_system.memory_v4_model_step,
                initial_context=context,
                policy=policy,
                event_sink=publish,
            )
            await queue.put(("result", result))
        except Exception as exc:
            await queue.put(("error", {"status": "error", "reason": str(exc)[:240]}))
        finally:
            await queue.put(("done", None))

    worker = asyncio.create_task(run_worker())

    async def event_generator():
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "event":
                    yield "event: agent_event\n" + f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                elif kind == "result":
                    public_result = {key: value for key, value in payload.items() if key != "events"}
                    yield "event: agent_result\n" + f"data: {json.dumps(public_result, ensure_ascii=False)}\n\n"
                elif kind == "error":
                    yield "event: agent_result\n" + f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                elif kind == "done":
                    break
        except asyncio.CancelledError:
            kb_system.cancel_memory_v4_agent(turn_id)
            raise
        finally:
            if not worker.done():
                kb_system.cancel_memory_v4_agent(turn_id)
                worker.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.get("/v4/connectors/status")
def get_memory_v4_connector_status():
    configured = kb_system.configure_memory_v4_connectors_from_env()
    connector = getattr(kb_system, "memory_v4_connector", None)
    return {
        "status": "ready",
        "configured": configured.get("configured", []),
        "active_source": str(getattr(connector, "source", "") or "") or None,
        "read_only": bool(connector is not None and not hasattr(connector, "send")),
    }


@router.get("/v4/controls/summary")
def get_memory_v4_control_summary(limit: int = 100):
    return kb_system.memory_v4_controls_summary(limit=limit)


@router.post("/v4/controls")
@web_operation('edit')
def apply_memory_v4_control(req: dict):
    try:
        result = kb_system.memory_v4_control(
            req.get("operation"),
            memory_id=req.get("memory_id") or req.get("id"),
            updates=req.get("updates") if isinstance(req.get("updates"), dict) else {},
            revision=req.get("revision"),
            turn_id=req.get("turn_id"),
            actor=req.get("actor", "user"),
            enabled=req.get("enabled", True),
            expected_version=req.get("expected_version"),
        )
    except MemoryVersionConflict:
        raise HTTPException(409, '记忆已变化，请重新读取后操作') from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="V4 memory not found")
    if result.get("status") == "rejected":
        raise HTTPException(status_code=400, detail=result.get("reason", "Invalid V4 memory control"))
    if result.get('status') == 'partial':
        raise HTTPException(503, result.get('reason') or '部分提交，请重新读取核对，勿重复执行')
    return result


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
            expected_graph_version=req.get('expected_graph_version'),
        )
        return {"status": "success", "edge": edge}
    except MemoryVersionConflict:
        raise HTTPException(409, '关系或记忆已变化，请重新读取后操作') from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/graph/edges/remove")
def remove_memory_graph_edge(req: dict):
    try:
        if kb_system.remove_memory_graph_edge(req.get("edge_id"), expected_graph_version=req.get('expected_graph_version')):
            return {"status": "success"}
    except MemoryVersionConflict:
        raise HTTPException(409, '关系或记忆已变化，请重新读取后操作') from None
    except ValueError:
        raise HTTPException(400, '关系版本参数无效') from None
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
@web_operation('edit')
def update_memory(req: dict):
    mem_id = req.get("id")
    updates = req.get("updates")
    try:
        with kb_system._memory_write_lock:
            if kb_system.update_memory(mem_id, updates, expected_version=req.get('expected_version')):
                return {"status": "success", "content_version": record_version(kb_system._find_memory(mem_id))}
    except MemoryVersionConflict:
        raise HTTPException(409, '记忆已变化，请重新读取后修改') from None
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
        try:
            return kb_system.preview_memory_deletion([memory_id], reason="user_delete",
                expected_versions={memory_id:req['expected_version']} if req.get('expected_version') is not None else None)
        except MemoryVersionConflict:
            raise HTTPException(409, '记忆已变化，请重新核对删除对象') from None
        except ValueError:
            raise HTTPException(400, '删除版本参数无效') from None
    try:
        result = kb_system.delete_memories(
            [memory_id], reason="user_delete", confirmation_token=confirmation_token,
            expected_versions={memory_id:req['expected_version']} if req.get('expected_version') is not None else None,
        )
    except MemoryVersionConflict:
        raise HTTPException(409, '记忆已变化，请重新核对删除对象') from None
    except ValueError:
        raise HTTPException(400, '删除版本参数无效') from None
    return result


@router.post("/delete/bulk")
def delete_memories_bulk(req: dict):
    memory_ids = req.get("ids")
    if not isinstance(memory_ids, list) or not memory_ids:
        raise HTTPException(status_code=400, detail="Select at least one memory")
    confirmation_token = str(req.get("confirmation_token") or "")
    versions = req.get('expected_versions')
    if versions is not None and not isinstance(versions, dict):
        raise HTTPException(400, '删除版本参数无效')
    try:
        if not confirmation_token:
            return kb_system.preview_memory_deletion(memory_ids, reason="user_bulk_delete", expected_versions=versions)
        result = kb_system.delete_memories(
            memory_ids, reason="user_bulk_delete", confirmation_token=confirmation_token,
            expected_versions=versions,
        )
    except MemoryVersionConflict:
        raise HTTPException(409, '记忆已变化，请重新核对删除对象') from None
    except ValueError:
        raise HTTPException(400, '删除版本参数无效') from None
    if result.get("status") not in {"success", "confirmation_required"}:
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
