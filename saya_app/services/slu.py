"""Spoken-utterance understanding (SLU) layer: instruction vs chat vs mixed.

This module is the seam where the fine-tuned small model plugs in.  Today the
routing truth lives in conversation.py: deterministic tables (parse_intent,
parse_task_intent) plus a big-LLM structured call that returns
{route, direct_action, goal}.  The plan (2026-09-05) is to move the
understanding step to a small LOCAL model fine-tuned on Saya's own utterance
corpus, keeping the big LLM only for persona/reply generation.

Same interface, swappable backends:

- ``legacy``  - deterministic labels only (parse_intent / parse_task_intent).
               Zero behaviour change; this is the default.
- ``model``   - POST the utterance to an OpenAI-compatible chat endpoint
               (llama.cpp / Ollama / vLLM hosting the fine-tuned adapter, or
               any remote model for A/B experiments) and validate the JSON
               contract below.  Any failure falls back to ``legacy``.

JSON contract (assistant message MUST be exactly this shape):

    {"segments": [{
        "type": "instruction" | "chat" | "mixed",
        "text": "原句或分句",
        "route": "task" | "direct_action" | "goal" | "chat" | "clarify",
        "direct_action": "follow Doctor" | null,
        "goal": {"type": "obtain", "target": "iron_ingot", "count": 2} | null
    }]}

Corpus: every conversation turn is appended to
knowledge_base/slu_corpus/utterances.jsonl with the routing truth of the day
(see record_turn).  scripts/build_slu_dataset.py turns that corpus into a
fine-tune dataset; scripts/train_slu_lora.py runs the LoRA job locally.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ..config import KNOWLEDGE_BASE_DIR

CORPUS_DIR = KNOWLEDGE_BASE_DIR / "slu_corpus"
CORPUS_FILE = CORPUS_DIR / "utterances.jsonl"

# Route labels the contract may emit.
VALID_ROUTES = {"task", "direct_action", "goal", "chat", "clarify"}
VALID_TYPES = {"instruction", "chat", "mixed"}

_CORPUS_LOCK = threading.Lock()

# Model-backend endpoint override.  Empty = reuse the conversation API
# credentials (same URL/key/model), which makes live A/B experiments possible
# before the fine-tuned adapter exists.
SLU_MODEL_URL = os.environ.get("SAYA_SLU_MODEL_URL", "")
SLU_MODEL_NAME = os.environ.get("SAYA_SLU_MODEL_NAME", "")
# Local endpoints cold-load the model into VRAM on the first request, which
# can take tens of seconds - far longer than any API.
SLU_TIMEOUT_S = float(os.environ.get("SAYA_SLU_TIMEOUT_S", "120"))

SYSTEM_PROMPT = """你是 Minecraft 陪伴机器人 Saya 的话语理解模块。Doctor 说的每句话，你要判断它是
指令（要 Saya 做事）、对话（聊天/陈述）、还是两者混合，并按以下 JSON 输出，不要输出任何其他文字：

{"segments": [{"type": "instruction"或"chat"或"mixed", "text": "原句或分句",
"route": "task"|"direct_action"|"goal"|"chat"|"clarify",
"direct_action": 具体命令字符串或null, "goal": {"type":"obtain","target":"物品","count":数量}或null}]}

route 语义：direct_action=可直接执行的动词命令；goal=需要规划的物品目标；
task=停止/取消等任务控制；chat=纯聊天（含否定句和假设句）；clarify=像指令但缺少关键信息。
direct_action 只能是已知命令（follow/unfollow/come/stop/cancel task/status/
jump/swim/inventory/pickup/eat/mine below/mine nearby/sleep/pillar N/
chest find/deposit 物品 数量|all/withdraw 物品 数量）。
goal.type 只能是 obtain / obtain_and_deliver / craft / travel / combat。
goal.target 必须使用英文规范物品名（见可用列表），不允许中文或自造词；
"挖点/弄点"不带数量时 count=8。输出必须是单个 JSON 对象，不要解释。

