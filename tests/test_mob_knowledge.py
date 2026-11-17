"""Mob-drop knowledge, cooked-food chains, milking and chat filtering.

User asks: Saya knows ore->ingot flows; she must ALSO know what animals drop
(and in what average amounts), how raw meat becomes cooked food, and some
mechanism facts (milking a cow consumes a bucket).  Locked down here:

- hunting plans: attack N + pickup nearest with an inventory postcondition
  (bad drop luck retries/replans instead of faking success);
- food chains: 牛排 = hunt cows -> pickup -> smelt;
- milk: ensure bucket -> milk step -> milk_bucket postcondition;
- slash-prefixed game lines never enter the conversation router.
"""

import pytest

from saya_app.services import minecraft_intents
from saya_app.services import minecraft_planner
from saya_app.services.conversation import parse_intent
from saya_app.services.minecraft_items import item_catalog


def _state(inventory, **stations):
    runtime_stations = {
        "crafting_tables": [], "furnaces": [{"name": "furnace"}],
        "brewing_stands": [],
    }
    runtime_stations.update(stations)
    return {
        "runtime": {
            "inventory": inventory,
            "stations": runtime_stations,
            "vitals": {"health": 20, "food": 20},
        }
    }


def _commands(plan):
    return [step["command"] for step in plan["steps"]]


# ------------------------------------------------------------ hunting plans

def test_beef_goal_hunts_cows_then_picks_up():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "beef", "count": 3}, _state([]))
    commands = _commands(plan)
    assert "attack cow 2" in commands          # ceil(3 / 1.5)
    assert "pickup nearest" in commands
    pickup = next(s for s in plan["steps"] if s["action"] == "pickup")
    assert pickup["postcondition"] == "inventory:beef>=3"


def test_leather_and_wool_come_from_their_carriers():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "leather", "count": 2}, _state([]))
    assert "attack cow 2" in _commands(plan)
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "wool", "count": 3}, _state([]))
    assert "attack sheep 3" in _commands(plan)


def test_hostile_drops_are_known_too():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "gunpowder", "count": 3}, _state([]))
    assert "attack creeper 2" in _commands(plan)
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "ender_pearl", "count": 1}, _state([]))
    assert "attack enderman 2" in _commands(plan)


def test_existing_stock_skips_the_hunt():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "beef", "count": 2},
        _state([{"name": "beef", "count": 5}]))
    # Enough already carried: no hunting, just the inventory verification step.
    assert _commands(plan) == ["inventory"]


# ------------------------------------------------------------- food chains

def test_steak_chain_hunt_pickup_smelt_deliver():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain_and_deliver", "target": "cooked_beef", "count": 2},
        _state([{"name": "coal", "count": 2}]))
    commands = _commands(plan)
    assert commands[0] == "attack cow 2"
    assert "pickup nearest" in commands
    assert "smelt beef cooked_beef 2 coal 1" in commands
    assert commands[-1] == "give cooked_beef 2 to Doctor"


def test_cooked_porkchop_and_chicken_chains():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "cooked_porkchop", "count": 2},
        _state([{"name": "coal", "count": 2}]))
    assert "attack pig 1" in _commands(plan)
    assert "smelt porkchop cooked_porkchop 2 coal 1" in _commands(plan)
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "cooked_chicken", "count": 1},
        _state([{"name": "coal", "count": 1}]))
    assert "attack chicken 1" in _commands(plan)


def test_chinese_food_aliases_resolve():
    catalog = item_catalog()
    assert catalog.lookup("牛排") == "cooked_beef"
    assert catalog.lookup("羊肉") == "mutton"
    assert catalog.lookup("兔肉") == "rabbit"
    assert catalog.lookup("生鸡肉") == "chicken"
    assert catalog.lookup("牛奶") == "milk_bucket"


# ----------------------------------------------------------------- milking

def test_milk_command_phrases():
    assert parse_intent("挤牛奶") == "milk 1"
    assert parse_intent("挤两桶牛奶") == "milk 2"
    assert parse_intent("给我弄点牛奶") == "milk 1"


def test_milk_plan_ensures_bucket_then_milks():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain_and_deliver", "target": "milk_bucket", "count": 1},
        _state([{"name": "iron_ingot", "count": 3}]))
    commands = _commands(plan)
    assert "craft bucket 1" in commands
    assert "milk 1" in commands
    milk = next(s for s in plan["steps"] if s["action"] == "milk")
    assert milk["postcondition"] == "inventory:milk_bucket>=1"
    assert milk["reserves"] == {"bucket": 1}
    assert commands[-1] == "give milk_bucket 1 to Doctor"


def test_milk_with_bucket_in_stock_skips_crafting():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "milk_bucket", "count": 1},
        _state([{"name": "bucket", "count": 2}]))
    commands = _commands(plan)
    assert "craft bucket 1" not in commands
    assert commands == ["milk 1"]


# ---------------------------------------------------------- chat filtering

@pytest.mark.parametrize("text", ["/home", "/msg Saya hi", "/login hunter2", " /tp spawn"])
def test_slash_lines_are_commands_not_chat(text):
    assert minecraft_intents.is_command_message(text) is True


@pytest.mark.parametrize("text", ["跟我来", "你好呀", "挖点石头"])
def test_normal_chat_is_not_filtered(text):
    assert minecraft_intents.is_command_message(text) is False
