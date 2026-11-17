"""Run in the native qwen-tts venv; never changes the saved voice/settings.

Compare the same Base clone with and without an unsupported instruct kwarg.
Outputs are retained even when the kwarg is rejected or silently ignored.
"""
import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root
    output = args.output or root / "docs/experiments/qwen-mood/native"
    output.mkdir(parents=True, exist_ok=True)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    settings = json.loads((root / "knowledge_base/tts/settings.json").read_text(encoding="utf-8"))
    ref = root / "knowledge_base/tts/voices" / (settings["voice_id"] + ".wav")
    metadata = json.loads(ref.with_suffix(".json").read_text(encoding="utf-8"))
    report = dict(qwen_tts=importlib.metadata.version("qwen-tts"), torch=torch.__version__,
                  voice_id=settings["voice_id"], reference_sha256=hashlib.sha256(ref.read_bytes()).hexdigest(),
                  signature=str(inspect.signature(Qwen3TTSModel.generate_voice_clone)),
                  text="博士，我们先把今天的事情安排好，然后一起休息一会儿。", seed=42, results=[])
    model = Qwen3TTSModel.from_pretrained(str(root / "models/qwen3-tts/Base"), device_map="cuda:0",
                                        dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
    prompt = model.create_voice_clone_prompt(ref_audio=str(ref), ref_text=metadata["text"], x_vector_only_mode=False)
    baseline = None
    for label, instruction in [("neutral", None), ("instruct-happy", "用非常开心、兴奋的语气说。"),
                               ("instruct-sad", "用非常悲伤、低落的语气说。")]:
        started = time.perf_counter()
        torch.manual_seed(report["seed"])
        extra = {} if instruction is None else {"instruct": instruction}
        result = dict(label=label, instruct=instruction)
        try:
            wavs, rate = model.generate_voice_clone(text=report["text"], language="Chinese", voice_clone_prompt=prompt,
                                                    max_new_tokens=512, temperature=0.9, top_p=1.0, top_k=50,
                                                    repetition_penalty=1.05, **extra)
            samples = wavs[0]
            sf.write(str(output / (label + ".wav")), samples, rate, subtype="PCM_16")
            if baseline is None:
                baseline = samples.copy()
            same_shape = baseline.shape == samples.shape
            result.update(accepted=True, samples=len(samples), sample_rate=rate, duration=len(samples) / rate,
                          float_sha256=hashlib.sha256(samples.tobytes()).hexdigest(),
                          identical_to_baseline=bool(same_shape and np.array_equal(baseline, samples)),
                          max_abs_difference=float(np.max(np.abs(baseline - samples))) if same_shape else None)
        except Exception as error:
            result.update(accepted=False, error=repr(error))
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        report["results"].append(result)
        (output / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    report["reference_unchanged"] = hashlib.sha256(ref.read_bytes()).hexdigest() == report["reference_sha256"]
    (output / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
