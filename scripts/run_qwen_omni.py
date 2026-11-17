"""Start the isolated WSL Qwen streaming server with a single-GPU profile."""
import argparse
import json
import os
import sysconfig
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--mode", choices=("clone", "design"), default="clone")
parser.add_argument("--port", type=int, default=8091)
parser.add_argument("--runner", choices=("v1", "v2"), default="v2")
parser.add_argument("--eager", action="store_true")
parser.add_argument("--token", default="manual")
parser.add_argument('--guarded', action='store_true')
args = parser.parse_args()

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", VLLM_WORKER_MULTIPROC_METHOD="spawn", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4")
os.environ.setdefault("TRITON_CACHE_DIR", str(Path.home() / ".cache/saya/triton"))
os.environ.update(VLLM_NO_USAGE_STATS="1", DO_NOT_TRACK="1", MAX_JOBS="4")
os.environ["PATH"] = str(Path(os.sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
cuda = Path(sysconfig.get_paths()["purelib"]) / "nvidia/cu13"
if (cuda / "bin/nvcc").exists():
    os.environ.update(CUDA_HOME=str(cuda), CUDA_PATH=str(cuda), CUDACXX=str(cuda / "bin/nvcc"))
    os.environ["PATH"] = str(cuda / "bin") + os.pathsep + os.environ["PATH"]
os.environ["SAYA_OMNI_TOKEN"] = args.token
try:
    os.setsid()
except PermissionError:
    pass
(args.root / (".runtime/qwen-omni-" + args.token + ".pid")).write_text(str(os.getpid()))

guardian = args.root / ('.runtime/qwen-omni-' + args.token + '.guardian')
if args.guarded:
    deadline = time.monotonic() + 15
    while not guardian.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not guardian.exists():
        raise SystemExit('Backend guardian is absent; refusing to start an orphan voice server.')

import yaml
import vllm_omni

config = yaml.safe_load((Path(vllm_omni.__file__).parent / "deploy/qwen3_tts.yaml").read_text())
config["model_runner"] = args.runner
connector = config["connectors"]["connector_of_shared_memory"]["extra"]
connector["initial_codec_chunk_frames"] = 4
connector["codec_chunk_frames"] = 25
connector["decode_batch_max_size"] = 4
for stage in config["stages"]:
    stage["max_num_seqs"] = 4
    stage["gpu_memory_utilization"] = 0.24 if stage["stage_id"] == 0 else 0.15
    stage["compilation_config"] = {"cudagraph_capture_sizes": [1, 2, 4]}
    if args.eager:
        stage["enforce_eager"] = True
target = args.root / ".runtime/qwen-omni-deploy.yaml"
target.write_text(yaml.safe_dump(config, sort_keys=False))
metadata = json.loads((args.root / ".runtime/qwen-omni-installed.json").read_text())
model = Path(metadata.get("model_root", str(args.root / "models/qwen3-tts"))) / ("Base" if args.mode == "clone" else "VoiceDesign")
command = [str(Path(os.sys.executable).with_name("vllm")), "serve", str(model), "--omni", "--deploy-config", str(target), "--host", "127.0.0.1", "--port", str(args.port), "--served-model-name", "saya-qwen-tts", "--trust-remote-code", "--safetensors-load-strategy", "prefetch", "--allowed-local-media-path", str(args.root / "knowledge_base/tts/voices")]
if args.guarded and not guardian.exists():
    raise SystemExit('Backend exited while preparing the voice server.')
os.execv(command[0], command)
