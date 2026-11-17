from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool

from core_services import emotion_engine, kb_system, memory_manager
from saya_app.services import mood_continuity
from saya_app.services import conversation_mood


router = APIRouter(prefix="/api", tags=["emotion"])


@router.get("/status")
def get_status():
    try:
        return mood_continuity.snapshot(emotion_engine, enabled=kb_system.settings.get("mood_continuity_enabled", True))
    except OSError as error:
        raise HTTPException(503, str(error))


class EmotionLockRequest(BaseModel):
    locked: StrictBool
    session_id: str = ""


@router.post("/emotion/lock")
def toggle_emotion_lock(req: EmotionLockRequest):
    try:
        state = conversation_mood.control(memory_manager, req.session_id, locked=req.locked) if req.session_id else mood_continuity.set_locked(emotion_engine, req.locked)
    except ValueError as error:
        raise HTTPException(400, str(error))
    except OSError as error:
        raise HTTPException(503, str(error))
    return {"status": "success", "is_locked": state.get("is_locked", False), "mood": mood_continuity.summarize(state)}


class EmotionResetRequest(BaseModel):
    session_id: str = ""


@router.post("/emotion/reset")
def reset_emotion(req: EmotionResetRequest = EmotionResetRequest()):
    try:
        state = conversation_mood.control(memory_manager, req.session_id, reset=True) if req.session_id else mood_continuity.reset_neutral(emotion_engine)
    except ValueError as error:
        raise HTTPException(400, str(error))
    except OSError as error:
        raise HTTPException(503, str(error))
    return {"status": "success", "mood": mood_continuity.summarize(state)}


@router.get("/emotion/offline_report")
def get_offline_report():
    try:
        return mood_continuity.offline_report(emotion_engine, enabled=kb_system.settings.get("mood_continuity_enabled", True))
    except OSError as error:
        raise HTTPException(503, str(error))

