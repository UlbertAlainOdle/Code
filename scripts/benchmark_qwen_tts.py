"""Compare warm single/batch inference without changing saved voice settings.

Run with .runtime/qwen-tts/Scripts/python.exe. Results stay in .runtime.
"""
import argparse
import json
import os
import time
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime/tts-benchmark"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--representative", action="store_true", help="Compare different length texts, single versus batch")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    settings = json.loads((ROOT / "knowledge_base/tts/settings.json").read_text(encoding="utf-8"))
    voice = ROOT / "knowledge_base/tts/voices" / settings["voice_id"]
    metadata = json.loads(voice.with_suffix(".json").read_text(encoding="utf-8"))
    samples, ref_rate = sf.read(voice.with_suffix(".wav"), dtype="float32")
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(str(ROOT / "models/qwen3-tts/Base"), device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    with torch.inference_mode():
        prompt = model.create_voice_clone_prompt(ref_audio=(samples, ref_rate), ref_text=metadata["text"], x_vector_only_mode=False)
    torch.cuda.synchronize()
    prompt_seconds = time.perf_counter() - started
    default_threads = torch.get_num_threads()
    print(json.dumps(dict(load_seconds=load_seconds, prompt_seconds=prompt_seconds, default_threads=default_threads), ensure_ascii=False), flush=True)
    phases = {}
    for owner, name, label in ((model.model, "generate", "codes_seconds"), (model.model.speech_tokenizer, "decode", "decode_seconds")):
        original = getattr(owner, name)
        def timed(*args, _original=original, _label=label, **kwargs):
            torch.cuda.synchronize()
            start = time.perf_counter()
            result = _original(*args, **kwargs)
            torch.cuda.synchronize()
            phases[_label] = time.perf_counter() - start
            return result
        setattr(owner, name, timed)
    text = "博士，你回来啦。今天也辛苦了，先休息一下，再和我聊聊天吧。"
    kwargs = {key: settings[key] for key in ("temperature", "top_p", "top_k", "repetition_penalty", "max_new_tokens")}
    cases = [("default", default_threads, [text]), ("threads1", 1, [text]), ("threads4", 4, [text]), ("batch2_threads1", 1, [text, text])]
    if args.representative:
        texts = ["博士，今天的事情已经做得很好了。如果还有没完成的计划，我们可以明天慢慢继续，先坐下来休息一会儿吧。", "我会陪着你一起整理今天的思路。先选最重要的一件事，再把它拆成几个简单的小步骤，这样就容易开始了。"]
        cases = [("single_a", default_threads, texts[:1]), ("single_b", default_threads, texts[1:]), ("mixed_batch_default", default_threads, texts), ("mixed_batch_threads1", 1, texts)]
    results = []
    for label, threads, texts in cases:
        batch = len(texts)
        torch.set_num_threads(threads)
        torch.manual_seed(settings["seed"])
        torch.cuda.reset_peak_memory_stats()
        phases.clear()
        start = time.perf_counter()
        with torch.inference_mode():
            waves, rate = model.generate_voice_clone(text=texts[0] if batch == 1 else texts, language="Chinese", voice_clone_prompt=prompt, **kwargs)
        torch.cuda.synchronize()
        seconds = time.perf_counter() - start
        durations = [len(wave) / rate for wave in waves]
        result = dict(label=label, threads=threads, batch=batch, text_chars=[len(t) for t in texts], seconds=round(seconds, 3), durations=durations, rtf=round(seconds / sum(durations), 3), peak_mb=round(torch.cuda.max_memory_allocated() / 1024**2), **phases)
        results.append(result)
        sf.write(OUT / (label + ".wav"), np.asarray(waves[0], dtype=np.float32), rate)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        filename = "representative.json" if args.representative else "results.json"
        (OUT / filename).write_text(json.dumps(dict(load_seconds=load_seconds, prompt_seconds=prompt_seconds, results=results), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
