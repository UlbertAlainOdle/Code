"""Real local HTTP/GPU audit with transient settings and immutable reference.

Run with Saya's Python. Starts a small TTS-only server on 18080 for the audit,
then closes it and its own GPU service. Does not write saved preferences/mood.
"""
import argparse
import hashlib
import io
import json
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
from fastapi import FastAPI
from saya_app.routers import tts
from saya_app.services.local_tts import local_tts
from saya_app.services.speech_prosody import map_mood, process_wav
from scripts.benchmark_streaming_tts import playout

TEXT = "博士，我们先把今天的事情安排好，然后一起休息一会儿。"


def duration(content):
    with wave.open(io.BytesIO(content)) as wav:
        return wav.getnframes() / wav.getframerate()


def pcm_wav(content):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "NONE"))
        wav.writeframes(content)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    output = ROOT / "docs/experiments/qwen-mood"
    output.mkdir(parents=True, exist_ok=True)
    settings = local_tts.settings().model_copy(update={"engine": "vllm_omni", "mode": "clone", "mood_prosody": True, "mood_control": "timing", "playback_rate": 1.0})
    ref = local_tts.audio_path(settings.voice_id, "voices")
    watched = [ref, ref.with_suffix(".json"), local_tts.data / "settings.json", ROOT / "knowledge_base/emotion_state.json"]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
    report = dict(text=TEXT, settings=settings.model_dump(), reference_sha256=before[str(ref)],
                  comparison_source="one generated clone waveform reused for DSP A/B; avoids random sampling confound",
                  listening_status="human listening not performed", results=[], instruction_probe=[])
    app = FastAPI()
    app.include_router(tts.router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{args.port}/api/tts"

    def save_report():
        (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        with httpx.Client(trust_env=False, timeout=600) as client:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.1)
            if not server.started:
                raise RuntimeError("Audit HTTP server failed to start")
            started = time.perf_counter()
            response = client.post(url + "/preview", json={"text": TEXT, "settings": settings.model_dump(), "mood": "neutral"})
            response.raise_for_status()
            preview = response.json()
            source = client.get(f"http://127.0.0.1:{args.port}" + preview["url"]).content
            report["cold_preview_seconds"] = time.perf_counter() - started
            for mood in ("neutral", "happy", "excited", "sad", "stressed"):
                started = time.perf_counter()
                plan = map_mood(settings, mood)
                audio = process_wav(source, plan)
                (output / (mood + ".wav")).write_bytes(audio)
                result = dict(mood=mood, **{k: v for k, v in plan.metadata().items() if k != "mood"},
                              duration=duration(audio), dsp_seconds=time.perf_counter() - started,
                              sha256=hashlib.sha256(audio).hexdigest())
                report["results"].append(result)
                print(json.dumps(result), flush=True)
                save_report()
            # Exercise both ordinary binary WAV and the accelerated PCM route.
            response = client.post(url + "/synthesize", json={"text": TEXT, "settings": settings.model_dump(), "mood": "sad"})
            response.raise_for_status()
            (output / "api-sad.wav").write_bytes(response.content)
            report["wav_route"] = dict(duration=duration(response.content), mood=response.headers.get("x-speech-mood"))
            arrivals, content = [], bytearray()
            started = time.perf_counter()
            with client.stream("POST", url + "/stream", json={"text": TEXT * 3, "settings": settings.model_dump(), "mood": "excited"}) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    arrivals.append(dict(seconds=time.perf_counter() - started, audio_seconds=len(chunk) / 48000))
                    content.extend(chunk)
            if not content or len(content) % 2:
                raise RuntimeError("Invalid stream PCM")
            (output / "api-stream-excited.wav").write_bytes(pcm_wav(content))
            report["stream_route"] = dict(duration=len(content) / 48000, generated_seconds=time.perf_counter() - started,
                                          first_packet_seconds=arrivals[0]["seconds"], packets=len(arrivals),
                                          **playout(arrivals, settings.stream_buffer_seconds))
            save_report()
            # vLLM accepts instructions for Base and prefixes them despite the
            # model card lacking control support. Record behavior, not support.
            reference = local_tts.reference(settings)
            for label, instruction in [("baseline", None), ("baseline-repeat", None),
                                       ("happy", "用非常开心、兴奋的语气说。"), ("sad", "用非常悲伤、低落的语气说。")]:
                payload = local_tts.omni.payload(TEXT, settings, reference)
                if instruction:
                    payload["instructions"] = instruction
                started = time.perf_counter()
                response = client.post(local_tts.omni.url + "/v1/audio/speech", json=payload)
                result = dict(label=label, instruction=instruction, status=response.status_code, elapsed_seconds=time.perf_counter() - started)
                if response.is_success:
                    (output / ("omni-instruct-" + label + ".wav")).write_bytes(response.content)
                    result.update(duration=duration(response.content), sha256=hashlib.sha256(response.content).hexdigest())
                else:
                    result["error"] = response.text[:1000]
                report["instruction_probe"].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                save_report()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        local_tts.unload()
        report["state_unchanged"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() == before[str(path)] for path in watched}
        save_report()


if __name__ == "__main__":
    main()
