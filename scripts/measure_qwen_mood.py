"""Run in the native qwen-tts venv: pitch + Qwen speaker-encoder proxies.

These measurements cannot replace human listening or prove emotional acting.
"""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root
    output = root / "docs/experiments/qwen-mood"
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    model = Qwen3TTSModel.from_pretrained(str(root / "models/qwen3-tts/Base"), device_map="cuda:0",
                                        dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
    settings = json.loads((root / "knowledge_base/tts/settings.json").read_text(encoding="utf-8"))
    reference = root / "knowledge_base/tts/voices" / (settings["voice_id"] + ".wav")

    def measure(path):
        samples, rate = sf.read(str(path), dtype="float32")
        if samples.ndim != 1:
            raise ValueError("Expected mono")
        resampled = librosa.resample(samples, orig_sr=rate, target_sr=model.model.speaker_encoder_sample_rate)
        with torch.inference_mode():
            embedding = model.model.extract_speaker_embedding(audio=resampled, sr=model.model.speaker_encoder_sample_rate).float().cpu().numpy()
        pitch, voiced, _ = librosa.pyin(samples, sr=rate, fmin=65, fmax=700, frame_length=1024, hop_length=240)
        valid = pitch[voiced & np.isfinite(pitch)]
        return dict(file=path.name, duration=len(samples) / rate, rms=float(np.sqrt(np.mean(samples ** 2))),
                    peak=float(np.max(np.abs(samples))), clipped_samples=int(np.sum(np.abs(samples) >= 32767 / 32768)),
                    f0_median_hz=float(np.median(valid)) if len(valid) else None), embedding

    def cosine(a, b):
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    _, ref_embedding = measure(reference)
    baseline, base_embedding = measure(output / "neutral.wav")
    results = []
    for path in [output / (mood + ".wav") for mood in ("neutral", "happy", "excited", "sad", "stressed")] + sorted(output.glob("omni-instruct-*.wav")):
        result, embedding = measure(path)
        result.update(speaker_cosine_to_neutral=cosine(embedding, base_embedding), speaker_cosine_to_reference=cosine(embedding, ref_embedding),
                      f0_ratio_to_neutral=result["f0_median_hz"] / baseline["f0_median_hz"] if result["f0_median_hz"] else None)
        results.append(result)
        print(json.dumps(result), flush=True)
        (output / "measurements.json").write_text(json.dumps(dict(method="Qwen Base speaker encoder cosine; librosa.pyin median F0; no human listening",
                                                               results=results), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
