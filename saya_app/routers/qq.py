"""Authenticated OneBot ingress; public status contains no IDs or message text."""

import hmac
import json
import ipaddress
import secrets
import time
import sqlite3
import asyncio
import re
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse,Response
from pydantic import BaseModel, Field, StrictInt, StrictBool

from saya_app.services.qq_bridge import qq_bridge
from saya_app.services.qq_controls import QQControlsUpdate, QQControlsBusy, QQControlsConflict, QQControlsUnsupported
from saya_app.services.qq_memory_access import MemoryActor, MemoryAccessDenied, MemoryAccessConflict
from saya_app.config import KNOWLEDGE_BASE_DIR
BROWSER_ARCHIVE_PATH=KNOWLEDGE_BASE_DIR/'browser_settings.json'

router = APIRouter(prefix="/api/qq", tags=["qq"])
MAX_EVENT_BYTES = 128 * 1024
_ui_sessions = {}
UI_COOKIE = "saya_qq_controls"


def local_origin(request, require_origin=False):
    try:
        peer_local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        return False
    if not peer_local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    origin = request.headers.get("origin")
    if not origin:
        return not require_origin
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    except ValueError:
        return False
    return (parsed.scheme == request.url.scheme and parsed.hostname == request.url.hostname
            and origin_port == target_port and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and parsed.path in {"", "/"})


@router.post("/ui-session")
async def ui_session(request: Request):
    # Same-origin POST + custom header + loopback Host/peer prevents websites
    # and localhost cross-port pages from bootstrapping a management session.
    if request.headers.get("x-saya-qq-ui") != "1" or not local_origin(request, require_origin=True):
        raise HTTPException(403, "QQ 设置仅允许从本机 Saya 网页打开")
    now = time.monotonic()
    for token, expires in list(_ui_sessions.items()):
        if expires <= now:
            _ui_sessions.pop(token, None)
    existing = request.cookies.get(UI_COOKIE)
    token = existing if existing in _ui_sessions else secrets.token_urlsafe(32)
    if len(_ui_sessions) >= 64 and token not in _ui_sessions:
        raise HTTPException(429, "本地设置会话过多，请稍后重试")
    _ui_sessions[token] = now + 3600
    response = JSONResponse({"ready": True})
    response.set_cookie(UI_COOKIE, token, httponly=True, samesite="strict", max_age=3600,
                        secure=request.url.scheme == "https", path="/api/qq")
    return response


@router.get("/status")
def status():
    return qq_bridge.status()


@router.post("/events")
async def events(request: Request):
    if not qq_bridge.settings or qq_bridge.config_error:
        raise HTTPException(503, "QQ bridge is disabled or unconfigured")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_EVENT_BYTES:
            raise HTTPException(413, "Event too large")
    if not qq_bridge.authenticate(bytes(body), request.headers.get("authorization"), request.headers.get("x-signature")):
        raise HTTPException(401, "Invalid webhook authentication")
    if not qq_bridge.settings.enabled:
        return {"status": "disabled", "reason": "paused_in_settings"}
    try:
        event = json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid JSON event") from None
    result, reason = qq_bridge.submit_signed(bytes(body), request.headers.get('authorization'), request.headers.get('x-signature'))
    if result in {"busy", "unavailable"}:
        raise HTTPException(503, "QQ queue unavailable; retry later", headers={"Retry-After": "5"})
    # Never put a quick-operation `reply` in this ACK: generation is queued.
    return {"status": result, "reason": reason}


def require_admin(request, require_running=True):
    settings = qq_bridge.settings
    bearer_ok = bool(settings and settings.admin_token and hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + settings.admin_token))
    cookie_ok = (_ui_sessions.get(request.cookies.get(UI_COOKIE), 0) > time.monotonic()
                 and local_origin(request, require_origin=request.method not in {"GET", "HEAD"}))
    if not (bearer_ok or cookie_ok):
        raise HTTPException(401, "Configure and provide SAYA_QQ_ADMIN_TOKEN")
    if require_running and (not qq_bridge.task or qq_bridge.task.done()):
        raise HTTPException(503, "QQ bridge is not running")


@router.get("/settings")
async def get_settings(request: Request):
    require_admin(request, require_running=False)
    try:
        return qq_bridge.controls()
    except ValueError:
        raise HTTPException(400, "QQ 连接配置有误，请先检查本地账号和连接配置") from None


@router.put("/settings")
async def save_settings(values: QQControlsUpdate, request: Request):
    require_admin(request, require_running=False)
    if values.revision is None:
        raise HTTPException(409, "设置接口已更新，请刷新网页并重新读取后再保存。")
    if values.inherit_fields is None and qq_bridge.controls()["inherit_fields"]:
        raise HTTPException(409, "QQ 已启用设置继承，请刷新网页后再修改。")
    try:
        # Older pages know only the previous fields. A fresh revision does not
        # authorize their omitted new options to reset to schema defaults.
        patch = values.model_dump(exclude={"revision", "inherit_fields"}, exclude_unset=True)
        return await qq_bridge.update_controls(qq_bridge.controls()['settings'] | patch,
            values.revision, inherit_fields=values.inherit_fields)
    except (QQControlsBusy, QQControlsConflict) as exc:
        raise HTTPException(409, str(exc)) from None
    except QQControlsUnsupported as exc:
        raise HTTPException(400, str(exc)) from None
    except ValueError:
        raise HTTPException(400, "设置未保存，请检查主人账号、连接配置及参数范围") from None


@router.post("/diagnostics")
async def diagnostics(request: Request):
    require_admin(request)
    return await qq_bridge.diagnostics()


@router.get("/health")
async def health(request: Request):
    require_admin(request, require_running=False)
    from saya_app.services.conversation import conversation_service
    from saya_app.services.qq_bridge import OneBotClient
    from saya_app.services.qq_health import inspect_health
    from saya_app.config import PROJECT_ROOT
    from saya_app.services.qq_runtime_status import runtime_status
    from saya_app.services.qq_napcat_login_health import napcat_login_probe
    from saya_app.services.qq_network_health import public_network_probe
    result = await inspect_health(qq_bridge, conversation_service.credentials(), OneBotClient,
                                  login_probe=napcat_login_probe.probe, network_probe=public_network_probe.probe)
    result['service'] = runtime_status(PROJECT_ROOT)
    return result


class NapcatPolicyUpdate(BaseModel):
    enabled: bool
    revision: str


@router.get('/service')
async def service_status(request: Request):
    require_admin(request, require_running=False)
    from saya_app.config import PROJECT_ROOT
    from saya_app.services.qq_runtime_status import runtime_status
    return runtime_status(PROJECT_ROOT)


@router.put('/service/napcat-supervision')
async def napcat_supervision(values: NapcatPolicyUpdate, request: Request):
    require_admin(request, require_running=False)
    if not local_origin(request, require_origin=True):
        raise HTTPException(403, '进程守护开关仅允许在本机同源网页修改')
    from saya_app.config import PROJECT_ROOT
    from saya_app.services.qq_napcat import NapcatSupervisor
    try:
        return NapcatSupervisor(PROJECT_ROOT).policy.save(values.enabled, values.revision)
    except QQControlsConflict as error:
        raise HTTPException(409, str(error)) from None
    except ValueError:
        raise HTTPException(400, 'NapCat守护设置损坏，请检查本机配置') from None


class DrainUpdate(BaseModel):
    draining: bool


def context_service(request,owner_id):
    require_admin(request)
    bridge=qq_bridge
    if not bridge.settings or owner_id not in bridge.settings.owner_ids:
        raise HTTPException(403,'只能为已配置主人选择私人网页会话')
    links=getattr(bridge,'context_links',None)
    if links is None:raise HTTPException(503,'网页话题接续尚未准备好')
    return bridge,links,links.scope(bridge.settings.bot_id,owner_id)


