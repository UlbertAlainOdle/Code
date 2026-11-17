"""SLU routing layer (conversation Layer 2.9): the fine-tuned local model
must sit after the deterministic tables, execute validated actions/goals,
and fall through to the persona LLM on chat/failure - never block chat.
"""

import pytest

from saya_app.services import slu
from saya_app.services.conversation import ConversationService


@pytest.fixture()
def service():
    return ConversationService()


def _patch_settings(monkeypatch, backend="local", model="merged"):
    monkeypatch.setattr(
        ConversationService, "_slu_settings",
        lambda self: (backend, {"slu_local_model": model}),
    )


def test_instruction_reaches_command_execution(monkeypatch, service):
    """A model instruction is re-validated and sent down the normal pipe."""
    _patch_settings(monkeypatch)
    monkeypatch.setattr(
        slu, "segment_local",
        lambda text, settings=None: (
            {"segments": [{"type": "instruction", "text": text,
                           "route": "direct_action",
                           "direct_action": "follow Doctor", "goal": None}]},
            "local",
        ),
    )
    result = service.handle_message("test", "Tester", "你跟在我身后别走丢呀")
    # Worker is down in tests -> recognised but not delivered; the important
    # part is that the SLU layer owned the routing (source=local_slu).
    assert result["source"] == "local_slu"
    assert result["route"] in ("task", "clarify")


def test_goal_reaches_the_planner(monkeypatch, service):
    _patch_settings(monkeypatch)
    monkeypatch.setattr(
        slu, "segment_local",
        lambda text, settings=None: (
            {"segments": [{"type": "instruction", "text": text,
                           "route": "goal", "direct_action": None,
                           "goal": {"type": "obtain_and_deliver",
                                    "target": "cooked_beef", "count": 2,
                                    "recipient": "Doctor"}}]},
            "local",
        ),
    )
    result = service.handle_message("test", "Tester", "帮我弄两块牛排吃吃看")
    assert result["source"] == "local_slu"
    assert result["route"] == "goal"
    assert result["goal"]["target"] == "cooked_beef"


def test_model_chat_falls_through_to_persona(monkeypatch, service):
    """Model says pure chat -> the persona LLM layer owns the reply."""
    _patch_settings(monkeypatch)
    called = {"n": 0}

    def fake_segment(text, settings=None):
        called["n"] += 1
        return ({"segments": [{"type": "chat", "text": text, "route": "chat",
                               "direct_action": None, "goal": None}]}, "local")

    monkeypatch.setattr(slu, "segment_local", fake_segment)
    result = service.handle_message("test", "Tester", "今天心情不错呀")
    assert called["n"] == 1
    assert result["source"] != "local_slu"  # fell through to chat handling


def test_backend_failure_falls_through(monkeypatch, service):
    _patch_settings(monkeypatch)

    def boom(text, settings=None):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(slu, "segment_local", boom)
    result = service.handle_message("test", "Tester", "随便说点什么")
    assert result["source"] != "local_slu"


def test_legacy_backend_never_calls_the_model(monkeypatch, service):
    _patch_settings(monkeypatch, backend="legacy")

    def must_not_run(text, settings=None):
        raise AssertionError("legacy backend must not load the model")

    monkeypatch.setattr(slu, "segment_local", must_not_run)
    result = service.handle_message("test", "Tester", "今天心情不错呀")
    assert result["source"] != "local_slu"


def test_clarify_when_model_is_unsure(monkeypatch, service):
    _patch_settings(monkeypatch)
    monkeypatch.setattr(
        slu, "segment_local",
        lambda text, settings=None: (
            {"segments": [{"type": "instruction", "text": text,
                           "route": "clarify", "direct_action": None,
                           "goal": None}]},
            "local",
        ),
    )
    result = service.handle_message("test", "Tester", "帮我弄点那个啥")
    assert result["source"] == "local_slu"
    assert result["route"] == "clarify"
    assert "不确定" in result["reply"]
