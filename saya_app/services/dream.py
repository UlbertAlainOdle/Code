"""Nightly dream: evidence-locked diary + theme consolidation.

Runs while the backend is idle inside a night window. Two bounded model
calls per night, both under a hard evidence contract:

1. theme extraction - one abstract memory summarising the day, every
   evidence quote must appear verbatim in the day's transcript or it is
   dropped (no valid quote -> no memory at all);
2. first-person diary - each sentence must contain at least one 「…」 quote
   that exists verbatim in the transcript; any sentence without a verifiable
   quote, and anything matching the secret filter, is removed BEFORE the
   diary is stored. If nothing survives, no diary is written.

The result is browsable at /api/companion/diary and feeds the echo's
morning line ("昨晚我想了想…"). Emotional salience: turns whose stored mood
metadata carries a strong emotion raise the theme memory's importance.

Every acceptance criterion in scripts/companion_experiment.py is enforced
here, not in the prompt: the prompt asks, the code verifies.
"""

import glob
import json
import os
import re
import threading
from datetime import datetime, timedelta

from .memory_v4.synthesis import _SECRET_RE

_LOCK = threading.Lock()

QUOTE_PATTERNS = [re.compile(r"「([^」]{2,120})」"), re.compile(r"『([^』]{2,120})』"), re.compile(r"“([^”]{2,120})”")]
SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?\n])")
STRONG_MOODS = {"happy", "angry", "helpless", "worried", "sad"}
MAX_DIGEST_CHARS = 15000
MAX_CALLS_PER_RUN = 2


# ---------------------------------------------------------------- transcript

def collect_day(base_dir, date_str):
    from .dream_writes import collect
    return collect(base_dir,date_str)[0]


def build_digest(entries):
    """Compact, tail-biased transcript text used for quote verification."""
    lines = []
    total = 0
    for entry in reversed(entries):
        prefix = "Doctor" if entry["role"] == "user" else "Saya"
        line = f"{prefix}: {entry['content']}"
        lines.append(line)
        total += len(line)
        if total > MAX_DIGEST_CHARS:
            break
    return "\n".join(reversed(lines))


# ---------------------------------------------------------------- validation

def _normalize(text):
    return re.sub(r"\s+", "", str(text or ""))


def extract_quotes(sentence):
    found = []
    for pattern in QUOTE_PATTERNS:
        found.extend(m.group(1).strip() for m in pattern.finditer(sentence))
    return found


def valid_quotes(sentence, normalized_digest):
    """Quotes inside the sentence that appear verbatim (whitespace-free) in the digest."""
    return [q for q in extract_quotes(sentence) if _normalize(q) and _normalize(q) in normalized_digest]


def filter_diary(raw_text, normalized_digest):
    """Sentence-level evidence filter. Returns (kept_sentences, valid_quotes)."""
    kept, quotes = [], []
    for sentence in SENTENCE_SPLIT.split(str(raw_text or "")):
        sentence = sentence.strip()
        if len(sentence) < 4:
            continue
        if _SECRET_RE.search(sentence):
            continue
        good = valid_quotes(sentence, normalized_digest)
        if not good:
            continue
        kept.append(sentence)
        quotes.extend(good)
    return kept, quotes


# ---------------------------------------------------------------- state / diary files

def _state_path(base_dir):
    return os.path.join(str(base_dir), "dream_state.json")


def _diary_dir(base_dir):
    return os.path.join(str(base_dir), "saya_diary")


def _diary_path(base_dir, date_str):
    from .dream_writes import date_key
    return os.path.join(_diary_dir(base_dir), f"{date_key(date_str)}.json")


def _load_state(base_dir):
    path = _state_path(base_dir)
    if not os.path.exists(path):
        return {"done": {}, "running": False}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("done"), dict):
            return data
    except (OSError, ValueError):
        pass
    raise ValueError("dream_state_unavailable")


def _save_state(base_dir, state):
    from .state_storage import atomic_save_json
    atomic_save_json(_state_path(base_dir),state)


def save_diary(base_dir, date_str, payload):
    os.makedirs(_diary_dir(base_dir), exist_ok=True)
    tmp = _diary_path(base_dir, date_str) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _diary_path(base_dir, date_str))


