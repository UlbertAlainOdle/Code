from fastapi import APIRouter, HTTPException

from core_services import sync_anki_checkin_state
from saya_app.services.anki import anki_service


router = APIRouter(prefix="/api/anki", tags=["anki"])


def _sync_checkin(state):
    try:
        sync_anki_checkin_state(state)
    except Exception:
        # Anki statistics remain usable even if an optional memory/check-in
        # write encounters a transient problem.
        pass
    return state


@router.get("/state")
def get_anki_state():
    return anki_service.get_state()


@router.post("/discover")
def discover_anki_profiles(req: dict):
    try:
        return anki_service.discover_profiles((req or {}).get("data_path", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/settings")
def save_anki_settings(req: dict):
    try:
        return _sync_checkin(anki_service.configure(
            (req or {}).get("data_path", ""),
            (req or {}).get("profile", ""),
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/settings")
def clear_anki_settings():
    return anki_service.clear_settings()


@router.post("/refresh")
def refresh_anki_state():
    return _sync_checkin(anki_service.refresh())
