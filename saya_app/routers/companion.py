"""Companion features: proactive echo, memory conflict negotiation, dream diary.

None of these share the streaming chat internals, so they live in a router
(backend-layout rule). Model calls go through the shared blocking invoke,
which books them into the API usage ledger.
"""

from fastapi import APIRouter, HTTPException,Request
from pydantic import BaseModel
from typing import Optional
from saya_app.services.memory_versions import MemoryVersionConflict

from core_services import emotion_engine, kb_system, log_to_file, memory_manager, project_manager
from saya_app.config import KNOWLEDGE_BASE_DIR
from saya_app.services import dream as dream_service
from saya_app.services import echo as echo_service
from saya_app.services import memory_conflicts
from saya_app.services import mood_continuity
from saya_app.services import conversation_mood
from saya_app.services.companion_invoke import blocking_model_invoke

router = APIRouter(prefix="/api/companion", tags=["companion"])
proactive = None

_BASE = str(KNOWLEDGE_BASE_DIR)

_COMPANION_SETTING_KEYS = [
    "mood_continuity_enabled",
    "echo_enabled", "echo_source_projects", "echo_source_dates", "echo_source_dream",
    "echo_daily_limit", "echo_quiet_start", "echo_quiet_end",
    "echo_project_stale_days", "echo_memory_stale_days", "echo_min_gap_minutes",
    "echo_source_context", "echo_min_idle_minutes", "echo_timezone",
    "dream_enabled", "dream_idle_hours", "dream_window_start", "dream_window_end",
    "conflict_negotiation_enabled", "conflict_daily_limit",
]

_SETTING_DEFAULTS = {
    "mood_continuity_enabled": True,
    "echo_enabled": False, "echo_source_projects": False, "echo_source_dates": False,
    "echo_source_dream": False, "echo_daily_limit": 1, "echo_quiet_start": 23,
    "echo_quiet_end": 8, "echo_project_stale_days": 3, "echo_memory_stale_days": 14,
    "echo_min_gap_minutes": 30,
    "echo_source_context":False,"echo_min_idle_minutes":30,"echo_timezone":"America/New_York",
    "dream_enabled": False, "dream_idle_hours": 2, "dream_window_start": 0,
    "dream_window_end": 8,
    "conflict_negotiation_enabled": True, "conflict_daily_limit": 2,
}


# ------------------------------------------------------------------ settings

@router.get("/settings")
def get_companion_settings():
    current = {key: kb_system.settings.get(key, _SETTING_DEFAULTS.get(key)) for key in _COMPANION_SETTING_KEYS}
    return {"settings": current}


@router.post("/settings")
def update_companion_settings(req: dict,request:Request=None):
    updates = req.get("settings") if isinstance(req, dict) else None
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="settings object required")
    clean = {key: value for key, value in updates.items() if key in _COMPANION_SETTING_KEYS}
    if proactive is not None and any(k.startswith('echo_') for k in clean):
        if request is not None:_proactive_local(request)
        from saya_app.services.proactive import ProactivePolicy
        mapping={'echo_enabled':'enabled','echo_source_context':'source_context','echo_source_projects':'source_projects','echo_source_dates':'source_dates','echo_source_dream':'source_dream',
            'echo_daily_limit':'daily_limit','echo_min_gap_minutes':'min_gap_minutes','echo_min_idle_minutes':'min_idle_minutes','echo_quiet_start':'quiet_start','echo_quiet_end':'quiet_end','echo_timezone':'timezone'}
        values=proactive.web_policy().model_dump()
        values.update({mapping[k]:v for k,v in clean.items() if k in mapping})
        try:ProactivePolicy.model_validate(values)
        except ValueError:raise HTTPException(400,'主动互动设置无效，请检查次数、时区和时段。') from None
    kb_system.update_settings(clean)
    return {"status": "success", "settings": {
        key: kb_system.settings.get(key, _SETTING_DEFAULTS.get(key)) for key in _COMPANION_SETTING_KEYS
    }}


