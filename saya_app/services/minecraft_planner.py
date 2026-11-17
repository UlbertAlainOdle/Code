"""Deterministic recipe planner and single-step plan executor
(2026-09 plan sections 4-6).

Division of labour:

- The LLM only understands the goal ("obtain_and_deliver wooden_sword 1 to
  Doctor"); this module computes every concrete step from the live inventory,
  the local 1.21.5 registry (items/blocks/recipes) and the nearby-station
  snapshot.  The model never guesses recipes or inventory.
- The PlanExecutor sends exactly ONE foreground command at a time, waits for
  the worker activity to finish, verifies the step's postcondition against
  the reported inventory, and only then advances (plan section 5).  A failed
  step is retried once, then the remaining goal is re-planned once, then the
  plan aborts with a report.
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from pathlib import Path

from .minecraft_items import DEFAULT_VERSION, item_catalog, matches_family
from . import potions

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "minecraft"

TOOL_TIER = {"wooden": 1, "golden": 1, "stone": 2, "iron": 3, "diamond": 4, "netherite": 5}
TIER_MATERIAL = {1: "wooden", 2: "stone", 3: "iron", 4: "diamond"}
TOOL_FAMILIES = ("pickaxe", "axe", "shovel", "hoe", "sword")

SMELTING_RECIPES = {
    "iron_ingot": "raw_iron", "gold_ingot": "raw_gold", "copper_ingot": "raw_copper",
    "glass": "sand", "charcoal": "_log", "stone": "cobblestone",
    "smooth_stone": "stone", "brick": "clay_ball", "terracotta": "clay",
    "nether_brick": "netherrack", "cooked_beef": "beef",
    "cooked_porkchop": "porkchop", "cooked_chicken": "chicken",
    "cooked_mutton": "mutton", "cooked_rabbit": "rabbit", "cooked_cod": "cod",
    "cooked_salmon": "salmon", "baked_potato": "potato", "dried_kelp": "kelp",
    "green_dye": "cactus", "sponge": "wet_sponge",
}
BLAST_FURNACE_INPUTS = {"raw_iron", "raw_gold", "raw_copper"}
SMOKER_INPUTS = {
    "beef", "porkchop", "chicken", "mutton", "rabbit", "cod", "salmon",
    "potato", "kelp",
}
FUEL_CAPACITY = {
    "coal_block": 80, "dried_kelp_block": 20, "blaze_rod": 12,
    "coal": 8, "charcoal": 8, "_log": 1.5, "planks": 1.5, "stick": 0.5,
}

PLAN_STEP_TIMEOUT_S = 200.0
STEP_POLL_S = 0.5
MAX_STEP_ATTEMPTS = 2
MAX_REPLANS = 1
# Worker state snapshots lag real actions by up to ~1s, and the craft-table
# flow can lag 2-3s more.  A completed activity whose inventory postcondition
# momentarily fails gets this grace window before the step is declared failed
# - a false failure here used to cascade into re-crafting already-finished
# output while the item sat in hand (looked like: crafted, stood still, then
# "later" delivered after retry/replan recovered).  6s covers the observed
# worst case without masking a genuinely failed step for long.
VERIFY_GRACE_S = 6.0
CRAFT_LOG_LIMIT = 20

# Task suspension (2026-09 plan): plans whose steps cannot be completed right
# now (e.g. nothing collectible nearby) are parked instead of failing.  The
# executor re-checks them periodically and resumes one whenever Saya has
# moved somewhere new; only Doctor can abandon them.
SUSPEND_CHECK_INTERVAL_S = 20.0
RESUME_MOVE_DISTANCE = 8.0
MAX_SUSPENDED = 8


def _load_json(version, filename):
    try:
        return json.loads((DATA_DIR / str(version) / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _build_harvest_drop_maps(version=DEFAULT_VERSION):
    """Build block -> normal drop mappings from the shipped MC registry.

    This is deliberately data-driven: ores, stone, clay, grass blocks and
    future single-drop blocks all use the same completion contract.  Blocks
    with no deterministic registry drop (for example leaves) are omitted.
    """
    items_by_id = {
        int(row["id"]): str(row.get("name") or row["id"])
        for row in _load_json(version, "items.json")
        if isinstance(row, dict) and "id" in row
    }
    item_from_block = {}
    for block in _load_json(version, "blocks.json"):
        if not isinstance(block, dict) or not block.get("name"):
            continue
        drops = []
        for item_id in block.get("drops") or []:
            try:
                name = items_by_id.get(int(item_id))
            except (TypeError, ValueError):
                name = None
            if name and name not in drops:
                drops.append(name)
        if len(drops) == 1:
            item_from_block[str(block["name"])] = drops[0]

    block_for_item = {}
    for block, item in item_from_block.items():
        # Prefer the ordinary ore over its deepslate variant and prefer a
        # source whose name differs from the result (stone -> cobblestone).
        score = (block.startswith("deepslate_"), block == item, len(block))
        previous = block_for_item.get(item)
        previous_score = (
            previous.startswith("deepslate_"), previous == item, len(previous)
        ) if previous else None
        if previous_score is None or score < previous_score:
            block_for_item[item] = block
    return item_from_block, block_for_item


ITEM_FROM_BLOCK, BLOCK_FOR_ITEM = _build_harvest_drop_maps()


def _canon_ingredient(name):
    """Canonicalize interchangeable ingredient families.

    minecraft-data lists every planks/log variant as separate recipes (in an
    arbitrary order); for planning, any log crafts any planks family member
    and cobbled deepslate substitutes cobblestone in stone tools.
    """
    if matches_family(name, "_log"):
        return "_log"
    if matches_family(name, "planks"):
        return "planks"
    if name == "cobbled_deepslate":
        return "cobblestone"
    return name


class RecipeBook:
    """Flattened 1.21.5 crafting recipes with table requirements."""

    def __init__(self, version=DEFAULT_VERSION):
        items = _load_json(version, "items.json")
        self.id_to_name = {
            int(row["id"]): str(row.get("name") or row["id"])
            for row in items if isinstance(row, dict) and "id" in row
        }
        self.recipes = {}   # result name -> [{ingredients, result_count, requires_table}]
        for key, variants in (_load_json(version, "recipes.json") or {}).items():
            try:
                result_name = self.id_to_name.get(int(key))
            except (TypeError, ValueError):
                continue
            if not result_name:
                continue
            flat = []
            for raw in variants or []:
                ingredients = {}
                requires_table = False
                shape = raw.get("inShape")
                if isinstance(shape, list) and shape:
                    requires_table = len(shape) > 2 or len(shape[0]) > 2
                    for row in shape:
                        for cell in row:
                            if cell is None:
                                continue
                            name = self.id_to_name.get(int(cell))
                            if name:
                                name = _canon_ingredient(name)
                                ingredients[name] = ingredients.get(name, 0) + 1
                elif isinstance(raw.get("ingredients"), list):
                    for cell in raw["ingredients"]:
                        name = self.id_to_name.get(int(cell))
                        if name:
                            name = _canon_ingredient(name)
                            ingredients[name] = ingredients.get(name, 0) + 1
                if not ingredients:
                    continue
                flat.append({
                    "ingredients": ingredients,
                    "result_count": max(1, int((raw.get("result") or {}).get("count") or 1)),
                    "requires_table": bool(requires_table),
                })
            if flat:
                # Deduplicate family-equivalent variants (planks from any log).
                unique = {}
                for recipe in flat:
                    signature = (
                        tuple(sorted(recipe["ingredients"].items())),
                        recipe["result_count"],
                        recipe["requires_table"],
                    )
                    unique.setdefault(signature, recipe)
                self.recipes[result_name] = list(unique.values())

    def best_recipe(self, name, inventory_counts=None):
        """Cheapest usable recipe: fewest total ingredients, no table preferred,
        preferring ingredients already in the inventory."""
        candidates = self.recipes.get(name)
        if not candidates:
            return None
        inventory_counts = inventory_counts or {}

        def score(recipe):
            total = sum(recipe["ingredients"].values())
            missing = sum(
                max(0, count - inventory_counts.get(ing, 0))
                for ing, count in recipe["ingredients"].items()
            )
            return (recipe["requires_table"], missing, total)

        return sorted(candidates, key=score)[0]


class HarvestRequirements:
    """Block -> minimum effective tool, from blocks.json harvestTools."""

    def __init__(self, version=DEFAULT_VERSION):
        items = _load_json(version, "items.json")
        id_to_name = {
            int(row["id"]): str(row.get("name") or row["id"])
            for row in items if isinstance(row, dict) and "id" in row
        }
        self.requirements = {}   # block name -> (tool_family, min_tier)
        self.block_names = set()
        for block in _load_json(version, "blocks.json"):
            if not isinstance(block, dict) or not block.get("name"):
                continue
            self.block_names.add(str(block["name"]))
            tools = block.get("harvestTools") or {}
            best = None
            for tool_id in tools:
                name = id_to_name.get(int(tool_id)) if str(tool_id).isdigit() else None
                if not name:
                    continue
                for family in TOOL_FAMILIES:
                    if name.endswith(f"_{family}"):
                        material = name[: -(len(family) + 1)]
                        tier = TOOL_TIER.get(material)
                        if tier and (best is None or tier < best[1]):
                            best = (family, tier)
                        break
            if best:
                self.requirements[str(block["name"])] = best

    def is_block(self, name):
        return str(name or "") in self.block_names

    def min_tool(self, block_name):
        """(family, tier) needed so the block drops its item, or None."""
        return self.requirements.get(str(block_name or ""))


_recipe_book = None
_harvest = None
_data_lock = threading.Lock()


def recipe_book():
    global _recipe_book
    with _data_lock:
        if _recipe_book is None:
            _recipe_book = RecipeBook()
        return _recipe_book


def harvest_requirements():
    global _harvest
    with _data_lock:
        if _harvest is None:
            _harvest = HarvestRequirements()
        return _harvest


# ---------------------------------------------------------------- planner

class PlanError(Exception):
    """Raised when a goal cannot be planned; message is user-facing."""


class ExpeditionRequired(PlanError):
    """The item's only known sources are expedition-class (structured
    destinations like mansions, raids, the Nether, the End).  Never planned
    implicitly; the message names the source, its habitat and what an
    expedition would need so Doctor can decide (and prepare) deliberately.
    Plan the route anyway by setting the goal's ``allow_expedition`` flag."""

    def __init__(self, item_label, sources):
        self.item_label = item_label
        self.sources = list(sources)
        detail = "；".join(
            f"击杀{MOB_CHINESE.get(src['mob'], src['mob'])}"
            f"（出没于{MOB_HABITAT.get(src['mob'], '远征区域')}）"
            for src in sources
        )
        super().__init__(
            f"{item_label}没有常规获取途径。已知来源需要远征：{detail}。"
            f"需要我先备好武器、食物和盔甲再说「允许远征」吗？"
        )


