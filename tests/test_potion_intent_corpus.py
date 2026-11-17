"""Failure corpus + acceptance tests for the potion delivery milestone.

User-reported gaps being locked down here:
- 我需要一瓶治疗药水 must create a check-existing -> brew-if-needed ->
  precise-deliver plan (it used to fall through as lifeless chat);
- 给我/我需要/帮我酿造 default to delivery, bare 酿造 keeps and reports;
- negations, conditionals and capability questions never create tasks;
- variant identity (potion/healing vs potion/night_vision) is respected in
  counting, goals and postconditions - a night-vision bottle must never
  satisfy a healing-potion goal;
- the Python potion registry mirrors the worker's 1.21.5 registry exactly
  (and there is deliberately no long_healing/long_harming).
"""

import re
from pathlib import Path

import pytest

from saya_app.services import minecraft_mineflayer
from saya_app.services import minecraft_planner
from saya_app.services import potions
from saya_app.services.conversation import validate_goal
from saya_app.services.minecraft_items import item_catalog
from saya_app.services.task_intent import intent_to_goal, parse_task_intent

# ----------------------------------------------------------------- corpus

REQUEST_CASES = [
    # text, item, count, disposition, recipient, method
    ("我需要一瓶治疗药水", "potion/healing", 1, "deliver", "Doctor", "unspecified"),
    ("给我一瓶治疗药水", "potion/healing", 1, "deliver", "Doctor", "unspecified"),
    ("帮我酿造一瓶治疗药水", "potion/healing", 1, "deliver", "Doctor", "brew"),
    ("请帮我酿造两瓶治疗药水吧", "potion/healing", 2, "deliver", "Doctor", "brew"),
    ("给我准备一瓶抗火药水", "potion/fire_resistance", 1, "deliver", "Doctor", "unspecified"),
    ("给我一瓶治疗药水，交给Kurosawa_Jin", "potion/healing", 1, "deliver", "Kurosawa_Jin", "unspecified"),
    ("给我一把木剑", "wooden_sword", 1, "deliver", "Doctor", "unspecified"),
]

KEEP_CASES = [
    ("酿造一瓶治疗药水", "potion/healing", 1),
    ("给自己留两瓶治疗药水", "potion/healing", 2),
]

BLOCKED_CASES = [
    "我不需要治疗药水了",
    "如果去下界可能需要抗火药水",
    "你会做治疗药水吗",
    "不要因为我提到石头就去挖",
]


@pytest.mark.parametrize("text,item,count,disposition,recipient,method", REQUEST_CASES)
def test_request_intents(text, item, count, disposition, recipient, method):
    intent = parse_task_intent(text)
    assert intent is not None, f"未识别出请求意图: {text}"
    assert intent["modality"] == "request", text
    assert intent["item"] == item, text
    assert intent["count"] == count, text
    assert intent["disposition"] == disposition, text
    assert intent["recipient"] == recipient, text
    assert intent["method"] == method, text
    goal = intent_to_goal(intent)
    if disposition == "deliver":
        assert goal == {
            "type": "obtain_and_deliver", "target": item,
            "count": count, "recipient": recipient,
        }
    else:
        assert goal == {"type": "obtain", "target": item, "count": count}


@pytest.mark.parametrize("text,item,count", KEEP_CASES)
def test_keep_intents(text, item, count):
    intent = parse_task_intent(text)
    assert intent is not None, text
    assert intent["modality"] == "request"
    assert intent["disposition"] == "keep", text
    assert intent["item"] == item
    assert intent["count"] == count
    goal = intent_to_goal(intent)
    assert goal == {"type": "obtain", "target": item, "count": count}


@pytest.mark.parametrize("text", BLOCKED_CASES)
def test_blocked_intents_never_become_tasks(text):
    intent = parse_task_intent(text)
    if intent is not None:
        assert intent["modality"] in ("negation", "conditional"), text
        with pytest.raises(ValueError):
            intent_to_goal(intent)
    else:
        # Falls through to the LLM chat layer untouched - also fine, as long
        # as this layer does not turn it into a request.
        assert not intent


def test_ordinary_chat_stays_untouched():
    for text in ("刚才砍树挺有趣的", "今天天气怎么样", "谢谢啦"):
        assert parse_task_intent(text) is None, text


