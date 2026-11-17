"""Exercise model direction through real Saya WAV/PCM routes, without saving settings."""
import hashlib
import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
from fastapi import FastAPI
from saya_app.routers import tts
from saya_app.services.local_tts import local_tts
from saya_app.services.speech_prosody import MODEL_INSTRUCTIONS
from scripts.audit_qwen_direction import TEXTS, audio_info
from scripts.audit_qwen_mood import pcm_wav


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default="direction/routes")
    parser.add_argument("--x-vector-only", action="store_true")
    args = parser.parse_args()
    out = ROOT / "docs/experiments/qwen-mood" / args.folder
    out.mkdir(parents=True, exist_ok=True)
    value = local_tts.settings().model_copy(update={"engine": "vllm_omni", "mode": "clone", "mood_prosody": True, "mood_control": "model", "playback_rate": 1.0})
    if args.x_vector_only:
        value = value.model_copy(update={"x_vector_only": True})
    ref = local_tts.audio_path(value.voice_id, "voices")
    paths = [ref, ref.with_suffix(".json"), local_tts.data / "settings.json", ROOT / "knowledge_base/emotion_state.json"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report = dict(text=TEXTS["arrival"], settings=value.model_dump(), results=[])
    app = FastAPI()
    app.include_router(tts.router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18080, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = "http://127.0.0.1:18080/api/tts"

    def save():
        (out / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.1)
        if not server.started:
            raise RuntimeError("Audit HTTP server failed to start")
        with httpx.Client(trust_env=False, timeout=600) as client:
            for mood in ("neutral", "happy", "sad", "stressed"):
                body = dict(text=report["text"], settings=value.model_dump(), mood=mood)
                preview = client.post(url + "/preview", json=body)
                preview.raise_for_status()
                metadata = preview.json()
                plan = metadata["prosody"]
                assert plan["instructions"] == MODEL_INSTRUCTIONS[mood]
                assert plan["tempo"] == 1 and plan["gain"] == 1
                wave = client.get("http://127.0.0.1:18080" + metadata["url"])
                wave.raise_for_status()
                (out / (mood + "-preview.wav")).write_bytes(wave.content)
                binary = client.post(url + "/synthesize", json=body)
                binary.raise_for_status()
                assert binary.headers["x-speech-prosody"] == "omni_base_direction_v3"
                (out / (mood + "-synthesize.wav")).write_bytes(binary.content)
                pcm = bytearray()
                started = time.perf_counter()
                first = None
                with client.stream("POST", url + "/stream", json=body) as response:
                    response.raise_for_status()
                    assert response.headers["x-speech-prosody"] == "omni_base_direction_v3"
                    assert response.headers["x-audio-playback-rate"] == "1"
                    for chunk in response.iter_bytes():
                        if first is None:
                            first = time.perf_counter() - started
                        pcm.extend(chunk)
                assert pcm and len(pcm) % 2 == 0
                stream = pcm_wav(pcm)
                (out / (mood + "-stream.wav")).write_bytes(stream)
                row = dict(mood=mood, prosody=plan, preview=audio_info(wave.content), synthesize=audio_info(binary.content), stream=audio_info(stream), first_packet_seconds=first, stream_generated_seconds=time.perf_counter() - started)
                if value.x_vector_only and mood in ("neutral", "sad", "stressed"):
                    listened = json.loads((ROOT / "docs/experiments/qwen-mood/direction/negative/direction.json").read_text(encoding="utf-8"))
                    prior = next(r for r in listened["results"] if r["case"] == "arrival" and r["seed"] == value.seed and r["mood"] == mood + "-xvec")
                    assert prior["instruction"] == plan["instructions"]
                    row["matches_listened_original"] = all(row[k]["sha256"] == prior["sha256"] for k in ("preview", "synthesize", "stream"))
                report["results"].append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                save()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        local_tts.unload()
        report["state_unchanged"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() == before[str(p)] for p in paths}
        save()


if __name__ == "__main__":
    main()
