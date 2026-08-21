import os
from pathlib import Path


_CONFIG_FILE = Path(__file__).resolve()
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", _CONFIG_FILE.parents[1])).resolve()
DEAR_ROOT = Path(os.environ.get("DEAR_ROOT", PROJECT_ROOT.parents[2])).resolve()
STATIC_DIR = PROJECT_ROOT / "static"
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"
UPLOADED_FILES_DIR = PROJECT_ROOT / "uploaded_files"
BILIBILI_COOKIE_FILE = Path(os.environ.get("BILIBILI_COOKIE_FILE", KNOWLEDGE_BASE_DIR / "bilibili_cookie.txt"))
YOUTUBE_COOKIE_FILE = Path(os.environ.get("YOUTUBE_COOKIE_FILE", KNOWLEDGE_BASE_DIR / "youtube_cookie.txt"))
YOUTUBE_API_KEY_FILE = Path(os.environ.get("YOUTUBE_API_KEY_FILE", KNOWLEDGE_BASE_DIR / "youtube_api_key.txt"))
YOUTUBE_PROXY = os.environ.get("YOUTUBE_PROXY", "").strip()

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

# --- Web research defaults -------------------------------------------------
# Brave Search API key. The app must still start when this is missing.
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
# Search behavior (plan section 11). Search a wider candidate pool, then let
# Saya's source-quality reranker choose a small evidence set.
WEB_SEARCH_COUNT = 10
WEB_SEARCH_MAX_RESULTS = 20
WEB_SEARCH_MAX_FINAL_SOURCES = 3
WEB_SEARCH_CONNECT_TIMEOUT = 5
WEB_SEARCH_READ_TIMEOUT = 15
# Download safety (plan section 12).
WEB_READER_MAX_REDIRECTS = 3
WEB_READER_MAX_BYTES = 2 * 1024 * 1024  # 2 MB decompressed cap.
WEB_READER_MAX_PDF_BYTES = 8 * 1024 * 1024  # Official reports are often larger than HTML.
WEB_READER_ALLOWED_PORTS = (80, 443)
WEB_READER_ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/pdf",
)
# Honest, fixed User-Agent for the Saya reader.
WEB_READER_USER_AGENT = "Saya-Reader/1.0 (+web-research; controlled-fetch)"
# Extraction (plan section 13).
WEB_READER_MAX_TEXT_CHARS = 12000
# Context budget (plan section 16).
WEB_CONTEXT_SAFETY_RESERVE_TOKENS = 512
WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS = 7000
WEB_CONTEXT_API_HARD_LIMIT_CHARS = 24000

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
