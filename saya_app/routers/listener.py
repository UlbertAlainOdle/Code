"""REST endpoints for the Listener memory-intake role."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..services.listener import ListenerError, listener_service
from ..services.memory_versions import MemoryVersionConflict

router = APIRouter(prefix="/api/listener", tags=["listener"])


class SendRequest(BaseModel):
    session_id: str
    text: str


class ConfirmRequest(BaseModel):
    session_id: str
    content: str
    layer: str
    category: Optional[str] = "general"
    title: Optional[str] = ""
    expected_version: str
    operation_id: str


class RecoverRequest(BaseModel):
    session_id: str
    operation_id: str


class UpdateRequest(ConfirmRequest):
    pass


@router.post("/open")
def open_listener_session():
    try:
        session_id = listener_service.open_session()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"session_id": session_id}


@router.post("/send")
def listener_send(req: SendRequest):
    try:
        return listener_service.handle_message(req.session_id, req.text)
    except ListenerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except MemoryVersionConflict as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception as exc:  # never leak a stack trace to the chat UI
        raise HTTPException(status_code=500, detail=f"Listener 内部错误：{exc}")


@router.post("/confirm")
def listener_confirm(req: ConfirmRequest):
    try:
        return listener_service.confirm(
            req.session_id,req.content,req.layer,req.category,req.title,
            expected_version=req.expected_version,operation_id=req.operation_id)
    except MemoryVersionConflict as exc:
        raise HTTPException(409,str(exc)) from None
    except ListenerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Listener结果待核对，请查询原操作，不要重复保存。") from None


@router.post("/update")
def listener_update(req: UpdateRequest):
    try:
        return listener_service.update_saved(
            req.session_id,req.content,req.layer,req.category,req.title,
            expected_version=req.expected_version,operation_id=req.operation_id)
    except MemoryVersionConflict as exc:
        raise HTTPException(409,str(exc)) from None
    except ListenerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Listener结果待核对，请查询原操作，不要重复保存。") from None


@router.get("/state")
def listener_state(session_id: str):
    try:return listener_service.derive_state(session_id)
    except (ListenerError,ValueError) as error:raise HTTPException(400,str(error)) from None


@router.post('/recover')
def listener_recover(req: RecoverRequest):
    from ..services.listener_writes import recover
    try:return recover(listener_service,req.session_id,req.operation_id)
    except (ListenerError,ValueError) as error:raise HTTPException(409,str(error)) from None


@router.get('/result')
def listener_result(session_id: str, operation_id: str):
    from ..services.listener_writes import require_session,scope_for
    from ..services.chat_memory_writes import journal,public_result
    try:require_session(listener_service,session_id)
    except (ListenerError,ValueError) as error:raise HTTPException(400,str(error)) from None
    result=journal(listener_service.knowledge_base()).lookup(scope_for(session_id),session_id,operation_id,kind='listener')
    if result is None:raise HTTPException(404,'当前Listener会话没有该操作')
    return public_result(result)


@router.get("/sessions")
def listener_sessions():
    return {"session_ids": listener_service.listener_sessions()}
