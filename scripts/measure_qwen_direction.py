"""Measure phrase-level changes beyond uniform speed/loudness.

Run in qwen-tts venv. DTW aligns MFCC content before comparing centered F0
and energy. These are acoustic proxies, not emotion recognition or ASR.
"""
import json
import os
import argparse
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default="direction")
    args = parser.parse_args()
    out = root / "docs/experiments/qwen-mood" / args.folder
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    from scipy.ndimage import gaussian_filter1d

    model = Qwen3TTSModel.from_pretrained(str(root / "models/qwen3-tts/Base"), device_map="cuda:0", dtype=torch.bfloat16,
                                        attn_implementation="sdpa", local_files_only=True)
    report = json.loads((out / "direction.json").read_text(encoding="utf-8"))
    voice_id = report["settings"]["voice_id"]

    def features(path):
        y, sr = sf.read(str(path), dtype="float32")
        hop = 240
        f0, voiced, _ = librosa.pyin(y, sr=sr, fmin=65, fmax=700, frame_length=1024, hop_length=hop)
        rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop, n_fft=1024)[1:]
        mask = rms < max(0.0015, float(np.quantile(rms, 0.95)) * 0.07)
        starts = np.where(np.diff(np.r_[False, mask, False].astype(int)) == 1)[0]
        ends = np.where(np.diff(np.r_[False, mask, False].astype(int)) == -1)[0]
        duration = len(y) / sr
        pauses = [dict(start=round(s * hop / sr, 3), seconds=round((e-s) * hop / sr, 3), fraction=round(s * hop / sr / duration, 3))
                  for s, e in zip(starts, ends) if (e-s) * hop / sr >= 0.10 and s * hop / sr > 0.1 and e * hop / sr < duration - 0.1]
        samples = librosa.resample(y, orig_sr=sr, target_sr=model.model.speaker_encoder_sample_rate)
        with torch.inference_mode():
            emb = model.model.extract_speaker_embedding(audio=samples, sr=model.model.speaker_encoder_sample_rate).float().cpu().numpy()
        finite = f0[np.isfinite(f0)]
        return dict(y=y, sr=sr, f0=f0, rms=rms, mfcc=mfcc, embedding=emb,
                    summary=dict(duration=duration, f0_median_hz=float(np.median(finite)), f0_spread_semitones=float(12 * np.log2(np.quantile(finite, .9) / np.quantile(finite, .1))),
                                 internal_pauses=pauses, clipped_samples=int(np.sum(np.abs(y) >= 32767/32768))))

    def cos(a, b):
        return float(np.clip(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))

    def aligned(a, b):
        # Exclude MFCC0 (energy), center MFCCs to reduce timbre influence, and
        # align phonetic content rather than simply equating normalized time.
        x, y = a["mfcc"], b["mfcc"]
        scale = np.maximum(np.std(np.concatenate([x, y], axis=1), axis=1, keepdims=True), 1)
        _, path = librosa.sequence.dtw(X=x/scale, Y=y/scale, metric="euclidean", global_constraints=True, band_rad=.25)
        i, j = path[:, 0], path[:, 1]
        f, g = a["f0"][i], b["f0"][j]
        valid = np.isfinite(f) & np.isfinite(g)
        cents = 1200 * np.log2(g[valid] / f[valid])
        energy = 20 * np.log10(np.maximum(b["rms"][j], .0001) / np.maximum(a["rms"][i], .0001))
        return dict(centered_pitch_median_abs_cents=float(np.median(np.abs(cents - np.median(cents)))),
                    centered_energy_median_abs_db=float(np.median(np.abs(energy - np.median(energy)))),
                    aligned_voiced_pairs=int(valid.sum()))

    reference = features(root / "knowledge_base/tts/voices" / (voice_id + ".wav"))
    results = []
    grouped = {}
    for row in report["results"]:
        key = (row["case"], row["seed"])
        if key not in grouped:
            grouped[key] = features(out / f"{key[0]}-{key[1]}-neutral.wav")
        baseline = grouped[key]
        current = baseline if row["mood"] == "neutral" else features(out / row["file"])
        result = dict(case=row["case"], seed=row["seed"], mood=row["mood"], file=row["file"],
                      speaker_cosine_to_neutral=cos(current["embedding"], baseline["embedding"]),
                      speaker_cosine_to_reference=cos(current["embedding"], reference["embedding"]),
                      **current["summary"], **aligned(baseline, current))
        results.append(result)
        print(json.dumps(result), flush=True)
        # Small plot-ready curves; no claim that this is word alignment.
        if row["seed"] == 42:
            curves = dict(seconds=(np.arange(len(current["f0"])) * .01).tolist(),
                          f0_hz=[float(x) if np.isfinite(x) else None for x in current["f0"]],
                          rms=gaussian_filter1d(current["rms"], 2).tolist())
            (out / (Path(row["file"]).stem + "-curve.json")).write_text(json.dumps(curves), encoding="utf-8")
        (out / "measurements.json").write_text(json.dumps(dict(method="librosa.pyin + MFCC DTW; centered local changes; Qwen speaker encoder; no ASR/no listening", results=results), indent=2), encoding="utf-8")
    # Local variation across neutral seeds is a necessary randomness control;
    # changed curves alone cannot establish emotion instruction adherence.
    variations = []
    for case in report["texts"]:
        a, b = grouped[(case, 42)], grouped[(case, 77)]
        variations.append(dict(case=case, seed_a=42, seed_b=77, **aligned(a, b)))
    (out / "measurements.json").write_text(json.dumps(dict(method="librosa.pyin + MFCC DTW; centered local changes; Qwen speaker encoder; no ASR/no listening", results=results, baseline_seed_variation=variations), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
