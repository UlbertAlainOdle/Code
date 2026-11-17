import asyncio
import json
import re
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saya_app.services import conversation_mood as mood, api_thinking, task_progress
from saya_app.services.speech_prosody import current_mood


class Sessions:
    def __init__(self, root):
        self.root = Path(root)

    def _safe_session_path(self, session_id):
        return str(self.root / session_id) if re.fullmatch(r"session_[\w]+\.jsonl", session_id or "") else None

    def load_session_full(self, session_id, make_current=False):
        path = self._safe_session_path(session_id)
        return [json.loads(x) for x in Path(path).read_text().splitlines()] if path and Path(path).exists() else []

    def save(self, session_id, items):
        Path(self._safe_session_path(session_id)).write_text("\n".join(json.dumps(x) for x in items), encoding="utf-8")


def test_snapshots_isolate_sessions_and_follow_truncation_and_branch(tmp_path):
    manager = Sessions(tmp_path)
    a, b, branch = "session_a.jsonl", "session_b.jsonl", "session_branch.jsonl"
    happy = mood.advance(mood.neutral(), "happy")
    sad = mood.advance(happy, "sad")
    items = [dict(role="ai", content="hello", mood_state=happy), dict(role="ai", content="bye", mood_state=sad)]
    manager.save(a, items)
    manager.save(b, [dict(role="ai", mood_state=mood.advance(mood.neutral(), "stressed"))])
    assert mood.snapshot(manager, a) == sad
    assert mood.snapshot(manager, b)["speech_mood"] == "stressed"
    for _ in range(3):
        assert mood.snapshot(manager, a) == sad
    manager.save(branch, items[:1])
    assert mood.snapshot(manager, branch) == happy
    manager.save(a, items[:1])
    assert mood.snapshot(manager, a) == happy
    assert mood.snapshot(manager, b)["speech_mood"] == "stressed"


def test_state_is_bounded_not_saturated_and_read_is_immutable(tmp_path):
    state = mood.neutral()
    for _ in range(100):
        state = mood.advance(state, "happy")
    assert 80 < state["happiness"] < 100
    switched = mood.advance(state, "sad")
    assert state["happiness"] - switched["happiness"] <= 22
    assert switched["speech_mood"] == "sad"
    assert mood.advance(state, "happy", enabled=False) == state
    state["is_locked"] = True
    assert mood.advance(state, "stressed") == state
    manager = Sessions(tmp_path)
    manager.save("session_a.jsonl", [dict(role="ai", mood_state=state)])
    before = Path(manager._safe_session_path("session_a.jsonl")).read_bytes()
    mood.snapshot(manager, "session_a.jsonl")
    assert before == Path(manager._safe_session_path("session_a.jsonl")).read_bytes()


def test_historical_estimates_and_assistant_are_explicit():
    items = [dict(role="ai", content="hello", mood="happy"), dict(role="ai", role_mode="assistant", content="work", mood="sad")]
    result = mood.items_with_snapshots(items)
    assert result[0]["mood_estimated"]
    assert result[0]["mood_state"]["speech_mood"] == "happy"
    assert "mood_state" not in result[1]
    assert "mood_state" not in items[0]


def test_v2_history_preserves_values_and_marks_only_missing_dimensions(tmp_path):
    manager = Sessions(tmp_path)
    session = "session_old.jsonl"
    original = dict(role="ai", content="hello", mood="sad", mood_state=dict(
        happiness=31.7, excitement=42.6, stress=19.4, speech_mood="sad", version=2))
    manager.save(session, [original])
    before = Path(manager._safe_session_path(session)).read_bytes()
    state = mood.snapshot(manager, session)
    assert [state[key] for key in mood.KEYS[:3]] == [31.7, 42.6, 19.4]
    assert state["estimated_dimensions"] == list(mood.KEYS[3:])
    assert state["sadness"] > mood.neutral()["sadness"]
    assert state["speech_mood"] == "sad"
    assert Path(manager._safe_session_path(session)).read_bytes() == before
    fresh = mood.advance(state, "curious")
    assert "estimated_dimensions" not in fresh
    assert fresh["curiosity"] > state["curiosity"]
    assert all(0 <= fresh[key] <= 100 for key in mood.KEYS)
    manager.save(session, [original, dict(role="ai", content="new", mood_state=fresh)])
    assert mood.snapshot(manager, session) == fresh


