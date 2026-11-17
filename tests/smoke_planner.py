"""Offline planner + executor tests (2026-09 plan sections 4-6 and 8.3).

Runs entirely offline against the local 1.21.5 registry data; the executor
is exercised through a fake mineflayer service that simulates the worker
answering each single-step command.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
tmp.close()
os.environ["SAYA_MC_BOT_CONFIG"] = tmp.name

from saya_app.services.minecraft_planner import (  # noqa: E402
    BLOCK_FOR_ITEM,
    ITEM_FROM_BLOCK,
    PlanError,
    plan_executor,
    plan_goal,
)


def state(inv=None, tables=None, held=""):
    inventory = [{"name": name, "count": count} for name, count in (inv or {}).items()]
    return {
        "runtime": {
            "inventory": inventory,
            "stations": {"crafting_tables": tables or [], "furnaces": []},
            "held_item": held,
        }
    }


def commands_of(goal, st):
    return [s["command"] for s in plan_goal(goal, st)["steps"]]


# --- 8.3 scenario 1: all materials + a nearby table ----------------------
plan = plan_goal({"type": "craft", "target": "wooden_sword", "count": 1},
                 state({"oak_planks": 6, "stick": 2}, tables=[{"x": 1, "y": 64, "z": 2}]))
assert [s["command"] for s in plan["steps"]] == ["craft wooden_sword 1"], plan["steps"]

# --- 8.3 scenario 2: materials but no table --------------------------------
plan = plan_goal({"type": "craft", "target": "wooden_sword", "count": 1},
                 state({"oak_planks": 6, "stick": 2}))
assert [s["command"] for s in plan["steps"]] == [
    "craft crafting_table 1", "place crafting_table", "craft wooden_sword 1",
], plan["steps"]

# --- 8.3 scenario 3: only logs (table nearby, no collection needed) ---------
plan = plan_goal({"type": "craft", "target": "wooden_sword", "count": 1},
                 state({"oak_log": 5}, tables=[{"x": 1, "y": 64, "z": 2}]))
seq = [s["command"] for s in plan["steps"]]
assert seq == ["craft planks 4", "craft stick 4", "craft wooden_sword 1"], seq
# exact material economy: 2+2 planks needed -> one log craft (4), no waste
logs_needed = sum(int(c.split()[-1]) for c in seq if c.startswith("collect "))
assert logs_needed == 0, seq

# --- 8.3 scenario 4: nothing at all -----------------------------------------
plan = plan_goal({"type": "craft", "target": "wooden_sword", "count": 1}, state())
seq = [s["command"] for s in plan["steps"]]
assert "place crafting_table" in seq and seq[-1] == "craft wooden_sword 1", seq
# wood-family planning: never locked onto oak logs / a specific planks type
assert "collect _log" in " ".join(seq) and "craft planks" in " ".join(seq), seq

# --- 8.3 scenario 5: final item already owned --------------------------------
plan = plan_goal({"type": "craft", "target": "wooden_sword", "count": 1},
                 state({"wooden_sword": 1}))
assert len(plan["steps"]) == 1 and plan["steps"][0]["action"] == "inventory"

# --- 8.3 scenario 6: insufficient tool durability -> tool chain ---------------
plan = plan_goal({"type": "obtain", "target": "cobblestone", "count": 8}, state())
seq = [s["command"] for s in plan["steps"]]
assert "craft wooden_pickaxe 1" in seq and "equip wooden_pickaxe" in seq, seq
assert seq[-1] == "collect stone 8", seq
# with a pickaxe already carried, mining is direct
plan = plan_goal({"type": "obtain", "target": "cobblestone", "count": 8},
                 state({"wooden_pickaxe": 1}))
assert [s["command"] for s in plan["steps"]] == ["collect stone 8"]

# --- registry drop contracts: block names are not inventory item names -------
assert ITEM_FROM_BLOCK["iron_ore"] == "raw_iron"
assert ITEM_FROM_BLOCK["deepslate_iron_ore"] == "raw_iron"
assert ITEM_FROM_BLOCK["copper_ore"] == "raw_copper"
assert ITEM_FROM_BLOCK["gold_ore"] == "raw_gold"
assert ITEM_FROM_BLOCK["diamond_ore"] == "diamond"
assert ITEM_FROM_BLOCK["coal_ore"] == "coal"
assert ITEM_FROM_BLOCK["clay"] == "clay_ball"
assert BLOCK_FOR_ITEM["raw_iron"] == "iron_ore"

# Asking for an ore means its normal survival drop.  Existing raw iron already
# satisfies the goal; otherwise the worker mines the ore and the executor
# verifies raw_iron rather than the now-destroyed iron_ore block.
plan = plan_goal({"type": "obtain", "target": "iron_ore", "count": 8},
                 state({"raw_iron": 23}))
assert [s["action"] for s in plan["steps"]] == ["inventory"], plan["steps"]
assert plan["steps"][0]["postcondition"] == "inventory:raw_iron>=8", plan["steps"]
plan = plan_goal({"type": "obtain", "target": "iron_ore", "count": 8},
                 state({"stone_pickaxe": 1}))
assert [s["command"] for s in plan["steps"]] == ["collect iron_ore 8"], plan["steps"]
assert plan["steps"][0]["postcondition"] == "inventory:raw_iron>=8", plan["steps"]

# The same rule applies to another ore and does not recurse through its storage
# block recipe.
plan = plan_goal({"type": "obtain", "target": "diamond_ore", "count": 3},
                 state({"iron_pickaxe": 1}))
assert [s["command"] for s in plan["steps"]] == ["collect diamond_ore 3"]
assert plan["steps"][0]["postcondition"] == "inventory:diamond>=3"

# --- 8.3 scenario 7: mixed wood types (planks follow the owned log) ---------
plan = plan_goal({"type": "craft", "target": "wooden_sword", "count": 1},
                 state({"birch_log": 3}, tables=[{"x": 0, "y": 60, "z": 0}]))
seq = [s["command"] for s in plan["steps"]]
assert seq == ["craft planks 4", "craft stick 4", "craft wooden_sword 1"], seq

# --- 8.3 scenario 8: materials reserved by another running plan ----------------
from saya_app.services import minecraft_planner as planner_module  # noqa: E402

queued = plan_goal({"type": "craft", "target": "crafting_table", "count": 1},
                   state({"oak_planks": 4}))
queued["status"] = "running"
queued["current_step"] = 0
plan_executor._plan = queued
reserved = plan_executor.reserved_items()
assert reserved.get("planks", 0) >= 4, reserved
plan_executor._plan = None

# --- 8.3 scenario 9: alternative ingredients ------------------------------------
plan = plan_goal({"type": "craft", "target": "stone_pickaxe", "count": 1},
                 state({"oak_log": 3, "cobblestone": 3}, tables=[]))
seq = [s["command"] for s in plan["steps"]]
assert "craft stone_pickaxe 1" in seq, seq
# cobblestone owned -> no stone collection needed
assert not any(c.startswith("collect stone") for c in seq), seq

# --- 8.3 scenario 10: furnace planning -----------------------------------------
furnace_state = state({"raw_iron": 3, "coal": 1})
furnace_state["runtime"]["stations"]["furnaces"] = [{"x": 1, "y": 64, "z": 1}]
plan = plan_goal({"type": "obtain", "target": "iron_ingot", "count": 3}, furnace_state)
assert [s["command"] for s in plan["steps"]] == [
    "smelt raw_iron iron_ingot 3 coal 1"
], plan["steps"]
assert plan["steps"][0]["postcondition"] == "inventory:iron_ingot>=3"

blast_state = state({"raw_iron": 3, "coal": 1})
blast_state["runtime"]["stations"]["furnaces"] = [
    {"x": 1, "y": 64, "z": 1, "name": "blast_furnace"}
]
plan = plan_goal({"type": "obtain", "target": "iron_ingot", "count": 3}, blast_state)
assert [s["command"] for s in plan["steps"]] == [
    "smelt raw_iron iron_ingot 3 coal 1"
], plan["steps"]

smoker_state = state({"potato": 2, "coal": 1})
smoker_state["runtime"]["stations"]["furnaces"] = [
    {"x": 1, "y": 64, "z": 1, "name": "smoker"}
]
plan = plan_goal({"type": "obtain", "target": "baked_potato", "count": 2}, smoker_state)
assert [s["command"] for s in plan["steps"]] == [
    "smelt potato baked_potato 2 coal 1"
], plan["steps"]

wrong_station = state({"sand": 1, "coal": 1})
wrong_station["runtime"]["stations"]["furnaces"] = [
    {"x": 1, "y": 64, "z": 1, "name": "blast_furnace"}
]
plan = plan_goal({"type": "obtain", "target": "glass", "count": 1}, wrong_station)
assert "place furnace" in [s["command"] for s in plan["steps"]], plan["steps"]

plan = plan_goal({"type": "obtain", "target": "iron_ingot", "count": 1}, state())
seq = [s["command"] for s in plan["steps"]]
assert "place furnace" in seq and any(c.startswith("smelt raw_iron iron_ingot 1 ") for c in seq), seq
print("planner 8.3 scenarios: OK")

# --- goal descriptions ------------------------------------------------------
plan = plan_goal({"type": "obtain_and_deliver", "target": "木剑", "count": 1,
                  "recipient": "Doctor"}, state())
assert "木剑" in plan["goal_description"] and plan["will_deliver"] is True
assert plan["steps"][-1]["action"] == "give"
print("goal descriptions + delivery tail: OK")

plan = plan_goal({"type": "combat", "target": "zombie", "count": 3}, state())
assert plan["goal_description"] == "击杀3个zombie"
assert plan["steps"][0]["command"] == "attack zombie 3"
assert plan["steps"][0]["postcondition"] == "kills:zombie>=3"


# --------------------------------------------------------------------------
# Executor end-to-end against a simulated worker
# --------------------------------------------------------------------------
import saya_app.services.minecraft_mineflayer as mm  # noqa: E402
import saya_app.services.conversation as conv  # noqa: E402

SIM_RECIPES = {
    # item -> (per-craft ingredients, result count, needs table)
    "oak_planks": ({"_log": 1}, 4, False),
    "stick": ({"planks": 2}, 4, False),
    "crafting_table": ({"planks": 4}, 1, False),
    "wooden_sword": ({"planks": 2, "stick": 1}, 1, True),
    "wooden_pickaxe": ({"planks": 3, "stick": 2}, 1, True),
}

svc = mm.minecraft_bot_service
spoken = []
activity_seq = [0]
lock = threading.Lock()


def _inventory_counts():
    runtime = svc.state.get("runtime") or {}
    counts = {}
    for entry in runtime.get("inventory") or []:
        counts[entry["name"]] = counts.get(entry["name"], 0) + int(entry.get("count") or 0)
    return counts


def _set_inventory(counts):
    svc.state["runtime"]["inventory"] = [
        {"name": name, "count": count} for name, count in counts.items()
    ]


def _family_count(counts, family):
    if family == "_log":
        return sum(v for k, v in counts.items() if k.endswith(("_log", "_stem")))
    if family == "planks":
        return sum(v for k, v in counts.items() if k.endswith("_planks"))
    return counts.get(family, 0)


def _finish_activity(result, message):
    activity_seq[0] += 1
    svc.state["activity"] = {
        "id": f"act-{activity_seq[0]}",
        "command": "",
        "state": result,
        "message": message,
    }


def _apply_command(text):
    counts = _inventory_counts()
    parts = text.split()
    verb = parts[0] if parts else ""
    if verb == "craft":
        item, count = parts[1], int(parts[2])
        if item == "planks":  # family command: resolve to whatever log is held
            item = next((name for name in counts if name.endswith("_log")), "oak_log").replace(
                "_log", "_planks")
        ingredients, result_count, _ = SIM_RECIPES[item]
        crafts = max(1, count // result_count)
        for ingredient, per in ingredients.items():
            need = per * crafts
            remaining = need
            for name in sorted(counts):
                if remaining <= 0:
                    break
                if ingredient == "_log" and name.endswith(("_log", "_stem")) \
                        or ingredient == "planks" and name.endswith("_planks") \
                        or name == ingredient:
                    take = min(counts[name], remaining)
                    counts[name] -= take
                    remaining -= take
            assert remaining <= 0, f"sim missing {ingredient} for {text}"
            counts = {k: v for k, v in counts.items() if v > 0}
        counts[item] = counts.get(item, 0) + crafts * result_count
        _set_inventory(counts)
        _finish_activity("completed", f"made {count} {item}")
    elif verb == "place":
        assert counts.get("crafting_table", 0) >= 1
        counts["crafting_table"] -= 1
        counts = {k: v for k, v in counts.items() if v > 0}
        _set_inventory(counts)
        svc.state["runtime"]["stations"]["crafting_tables"] = [{"x": 1, "y": 64, "z": 1}]
        _finish_activity("completed", "placed crafting_table")
    elif verb == "equip":
        svc.state["runtime"]["held_item"] = parts[1]
        _finish_activity("completed", f"equipped {parts[1]}")
    elif verb == "collect":
        block = parts[1]
        # '_log' family goals grow oak trees in the simulation world.
        gained = {"oak_log": "oak_log", "stone": "cobblestone", "_log": "oak_log"}.get(block, block)
        counts[gained] = counts.get(gained, 0) + int(parts[2])
        _set_inventory(counts)
        _finish_activity("completed", f"collected {parts[2]} {block}")
    elif verb == "inventory":
        _finish_activity("completed", "checked")
    else:
        _finish_activity("completed", text)


def fake_send_command(text, origin="doctor", quiet=False, task_id=None):
    with lock:
        spoken.append(f"SEND:{origin}:{text}")
        _apply_command(text)
    return True


svc.send_command = fake_send_command
svc._speak = lambda text: spoken.append(f"SPEAK:{text}") or True
svc.state["running"] = True
svc.state["connected"] = True
conv.conversation_service._record = lambda *a, **k: None

# Happy path: craft a wooden sword from scratch (table included).
svc.state["runtime"] = {
    "inventory": [], "stations": {"crafting_tables": [], "furnaces": []},
    "held_item": "",
}
svc.state["activity"] = {}
plan = plan_goal({"type": "obtain_and_deliver", "target": "wooden_sword",
                  "count": 1, "recipient": "Doctor"}, svc.status())
plan_executor.submit(plan)
deadline = time.time() + 30
while time.time() < deadline:
    view = plan_executor.state()
    if not view.get("active") and view.get("history"):
        break
    time.sleep(0.1)
assert not plan_executor.state().get("active"), plan_executor.state()
history = plan_executor.state()["history"]
assert history and history[-1]["status"] == "completed", history
final_counts = _inventory_counts()
assert final_counts.get("wooden_sword") == 1, final_counts
gives = [line for line in spoken if "give wooden_sword" in line]
assert gives and gives[0].startswith("SEND:plan:"), spoken
assert any(line.startswith("SPEAK:") and "木剑" in line for line in spoken), spoken
# quiet execution: every plan-origin send must be quiet (checked via signature
# call below) and the worker never received a competing command burst.
assert all(not line.startswith("SEND:saya:") for line in spoken), spoken
print("executor happy path (craft + deliver): OK")

# Regression: a worker activity saying "completed" is not sufficient by
# itself.  The first reply deliberately leaves the inventory unchanged; the
# executor must keep the same step and retry it.  Only the second reply, which
# applies the expected inventory delta, may advance the plan.
postcondition_sends = []


def delayed_inventory_send_command(text, origin="doctor", quiet=False, task_id=None):
    postcondition_sends.append(text)
    if text == "craft wooden_sword 1" and postcondition_sends.count(text) == 1:
        _finish_activity("completed", "simulated completion without inventory update")
    else:
        _apply_command(text)
    return True


svc.send_command = delayed_inventory_send_command
svc.state["runtime"] = {
    "inventory": [{"name": "oak_planks", "count": 6}, {"name": "stick", "count": 2}],
    "stations": {"crafting_tables": [{"x": 1, "y": 64, "z": 2}], "furnaces": []},
    "held_item": "",
}
svc.state["activity"] = {}
history_before = len(plan_executor.state().get("history") or [])
postcondition_plan = plan_goal(
    {"type": "craft", "target": "wooden_sword", "count": 1}, svc.status()
)
assert [entry["command"] for entry in postcondition_plan["steps"]] == [
    "craft wooden_sword 1"
], postcondition_plan["steps"]
plan_executor.submit(postcondition_plan)
deadline = time.time() + 20
while time.time() < deadline:
    view = plan_executor.state()
    if not view.get("active") and len(view.get("history") or []) > history_before:
        break
    time.sleep(0.1)
history = plan_executor.state()["history"]
assert history[-1]["status"] == "completed", history
assert postcondition_sends == ["craft wooden_sword 1", "craft wooden_sword 1"], postcondition_sends
assert postcondition_plan["steps"][0]["attempts"] == 1, postcondition_plan["steps"]
assert _inventory_counts().get("wooden_sword") == 1, _inventory_counts()
svc.send_command = fake_send_command
print("executor completed + postcondition gate regression: OK")

# Failure path: every craft step fails at the worker -> retry, replan, abort.
def broken_send_command(text, origin="doctor", quiet=False, task_id=None):
    spoken.append(f"SEND:{origin}:{text}")
    if text.startswith("craft"):
        _finish_activity("failed", "simulated craft failure")
    else:
        _finish_activity("completed", "simulated success")
    return True


# Failure path: every craft step fails at the worker -> retry, replan, SUSPEND
# (never auto-fail; only Doctor abandons a plan).
import saya_app.services.minecraft_planner as planner_module  # noqa: E402

planner_module.SUSPEND_CHECK_INTERVAL_S = 1  # fast periodic checks for the test

def broken_send_command(text, origin="doctor", quiet=False, task_id=None):
    spoken.append(f"SEND:{origin}:{text}")
    if text.startswith("craft") and "crafting_table" in text:
        _finish_activity("failed", "simulated craft failure")  # placement never works
    else:
        _finish_activity("completed", "simulated success")
    return True


svc.send_command = broken_send_command
svc.state["runtime"] = {
    "inventory": [{"name": "oak_planks", "count": 64}, {"name": "stick", "count": 64}],
    "stations": {"crafting_tables": [], "furnaces": []},
    "held_item": "",
    "position": {"x": 0, "y": 64, "z": 0},
}
plan = plan_goal({"type": "craft", "target": "wooden_sword", "count": 1}, svc.status())
plan_executor.submit(plan)
deadline = time.time() + 40
while time.time() < deadline:
    view = plan_executor.state()
    if not view.get("active") and view.get("suspended"):
        break
    time.sleep(0.1)
view = plan_executor.state()
assert not view.get("active") and len(view["suspended"]) == 1, view
suspended = view["suspended"][0]
assert "工作台" in suspended["reason"] or "工作台" in suspended["goal_description"], suspended
assert view["history"][-1]["status"] == "suspended"
print("executor suspend path (no auto-fail): OK")

# Auto-resume: once the blocker is gone AND Saya moved somewhere new, the
# suspended plan is re-planned and completes.
svc.send_command = fake_send_command
svc.state["runtime"]["inventory"] = [
    {"name": "oak_planks", "count": 64}, {"name": "stick", "count": 64},
]
svc.state["runtime"]["stations"]["crafting_tables"] = [{"x": 1, "y": 64, "z": 1}]
svc.state["runtime"]["position"] = {"x": 12, "y": 64, "z": 6}  # >= 8 blocks away
deadline = time.time() + 30
while time.time() < deadline:
    view = plan_executor.state()
    if not view.get("active") and not view.get("suspended") and view.get("history")[-1]["status"] == "completed":
        break
    time.sleep(0.1)
view = plan_executor.state()
assert not view.get("suspended"), view
assert view["history"][-1]["status"] == "completed", view["history"][-1]
print("executor auto-resume on movement: OK")

# Doctor abandons a suspended plan explicitly.
def failing_collect(text, origin="doctor", quiet=False, task_id=None):
    spoken.append(f"SEND:{origin}:{text}")
    _finish_activity("failed", "64 格内没有找到可达的 cobblestone")
    return True


svc.send_command = failing_collect
svc.state["runtime"] = {
    "inventory": [], "stations": {"crafting_tables": [], "furnaces": []},
    "held_item": "", "position": {"x": 0, "y": 64, "z": 0},
}
plan = plan_goal({"type": "obtain", "target": "cobblestone", "count": 4}, svc.status())
plan_executor.submit(plan)
deadline = time.time() + 40
while time.time() < deadline:
    view = plan_executor.state()
    if not view.get("active") and view.get("suspended"):
        break
    time.sleep(0.1)
plan_id = plan_executor.state()["suspended"][0]["plan_id"]
assert plan_executor.abandon_suspended(plan_id) is True
view = plan_executor.state()
assert not view.get("suspended"), view
assert view["history"][-1]["status"] == "cancelled"
assert view["history"][-1]["last_error"] == "主人放弃了这个任务"
svc.send_command = fake_send_command
print("executor doctor abandon: OK")

# Cancel path.
svc.state["runtime"] = {
    "inventory": [{"name": "oak_log", "count": 64}],
    "stations": {"crafting_tables": [], "furnaces": []},
    "held_item": "",
}
plan = plan_goal({"type": "craft", "target": "crafting_table", "count": 1}, svc.status())
plan_executor.submit(plan)
time.sleep(0.3)
plan_executor.cancel(reason="doctor stop")
deadline = time.time() + 15
while time.time() < deadline and plan_executor.state().get("active"):
    time.sleep(0.1)
history = plan_executor.state()["history"]
assert history[-1]["status"] == "cancelled", history
print("executor cancel path: OK")

# state view exposes step progress for the desktop console
view = plan_executor.state()
assert {"active", "history"} <= set(view)
print("executor state view: OK")

os.unlink(tmp.name)
print("\nPlanner + executor smoke tests passed.")