# ------------------------------------------------------------ potion keys

def test_parse_potion_targets():
    assert potions.parse_potion_target("potion/healing")["key"] == "potion/healing"
    assert potions.parse_potion_target("healing_potion")["key"] == "potion/healing"
    assert potions.parse_potion_target("治疗药水")["key"] == "potion/healing"
    assert potions.parse_potion_target("强效治疗药水")["key"] == "potion/strong_healing"
    assert potions.parse_potion_target("喷溅型治疗药水")["key"] == "splash_potion/healing"
    assert potions.parse_potion_target("抗火药水")["key"] == "potion/fire_resistance"
    # Instant effects cannot be extended: no such potion exists.
    assert potions.parse_potion_target("potion/long_healing") is None
    assert "long_healing" not in potions.VANILLA_POTION_IDS
    # Non-potion targets stay untouched.
    assert potions.parse_potion_target("wooden_sword") is None
    assert potions.parse_potion_target("potion") is None


def test_brew_chains():
    assert potions.brew_chain("healing") == [
        ("awkward", "nether_wart", "water"),
        ("healing", "glistering_melon_slice", "awkward"),
    ]
    assert potions.brew_chain("strong_healing") == [
        ("awkward", "nether_wart", "water"),
        ("healing", "glistering_melon_slice", "awkward"),
        ("strong_healing", "glowstone_dust", "healing"),
    ]
    with pytest.raises(potions.UnknownPotionError):
        potions.brew_chain("luck")


# ------------------------------------------------------ registry contract

def test_python_potion_registry_mirrors_worker():
    worker_dir = Path(__file__).resolve().parents[1] / "minecraft_worker"
    potion_text = (worker_dir / "potion.mjs").read_text(encoding="utf-8")
    brewing_text = (worker_dir / "brewing.mjs").read_text(encoding="utf-8")
    match = re.search(r"VANILLA_POTIONS_1_21_5\s*=\s*\[(.*?)\]", potion_text, re.DOTALL)
    assert match, "worker 注册表找不到"
    worker_list = re.findall(r"'([a-z_]+)'", match.group(1))
    assert worker_list == potions.VANILLA_POTIONS_1_21_5

    recipe_pairs = re.findall(
        r"\['([a-z_]+)',\s*\{\s*input:\s*'([a-z_]+)',\s*ingredient:\s*'([a-z_]+)'\s*\}\s*\]",
        brewing_text,
    )
    assert recipe_pairs, "worker 酿造配方图找不到"
    worker_recipes = {out: {"input": inp, "ingredient": ing} for out, inp, ing in recipe_pairs}
    assert worker_recipes == potions.BREWING_RECIPES


# ------------------------------------------------------- variant counting

def _potion_entry(potion_id, count=1, container="potion"):
    return {
        "name": container, "count": count,
        "potion": {"container": container, "potion": potion_id, "custom_effects": 0},
    }


def test_inventory_count_is_variant_aware():
    inventory = [_potion_entry("night_vision", 2), _potion_entry("healing", 1)]
    catalog = item_catalog()
    assert catalog.inventory_count(inventory, "potion/healing") == 1
    assert catalog.inventory_count(inventory, "potion/night_vision") == 2
    assert catalog.inventory_count(inventory, "potion") == 3
    assert catalog.inventory_count(inventory, "potion/strong_healing") == 0
    assert catalog.inventory_count([{"name": "wooden_sword", "count": 2}], "wooden_sword") == 2


# ------------------------------------------------------------ goal format

def test_validate_goal_accepts_potion_variants():
    goal, err = validate_goal({
        "type": "obtain_and_deliver", "target": "potion/healing",
        "count": 2, "recipient": "Doctor",
    })
    assert err is None
    assert goal["target"] == "potion/healing"
    goal, err = validate_goal({"type": "obtain", "target": "治疗药水", "count": 1})
    assert err is None
    assert goal["target"] == "potion/healing"


# ----------------------------------------------------------- plan shapes

def _state(inventory=None, brewing_stands=None):
    return {
        "runtime": {
            "inventory": inventory or [],
            "stations": {
                "crafting_tables": [], "furnaces": [],
                "brewing_stands": brewing_stands or [],
            },
            "vitals": {"health": 20, "food": 20},
        }
    }


