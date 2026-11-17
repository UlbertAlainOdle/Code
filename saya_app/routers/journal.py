"""REST endpoints for Saya's diary."""

from fastapi import APIRouter, HTTPException
from typing import Optional

from pydantic import BaseModel

from ..services.journal import journal_service

router = APIRouter(prefix="/api/journal", tags=["journal"])


class AddEntryRequest(BaseModel):
    content: str
    title: Optional[str] = None
    mood: Optional[str] = None
    date: Optional[str] = None


class WriteDiaryRequest(BaseModel):
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_model_name: Optional[str] = None
    extra_context: str = ""
    response_language: Optional[str] = "auto"


@router.get("/entries")
def list_entries(limit: int = 50, date: Optional[str] = None):
    return journal_service.list(limit=limit, date=date)


@router.post("/add")
def add_entry(req: AddEntryRequest):
    try:
        return journal_service.add(req.content, title=req.title, source="doctor", mood=req.mood, date=req.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/write")
def write_diary(req: WriteDiaryRequest):
    """Saya writes today's entry from the managed daily context."""
    credentials = {
        "api_url": req.api_url,
        "api_key": req.api_key,
        "api_model_name": req.api_model_name,
    }
    try:
        return journal_service.generate(credentials, extra_context=req.extra_context, language=req.response_language or "auto")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/{entry_id}/delete")
def delete_entry(entry_id: str):
    try:
        return journal_service.delete(entry_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Diary entry not found")