@router.get('/context-links')
async def context_links(request:Request,owner_id:str=''):
    require_admin(request)
    owners=sorted(qq_bridge.settings.owner_ids)
    owner_id=owner_id or (owners[0] if len(owners)==1 else '')
    if not owner_id:return {'owners':owners,'owner_id':'','state':None,'sessions':[],'revision':qq_bridge.controls()['revision']}
    bridge,links,scope=context_service(request,owner_id)
    import re
    sessions=[{'id':x['id'],'preview':str(x.get('preview') or '')[:80]} for x in bridge.context_sessions()
              if re.fullmatch(r'session_[A-Za-z0-9_-]+\.jsonl',x['id'])
              and not x['id'].startswith(('session_qq_','session_mc_')) and x['id']!='session_saya_unified.jsonl']
    return {'owners':owners,'owner_id':owner_id,'state':links.state(scope),'sessions':sessions,'revision':bridge.controls()['revision']}


class ContextLinkUpdate(BaseModel):
    owner_id:str
    generation:int
    revision:str
    session_id:str=''
    action:str


def memory_management_service(request, owner_id=''):
    require_admin(request)
    bridge = qq_bridge
    owners = sorted(bridge.settings.owner_ids)
    owner_id = owner_id or (owners[0] if len(owners) == 1 else '')
    if owner_id not in owners:
        raise HTTPException(403, '请选择已配置的主人账号')
    service = getattr(bridge, 'memory_management', None)
    if service is None:
        raise HTTPException(503, '记忆管理尚未准备好')
    return service, MemoryActor(bridge.settings.bot_id, owner_id, True, True), owners


def daily_memory_service(request,owner_id=''):
    _,actor,_=memory_management_service(request,owner_id)
    service=getattr(qq_bridge,'daily_memory',None)
    if service is None:raise HTTPException(503,'记忆操作台尚未准备好')
    return service,actor.user_id


def daily_error(error):
    if isinstance(error,MemoryAccessDenied):raise HTTPException(403,'当前账号或保存权限不可用') from None
    if isinstance(error,(OSError,sqlite3.Error)):raise HTTPException(503,'结果待核对，请刷新原操作；不要重复新建') from None
    raise HTTPException(409,'内容、来源或版本已变化，或尚未到日终；请重新读取预览') from None


class DailyPolicyUpdate(BaseModel):
    owner_id:str
    revision:StrictInt
    enabled:StrictBool


class DailyDecision(BaseModel):
    owner_id:str
    id:str=Field(min_length=32,max_length=32,pattern='^[a-f0-9]+$')
    preview_version:str=Field(min_length=64,max_length=64,pattern='^[a-f0-9]+$')
    action:Literal['approve','reject']


@router.get('/memory-console')
async def daily_listing(request:Request,owner_id:str=''):
    service,owner=daily_memory_service(request,owner_id)
    return await asyncio.to_thread(service.listing,owner)


@router.get('/memory-console/accounts')
async def console_accounts(request:Request):
    require_admin(request)
    return JSONResponse({'owners':sorted(qq_bridge.settings.owner_ids)},headers={'Cache-Control':'no-store'})


@router.put('/memory-console/policy')
async def daily_policy(values:DailyPolicyUpdate,request:Request):
    service,owner=daily_memory_service(request,values.owner_id)
    try:return await asyncio.to_thread(service.configure,owner,values.revision,values.enabled)
    except (ValueError,MemoryAccessDenied,OSError,sqlite3.Error) as e:daily_error(e)


@router.get('/memory-console/preview/{identifier}')
async def daily_preview(identifier:str,request:Request,owner_id:str=''):
    service,owner=daily_memory_service(request,owner_id)
    try:return await asyncio.to_thread(service.preview,owner,identifier)
    except (ValueError,MemoryAccessDenied,OSError,sqlite3.Error) as e:daily_error(e)


@router.post('/memory-console/decision')
async def daily_decision(values:DailyDecision,request:Request):
    service,owner=daily_memory_service(request,values.owner_id)
    try:return await asyncio.to_thread(service.decide,owner,values.id,values.preview_version,values.action)
    except (ValueError,MemoryAccessDenied,OSError,sqlite3.Error) as e:daily_error(e)


