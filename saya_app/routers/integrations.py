import json
import os
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import models, tmt_client

from core_services import memory_manager


router = APIRouter(tags=["integrations"])


class TranslateReq(BaseModel):
    text: str
    source: str = "en"
    target: str = "zh"
    session_id: Optional[str] = None
    message_index: Optional[int] = None
    secret_id: str = ""
    secret_key: str = ""
    region: str = ""
    endpoint: str = ""


@router.post("/translate")
def translate(req: TranslateReq):
    secret_id = (req.secret_id or os.getenv("SECRET_ID") or "").strip()
    secret_key = (req.secret_key or os.getenv("SECRET_KEY") or "").strip()
    region = (req.region or os.getenv("TENCENT_TMT_REGION") or "ap-guangzhou").strip()
    endpoint = (req.endpoint or os.getenv("TENCENT_TMT_ENDPOINT") or "tmt.tencentcloudapi.com").strip()
    if not secret_id or not secret_key:
        raise HTTPException(
            status_code=503,
            detail="Translation is not configured. Set it in Other API or provide SECRET_ID and SECRET_KEY.",
        )
    if not endpoint or any(char.isspace() for char in endpoint):
        raise HTTPException(status_code=400, detail="Invalid translation endpoint.")

    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile(endpoint=endpoint)
    client_profile = ClientProfile(httpProfile=http_profile)
    client = tmt_client.TmtClient(cred, region, client_profile)

    request = models.TextTranslateRequest()
    params = {
        "SourceText": req.text,
        "Source": req.source,
        "Target": req.target,
        "ProjectId": 0,
    }
    request.from_json_string(json.dumps(params))
    try:
        response = client.TextTranslate(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Translation service error: {exc}")
    if req.session_id:
        session_path = memory_manager._safe_session_path(req.session_id)
        if session_path and os.path.exists(session_path):
            memory_manager.current_session_file = session_path
    if req.message_index is not None:
        memory_manager.update_message_translation(req.message_index, response.TargetText)
    return {"translatedText": response.TargetText}


class TTSRequest(BaseModel):
    text: str
    api_url: str = ""
    api_key: str = ""
    voice_id: str = ""
    model_id: str = ""


@router.post("/tts")
def tts_generate(req: TTSRequest):
    api_url = (req.api_url or os.getenv("ELEVEN_API_URL") or "https://api.elevenlabs.io/v1").strip().rstrip("/")
    api_key = (req.api_key or os.getenv("ELEVEN_API_KEY") or "").strip()
    voice_id = (req.voice_id or os.getenv("ELEVEN_VOICE_ID") or "").strip()
    model_id = (req.model_id or os.getenv("ELEVEN_MODEL_ID") or "eleven_multilingual_v2").strip()
    if not api_key or not voice_id:
        raise HTTPException(
            status_code=503,
            detail="TTS is not configured. Set it in Other API or provide ELEVEN_API_KEY and ELEVEN_VOICE_ID.",
        )
    if not api_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="TTS Base URL must use http:// or https://.")

    if "{voice_id}" in api_url:
        request_url = api_url.replace("{voice_id}", voice_id)
    elif "/text-to-speech/" in api_url:
        request_url = api_url
    elif api_url.endswith("/text-to-speech"):
        request_url = f"{api_url}/{voice_id}"
    else:
        request_url = f"{api_url}/text-to-speech/{voice_id}"

    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": req.text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.75,
        },
    }

    try:
        response = requests.post(
            request_url,
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"TTS service connection failed: {exc}")

    if response.status_code != 200 or "audio/mpeg" not in response.headers.get("Content-Type", ""):
        raise HTTPException(status_code=502, detail=f"TTS service error: {response.status_code} {response.text[:200]}")

    return Response(content=response.content, media_type="audio/mpeg")