def test_expanded_mood_drives_prompt_and_supported_fixed_voice_styles():
    curious = mood.advance(mood.neutral(), "curious")
    affectionate = mood.advance(mood.neutral(), "affectionate")
    assert curious["curiosity"] > affectionate["curiosity"]
    assert affectionate["affection"] > curious["affection"]
    assert curious["speech_mood"] == "neutral"
    assert affectionate["speech_mood"] == "happy"
    directive = mood.directive(curious)
    assert all(f"{key}=" in directive for key in mood.KEYS)
    assert "never factual certainty" in directive
    assert mood.normalize({key: 200 for key in mood.KEYS})["anger"] == 100


def test_v2_reset_sidecar_survives_schema_upgrade(tmp_path):
    import hashlib
    manager = Sessions(tmp_path)
    session = "session_old_reset.jsonl"
    legacy = {key: value for key, value in mood.advance(mood.neutral(), "sad").items() if key not in mood.KEYS[3:]}
    legacy["version"] = 2
    item = dict(role="ai", mood_state=legacy, content="saved")
    manager.save(session, [item])
    anchor = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    override = dict(happiness=50, excitement=50, stress=0, speech_mood="neutral", version=2)
    Path(manager._safe_session_path(session)).with_suffix(".mood.json").write_text(json.dumps(dict(anchor=anchor, override=override)), encoding="utf-8")
    assert mood.snapshot(manager, session) == mood.neutral()
    manager.save(session, [item, dict(role="ai", mood="happy")])
    assert mood.snapshot(manager, session)["speech_mood"] == "happy"


def test_session_control_and_tts_do_not_read_global_emotion(tmp_path):
    from types import SimpleNamespace
    manager = Sessions(tmp_path)
    session = "session_a.jsonl"
    state = mood.advance(mood.neutral(), "sad")
    manager.save(session, [dict(role="ai", mood_state=state)])
    locked = mood.control(manager, session, locked=True)
    assert locked["is_locked"] and locked["happiness"] == state["happiness"]
    with patch.dict("sys.modules", core_services=SimpleNamespace(emotion_engine=None, memory_manager=manager, kb_system=SimpleNamespace(settings={}))):
        assert current_mood(session) == "sad"
        assert current_mood("session_empty.jsonl") == "neutral"
    assert mood.control(manager, session, reset=True) == mood.neutral()
    with pytest.raises(ValueError):
        mood.control(manager, "../private", locked=True)


def test_composer_endpoint_preserves_exact_last_snapshot(tmp_path):
    from saya_app.routers import companion
    manager = Sessions(tmp_path)
    state = mood.advance(mood.advance(mood.neutral(), "happy"), "sad")
    session = "session_precise.jsonl"
    manager.save(session, [dict(role="ai", mood_state=state)])
    app = FastAPI()
    app.include_router(companion.router)
    with patch.object(companion, "memory_manager", manager), TestClient(app) as client:
        data = client.get("/api/companion/mood", params={"session_id": session}).json()
    for key in mood.KEYS:
        assert data[key] == state[key]
    assert data["speech_mood"] == state["speech_mood"]
    Path(manager._safe_session_path(session)).with_suffix(".mood.json").write_text("bad", encoding="utf-8")
    assert mood.snapshot(manager, session) == state


def test_deepseek_thinking_is_provider_scoped_and_uses_actual_parameters():
    for effort in ("low", "high", "max"):
        payload = api_thinking.apply({"model": "deepseek-v4-pro"}, "https://api.deepseek.com/v1", effort)
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == effort
    off = api_thinking.apply({"model": "deepseek-flash"}, "https://api.deepseek.com", "none")
    assert off["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in off
    assert api_thinking.apply({"model": "other"}, "https://other.example/v1", "auto") == {"model": "other"}
    with pytest.raises(ValueError):
        api_thinking.apply({"model": "deepseek-v4-pro"}, "https://other.example/v1", "max")


def test_real_work_phase_is_delivered_before_worker_finishes():
    release = threading.Event()
    def work(progress=None):
        progress("reading", "reading")
        assert release.wait(3)
        return "done"
    async def run():
        stream = task_progress.run_blocking(work)
        kind, event = await anext(stream)
        assert kind == "progress" and event["phase"] == "reading"
        release.set()
        assert await anext(stream) == ("result", "done")
        await stream.aclose()
    asyncio.run(run())