# ------------------------------------------------------------------ echo

@router.get("/echo/poll")
def echo_poll():
    if proactive is not None:return {'message':None,'reason':'use_active_page_protocol'}
    result = echo_service.poll(
        _BASE, kb_system, project_manager,
        invoke=lambda prompt: blocking_model_invoke(prompt, max_tokens=120, temperature=0.7),
    )
    return result


@router.get("/echo/status")
def echo_status():
    if proactive is not None:
        return {'enabled':proactive.web_policy().enabled,'last_reason':proactive.last_reason,'delivery':proactive.store.summary(),
            'quiet':[proactive.web_policy().quiet_start,proactive.web_policy().quiet_end],'timezone':proactive.web_policy().timezone}
    return echo_service.status(_BASE, kb_system, project_manager)


def _proactive_local(request):
    from saya_app.routers.qq import local_origin
    if request.headers.get('x-saya-proactive')!='1' or not local_origin(request,require_origin=True):raise HTTPException(403,'仅允许本机当前页面操作主动互动。')
    if proactive is None:raise HTTPException(503,'主动互动服务尚未启动。')


class EchoPollRequest(BaseModel):
    client_id:str
    session_id:str
    visible:bool=True
    busy:bool=False


class EchoReceipt(BaseModel):
    client_id:str
    session_id:str
    offer_id:str
    token:str


@router.post('/echo/poll')
async def initiative_poll(values:EchoPollRequest,request:Request):
    _proactive_local(request)
    try:return await proactive.poll_web(values.client_id,values.session_id,values.visible,values.busy)
    except PermissionError:raise HTTPException(409,'另一主页面仍在使用，请回到原页面。') from None
    except ValueError:raise HTTPException(400,'请选择已保存的普通聊天会话。') from None


@router.post('/echo/presence')
async def initiative_presence(values:EchoPollRequest,request:Request):
    _proactive_local(request)
    try:proactive.heartbeat(values.client_id,values.session_id,values.visible,values.busy);return {'ready':True}
    except PermissionError:raise HTTPException(409,'另一主页面仍在使用。') from None
    except ValueError:raise HTTPException(400,'当前不是可用的普通会话。') from None


@router.post('/echo/commit')
def initiative_commit(values:EchoReceipt,request:Request):
    _proactive_local(request)
    try:return proactive.commit_web(values.offer_id,values.token,values.client_id,values.session_id)
    except PermissionError:raise HTTPException(403,'主动消息不属于当前页面。') from None
    except (ValueError,OSError):raise HTTPException(409,'话题、权限或页面已变化，本次不重复发送。') from None


@router.post('/echo/ack')
def initiative_ack(values:EchoReceipt,request:Request):
    _proactive_local(request)
    try:return proactive.ack_web(values.offer_id,values.token,values.client_id,values.session_id)
    except PermissionError:raise HTTPException(403,'主动消息不属于当前页面。') from None
    except ValueError:raise HTTPException(409,'主动消息已过期或页面变化。') from None


# ------------------------------------------------------------------ conflicts

@router.get("/conflicts")
def list_conflicts():
    memory_conflicts.prune_expired(_BASE)
    return {"conflicts": [
        memory_conflicts.card_payload(c) for c in memory_conflicts.pending(_BASE)
    ]}


class ConflictResolveRequest(BaseModel):
    conflict_id: str
    action: str
    expected_version: Optional[str] = None


@router.post("/conflicts/resolve")
def resolve_conflict(req: ConflictResolveRequest):
    try:
        result = memory_conflicts.resolve(_BASE, kb_system, req.conflict_id, req.action, expected_version=req.expected_version)
    except MemoryVersionConflict:
        raise HTTPException(409, '冲突或记忆已变化，请重新读取核对后裁决') from None
    except (ValueError, TypeError):
        raise HTTPException(400, '冲突版本参数无效') from None
    except OSError:
        raise HTTPException(503, '裁决提交结果待核对；请重新读取，勿重复执行') from None
    if result.get('status') in {'partial', 'unknown'}:
        raise HTTPException(503, result.get('message'))
    if result.get("status") != "success":
        raise HTTPException(status_code=400, detail=result.get("message") or "resolve failed")
    return result