示例（务必模仿此格式与命名习惯）：
输入：跟着我
输出：{"segments":[{"type":"instruction","text":"跟着我","route":"direct_action","direct_action":"follow Doctor","goal":null}]}
输入：给我两块牛排
输出：{"segments":[{"type":"instruction","text":"给我两块牛排","route":"goal","direct_action":null,"goal":{"type":"obtain_and_deliver","target":"cooked_beef","count":2,"recipient":"Doctor"}}]}
输入：挖点石头
输出：{"segments":[{"type":"instruction","text":"挖点石头","route":"direct_action","direct_action":"collect cobblestone 8","goal":null}]}
输入：我不需要治疗药水了
输出：{"segments":[{"type":"chat","text":"我不需要治疗药水了","route":"chat","direct_action":null,"goal":null}]}
输入：过来陪我看风景
输出：{"segments":[{"type":"mixed","text":"过来陪我看风景","route":"direct_action","direct_action":"come Doctor","goal":null}]}"""

# Canonical-name whitelist injected into the system prompt for model calls:
# most zero-shot slot errors were surface words (石头 instead of cobblestone).
_CATALOG_HINT_CACHE = (0.0, "")


def catalog_hint():
    """Compact canonical-name whitelist built from the item catalog aliases."""
    global _CATALOG_HINT_CACHE
    now = time.time()
    cached_at, cached = _CATALOG_HINT_CACHE
    if cached and now - cached_at < 600:
        return cached
    try:
        from .minecraft_items import item_catalog

        groups = {}
        for alias, canonical in item_catalog().alias_map.items():
            groups.setdefault(canonical, []).append(alias)
        parts = [
            f"{canonical}（{'/'.join(aliases[:3])}）"
            for canonical, aliases in sorted(groups.items())
        ]
        cached = ("可用 goal.target 规范名（必须原样选用这些英文规范名，禁止中文或自造）："
                  + "、".join(parts))
        _CATALOG_HINT_CACHE = (now, cached)
        return cached
    except Exception:
        return ""


# --------------------------------------------------------------- corpus

def record_turn(text, result, source=""):
    """Append one conversation turn + its routing truth to the JSONL corpus.

    Never raises: corpus collection must not be able to break the chat.  Set
    SAYA_SLU_CORPUS=0 to opt out entirely (privacy switch).
    """
    if os.environ.get("SAYA_SLU_CORPUS", "1").strip() == "0":
        return False
    text = str(text or "").strip()
    if not text:
        return False
    result = result if isinstance(result, dict) else {}
    entry = {
        "ts": time.time(),
        "source": str(source or ""),
        "text": text[:500],
        "route": str(result.get("route") or ""),
        "direct_action": result.get("direct_action"),
        "goal": result.get("goal"),
        "commands": list(result.get("commands") or []),
        "confidence": result.get("confidence"),
        "layer": str(result.get("source") or ""),
    }
    try:
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        with _CORPUS_LOCK:
            with CORPUS_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def load_corpus(limit=None):
    """Read the corpus back (oldest first).  Malformed lines are skipped."""
    if not CORPUS_FILE.exists():
        return []
    rows = []
    with CORPUS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit:
        rows = rows[-int(limit):]
    return rows


# ------------------------------------------------------------- contract

def validate_segments(payload):
    """Validate one model answer against the contract; None when invalid.

    A payload is usable when it is a dict with a non-empty `segments` list
    whose every entry carries a valid type/route pair.  Entries missing
    optional fields are repaired (command/goal null) rather than rejected -
    small models drop optional fields far more often than they hallucinate
    them, and repair keeps a whole answer usable because one bad segment.
    """
    if not isinstance(payload, dict):
        return None
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    normalized = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        seg_type = str(segment.get("type") or "").strip()
        route = str(segment.get("route") or "").strip()
        if seg_type not in VALID_TYPES or route not in VALID_ROUTES:
            continue
        normalized.append({
            "type": seg_type,
            "text": str(segment.get("text") or "")[:300],
            "route": route,
            "direct_action": _clean_action(segment.get("direct_action")),
            "goal": _clean_goal(segment.get("goal")),
        })
    if not normalized:
        return None
    return {"segments": normalized}


_GOAL_TYPES = {"obtain", "obtain_and_deliver", "craft", "travel", "combat"}
_ACTION_RE = re.compile(r"^[a-z_]+( [A-Za-z0-9_\-]+){0,6}$")


def _clean_action(action):
    action = str(action or "").strip()
    if not action or not _ACTION_RE.match(action) or len(action) > 120:
        return None
    return action


def _clean_goal(goal):
    if not isinstance(goal, dict):
        return None
    target = str(goal.get("target") or "").strip().lower()
    if not target or str(goal.get("type") or "obtain") not in _GOAL_TYPES:
        return None
    try:
        count = max(1, min(640, int(goal.get("count") or 1)))
    except (TypeError, ValueError):
        count = 1
    return {
        "type": str(goal.get("type") or "obtain"),
        "target": target,
        "count": count,
        "recipient": str(goal.get("recipient") or "Doctor"),
    }


# ------------------------------------------------------------- backends

def heuristic_segments(text):
    """Deterministic baseline: label via the existing tables, no network.

    Returns the contract JSON, or None when no deterministic layer recognizes
    the utterance (which is itself the interesting label: 'chat by default').
    Lazy imports keep module-level import direction conversation -> slu.
    """
    from .conversation import parse_intent
    from .task_intent import parse_task_intent

    value = str(text or "").strip()
    if not value:
        return None
    command = parse_intent(value)
    if command in ("stop", "cancel task"):
        # Emergency/task-control layer - the contract's "task" route.
        return {"segments": [{
            "type": "instruction", "text": value, "route": "task",
            "direct_action": command, "goal": None,
        }]}
    if command:
        return {"segments": [{
            "type": "instruction", "text": value, "route": "direct_action",
            "direct_action": command, "goal": None,
        }]}
    try:
        intent = parse_task_intent(value)
    except Exception:
        intent = None
    # Only REQUEST modality becomes a goal: negation ("我不需要治疗药水了")
    # and conditional ("如果去下界…"） carry an item but are chat.
    if isinstance(intent, dict) and intent.get("item") and intent.get("modality") == "request":
        goal_type = "obtain_and_deliver" if intent.get("disposition") == "deliver" else "obtain"
        goal = _clean_goal({
            "type": goal_type,
            "target": intent.get("item"),
            "count": intent.get("count") or 1,
            "recipient": intent.get("recipient") or "Doctor",
        })
        if goal:
            return {"segments": [{
                "type": "instruction", "text": value, "route": "goal",
                "direct_action": None, "goal": goal,
            }]}
    return None


def _chat_segments(text):
    value = str(text or "").strip()
    return {"segments": [{
        "type": "chat", "text": value, "route": "chat",
        "direct_action": None, "goal": None,
    }]}


SLU_DEBUG = os.environ.get("SAYA_SLU_DEBUG", "") == "1"


def _debug(*parts):
    if SLU_DEBUG:
        print("[slu]", *parts)


def _post_chat_completion(url, api_key, model, user_content, system_override=None, timeout_s=None):
    # Local thinking models (Qwen3 ...) waste latency and pollute the answer
    # with a <think> block; the soft switch alone is unreliable (Qwen3 mostly
    # ignores /no_think) and Ollama honours the native `think` parameter on
    # its OpenAI-compatible route.  Only applied when a dedicated SLU
    # endpoint is configured - the conversation API gets the clean payload.
    if SLU_MODEL_URL and system_override is None:
        user_content = f"{user_content}\n/no_think"
    # NO catalog hint: the fine-tuned model must see EXACTLY the
    # training-time prompt (SYSTEM_PROMPT alone).  The long hint block
    # was a zero-shot crutch for big API models - on the fine-tuned 4B
    # it tripled latency and degenerated output into whitespace soup.
    system = system_override or SYSTEM_PROMPT
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "temperature": 0.1,
        # Reasoning models (deepseek-v4-flash, Qwen3 thinking) spend output
        # budget on the reasoning phase first - 300 tokens used to truncate
        # to an EMPTY content before the JSON was ever written.
        "max_tokens": 2000,
    }
    if SLU_MODEL_URL and system_override is None:
        payload["think"] = False
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    # Local endpoints must bypass system proxies: urllib honours HTTP_PROXY,
    # which routes 127.0.0.1 through the proxy and gets refused.
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        opener = urllib.request.build_opener()
    with opener.open(request, timeout=timeout_s or SLU_TIMEOUT_S) as response:
        body = json.loads(response.read().decode("utf-8"))
    message = body["choices"][0]["message"]
    content = str(message.get("content") or "")
    if not content.strip():
        # Thinking models (Qwen3 ...) put the answer phase in a separate
        # field and leave `content` empty when the soft /no_think switch is
        # ignored - the JSON then lives inside the reasoning text.
        for key in ("reasoning_content", "reasoning"):
            if message.get(key):
                return str(message[key])
        _debug("empty content:", json.dumps(body)[:200])
    return content


def _extract_json(text):
    """Pull the first JSON object out of a chatty (or thinking) model answer.

    Thinking models wrap their reasoning in <think>...</think>, which itself
    can contain braces - strip it before searching, and prefer the LAST
    object when several candidates exist (the answer follows the thinking).
    """
    cleaned = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.DOTALL)
    matches = re.findall(r"\{.*\}", cleaned, re.DOTALL)
    for candidate in reversed(matches):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def segment_utterance(text, backend="legacy", credentials=None):
    """Route one utterance through the configured backend.

    Returns (segments, backend_used).  The model backend validates the JSON
    contract and falls back to the heuristic label (chat when even that is
    silent) on any transport or format failure - understanding must never
    break the chat.
    """
    value = str(text or "").strip()
    if not value:
        return None, "legacy"
    if backend == "model":
        try:
            creds = credentials or {}
            url = (SLU_MODEL_URL or creds.get("api_url") or "").strip()
            api_key = (creds.get("api_key") or "").strip()
            model = (SLU_MODEL_NAME or creds.get("model") or "").strip()
            if url and api_key:
                if not url.casefold().endswith("/chat/completions"):
                    url += "/chat/completions"
                answer = _post_chat_completion(url, api_key, model, value)
                segments = validate_segments(_extract_json(answer))
                if segments is not None:
                    return segments, "model"
                _debug("contract rejected:", str(answer)[:200])
        except Exception as exc:
            _debug("model backend failed:", type(exc).__name__, str(exc)[:200])
    return heuristic_segments(value) or _chat_segments(value), "legacy"


# ------------------------------------------------- local in-process backend

# Merged fine-tuned model directory (env override or config `slu_local_model`).
SLU_LOCAL_MODEL = os.environ.get("SAYA_SLU_LOCAL_MODEL", "")

_LOCAL_MODEL_CACHE = {"path": None, "model": None, "tokenizer": None}


def local_backend_available(settings=None):
    """True when a merged model directory is configured."""
    path = (settings or {}).get("slu_local_model") or SLU_LOCAL_MODEL
    return bool(path)


def _load_local_model(path):
    """Lazy-load the merged model once per process (~10-60s first call)."""
    if _LOCAL_MODEL_CACHE["path"] == path and _LOCAL_MODEL_CACHE["model"] is not None:
        return _LOCAL_MODEL_CACHE["model"], _LOCAL_MODEL_CACHE["tokenizer"]
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    _debug("loading local SLU model from", path)
    tokenizer = AutoTokenizer.from_pretrained(path)
    # device_map="cuda" is the load pattern proven in scripts/eval_slu_lora.py;
    # a plain .to("cuda") after eager load triggered fp32-sized (27GB)
    # allocation OOMs on the 24GB card.
    model = AutoModelForCausalLM.from_pretrained(
        path, dtype=torch.bfloat16, attn_implementation="eager", device_map="cuda",
    )
    model.eval()
    _LOCAL_MODEL_CACHE.update(path=path, model=model, tokenizer=tokenizer)
    return model, tokenizer


def generate_local(messages, settings=None, max_new_tokens=256):
    """One greedy generation through the in-process merged model.

    Greedy on purpose: the SLU contract wants deterministic routing, and a
    0.1-temperature sample still occasionally derails a 4B model.
    """
    import torch

    path = (settings or {}).get("slu_local_model") or SLU_LOCAL_MODEL
    if not path:
        raise ValueError("no local SLU model configured (slu_local_model)")
    model, tokenizer = _load_local_model(path)
    encoded = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_tensors="pt", return_dict=True,
    ).to(model.device)
    import torch

    try:
        with torch.no_grad():
            output = model.generate(**encoded, max_new_tokens=max_new_tokens, do_sample=False)
    except torch.cuda.OutOfMemoryError:
        # A zombie CUDA context from a crashed process can leave the card
        # full; flush the allocator and retry once before giving up (the
        # caller then falls back to legacy routing).
        torch.cuda.empty_cache()
        with torch.no_grad():
            output = model.generate(**encoded, max_new_tokens=max_new_tokens, do_sample=False)
    return tokenizer.decode(output[0][encoded["input_ids"].shape[1]:], skip_special_tokens=True)


def segment_local(text, settings=None):
    """Route one utterance through the in-process merged model.

    Returns (segments, "local"); on ANY failure returns (None, "local") so
    the caller can fall through to the next layer - understanding must
    never break the chat.
    """
    value = str(text or "").strip()
    if not value:
        return None, "local"
    try:
        # Same rule as the HTTP path: training-time prompt ONLY.
        answer = generate_local(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": value}],
            settings=settings,
        )
        segments = validate_segments(_extract_json(answer))
        if segments is not None:
            return segments, "local"
        _debug("local contract rejected:", str(answer)[:200])
    except Exception as exc:
        _debug("local backend failed:", type(exc).__name__, str(exc)[:200])
    return None, "local"


CHAT_SYSTEM_PROMPT = (
    "你是陪伴机器人 Saya，正在和主人（Doctor）聊天。"
    "用不超过两句话、口语化地自然回应主人这句话。这不是任务指令，"
    "不要输出 JSON，不要执行任何动作，不要提议去游戏里做什么。"
)


def chat_reply(text, backend="model", settings=None, credentials=None, timeout_s=20):
    """One casual reply from the fine-tuned model (offline persona fallback).

    The merged model RETAINS general chat ability (verified during merging),
    so when the cloud persona LLM is unreachable the local model can still
    answer small talk naturally instead of the canned
    '我在听。你是想聊天，还是要我在游戏里做一件事？' string.
    Returns "" on any failure - callers keep their last-resort text.
    """
    value = str(text or "").strip()
    if not value:
        return ""
    try:
        if backend == "local":
            answer = generate_local(
                [{"role": "system", "content": CHAT_SYSTEM_PROMPT},
                 {"role": "user", "content": value}],
                settings=settings, max_new_tokens=120,
            )
        else:
            creds = credentials or {}
            url = ((settings or {}).get("slu_model_url") or creds.get("api_url") or "").strip()
            api_key = (creds.get("api_key") or "local").strip()
            model = ((settings or {}).get("slu_model_name") or creds.get("model") or "").strip()
            if not url:
                return ""
            if not url.casefold().endswith("/chat/completions"):
                url += "/chat/completions"
            answer = _post_chat_completion(
                url, api_key, model, value,
                system_override=CHAT_SYSTEM_PROMPT, timeout_s=timeout_s,
            )
        reply = str(answer or "").strip().strip('"').strip("「」“”")
        return reply[:200] if reply else ""
    except Exception as exc:
        _debug("chat_reply failed:", type(exc).__name__, str(exc)[:160])
        return ""
