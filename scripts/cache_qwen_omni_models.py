"""Cache existing verified weights on WSL ext4; do not download new models."""
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
key = hashlib.sha256(str(root).encode()).hexdigest()[:12]
destination = Path.home() / ".local/share/saya" / ("qwen-models-" + key)
source = root / "models/qwen3-tts"
manifest = destination / "source-files.json"
previous = json.loads(manifest.read_text()) if manifest.exists() else {}
current = {}
for mode in ("Base", "VoiceDesign"):
    for path in sorted((source / mode).rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(source).parts):
            continue
        name = str(path.relative_to(source))
        target = destination / name
        stat = path.stat()
        identity = [stat.st_size, stat.st_mtime_ns]
        if previous.get(name, {}).get("identity") == identity and target.exists() and target.stat().st_size == stat.st_size:
            current[name] = previous[name]
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".copying")
        digest = hashlib.sha256()
        print("Caching", name, stat.st_size, flush=True)
        with path.open("rb") as reader, partial.open("wb") as writer:
            while data := reader.read(16 * 1024**2):
                writer.write(data)
                digest.update(data)
        with partial.open("rb") as reader:
            if hashlib.file_digest(reader, "sha256").hexdigest() != digest.hexdigest():
                raise RuntimeError("Model cache copy verification failed: " + name)
        partial.replace(target)
        current[name] = dict(identity=identity, sha256=digest.hexdigest())
        manifest.write_text(json.dumps(current))
manifest.write_text(json.dumps(current))
marker = root / ".runtime/qwen-omni-installed.json"
metadata = json.loads(marker.read_text())
metadata["model_root"] = str(destination)
marker.write_text(json.dumps(metadata))
print("Model cache verified:", destination, flush=True)