def _goal(kind="obtain_and_deliver", count=1, target="potion/healing", recipient="Doctor"):
    return {"type": kind, "target": target, "count": count, "recipient": recipient}


MATERIALS = [
    {"name": "water_bottle", "count": 3},
    {"name": "nether_wart", "count": 3},
    {"name": "blaze_powder", "count": 1},
    {"name": "glistering_melon_slice", "count": 3},
]


def test_existing_potion_is_delivered_without_brewing():
    plan = minecraft_planner.plan_goal(
        _goal(count=1), _state(inventory=[_potion_entry("healing")])
    )
    actions = [s["action"] for s in plan["steps"]]
    assert actions == ["give"]
    assert plan["steps"][0]["command"] == "give potion/healing 1 to Doctor"
    assert plan["steps"][0]["postcondition"] == "delivered:potion/healing"


def test_wrong_variant_never_satisfies_the_goal():
    state = _state(inventory=[_potion_entry("night_vision", 3)])
    with pytest.raises(minecraft_planner.PlanError) as exc:
        minecraft_planner.plan_goal(_goal(count=1), state)
    assert "酿造台" in str(exc.value)


def test_brew_plan_with_materials():
    state = _state(inventory=list(MATERIALS), brewing_stands=[{"name": "brewing_stand"}])
    plan = minecraft_planner.plan_goal(_goal(count=1), state)
    actions = [s["action"] for s in plan["steps"]]
    assert "brew" in actions
    brew = next(s for s in plan["steps"] if s["action"] == "brew")
    assert brew["command"] == "brew healing 1"
    assert brew["postcondition"] == "inventory:potion/healing>=1"
    last = plan["steps"][-1]
    assert last["action"] == "give"
    assert last["command"] == "give potion/healing 1 to Doctor"
    # Reserved materials protect the brew from the auto-discard / drop guard.
    assert brew["reserves"].get("nether_wart") == 1
    assert brew["reserves"].get("water_bottle") == 1


def test_existing_final_potion_reduces_the_brew():
    inventory = list(MATERIALS) + [_potion_entry("healing")]
    state = _state(inventory=inventory, brewing_stands=[{"name": "brewing_stand"}])
    plan = minecraft_planner.plan_goal(_goal(count=2), state)
    brews = [s for s in plan["steps"] if s["action"] == "brew"]
    assert [s["command"] for s in brews] == ["brew healing 1"]
    last = plan["steps"][-1]
    assert last["command"] == "give potion/healing 2 to Doctor"


def test_brew_batches_are_capped_at_three():
    materials = [
        {"name": "water_bottle", "count": 5},
        {"name": "nether_wart", "count": 5},
        {"name": "blaze_powder", "count": 1},
        {"name": "glistering_melon_slice", "count": 5},
    ]
    state = _state(inventory=materials, brewing_stands=[{"name": "brewing_stand"}])
    plan = minecraft_planner.plan_goal(_goal(count=5), state)
    brews = [s for s in plan["steps"] if s["action"] == "brew"]
    assert [s["command"] for s in brews] == ["brew healing 3", "brew healing 2"]
    assert brews[-1]["postcondition"] == "inventory:potion/healing>=5"


def test_missing_materials_are_listed():
    state = _state(inventory=[], brewing_stands=[{"name": "brewing_stand"}])
    with pytest.raises(minecraft_planner.PlanError) as exc:
        minecraft_planner.plan_goal(_goal(count=1), state)
    message = str(exc.value)
    for label in ("水瓶", "下界疣", "烈焰粉", "闪烁的西瓜片"):
        assert label in message, message


def test_missing_brewing_stand_is_reported():
    state = _state(inventory=list(MATERIALS), brewing_stands=[])
    with pytest.raises(minecraft_planner.PlanError) as exc:
        minecraft_planner.plan_goal(_goal(count=1), state)
    assert "酿造台" in str(exc.value)


