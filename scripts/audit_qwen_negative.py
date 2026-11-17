"""Test negative directions with ICL vs the same speaker's x-vector only."""
import json
from pathlib import Path
import sys
import time
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from saya_app.services.local_tts import local_tts
from saya_app.services.speech_prosody import Prosody, process_wav
from scripts.audit_qwen_direction import audio_info, TEXTS
import httpx

CONDITIONS = {
    "neutral": (False, ""),
    "neutral-xvec": (True, ""),
    "sad": (False, "用明显悲伤、失落和疲惫的语气说。压低声音，语调沉重，重点处有长一点的犹豫停顿，句尾无力地下沉。"),
    "sad-xvec": (True, "用明显悲伤、失落和疲惫的语气说。压低声音，语调沉重，重点处有长一点的犹豫停顿，句尾无力地下沉。"),
    "stressed": (False, "用明显紧张、不安和谨慎的语气说。声线收紧，压着声音，重音后短暂犹豫，短句结尾收住，表达克制而绷紧。"),
    "stressed-xvec": (True, "用明显紧张、不安和谨慎的语气说。声线收紧，压着声音，重音后短暂犹豫，短句结尾收住，表达克制而绷紧。"),
}
LABELS = {"neutral": "原参考 ICL · 平静", "neutral-xvec": "仅原音色向量 · 平静", "sad": "原参考 ICL · 加强难过指令", "sad-xvec": "仅原音色向量 · 加强难过指令", "stressed": "原参考 ICL · 加强紧张指令", "stressed-xvec": "仅原音色向量 · 加强紧张指令"}


def main():
    out = ROOT / "docs/experiments/qwen-mood/direction/negative"
    out.mkdir(parents=True, exist_ok=True)
    settings = local_tts.settings().model_copy(update={"engine": "vllm_omni", "mode": "clone", "mood_prosody": False, "playback_rate": 1.0})
    ref = local_tts.audio_path(settings.voice_id, "voices")
    paths = [ref, ref.with_suffix(".json"), local_tts.data / "settings.json", ROOT / "knowledge_base/emotion_state.json"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    texts = {k: TEXTS[k] for k in ("arrival", "comfort")}
    report = dict(texts=texts, labels=LABELS, settings=settings.model_dump(), results=[], user_feedback_v2="some changes; sad/stressed weak, still lively especially stressed", listening_status="negative control test pending", reference_sha256=before[str(ref)])
    def save():
        (out / "direction.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        reference = local_tts.reference(settings)
        local_tts.omni.ensure("clone")
        for case, text in texts.items():
            for seed in (42, 77):
                audio_set = {}
                for mood, (xvec, instruction) in CONDITIONS.items():
                    value = settings.model_copy(update={"seed": seed, "x_vector_only": xvec})
                    payload = local_tts.omni.payload(text, value, reference, instructions=instruction)
                    started = time.perf_counter()
                    with httpx.Client(trust_env=False, timeout=120) as client:
                        response = client.post(local_tts.omni.url + "/v1/audio/speech", json=payload)
                    response.raise_for_status()
                    audio = response.content
                    name = f"{case}-{seed}-{mood}"
                    (out / (name + ".wav")).write_bytes(audio)
                    info = audio_info(audio)
                    audio_set[mood] = (audio, info)
                    result = dict(case=case, seed=seed, mood=mood, x_vector_only=xvec, instruction=instruction, file=name + ".wav", elapsed_seconds=time.perf_counter() - started, **info)
                    report["results"].append(result)
                    print(json.dumps({k:result[k] for k in ("case","seed","mood","duration")}), flush=True)
                    save()
                target_duration = audio_set["neutral"][1]["duration"]
                target_rms = min(info["rms"] for _, info in audio_set.values()) * 0.85
                for mood, (audio, info) in audio_set.items():
                    plan = Prosody(mood, info["duration"] / target_duration, target_rms / info["rms"])
                    normalized = process_wav(audio, plan)
                    filename = f"{case}-{seed}-{mood}-matched.wav"
                    (out / filename).write_bytes(normalized)
                    next(r for r in report["results"] if r["case"] == case and r["seed"] == seed and r["mood"] == mood).update(matched_file=filename, matched=audio_info(normalized), normalization=plan.metadata())
                save()
    finally:
        local_tts.unload()
        report["state_unchanged"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() == before[str(p)] for p in paths}
        save()


if __name__ == "__main__":
    main()
