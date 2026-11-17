"""Local speech settings, audio assets and GPU lifecycle endpoints."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
import anyio
import httpx
import asyncio
import logging
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from saya_app.services.local_tts import VoiceSettings, local_tts, settings_dict
from saya_app.services import speech_prosody
from saya_app.services.speech_text import prepare_speech

router = APIRouter(prefix="/api/tts", tags=["speech"])
router.add_event_handler("shutdown", local_tts.unload)


async def preload_streaming_voice():
    settings = local_tts.settings()
    if settings.enabled and settings.provider == "qwen_local" and local_tts.effective_engine(settings) == "vllm_omni":
        async def warm():
            try:
                await asyncio.to_thread(local_tts.load)
            except Exception:
                logging.getLogger(__name__).exception("Streaming voice preload failed")
        asyncio.create_task(warm())


router.add_event_handler("startup", preload_streaming_voice)


class SpeechStreamingResponse(StreamingResponse):
    async def stream_response(self, send):
        try:
            await super().stream_response(send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


class SpeechRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    settings: Optional[VoiceSettings] = None
    mood: speech_prosody.SpeechMood = "auto"
    session_id: Optional[str] = Field(None, max_length=200)


class SaveVoiceRequest(BaseModel):
    preview_id: str = Field(..., pattern=r"^[a-f0-9]{32}$")
    name: str = Field("设计声音", max_length=100)


async def call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except ValueError as error:
        raise HTTPException(400, str(error))
    except (RuntimeError, OSError, httpx.HTTPError) as error:
        raise HTTPException(503, str(error))


@router.get("/settings")
async def get_settings():
    return settings_dict(await call(local_tts.settings))


@router.put("/settings")
async def save_settings(settings: VoiceSettings):
    return settings_dict(await call(local_tts.save_settings, settings))


@router.get("/status")
async def status():
    return await call(local_tts.status)


@router.post("/load")
async def load():
    return await call(local_tts.load)


@router.post("/unload")
async def unload():
    return await call(local_tts.unload)


@router.post("/preview")
async def preview(request: SpeechRequest):
    if not request.text.strip():
        raise HTTPException(400, "请输入试听文字。")
    settings = request.settings or await call(local_tts.settings)
    plan = await call(speech_prosody.resolve, settings, request.mood, engine=local_tts.effective_engine(settings), session_id=request.session_id)
    return await call(local_tts.synthesize, request.text.strip(), settings, prosody=plan)


@router.get("/prosody")
async def prosody(session_id: Optional[str] = None):
    """Freeze this mood in the client for all sentences of a read session."""
    settings = await call(local_tts.settings)
    return (await call(speech_prosody.resolve, settings, engine=local_tts.effective_engine(settings), session_id=session_id)).metadata()


@router.post("/synthesize")
async def synthesize(request: SpeechRequest):
    if not request.text.strip():
        raise HTTPException(400, "请输入朗读文字。")
    settings = await call(local_tts.settings)
    if not settings.enabled:
        raise HTTPException(409, "语音功能已关闭，请在设置中开启。")
    if settings.provider != "qwen_local":
        raise HTTPException(409, "当前语音服务为 ElevenLabs。")
    settings = request.settings or settings
    plan = await call(speech_prosody.resolve, settings, request.mood, engine=local_tts.effective_engine(settings), session_id=request.session_id)
    result = await call(local_tts.synthesize, request.text.strip(), settings, prosody=plan)
    path = local_tts.audio_path(result["id"])
    return Response(await call(path.read_bytes), media_type="audio/wav", headers={"X-Speech-Mood": plan.mood, "X-Speech-Prosody": plan.method})


@router.get("/audio/{identifier}")
async def audio(identifier: str):
    path = await call(local_tts.audio_path, identifier)
    if not path.exists():
        raise HTTPException(404, "音频已过期，请重新试听。")
    return FileResponse(path, media_type="audio/wav")


@router.post("/stream")
async def stream_speech(request: SpeechRequest, connection: Request):
    saved = await call(local_tts.settings)
    if not saved.enabled or saved.provider != "qwen_local":
        raise HTTPException(409, "请开启本地语音服务。")
    settings = request.settings or saved
    if not request.text.strip():
        raise HTTPException(400, "请输入朗读文字。")
    if local_tts.effective_engine(settings) != "vllm_omni":
        raise HTTPException(409, "当前后端不支持音频流式生成，请选择流式加速后端。")
    request_text, settings = await call(prepare_speech, request.text, settings)
    reference = await call(local_tts.reference, settings)
    plan = await call(speech_prosody.resolve, settings, request.mood, engine=local_tts.effective_engine(settings), session_id=request.session_id)
    stream_plan = plan.for_stream(settings.playback_rate)
    processor = await call(speech_prosody.PCMProsody, stream_plan) if stream_plan.changed else None
    try:
        await call(local_tts._unload_worker)
        local_tts._watch_idle()
        kwargs = {"instructions": plan.instructions} if plan.instructions else {}
        client, response = await local_tts.omni.open_stream(request_text, settings, reference, is_disconnected=connection.is_disconnected, **kwargs)
    except BaseException as error:
        if processor:
            with anyio.CancelScope(shield=True):
                await call(processor.close)
        if isinstance(error, (RuntimeError, OSError, httpx.HTTPError)):
            raise HTTPException(503, str(error))
        raise

    async def chunks():
        source = response.aiter_bytes()
        filtered = processor.chunks(source) if processor else source
        try:
            async for chunk in filtered:
                if chunk:
                    yield chunk
        finally:
            with anyio.CancelScope(shield=True):
                await filtered.aclose()
                if processor:
                    await call(processor.close)
                await response.aclose()
                await client.aclose()
            local_tts.omni.finished()

    return SpeechStreamingResponse(chunks(), media_type="audio/pcm", headers={"X-Audio-Sample-Rate": "24000", "X-Audio-Channels": "1", "X-Audio-Format": "s16le", "X-Audio-Playback-Rate": "1", "X-Speech-Mood": plan.mood, "X-Speech-Prosody": plan.method, "Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/voices")
async def voices():
    return await call(local_tts.voices)


@router.post("/voices/from-preview")
async def save_voice(request: SaveVoiceRequest):
    return await call(local_tts.save_preview, request.preview_id, request.name)


@router.post("/voices/upload")
async def upload(request: Request, name: str = "参考声音", text: str = ""):
    if len(name) > 100 or len(text) > 2000:
        raise HTTPException(400, "名称或参考文字过长。")
    content = bytearray()
    async for part in request.stream():
        content.extend(part)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "参考音频不能超过 10 MB。")
    if not content:
        raise HTTPException(400, "请选择一个音频文件。")
    return await call(local_tts.upload_voice, bytes(content), name, text)
