import os
from pathlib import Path


_CONFIG_FILE = Path(__file__).resolve()
DEAR_ROOT = Path(os.environ.get("DEAR_ROOT", _CONFIG_FILE.parents[3])).resolve()
PROJECT_ROOT = DEAR_ROOT / "Talk" / "mistral_models"
STATIC_DIR = PROJECT_ROOT / "static"
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"
UPLOADED_FILES_DIR = PROJECT_ROOT / "uploaded_files"
BILIBILI_COOKIE_FILE = Path(os.environ.get("BILIBILI_COOKIE_FILE", KNOWLEDGE_BASE_DIR / "bilibili_cookie.txt"))
YOUTUBE_COOKIE_FILE = Path(os.environ.get("YOUTUBE_COOKIE_FILE", KNOWLEDGE_BASE_DIR / "youtube_cookie.txt"))
YOUTUBE_API_KEY_FILE = Path(os.environ.get("YOUTUBE_API_KEY_FILE", KNOWLEDGE_BASE_DIR / "youtube_api_key.txt"))

CHAT_MEMORY_DIR = Path(os.environ.get("SAYA_CHAT_MEMORY_DIR", DEAR_ROOT / "Talk" / "memory" / "chats"))
MMD_DIRECTORY = Path(os.environ.get("SAYA_MMD_DIRECTORY", DEAR_ROOT / "MMD"))
SAYA_R1_MODEL_PATH = Path(os.environ.get("SAYA_R1_MODEL_PATH", DEAR_ROOT / "Saya" / "saya.q5_k_m.gguf"))

DEFAULT_LOCAL_MODEL = os.environ.get("SAYA_DEFAULT_MODEL", "qwen3_30b_q5")
DEFAULT_API_URL = os.environ.get("API_URL", "https://api.openai.com/v1")
DEFAULT_API_MODEL = os.environ.get("API_MODEL", "gpt-3.5-turbo")
DEFAULT_API_MAX_OUTPUT_TOKENS = int(os.environ.get("API_MAX_OUTPUT_TOKENS", "8192"))
DEFAULT_LOCAL_MAX_OUTPUT_TOKENS = int(os.environ.get("LOCAL_MAX_OUTPUT_TOKENS", "4096"))
SAYA_API_URL = os.environ.get("SAYA_API_URL", "https://newapi.xiaoqianran.xyz/v1")
SAYA_API_MODEL = os.environ.get("SAYA_API_MODEL", "openai/gpt-oss-120b")

PROFILE_PATH = KNOWLEDGE_BASE_DIR / "saya" / "profile.txt"
LLAVA_MODEL_PATH = PROJECT_ROOT / "models" / "llava-v1.5-7b" / "llava-v1.5-7b-Q4_K.gguf"
LLAVA_MMPROJ_PATH = PROJECT_ROOT / "models" / "llava-v1.5-7b" / "mmproj-model-f16.gguf"

MODEL_CONFIGS = {
    "mistral_22b_q6": {
        "path": str(PROJECT_ROOT / "models" / "Mistral-Small-22B-ArliAI-RPMax-v1.1-Q6_K_L" / "Mistral-Small-22B-ArliAI-RPMax-v1.1-Q6_K_L.gguf"),
        "params": {
            "n_gpu_layers": 100,
            "n_ctx": 8192,
            "num_threads": 16,
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 40,
            "repeat_penalty": 1.15,
            "stop": ["Doctor:", "Saya:", "\nDoctor", "\nSaya"],
        },
    },
    "mistral_22b_q5": {
        "path": str(PROJECT_ROOT / "models" / "Mistral-Small-22B-ArliAI-RPMax-v1.1-Q5_K_M" / "Mistral-Small-22B-ArliAI-RPMax-v1.1-Q5_K_M.gguf"),
        "params": {
            "n_gpu_layers": 100,
            "n_ctx": 8192,
            "num_threads": 16,
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 40,
            "repeat_penalty": 1.15,
            "stop": ["Doctor:", "Saya:", "\nDoctor", "\nSaya"],
        },
    },
    "qwen3_30b_q5": {
        "path": str(PROJECT_ROOT / "models" / "Qwen3-30B-A3B-Instruct-2507-Q5_K_M" / "Qwen3-30B-A3B-Instruct-2507-Q5_K_M.gguf"),
        "params": {
            "n_gpu_layers": 100,
            "n_ctx": 8192,
            "num_threads": 16,
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 40,
            "repeat_penalty": 1.15,
            "stop": ["Doctor:", "Saya:", "\nDoctor", "\nSaya"],
        },
    },
    "saya_r1_q5": {
        "path": str(SAYA_R1_MODEL_PATH),
        "params": {
            "n_gpu_layers": 100,
            "n_ctx": 8192,
            "num_threads": 16,
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 40,
            "repeat_penalty": 1.05,
            "presence_penalty": 0.2,
            "num_predict": 4096,
            "mirostat_mode": 2,
            "mirostat_tau": 5.0,
            "mirostat_eta": 0.1,
            "stop": ["Doctor:", "Saya:", "\nDoctor", "\nSaya", "[System]", "\n[System]", "User uploaded"],
        },
    },
}
