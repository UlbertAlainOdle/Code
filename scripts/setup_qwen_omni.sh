#!/usr/bin/env bash
# Run inside WSL Ubuntu from the project directory.
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${SAYA_OMNI_UV_CACHE:-$HOME/.cache/saya-uv}"
export UV_LINK_MODE=copy
if [[ ! -f /usr/include/python3.12/Python.h ]] || ! command -v gcc >/dev/null; then
  echo 'WSL requires build-essential and python3-dev. Install them with apt before continuing.' >&2
  exit 1
fi
if [[ ! -x .runtime/uv-linux ]]; then
  python3 scripts/bootstrap_uv_linux.py
fi
# Small library reads through /mnt/d are prohibitively slow; keep the venv on ext4.
project_key=$(python3 -c 'import hashlib,pathlib; print(hashlib.sha256(str(pathlib.Path.cwd()).encode()).hexdigest()[:12])')
omni_env=${SAYA_OMNI_VENV:-"$HOME/.local/share/saya/qwen-omni-0.30.0-$project_key"}
.runtime/uv-linux venv --allow-existing --python python3 "$omni_env"
.runtime/uv-linux pip install --python "$omni_env/bin/python" -r scripts/requirements-qwen-omni.txt
"$omni_env/bin/python" scripts/patch_qwen_omni.py
"$omni_env/bin/python" -c 'import torch,importlib.metadata; print(torch.__version__, importlib.metadata.version("vllm"), torch.cuda.get_device_name())'
"$omni_env/bin/python" -c 'import json,pathlib,sys; pathlib.Path(".runtime/qwen-omni-installed.json").write_text(json.dumps({"vllm":"0.30.0","vllm_omni":"0.30.0","python":sys.executable}))'
"$omni_env/bin/python" scripts/cache_qwen_omni_models.py
