import asyncio
import io
import math
import struct
import wave
from types import SimpleNamespace
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saya_app.routers import tts
from saya_app.services.local_tts import LocalTTS, VoiceSettings
from saya_app.services import speech_prosody as sp


def tone(seconds=1, rate=24000):
    return b"".join(struct.pack("<h", round(12000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate)))


def wav(pcm):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "NONE"))
        audio.writeframes(pcm)
    return output.getvalue()


def read_pcm(content):
    with wave.open(io.BytesIO(content)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
        return audio.readframes(audio.getnframes())


def test_mapping_is_opt_in_and_never_changes_voice_or_sampling():
    value = VoiceSettings(mode="clone", mood_prosody=True, mood_control="timing", voice_id="a" * 32)
    before = value.model_dump()
    for mood in sp.PROFILES:
        plan = sp.resolve(value, mood)
        assert 0.88 <= plan.tempo <= 1.14
        assert 0.78 <= plan.gain <= 1
    assert value.model_dump() == before
    with patch.object(sp, "current_mood", side_effect=AssertionError("must not read state")):
        assert sp.resolve(value.model_copy(update={"mood_prosody": False})).method == "none"
        assert sp.resolve(value.model_copy(update={"mode": "design"})).method == "none"
    with patch.object(sp, "current_mood", return_value="sad"):
        assert sp.resolve(value).mood == "sad"
    assert sp.map_mood(value, "unknown").mood == "neutral"


def test_auto_obeys_continuity_disabled_and_uses_shared_snapshot():
    engine = SimpleNamespace(state={"dominant": "sad", "is_locked": True})
    kb = SimpleNamespace(settings={"mood_continuity_enabled": False})
    with patch.dict("sys.modules", core_services=SimpleNamespace(emotion_engine=engine, kb_system=kb)), \
            patch("saya_app.services.mood_continuity.snapshot", return_value=engine.state) as snapshot:
        assert sp.current_mood() == "neutral"
        snapshot.assert_not_called()
        kb.settings["mood_continuity_enabled"] = True
        assert sp.current_mood() == "sad"
        snapshot.assert_called_once_with(engine, enabled=True)
        assert engine.state == {"dominant": "sad", "is_locked": True}


def test_real_filter_changes_duration_without_shifting_tone():
    source = wav(tone(2))
    assert sp.process_wav(source, sp.Prosody()) is source
    value = VoiceSettings(mode="clone", mood_prosody=True, mood_control="timing")
    for mood in ("excited", "sad"):
        plan = sp.map_mood(value, mood)
        pcm = read_pcm(sp.process_wav(source, plan))
        samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
        duration = len(samples) / 24000
        assert duration == pytest.approx(2 / plan.tempo, abs=0.06)
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        assert crossings / duration == pytest.approx(220, abs=3)
        assert max(abs(x) for x in samples) < 32767
    with patch.object(sp.shutil, "which", return_value=None):
        with pytest.raises(RuntimeError, match="FFmpeg"):
            sp.process_wav(source, sp.map_mood(value, "sad"))


def test_pcm_packet_boundaries_do_not_reset_filter_and_cleanup():
    pcm = tone(2)
    plan = sp.Prosody("sad", 0.88, 0.78, "pitch_preserving_dsp_v1")

    async def run():
        async def pieces():
            # Include odd-length chunks, which the upstream transport can emit.
            for start in range(0, len(pcm), 719):
                yield pcm[start:start + 719]
        processor = sp.PCMProsody(plan)
        result = b"".join([chunk async for chunk in processor.chunks(pieces())])
        assert processor.process.poll() == 0
        processor.close()  # idempotent outer route cleanup
        return result

    assert asyncio.run(run()) == read_pcm(sp.process_wav(wav(pcm), plan))


def test_pcm_cancel_kills_process_and_closes_producer():
    async def run():
        stopped = asyncio.Event()
        async def pieces():
            try:
                while True:
                    yield tone(0.1)
                    await asyncio.sleep(0)
            finally:
                stopped.set()
        source = pieces()
        processor = sp.PCMProsody(sp.Prosody("sad", 0.88, 0.78))
        chunks = processor.chunks(source)
        await anext(chunks)
        await chunks.aclose()
        await source.aclose()
        assert stopped.is_set()
        assert processor.process.poll() is not None
        assert processor.errors.closed
    asyncio.run(run())


def test_model_direction_changes_conditioning_without_timing_dsp_or_reference(tmp_path):
    service = LocalTTS(root=tmp_path, data_dir=tmp_path / "tts")
    value = VoiceSettings(mode="clone", mood_prosody=True, mood_control="model", engine="vllm_omni", voice_id="a" * 32)
    before = value.model_dump()
    plan = sp.resolve(value, "sad", engine="vllm_omni")
    assert not plan.changed
    assert plan.instructions == sp.MODEL_INSTRUCTIONS["sad"]
    assert value.model_dump() == before
    reference = {"text": "逐字参考"}
    plain = service.omni.payload("原句。", value, reference)
    directed = service.omni.payload("原句。", value, reference, instructions=plan.instructions)
    assert directed.pop("instructions") == plan.instructions
    assert directed == plain
    with pytest.raises(ValueError, match="流式加速"):
        sp.resolve(value, "sad", engine="transformers")
    assert not sp.resolve(value, "neutral", engine="transformers").instructions
    original = wav(tone(1))
    with patch.object(service, "reference", return_value=reference), patch.object(service, "_watch_idle"), \
            patch.object(service.omni, "synthesize", return_value=original) as model:
        result = service.synthesize("原句。", value, plan)
    assert service.audio_path(result["id"]).read_bytes() == original
    model.assert_called_once_with("原句。", value, reference, instructions=plan.instructions)
    assert result["prosody"]["method"] == "omni_base_direction_v3"
    with pytest.raises(ValueError, match="不能保存"):
        service.save_preview(result["id"], "不能固化表达")


@pytest.mark.parametrize("x_vector_only", [False, True])
def test_model_direction_reaches_both_routes_and_preserves_pcm(tmp_path, x_vector_only):
    service = LocalTTS(root=tmp_path, data_dir=tmp_path / "tts")
    value = VoiceSettings(mode="clone", mood_prosody=True, mood_control="model", engine="vllm_omni", voice_id="a" * 32, x_vector_only=x_vector_only)
    app = FastAPI()
    app.include_router(tts.router)
    pcm = tone(1)
    response = MagicMock(aclose=AsyncMock())
    async def pieces():
        yield pcm
    response.aiter_bytes = pieces
    upstream = MagicMock(aclose=AsyncMock())
    with patch.object(tts, "local_tts", service), patch.object(service, "settings", return_value=value), \
            patch.object(service, "reference", return_value={"text": "原参考"}), patch.object(service, "_watch_idle"), \
            patch.object(service, "load"), patch.object(service.omni, "synthesize", return_value=wav(pcm)) as model, \
            patch.object(service.omni, "open_stream", new=AsyncMock(return_value=(upstream, response))) as open_stream, \
            patch.object(service.omni, "finished"), patch.object(sp, "PCMProsody", side_effect=AssertionError("no timing DSP")), TestClient(app) as client:
        preview = client.post("/api/tts/preview", json={"text": "正文", "mood": "stressed"})
        assert preview.status_code == 200
        assert model.call_args.kwargs["instructions"] == sp.MODEL_INSTRUCTIONS["stressed"]
        assert model.call_args.args[1].x_vector_only is x_vector_only
        assert model.call_args.args[1].voice_id == value.voice_id
        streamed = client.post("/api/tts/stream", json={"text": "正文", "mood": "stressed"})
        assert streamed.content == pcm
        assert streamed.headers["x-speech-prosody"] == "omni_base_direction_v3"
        assert open_stream.call_args.kwargs["instructions"] == sp.MODEL_INSTRUCTIONS["stressed"]
        assert open_stream.call_args.args[1].x_vector_only is x_vector_only
        assert open_stream.call_args.args[1].voice_id == value.voice_id
        neutral = client.post("/api/tts/preview", json={"text": "正文", "mood": "neutral"})
        assert neutral.status_code == 200
        assert model.call_args.args[1].x_vector_only is x_vector_only
        assert "instructions" not in model.call_args.kwargs


def test_route_stream_and_wav_share_mapping_and_bake_speed_once(tmp_path):
    service = LocalTTS(root=tmp_path, data_dir=tmp_path / "tts")
    settings = VoiceSettings(mode="clone", voice_id="a" * 32, mood_prosody=True, mood_control="timing", engine="vllm_omni", playback_rate=1.2)
    app = FastAPI()
    app.include_router(tts.router)
    response = MagicMock(aclose=AsyncMock())
    async def pieces():
        yield tone(1)
    response.aiter_bytes = pieces
    upstream = MagicMock(aclose=AsyncMock())
    with patch.object(tts, "local_tts", service), patch.object(service, "settings", return_value=settings), \
            patch.object(service, "reference", return_value={"text": "固定参考"}), patch.object(service, "_watch_idle"), \
            patch.object(service, "load"), patch.object(service.omni, "synthesize", return_value=wav(tone(1))) as generate, \
            patch.object(service.omni, "open_stream", new=AsyncMock(return_value=(upstream, response))), \
            patch.object(service.omni, "finished") as finished, TestClient(app) as client:
        result = client.post("/api/tts/preview", json={"text": "你好", "mood": "sad"})
        assert result.status_code == 200
        assert result.json()["prosody"]["tempo"] == 0.88
        assert result.json()["duration"] == pytest.approx(1 / 0.88, abs=0.06)
        assert generate.call_args.args[1].voice_id == "a" * 32
        stream = client.post("/api/tts/stream", json={"text": "你好", "mood": "sad"})
        assert stream.status_code == 200
        assert stream.headers["x-speech-mood"] == "sad"
        assert stream.headers["x-audio-playback-rate"] == "1"
        assert len(stream.content) / 48000 == pytest.approx(1 / (0.88 * 1.2), abs=0.06)
        response.aclose.assert_awaited_once()
        upstream.aclose.assert_awaited_once()
        finished.assert_called_once()
        assert client.post("/api/tts/preview", json={"text": "你好", "mood": "angry"}).status_code == 422


def test_native_result_is_processed_in_parent_and_cannot_be_saved_as_reference(tmp_path):
    service = LocalTTS(root=tmp_path, data_dir=tmp_path / "tts")
    settings = VoiceSettings(mode="clone", voice_id="a" * 32, mood_prosody=True, mood_control="timing", engine="transformers")
    def generate(operation, **payload):
        assert operation == "synthesize"
        assert payload["text"] == "原句。"
        assert payload["settings"]["voice_id"] == settings.voice_id
        assert "instruct" not in payload
        path = service.audio_path(payload["identifier"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav(tone(1)))
        return dict(duration=1, sample_rate=24000, mode="clone")
    with patch.object(service, "rpc", side_effect=generate), patch.object(service, "reference", return_value={}):
        result = service.synthesize("原句。", settings, prosody=sp.map_mood(settings, "sad"))
    assert result["duration"] == pytest.approx(1 / 0.88, abs=0.06)
    metadata = json.loads(service.audio_path(result["id"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["prosody"]["mood"] == "sad"
    with pytest.raises(ValueError, match="不能保存"):
        service.save_preview(result["id"], "不可固化的韵律")
    assert service.voices() == []


def test_outer_stream_close_cancels_filter_and_upstream(tmp_path):
    service = LocalTTS(root=tmp_path, data_dir=tmp_path / "tts")
    settings = VoiceSettings(mode="clone", mood_prosody=True, mood_control="timing", engine="vllm_omni")
    stopped = False
    async def pieces():
        nonlocal stopped
        try:
            while True:
                yield tone(0.1)
                await asyncio.sleep(0)
        finally:
            stopped = True
    response = MagicMock(aclose=AsyncMock(), aiter_bytes=pieces)
    upstream = MagicMock(aclose=AsyncMock())
    processors = []
    real_processor = sp.PCMProsody
    def start(plan):
        processors.append(real_processor(plan))
        return processors[-1]
    async def run():
        with patch.object(tts, "local_tts", service), patch.object(service, "settings", return_value=settings), \
                patch.object(service, "reference", return_value={}), patch.object(service, "_watch_idle"), \
                patch.object(service.omni, "open_stream", new=AsyncMock(return_value=(upstream, response))), \
                patch.object(sp, "PCMProsody", side_effect=start), patch.object(service.omni, "finished") as finished:
            stream = await tts.stream_speech(tts.SpeechRequest(text="你好", mood="sad"), MagicMock(is_disconnected=AsyncMock(return_value=False)))
            await anext(stream.body_iterator)
            await stream.body_iterator.aclose()
            assert stopped
            assert processors[0].process.poll() is not None
            response.aclose.assert_awaited_once()
            upstream.aclose.assert_awaited_once()
            finished.assert_called_once()
    asyncio.run(run())
