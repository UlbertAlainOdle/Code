"""Proactive memory echo: Saya brings something up first.

The frontend polls /api/companion/echo/poll (the existing 5s status poll,
revived); when a due candidate exists the service returns one short
in-character line, rendered as a Saya-initiated bubble. Everything is
opt-in and rate-limited per the granular-controls policy:

- master switch off (default) -> the poll is inert;
- each source (stale projects / dated memories / fresh dream diary) is an
  explicit opt-in, empty means inert;
- at most N echoes per day (default 1), a minimum gap between echoes,
  quiet hours, and never while a reply is being generated;
- the detector's latest match is always visible via /status for the UI.
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

_LOCK = threading.Lock()


def _state_path(base_dir):
    return os.path.join(str(base_dir), "echo_state.json")


def _load(base_dir):
    path = _state_path(base_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(base_dir, state):
    path = _state_path(base_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def mark_chat_activity(base_dir):
    """Called after every completed chat turn (dream idle window uses it too)."""
    with _LOCK:
        state = _load(base_dir)
        state["last_chat_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save(base_dir, state)


def last_chat_at(base_dir):
    raw = _load(base_dir).get("last_chat_at")
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _setting_int(settings, key, default):
    """int() that keeps explicit zeros (quiet-hour 0, window start 0...)."""
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def _in_quiet_hours(now, start, end):
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window across midnight


_DATE_PATTERNS = [
    re.compile(r"(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})"),
    re.compile(r"(\d{1,2})[月./\-](\d{1,2})日?"),
]

_DATED_MEMORY_HINTS = ("生日", "纪念日", "周年", "生日是", "纪念日是")


def _dated_memory_candidates(kb, now):
    """Memories whose content carries a matching MM-DD (birthday/anniversary)."""
    hits = []
    for memory in kb.memories:
        content = str(memory.get("content") or "")
        title = str(memory.get("title") or "")
        if not any(hint in content or hint in title for hint in _DATED_MEMORY_HINTS):
            continue
        for pattern in _DATE_PATTERNS:
            match = pattern.search(content)
            if not match:
                continue
            groups = match.groups()
            if len(groups) == 3 and len(groups[0]) == 4:
                month, day = int(groups[1]), int(groups[2])
            elif len(groups) == 2:
                month, day = int(groups[0]), int(groups[1])
            else:
                continue
            if month == now.month and day == now.day:
                hits.append(memory)
            break
    return hits[:3]


def build_candidates(kb, project_manager, settings, now=None):
    """Rule-based shortlist across enabled sources (best first)."""
    now = now or datetime.now()
    candidates = []
    if settings.get("echo_source_dream"):
        from .dream import latest_diary
        diary = latest_diary(kb.base_dir if hasattr(kb, "base_dir") else None, now)
        if diary:
            candidates.append({
                "kind": "dream",
                "score": 100,
                "topic": "昨夜整理的日记",
                "detail": {"diary_date": diary.get("date")},
            })
    if settings.get("echo_source_projects"):
        stale_days = max(1, _setting_int(settings, "echo_project_stale_days", 3))
        cutoff = (now - timedelta(days=stale_days)).strftime("%Y-%m-%d %H:%M:%S")
        for project in project_manager.list_projects():
            updated = str(project.get("updated_at") or "")
            if updated and updated < cutoff:
                age_days = (now - datetime.strptime(updated[:10], "%Y-%m-%d")).days
                candidates.append({
                    "kind": "project",
                    "score": min(60, 20 + age_days),
                    "topic": str(project.get("name") or "未命名项目")[:60],
                    "detail": {"project_id": project.get("id"), "stale_days": age_days},
                })
    if settings.get("echo_source_dates"):
        for memory in _dated_memory_candidates(kb, now):
            candidates.append({
                "kind": "date",
                "score": 80,
                "topic": str(memory.get("title") or memory.get("content") or "")[:60],
                "detail": {"memory_id": memory.get("id")},
            })
    candidates.sort(key=lambda c: -c.get("score", 0))
    return candidates[:3]


PHRASE_PROMPT = (
    "You are Saya, a gentle otherworldly girl companion. She wants to softly bring "
    "up ONE thing to Doctor (the user) right now, out of her own memory - not a "
    "questionnaire, not a reminder app.\n"
    "Context: {context}\n"
    "Write ONE short line in Chinese (max 40 chars), warm and light, referencing "
    "the topic naturally. No bullet points, no emoji, no quotes. If it is a "
    "birthday/anniversary, keep it tender, not congratulatory-formal. If it is an "
    "old project, gentle curiosity about how it went. If it is last night's "
    "reflections, start like she thought about something overnight.\n"
    "Output the line only."
)


def _fallback_phrase(candidate):
    topic = candidate.get("topic") or ""
    kind = candidate.get("kind")
    if kind == "project":
        return f"Doctor，{topic}……后来进行得怎么样了？我有点惦记。"
    if kind == "date":
        return f"Doctor，今天是和「{topic}」有关的日子呢。"
    if kind == "dream":
        return "Doctor，昨晚我整理了一下我们的对话，想到了一些事。"
    return f"Doctor，我刚刚想起了{topic}。"


def poll(base_dir, kb, project_manager, invoke=None, now=None, is_generating=False):
    """Return {"message", "kind", ...} once when due, else {"message": None}.

    ``invoke`` is an optional blocking callable(prompt) -> str for in-character
    phrasing; without it a template fallback is used. Rate state updates only
    happen when a message is actually delivered.
    """
    now = now or datetime.now()
    settings = kb.settings if hasattr(kb, "settings") else {}
    if not settings.get("echo_enabled"):
        return {"message": None, "reason": "disabled"}
    if is_generating:
        return {"message": None, "reason": "generating"}

    with _LOCK:
        state = _load(base_dir)
    quiet_start = _setting_int(settings, "echo_quiet_start", 23)
    quiet_end = _setting_int(settings, "echo_quiet_end", 8)
    if _in_quiet_hours(now, quiet_start, quiet_end):
        return {"message": None, "reason": "quiet_hours"}
    daily_limit = max(1, _setting_int(settings, "echo_daily_limit", 1))
    today = now.strftime("%Y-%m-%d")
    if state.get("day") != today:
        sent_today = 0
    else:
        sent_today = int(state.get("sent_today", 0) or 0)
    if sent_today >= daily_limit:
        return {"message": None, "reason": "daily_limit"}
    min_gap = _setting_int(settings, "echo_min_gap_minutes", 30)
    last_sent = state.get("last_sent_at")
    if last_sent:
        try:
            elapsed = (now - datetime.strptime(str(last_sent), "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            if elapsed < min_gap:
                return {"message": None, "reason": "min_gap"}
        except ValueError:
            pass

    candidates = build_candidates(kb, project_manager, settings, now)
    if not candidates:
        return {"message": None, "reason": "no_candidate"}
    candidate = candidates[0]
    topic_key = f"{candidate.get('kind')}:{candidate.get('topic')}"
    if state.get("last_topic_key") == topic_key and state.get("day") == today:
        # Same topic already echoed today: try the next candidate if any.
        if len(candidates) > 1:
            candidate = candidates[1]
            topic_key = f"{candidate.get('kind')}:{candidate.get('topic')}"
        else:
            return {"message": None, "reason": "topic_already_sent"}

    phrase = None
    if callable(invoke):
        try:
            context = json.dumps(candidate.get("detail") or {}, ensure_ascii=False) + f" topic={candidate.get('topic')}"
            phrase = str(invoke(PHRASE_PROMPT.replace("{context}", context)) or "").strip().strip('"“”')[:120]
        except Exception:
            phrase = None
    if not phrase:
        phrase = _fallback_phrase(candidate)

    if candidate.get("kind")=="dream" and candidate not in build_candidates(kb,project_manager,settings,now):
        return {"message":None,"reason":"dream_source_changed"}
    with _LOCK:
        state = _load(base_dir)
        state["day"] = today
        state["sent_today"] = (0 if state.get("day") != today else int(state.get("sent_today", 0) or 0)) + 1
        state["day"] = today
        state["last_sent_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        state["last_topic_key"] = topic_key
        state["last_match"] = {"kind": candidate.get("kind"), "topic": candidate.get("topic"),
                               "at": state["last_sent_at"]}
        _save(base_dir, state)
    return {"message": phrase, "kind": candidate.get("kind"), "topic": candidate.get("topic")}


def status(base_dir, kb, project_manager, now=None):
    """For the settings panel: switch states + detector's latest match."""
    now = now or datetime.now()
    settings = kb.settings if hasattr(kb, "settings") else {}
    state = _load(base_dir)
    candidates = []
    if settings.get("echo_enabled"):
        candidates = [
            {"kind": c.get("kind"), "topic": c.get("topic"), "score": c.get("score")}
            for c in build_candidates(kb, project_manager, settings, now)
        ]
    return {
        "enabled": bool(settings.get("echo_enabled")),
        "sources": {
            "projects": bool(settings.get("echo_source_projects")),
            "dates": bool(settings.get("echo_source_dates")),
            "dream": bool(settings.get("echo_source_dream")),
        },
        "daily_limit": max(1, _setting_int(settings, "echo_daily_limit", 1)),
        "quiet": [_setting_int(settings, "echo_quiet_start", 23), _setting_int(settings, "echo_quiet_end", 8)],
        "last_sent_at": state.get("last_sent_at"),
        "last_match": state.get("last_match"),
        "current_candidates": candidates,
    }
