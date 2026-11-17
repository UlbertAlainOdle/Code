"""Variant-aware potion identity for the Python side (TaskIntent / planner).

The Node worker owns the authoritative 1.21.5 registry (``potion.mjs``) and
reports what it actually loaded in its state snapshot.  This module mirrors
that registry so Python can validate goals *before* they reach the worker,
and must stay byte-compatible with it - enforced by the cross-language
contract test in ``tests/test_potion_intent_corpus.py``.

Identity convention across the whole stack:

    ``<container>/<potion>``  e.g. ``potion/healing``, ``splash_potion/healing``

- container: ``potion`` (drinkable) | ``splash_potion`` | ``lingering_potion``
- potion:    vanilla potion id from the registry (``healing``,
             ``strong_healing``, ``long_fire_resistance``, ...)
- Phase 1 scope: vanilla potions only.  Stacks carrying custom effects are
  counted as unknown and every automatic action on them is refused.
"""

from __future__ import annotations

import re

# Mirror of minecraft_worker/potion.mjs VANILLA_POTIONS_1_21_5.  Contract
# tested; do not edit one side without the other.  Note there is deliberately
# NO long_healing / long_harming: instant effects cannot be extended.
VANILLA_POTIONS_1_21_5 = [
    "water", "mundane", "thick", "awkward",
    "night_vision", "long_night_vision", "invisibility", "long_invisibility",
    "leaping", "long_leaping", "strong_leaping",
    "fire_resistance", "long_fire_resistance",
    "swiftness", "long_swiftness", "strong_swiftness",
    "slowness", "long_slowness", "strong_slowness",
    "turtle_master", "long_turtle_master", "strong_turtle_master",
    "water_breathing", "long_water_breathing",
    "healing", "strong_healing", "harming", "strong_harming",
    "poison", "long_poison", "strong_poison",
    "regeneration", "long_regeneration", "strong_regeneration",
    "strength", "long_strength", "strong_strength",
    "weakness", "long_weakness", "luck",
    "slow_falling", "long_slow_falling",
    "wind_charged", "weaving", "oozing", "infested",
]

VANILLA_POTION_IDS = frozenset(VANILLA_POTIONS_1_21_5)

CONTAINERS = ("potion", "splash_potion", "lingering_potion")

# Mirror of minecraft_worker/brewing.mjs BREWING_RECIPES (conservative subset).
# out_potion <- {input: base potion, ingredient}
BREWING_RECIPES = {
    "awkward": {"input": "water", "ingredient": "nether_wart"},
    "night_vision": {"input": "awkward", "ingredient": "golden_carrot"},
    "invisibility": {"input": "night_vision", "ingredient": "fermented_spider_eye"},
    "leaping": {"input": "awkward", "ingredient": "rabbit_foot"},
    "fire_resistance": {"input": "awkward", "ingredient": "magma_cream"},
    "swiftness": {"input": "awkward", "ingredient": "sugar"},
    "slowness": {"input": "swiftness", "ingredient": "fermented_spider_eye"},
    "water_breathing": {"input": "awkward", "ingredient": "pufferfish"},
    "healing": {"input": "awkward", "ingredient": "glistering_melon_slice"},
    "harming": {"input": "healing", "ingredient": "fermented_spider_eye"},
    "poison": {"input": "awkward", "ingredient": "spider_eye"},
    "regeneration": {"input": "awkward", "ingredient": "ghast_tear"},
    "strength": {"input": "awkward", "ingredient": "blaze_powder"},
    "weakness": {"input": "water", "ingredient": "fermented_spider_eye"},
    "slow_falling": {"input": "awkward", "ingredient": "phantom_membrane"},
    "long_night_vision": {"input": "night_vision", "ingredient": "redstone"},
    "long_invisibility": {"input": "invisibility", "ingredient": "redstone"},
    "long_leaping": {"input": "leaping", "ingredient": "redstone"},
    "strong_leaping": {"input": "leaping", "ingredient": "glowstone_dust"},
    "long_fire_resistance": {"input": "fire_resistance", "ingredient": "redstone"},
    "long_swiftness": {"input": "swiftness", "ingredient": "redstone"},
    "strong_swiftness": {"input": "swiftness", "ingredient": "glowstone_dust"},
    "long_slowness": {"input": "slowness", "ingredient": "redstone"},
    "strong_slowness": {"input": "slowness", "ingredient": "glowstone_dust"},
    "long_water_breathing": {"input": "water_breathing", "ingredient": "redstone"},
    "strong_healing": {"input": "healing", "ingredient": "glowstone_dust"},
    "strong_harming": {"input": "harming", "ingredient": "glowstone_dust"},
    "long_poison": {"input": "poison", "ingredient": "redstone"},
    "strong_poison": {"input": "poison", "ingredient": "glowstone_dust"},
    "long_regeneration": {"input": "regeneration", "ingredient": "redstone"},
    "strong_regeneration": {"input": "regeneration", "ingredient": "glowstone_dust"},
    "long_strength": {"input": "strength", "ingredient": "redstone"},
    "strong_strength": {"input": "strength", "ingredient": "glowstone_dust"},
    "long_weakness": {"input": "weakness", "ingredient": "redstone"},
    "long_slow_falling": {"input": "slow_falling", "ingredient": "redstone"},
}

