"""Round-2026-09-06 fixes: offline persona reply, mixed-utterance ack,
special probabilistic block drops, chest phrase coverage in parse_intent.
"""

import pytest

from saya_app.services import minecraft_planner
from saya_app.services.conversation import ConversationService, parse_intent
from saya_app.services.minecraft_items import item_catalog


def _state(inventory=()):
    return {
        "runtime": {
            "inventory": list(inventory),
            "stations": {
                "crafting_tables": [], "furnaces": [{"name": "furnace"}],
                "brewing_stands": [],
            },
            "vitals": {"health": 20, "food": 20},
        }
    }


# ---------------------------------------------------- chest phrase coverage

@pytest.mark.parametrize("text,expected", [
    ("把背包里的泥土存到箱子里去", "deposit dirt all"),
    ("帮我把圆石存进附近的箱子", "deposit cobblestone all"),
    ("从箱子里拿出2个铁锭", "withdraw iron_ingot 2"),
    ("从木桶里取出面包", "withdraw bread 1"),
])
def test_chest_phrase_variants_parse(text, expected):
    assert parse_intent(text) == expected


# ---------------------------------------------------- special block drops

def test_seeds_come_from_grass_with_generous_break_count():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "wheat_seeds", "count": 3}, _state())
    collect = next(s for s in plan["steps"] if s["command"].startswith("collect"))
    assert collect["command"].startswith("collect short_grass ")
    blocks = int(collect["command"].rsplit(" ", 1)[1])
    assert blocks >= 3 * 8  # 12.5% drop rate -> generous multiple
    assert collect["postcondition"] == "inventory:wheat_seeds>=3"


def test_multidrop_sources_use_dense_rates():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "clay_ball", "count": 4}, _state())
    collect = next(s for s in plan["steps"] if s["command"].startswith("collect"))
    assert collect["command"] == "collect clay 4"  # 4.0 per block, no inflation


def test_flint_from_gravel_and_sapling_from_leaves():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "flint", "count": 2}, _state())
    assert any(s["command"].startswith("collect gravel ")
               for s in plan["steps"])
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "oak_sapling", "count": 1}, _state())
    assert any(s["command"].startswith("collect oak_leaves ")
               for s in plan["steps"])


def test_new_aliases_resolve():
    catalog = item_catalog()
    assert catalog.lookup("小麦种子") == "wheat_seeds"
    assert catalog.lookup("种子") == "wheat_seeds"
    assert catalog.lookup("树苗") == "oak_sapling"


# ---------------------------------------------------- mixed ack + offline reply

@pytest.fixture()
def service():
    return ConversationService()


def _patch_slu(monkeypatch, segments_fn):
    monkeypatch.setattr(
        ConversationService, "_slu_settings",
        lambda self: ("model", {"slu_model_url": "http://x", "slu_model_name": "m"}),
    )
    from saya_app.services import slu
    monkeypatch.setattr(slu, "segment_utterance", segments_fn)


def test_mixed_message_gets_chat_ack_plus_task(service, monkeypatch):
    def segments_fn(text, backend="model", credentials=None):
        return ({
            "segments": [
                {"type": "chat", "text": "你今天很好", "route": "chat",
                 "direct_action": None, "goal": None},
                {"type": "instruction", "text": "给我做把镐子", "route": "goal",
                 "direct_action": None,
                 "goal": {"type": "obtain_and_deliver", "target": "wooden_pickaxe",
                          "count": 1, "recipient": "Doctor"}},
            ],
        }, "model")

    _patch_slu(monkeypatch, segments_fn)
    result = service.handle_message("test", "Tester", "你今天很好，给我做把镐子吧")
    assert result["source"] == "local_slu"
    assert "被主人夸" in result["reply"] or "嘿嘿" in result["reply"]
    assert result["goal"]["target"] == "wooden_pickaxe"


def test_pure_instruction_gets_no_ack(service, monkeypatch):
    def segments_fn(text, backend="model", credentials=None):
        return ({
            "segments": [
                {"type": "instruction", "text": text, "route": "goal",
                 "direct_action": None,
                 "goal": {"type": "obtain", "target": "dirt", "count": 1,
                          "recipient": "Doctor"}},
            ],
        }, "model")

    _patch_slu(monkeypatch, segments_fn)
    # A phrasing the deterministic tables do NOT know, so it reaches SLU.
    result = service.handle_message("test", "Tester", "麻烦帮我留意着泥土的事")
    assert result["source"] == "local_slu"
    assert "嘿嘿" not in result["reply"] and "我在听" not in result["reply"]


def test_offline_reply_prefers_local_model(service, monkeypatch):
    from saya_app.services import slu

    monkeypatch.setattr(
        ConversationService, "_slu_settings",
        lambda self: ("model", {"slu_model_url": "http://x/v1", "slu_model_name": "m"}),
    )
    monkeypatch.setattr(
        slu, "chat_reply", lambda *a, **k: "今天也一起加油呀～")
    assert service._offline_reply("今天好累啊") == "今天也一起加油呀～"

    monkeypatch.setattr(slu, "chat_reply", lambda *a, **k: "")
    reply = service._offline_reply("今天好累啊")
    assert "我在听" in reply or "联系不上" in reply  # honest last resort


def test_old_canned_askback_string_is_gone():
    """The ask-back fallback no longer exists anywhere in the service."""
    import inspect
    from saya_app.services import conversation as conv

    source = inspect.getsource(conv)
    assert "是要我在游戏里做一件事" not in source
