"""JSON-lines worker: CUDA and Qwen dependencies never enter Saya's environment."""
import argparse
import gc
import json
import base64
import io
import sys
import time
import traceback
from pathlib import Path

protocol_output = sys.stdout
sys.stdout = sys.stderr  # Libraries may print; reserve stdout for protocol messages.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    root, data = Path(args.root), Path(args.data)
    import numpy as np
    import soundfile as sf
    import torch

    model, loaded_mode = None, None
    prompts = {}

    def load(mode):
        nonlocal model, loaded_mode
        if loaded_mode == mode:
            return
        model = None
        loaded_mode = None
        prompts.clear()
        gc.collect()
        torch.cuda.empty_cache()
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA 不可用；请检查 NVIDIA 驱动和独立语音环境。")
        from qwen_tts import Qwen3TTSModel
        folder = "VoiceDesign" if mode == "design" else "Base"
        model = Qwen3TTSModel.from_pretrained(str(root / "models/qwen3-tts" / folder), device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
        loaded_mode = mode

    def status():
        return dict(running=True, busy=False, ephemeral_audio=True, loaded_mode=loaded_mode, device=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None, allocated_mb=round(torch.cuda.memory_allocated() / 1024**2), reserved_mb=round(torch.cuda.memory_reserved() / 1024**2))

    def execute(request):
        op = request["op"]
        if op == "status":
            return status()
        if op == "load":
            load(request["mode"])
            return status()
        identifier = request["identifier"]
        # Parent validates IDs; also protect this boundary from malformed protocol data.
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Invalid audio identifier")
        if op == "import_voice":
            try:
                samples, rate = sf.read(str(data / "uploads" / (identifier + ".audio")), dtype="float32", always_2d=True)
            except Exception as error:
                raise ValueError("无法读取参考音频，请使用有效的 WAV / FLAC / MP3。") from error
            samples = samples.mean(axis=1)
            duration = len(samples) / rate
            if not 3 <= duration <= 30:
                raise ValueError("参考音频需要 3–30 秒，请裁剪为清晰、无背景音乐的一段声音。")
            if not np.isfinite(samples).all() or np.max(np.abs(samples)) < 0.001:
                raise ValueError("参考音频无有效声音。")
            path = data / "voices" / (identifier + ".wav")
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(path), samples, rate, subtype="PCM_16")
            info = dict(id=identifier, name=request["name"].strip() or "参考声音", text=request["text"].strip(), duration=round(duration, 2), sample_rate=rate)
            path.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            return info
        if op != "synthesize":
            raise ValueError("Unknown operation")
        settings = request["settings"]
        started = time.monotonic()
        load(settings["mode"])
        torch.cuda.reset_peak_memory_stats()
        if settings["seed"] >= 0:
            torch.manual_seed(settings["seed"])
        kwargs = {key: settings[key] for key in ("temperature", "top_p", "top_k", "repetition_penalty", "max_new_tokens")}
        with torch.inference_mode():
            if settings["mode"] == "design":
                wavs, rate = model.generate_voice_design(text=request["text"], language=settings["language"], instruct=settings["description"], **kwargs)
            else:
                voice_id = settings["voice_id"]
                key = (voice_id, settings["x_vector_only"])
                if key not in prompts:
                    if len(prompts) >= 4:
                        prompts.clear()
                    path = data / "voices" / (voice_id + ".wav")
                    metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
                    samples, ref_rate = sf.read(str(path), dtype="float32")
                    prompts[key] = model.create_voice_clone_prompt(ref_audio=(samples, ref_rate), ref_text=metadata["text"] or None, x_vector_only_mode=settings["x_vector_only"])
                wavs, rate = model.generate_voice_clone(text=request["text"], language=settings["language"], voice_clone_prompt=prompts[key], **kwargs)
        wave = np.asarray(wavs[0], dtype=np.float32)
        if not len(wave) or not np.isfinite(wave).all():
            raise RuntimeError("模型没有生成有效音频。")
        if request.get('ephemeral') is True:
            if len(wave)/rate>60:raise ValueError('Ephemeral audio exceeds duration limit')
            output=io.BytesIO();sf.write(output,wave,rate,format='WAV',subtype='PCM_16')
            audio=output.getvalue()
            if len(audio)>2*1024*1024:raise ValueError('Ephemeral audio exceeds byte limit')
            return {'audio_base64':base64.b64encode(audio).decode('ascii')}
        folder = data / "previews"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (identifier + ".wav")
        sf.write(str(path), wave, rate, subtype="PCM_16")
        info = dict(text=request["text"], duration=round(len(wave) / rate, 2), sample_rate=rate, elapsed_seconds=round(time.monotonic() - started, 2), peak_allocated_mb=round(torch.cuda.max_memory_allocated() / 1024**2), mode=settings["mode"])
        path.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        # Keep only a bounded preview history; saved voices live in another folder.
        for old in sorted(folder.glob("*.wav"), key=lambda p: p.stat().st_mtime)[:-50]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        return info

    for line in sys.stdin:
        try:
            result = execute(json.loads(line))
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            message = str(error)
            if "out of memory" in message.lower():
                model, loaded_mode = None, None
                prompts.clear()
                gc.collect()
                torch.cuda.empty_cache()
                message = "显存不足。请卸载本地聊天模型或其他 GPU 程序后重试。"
            result = {"error": message, "error_type": "validation" if isinstance(error, ValueError) else "runtime"}
        protocol_output.write(json.dumps(result, ensure_ascii=False) + "\n")
        protocol_output.flush()


if __name__ == "__main__":
    main()
