from fastapi import APIRouter, HTTPException

from saya_app.services.video_recommendations import VideoNetworkError, video_recommendation_service


router = APIRouter(prefix="/api/video_recommendations", tags=["video_recommendations"])


@router.get("/state")
def get_state():
    return video_recommendation_service.get_state()


@router.post("/settings")
def update_settings(req: dict):
    return {
        "status": "success",
        "settings": video_recommendation_service.update_settings(req or {}),
    }


@router.post("/creators/add")
def add_creator(req: dict):
    try:
        creator = video_recommendation_service.add_creator(
            platform=req.get("platform"),
            name=req.get("name"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "creator": creator}


@router.post("/creators/delete")
def delete_creator(req: dict):
    creator_id = req.get("id")
    if video_recommendation_service.delete_creator(creator_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Creator not found")


@router.get("/recommendations")
def get_recommendations(refresh: bool = False):
    return video_recommendation_service.get_recommendations(refresh=refresh)


@router.post("/videos/watched")
def set_video_watched(req: dict):
    try:
        state = video_recommendation_service.set_video_watched(
            video_id=req.get("id"),
            watched=bool(req.get("watched", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "state": state}


@router.post("/videos/discuss")
def discuss_video(req: dict):
    try:
        return video_recommendation_service.prepare_video_discussion(
            video_id=req.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/videos/discuss-captions")
def discuss_video_with_captions(req: dict):
    try:
        return video_recommendation_service.prepare_caption_discussion(
            video_id=req.get("id"),
        )
    except VideoNetworkError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
