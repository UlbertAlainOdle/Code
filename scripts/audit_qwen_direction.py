"""Omni Base direction experiment; same reference/text, 3 texts x 2 seeds.

Original and duration/RMS matched comparisons are retained. Uses only local
weights. Saved preferences, reference files and mood state are hashed unchanged.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services.local_tts import local_tts
from saya_app.services.speech_prosody import Prosody, process_wav, MODEL_INSTRUCTIONS

TEXTS = {
    "arrival": "博士，你终于回来了。我一直在等你。先坐下来，慢慢告诉我今天发生了什么。",
    "waiting": "这件事真的让我很在意。你说会来的，可是我等了很久。现在，我想听你亲口解释。",
    "comfort": "我们可以先停一下。刚才的事情已经过去了。你不用急着回答，我会在这里陪着你。",
}
INSTRUCTIONS = {key: MODEL_INSTRUCTIONS[key] for key in ("neutral", "happy", "sad", "stressed")}


def audio_info(content):
    with wave.open(io.BytesIO(content)) as wav:
        rate = wav.getframerate()
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
        samples = struct.unpack("<" + "h" * wav.getnframes(), wav.readframes(wav.getnframes()))
    rms = math.sqrt(sum(float(x) ** 2 for x in samples) / len(samples)) / 32768
    return dict(duration=len(samples) / rate, rms=rms, sha256=hashlib.sha256(content).hexdigest())


def main():
    out = ROOT / "docs/experiments/qwen-mood/direction"
    out.mkdir(parents=True, exist_ok=True)
    settings = local_tts.settings().model_copy(update={"engine": "vllm_omni", "mode": "clone", "mood_prosody": False, "playback_rate": 1.0})
    ref = local_tts.audio_path(settings.voice_id, "voices")
    files = [ref, ref.with_suffix(".json"), local_tts.data / "settings.json", ROOT / "knowledge_base/emotion_state.json"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    report = dict(texts=TEXTS, instructions=INSTRUCTIONS, settings=settings.model_dump(), results=[], user_feedback_v1="Only speed changes; no perceived emotional phrasing", listening_status="v2 pending", reference_sha256=before[str(ref)])
    def save():
        (out / "direction.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        reference = local_tts.reference(settings)
        local_tts.omni.ensure("clone")
        for case, text in TEXTS.items():
            for seed in (42, 77):
                value = settings.model_copy(update={"seed": seed})
                audio_set = {}
                for mood, instruction in INSTRUCTIONS.items():
                    payload = local_tts.omni.payload(text, value, reference)
                    if instruction:
                        payload["instructions"] = instruction
                    import httpx
                    started = time.perf_counter()
                    with httpx.Client(trust_env=False, timeout=120) as client:
                        response = client.post(local_tts.omni.url + "/v1/audio/speech", json=payload)
                    response.raise_for_status()
                    name = f"{case}-{seed}-{mood}"
                    audio = response.content
                    (out / (name + ".wav")).write_bytes(audio)
                    info = audio_info(audio)
                    audio_set[mood] = (audio, info)
                    result = dict(case=case, seed=seed, mood=mood, instruction=instruction, file=name + ".wav", elapsed_seconds=time.perf_counter() - started, **info)
                    report["results"].append(result)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    save()
                target_rms = min(info["rms"] for _, info in audio_set.values()) * 0.85
                target_duration = audio_set["neutral"][1]["duration"]
                for mood, (audio, info) in audio_set.items():
                    # Remove average speed/loudness as explanations for A/B.
                    plan = Prosody(mood, info["duration"] / target_duration, target_rms / info["rms"])
                    matched = process_wav(audio, plan)
                    name = f"{case}-{seed}-{mood}-matched.wav"
                    (out / name).write_bytes(matched)
                    next(r for r in report["results"] if r["case"] == case and r["seed"] == seed and r["mood"] == mood).update(matched_file=name, matched=audio_info(matched), normalization=plan.metadata())
                save()
    finally:
        local_tts.unload()
        report["state_unchanged"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() == before[str(p)] for p in files}
        save()


if __name__ == "__main__":
    main()
