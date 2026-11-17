import json
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
import io
import wave
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from saya_app.services.local_tts import LocalTTS, VoiceSettings, settings_dict
from saya_app.routers import tts


@pytest.fixture
def service(tmp_path):
    return LocalTTS(root=tmp_path, data_dir=tmp_path / "tts")


def test_settings_persist_and_validate(service):
    value = VoiceSettings(temperature=0.75, auto_read=True, seed=-1)
    service.save_settings(value)
    assert service.settings().auto_read
    assert service.settings().temperature == 0.75
    with pytest.raises(ValidationError):
        VoiceSettings(temperature=float("nan"))
    with pytest.raises(ValidationError):
        VoiceSettings(chunk_chars=2000)
    with pytest.raises(ValidationError):
        VoiceSettings(voice_id="../settings")
    with pytest.raises(ValueError):
        service.save_settings(VoiceSettings(voice_id="a" * 32))


def test_paths_and_clone_reference_validation(service):
    with pytest.raises(ValueError):
        service.audio_path("../private")
    with pytest.raises(ValueError):
        service.synthesize("你好", VoiceSettings(mode="clone"))
    path = service.audio_path("a" * 32, "voices")
    path.parent.mkdir(parents=True)
    path.with_suffix(".json").write_text(json.dumps({"text": ""}))
    with pytest.raises(ValueError, match="参考音频"):
        service.synthesize("你好", VoiceSettings(mode="clone", voice_id="a" * 32))
    with patch.object(service, "rpc", return_value={"duration": 3}) as rpc:
        service.synthesize("你好", VoiceSettings(mode="clone", voice_id="a" * 32, x_vector_only=True))
        assert rpc.call_args.kwargs["settings"]["x_vector_only"]


def test_save_designed_voice_keeps_exact_reference_text(service):
    identifier = "b" * 32
    path = service.audio_path(identifier)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"RIFFtest")
    path.with_suffix(".json").write_text(json.dumps({"text": "博士，你回来啦。", "duration": 5}), encoding="utf-8")
    voice = service.save_preview(identifier, "Saya")
    assert voice["text"] == "博士，你回来啦。"
    assert service.audio_path(voice["id"], "voices").read_bytes() == b"RIFFtest"
    assert service.voices()[0]["name"] == "Saya"


def test_local_routes_binary_audio_disabled_and_settings(service):
    app = FastAPI()
    app.include_router(tts.router)
    with patch.object(tts, "local_tts", service), TestClient(app) as client:
        assert client.get("/api/tts/status").json()["installed"] is False
        assert client.put("/api/tts/settings", json={"enabled": False}).status_code == 200
        assert client.post("/api/tts/synthesize", json={"text": "你好"}).status_code == 409
        assert client.post("/api/tts/preview", json={"text": " "}).status_code == 400
        assert client.post("/api/tts/preview", json={"text": "x" * 1001}).status_code == 422
        assert client.get("/api/tts/audio/bad-id").status_code == 400
        client.put("/api/tts/settings", json=settings_dict(VoiceSettings()))
        identifier = "c" * 32
        path = service.audio_path(identifier)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"RIFF-WAV")
        with patch.object(service, "synthesize", return_value={"id": identifier}):
            result = client.post("/api/tts/synthesize", json={"text": "你好"})
            assert result.headers["content-type"] == "audio/wav"
            assert result.content == b"RIFF-WAV"
        client.put("/api/tts/settings", json={"provider": "elevenlabs"})
        assert client.post("/api/tts/synthesize", json={"text": "你好"}).status_code == 409


def test_streaming_settings_validate_and_fallback_without_install(service):
    assert service.effective_engine(VoiceSettings()) == "transformers"
    with pytest.raises(ValidationError):
        VoiceSettings(stream_buffer_seconds=0)
    with pytest.raises(ValidationError):
        VoiceSettings(engine="arbitrary_remote")
    (service.root / ".runtime").mkdir()
    (service.root / ".runtime/qwen-omni-installed.json").write_text("{}")
    assert service.effective_engine(VoiceSettings()) == "vllm_omni"
    assert service.effective_engine(VoiceSettings(engine="transformers")) == "transformers"


def test_omni_payload_preserves_voice_and_sampling(service):
    value = VoiceSettings(mode="clone", voice_id="a" * 32, temperature=0.7, repetition_penalty=1.2)
    payload = service.omni.payload("你好。", value, {"text": "参考文字。"}, streaming=True)
    assert payload["task_type"] == "Base"
    assert payload["ref_text"] == "参考文字。"
    assert payload["stream_format"] == "audio"
    assert payload["sample_rate"] == 24000
    assert payload["extra_params"]["temperature"] == 0.7
    assert payload["extra_params"]["repetition_penalty"] == 1.2


