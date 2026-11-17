"""Resumable official ModelScope download, verified against pinned HF SHA-256."""
import concurrent.futures
import hashlib
import json
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ("VoiceDesign", "model.safetensors", "391e8db219f292c515297cdceeb43e4eae67cdde35fa57e79a6a8a532fca0522"),
    ("Base", "model.safetensors", "38fc7fc51c5e776e840414b6fd443962e9411b9654888fd7913e4da643cb857c"),
    ("VoiceDesign", "speech_tokenizer/model.safetensors", "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
    ("Base", "speech_tokenizer/model.safetensors", "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def download(folder, filename, expected):
    path = ROOT / "models/qwen3-tts" / folder / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and sha256(path) == expected:
        print(folder, filename, "verified", flush=True)
        return
    # The tokenizer is byte-identical in the two officially pinned model repos.
    previous = ROOT / "models/qwen3-tts/VoiceDesign" / filename
    if folder == "Base" and "speech_tokenizer" in filename and previous.exists() and sha256(previous) == expected:
        import shutil
        shutil.copyfile(previous, path)
        print(folder, filename, "copied verified tokenizer", flush=True)
        return
    url = f"https://modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-{folder}/resolve/master/{filename}"
    response = requests.get(url, headers={"Range": "bytes=0-0"}, timeout=60)
    response.raise_for_status()
    total = int(response.headers["Content-Range"].split("/")[-1])
    partial = path.with_suffix(".partial")
    manifest = path.with_suffix(".parts.json")
    done = set(json.loads(manifest.read_text()) if manifest.exists() and partial.exists() else [])
    if not partial.exists():
        with partial.open("wb") as target:
            target.truncate(total)
    block_size = 16 * 1024**2
    count = (total + block_size - 1) // block_size
    lock = threading.Lock()
    last_report = [0.0]

    def part(index):
        start, end = index * block_size, min(total, (index + 1) * block_size) - 1
        for attempt in range(6):
            try:
                chunk = requests.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=90)
                chunk.raise_for_status()
                if chunk.status_code != 206 or chunk.headers.get("Content-Range") != f"bytes {start}-{end}/{total}" or len(chunk.content) != end - start + 1:
                    raise RuntimeError("Invalid ranged response")
                with partial.open("r+b") as target:
                    target.seek(start)
                    target.write(chunk.content)
                with lock:
                    done.add(index)
                    temporary = manifest.with_suffix(".tmp")
                    temporary.write_text(json.dumps(sorted(done)))
                    temporary.replace(manifest)
                    now = time.monotonic()
                    if now - last_report[0] >= 15 or len(done) == count:
                        print(folder, filename, f"{len(done)}/{count} blocks ({100*len(done)/count:.1f}%)", flush=True)
                        last_report[0] = now
                return
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(min(2**attempt, 20))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(part, [index for index in range(count) if index not in done]))
    actual = sha256(partial)
    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch for {folder}/{filename}: {actual}")
    partial.replace(path)
    manifest.unlink(missing_ok=True)
    print(folder, filename, "SHA-256 verified", flush=True)


if __name__ == "__main__":
    for entry in FILES:
        download(*entry)
