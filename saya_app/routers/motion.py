from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core_services import motion_ai


router = APIRouter(tags=["motion"])


class MotionRequest(BaseModel):
    context: dict = {}


@router.post("/get_motion_suggestion")
def get_motion_suggestion(req: MotionRequest):
    try:
        suggestion = motion_ai.get_motion_suggestion(req.context)
        return JSONResponse(content=suggestion)
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)


@router.get("/get_motion_config")
def get_motion_config():
    return JSONResponse(content=motion_ai.motion_categories)