def test_accelerated_preview_saves_real_wav_metadata(service):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "NONE"))
        wav.writeframes(b"\x01\x00" * 24000)
    with patch.object(service.omni, "synthesize", return_value=output.getvalue()), patch.object(service, "_watch_idle"):
        result = service.synthesize("你好。", VoiceSettings(engine="vllm_omni"))
    assert result["duration"] == 1
    assert result["engine"] == "vllm_omni"
    assert service.audio_path(result["id"]).read_bytes() == output.getvalue()
    assert service.audio_path(result["id"]).with_suffix(".json").exists()


def test_native_synthesis_cleans_symbols_and_adapts_language_without_saving(service):
    value = VoiceSettings(language="Chinese", engine="transformers")
    with patch.object(service, "rpc", return_value={"duration": 1}) as rpc:
        service.synthesize("请说 hello world 😊。[捂脸]", value)
        assert rpc.call_args.kwargs["text"] == "请说 hello world。"
        assert rpc.call_args.kwargs["settings"]["language"] == "Auto"
        assert value.language == "Chinese"
        assert not (service.data / 'settings.json').exists()
    with patch.object(service, "rpc") as rpc, pytest.raises(ValueError, match="没有可朗读"):
        service.synthesize("😊❤️ :smile:", value)
    rpc.assert_not_called()


def test_stream_route_cleans_before_gpu_and_uses_request_language(service):
    app = FastAPI()
    app.include_router(tts.router)
    value = VoiceSettings(engine="vllm_omni", language="Chinese")
    service.save_settings(value)
    response = MagicMock()
    async def audio():
        yield b"\x01\x00" * 240
    response.aiter_bytes = audio
    response.aclose = AsyncMock()
    upstream = MagicMock(aclose=AsyncMock())
    with patch.object(tts, "local_tts", service), patch.object(service, "_watch_idle"), patch.object(service.omni, "open_stream", new=AsyncMock(return_value=(upstream, response))) as generate, patch.object(service, "load"), TestClient(app) as client:
        result = client.post("/api/tts/stream", json={"text": "请说 hello world😊。"})
        assert result.status_code == 200
        assert generate.call_args.args[0] == "请说 hello world。"
        assert generate.call_args.args[1].language == "Auto"
        assert service.settings().language == "Chinese"
        generate.reset_mock()
        assert client.post("/api/tts/stream", json={"text": "😊❤️!!!"}).status_code == 400
        generate.assert_not_called()


def test_stream_route_forwards_pcm_and_closes_upstream(service):
    app = FastAPI()
    app.include_router(tts.router)
    value = VoiceSettings(engine="vllm_omni")
    service.save_settings(value)
    response = MagicMock()
    async def audio():
        yield b"\x01\x00" * 240
        yield b"\x02\x00" * 240
    response.aiter_bytes = audio
    response.aclose = AsyncMock()
    upstream = MagicMock(aclose=AsyncMock())
    with patch.object(tts, "local_tts", service), patch.object(service, "_watch_idle"), patch.object(service.omni, "open_stream", new=AsyncMock(return_value=(upstream, response))), patch.object(service.omni, "finished") as finished, patch.object(service, "load"), TestClient(app) as client:
        result = client.post("/api/tts/stream", json={"text": "你好。"})
        assert result.status_code == 200
        assert result.headers["x-audio-format"] == "s16le"
        assert result.headers["x-audio-sample-rate"] == "24000"
        assert len(result.content) == 960
        response.aclose.assert_awaited_once()
        upstream.aclose.assert_awaited_once()
        finished.assert_called_once()


def test_disconnect_during_model_loading_does_not_start_speech(service):
    with patch.object(service.omni, "ensure"), patch("saya_app.services.omni_tts.httpx.AsyncClient") as client:
        with pytest.raises(RuntimeError, match="取消"):
            asyncio.run(service.omni.open_stream("不应朗读这句话。", VoiceSettings(), is_disconnected=AsyncMock(return_value=True)))
        assert service.omni.active == 0
        client.assert_not_called()


@pytest.mark.parametrize("settings", [VoiceSettings(enabled=False), VoiceSettings(provider="elevenlabs")])
def test_disabling_local_voice_releases_resident_gpu(service, settings):
    with patch.object(service, "unload") as unload:
        service.save_settings(settings)
        unload.assert_called_once()