# Chinese display names for potions, keyed by potion id.  Container prefixes
# (喷溅型 / 滞留型) are handled separately so "喷溅型治疗药水" composes.
POTION_CHINESE = {
    "water": "水瓶", "mundane": "粗制药水", "thick": "黏稠药水", "awkward": "粗制基础药水",
    "night_vision": "夜视药水", "long_night_vision": "延长夜视药水",
    "invisibility": "隐身药水", "long_invisibility": "延长隐身药水",
    "leaping": "跳跃药水", "long_leaping": "延长跳跃药水", "strong_leaping": "强效跳跃药水",
    "fire_resistance": "抗火药水", "long_fire_resistance": "延长抗火药水",
    "swiftness": "迅捷药水", "long_swiftness": "延长迅捷药水", "strong_swiftness": "强效迅捷药水",
    "slowness": "缓慢药水", "long_slowness": "延长缓慢药水", "strong_slowness": "强效缓慢药水",
    "turtle_master": "龟霸药水", "long_turtle_master": "延长龟霸药水", "strong_turtle_master": "强效龟霸药水",
    "water_breathing": "水肺药水", "long_water_breathing": "延长水肺药水",
    "healing": "治疗药水", "strong_healing": "强效治疗药水",
    "harming": "伤害药水", "strong_harming": "强效伤害药水",
    "poison": "剧毒药水", "long_poison": "延长剧毒药水", "strong_poison": "强效剧毒药水",
    "regeneration": "再生药水", "long_regeneration": "延长再生药水", "strong_regeneration": "强效再生药水",
    "strength": "力量药水", "long_strength": "延长力量药水", "strong_strength": "强效力量药水",
    "weakness": "虚弱药水", "long_weakness": "延长虚弱药水",
    "luck": "幸运药水",
    "slow_falling": "缓降药水", "long_slow_falling": "延长缓降药水",
    "wind_charged": "风弹药水", "weaving": "盘丝药水", "oozing": "渗浆药水", "infested": "寄生药水",
}

# Chinese names for brewing materials the planner reports as missing.
INGREDIENT_CHINESE = {
    "water_bottle": "水瓶",
    "glass_bottle": "玻璃瓶",
    "nether_wart": "下界疣",
    "blaze_powder": "烈焰粉",
    "blaze_rod": "烈焰棒",
    "glistering_melon_slice": "闪烁的西瓜片",
    "melon_slice": "西瓜片",
    "gold_nugget": "金粒",
    "glowstone_dust": "荧石粉",
    "redstone": "红石粉",
    "fermented_spider_eye": "发酵蛛眼",
    "spider_eye": "蜘蛛眼",
    "sugar": "糖",
    "rabbit_foot": "兔子脚",
    "golden_carrot": "金胡萝卜",
    "magma_cream": "岩浆膏",
    "ghast_tear": "恶魂之泪",
    "pufferfish": "河豚",
    "phantom_membrane": "幻翼膜",
}

# Spoken Chinese -> potion id (mirror of the worker's alias tables; the
# conversation layer also keeps its own brew-phrase table for parse_intent).
POTION_NAME_ALIASES = {
    "水瓶": "water", "水壶": "water",
    "粗制药水": "mundane", "黏稠药水": "thick", "基础药水": "awkward", "粗制基础药水": "awkward",
    "治疗药水": "healing", "瞬间治疗药水": "healing",
    "力量药水": "strength", "强效力量药水": "strong_strength", "延长力量药水": "long_strength",
    "迅捷药水": "swiftness", "速度药水": "swiftness", "强效迅捷药水": "strong_swiftness",
    "延长迅捷药水": "long_swiftness",
    "抗火药水": "fire_resistance", "防火药水": "fire_resistance", "延长抗火药水": "long_fire_resistance",
    "水肺药水": "water_breathing", "延长水肺药水": "long_water_breathing",
    "夜视药水": "night_vision", "延长夜视药水": "long_night_vision",
    "隐身药水": "invisibility", "延长隐身药水": "long_invisibility",
    "剧毒药水": "poison", "强效剧毒药水": "strong_poison", "延长剧毒药水": "long_poison",
    "再生药水": "regeneration", "强效再生药水": "strong_regeneration", "延长再生药水": "long_regeneration",
    "瞬间伤害药水": "harming", "强效伤害药水": "strong_harming",
    "虚弱药水": "weakness", "延长虚弱药水": "long_weakness",
    "缓降药水": "slow_falling", "延长缓降药水": "long_slow_falling",
    "跳跃药水": "leaping", "缓慢药水": "slowness",
    "风弹药水": "wind_charged", "盘丝药水": "weaving", "渗浆药水": "oozing", "寄生药水": "infested",
    "伤害药水": "harming",
}

CONTAINER_PREFIXES = {
    "喷溅型": "splash_potion", "喷溅": "splash_potion",
    "滞留型": "lingering_potion", "滞留": "lingering_potion",
}