# Survival arbitration (delivery vs self-preservation): below this health
# Saya will not hand away her last healing potion - the plan suspends and
# re-plans automatically once she has recovered or brewed a spare.
SURVIVAL_HOLD_HEALTH = 10.0
HEALING_POTION_IDS = ("healing", "strong_healing")

# Average drop counts per confirmed kill (Java edition averages, rounded
# pragmatically).  The plan hunts ceil(needed / avg) victims; the step's
# inventory postcondition catches bad luck (retry -> replan hunts more).
# Mob -> {item: average drops per confirmed kill}.  Each mob carries a risk
# class (MOB_RISK) that drives acquisition-source selection:
#   passive    - safe to farm; preferred whenever available
#   hostile    - a fight (zombies, skeletons, ...); planned when no passive
#                source, mining or craft path exists
#   expedition - lives at a structured destination (mansion, raid, Nether,
#                End, ocean monument); NEVER auto-planned - asking for its
#                drops yields an honest ExpeditionRequired report with the
#                habitat and preparation hints instead of suicide steps
MOB_DROPS = {
    # passive
    "cow": {"beef": 1.5, "leather": 1.0},
    "pig": {"porkchop": 2.0},
    "sheep": {"mutton": 1.5, "wool": 1.0},
    "chicken": {"chicken": 1.0, "feather": 1.0},
    "rabbit": {"rabbit": 1.0, "rabbit_hide": 0.5, "rabbit_foot": 0.1},
    "squid": {"ink_sac": 1.0},
    "cod": {"cod": 1.0},
    "salmon": {"salmon": 1.0},
    # hostile
    "zombie": {"rotten_flesh": 1.5},
    "skeleton": {"bone": 1.5, "arrow": 1.0},
    "creeper": {"gunpowder": 1.5},
    "spider": {"string": 1.5},
    "enderman": {"ender_pearl": 0.5},
    "witch": {"glass_bottle": 1.0, "redstone": 1.0, "glowstone_dust": 1.0, "sugar": 1.0},
    "slime": {"slime_ball": 1.0},
    "phantom": {"phantom_membrane": 0.5},
    # expedition
    "evoker": {"totem_of_undying": 1.0, "emerald": 0.5},
    "blaze": {"blaze_rod": 0.5},
    "ghast": {"ghast_tear": 0.5},
    "magma_cube": {"magma_cream": 0.5},
    "guardian": {"prismarine_shard": 1.0, "prismarine_crystal": 0.33},
    "shulker": {"shulker_shell": 0.5},
    "wither_skeleton": {"coal": 0.5, "bone": 0.2, "wither_skeleton_skull": 0.025},
}
MOB_RISK = {
    "cow": "passive", "pig": "passive", "sheep": "passive", "chicken": "passive",
    "rabbit": "passive", "squid": "passive", "cod": "passive", "salmon": "passive",
    "zombie": "hostile", "skeleton": "hostile", "creeper": "hostile",
    "spider": "hostile", "enderman": "hostile", "witch": "hostile",
    "slime": "hostile", "phantom": "hostile",
    "evoker": "expedition", "blaze": "expedition", "ghast": "expedition",
    "magma_cube": "expedition", "guardian": "expedition", "shulker": "expedition",
    "wither_skeleton": "expedition",
}
# Where expedition mobs live - used in the honest "needs an expedition"
# report so Doctor gets an actionable answer instead of a bare failure.
MOB_HABITAT = {
    "evoker": "林地府邸或袭击",
    "blaze": "下界要塞",
    "ghast": "下界",
    "magma_cube": "下界（玄武岩三角洲/下界要塞周边）",
    "guardian": "海底神殿",
    "shulker": "末地城",
    "wither_skeleton": "下界要塞",
}
MOB_CHINESE = {
    "cow": "牛", "pig": "猪", "sheep": "羊", "chicken": "鸡", "rabbit": "兔子",
    "zombie": "僵尸", "skeleton": "骷髅", "creeper": "苦力怕", "spider": "蜘蛛",
    "enderman": "末影人", "witch": "女巫", "squid": "鱿鱼",
    "slime": "史莱姆", "phantom": "幻翼", "cod": "鳕鱼", "salmon": "鲑鱼",
    "evoker": "唤魔者", "blaze": "烈焰人", "ghast": "恶魂",
    "magma_cube": "岩浆怪", "guardian": "守卫者", "shulker": "潜影贝",
    "wither_skeleton": "凋灵骷髅",
}

# item -> mobs carrying it, ordered by risk class then name (passive first,
# expedition last).  Several mobs can share a drop; selection prefers the
# safest carrier.
ITEM_MOBS = {}
for _mob, _drops in MOB_DROPS.items():
    for _item in _drops:
        ITEM_MOBS.setdefault(_item, []).append(_mob)