def test_strong_healing_needs_glowstone_dust():
    state = _state(inventory=list(MATERIALS), brewing_stands=[{"name": "brewing_stand"}])
    with pytest.raises(minecraft_planner.PlanError) as exc:
        minecraft_planner.plan_goal(
            _goal(count=1, target="potion/strong_healing"), state)
    assert "荧石粉" in str(exc.value)


def test_keep_goal_brews_without_delivery():
    state = _state(inventory=list(MATERIALS), brewing_stands=[{"name": "brewing_stand"}])
    plan = minecraft_planner.plan_goal(_goal(kind="obtain", count=1), state)
    actions = [s["action"] for s in plan["steps"]]
    assert "brew" in actions and "give" not in actions
    assert plan["will_deliver"] is False


def test_splash_potion_delivery_only():
    state = _state(inventory=[_potion_entry("healing", 1, container="splash_potion")])
    plan = minecraft_planner.plan_goal(
        _goal(count=1, target="splash_potion/healing"), state)
    assert [s["action"] for s in plan["steps"]] == ["give"]
    assert plan["steps"][0]["command"] == "give splash_potion/healing 1 to Doctor"


def test_splash_potion_cannot_be_brewed_yet():
    state = _state(inventory=list(MATERIALS), brewing_stands=[{"name": "brewing_stand"}])
    with pytest.raises(minecraft_planner.PlanError) as exc:
        minecraft_planner.plan_goal(
            _goal(count=1, target="splash_potion/healing"), state)
    assert "喷溅" in str(exc.value)


def test_describe_goal_uses_potion_labels():
    text = minecraft_planner.describe_goal(_goal(count=2))
    assert "治疗药水" in text and "Doctor" in text


# ------------------------------------------------- survival arbitration

class _VitalsService:
    """Minimal worker stand-in with vitals + inventory for executor tests."""

    def __init__(self, health, inventory):
        self.spoken = []
        self.sent = []
        self._status = {
            "activity": {"id": "idle", "state": "completed", "message": "idle"},
            "runtime": {
                "inventory": inventory,
                "vitals": {"health": health, "food": 20},
            },
        }

    def status(self):
        return self._status

    def send_command(self, text, origin="doctor", quiet=False, task_id=None):
        self.sent.append(text)
        self._status["activity"] = {
            "id": task_id or f"task-{len(self.sent)}",
            "state": "completed", "message": "ok",
        }
        return True

    def _speak(self, text):
        self.spoken.append(text)
        return True


def test_survival_hold_defers_last_healing_potion(monkeypatch):
    service = _VitalsService(
        health=5.0,
        inventory=[_potion_entry("healing")],
    )
    monkeypatch.setattr(minecraft_mineflayer, "minecraft_bot_service", service)
    monkeypatch.setattr(minecraft_planner, "STEP_POLL_S", 0.001)

    plan = minecraft_planner.new_plan(
        _goal(count=1),
        [minecraft_planner.step(
            "give", "give potion/healing 1 to Doctor",
            postcondition="delivered:potion/healing",
            description="把 1 瓶治疗药水交给 Doctor",
        )],
    )
    plan["status"] = "running"
    executor = minecraft_planner.PlanExecutor()
    executor._run_plan(plan)

    assert plan["status"] == "suspended"
    assert service.sent == [], "保命储备不允许被交出"
    assert any("保命" in text for text in service.spoken)


def test_delivery_proceeds_when_healthy_or_spare_exists(monkeypatch):
    for health, inventory in ((20.0, [_potion_entry("healing")]), (5.0, [_potion_entry("healing", 2)])):
        service = _VitalsService(health=health, inventory=inventory)
        monkeypatch.setattr(minecraft_mineflayer, "minecraft_bot_service", service)
        monkeypatch.setattr(minecraft_planner, "STEP_POLL_S", 0.001)

        plan = minecraft_planner.new_plan(
            _goal(count=1),
            [minecraft_planner.step(
                "give", "give potion/healing 1 to Doctor",
                postcondition="delivered:potion/healing",
                description="把 1 瓶治疗药水交给 Doctor",
            )],
        )
        plan["status"] = "running"
        executor = minecraft_planner.PlanExecutor()
        executor._run_plan(plan)

        assert plan["status"] == "completed", (health, len(inventory))
        assert service.sent == ["give potion/healing 1 to Doctor"]