_POTION_PHRASE_RE = re.compile(
    r"(?P<container>喷溅型|喷溅|滞留型|滞留)?(?P<effect>强效|延长)?(?P<name>[\u4e00-\u9fa5]{1,8}药水)"
)


class UnknownPotionError(Exception):
    """Raised when a potion id has no brew chain in the conservative subset."""


def strip_namespace(value):
    return str(value or "").replace("minecraft:", "", 1)


def variant_key(container, potion_id):
    """Canonical ``<container>/<potion>`` key used across planner/worker."""
    return f"{strip_namespace(container)}/{strip_namespace(potion_id)}"


def parse_variant_key(target):
    """Parse ``potion/healing`` style keys.  Returns dict or None.

    Only vanilla registry ids are accepted - phase 1 refuses custom potions
    (and impossible ids like ``long_healing``) at parse time.
    """
    text = strip_namespace(str(target or "").strip())
    base, slash, potion_id = text.partition("/")
    if not slash:
        return None
    base, potion_id = base.strip().lower(), potion_id.strip().lower()
    if base not in CONTAINERS or potion_id not in VANILLA_POTION_IDS:
        return None
    return {"container": base, "potion": potion_id, "key": variant_key(base, potion_id)}


def parse_potion_target(target):
    """Resolve a goal target into a potion descriptor.

    Accepts ``potion/healing`` keys, bare registry ids (``healing``,
    ``healing_potion``) and spoken Chinese (``治疗药水``, ``喷溅型治疗药水``).
    Returns {container, potion, key} or None when the target is not a potion.
    """
    text = str(target or "").strip()
    if not text:
        return None
    direct = parse_variant_key(text)
    if direct:
        return direct
    lowered = strip_namespace(text).lower().replace(" ", "_")
    if lowered in CONTAINERS:
        # Bare "potion" without an effect is not a concrete target.
        return None
    if lowered.endswith("_potion"):
        lowered = lowered[: -len("_potion")]
    if lowered in VANILLA_POTION_IDS:
        return {"container": "potion", "potion": lowered, "key": variant_key("potion", lowered)}
    match = _POTION_PHRASE_RE.search(text)
    if not match:
        return None
    container = CONTAINERS[0]
    if match.group("container"):
        container = CONTAINER_PREFIXES[match.group("container")]
    # The regex consumes 强效/延长 as its own group and may swallow leading
    # words into the name; reassemble and resolve from the LONGEST suffix so
    # "强效治疗药水" stays strong_healing instead of degrading to healing.
    name = (match.group("effect") or "") + match.group("name")
    potion_id = ""
    for cut in range(len(name)):
        candidate = name[cut:]
        if not candidate.endswith("药水") or len(candidate) < 4:
            continue
        potion_id = POTION_NAME_ALIASES.get(candidate, "")
        if potion_id:
            break
        stem = candidate[:-2]
        if stem.startswith("强效") or stem.startswith("延长"):
            modifier = "strong_" if stem.startswith("强效") else "long_"
            base_id = POTION_NAME_ALIASES.get(f"{stem[2:]}药水", "")
            if base_id:
                potion_id = f"{modifier}{base_id}"
                break
    if not potion_id or potion_id not in VANILLA_POTION_IDS:
        return None
    return {"container": container, "potion": potion_id, "key": variant_key(container, potion_id)}


def potion_chinese(key_or_container, potion_id=None):
    """Human label for a variant key (``potion/healing`` -> ``治疗药水``)."""
    if potion_id is None:
        parsed = parse_variant_key(key_or_container)
        if not parsed:
            return str(key_or_container)
        container, potion_id = parsed["container"], parsed["potion"]
    else:
        container = key_or_container
    base = POTION_CHINESE.get(potion_id, potion_id)
    if container == "splash_potion":
        return f"喷溅型{base}" if not base.startswith("喷溅") else base
    if container == "lingering_potion":
        return f"滞留型{base}"
    return base


def ingredient_chinese(name):
    return INGREDIENT_CHINESE.get(name) or POTION_CHINESE.get(name) or name


def is_brewable(potion_id):
    """True when the conservative recipe subset can produce this potion."""
    if potion_id not in BREWING_RECIPES:
        return False
    chain = brew_chain(potion_id)
    return bool(chain)


def brew_chain(potion_id):
    """Recipe edges from the base stage to the target, base first.

    Each entry is ``(out_potion, ingredient, input_potion)``.  Raises
    UnknownPotionError when any stage is missing from the conservative
    subset (e.g. luck, mundane) or the chain is suspiciously deep.
    """
    chain = []
    current = strip_namespace(potion_id)
    for _ in range(8):
        edge = BREWING_RECIPES.get(current)
        if edge is None:
            raise UnknownPotionError(f"{current} 不在可酿造配方表里")
        chain.append((current, edge["ingredient"], edge["input"]))
        if edge["input"] == "water":
            chain.reverse()
            return chain
        current = edge["input"]
    raise UnknownPotionError(f"{potion_id} 的配方链太深")
