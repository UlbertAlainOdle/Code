"""Download the Linux uv executable from its official PyPI wheel."""
import io
import json
import os
import urllib.request
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
metadata = json.load(urllib.request.urlopen("https://pypi.org/pypi/uv/0.12.24/json", timeout=30))
wheel = next(item for item in metadata["urls"] if "manylinux" in item["filename"] and "x86_64" in item["filename"])
destination = root / ".runtime/uv-linux"
destination.parent.mkdir(parents=True, exist_ok=True)
partial = root / ".runtime/uv-wheel.part"
size = wheel["size"]
offset = partial.stat().st_size if partial.exists() else 0
while offset < size:
    request = urllib.request.Request(wheel["url"], headers={"Range": f"bytes={offset}-{min(size - 1, offset + 1024 * 1024 - 1)}"})
    with urllib.request.urlopen(request, timeout=45) as response:
        chunk = response.read()
        if response.status == 200 and offset:
            raise RuntimeError("Download server did not honor resume range")
    with partial.open("ab") as output:
        output.write(chunk)
    offset += len(chunk)
    print(f"uv download {offset}/{size}", flush=True)
content = partial.read_bytes()
import hashlib
if hashlib.sha256(content).hexdigest() != wheel["digests"]["sha256"]:
    raise RuntimeError("uv wheel checksum mismatch")
with zipfile.ZipFile(io.BytesIO(content)) as archive:
    executable = next(name for name in archive.namelist() if name.endswith("/uv") and not name.endswith("/bin/uv"))
    destination.write_bytes(archive.read(executable))
    os.chmod(destination, 0o755)
print("uv", metadata["info"]["version"], "installed at", destination, flush=True)