# ------------------------------------------------------------------ dream

@router.get("/dream/status")
def dream_status():
    return dream_service.status(_BASE)


@router.get('/dream/result')
def dream_result(date: str):
    from ..services.dream_writes import readonly_result
    try:result=readonly_result(_BASE,date)
    except ValueError:raise HTTPException(400,'日记日期无效') from None
    if result is None:raise HTTPException(404,'该日期没有Dream操作记录')
    return result


class DreamRunRequest(BaseModel):
    date: str = ""       # YYYY-MM-DD; blank = yesterday
    force: bool = False  # bypass window/idle gating (acceptance runs)


@router.post("/dream/run")
def dream_run(req: DreamRunRequest):
    if req.date:
        from ..services.dream_writes import date_key
        try:date_key(req.date)
        except ValueError:raise HTTPException(400,'日记日期无效') from None
    if not req.force:
        ok, reason = dream_service.should_run(kb_system.settings, _BASE)
        if not ok:
            return {"status": "skipped", "reason": reason}
    # The theme step returns JSON; the diary step returns prose. The dream
    # service tags its prompts, so route the format flag accordingly.
    report = dream_service.run(
        _BASE, kb_system,
        lambda prompt: blocking_model_invoke(
            prompt, max_tokens=700, temperature=0.5,
            expect_json="Return JSON only" in prompt,
        ),
        target_date=req.date or None,
        authorized=lambda:req.force or bool(kb_system.settings.get("dream_enabled")),
    )
    return {"status": "ran" if report.get("ran") else "skipped", "report": report}


# ------------------------------------------------------------------ diary

@router.get("/diary")
def diary_list():
    return {"diaries": dream_service.list_diaries(_BASE)}


@router.get("/diary/{date}")
def diary_entry(date: str):
    try:data = dream_service.read_diary(_BASE, date)
    except ValueError:raise HTTPException(400,'日记日期无效') from None
    if not data:
        raise HTTPException(status_code=404, detail="no diary for this date")
    return data


# ------------------------------------------------------------------ mood

@router.get("/mood")
def mood_summary(session_id: str | None = None):
    if session_id is not None:
        state = conversation_mood.snapshot(memory_manager, session_id)
        return dict(state, locked=state["is_locked"], session_id=session_id, enabled=kb_system.settings.get("mood_continuity_enabled", True))
    enabled = kb_system.settings.get("mood_continuity_enabled", True)
    try:
        state = mood_continuity.snapshot(emotion_engine, enabled=enabled)
    except OSError as error:
        raise HTTPException(503, str(error))
    return dict(mood_continuity.summarize(state), enabled=enabled)


def check_conflict_for_memory(new_memory, origin):
    """Shared hook for the chat stream and the candidate-confirm endpoint.

    Returns a conflict card payload dict, or None. Blocking (one model call);
    callers already run inside worker threads / threadpool endpoints.
    """
    if not kb_system.settings.get("conflict_negotiation_enabled", True):
        return None
    if not new_memory or not isinstance(new_memory, dict):
        return None
    try:
        return memory_conflicts.verified_create(
            _BASE, kb_system,
            lambda prompt: blocking_model_invoke(prompt, max_tokens=200, temperature=0.0,
                                                 system_hint="Return only one JSON object.",
                                                 expect_json=True),
            new_memory,
            origin=origin,
            daily_limit=int(kb_system.settings.get("conflict_daily_limit", 2) or 2),
        )
    except Exception as exc:
        log_to_file(f"Conflict negotiation skipped: {exc}")
        return None
