import copy
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_services import EmotionEngine
from saya_app.routers import emotion, companion
from saya_app.services import mood_continuity as mood
from saya_app.services.state_storage import StateSaveError


@pytest.fixture
def engine(tmp_path):
    result = EmotionEngine(str(tmp_path))
    result.state.update(happiness=12, excitement=12, stress=24, dominant="sad", is_locked=True, last_update="2026-01-30 18:03:50")
    return result


def test_unlock_preserves_old_mood_and_resumes_updates_and_persistence(engine):
    before = copy.deepcopy(engine.state)
    mood.set_locked(engine, False)
    assert engine.state["happiness"] == before["happiness"]
    assert mood.snapshot(engine)["dominant"] == "sad"
    mood.update_from_motion(engine, "happy")
    assert engine.state["happiness"] == 19
    restored = EmotionEngine(engine.base_dir)
    assert restored.state == engine.state
    assert restored.state["is_locked"] is False


def test_locked_mood_does_not_decay_or_mutate_on_chat_or_poll(engine):
    before = copy.deepcopy(engine.state)
    mood.update_from_motion(engine, "angry")
    assert mood.snapshot(engine) == before
    assert engine.state == before


def test_idle_decay_recomputes_label_and_polls_do_not_reset_clock(engine):
    engine.state.update(is_locked=False, last_update=(datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"))
    state = mood.snapshot(engine)
    assert state["dominant"] == "neutral"
    assert state["happiness"] == 50
    stamp = state["last_update"]
    with patch.object(engine, "save_state", wraps=engine.save_state) as save:
        for _ in range(4):
            mood.snapshot(engine)
        save.assert_not_called()
    assert engine.state["last_update"] == stamp


def test_disabled_feature_does_not_advance_values(engine):
    engine.state["is_locked"] = False
    before = copy.deepcopy(engine.state)
    assert mood.snapshot(engine, enabled=False) == before


def test_save_failure_rolls_back_and_preserves_previous_file(engine):
    engine.save_state()
    previous_file = open(engine.file_path, encoding="utf-8").read()
    before = copy.deepcopy(engine.state)
    with patch("core_services.atomic_save_json", side_effect=StateSaveError("disk full")):
        with pytest.raises(StateSaveError):
            mood.set_locked(engine, False)
    assert engine.state == before
    assert open(engine.file_path, encoding="utf-8").read() == previous_file


def test_api_lock_validation_reset_and_disabled_status(engine):
    app = FastAPI()
    app.include_router(emotion.router)
    app.include_router(companion.router)
    settings = {"mood_continuity_enabled": True}
    with patch.object(emotion, "emotion_engine", engine), patch.object(companion, "emotion_engine", engine), patch.object(emotion.kb_system, "settings", settings), TestClient(app) as client:
        assert client.post("/api/emotion/lock", json={"locked": "false"}).status_code == 422
        assert client.post("/api/emotion/lock", json={}).status_code == 422
        result = client.post("/api/emotion/lock", json={"locked": False})
        assert result.json()["is_locked"] is False
        assert result.json()["mood"]["happiness"] == 12
        settings["mood_continuity_enabled"] = False
        engine.state["last_update"] = "2026-01-30 18:03:50"
        assert client.get("/api/companion/mood").json()["happiness"] == 12
        assert client.get("/api/companion/mood").json()["enabled"] is False
        assert client.get("/api/status").json()["dominant"] == "sad"
        reset = client.post("/api/emotion/reset").json()["mood"]
        assert reset["dominant"] == "neutral"
        assert reset["happiness"] == reset["excitement"] == 50
        assert reset["stress"] == 0
        assert reset["locked"] is False


def test_failed_save_returns_api_error_without_unlocking(engine):
    app = FastAPI()
    app.include_router(emotion.router)
    with patch.object(emotion, "emotion_engine", engine), patch.object(engine, "save_state", side_effect=StateSaveError("disk full")), TestClient(app) as client:
        assert client.post("/api/emotion/lock", json={"locked": False}).status_code == 503
    assert engine.state["is_locked"] is True


def test_backstop_sad_tag_has_effect(engine):
    mood.set_locked(engine, False)
    mood.update_from_motion(engine, mood.parse_mood_tag("[MOOD_SAYA: sad]"))
    assert engine.state["happiness"] == 7