class DailyConsoleCommand(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    text:str=Field(min_length=1,max_length=400)


def explicit_console_service(request,owner_id=''):
    management,actor,_=memory_management_service(request,owner_id)
    service=getattr(management,'console',None)
    if service is None:raise HTTPException(503,'保存待办尚未准备好')
    return service,management,actor


class ConsoleSaveDecision(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    id:str=Field(pattern=r'^[a-f0-9]{32}$')
    preview_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    action:Literal['approve','reject','ignore']


class ConsoleSaveSelection(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    id:str=Field(pattern=r'^[a-f0-9]{32}$')
    preview_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    indices:list[StrictInt]=Field(min_length=1,max_length=4)


class ConsoleCandidatePreview(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    candidate_id:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.-]+$')
    content_version:str=Field(pattern=r'^[a-f0-9]{64}$')


@router.get('/memory-console/saves')
async def console_saves(request:Request,owner_id:str=''):
    service,_,actor=explicit_console_service(request,owner_id)
    return JSONResponse(await asyncio.to_thread(service.listing,actor.user_id),headers={'Cache-Control':'no-store'})


@router.get('/memory-console/saves/{identifier}')
async def console_save_preview(identifier:str,request:Request,owner_id:str=''):
    service,_,actor=explicit_console_service(request,owner_id)
    try:return JSONResponse(await asyncio.to_thread(service.preview,actor.user_id,identifier),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/save-select')
async def console_save_select(values:ConsoleSaveSelection,request:Request):
    service,_,actor=explicit_console_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台选择来源')
    try:return JSONResponse(await asyncio.to_thread(service.select,actor.user_id,values.id,values.preview_version,values.indices),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/save-decision')
async def console_save_decision(values:ConsoleSaveDecision,request:Request):
    service,_,actor=explicit_console_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台预览并确认')
    try:return JSONResponse(await asyncio.to_thread(service.decide,actor.user_id,values.id,values.preview_version,values.action),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.get('/memory-console/candidates')
async def console_candidates(request:Request,owner_id:str='',query:str='',page:int=1,snapshot:str=''):
    _,management,actor=explicit_console_service(request,owner_id)
    try:return JSONResponse(await asyncio.to_thread(management.browse,actor,kind='candidates',query=query,page=page,snapshot=snapshot),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/candidate-preview')
async def console_candidate_preview(values:ConsoleCandidatePreview,request:Request):
    service,_,actor=explicit_console_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台选择候选')
    try:return JSONResponse(await asyncio.to_thread(service.candidate,actor.user_id,values.candidate_id,values.content_version),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


class ConsoleOperationPreview(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    memory_id:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.:-]+$')
    content_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    action:Literal['delete','block','unblock']


class ConsolePairPreview(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    kind:Literal['link','archive']
    first:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.:-]+$')
    second:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.:-]+$')
    first_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    second_version:str=Field(pattern=r'^[a-f0-9]{64}$')


class ConsoleActionDecision(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    id:str=Field(pattern=r'^[a-f0-9]{32}$')
    preview_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    action:Literal['confirm','undo','cancel']


class ConsoleEventDecision(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    id:str=Field(pattern=r'^[a-f0-9]{32}$')
    preview_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    action:Literal['confirm','reject']


class LibraryScan(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    snapshot:str=Field(pattern=r'^[a-f0-9]{64}$')
    cursor:StrictInt=Field(ge=0)


class LibraryPrepare(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    id:str=Field(pattern=r'^[a-f0-9]{32}$')
    preview_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    keep:Literal['first','second']


class OverlapPrepare(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    id:str=Field(pattern=r'^[a-f0-9]{32}$')
    preview_version:str=Field(pattern=r'^[a-f0-9]{64}$')


def library_service(request,owner):
    management,actor,_=memory_management_service(request,owner)
    if not management.library:raise HTTPException(503,'全库审阅尚未准备好')
    return management.library,actor.user_id


@router.post('/memory-console/library-overlap')
async def library_overlap(values:OverlapPrepare,request:Request):
    management,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台核对完整内容')
    if not management.overlap:raise HTTPException(503,'重合整理尚未准备好')
    try:return JSONResponse(await management.overlap.prepare_saved(actor.user_id,values.id,values.preview_version),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:overlap_error(e)


@router.post('/memory-console/daily-overlap')
async def daily_overlap(values:OverlapPrepare,request:Request):
    management,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台核对每日稿')
    if not management.overlap:raise HTTPException(503,'重合整理尚未准备好')
    try:return JSONResponse(await management.overlap.prepare_daily(actor.user_id,values.id,values.preview_version),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:overlap_error(e)


def overlap_error(error):
    if str(error) in {'overlap_no_complete_match','overlap_no_residual','overlap_incomplete_checks'}:
        raise HTTPException(409,'未找到可安全移除的完整重合句段，原文保持；可以继续人工核对。') from None
    if str(error) in {'overlap_input_budget','overlap_anchor_budget','overlap_unit_budget'}:
        raise HTTPException(409,'完整原文超过本次整理预算，没有截断或修改；可按原编号手动编辑。') from None
    memory_management_error(error)


@router.post('/memory-console/daily-overlap-cancel')
async def daily_overlap_cancel(values:OverlapPrepare,request:Request):
    management,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台核对每日稿')
    if not management.overlap:raise HTTPException(503,'重合整理尚未准备好')
    try:return JSONResponse(await asyncio.to_thread(management.overlap.cancel_daily,actor.user_id,values.id,values.preview_version),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:overlap_error(e)


@router.get('/memory-console/library')
async def library_status(request:Request,owner_id:str=''):
    management,actor,_=memory_management_service(request,owner_id)
    if not management.library:return JSONResponse({'available':False,'entries':[]},headers={'Cache-Control':'no-store'})
    try:return JSONResponse(await asyncio.to_thread(management.library.status,actor.user_id),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.get('/memory-console/reads')
async def native_read_list(request:Request,owner_id:str=''):
    management,actor,_=memory_management_service(request,owner_id)
    if not management.reads:return JSONResponse({'available':False,'entries':[]},headers={'Cache-Control':'no-store'})
    try:return JSONResponse(await asyncio.to_thread(management.reads.listing,actor.user_id),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.get('/memory-console/reads/{identifier}')
async def native_read_result(identifier:str,request:Request,owner_id:str=''):
    management,actor,_=memory_management_service(request,owner_id)
    if not management.reads:raise HTTPException(503,'查询操作台尚未准备好')
    try:return JSONResponse(await asyncio.to_thread(management.reads.read,actor.user_id,identifier),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.get('/memory-console/library/{identifier}')
async def library_preview(identifier:str,request:Request,owner_id:str=''):
    service,owner=library_service(request,owner_id)
    try:return JSONResponse(await asyncio.to_thread(service.preview,owner,identifier),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/library-scan')
async def library_scan(values:LibraryScan,request:Request):
    service,owner=library_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台开始语义审阅')
    try:return JSONResponse(await service.scan(owner,values.snapshot,values.cursor),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/library-prepare')
async def library_prepare(values:LibraryPrepare,request:Request):
    service,owner=library_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台核对两条完整正文')
    try:return JSONResponse(await asyncio.to_thread(service.prepare,owner,values.id,values.preview_version,values.keep),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


def event_console_service(request,owner_id):
    require_admin(request)
    episodes=getattr(qq_bridge,'episodes',None)
    service=getattr(getattr(episodes,'operations',None),'console',None)
    if not service:raise HTTPException(503,'事件操作台尚未准备好')
    try:service.owner(owner_id)
    except PermissionError:raise HTTPException(403,'只能核对已配置主人自己的私聊操作')
    return service


@router.get('/memory-console/events')
async def console_event_list(request:Request,owner_id:str=''):
    require_admin(request)
    service=getattr(getattr(getattr(qq_bridge,'episodes',None),'operations',None),'console',None)
    if not service:return JSONResponse({'available':False,'entries':[]},headers={'Cache-Control':'no-store'})
    service=event_console_service(request,owner_id)
    return JSONResponse(await asyncio.to_thread(service.listing,owner_id),headers={'Cache-Control':'no-store'})


@router.get('/memory-console/events/{identifier}')
async def console_event_preview(identifier:str,request:Request,owner_id:str=''):
    service=event_console_service(request,owner_id)
    try:
        await service.refresh_groups(owner_id,identifier)
        return JSONResponse(await asyncio.to_thread(service.preview,owner_id,identifier),headers={'Cache-Control':'no-store'})
    except PermissionError:raise HTTPException(403,'事件操作权限已变化')
    except (ValueError,LookupError,OSError,sqlite3.Error):raise HTTPException(409,'事件或来源已变化，请重新读取核对')


@router.post('/memory-console/event-decision')
async def console_event_decide(values:ConsoleEventDecision,request:Request):
    service=event_console_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台核对后决定')
    try:
        await service.refresh_groups(values.owner_id,values.id)
        return JSONResponse(await asyncio.to_thread(service.decide,values.owner_id,values.id,values.preview_version,values.action),headers={'Cache-Control':'no-store'})
    except PermissionError:raise HTTPException(403,'事件操作权限已变化')
    except (ValueError,LookupError,OSError,sqlite3.Error):raise HTTPException(409,'事件或来源已变化，请重新读取核对')


def native_action_service(request,owner_id):
    management,actor,_=memory_management_service(request,owner_id)
    if management.actions is None:raise HTTPException(503,'整理待办尚未准备好')
    return management.actions,actor


@router.get('/memory-console/actions')
async def console_actions(request:Request,owner_id:str=''):
    service,actor=native_action_service(request,owner_id)
    return JSONResponse(await asyncio.to_thread(service.listing,actor.user_id),headers={'Cache-Control':'no-store'})


@router.get('/memory-console/actions/{identifier}')
async def console_action_preview(identifier:str,request:Request,owner_id:str=''):
    service,actor=native_action_service(request,owner_id)
    try:return JSONResponse(await asyncio.to_thread(service.preview,actor.user_id,identifier),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/action-preview')
async def console_pair_preview(values:ConsolePairPreview,request:Request):
    service,actor=native_action_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台预览完整来源')
    try:return JSONResponse(await asyncio.to_thread(service.prepare,actor.user_id,values.kind,values.first,values.second,values.first_version,values.second_version),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/action-decision')
async def console_action_decision(values:ConsoleActionDecision,request:Request):
    service,actor=native_action_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台核对后决定')
    try:return JSONResponse(await asyncio.to_thread(service.decide,actor.user_id,values.id,values.preview_version,values.action),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


class ConsoleOperationConfirm(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    token:str=Field(min_length=16,max_length=128)


class ConsoleRevisionPreview(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    memory_id:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_.:-]+$')
    content_version:str=Field(pattern=r'^[a-f0-9]{64}$')
    operation:Literal['append','correct']
    content:str=Field(min_length=2,max_length=12000)


@router.post('/memory-console/revision-preview')
async def console_revision_preview(values:ConsoleRevisionPreview,request:Request):
    management,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台编辑并预览')
    scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
    session=qq_bridge.store.conversation(scope)[1]
    def prepare():
        from saya_app.services.memory_revisions import revision_service
        from saya_app.services.memory_origin import bind_origin,ingress
        context=ingress('web',subject=actor.user_id,quote=values.content)
        context['operation']='native_qq_console_revision'
        with bind_origin(context):
            return revision_service(management.kb).preview(values.memory_id,values.operation,values.content,
                expected_version=values.content_version,scope='qq-console:'+scope,session_id=session,
                authorize=lambda:management.authorize_operation(actor,scope,session),subject_id=actor.user_id)
    try:return JSONResponse(await asyncio.to_thread(prepare),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/revision-confirm')
async def console_revision_confirm(values:ConsoleOperationConfirm,request:Request):
    management,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台确认修改')
    scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
    session=qq_bridge.store.conversation(scope)[1]
    def commit():
        from saya_app.services.memory_revisions import revision_service
        from saya_app.services.memory_origin import bind_origin,ingress
        context=ingress('web',subject=actor.user_id)
        context['operation']='native_qq_console_revision_confirmation'
        with bind_origin(context):
            result=revision_service(management.kb).confirm(values.token,scope='qq-console:'+scope,session_id=session,
                authorize=lambda:management.authorize_operation(actor,scope,session))
        if result.get('status')=='applied':management.saved_receipt(actor,result['memory_id'],session,confirmed=False)
        return result
    try:return JSONResponse(await asyncio.to_thread(commit),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/operation-preview')
async def console_operation_preview(values:ConsoleOperationPreview,request:Request):
    service,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机管理窗口预览并确认')
    scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
    session=qq_bridge.store.conversation(scope)[1]
    def prepare():
        with service.kb._memory_write_lock:
            result=service.operations.preview(actor,scope,session,values.memory_id,values.content_version,action=values.action)
            if result.get('status')=='blocked':raise MemoryAccessDenied('managed_memory')
            # Full content and token are read under the same original write lock.
            # This is local-console consent, never a fabricated QQ delivery.
            memory=service._record(values.memory_id)
            return {'memory_id':values.memory_id,'content_version':values.content_version,'action':values.action,
                    'title':str(memory.get('title') or ''),'body':str(memory.get('content') or ''),
                    'origin':memory.get('origin') or {},'message':result['message'],'token':result['confirmation_token']}
    try:return JSONResponse(await asyncio.to_thread(prepare),headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/operation-confirm')
async def console_operation_confirm(values:ConsoleOperationConfirm,request:Request):
    service,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机管理窗口明确确认')
    scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
    session=qq_bridge.store.conversation(scope)[1]
    try:
        result=await asyncio.to_thread(service.operations.confirm,actor,scope,session,values.token)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)


@router.post('/memory-console/command')
async def daily_command(values:DailyConsoleCommand,request:Request):
    service,owner=daily_memory_service(request,values.owner_id)
    text=values.text.strip()
    native=re.fullmatch(r'/memory\s+(link|archive|delete|block|unblock)\s+([A-Za-z0-9_.:-]{1,128})(?:\s+([A-Za-z0-9_.:-]{1,128}))?',text)
    if native:
        if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机操作台输入并核对完整预览')
        actions,actor=native_action_service(request,owner)
        members=[x for x in native.groups()[1:] if x is not None]
        try:return JSONResponse({'kind':'action_preview','data':await asyncio.to_thread(actions.command_preview,actor.user_id,native[1],members)},headers={'Cache-Control':'no-store'})
        except (MemoryAccessDenied,MemoryAccessConflict,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)
    if text in {'刷新','查看今日','查看待确认','查看最近操作'}:
        return {'kind':'list','data':await asyncio.to_thread(service.listing,owner)}
    management,actor,_=memory_management_service(request,owner)
    from saya_app.services.qq_console_reads import readonly_query
    if management.reads and readonly_query(text):
        try:return JSONResponse({'kind':'native_read','data':await asyncio.to_thread(management.reads.local,actor.user_id,text)},headers={'Cache-Control':'no-store'})
        except (MemoryAccessDenied,ValueError,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)
    match=re.fullmatch(r'(查看|批准|不保存)\s+([a-f0-9]{32})',text)
    if match:
        try:return {'kind':'preview','requested_action':match[1],'data':await asyncio.to_thread(service.preview,owner,match[2])}
        except (ValueError,MemoryAccessDenied,OSError,sqlite3.Error) as e:daily_error(e)
    if re.fullmatch(r'/memory\s+(?:list|show\s+[A-Za-z0-9_.:-]{1,128}|result\s+[a-f0-9]{32})',text):
        management,actor,_=memory_management_service(request,owner)
        session=qq_bridge.store.conversation(f'{actor.bot_id}:private:{owner}:shared')[1]
        try:return {'kind':'read_result','text':str(await asyncio.to_thread(management.command,actor,text,session))}
        except (ValueError,MemoryAccessDenied,MemoryAccessConflict,LookupError,OSError,sqlite3.Error) as e:memory_management_error(e)
    raise HTTPException(400,'可输入已登记的查询命令，或 /memory link、archive 两个编号及 delete、block、unblock 一个编号生成预览；执行须核对完整预览后点击确认。')


def memory_management_error(error):
    if isinstance(error,ValueError) and str(error)=='overlap_owner_review_busy':
        raise HTTPException(409,'已有一份整理正在处理，请等待原结果，不要重复发起模型请求') from None
    if isinstance(error,ValueError) and str(error)=='overlap_review_time_budget':
        raise HTTPException(408,'本次整理达到等待预算，未生成新的整理结果；请重新读取原稿后核对') from None
    if isinstance(error, MemoryAccessDenied):
        raise HTTPException(403, '权限未保存：请明确确认、选择已授权群，并检查条目是否可发布') from None
    if isinstance(error, MemoryAccessConflict):
        raise HTTPException(409, '记忆、列表或权限已变化，请重新读取后再确认；草稿保留') from None
    if isinstance(error, LookupError):
        raise HTTPException(404, '记忆不存在或已删除') from None
    if isinstance(error, (OSError, sqlite3.Error)):
        raise HTTPException(503, '存储结果未确认，请重新读取核对，勿重复提交') from None
    raise HTTPException(400, '参数无效，请检查条目、版本、账号与范围') from None


@router.get('/memory-management')
async def browse_memory_management(request: Request, owner_id: str = '', kind: str = 'saved',
        query: str = '', page: int = 1, snapshot: str = ''):
    service, actor, owners = memory_management_service(request, owner_id)
    try:
        data = service.browse(actor, kind=kind, query=query, page=page, snapshot=snapshot)
    except (MemoryAccessDenied, MemoryAccessConflict, ValueError, LookupError, OSError, sqlite3.Error) as error:
        memory_management_error(error)
    return JSONResponse(data | {'owner_id': actor.user_id, 'owners': owners,
        'revision': qq_bridge.controls()['revision'], 'group_recall_enabled': False,
        'group_recall_available':getattr(qq_bridge,'group_context',None) is not None}, headers={'Cache-Control':'no-store'})


@router.get('/group-context')
async def group_context_state(request:Request,owner_id:str=''):
    _,actor,owners=memory_management_service(request,owner_id)
    service=getattr(qq_bridge,'group_context',None)
    if service is None:raise HTTPException(503,'群语境服务尚未准备好')
    groups=[{'group_id':gid,**service.policy(gid)} for gid in sorted(qq_bridge.effective_groups())]
    return JSONResponse({'owner_id':actor.user_id,'owners':owners,'revision':qq_bridge.controls()['revision'],
        'task_control_enabled':qq_bridge.settings.task_control_enabled,'groups':groups},headers={'Cache-Control':'no-store'})


@router.get('/contact-requests')
async def contact_requests(request:Request,owner_id:str=''):
    _,actor,owners=memory_management_service(request,owner_id)
    service=getattr(qq_bridge,'contact_review',None)
    if service is None:raise HTTPException(503,'好友申请审核尚未准备好')
    return JSONResponse(service.state()|{'owner_id':actor.user_id,'owners':owners,'revision':qq_bridge.controls()['revision']},headers={'Cache-Control':'no-store'})


@router.get('/media-policy')
async def media_policy_state(request:Request,owner_id:str=''):
    _,actor,owners=memory_management_service(request,owner_id)
    service=getattr(qq_bridge,'media',None)
    if service is None:raise HTTPException(503,'图片服务尚未准备好')
    from saya_app.services.conversation import conversation_service
    creds=conversation_service.credentials()
    route=creds.get('model_routes',{}).get('multimodal_model_name','')
    return JSONResponse(service.state()|{'owner_id':actor.user_id,'owners':owners,'revision':qq_bridge.controls()['revision'],
        'vision_model_configured':bool(creds.get('configured') and route),'vision_model':route},headers={'Cache-Control':'no-store'})


class MediaPolicyUpdate(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    revision:str
    media_revision:StrictInt
    confirmed:StrictBool
    values:dict


class ExpressionPolicyUpdate(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    expression_revision:StrictInt
    mode:str
    confirmed:StrictBool


@router.get('/expression-policy')
async def expression_policy(request:Request,owner_id:str=''):
    _,actor,_=memory_management_service(request,owner_id)
    service=getattr(qq_bridge,'expressions',None)
    if service is None:raise HTTPException(503,'表达模式暂不可用。')
    return JSONResponse(service.state()|{'owner_id':actor.user_id},headers={'Cache-Control':'no-store'})


@router.put('/expression-policy')
async def expression_policy_update(values:ExpressionPolicyUpdate,request:Request):
    _,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机明确保存。')
    service=getattr(qq_bridge,'expressions',None)
    if service is None:raise HTTPException(503,'表达模式暂不可用。')
    from saya_app.services.qq_expressions import ExpressionConflict
    try:result=service.configure(actor.user_id,values.expression_revision,values.mode,confirmed=values.confirmed)
    except ExpressionConflict as e:raise HTTPException(409,str(e)) from None
    except PermissionError:raise HTTPException(403,'需要主人明确保存。') from None
    except ValueError:raise HTTPException(422,'表达模式无效。') from None
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


def favorite_service(request,owner_id):
    _,actor,owners=memory_management_service(request,owner_id)
    if not local_origin(request,require_origin=request.method!='GET'):raise HTTPException(403,'请从本机操作收藏清单。')
    source=request.query_params.get('source','favorites')
    if source not in {'favorites','official'}:raise HTTPException(422,'表情来源无效。')
    service=getattr(qq_bridge,'official_packs' if source=='official' else 'favorites',None)
    if service is None:raise HTTPException(503,'收藏清单暂不可用。')
    return service,actor,owners


@router.get('/favorites')
async def favorite_state(request:Request,owner_id:str=''):
    service,actor,owners=favorite_service(request,owner_id)
    return JSONResponse(service.state()|{'owner_id':actor.user_id,'owners':owners},headers={'Cache-Control':'no-store'})


@router.get('/favorites/{key}/preview')
async def favorite_preview(key:str,request:Request,owner_id:str=''):
    service,_,_=favorite_service(request,owner_id)
    try:png=await service.preview(key)
    except Exception:raise HTTPException(422,'图片暂时无法预览，未扩大读取范围。') from None
    return Response(png,media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


class FavoriteChoice(BaseModel):
    model_config={'extra':'forbid'}
    id:str
    selected:StrictBool
    meaning:str=Field(default='',max_length=1200)


class FavoriteUpdate(BaseModel):
    model_config={'extra':'forbid'}
    owner_id:str
    catalogue_revision:StrictInt
    confirmed:StrictBool
    choices:list[FavoriteChoice]=Field(default_factory=list,max_length=48)


@router.post('/favorites/{action}')
async def favorite_action(action:str,values:FavoriteUpdate,request:Request,key:str=''):
    service,_,_=favorite_service(request,values.owner_id)
    if values.confirmed is not True:raise HTTPException(403,'请明确确认操作。')
    from saya_app.services.qq_favorites import FavoriteConflict
    try:
        if service.state()['catalogue_revision']!=values.catalogue_revision:raise FavoriteConflict('收藏清单已变化，请重新读取。')
        if action=='refresh':
            if request.query_params.get('source')=='official':result=await service.refresh(values.catalogue_revision,owner_id=values.owner_id)
            else:result=await service.refresh(values.catalogue_revision)
        elif action=='select':result=service.select(values.catalogue_revision,[x.model_dump() for x in values.choices])
        elif action=='analyze':result=await service.analyze(key,values.catalogue_revision)
        else:raise HTTPException(404,'没有这个收藏操作。')
    except FavoriteConflict as e:raise HTTPException(409,str(e)) from None
    except ValueError:raise HTTPException(422,'选择或分析未完成，请核对指定项和视觉配置；未更改QQ收藏。') from None
    except HTTPException:raise
    except Exception:raise HTTPException(502,'读取或分析服务暂不可用；未更改QQ收藏。') from None
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


@router.put('/media-policy')
async def update_media_policy(values:MediaPolicyUpdate,request:Request):
    _,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请在本机网页明确确认')
    if values.revision!=qq_bridge.controls()['revision']:raise HTTPException(409,'QQ设置已变化，请重新读取')
    service=getattr(qq_bridge,'media',None)
    if service is None:raise HTTPException(503,'图片服务尚未准备好')
    from saya_app.services.qq_media import MediaConflict
    try:state=service.configure(actor.user_id,values.media_revision,values.values,confirmed=values.confirmed)
    except PermissionError:raise HTTPException(403,'需要主人明确确认') from None
    except MediaConflict:raise HTTPException(409,'媒体设置已变化，请重新读取；草稿保留') from None
    except ValueError:raise HTTPException(400,'参数无效，媒体功能需保持任务控制开启') from None
    return JSONResponse(state,headers={'Cache-Control':'no-store'})


class FriendDecision(BaseModel):
    owner_id:str
    revision:str
    approve:bool
    confirmed:bool=False


@router.post('/contact-requests/{request_id}')
async def decide_friend_request(request_id:str,values:FriendDecision,request:Request):
    _,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机网页明确确认')
    if values.revision!=qq_bridge.controls()['revision']:raise HTTPException(409,'设置已变化，请重新读取')
    service=getattr(qq_bridge,'contact_review',None)
    if service is None:raise HTTPException(503,'好友申请审核尚未准备好')
    try:return await service.decide(actor.user_id,request_id,values.approve,confirmed=values.confirmed)
    except ValueError:raise HTTPException(409,'申请已处理、过期或未明确确认；请重新读取核对') from None
    except Exception:raise HTTPException(502,'QQ操作结果待核对，未自动重试；接受好友不会授予聊天权限') from None


class GroupContextUpdate(BaseModel):
    owner_id:str
    group_id:str
    revision:str
    expected_revision:int
    confirmed:bool=False
    values:dict


@router.put('/group-context')
async def update_group_context(values:GroupContextUpdate,request:Request):
    _,actor,_=memory_management_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机网页明确确认')
    if values.revision!=qq_bridge.controls()['revision']:raise HTTPException(409,'QQ设置已变化，请重新读取；草稿保留')
    service=getattr(qq_bridge,'group_context',None)
    if service is None:raise HTTPException(503,'群语境服务尚未准备好')
    from saya_app.services.qq_group_context import GroupConflict,GroupDenied
    try:
        state=service.configure(actor.user_id,values.group_id,values.values,values.expected_revision,confirmed=values.confirmed)
    except GroupDenied:raise HTTPException(403,'请选择已授权群并明确确认；开启群功能需要任务控制保持开启') from None
    except GroupConflict:raise HTTPException(409,'群设置已变化，请重新读取；草稿保留') from None
    except ValueError:raise HTTPException(400,'群设置参数无效') from None
    return JSONResponse({'group_id':values.group_id,**state},headers={'Cache-Control':'no-store'})


@router.get('/memory-management/item')
async def memory_management_item(request: Request, memory_id: str, owner_id: str = ''):
    service, actor, _ = memory_management_service(request, owner_id)
    try:
        return JSONResponse(service.detail(actor, memory_id), headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied, MemoryAccessConflict, ValueError, LookupError, OSError, sqlite3.Error) as error:
        memory_management_error(error)


class MemoryAccessUpdate(BaseModel):
    owner_id: str
    memory_id: str = Field(max_length=128)
    content_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_revision: StrictInt = Field(ge=0)
    subject_id: str
    visibility: str = 'private'
    groups: list[str] = Field(default_factory=list, max_length=128)
    requesters: list[str] = Field(default_factory=list, max_length=128)
    writers: list[str] = Field(default_factory=list, max_length=128)
    confirmed: StrictBool
    revision: str


@router.put('/memory-management/access')
async def update_memory_access(values: MemoryAccessUpdate, request: Request):
    service, actor, _ = memory_management_service(request, values.owner_id)
    if not local_origin(request, require_origin=True):
        raise HTTPException(403, '逐条权限只能从本机同源网页明确确认')
    if values.revision != qq_bridge.controls()['revision']:
        raise HTTPException(409, 'QQ设置已变化，请重新读取后确认')
    try:
        data = service.set_access(actor, values.memory_id, **values.model_dump(
            exclude={'owner_id', 'memory_id', 'revision'}))
        return JSONResponse(data, headers={'Cache-Control':'no-store'})
    except (MemoryAccessDenied, MemoryAccessConflict, ValueError, LookupError, OSError, sqlite3.Error) as error:
        memory_management_error(error)


@router.put('/context-links')
async def update_context_links(values:ContextLinkUpdate,request:Request):
    bridge,links,scope=context_service(request,values.owner_id)
    if not local_origin(request,require_origin=True):raise HTTPException(403,'请从本机网页明确选择并确认')
    if values.revision!=bridge.controls()['revision']:raise HTTPException(409,'设置已变化，请重新读取')
    if bridge.draining or bridge._configuration_busy():raise HTTPException(409,'QQ任务处理中，请结束后再切换话题')
    from saya_app.services.qq_context_links import ContextConflict,ContextUnavailable
    from saya_app.services import qq_delivery
    from uuid import uuid4
    try:
        if values.action=='select':
            if not bridge.settings.enabled or not bridge.settings.shared_memory or not bridge.settings.task_control_enabled:
                raise HTTPException(409,'接续需要开启QQ、主人私聊共享及任务状态与取消，以保护撤销后的读写边界')
            result=links.select(scope,values.session_id,values.generation,confirmed=True,new_session='session_qq_'+uuid4().hex+'.jsonl')
        elif values.action=='off':result=links.revoke(scope,values.generation,new_session='session_qq_'+uuid4().hex+'.jsonl')
        else:raise HTTPException(400,'无效话题操作')
    except ContextConflict:raise HTTPException(409,'话题已变化，请重新读取') from None
    except ContextUnavailable:raise HTTPException(400,'来源不可用，只能选择已保存的普通网页会话') from None
    return {'state':result}


@router.post("/lifecycle/drain")
async def drain(values: DrainUpdate, request: Request):
    require_admin(request, require_running=False)
    qq_bridge.draining = values.draining
    activity = getattr(request.app.state, 'service_activity', None)
    return qq_bridge.status() | {'active_web_requests': activity.active_web_requests if activity else 0}


class StopWorkersUpdate(BaseModel):
    model_config={'extra':'forbid'}
    confirmed:StrictBool


@router.post('/lifecycle/cancel-pending')
async def cancel_pending_qq(values:StopWorkersUpdate,request:Request):
    require_admin(request,require_running=False)
    if not values.confirmed:raise HTTPException(403,'需要明确取消未执行任务。')
    if not qq_bridge.draining:raise HTTPException(409,'请先暂停新接收。')
    return {'cancelled_pending':qq_bridge.cancel_pending(),'status':qq_bridge.status()}


@router.post('/lifecycle/stop-workers')
async def stop_workers(values:StopWorkersUpdate,request:Request):
    require_admin(request,require_running=False)
    if not values.confirmed:raise HTTPException(403,'需要明确停止。')
    activity=getattr(request.app.state,'service_activity',None)
    counts=qq_bridge.status().get('counts',{})
    if (not qq_bridge.draining or (activity and activity.active_web_requests)
        or any(counts.get(k,0) for k in ['queued','generating','sending','voice_rendering','active_tasks'])):
        raise HTTPException(409,'先排空现有任务，再停止辅助服务。')
    # Block concurrent shutdown from treating this asynchronous cleanup as an
    # idle server. The QQ namespace remains available behind the drain barrier.
    if activity:activity.active_web_requests+=1
    try:
        from starlette.concurrency import run_in_threadpool
        from saya_app.services.local_tts import local_tts
        from saya_app.services.minecraft_mineflayer import minecraft_bot_service
        await run_in_threadpool(local_tts.unload)
        await run_in_threadpool(minecraft_bot_service.stop)
        return {'stopped':True}
    finally:
        if activity:activity.active_web_requests-=1


class ProactiveUpdate(BaseModel):
    owner_id:str
    revision:int
    confirmed:StrictBool
    values:dict


@router.get('/proactive')
async def proactive_status(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.proactive:raise HTTPException(503,'请先启动受管后端。')
    return qq_bridge.proactive.state()


@router.put('/proactive')
async def proactive_save(values:ProactiveUpdate,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.proactive:raise HTTPException(503,'请先启动受管后端。')
    if not values.confirmed:raise HTTPException(403,'需要明确保存。')
    try:return qq_bridge.proactive.configure(values.owner_id,values.revision,values.values)
    except PermissionError:raise HTTPException(403,'仅配置主人可修改。') from None
    except ValueError:raise HTTPException(409,'设置已变化或参数无效，请重新读取。') from None


class SharedSettingsUpdate(BaseModel):
    values: dict
    web_follow_global: bool
    revision: str


@router.get('/context-policy')
async def context_policy_status(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.context_salience:raise HTTPException(503,'请先启动受管QQ接入。')
    return qq_bridge.context_salience.state()


@router.get('/person-events/migration')
async def personal_events_migration(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    return qq_bridge.episodes.people.migration_preview()|{'owners':sorted(qq_bridge.settings.owner_ids)}


@router.get('/episode-policy')
async def episode_policy_status(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    return qq_bridge.episodes.policy()


@router.get('/unified-events')
async def unified_events_status(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    service=qq_bridge.episodes.unified
    with service.store.connect() as db:
        rows=db.execute('select id,grp,revision,data from qq_shared_events where bot=? order by id limit 101',(service.bot,)).fetchall()
    return {'policy':service.policy(),'preview':service.preview(),'groups':[
        {'id':r['id'],'group':r['grp'],'revision':r['revision'],'summary':service.summary({**json.loads(r['data']),'group':r['grp']}),
         'timing':service.timing({**json.loads(r['data']),'group':r['grp']})} for r in rows[:100]],
        'more':len(rows)>100}


@router.put('/unified-events')
async def unified_events_configure(values:dict,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    if values.get('confirmed') is not True:raise HTTPException(400,'须明确确认当前预览。')
    try:return qq_bridge.episodes.unified.configure(values.get('owner_id'),values.get('revision'),
        values.get('values'),values.get('expected_version'))
    except PermissionError:raise HTTPException(403,'仅主人可配置。') from None
    except (ValueError,TypeError):raise HTTPException(409,'参数、权限或预览已变化，请重新读取。') from None


@router.get('/context-services')
async def context_services_status(request:Request):
    require_admin(request,require_running=False)
    names=('dialogue_experience','context_salience','forward_input','relationships','episodes')
    bindings={name:getattr(qq_bridge,name,None) is not None and getattr(qq_bridge.responder,name,None) is getattr(qq_bridge,name,None) for name in names}
    return {'ready':all(bindings.values()),'bindings':bindings,
        'event_controls':bool(getattr(getattr(getattr(qq_bridge,'episodes',None),'people',None),'controls',None)),
        'event_operations':bool(getattr(getattr(qq_bridge,'episodes',None),'operations',None)),
        'event_effective_views':bool(getattr(getattr(qq_bridge,'episodes',None),'views',None)),
        'event_provenance':bool(getattr(getattr(qq_bridge,'episodes',None),'provenance',None))}


@router.get('/topic-lifecycle')
async def topic_lifecycle_status(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    return qq_bridge.episodes.unified.lifecycle.policy()


@router.put('/topic-lifecycle')
async def topic_lifecycle_configure(values:dict,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    if values.get('confirmed') is not True:raise HTTPException(400,'须明确保存策略。')
    try:return qq_bridge.episodes.unified.lifecycle.configure(values.get('owner_id'),values.get('revision'),values.get('values'))
    except PermissionError:raise HTTPException(403,'仅主人可配置。') from None
    except (ValueError,TypeError):raise HTTPException(409,'策略或参数已变化，请重新读取。') from None


@router.put('/episode-policy')
async def episode_policy_save(values:ProactiveUpdate,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    if not values.confirmed:raise HTTPException(403,'需要明确保存。')
    try:return qq_bridge.episodes.configure(values.owner_id,values.revision,values.values)
    except PermissionError:raise HTTPException(403,'仅配置主人可修改。') from None
    except ValueError:raise HTTPException(409,'版本已变化、参数无效或尚未启用话题策略，请重新读取。') from None


@router.post('/person-events/migration')
async def migrate_personal_events(values:dict,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    if values.get('confirmed') is not True:raise HTTPException(403,'请先预览并明确确认迁移。')
    try:return qq_bridge.episodes.people.migrate(values.get('owner_id'),values.get('expected_version'))
    except PermissionError:raise HTTPException(403,'仅配置主人可管理。') from None
    except ValueError:raise HTTPException(409,'预览或容量已变化，请重新读取，原开关保持。') from None


@router.get('/person-events')
async def personal_events(request:Request,subject:str='',offset:int=0):
    require_admin(request,require_running=False)
    if not qq_bridge.episodes:raise HTTPException(503,'请先启动受管QQ接入。')
    service=qq_bridge.episodes;people=service.people
    import re
    if subject and not re.fullmatch(r'[1-9]\d{0,19}',subject):raise HTTPException(400,'主体须为账号编号。')
    with service.store.connect() as db:
        rows=db.execute('select id,revision,data from qq_episode_cards where scope '+('=?' if subject else 'like ?')+
            ' order by id limit 21 offset ?',(people.scope(subject) if subject else people.bot+':person:%',max(0,offset))).fetchall()
        paused_ids={r[0] for r in db.execute('select event_id from qq_episode_pauses').fetchall()}
    items=[]
    for row in rows[:20]:
        card=json.loads(row['data'])
        from ..services.qq_event_priority import validate,timing_view,project_text
        validate(card)
        if card['expires']<=service.clock() or card['epoch']!=service.epoch():continue
        card['paused_for_actor']=row['id'] in paused_ids
        available=any(f['source']==card.get('latest_source') and f['expires']>service.clock() and not f.get('superseded') for f in card['facts'])
        from ..services.qq_event_attribution import owner_ref
        items.append({'id':row['id'],'revision':row['revision'],'title':project_text(card,card['title'],'private',service.clock(),observed_at=card['last_user']) if available else '来源已不可用的本人事件','subject':card['subject'],
            'owner_display':owner_ref(card),
            'state':people.controls.effective(card),'capacity':people.capacity(card['subject']),
            'importance':card.get('importance','normal'),'importance_locked':card.get('importance_locked',False),
            'lifecycle':card.get('lifecycle','open'),'state_reason':card.get('state_reason','evidence_or_time'),
            'last_user':card['last_user'],'expires':card['expires'],
            'source_available':available,
            'timing':timing_view(card,'private',service.clock()),'importance_rule_version':card.get('importance_rule_version'),
            'importance_reason':card.get('importance_reason','default'),
            'visible_sources':sorted({f.get('visibility','unknown') for f in card['facts'] if f['expires']>service.clock()}),
            'capacity_grace_until':card.get('capacity_grace_until')})
    return {'items':items,'offset':max(0,offset),'next_offset':max(0,offset)+20 if len(rows)>20 else None,
        'identity_migrated':people.active(),'enabled':service.policy()['values']['enabled']}


@router.get('/dialogue-experience')
async def dialogue_experience_status(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.dialogue_experience:raise HTTPException(503,'请先启动受管QQ接入。')
    return qq_bridge.dialogue_experience.state()|{'diagnostics':qq_bridge.dialogue_experience.summary(),'owners':sorted(qq_bridge.settings.owner_ids)}


@router.put('/dialogue-experience')
async def dialogue_experience_save(values:ProactiveUpdate,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.dialogue_experience:raise HTTPException(503,'请先启动受管QQ接入。')
    if not values.confirmed:raise HTTPException(403,'需要明确保存。')
    try:return qq_bridge.dialogue_experience.configure(values.owner_id,values.revision,values.values)
    except PermissionError:raise HTTPException(403,'仅配置主人可修改。') from None
    except ValueError:raise HTTPException(409,'设置已变化或参数无效，请重新读取。') from None


@router.get('/forward-input')
async def forward_input_status(request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.forward_input:raise HTTPException(503,'请先启动受管QQ接入。')
    return qq_bridge.forward_input.state()


@router.put('/forward-input')
async def forward_input_save(values:ProactiveUpdate,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.forward_input:raise HTTPException(503,'请先启动受管QQ接入。')
    if not values.confirmed:raise HTTPException(403,'需要明确保存。')
    try:return qq_bridge.forward_input.configure(values.owner_id,values.revision,values.values)
    except PermissionError:raise HTTPException(403,'仅配置主人可修改。') from None
    except ValueError:raise HTTPException(409,'设置已变化或参数无效，请重新读取。') from None


@router.put('/context-policy')
async def context_policy_save(values:ProactiveUpdate,request:Request):
    require_admin(request,require_running=False)
    if not qq_bridge.context_salience:raise HTTPException(503,'请先启动受管QQ接入。')
    if not values.confirmed:raise HTTPException(403,'需要明确保存。')
    try:return qq_bridge.context_salience.configure(values.owner_id,values.revision,values.values)
    except PermissionError:raise HTTPException(403,'仅配置主人可修改。') from None
    except ValueError:raise HTTPException(409,'设置已变化或参数无效，请重新读取。') from None


@router.get("/shared-settings")
async def shared_settings(request: Request):
    require_admin(request, require_running=False)
    try:
        return qq_bridge.shared_config.load()
    except ValueError:
        raise HTTPException(400, "共享配置损坏，请恢复本地有效配置后再操作。") from None


@router.put("/shared-settings")
async def save_shared_settings(values: SharedSettingsUpdate, request: Request):
    require_admin(request, require_running=False)
    try:
        return await qq_bridge.update_shared_controls(values.values, values.web_follow_global, values.revision)
    except (QQControlsBusy, QQControlsConflict) as exc:
        raise HTTPException(409, str(exc)) from None
    except QQControlsUnsupported as exc:
        raise HTTPException(400, str(exc)) from None
    except ValueError:
        raise HTTPException(400, "共享默认未保存，请检查字段和参数范围。") from None


@router.post("/credentials")
async def sync_credentials(request: Request):
    require_admin(request, require_running=False)
    from saya_app.services.conversation import conversation_service
    from saya_app.services.conversation import CREDENTIALS_FILE
    from saya_app.services.credentials_storage import read_credentials
    if read_credentials(CREDENTIALS_FILE).get('_local_profile_managed'):
        raise HTTPException(409, '请刷新网页，使用带修订检查的“浏览器配置保存到本机”按钮。')
    try:
        raw = await request.body()
        if len(raw) > 16384:
            raise ValueError("body too large")
        body = json.loads(raw)
        allowed = {"api_key", "api_url", "api_model_name", "brave_search_api_key"}
        if not isinstance(body, dict) or set(body) - allowed or any(not isinstance(value, str) or len(value) > 8192 for value in body.values()):
            raise ValueError("invalid credentials")
        if body.get("api_url"):
            from saya_app.services.qq_bridge import QQSettings
            QQSettings.valid_url(body["api_url"])
        conversation_service.save_credentials(**{key: body.get(key, "") for key in ("api_key", "api_url", "api_model_name")},
            brave_search_api_key=body.get("brave_search_api_key") or None)
    except (ValueError, TypeError):
        raise HTTPException(400, "凭据未同步，请检查本机 API 设置；不会返回密钥内容。") from None
    return {"saved": True, "credentials": conversation_service.credentials_status()}


@router.post("/search-credentials")
async def search_credentials(request: Request):
    require_admin(request, require_running=False)
    from saya_app.services.credentials_storage import read_credentials
    target = qq_bridge.search_credentials.canonical_path
    if target is not None and read_credentials(target).get('_local_profile_managed'):
        raise HTTPException(409, '请使用本机配置面板保存搜索密钥，旧页面不能覆盖新配置。')
    try:
        body = await request.json()
        qq_bridge.search_credentials.save(body.get("api_key") if isinstance(body, dict) else None)
    except (ValueError, TypeError):
        raise HTTPException(400, "搜索密钥未保存，请在网页 Other API 中检查 Brave Search 配置。") from None
    return {"configured": True}


def local_profile_access(request):
    require_admin(request, require_running=False)
    if not local_origin(request, require_origin=request.method not in {'GET','HEAD'}) or request.headers.get('x-saya-qq-ui') != '1':
        raise HTTPException(403, 'API配置仅允许本机同源网页访问。')
    from saya_app.services.conversation import CREDENTIALS_FILE
    from saya_app.services.local_api_profile import LocalApiProfile
    return LocalApiProfile(CREDENTIALS_FILE)


def browser_archive(request):
    local_profile_access(request)
    from saya_app.services.browser_settings_archive import BrowserSettingsArchive
    return BrowserSettingsArchive(BROWSER_ARCHIVE_PATH)


@router.get('/browser-settings')
async def browser_settings_status(request:Request):
    archive=browser_archive(request)
    try:return JSONResponse(archive.status(),headers={'Cache-Control':'no-store'})
    except (ValueError,OSError,RecursionError):
        raise HTTPException(400,'本机设置存档无法读取；未覆盖文件或浏览器缓存。') from None


async def browser_settings_operation(request,save=False):
    archive=browser_archive(request)
    from saya_app.services.credentials_storage import CredentialsConflict
    from saya_app.services.state_storage import StateSaveError
    try:
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>9*1024*1024:raise ValueError('large capture')
        body=json.loads(raw)
        if not isinstance(body,dict) or set(body)-{'values','revision','overwrite','source_label'}:raise ValueError('invalid capture')
        if save:result=archive.save(body.get('values'),body.get('revision'),body.get('overwrite',False),body.get('source_label',''))
        else:result=archive.preview(body.get('values'),body.get('revision'))
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except CredentialsConflict as error:raise HTTPException(409,str(error)) from None
    except StateSaveError:raise HTTPException(503,'设置存档写入失败；原文件与浏览器缓存保持不变。') from None
    except (ValueError,TypeError,OSError,RecursionError):
        raise HTTPException(400,'设置存档未保存：请检查已登记字段、格式及容量。浏览器缓存未改动。') from None


@router.post('/browser-settings/preview')
async def browser_settings_preview(request:Request):return await browser_settings_operation(request)


@router.put('/browser-settings')
async def browser_settings_save(request:Request):return await browser_settings_operation(request,save=True)


@router.get('/browser-settings/runtime')
async def browser_settings_runtime(request:Request):
    archive=browser_archive(request)
    try:return JSONResponse(archive.runtime(),headers={'Cache-Control':'no-store','Vary':'Cookie'})
    except (ValueError,OSError,RecursionError):raise HTTPException(400,'本地设置无法读取，请检查文件；不会改用旧浏览器缓存。') from None


@router.put('/browser-settings/runtime')
async def browser_settings_runtime_save(request:Request):
    archive=browser_archive(request)
    from saya_app.services.credentials_storage import CredentialsConflict
    from saya_app.services.state_storage import StateSaveError
    try:
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>9*1024*1024:raise ValueError('large change')
        body=json.loads(raw)
        if not isinstance(body,dict) or set(body)-{'revision','values','clear_fields','enabled'}:raise ValueError('invalid update')
        if 'enabled' in body and type(body['enabled']) is not bool:raise ValueError('invalid policy')
        result=archive.update_runtime(body.get('revision'),body.get('values'),body.get('clear_fields'),body.get('enabled'))
        return JSONResponse(result,headers={'Cache-Control':'no-store','Vary':'Cookie'})
    except CredentialsConflict as error:raise HTTPException(409,str(error)) from None
    except StateSaveError:raise HTTPException(503,'本地设置写入失败；文件保持原值，页面草稿尚未保存。') from None
    except (ValueError,TypeError,OSError,RecursionError):raise HTTPException(400,'本地设置更新失败：字段或格式不正确；未覆盖旧文件。') from None


@router.get('/local-profile')
async def local_profile_status(request: Request):
    profile = local_profile_access(request)
    try:
        return JSONResponse(profile.status(),headers={'Cache-Control':'no-store','Vary':'Cookie'})
    except (ValueError,OSError):
        raise HTTPException(400, '本机配置无法读取；浏览器缓存保持原样，请先检查本机文件。') from None


@router.post('/local-profile/read')
async def local_profile_read(request: Request):
    profile = local_profile_access(request)
    from saya_app.services.credentials_storage import CredentialsConflict
    try:
        raw = await request.body()
        if len(raw)>1024: raise ValueError('oversize')
        body = json.loads(raw)
        if not isinstance(body,dict) or set(body)-{'revision','purpose'} or body.get('purpose') not in {'runtime','browser_sync'}:
            raise ValueError('invalid read')
        data = profile.runtime(body.get('revision'))
        if body['purpose']=='runtime':
            from saya_app.services.conversation import conversation_service
            effective = conversation_service.credentials()
            if data['prefer_local'] and effective['api_source']=='environment':
                data['settings'].update(api_key=effective['api_key'],api_url=effective['api_url'],api_model_name=effective['model'])
            import os
            if os.environ.get('BRAVE_SEARCH_API_KEY','').strip():
                data['settings']['brave_search_api_key']=effective['brave_search_api_key']
            if os.environ.get('DEEPSEEK_SEARCH_API_KEY','').strip():
                data['settings']['deepseek_search_api_key']=effective['deepseek_search_api_key']
            from saya_app.services.web_research.providers import official_deepseek_url
            data['deepseek_model_key_available']=bool(effective.get('api_key')) and official_deepseek_url(effective.get('api_url'))
        return JSONResponse(data,headers={'Cache-Control':'no-store','Vary':'Cookie'})
    except CredentialsConflict as error:
        raise HTTPException(409,str(error)) from None
    except (ValueError,TypeError,OSError):
        raise HTTPException(400,'本机配置读取失败，浏览器缓存未改动。') from None


@router.put('/local-profile')
async def local_profile_save(request: Request):
    profile = local_profile_access(request)
    from saya_app.services.credentials_storage import CredentialsConflict
    from saya_app.services.state_storage import StateSaveError
    try:
        raw = await request.body()
        if len(raw)>65536: raise ValueError('oversize')
        body = json.loads(raw)
        allowed = {'values','revision','overwrite','clear_fields','prefer_local'}
        if not isinstance(body,dict) or set(body)-allowed: raise ValueError('invalid body')
        result = profile.save(body.get('values'),body.get('revision'),body.get('overwrite',False),
                              body.get('clear_fields',[]),body.get('prefer_local',True))
        return JSONResponse(result,headers={'Cache-Control':'no-store','Vary':'Cookie'})
    except CredentialsConflict as error:
        raise HTTPException(409,str(error)) from None
    except StateSaveError:
        raise HTTPException(503,'本机文件保存失败，原配置及浏览器缓存未改变。') from None
    except (ValueError,TypeError,OSError) as error:
        detail = str(error) if str(error).startswith('模型连接须') else '配置未保存，请检查字段、地址及长度；空白不会清空已存字段。'
        raise HTTPException(400,detail) from None


@router.put('/local-profile/search-provider')
async def select_search_provider(request:Request):
    profile=local_profile_access(request)
    from saya_app.services.credentials_storage import CredentialsConflict
    from saya_app.services.state_storage import StateSaveError
    from saya_app.services.conversation import conversation_service
    try:
        raw=await request.body()
        if len(raw)>4096:raise ValueError('invalid body')
        body=json.loads(raw)
        if not isinstance(body,dict) or set(body)-{'provider','revision','api_key','reuse_model_key','overwrite'}:raise ValueError('invalid body')
        result=profile.select_search_provider(body.get('provider'),body.get('revision'),body.get('api_key'),
            body.get('reuse_model_key',False),body.get('overwrite',False),conversation_service.credentials())
        return JSONResponse(result,headers={'Cache-Control':'no-store','Vary':'Cookie'})
    except CredentialsConflict as error:raise HTTPException(409,str(error)) from None
    except StateSaveError:raise HTTPException(503,'搜索服务未切换：本机写入失败，原厂家和密钥保持不变。') from None
    except (ValueError,TypeError,OSError) as error:
        detail=str(error) if str(error).startswith(('仅可复制','请先填写')) else '搜索服务未切换：请检查厂家、密钥和设置版本。'
        raise HTTPException(400,detail) from None


@router.get("/management")
async def management(request: Request):
    require_admin(request)
    try:
        return await qq_bridge.group_management()
    except Exception:
        raise HTTPException(502, "无法读取 OneBot 群列表，请检查 QQ 连接") from None


class GroupAccess(BaseModel):
    enabled: bool


@router.put("/groups/{group_id}")
async def group_access(group_id: str, value: GroupAccess, request: Request):
    require_admin(request)
    try:
        return qq_bridge.set_group_access(group_id, value.enabled)
    except QQControlsBusy as exc:
        raise HTTPException(409,str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


class InviteDecision(BaseModel):
    approve: bool


@router.post("/invites/{request_id}")
async def decide_invite(request_id: str, value: InviteDecision, request: Request):
    require_admin(request)
    try:
        return await qq_bridge.decide_invite(request_id, value.approve)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(502, "操作结果不明确，请先在 QQ 核对，勿重复同意") from None
