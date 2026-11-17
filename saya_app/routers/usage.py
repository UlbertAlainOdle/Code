"""API usage summary + provider model-list proxy.

Usage numbers come from the per-day ledger the chat stream maintains
(saya_app/services/api_usage.py). The model-list proxy exists so the browser
never has to talk to the provider directly (CORS-free, key stays in the
request body and is never logged).
"""

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core_services import log_to_file
from saya_app.config import KNOWLEDGE_BASE_DIR
from saya_app.services.api_usage import summarize

router = APIRouter(prefix="/api/usage", tags=["api_usage"])

LLM_ROUTER = APIRouter(prefix="/api/llm", tags=["llm_tools"])


@router.get("/summary")
def usage_summary(days: int = 7):
    return summarize(str(KNOWLEDGE_BASE_DIR), days=days)


class ModelListRequest(BaseModel):
    api_url: str = ""
    api_key: str = ""


@LLM_ROUTER.post("/models")
def list_provider_models(req: ModelListRequest):
    """Proxy GET {api_url}/models server-side for the settings datalist."""
    from saya_app.config import DEFAULT_API_URL
    import os

    base = str(req.api_url or os.environ.get("API_URL", DEFAULT_API_URL) or DEFAULT_API_URL).strip()
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    url = f"{base}/models"
    key = str(req.api_key or os.environ.get("API_KEY", "") or "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        response = requests.get(url, headers=headers, timeout=(5, 15))
    except requests.RequestException as exc:
        log_to_file(f"Model list proxy unreachable: {url} - {exc}")
        raise HTTPException(status_code=502, detail=f"Provider unreachable: {exc}")
    if response.status_code != 200:
        detail = (response.text or "").strip()[:200]
        raise HTTPException(status_code=response.status_code, detail=detail or "Provider rejected the model list request")
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Provider returned a non-JSON model list")
    models = []
    seen = set()
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(items, list):
        for item in items:
            model_id = ""
            if isinstance(item, dict):
                model_id = str(item.get("id") or item.get("name") or "").strip()
            elif isinstance(item, str):
                model_id = item.strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                models.append(model_id)
    models.sort(key=str.lower)
    return {"models": models[:500], "count": len(models)}
