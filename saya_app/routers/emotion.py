from fastapi import APIRouter

from core_services import emotion_engine


router = APIRouter(prefix="/api", tags=["emotion"])


@router.get("/status")
def get_status():
    return emotion_engine.state


@router.post("/emotion/lock")
def toggle_emotion_lock(req: dict):
    locked = req.get("locked")
    if locked is not None:
        emotion_engine.state["is_locked"] = bool(locked)
        emotion_engine.save_state()
    return {"status": "success", "is_locked": emotion_engine.state.get("is_locked", False)}


@router.get("/emotion/offline_report")
def get_offline_report():
    return emotion_engine.calculate_offline_changes()

