"""Conversation-local mood; message snapshots are the source of continuity.

Reading/hovering does not decay or alter history. Truncation and branching
naturally restore the last retained snapshot, instead of a global current mood.
"""
import copy
import hashlib
import json
import math
import threading
from pathlib import Path

from saya_app.services.state_storage import atomic_save_json
from saya_app.services.mood_continuity import mood_directive

_LOCK = threading.RLock()
KEYS = ("happiness", "excitement", "stress", "sadness", "anger", "curiosity", "affection", "confidence")
BASELINE = (50, 50, 0, 10, 0, 40, 50, 65)
TARGETS = {
    "happy": (85, 65, 8, 5, 0, 55, 72, 78), "coquettish": (80, 58, 6, 4, 0, 35, 90, 70),
    "excited": (90, 85, 12, 3, 0, 75, 68, 82), "hyper": (85, 92, 16, 3, 2, 82, 60, 75),
    "surprised": (65, 85, 25, 10, 3, 90, 45, 48), "sad": (15, 30, 25, 85, 5, 20, 55, 30),
    "helpless": (20, 30, 65, 75, 8, 18, 45, 15), "worried": (35, 50, 90, 45, 12, 35, 62, 20),
    "stressed": (35, 55, 92, 35, 20, 28, 40, 18), "angry": (25, 75, 90, 25, 88, 18, 25, 60),
    "depressed": (15, 20, 60, 92, 10, 12, 32, 15), "tired": (45, 20, 15, 35, 3, 15, 45, 45),
    "curious": (65, 58, 10, 10, 0, 92, 50, 60),
    "affectionate": (75, 45, 6, 10, 0, 45, 92, 75),
    "confident": (65, 58, 8, 8, 2, 55, 55, 92),
}


def neutral():
    return dict(zip(KEYS, BASELINE), dominant="neutral",
                speech_mood="neutral", is_locked=False, version=3)


def normalize(state):
    result = neutral()
    if isinstance(state, dict):
        for key in KEYS:
            try:
                value = float(state.get(key, result[key]))
                if math.isfinite(value):
                    result[key] = round(max(0, min(100, value)), 1)
            except (TypeError, ValueError):
                pass
        result["is_locked"] = bool(state.get("is_locked", state.get("locked", False)))
    h, e, s = (result[k] for k in KEYS[:3])
    result["dominant"] = ("depressed" if s >= 55 and e < 35 else "stressed" if s >= 55
                          else "sad" if h < 40 else "excited" if h >= 65 and e >= 65
                          else "happy" if h >= 65 else "hyper" if e >= 80 else "neutral")
    requested = state.get("speech_mood") if isinstance(state, dict) else None
    result["speech_mood"] = requested if requested in ("neutral", "happy", "excited", "hyper", "sad", "depressed", "stressed") else result["dominant"]
    if isinstance(state, dict) and state.get("estimated_dimensions"):
        result["estimated_dimensions"] = [key for key in state["estimated_dimensions"] if key in KEYS]
    return result


def advance(state, category, enabled=True):
    result = normalize(state)
    if result["is_locked"] or not enabled:
        return result
    category = str(category or "").strip().lower()
    target = TARGETS.get(category)
    if target:
        for key, value in zip(KEYS, target):
            # Bounded interpolation reacts this turn without saturating after
            # many identical tags. Values stay continuous across the session.
            limit = 28 if key == "stress" else 22
            delta = .6 * (value - result[key])
            result[key] += max(-limit, min(limit, delta))
    else:
        for key, value in zip(KEYS, BASELINE):
            result[key] += .08 * (value - result[key])
    result.pop("speech_mood", None)
    result.pop("estimated_dimensions", None)
    result = normalize(result)
    expression = {"coquettish": "happy", "surprised": "excited", "helpless": "sad",
                  "worried": "stressed", "angry": "stressed", "tired": "depressed",
                  "curious": "neutral", "affectionate": "happy", "confident": "neutral"}.get(category, category)
    if expression in ("neutral", "happy", "excited", "hyper", "sad", "depressed", "stressed"):
        result["speech_mood"] = expression
    return result


