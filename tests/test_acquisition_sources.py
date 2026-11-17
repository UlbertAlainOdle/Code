"""Multi-source acquisition planning (2026-09-05 round).

Every item can have SEVERAL acquisition sources; the planner enumerates them
in a global cost/risk order (smelt < passive mob < stable mine < craft <
hostile mob < mine fallback < expedition mob) and executes the first eligible
one.  Expedition-class sources (mansion/raid/Nether/End mobs) are never
planned implicitly - asking for 不死图腾 yields an honest ExpeditionRequired
report naming the source and habitat, and is only planned when the goal
explicitly sets ``allow_expedition``.

Pinned fixes over the old fixed if-chain:
- redstone/coal mine their ores instead of hunting witch/wither_skeleton;
- glass bottles craft from glass instead of hunting witches;
- totem_of_undying (evoker-only) reports instead of suiciding.
"""

import pytest

from saya_app.services import minecraft_planner


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


def _commands(plan):
    return [step["command"] for step in plan["steps"]]


# ------------------------------------------------------- knowledge integrity

def test_item_mobs_prefers_the_safest_carrier():
    assert minecraft_planner.ITEM_MOBS["totem_of_undying"] == ["evoker"]
    assert minecraft_planner.ITEM_MOBS["coal"] == ["wither_skeleton"]  # expedition-only mob carrier
    assert minecraft_planner.ITEM_FROM_MOB["coal"] == "wither_skeleton"


def test_risk_classes_cover_every_known_mob():
    for mob in minecraft_planner.MOB_DROPS:
        assert minecraft_planner.MOB_RISK[mob] in ("passive", "hostile", "expedition")
    for mob in minecraft_planner.MOB_RISK:
        assert mob in minecraft_planner.MOB_DROPS


# ------------------------------------------------------- expedition handling

def test_totem_requires_expedition_by_default_and_reports_honestly():
    with pytest.raises(minecraft_planner.ExpeditionRequired) as excinfo:
        minecraft_planner.plan_goal(
            {"type": "obtain", "target": "totem_of_undying", "count": 1}, _state())
    message = str(excinfo.value)
    assert "唤魔者" in message and "林地府邸" in message
    assert isinstance(excinfo.value, minecraft_planner.PlanError)


def test_expedition_goal_plans_when_explicitly_allowed():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "totem_of_undying", "count": 1,
         "allow_expedition": True}, _state())
    commands = _commands(plan)
    assert "attack evoker 1" in commands
    assert "pickup nearest" in commands
    attack = next(s for s in plan["steps"] if s["command"] == "attack evoker 1")
    assert attack["description"].startswith("远征狩猎")


def test_ordinary_sources_beat_the_expedition_mob_carrier():
    # Coal's only mob carrier is the wither skeleton (expedition), but the
    # coal ore is a stable natural source and must win.
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "coal", "count": 5}, _state())
    assert "collect coal_ore 5" in _commands(plan)
    assert not any("wither_skeleton" in c for c in _commands(plan))


# ------------------------------------------------------- fixed misroutings

def test_redstone_mines_instead_of_hunting_witches():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "redstone", "count": 5}, _state())
    commands = _commands(plan)
    assert "collect redstone_ore 5" in commands
    assert not any(c.startswith("attack witch") for c in commands)


def test_glass_bottles_craft_instead_of_hunting_witches():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "glass_bottle", "count": 1}, _state())
    commands = _commands(plan)
    assert "craft glass_bottle 3" in commands
    assert not any(c.startswith("attack witch") for c in commands)


def test_hostile_mob_sources_still_work_when_they_are_the_only_source():
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "gunpowder", "count": 3}, _state())
    assert "attack creeper 2" in _commands(plan)
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "rotten_flesh", "count": 3}, _state())
    assert "attack zombie 2" in _commands(plan)
    plan = minecraft_planner.plan_goal(
        {"type": "obtain", "target": "slime_ball", "count": 2}, _state())
    assert any(c.startswith("attack slime") for c in _commands(plan))


# ------------------------------------------------------- chinese names

def test_new_special_items_have_spoken_chinese_names():
    from saya_app.services.minecraft_items import item_catalog
    catalog = item_catalog()
    assert catalog.lookup("不死图腾") == "totem_of_undying"
    assert catalog.chinese("totem_of_undying") == "不死图腾"
    assert catalog.chinese("blaze_rod") == "烈焰棒"
