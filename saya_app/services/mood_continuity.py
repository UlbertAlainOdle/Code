"""Mood continuity: keep Saya's multi-dimension emotion state alive.

The old EmotionEngine existed but the chat path stopped calling it, and it
relied on a dedicated model call per turn. This module revives it with a
free signal the reply already carries: the [MOTION: category] tag the model
must emit every turn (that category IS the mood Saya expressed). Effects:

- the state decays over real time, persists to emotion_state.json and is
  served by /api/emotion/status (already existing router);
- a short mood directive is appended to the API prompt tail (prefix stays
  byte-identical for provider KV caches), so the next reply's tone and its
  MOTION choice drift with the mood instead of snapping back to neutral;
- the per-turn mood is written onto the AI message metadata ("mood"), which
  the nightly dream job uses for emotional-salience weighting.
"""

import copy
import re
import threading
from contextlib import contextmanager
from datetime import datetime

# Deltas applied when Saya's reply carries this motion category.
MOTION_MOOD_DELTAS = {
    "happy": {"happiness": 7, "excitement": 3},
    "coquettish": {"happiness": 5, "excitement": 2},
    "surprised": {"excitement": 6},
    "angry": {"stress": 8, "happiness": -4},
    "helpless": {"happiness": -5, "stress": 4},
    "worried": {"stress": 5, "happiness": -2},
    "refusal": {"stress": 2},
    "confused_thinking": {"excitement": -1},
    "giving_answer": {},
    # Backstop [MOOD_SAYA: ...] labels use mood names rather than motion names.
    "sad": {"happiness": -5},
    "excited": {"happiness": 7, "excitement": 6},
    "hyper": {"excitement": 8},
    "stressed": {"stress": 6},
    "depressed": {"happiness": -5, "excitement": -3},
    "tired": {"excitement": -3},
    "neutral": {},
}

MOOD_DIRECTIVES = {
    "excited": "Saya is currently in a bright, excited mood (happiness high, energy high). Let the warmth show, but keep her gentle voice.",
    "happy": "Saya is currently in a warm, contented mood. Replies can be a little softer and more affectionate than usual.",
    "hyper": "Saya is currently overflowing with energy. Livelier phrasing is fine, one notch only.",
    "sad": "Saya is currently a bit down. Her replies are softer, quieter, seeking closeness - never dramatic, never cold.",
    "stressed": "Saya is currently a little tense. She chooses calmer words and avoids playful teasing this turn.",
    "depressed": "Saya is currently weary and low. Replies are short, gentle and sincere.",
    "neutral": None,
    "tired": "Saya is currently low on energy. Keep replies gentle and unhurried.",
}

_FALLBACK_LOCK = threading.RLock()


@contextmanager
def _state_transaction(engine):
    with getattr(engine, "_emotion_lock", _FALLBACK_LOCK):
        before = copy.deepcopy(engine.state)
        try:
            yield
        except Exception:
            engine.state = before
            raise


def snapshot(engine, enabled=True):
    """Advance idle decay without resetting its clock on every UI poll."""
    with _state_transaction(engine):
        before = dict(engine.state)
        if enabled and not engine.state.get("is_locked"):
            engine.apply_time_decay()
            _recompute_dominant(engine.state)
        if engine.state != before:
            engine.save_state()
        return copy.deepcopy(engine.state)


def set_locked(engine, locked):
    with _state_transaction(engine):
        if bool(engine.state.get("is_locked")) != locked:
            if locked:
                snapshot(engine)
            # Unlock resumes from NOW, so months spent locked do not instantly
            # erase the preserved mood on the next poll.
            engine.state["is_locked"] = locked
            _recompute_dominant(engine.state)
            engine.save_state()
        return copy.deepcopy(engine.state)


def reset_neutral(engine):
    with _state_transaction(engine):
        engine.state.update(happiness=50, excitement=50, stress=0, dominant="neutral", is_locked=False)
        engine.save_state()
        return copy.deepcopy(engine.state)


def offline_report(engine, enabled=True):
    with getattr(engine, "_emotion_lock", _FALLBACK_LOCK):
        before = dict(engine.state)
        after = snapshot(engine, enabled=enabled)
        diffs = [int(after.get(key, 0) - before.get(key, 0)) for key in ("happiness", "excitement", "stress")]
        return {"message": "While you were away: " + ", ".join(f"{key} {diff:+d}" for key, diff in zip(("Happiness", "Excitement", "Stress"), diffs) if diff) if any(diffs) else "No significant emotional changes while you were away.", "changes": diffs if any(diffs) else [], "is_locked": bool(after.get("is_locked"))}


def update_from_motion(engine, category):
    """Fold one turn's motion category into the persistent mood state.

    Applies real-time decay first (the engine's own model), then the mapped
    deltas, clamps, recomputes the dominant label and saves. A locked state
    is a user freeze: nothing changes. Returns the post-update state dict.
    """
    if engine is None:
        return None
    with _state_transaction(engine):
        if engine.state.get("is_locked"):
            return copy.deepcopy(engine.state)
        engine.apply_time_decay()
        deltas = MOTION_MOOD_DELTAS.get(str(category or "").strip().lower(), {})
        for key, delta in deltas.items():
            engine.state[key] = max(0, min(100, float(engine.state.get(key, 50)) + delta))
        _recompute_dominant(engine.state)
        engine.save_state()
        return copy.deepcopy(engine.state)


def _recompute_dominant(state):
    happiness = float(state.get("happiness", 50))
    excitement = float(state.get("excitement", 50))
    stress = float(state.get("stress", 0))
    if stress > 60:
        state["dominant"] = "stressed" if excitement > 50 else "depressed"
    elif happiness > 70:
        state["dominant"] = "excited" if excitement > 60 else "happy"
    elif happiness < 30:
        state["dominant"] = "sad"
    elif excitement > 80:
        state["dominant"] = "hyper"
    else:
        state["dominant"] = "neutral"


def mood_directive(state):
    """A one-line system-prompt nudge for the current mood, or None."""
    dominant = str(state.get("dominant") or "neutral").lower() if isinstance(state, dict) else ""
    return MOOD_DIRECTIVES.get(dominant)


def summarize(state):
    """Compact view for the UI chip."""
    if not isinstance(state, dict):
        return {"dominant": "unknown", "locked": False, "happiness": None, "excitement": None, "stress": None}
    return {
        "dominant": str(state.get("dominant") or "neutral"),
        "locked": bool(state.get("is_locked")),
        "happiness": round(float(state.get("happiness", 50))),
        "excitement": round(float(state.get("excitement", 50))),
        "stress": round(float(state.get("stress", 0))),
    }


_MOOD_TAG_RE = re.compile(r"\[MOOD_SAYA:([a-z_\- ]+)\]", re.IGNORECASE)


def parse_mood_tag(text):
    """Backstop signal: parse an explicit [MOOD_SAYA: x] tag if the model emits one."""
    match = _MOOD_TAG_RE.search(str(text or ""))
    return match.group(1).strip().lower() if match else None
