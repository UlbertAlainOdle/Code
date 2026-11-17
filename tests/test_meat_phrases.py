"""Regression corpus of the user's exact in-game phrases (2026-09-04).

Reported: with pigs standing next to her,
- 我需要猪肉  -> "不知道怎么做"  (猪肉 had NO alias at all)
- 给我猪肉    -> same
- 我需要熟猪排 -> same on the live box (stale backend), though the planner
  chain itself was correct.

These tests run every phrase through the FULL local pipeline:
phrase -> task intent -> goal -> plan commands.  They exist so that a
missing spoken alias can never again ship under green tests that only used
the written forms (生猪排/牛排).
"""

import pytest

from saya_app.services import minecraft_planner
from saya_app.services.conversation import parse_intent
from saya_app.services.minecraft_items import item_catalog
from saya_app.services.task_intent import intent_to_goal, parse_task_intent


def _state(inventory):
    return {
        "runtime": {
            "inventory": inventory,
            "stations": {"crafting_tables": [], "furnaces": [{"name": "furnace"}], "brewing_stands": []},
            "vitals": {"health": 20, "food": 20},
        }
    }


def _plan_for(phrase, inventory):
    intent = parse_task_intent(phrase)
    assert intent is not None, f"意图层未识别: {phrase}"
    goal = intent_to_goal(intent)
    return goal, minecraft_planner.plan_goal(goal, _state(inventory))


RAW_MEAT_GOALS = [
    ("我需要猪肉", "porkchop", "pig"),
    ("给我猪肉", "porkchop", "pig"),
    ("我需要牛肉", "beef", "cow"),
    ("我需要羊肉", "mutton", "sheep"),
]


@pytest.mark.parametrize("phrase,item,mob", RAW_MEAT_GOALS)
def test_raw_meat_phrases_hunt_the_right_animal(phrase, item, mob):
    goal, plan = _plan_for(phrase, [])
    assert goal == {"type": "obtain_and_deliver", "target": item, "count": 1, "recipient": "Doctor"}
    commands = [step["command"] for step in plan["steps"]]
    assert f"attack {mob} 1" in commands
    assert "pickup nearest" in commands
    assert commands[-1] == f"give {item} 1 to Doctor"


COOKED_MEAT_GOALS = [
    ("我需要熟猪排", "cooked_porkchop", "porkchop"),
    ("给我熟猪排", "cooked_porkchop", "porkchop"),
    ("我需要熟猪肉", "cooked_porkchop", "porkchop"),
    ("我需要熟牛肉", "cooked_beef", "beef"),
    ("我需要牛排", "cooked_beef", "beef"),
    ("给我烤鸡", "cooked_chicken", "chicken"),
]


@pytest.mark.parametrize("phrase,item,raw", COOKED_MEAT_GOALS)
def test_cooked_meat_phrases_from_scratch_hunt_then_smelt(phrase, item, raw):
    goal, plan = _plan_for(phrase, [{"name": "coal", "count": 1}])
    assert goal["target"] == item
    commands = [step["command"] for step in plan["steps"]]
    assert "pickup nearest" in commands
    assert any(command.startswith(f"smelt {raw} {item}") for command in commands)
    assert commands[-1] == f"give {item} 1 to Doctor"


def test_cooked_phrase_with_raw_meat_already_carried():
    # "给他猪肉" then asking for 熟猪排: no hunting, straight to the furnace.
    goal, plan = _plan_for("我需要熟猪排", [{"name": "porkchop", "count": 3}, {"name": "coal", "count": 2}])
    commands = [step["command"] for step in plan["steps"]]
    assert commands == ["smelt porkchop cooked_porkchop 1 coal 1", "give cooked_porkchop 1 to Doctor"]


def test_burn_phrases_resolve_deterministically():
    assert parse_intent("帮我烧熟猪排") == "smelt porkchop cooked_porkchop 1"
    assert parse_intent("烧熟猪排") == "smelt porkchop cooked_porkchop 1"
    assert parse_intent("烧两块猪肉") == "smelt porkchop cooked_porkchop 2"


@pytest.mark.parametrize("alias,expected", [
    ("猪肉", "porkchop"), ("猪排", "porkchop"), ("熟猪肉", "cooked_porkchop"),
    ("烤猪排", "cooked_porkchop"), ("牛肉", "beef"), ("熟牛肉", "cooked_beef"),
    ("烤牛排", "cooked_beef"), ("烤鸡", "cooked_chicken"), ("烤羊肉", "cooked_mutton"),
    ("烤兔肉", "cooked_rabbit"), ("生猪排", "porkchop"), ("牛排", "cooked_beef"),
])
def test_spoken_meat_aliases_exist(alias, expected):
    assert item_catalog().lookup(alias) == expected
