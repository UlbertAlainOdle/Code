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


# ---------------------------------------------------------------------------
# Weather (feature ported from airi-main's get_weather widget tool).
# Open-Meteo needs no API key, which keeps this dependency-free.
# ---------------------------------------------------------------------------
WEATHER_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_CODE_MAP = {
    0: ("Clear sky", "晴"), 1: ("Mainly clear", "大部晴"), 2: ("Partly cloudy", "多云"), 3: ("Overcast", "阴"),
    45: ("Fog", "雾"), 48: ("Depositing rime fog", "雾凇"), 51: ("Light drizzle", "小毛毛雨"),
    53: ("Moderate drizzle", "毛毛雨"), 55: ("Dense drizzle", "浓毛毛雨"), 61: ("Slight rain", "小雨"),
    63: ("Moderate rain", "中雨"), 65: ("Heavy rain", "大雨"), 66: ("Light freezing rain", "冻雨"),
    67: ("Heavy freezing rain", "强冻雨"), 71: ("Slight snow", "小雪"), 73: ("Moderate snow", "中雪"),
    75: ("Heavy snow", "大雪"), 77: ("Snow grains", "雪粒"), 80: ("Rain showers", "阵雨"),
    81: ("Moderate rain showers", "中阵雨"), 82: ("Violent rain showers", "强阵雨"),
    85: ("Slight snow showers", "小阵雪"), 86: ("Heavy snow showers", "大阵雪"),
    95: ("Thunderstorm", "雷雨"), 96: ("Thunderstorm with hail", "雷雨伴冰雹"), 99: ("Severe thunderstorm with hail", "强雷雨伴冰雹"),
}


def _weather_text(code):
    entry = WEATHER_CODE_MAP.get(int(code or 0), ("Unknown", "未知"))
    return {"text_en": entry[0], "text_cn": entry[1]}


class WeatherRequest(BaseModel):
    location: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@router.post("/api/weather")
def weather(req: WeatherRequest):
    latitude, longitude = req.latitude, req.longitude
    place_name = (req.location or "").strip()
    if latitude is None or longitude is None:
        if not place_name:
            raise HTTPException(status_code=400, detail="Provide a location name or coordinates")
        try:
            geo = requests.get(
                WEATHER_GEOCODING_URL,
                params={"name": place_name, "count": 1, "language": "en", "format": "json"},
                timeout=(5, 10),
            )
            geo.raise_for_status()
            results = (geo.json() or {}).get("results") or []
            if not results:
                raise HTTPException(status_code=404, detail=f"Location not found: {place_name}")
            best = results[0]
            latitude, longitude = best.get("latitude"), best.get("longitude")
            place_name = best.get("name") or place_name
        except HTTPException:
            raise
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Weather geocoding failed: {exc}")
    try:
        forecast = requests.get(
            WEATHER_FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 3,
            },
            timeout=(5, 10),
        )
        forecast.raise_for_status()
        data = forecast.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Weather service failed: {exc}")
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    days = []
    dates = daily.get("time") or []
    for index, date in enumerate(dates[:3]):
        days.append({
            "date": date,
            "weather_code": (daily.get("weather_code") or [None] * (index + 1))[index],
            "temp_max": (daily.get("temperature_2m_max") or [None] * (index + 1))[index],
            "temp_min": (daily.get("temperature_2m_min") or [None] * (index + 1))[index],
            "precipitation_probability": (daily.get("precipitation_probability_max") or [None] * (index + 1))[index],
            **_weather_text((daily.get("weather_code") or [0] * (index + 1))[index]),
        })
    payload = {
        "location": place_name,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": data.get("timezone"),
        "current": {
            "temperature": current.get("temperature_2m"),
            "apparent_temperature": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "weather_code": current.get("weather_code"),
            **_weather_text(current.get("weather_code")),
        },
        "daily": days,
    }
    # Short natural-language line that can be injected into prompts.
    current_day = days[0] if days else {}
    payload["summary_cn"] = (
        f"{place_name}今天{current_day.get('text_cn') or payload['current']['text_cn']}，"
        f"气温 {current_day.get('temp_min', '?')}~{current_day.get('temp_max', '?')}°C，"
        f"当前 {payload['current']['temperature']}°C。"
    )
    payload["summary_en"] = (
        f"{place_name}: {payload['current']['text_en']}, now {payload['current']['temperature']}C, "
        f"today {current_day.get('temp_min', '?')}-{current_day.get('temp_max', '?')}C."
    )
    return payload