_RISK_ORDER = {"passive": 0, "hostile": 1, "expedition": 2}
for _item in ITEM_MOBS:
    ITEM_MOBS[_item].sort(key=lambda m: (_RISK_ORDER[MOB_RISK[m]], m))

# Single-carrier view kept for callers that only need the primary mob.
ITEM_FROM_MOB = {item: mobs[0] for item, mobs in ITEM_MOBS.items()}

# Probabilistic / multi-drop block sources the registry map misses (it only
# records single-deterministic-drop blocks).  item -> [(block, avg_drops)]
# with generous break counts computed from the rate; the postcondition
# still gates reality, so bad luck retries instead of faking success.
# Overworld sources only - Nether/End blocks (glowstone) stay with their
# mob/hunt sources.
SPECIAL_BLOCK_DROPS = {
    "wheat_seeds": [("short_grass", 0.125), ("tall_grass", 0.125)],
    "oak_sapling": [("oak_leaves", 0.05)],
    "spruce_sapling": [("spruce_leaves", 0.05)],
    "birch_sapling": [("birch_leaves", 0.05)],
    "flint": [("gravel", 0.1)],
    "clay_ball": [("clay", 4.0)],
    "snowball": [("snow_block", 4.0)],
}
# Prefer the densest source for planning (clay's 4.0 over anything rarer).
SPECIAL_DROP_SOURCE = {
    item: max(sources, key=lambda entry: entry[1])
    for item, sources in SPECIAL_BLOCK_DROPS.items()
}


def _inv_counts(inventory):
    counts = {}
    for entry in inventory or []:
        name = str(entry.get("name") or "")
        count = int(entry.get("count") or 0)
        counts[name] = counts.get(name, 0) + count
        potion = entry.get("potion") or {}
        potion_id = str(potion.get("potion") or "")
        # Variant keys ("potion/healing") let goals, reserves and
        # postconditions address one specific potion identity.
        if potion_id:
            key = f"{name}/{potion_id}"
            counts[key] = counts.get(key, 0) + count
    return counts


def _have(counts, name):
    """Family-aware inventory count for a canonical name/family."""
    if name in ("_log", "planks"):
        return sum(count for item, count in counts.items() if matches_family(item, name))
    return counts.get(name, 0)


def _add(counts, name, amount):
    """Credit produced items (family-aware) during optimistic bookkeeping."""
    if name in ("_log", "planks"):
        for item in list(counts):
            if matches_family(item, name):
                counts[item] = counts.get(item, 0) + amount
                return
        counts[name if name != "planks" else "oak_planks"] = amount
        return
    counts[name] = counts.get(name, 0) + amount


def _deduct(counts, name, amount):
    """Remove consumed items (family-aware) during optimistic bookkeeping."""
    if name in ("_log", "planks"):
        remaining = amount
        for item in sorted(counts):
            if remaining <= 0:
                break
            if matches_family(item, name):
                take = min(counts[item], remaining)
                counts[item] -= take
                remaining -= take
        return
    counts[name] = max(0, counts.get(name, 0) - amount)


def _concretize(name, counts):
    """Turn a family canonical (planks/_log) into a concrete item name."""
    if name == "planks":
        for item in counts:
            if item.endswith("_planks"):
                return item
        for log in counts:
            if matches_family(log, "_log"):
                return log.replace("_log", "_planks").replace("_stem", "_planks") \
                          .replace("_wood", "_planks").replace("_hyphae", "_planks")
        return "oak_planks"
    if name == "_log":
        for item in counts:
            if matches_family(item, "_log"):
                return item
        return "oak_log"
    return name


def describe_goal(goal):
    goal = goal or {}
    kind = goal.get("type") or "obtain"
    target = goal.get("target") or ""
    count = goal.get("count") or 1
    recipient = goal.get("recipient") or "Doctor"
    catalog = item_catalog()
    if target and "/" in str(target):
        label = potions.potion_chinese(target)
    else:
        label = catalog.chinese(str(target)) if target else "目标"
    if kind == "obtain_and_deliver":
        return f"准备{count}个{label}并交给{recipient}"
    if kind == "obtain":
        return f"准备{count}个{label}"
    if kind == "craft":
        return f"制作{count}个{label}"
    if kind == "travel":
        return f"前往{target or recipient}"
    if kind == "combat":
        return f"击杀{count}个{target or '附近敌人'}"
    return str(goal)