def read_diary(base_dir, date_str):
    path = _diary_path(base_dir, date_str)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        from .dream_writes import diary_allowed
        return data if isinstance(data,dict) and data.get('date')==date_str and diary_allowed(base_dir,data) else None
    except (OSError, ValueError):
        return None


def list_diaries(base_dir, limit=60):
    entries = []
    for path in glob.glob(os.path.join(_diary_dir(base_dir), "*.json")):
        date_str = os.path.basename(path)[:-5]
        data = read_diary(base_dir, date_str)
        if data:
            entries.append({
                "date": date_str,
                "preview": str(data.get("entry") or "")[:80],
                "generated_at": data.get("generated_at"),
            })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries[:limit]


def latest_diary(base_dir, now=None):
    """Yesterday's diary, if one exists (the echo morning source)."""
    now = now or datetime.now()
    return read_diary(base_dir, (now - timedelta(days=1)).strftime("%Y-%m-%d"))


# ---------------------------------------------------------------- scheduling

def should_run(settings, base_dir, now=None):
    """enabled + inside the night window + idle long enough + not already done."""
    now = now or datetime.now()
    if not settings.get("dream_enabled"):
        return False, "disabled"
    try:
        start = int(settings.get("dream_window_start", 0))
        end = int(settings.get("dream_window_end", 8))
    except (TypeError, ValueError):
        start, end = 0, 8
    hour = now.hour
    in_window = (start <= hour < end) if start <= end else (hour >= start or hour < end)
    if not in_window:
        return False, "outside_window"
    try:
        idle_hours = max(1, int(settings.get("dream_idle_hours", 2)))
    except (TypeError, ValueError):
        idle_hours = 2
    from .echo import last_chat_at
    last = last_chat_at(base_dir)
    if last is not None and (now - last).total_seconds() < idle_hours * 3600:
        return False, "not_idle"
    target = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    state = _load_state(base_dir)
    if target in state.get("done", {}):
        return False, "already_done"
    if state.get("running"):
        return False, "running"
    return True, target


def _theme_prompt(digest):
    return (
        "You are Saya's nightly memory consolidator. Read the day's transcript and "
        "extract ONE durable theme memory (a stable fact, preference, project step or "
        "relationship shift worth keeping for months - never one-day states).\n"
        "Return JSON only: {\"theme\": \"one standalone sentence\", "
        "\"evidence\": [\"exact quote copied verbatim from Doctor's lines\"]} with 1-3 "
        "evidence quotes, each a contiguous verbatim substring of the transcript.\n"
        "Write the theme in the transcript's own language (Chinese transcript -> Chinese theme).\n"
        f"Transcript:\n{digest}\n"
    )


def _diary_prompt(digest):
    return (
        "You are Saya. Write tonight's diary entry (first person, Chinese, 3-6 "
        "sentences) about the day with Doctor. HARD RULE: every sentence must end "
        "with at least one 「…」 quote copied VERBATIM from the transcript below "
        "(from either speaker; a contiguous substring, unmodified). A sentence "
        "whose quote cannot be found verbatim in the transcript will be deleted. "
        "Do not invent events, feelings or facts that the transcript does not "
        "contain. No secrets, no keys, no passwords.\n"
        "Example sentence: 今天Doctor提到了新项目的事，「部署脚本还没跑通」，我有点惦记。\n"
        f"Transcript:\n{digest}\n"
        "Diary:"
    )


def _parse_theme(text):
    raw = str(text or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    theme = str(data.get("theme") or "").strip()
    evidence = data.get("evidence")
    if not theme or not isinstance(evidence, list):
        return None
    return theme, [str(e) for e in evidence if str(e).strip()]


def run(base_dir,kb,invoke,target_date=None,now=None,*,authorized=None):
    from .dream_writes import run as guarded_run
    return guarded_run(base_dir,kb,invoke,target_date,now,authorized=authorized)


def status(base_dir, now=None):
    now = now or datetime.now()
    state = _load_state(base_dir)
    return {
        "target_date": (now-timedelta(days=1)).strftime('%Y-%m-%d'),
        "running": bool(state.get("running")),
        "done": state.get("done", {}),
        "diaries": list_diaries(base_dir, limit=14),
    }