def items_with_snapshots(items):
    state = neutral()
    result = []
    for original in items:
        item = dict(original)
        if item.get("role_mode") != "assistant" and item.get("agent_id", "saya") == "saya" and item.get("role") in ("ai", "assistant"):
            original_state = item.get("mood_state")
            previous = state
            if isinstance(original_state, dict):
                merged = {**state, **original_state}
                if "estimated_dimensions" not in original_state:
                    merged.pop("estimated_dimensions", None)
                state = normalize(merged)
            else:
                state = advance(state, item.get("mood"))
            if isinstance(original_state, dict):
                missing = [key for key in KEYS if key not in original_state]
                if missing:
                    inferred = advance(previous, item.get("mood"))
                    for key in missing:
                        state[key] = inferred[key]
                    state["estimated_dimensions"] = missing
            item["mood_state"] = copy.deepcopy(state)
            if "mood_state" not in original:
                item["mood_estimated"] = True
                state["estimated_dimensions"] = list(KEYS)
                item["mood_state"] = copy.deepcopy(state)
        result.append(item)
    return result


def _control_path(manager, session_id):
    path = manager._safe_session_path(session_id)
    return Path(path).with_suffix(".mood.json") if path else None


def _anchors(raw, projected):
    def last(items):
        return next((i for i in reversed(items) if i.get("role") in ("ai", "assistant")
                     and i.get("role_mode") != "assistant" and i.get("agent_id", "saya") == "saya"), {})
    original, current = last(raw), last(projected)
    legacy = copy.deepcopy(current)
    # Previous sidecars hashed the three-dimensional read projection. Accept
    # that anchor during migration without rewriting saved messages.
    if legacy.get("mood_state"):
        legacy["mood_state"] = {k: v for k, v in legacy["mood_state"].items()
                                if k not in KEYS[3:] and k != "estimated_dimensions"}
        legacy["mood_state"]["version"] = 2
    return [hashlib.sha256(json.dumps(i, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for i in (original, current, legacy)]


def snapshot(manager, session_id):
    with _LOCK:
        raw = (manager.load_session_full(session_id, make_current=False) or []) if _control_path(manager, session_id) else []
        items = items_with_snapshots(raw)
        ai = [i for i in items if i.get("mood_state") and i.get("role_mode") != "assistant" and i.get("agent_id", "saya") == "saya" and i.get("role") in ("ai", "assistant")]
        state = normalize(ai[-1]["mood_state"]) if ai else neutral()
        anchors = _anchors(raw, items)
        path = _control_path(manager, session_id)
        try:
            controls = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
        except (OSError, ValueError):
            controls = {}
        if not isinstance(controls, dict):
            controls = {}
        if controls.get("anchor") in anchors and isinstance(controls.get("override"), dict):
            state = normalize(controls["override"])
        if "locked" in controls:
            state["is_locked"] = bool(controls["locked"])
        return state


def control(manager, session_id, locked=None, reset=False):
    with _LOCK:
        path = _control_path(manager, session_id)
        if not path or not Path(manager._safe_session_path(session_id)).exists():
            raise ValueError("请选择有效会话。")
        raw = manager.load_session_full(session_id, make_current=False) or []
        anchor = _anchors(raw, items_with_snapshots(raw))[0]
        state = neutral() if reset else snapshot(manager, session_id)
        if locked is not None:
            state["is_locked"] = locked
        atomic_save_json(path, dict(anchor=anchor, override=state, locked=state["is_locked"]))
        return state


def directive(state, *, inline_tag=True):
    normalized = normalize(state)
    text = mood_directive(normalized) or "Keep a gentle, natural tone."
    values = ", ".join(f"{key}={normalized[key]}" for key in KEYS)
    return (f"{text} Conversation mood values (0-100): {values}. "
            "Curiosity guides interest and exploration; affection guides warmth without assuming intimacy; "
            "confidence guides composure, never factual certainty. Sadness and anger are separate from stress. "
            "Respond to the present message; do not exaggerate distress or invent feelings. "
            + ("Include one accurate [MOOD_SAYA: neutral/happy/excited/sad/stressed/depressed/angry/worried/tired/curious/affectionate/confident] tag "
               "describing your expressed tone; use one label, not the list. The tag is hidden from the reader."
               if inline_tag else "Express the tone only through natural language. Do not output any mood labels, control tags or internal state. Mood metadata is extracted separately by the host."))
