"""Root-cause regressions for smelting request routing (烧矿 bug family).

User-reported: Saya stopped using the furnace to smelt ore.  Two causes were
fixed:

1. The task-intent layer treated 烧/炼 like gathering verbs: the named noun
   became the goal item, so 烧炼粗铁 produced "obtain raw_iron" (go mining!)
   instead of smelting it into ingots, and 烧木头 produced "obtain _log"
   (go chopping!) instead of charcoal.  Smelting verbs are removed from the
   intent layer - they belong to the deterministic smelt handler.
2. The deterministic smelt parser only understood PRODUCT-named requests
   (炼铁锭); INPUT-named ones (烧粗铁/烧木头/烧沙子) dead-ended.  Both
   directions now resolve via SMELT_PRODUCT_FOR_INPUT.

The planner experiments at the bottom assert the concrete step commands the
executor sends to the worker for iron, charcoal (with the log-as-fuel
fallback) and glass.
"""

import pytest

from saya_app.services import minecraft_planner
from saya_app.services.conversation import SMELT_PRODUCT_FOR_INPUT, parse_intent
from saya_app.services.task_intent import parse_task_intent

INTENT_FALLTHROUGH_CASES = [
    "烧炼粗铁", "烧木头", "烧沙子", "帮我烧粗铁", "炼3个铁锭", "熔炼金锭",
]

PARSE_CASES = [
    # 原料命名（此前一直是死路）
    ("烧炼粗铁", "smelt raw_iron iron_ingot 1"),
    ("烧木头", "smelt _log charcoal 1"),
    ("烧沙子", "smelt sand glass 1"),
    ("帮我烧粗铁", "smelt raw_iron iron_ingot 1"),
    ("烧3个木头", "smelt _log charcoal 3"),
    # 产物命名（原有能力，必须保留）
    ("炼3个铁锭", "smelt raw_iron iron_ingot 3"),
    ("熔炼金锭", "smelt raw_gold gold_ingot 1"),
]

GATHERING_REGRESSION_CASES = [
    # 采集短语必须留在确定性层（"点"→8 的数量逻辑等）
    ("挖点石头", "collect cobblestone 8"),
    ("挖铁矿", "collect iron_ore 1"),
    ("砍树", "chop 4"),
]


@pytest.mark.parametrize("text", INTENT_FALLTHROUGH_CASES)
def test_smelt_phrases_fall_through_to_deterministic_layer(text):
    assert parse_task_intent(text) is None, text


@pytest.mark.parametrize("text,expected", PARSE_CASES)
def test_smelt_phrases_resolve_both_directions(text, expected):
    assert parse_intent(text) == expected


@pytest.mark.parametrize("text,expected", GATHERING_REGRESSION_CASES)
def test_gathering_phrases_keep_deterministic_logic(text, expected):
    assert parse_task_intent(text) is None, text
    assert parse_intent(text) == expected


def test_smelt_product_map_is_the_inverse():
    from saya_app.services.conversation import SMELT_INPUT_FOR_OUTPUT

    assert SMELT_PRODUCT_FOR_INPUT == {
        source: output for output, source in SMELT_INPUT_FOR_OUTPUT.items()
    }
    assert SMELT_PRODUCT_FOR_INPUT["raw_iron"] == "iron_ingot"
    assert SMELT_PRODUCT_FOR_INPUT["_log"] == "charcoal"
    assert SMELT_PRODUCT_FOR_INPUT["sand"] == "glass"


# ------------------------------------------------------- planner experiments

def _state(inventory, with_furnace=True):
    return {
        "runtime": {
            "inventory": inventory,
            "stations": {
                "crafting_tables": [], "furnaces": [{"name": "furnace"}] if with_furnace else [],
                "brewing_stands": [],
            },
            "vitals": {"health": 20, "food": 20},
        }
    }


def _commands(plan):
    return [step["command"] for step in plan["steps"]]


def test_plan_smelts_iron_ingots_from_stocked_raw_iron():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "iron_ingot", "count": 3},
        _state([{"name": "raw_iron", "count": 3}, {"name": "coal", "count": 2}]),
    )
    assert _commands(plan) == ["smelt raw_iron iron_ingot 3 coal 1"]


def test_plan_charcoal_from_logs():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "charcoal", "count": 2},
        _state([{"name": "oak_log", "count": 4}, {"name": "coal", "count": 1}]),
    )
    assert _commands(plan) == ["smelt _log charcoal 2 coal 1"]


def test_plan_charcoal_uses_logs_as_fuel_without_coal():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "charcoal", "count": 2},
        _state([{"name": "oak_log", "count": 8}]),
    )
    assert _commands(plan) == ["smelt _log charcoal 2 _log 2"]


def test_plan_glass_from_sand():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "glass", "count": 1},
        _state([{"name": "sand", "count": 1}, {"name": "coal", "count": 1}]),
    )
    assert _commands(plan) == ["smelt sand glass 1 coal 1"]


def test_plan_builds_furnace_from_scratch():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "iron_ingot", "count": 1},
        _state([{"name": "raw_iron", "count": 1}, {"name": "coal", "count": 1}], with_furnace=False),
    )
    commands = _commands(plan)
    assert "craft furnace 1" in commands and "place furnace" in commands
    assert any(command.startswith("smelt raw_iron") for command in commands)