def plan_goal(goal, state):
    """Build a step list with pre/postconditions for a high-level goal.

    ``state`` is the mineflayer service status dict (runtime inventory,
    stations, held item).  Raises PlanError with a user-facing message when
    the goal is impossible right now.
    """
    goal = dict(goal or {})
    kind = str(goal.get("type") or "obtain")
    if kind not in ("obtain", "craft", "obtain_and_deliver", "travel", "combat"):
        raise PlanError(f"未知的目标类型 {kind}")
    runtime = (state or {}).get("runtime") or {}
    counts = _inv_counts(runtime.get("inventory"))
    station_state = runtime.get("stations") or {}
    stations = station_state.get("crafting_tables") or []
    furnaces = station_state.get("furnaces") or []
    steps = []
    # Items whose craft branch is currently being expanded (the recursion
    # chain) - a recipe re-entering this set is a compression loop.
    _chain = set()
    book = recipe_book()
    harvest = harvest_requirements()
    catalog = item_catalog()

    if kind == "travel":
        target = str(goal.get("target") or "")
        coords = goal.get("coords")
        if isinstance(coords, (list, tuple)) and len(coords) == 3:
            steps.append(step("goto", f"goto {int(coords[0])} {int(coords[1])} {int(coords[2])}",
                              postcondition=f"position_near:{coords}", description=f"前往坐标 {list(coords)}"))
        else:
            player = target or str(goal.get("recipient") or "Doctor")
            steps.append(step("come", f"come {player}", postcondition=f"near:{player}",
                              description=f"走到 {player} 身边"))
        return new_plan(goal, steps)

    if kind == "combat":
        target = str(goal.get("target") or "nearest")
        count = max(1, min(32, int(goal.get("count") or 1)))
        steps.append(step(
            "attack", f"attack {target} {count}",
            postcondition=f"kills:{target}>={count}",
            description=f"确认击杀 {count} 个 {target}",
        ))
        return new_plan(goal, steps)

    target = str(goal.get("target") or "")
    if not target:
        raise PlanError("缺少目标物品")
    # Variant-aware potion targets bypass the item catalog: their identity
    # lives in the potion registry, not in items.json.
    potion_target = potions.parse_potion_target(target)
    count = max(1, min(640, int(goal.get("count") or 1)))
    recipient = str(goal.get("recipient") or "Doctor")
    # Expedition-class acquisition sources (evoker mansions/raids, Nether,
    # End, ocean monuments) are only planned when the goal explicitly allows
    # them; otherwise the planner answers with an honest ExpeditionRequired
    # report instead of emitting suicide attack steps.
    allow_expedition = bool(goal.get("allow_expedition"))
    if potion_target is None:
        requested_canonical = catalog.lookup(target) or target
        # Natural requests name the block being mined (iron_ore), while survival
        # inventory receives its normal drop (raw_iron).  Plan and verify the
        # inventory result, retaining the source block for the collect command.
        canonical = (
            ITEM_FROM_BLOCK.get(requested_canonical, requested_canonical)
            if harvest.is_block(requested_canonical)
            else requested_canonical
        )
        if canonical not in ("_log", "planks") and canonical not in catalog.items and not harvest.is_block(canonical):
            raise PlanError(f"不认识的物品：{target}")
        # Wool is a family item like planks: any colour satisfies the goal, and
        # the recipe book only knows dye conversions for the coloured variants
        # (white_wool <- black_wool + white_dye recurses into dye chains).  The
        # survival-practical source is shearing/killing sheep.
        if canonical == "wool" or canonical.endswith("_wool"):
            canonical = "wool"

    def merge_or_append(new_step, merge_key=None):
        """Append a step, merging with the previous one when it is the same
        action+item (e.g. two 'collect oak_log 1' become one 'collect oak_log 2')."""
        key = merge_key or (new_step["action"], new_step["command"].rsplit(" ", 1)[0])
        if steps:
            prev = steps[-1]
            prev_key = (prev["action"], prev["command"].rsplit(" ", 1)[0])
            if prev_key == key and prev["action"] in ("collect", "craft"):
                try:
                    prev_count = int(prev["command"].rsplit(" ", 1)[1])
                    add_count = int(new_step["command"].rsplit(" ", 1)[1])
                except (IndexError, ValueError):
                    pass
                else:
                    prev["command"] = f"{new_step['command'].rsplit(' ', 1)[0]} {prev_count + add_count}"
                    prev["postcondition"] = new_step["postcondition"]
                    prev["description"] = new_step["description"]
                    for name, count in (new_step.get("reserves") or {}).items():
                        prev["reserves"][name] = prev["reserves"].get(name, 0) + count
                    return
        steps.append(new_step)

    def ensure_item(name, needed, depth=0):
        nonlocal table_ok, furnace_ok
        if depth > 8:
            raise PlanError(f"{target} 的配方链太深，无法规划")
        have = _have(counts, name)
        if have >= needed:
            return
        missing = needed - have
        concrete = _concretize(name, counts)

        if name == "_log" or (matches_family(concrete, "_log") and concrete not in book.recipes):
            # Logs come from trees, not crafting.  Collect ANY log family
            # member (worker matchesBlock('_log') accepts every log/stem) so
            # whatever forest is nearby works - never lock onto oak.
            merge_or_append(step(
                "collect", f"collect _log {missing}",
                reserves={"_log": missing},
                postcondition=f"inventory:{name}>={needed}",
                description=f"采集 {missing} 个原木（任意木材）",
            ))
            _add(counts, name, missing)
            return

        # Enumerate every acquisition source this item has, in a global
        # cost/risk order, then execute the FIRST eligible one.  Ranks:
        #   0 smelt   - renewable, input chain planned recursively
        #   1 passive mob - safe hunting
        #   2 stable natural block - ores/stone/clay: what mining requests mean
        #   3 craft   - recipe book (inputs planned recursively)
        #   4 hostile mob - a fight, only when nothing cheaper exists
        #   5 mine fallback - ordinary blocks (cobblestone ...)
        #   6 expedition mob - NEVER implicit: deferred and reported honestly
        #     (ExpeditionRequired) unless the goal set allow_expedition.
        # This fixes single-table blind spots of the old fixed if-chain:
        # redstone now mines instead of hunting witches, coal mines instead
        # of expeditioning to wither skeletons, glass bottles craft.
        candidates = []
        smelt_input = SMELTING_RECIPES.get(concrete)
        if smelt_input:
            candidates.append((0, {"kind": "smelt", "input": smelt_input}))
        for mob in ITEM_MOBS.get(concrete, []):
            if MOB_RISK[mob] == "passive":
                candidates.append((1, {"kind": "mob", "mob": mob, "avg": MOB_DROPS[mob][concrete],
                                       "risk": "passive"}))
        source_block = BLOCK_FOR_ITEM.get(concrete)
        if source_block and source_block != concrete and (
            source_block.endswith("_ore") or source_block in {"stone", "deepslate", "clay"}
        ):
            candidates.append((2, {"kind": "mine", "block": source_block}))
        special = SPECIAL_DROP_SOURCE.get(concrete)
        if special and harvest.is_block(special[0]):
            candidates.append((2.5, {"kind": "mine", "block": special[0], "per": special[1]}))
        # A craft recipe whose ingredients re-enter the chain currently being
        # planned is a compression loop (slime_block <-> slime_ball); the mob
        # source must win instead of recursing to the depth guard.
        recipe = book.best_recipe(concrete, counts)
        if recipe and not (_chain & set(recipe["ingredients"])):
            candidates.append((3, {"kind": "craft", "recipe": recipe}))
        for mob in ITEM_MOBS.get(concrete, []):
            if MOB_RISK[mob] == "hostile":
                candidates.append((4, {"kind": "mob", "mob": mob, "avg": MOB_DROPS[mob][concrete],
                                       "risk": "hostile"}))
        fallback_block = BLOCK_FOR_ITEM.get(concrete, concrete)
        if harvest.is_block(fallback_block):
            candidates.append((5, {"kind": "mine", "block": fallback_block}))
        for mob in ITEM_MOBS.get(concrete, []):
            if MOB_RISK[mob] == "expedition":
                candidates.append((6, {"kind": "mob", "mob": mob, "avg": MOB_DROPS[mob][concrete],
                                       "risk": "expedition"}))
        candidates.sort(key=lambda entry: entry[0])

        eligible = []
        deferred_expedition = []
        for _, candidate in candidates:
            if candidate["kind"] == "mob" and candidate["risk"] == "expedition" and not allow_expedition:
                deferred_expedition.append(candidate)
                continue
            eligible.append(candidate)
        if not eligible:
            if deferred_expedition:
                raise ExpeditionRequired(catalog.chinese(concrete), deferred_expedition)
            raise PlanError(f"没有可用的 {catalog.chinese(concrete)} 配方，也不知道去哪里获取")

        def execute_source(source):
            kind = source["kind"]
            if kind == "smelt":
                smelt_input = source["input"]
                ensure_furnace(smelt_input)
                remaining = missing
                while remaining > 0:
                    batch = min(8, remaining)
                    ensure_item(smelt_input, batch, depth + 1)
                    _deduct(counts, smelt_input, batch)
                    fuel_name, fuel_count = choose_fuel(batch, depth + 1)
                    expected_after = _have(counts, concrete) + batch
                    steps.append(step(
                        "smelt",
                        f"smelt {smelt_input} {concrete} {batch} {fuel_name} {fuel_count}",
                        reserves={smelt_input: batch, fuel_name: fuel_count},
                        postcondition=f"inventory:{concrete}>={expected_after}",
                        description=f"用熔炉烧炼 {batch} 个{catalog.chinese(concrete)}",
                    ))
                    _add(counts, concrete, batch)
                    remaining -= batch
                return

            if kind == "mob":
                # Hunt the carrier, pick up the drops, verify the inventory
                # result.  Bad drop luck retries the step, so bias the kill
                # count slightly high - over-hunting one extra animal is
                # cheaper than a failed plan.
                mob = source["mob"]
                average = source["avg"]
                expedition = source["risk"] == "expedition"
                hunts = max(1, math.ceil(missing / average)) if average >= 1 else max(1, math.ceil(missing * 2))
                label = MOB_CHINESE.get(mob, mob)
                hunt_verb = "远征狩猎" if expedition else "狩猎"
                steps.append(step(
                    "attack", f"attack {mob} {hunts}",
                    description=f"{hunt_verb} {hunts} 只{label}（{catalog.chinese(concrete)}来源）",
                ))
                steps.append(step(
                    "pickup", "pickup nearest",
                    postcondition=f"inventory:{concrete}>={needed}",
                    description=f"拾取{label}掉落的{catalog.chinese(concrete)}并核对数量",
                ))
                _add(counts, concrete, max(missing, math.ceil(hunts * average)))
                return

            if kind == "mine":
                ensure_tool_for(source["block"])
                # Probabilistic drops (seeds 12.5%, flint 10%, ...) need more
                # broken blocks than wanted items; bias high, cap sane - the
                # postcondition retries on bad luck anyway.
                per = float(source.get("per") or 1.0)
                blocks = missing if per >= 1 else min(128, math.ceil(missing / per * 1.4))
                merge_or_append(step(
                    "collect", f"collect {source['block']} {blocks}",
                    reserves={concrete: missing},
                    postcondition=f"inventory:{concrete}>={needed}",
                    description=f"采集 {blocks} 个{catalog.chinese(source['block'])}"
                                f"（{catalog.chinese(concrete)}来源）",
                ))
                _add(counts, concrete, missing)
                return

            # kind == "craft"
            recipe = source["recipe"]
            crafts = max(1, -(-missing // recipe["result_count"]))
            produces = crafts * recipe["result_count"]
            reserves = {}
            if recipe["requires_table"]:
                ensure_table()
            _chain.add(concrete)
            try:
                for ingredient, per_craft in recipe["ingredients"].items():
                    total = per_craft * crafts
                    reserves[ingredient] = reserves.get(ingredient, 0) + total
                    ensure_item(ingredient, total, depth + 1)
                    _deduct(counts, ingredient, total)
            finally:
                _chain.discard(concrete)
            # Planks are family-canonical: emit `craft planks N` and let the
            # worker resolve whichever planks type matches the logs collected
            # (the worker picks the recipe variant its inventory satisfies).
            family_name = "planks" if matches_family(concrete, "planks") else concrete
            expected_after = _have(counts, family_name) + produces
            merge_or_append(step(
                "craft", f"craft {family_name} {produces}",
                reserves=reserves,
                postcondition=f"inventory:{family_name}>={expected_after}",
                description=(
                    f"制作 {produces} 个木板（任意木材）"
                    if family_name == "planks"
                    else f"制作 {produces} 个{catalog.chinese(concrete)}"
                ),
            ))
            _add(counts, family_name, produces)

        def mine_reenters_chain(block):
            # Mining a manufactured block that is crafted from the very item
            # this recursion is sourcing is the compression loop in disguise
            # (slime_block <- 9x slime_ball): it plans "successfully" but the
            # block only exists player-crafted.  Reject it so the next source
            # in cost order (e.g. hunting the slime) gets its turn.
            block = str(block)
            block_recipe = book.best_recipe(block)
            return bool(block_recipe) and bool(set(block_recipe.get("ingredients") or {}) & _chain)

        failures = []
        for source in eligible:
            if source["kind"] == "mine" and mine_reenters_chain(source["block"]):
                failures.append("采集该来源会回环依赖当前物品")
                continue
            steps_snapshot = json.loads(json.dumps(steps))
            counts_snapshot = dict(counts)
            table_backup = table_ok
            furnace_backup = furnace_ok
            try:
                execute_source(source)
                return
            except PlanError as exc:
                # The route dead-ended mid-plan (missing recipe, unobtainable
                # input, ...).  Undo its partial steps/reservations and hand
                # over to the next source in cost order.
                steps[:] = steps_snapshot
                counts.clear()
                counts.update(counts_snapshot)
                table_ok = table_backup
                furnace_ok = furnace_backup
                failures.append(f"{exc}")
        if deferred_expedition:
            raise ExpeditionRequired(catalog.chinese(concrete), deferred_expedition)
        detail = "；".join(failures[:3]) or "没有可用来源"
        raise PlanError(f"{catalog.chinese(concrete)} 的所有获取途径都无法规划：{detail}")

    table_ok = bool(stations)
    # Older state fixtures did not include a station name; treat those as an
    # ordinary furnace for backward compatibility. Special furnaces are only
    # accepted for recipes they actually support.
    furnace_names = {str(item.get("name") or "furnace") for item in furnaces}
    furnace_ok = "furnace" in furnace_names

    def ensure_table():
        nonlocal table_ok
        if table_ok:
            return  # nearby station, or one already ensured in this plan
        if _have(counts, "crafting_table") >= 1:
            steps.append(step("place", "place crafting_table",
                              postcondition="block:crafting_table",
                              description="放置工作台"))
            _deduct(counts, "crafting_table", 1)
            table_ok = True
            return
        ensure_item("crafting_table", 1)
        steps.append(step("place", "place crafting_table",
                          postcondition="block:crafting_table",
                          description="放置工作台"))
        _deduct(counts, "crafting_table", 1)
        table_ok = True

    def ensure_furnace(input_name=""):
        nonlocal furnace_ok
        compatible = {"furnace"}
        if input_name in BLAST_FURNACE_INPUTS:
            compatible.add("blast_furnace")
        if input_name in SMOKER_INPUTS:
            compatible.add("smoker")
        if furnace_ok or furnace_names.intersection(compatible):
            return
        if _have(counts, "furnace") < 1:
            ensure_item("furnace", 1)
        steps.append(step(
            "place", "place furnace", postcondition="block:furnace",
            description="放置熔炉",
        ))
        _deduct(counts, "furnace", 1)
        furnace_ok = True
        furnace_names.add("furnace")

    def choose_fuel(items_to_smelt, depth):
        for fuel_name in ("coal_block", "dried_kelp_block", "blaze_rod", "coal", "charcoal", "planks", "_log", "stick"):
            capacity = FUEL_CAPACITY[fuel_name]
            required = max(1, math.ceil(items_to_smelt / capacity))
            if _have(counts, fuel_name) >= required:
                _deduct(counts, fuel_name, required)
                return fuel_name, required
        # Logs are the universal survival fallback and are already handled as
        # an interchangeable family by the planner and worker.
        required = max(1, math.ceil(items_to_smelt / FUEL_CAPACITY["_log"]))
        ensure_item("_log", required, depth + 1)
        _deduct(counts, "_log", required)
        return "_log", required

    def ensure_tool_for(block_name):
        need = harvest.min_tool(block_name)
        if not need:
            return
        family, tier = need
        # Do we already carry a sufficient tool (any material of tier >= tier)?
        owned = [
            item for item in counts
            if item.endswith(f"_{family}")
            and TOOL_TIER.get(item[: -(len(family) + 1)], 0) >= tier
        ]
        if owned:
            return
        material = TIER_MATERIAL.get(tier, "wooden")
        tool = f"{material}_{family}"
        ensure_item(tool, 1)
        steps.append(step("equip", f"equip {tool}",
                          postcondition=f"held:{tool}",
                          description=f"手持{catalog.chinese(tool)}"))

    def plan_potion():
        """Brew-chain planning for variant potion targets.

        Phase 1 scope (mirrors docs/minecraft-brewing-cauldron-handoff):
        - existing FINAL potions count toward the goal and are delivered
          without brewing anything;
        - brewing materials are checked against the current inventory only -
          missing ones are reported as a list instead of planning an
          open-ended mining chain (nether materials are not stably
          collectible from the overworld anyway);
        - existing intermediate stages (e.g. awkward) are NOT credited yet:
          the worker's brew command always runs the full chain from water
          bottles, so crediting them would under-collect materials.
        """
        container = potion_target["container"]
        potion_id = potion_target["potion"]
        label = potions.potion_chinese(container, potion_id)
        if potion_id not in potions.VANILLA_POTION_IDS:
            raise PlanError(f"自定义药水 {potion_id} 暂不支持自动处理")
        have_target = _have(counts, f"{container}/{potion_id}")
        needed = max(0, count - have_target)
        if needed > 0:
            if container != "potion":
                raise PlanError(f"{label}我只会交付现有的，新的还酿不了")
            if not (station_state.get("brewing_stands") or []):
                raise PlanError("附近没有酿造台：放一个或带我找一个，我就能开始酿")
            try:
                chain = potions.brew_chain(potion_id)
            except potions.UnknownPotionError as exc:
                raise PlanError(f"{label}我暂时酿不了：{exc}")
            needs = {}
            for _out, ingredient, _in in chain:
                needs[ingredient] = needs.get(ingredient, 0) + needed
            needs["water_bottle"] = needs.get("water_bottle", 0) + needed
            needs["blaze_powder"] = needs.get("blaze_powder", 0) + max(1, math.ceil(needed / 20))
            missing = []
            for material, amount in needs.items():
                if _have(counts, material) >= amount:
                    _deduct(counts, material, amount)
                else:
                    shortfall = amount - _have(counts, material)
                    missing.append(f"{potions.ingredient_chinese(material)}×{shortfall}")
            if missing:
                raise PlanError(
                    "材料不齐，还缺：" + "、".join(missing) + "。备齐后再说一声，我马上开始酿。"
                )
            produced = have_target
            left = needed
            while left > 0:
                batch = min(3, left)
                produced += batch
                steps.append(step(
                    "brew", f"brew {potion_id} {batch}",
                    reserves=dict(needs),
                    postcondition=f"inventory:potion/{potion_id}>={produced}",
                    description=f"酿造 {batch} 瓶{label}",
                ))
                _add(counts, f"potion/{potion_id}", batch)
                left -= batch
        if kind == "obtain_and_deliver":
            steps.append(step(
                "give", f"give {container}/{potion_id} {count} to {recipient}",
                postcondition=f"delivered:{container}/{potion_id}",
                description=f"把 {count} 瓶{label}交给 {recipient}",
            ))

    # Tool preparation happens inside ensure_item's collect branch, so both
    # direct mining goals ("挖点石头") and crafted tools whose ingredients need
    # mining (stone_pickaxe needs cobblestone) get the right tool chain.
    if potion_target is not None:
        plan_potion()
    elif canonical == "milk_bucket":
        # Mechanism knowledge: milking consumes one bucket per milk_bucket.
        missing_milk = max(0, count - _have(counts, "milk_bucket"))
        if missing_milk > 0:
            ensure_item("bucket", missing_milk)
            _deduct(counts, "bucket", missing_milk)
            steps.append(step(
                "milk", f"milk {missing_milk}",
                reserves={"bucket": missing_milk},
                postcondition=f"inventory:milk_bucket>={count}",
                description=f"给附近的奶牛挤 {missing_milk} 桶牛奶",
            ))
            _add(counts, "milk_bucket", missing_milk)
    else:
        ensure_item(canonical, count)

    if kind == "obtain_and_deliver" and potion_target is None:
        concrete = _concretize(canonical, counts)
        steps.append(step("give", f"give {concrete} {count} to {recipient}",
                          postcondition=f"delivered:{concrete}",
                          description=f"把 {count} 个{catalog.chinese(concrete)}交给 {recipient}"))

    if not steps:
        check_target = (
            f"{potion_target['container']}/{potion_target['potion']}"
            if potion_target is not None else canonical
        )
        steps.append(step("inventory", "inventory",
                          postcondition=f"inventory:{check_target}>={count}",
                          description="核对背包"))
    return new_plan(goal, steps)


def step(action, command, *, postcondition="none", description="", reserves=None):
    return {
        "id": f"s{uuid.uuid4().hex[:6]}",
        "action": action,
        "command": command,
        "postcondition": postcondition,
        "description": description or command,
        "reserves": reserves or {},
        "attempts": 0,
        "state": "pending",
    }


def new_plan(goal, steps):
    return {
        "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
        "goal": goal,
        "goal_description": describe_goal(goal),
        "steps": steps,
        "current_step": 0,
        "status": "pending",
        "will_deliver": str((goal or {}).get("type") or "") == "obtain_and_deliver",
        "waiting_for_doctor": None,
        "last_error": "",
        "replans_used": 0,
        "created_at": time.time(),
        "updated_at": time.time(),
    }


# ---------------------------------------------------------------- executor

class PlanExecutor:
    """Runs one plan step at a time against the live Mineflayer worker."""

    def __init__(self):
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self._plan = None
        self._queue = []
        self._suspended = []
        self._stop_flag = False
        self._last_craft = None
        self._history = []
        self._last_suspend_check = 0.0

    # -- lifecycle ------------------------------------------------------------
    def submit(self, plan):
        with self._lock:
            if self._plan and self._plan.get("status") == "running":
                self._queue.append(plan)
            else:
                self._plan = plan
            self._wake.set()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._loop, name="minecraft-plan", daemon=True
            )
            self._thread.start()
        return True

    def cancel(self, reason="doctor"):
        with self._lock:
            plan = self._plan
            self._queue.clear()
            if plan and plan.get("status") in ("pending", "running"):
                plan["status"] = "cancelled"
                plan["last_error"] = str(reason)
                plan["updated_at"] = time.time()
                self._finish_plan(plan)
        return True

    def _loop(self):
        while True:
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            now = time.time()
            if now - self._last_suspend_check >= SUSPEND_CHECK_INTERVAL_S:
                self._last_suspend_check = now
                try:
                    self._check_suspended()
                except Exception:
                    pass
            with self._lock:
                if self._plan and self._plan.get("status") in ("pending",):
                    plan = self._plan
                else:
                    plan = self._queue.pop(0) if self._queue else None
                    self._plan = plan
            if plan and plan.get("status") in ("pending", "running"):
                try:
                    self._run_plan(plan)
                except Exception as exc:
                    # a dead thread would freeze the plan in "running"
                    # forever - fail it loudly instead of hanging the UI.
                    plan["status"] = "failed"
                    plan["last_error"] = f"执行器异常：{exc}"[:200]
                    plan["updated_at"] = time.time()
                    with self._lock:
                        if self._plan is plan:
                            self._plan = None
                        self._push_history(plan)
                    self._log_plan(plan["last_error"])
                    self._set_plan_active(False)
                with self._lock:
                    if self._plan is plan:
                        if self._queue:
                            self._plan = self._queue.pop(0)
                            self._wake.set()
                        else:
                            self._plan = None

    def _recover_dead_thread(self):
        """Safety net: if the executor thread died without cleanup, finish
        the stuck plan so the UI never shows a phantom running task."""
        with self._lock:
            plan = self._plan
            dead = bool(
                plan
                and plan.get("status") in ("pending", "running")
                and (self._thread is None or not self._thread.is_alive())
            )
        if not dead:
            return
        plan["status"] = "failed"
        plan["last_error"] = "执行器线程中断，任务已结束"
        plan["updated_at"] = time.time()
        with self._lock:
            if self._plan is plan:
                self._plan = None
            self._push_history(plan)
        self._log_plan(plan["last_error"])
        self._set_plan_active(False)

    def suspend_active(self, reason=""):
        """Doctor suspends the currently running plan (stays resumable)."""
        with self._lock:
            plan = self._plan
        if not plan or plan.get("status") not in ("pending", "running"):
            return False
        self._suspend_plan(plan, f"主人手动挂起：{reason}" if reason else "主人手动挂起")
        return True

    # -- suspension -------------------------------------------------------------
    def _suspend_plan(self, plan, reason):
        """Park a plan that cannot run right now (never auto-fail it)."""
        from .minecraft_mineflayer import minecraft_bot_service

        plan["status"] = "suspended"
        plan["suspend_reason"] = str(reason)[:160]
        plan["last_error"] = plan["suspend_reason"]
        plan["suspend_at"] = time.time()
        plan["updated_at"] = time.time()
        self._set_plan_active(False)
        position = ((minecraft_bot_service.status().get("runtime") or {}).get("position"))
        if position:
            plan["checked_pos"] = [position.get("x"), position.get("y"), position.get("z")]
        with self._lock:
            self._suspended.append(plan)
            while len(self._suspended) > MAX_SUSPENDED:
                oldest = self._suspended.pop(0)
                oldest["status"] = "failed"
                oldest["last_error"] = "挂起队列已满，自动放弃"
                self._push_history(oldest)
            self._push_history(plan)
        self._log_plan(f"任务挂起：{plan['goal_description']}（{plan['suspend_reason']}）")
        try:
            minecraft_bot_service._speak(
                f"{plan['goal_description']}：{plan['suspend_reason']}。"
                "我先挂起了，情况变化后会自动重试，你也可以在控制台放弃它。"
            )
        except Exception:
            pass

    def _check_suspended(self):
        with self._lock:
            if self._plan or self._queue or not self._suspended:
                return
            plan = self._suspended[0]
        from .minecraft_mineflayer import minecraft_bot_service

        position = ((minecraft_bot_service.status().get("runtime") or {}).get("position"))
        if not position:
            return
        current = [position.get("x"), position.get("y"), position.get("z")]
        checked = plan.get("checked_pos")
        moved = True
        if checked and all(isinstance(v, (int, float)) for v in checked):
            moved = math.dist(checked, current) >= RESUME_MOVE_DISTANCE
        if moved:
            self.resume_suspended(plan["plan_id"])

    def resume_suspended(self, plan_id=None):
        """Re-plan a suspended goal against the live state and run it again."""
        from .minecraft_mineflayer import minecraft_bot_service

        with self._lock:
            plan = None
            if plan_id:
                plan = next((p for p in self._suspended if p["plan_id"] == str(plan_id)), None)
            elif self._suspended:
                plan = self._suspended[0]
            if not plan:
                return False
            self._suspended.remove(plan)
        try:
            fresh = plan_goal(plan["goal"], minecraft_bot_service.status())
        except PlanError as exc:
            with self._lock:
                plan["status"] = "failed"
                plan["last_error"] = str(exc)
                plan["updated_at"] = time.time()
                self._push_history(plan)
            self._log_plan(f"挂起任务无法恢复：{plan['goal_description']}（{exc}）")
            return False
        plan["steps"] = fresh["steps"]
        plan["current_step"] = 0
        plan["status"] = "pending"
        plan["replans_used"] = 0
        plan["last_error"] = ""
        plan["updated_at"] = time.time()
        # Goal already satisfied while suspended (e.g. a cancelled delivery
        # step had actually tossed the items, or the doctor stocked them):
        # complete the plan directly instead of re-running pointless steps -
        # the suspended entry must never outlive its own outcome.
        if len(fresh["steps"]) == 1 and fresh["steps"][0]["action"] == "inventory":
            plan["status"] = "completed"
            self._finish_plan(plan)
            self._log_plan(f"挂起任务的目标已达成：{plan['goal_description']}")
            return True
        self._log_plan(f"重新尝试挂起任务：{plan['goal_description']}")
        self.submit(plan)
        return True

    def abandon_suspended(self, plan_id=None):
        """Doctor gives up on one (or all) suspended plan(s)."""
        with self._lock:
            if plan_id:
                plan = next((p for p in self._suspended if p["plan_id"] == str(plan_id)), None)
                if not plan:
                    return False
                targets = [plan]
                self._suspended.remove(plan)
            else:
                targets = list(self._suspended)
                self._suspended.clear()
            for plan in targets:
                plan["status"] = "cancelled"
                plan["last_error"] = "主人放弃了这个任务"
                plan["updated_at"] = time.time()
                self._push_history(plan)
        for plan in targets:
            self._log_plan(f"放弃挂起任务：{plan['goal_description']}")
        return bool(targets)

    def _log_plan(self, text):
        try:
            from .minecraft_mineflayer import minecraft_bot_service

            minecraft_bot_service._log(text)
        except Exception:
            pass

    # -- step engine ------------------------------------------------------------
    def _set_plan_active(self, active):
        """Tell the worker a plan is in flight (follow/hunt stay deferred).

        Sent at plan start, re-asserted before every step (worker restarts
        clear the flag), and cleared on every terminal state.
        """
        try:
            from .minecraft_mineflayer import minecraft_bot_service

            minecraft_bot_service.send_plan_state(active)
        except Exception:
            pass

    def _run_plan(self, plan):
        from .minecraft_mineflayer import minecraft_bot_service

        plan["status"] = "running"
        plan["updated_at"] = time.time()
        self._set_plan_active(True)
        index = int(plan.get("current_step") or 0)
        while index < len(plan["steps"]):
            step_entry = plan["steps"][index]
            if plan.get("status") != "running":
                return
            # Survival arbitration before any delivery step: never hand away
            # the last healing potion while critically wounded - defer the
            # delivery by suspending the plan (auto-replanned on resume).
            if step_entry["action"] == "give":
                hold_reason = self._survival_delivery_hold(step_entry)
                if hold_reason:
                    self._suspend_plan(plan, hold_reason)
                    return
            outcome, message = self._execute_step(step_entry, plan)
            # cancel() records the plan immediately while the worker command
            # is being interrupted.  Do not append a second history/report
            # when that cancelled activity arrives.
            if plan.get("status") == "cancelled":
                return
            if outcome == "cancelled":
                plan["status"] = "cancelled"
                plan["last_error"] = f"步骤被取消：{message}"
                plan["updated_at"] = time.time()
                self._finish_plan(plan)
                return
            # Plan section 5: a step advances only when the worker finished it
            # AND the postcondition still holds against the live state; a
            # "completed" activity whose inventory check fails gets a short
            # grace window first (state snapshots lag real actions), then it
            # is a retry.
            verified = False
            if outcome == "completed":
                verified = self._verify(step_entry, minecraft_bot_service)
                if not verified:
                    grace_deadline = time.time() + VERIFY_GRACE_S
                    while time.time() < grace_deadline:
                        time.sleep(STEP_POLL_S)
                        if self._verify(step_entry, minecraft_bot_service):
                            verified = True
                            break
            if verified:
                step_entry["state"] = "completed"
                index += 1
                plan["current_step"] = index
                plan["updated_at"] = time.time()
                if step_entry["action"] == "craft":
                    item = step_entry["command"].split()[1] if len(step_entry["command"].split()) > 1 else ""
                    if item:
                        self._last_craft = {"name": item, "at": time.time()}
                continue
            if outcome != "completed":
                message = message or "步骤失败"
            else:
                message = "后置条件未满足（背包/世界状态与预期不符）"
            step_entry["attempts"] += 1
            if step_entry["attempts"] < MAX_STEP_ATTEMPTS:
                step_entry["state"] = "retrying"
                plan["last_error"] = f"{step_entry['description']}失败：{message}，重试一次"
                plan["updated_at"] = time.time()
                continue
            # One replan from the current live state; if it still cannot run,
            # SUSPEND the plan instead of failing - the executor re-checks it
            # periodically and resumes when conditions change.
            if plan["replans_used"] < MAX_REPLANS and self._replan(plan, index):
                index = int(plan.get("current_step") or 0)
                continue
            self._suspend_plan(plan, f"{step_entry['description']}暂时做不了：{message}")
            return
        plan["status"] = "completed"
        plan["updated_at"] = time.time()
        self._finish_plan(plan)

    def _survival_delivery_hold(self, step_entry):
        """Defer-and-rebrew arbitration for healing potion deliveries.

        Returns a suspend reason when Saya is critically wounded and the
        step would hand away her LAST healing potion; "" otherwise.  The
        suspended plan re-plans on resume: recovered health or a freshly
        brewed spare bottle both unblock the delivery, and only the doctor
        can force the handover before that.
        """
        parts = str(step_entry.get("command") or "").split()
        if len(parts) < 2 or parts[0] != "give":
            return ""
        potion = potions.parse_variant_key(parts[1])
        if not potion or potion["potion"] not in HEALING_POTION_IDS:
            return ""
        from .minecraft_mineflayer import minecraft_bot_service

        runtime = minecraft_bot_service.status().get("runtime") or {}
        try:
            health = float((runtime.get("vitals") or {}).get("health") or 20)
        except (TypeError, ValueError):
            health = 20.0
        if health > SURVIVAL_HOLD_HEALTH:
            return ""
        try:
            requested = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        except ValueError:
            requested = 1
        carrying = item_catalog().inventory_count(
            runtime.get("inventory"), potion["key"]
        )
        if carrying - requested > 0:
            return ""  # 交出之后自己还有剩，不构成保命储备
        label = potions.potion_chinese(potion["key"])
        return (
            f"这瓶{label}现在是我的保命储备（血量只有 {health:g}），先不交。"
            "等我脱离危险或再酿出一瓶就马上给你；急用的话再对我说一次，我硬交。"
        )

    def _execute_step(self, step_entry, plan):
        from .minecraft_mineflayer import minecraft_bot_service

        # Capture the activity id BEFORE sending: the worker starts the new
        # foreground task (and may finish it) as soon as the pipe delivers it.
        pre_id = ((minecraft_bot_service.status().get("activity") or {}).get("id"))
        # Task envelope: one lifecycle id per step, derived from the plan id
        # so worker activities, replies and cancellation share the lineage.
        step_task_id = f"{plan['plan_id']}-{step_entry['id']}"
        # Re-assert the plan flag per step: worker restarts clear it, and the
        # between-steps gap must never look idle to follow/hunt automation.
        self._set_plan_active(True)
        sent = minecraft_bot_service.send_command(
            step_entry["command"], origin="plan", quiet=True,
            task_id=step_task_id,
        )
        if not sent:
            return "failed", "worker 管道不可用"
        step_entry["state"] = "running"
        plan["updated_at"] = time.time()
        print(f"[plan {plan['plan_id'][-8:]}] step {step_entry['id']} start: "
              f"{step_entry['command']}")
        started_at = time.time()
        deadline = started_at + PLAN_STEP_TIMEOUT_S
        while time.time() < deadline:
            if plan.get("status") != "running":
                return "cancelled", "计划已取消"
            time.sleep(STEP_POLL_S)
            activity = minecraft_bot_service.status().get("activity") or {}
            if activity.get("id") == pre_id:
                # The command did not start a new foreground activity (e.g.
                # refused during an emergency rescue); fail fast instead of
                # waiting for the full step timeout.
                if time.time() - started_at > 15:
                    return "failed", "worker 没有开始该步骤（可能正在紧急自救或未连接）"
                continue
            if activity.get("state") == "running":
                continue
            state = str(activity.get("state") or "")
            message = str(activity.get("message") or "")
            if state == "completed":
                print(f"[plan {plan['plan_id'][-8:]}] step {step_entry['id']} "
                      f"completed in {time.time() - started_at:.1f}s")
                return "completed", message
            if state == "cancelled":
                return "cancelled", message
            print(f"[plan {plan['plan_id'][-8:]}] step {step_entry['id']} "
                  f"{state} after {time.time() - started_at:.1f}s: {message[:80]}")
            return ("timeout" if state == "timeout" else "failed"), message
        return "timeout", "步骤超时"

    def _verify(self, step_entry, service):
        post = str(step_entry.get("postcondition") or "none")
        if post in ("none",) or post.startswith((
            "position_near:", "near:", "block:", "delivered:", "held:",
            "kills:",
        )):
            # Verified inside the worker command itself.
            return True
        if post.startswith("inventory:"):
            spec = post[len("inventory:"):]
            name, _, required = spec.partition(">=")
            try:
                required = int(required)
            except ValueError:
                return False
            runtime = (service.status().get("runtime") or {})
            have = item_catalog().inventory_count(runtime.get("inventory"), name)
            return have >= required
        return True

    def _replan(self, plan, failed_index):
        from .minecraft_mineflayer import minecraft_bot_service

        try:
            fresh = plan_goal(plan["goal"], minecraft_bot_service.status())
        except PlanError as exc:
            plan["last_error"] = f"重新规划失败：{exc}"
            return False
        plan["replans_used"] += 1
        plan["steps"] = plan["steps"][:failed_index] + fresh["steps"]
        plan["current_step"] = failed_index
        plan["updated_at"] = time.time()
        return True

    def _push_history(self, plan):
        with self._lock:
            self._history.append({
                "plan_id": plan["plan_id"],
                "goal_description": plan["goal_description"],
                "status": plan["status"],
                "last_error": plan.get("last_error") or "",
                "finished_at": time.time(),
            })
            self._history = self._history[-CRAFT_LOG_LIMIT:]

    def _finish_plan(self, plan):
        from .minecraft_mineflayer import minecraft_bot_service

        self._push_history(plan)
        self._set_plan_active(False)
        report = self._report_text(plan)
        try:
            minecraft_bot_service._speak(report)
        except Exception:
            pass
        try:
            from .conversation import conversation_service

            # Plan reports belong to the current game conversation session so
            # they archive together with the chat that started them.
            conversation_service._record("ai", report, {
                "source": "plan", "kind": "plan_report",
                "plan_id": plan["plan_id"], "status": plan["status"],
            }, minecraft_bot_service.current_game_session_id())
        except Exception:
            pass
        # Deep memory linkage: a completed task becomes a long-term episodic
        # memory (source=game_task) so the persona can recall it later - the
        # session file alone was invisible to keyword retrieval until a
        # MANUAL archive ran.
        if plan.get("status") == "completed":
            try:
                from . import game_memory

                game_memory.record_task_event(plan)
            except Exception:
                pass

    @staticmethod
    def _report_text(plan):
        goal_text = plan.get("goal_description") or "任务"
        status = plan.get("status")
        if status == "completed":
            tail = "已经交到你手上了。" if plan.get("will_deliver") else "完成啦。"
            return f"{goal_text}的{tail}"
        if status == "cancelled":
            return f"{goal_text}的任务被中断了。"
        return f"{goal_text}没能完成：{plan.get('last_error') or '未知原因'}"

    # -- views -----------------------------------------------------------------
    def state(self):
        self._recover_dead_thread()
        with self._lock:
            suspended_view = [
                {
                    "plan_id": item["plan_id"],
                    "goal_description": item["goal_description"],
                    "reason": item.get("suspend_reason") or "",
                    "suspend_at": item.get("suspend_at"),
                    "steps_total": len(item.get("steps") or []),
                    "steps_done": int(item.get("current_step") or 0),
                }
                for item in self._suspended
            ]
            plan = self._plan
            if not plan:
                return {
                    "active": False,
                    "suspended": suspended_view,
                    "history": list(self._history),
                }
            total = len(plan["steps"])
            index = min(int(plan.get("current_step") or 0), total)
            current = plan["steps"][index] if 0 <= index < total else None
            missing = {}
            if current:
                for reserve_step in plan["steps"][index:]:
                    for name, count in (reserve_step.get("reserves") or {}).items():
                        missing[name] = missing.get(name, 0) + count
            return {
                "active": plan.get("status") in ("pending", "running"),
                "suspended": suspended_view,
                "plan_id": plan["plan_id"],
                "goal": plan["goal"],
                "goal_description": plan["goal_description"],
                "status": plan["status"],
                "current_step": index,
                "total_steps": total,
                "current_step_description": (current or {}).get("description", ""),
                "current_step_command": (current or {}).get("command", ""),
                "missing": missing,
                "waiting_for_doctor": plan.get("waiting_for_doctor"),
                "will_deliver": plan.get("will_deliver"),
                "last_error": plan.get("last_error"),
                "replans_used": plan.get("replans_used", 0),
                "history": list(self._history),
            }

    def reserved_items(self):
        """Aggregate item needs of the pending steps (drop protection)."""
        with self._lock:
            plan = self._plan
            if not plan or plan.get("status") not in ("pending", "running"):
                return {}
            reserved = {}
            index = int(plan.get("current_step") or 0)
            for step_entry in plan["steps"][index:]:
                for name, count in (step_entry.get("reserves") or {}).items():
                    reserved[name] = reserved.get(name, 0) + count
            return reserved

    @property
    def last_craft(self):
        with self._lock:
            return dict(self._last_craft) if self._last_craft else None


plan_executor = PlanExecutor()


# ---------------------------------------------------------------- goal entry

def start_goal(goal, state):
    """Plan + submit a goal.  Returns (plan_view, error_message)."""
    try:
        plan = plan_goal(goal, state)
    except PlanError as exc:
        return None, str(exc)
    plan_executor.submit(plan)
    return plan_executor.state(), None
