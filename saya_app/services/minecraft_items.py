"""Unified Minecraft item catalog and inventory reference resolver
(2026-09 plan section 3).

Built once from the local minecraft-data registry files under
``saya_app/data/minecraft/<version>/``:

- internal name         wooden_sword
- display name          Wooden Sword
- Chinese aliases       木剑 / 木制剑
- category              weapon | tool | armor | block | food | material
- stack size / food points

The resolver understands contextual references ("这个" = held item, "刚做的" =
last craft result, "木头" = every log family item, "食物" = anything edible)
and returns candidate lists instead of guessing when a phrase is ambiguous
("金合欢原木还是木板？").  Protection rules keep vague drops from throwing
away tools, weapons, rare ores or the last food.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "minecraft"
DEFAULT_VERSION = "1.21.5"

TOOL_SUFFIXES = ("pickaxe", "axe", "shovel", "hoe", "shears", "flint_and_steel",
                 "bow", "crossbow", "fishing_rod", "brush")
WEAPON_SUFFIXES = ("sword", "trident", "mace")
ARMOR_SUFFIXES = ("helmet", "chestplate", "leggings", "boots")

# Materials whose Chinese names prefix tools/weapons/armor.
MATRICES = {
    "wooden": "木", "stone": "石", "iron": "铁", "golden": "金",
    "diamond": "钻石", "netherite": "下界合金", "copper": "铜",
    "leather": "皮革", "chainmail": "锁链",
}
TOOL_CN = {
    "pickaxe": "镐", "axe": "斧", "shovel": "铲", "hoe": "锄",
    "sword": "剑", "helmet": "头盔", "chestplate": "胸甲",
    "leggings": "护腿", "boots": "靴子",
}

# Hand-curated aliases for common survival items.  Systematic tool/armor
# names are generated from MATRICES x TOOL_CN on top of these.
ZH_ALIASES = {
    "crafting_table": ["工作台", "合成台"],
    "furnace": ["熔炉", "炉子"],
    "chest": ["箱子"],
    "stick": ["木棍", "棍子"],
    "torch": ["火把"],
    "coal": ["煤炭", "煤"],
    "charcoal": ["木炭"],
    "cobblestone": ["圆石"],
    "stone": ["石头", "石料"],
    "dirt": ["泥土", "土"],
    "sand": ["沙子", "沙"],
    "gravel": ["砂砾", "碎石"],
    "clay_ball": ["黏土球", "黏土"],
    "bread": ["面包"],
    "apple": ["苹果"],
    "golden_apple": ["金苹果"],
    "porkchop": ["生猪排", "猪肉", "猪排"],
    "cooked_porkchop": ["熟猪排", "熟猪肉", "烤猪排"],
    "beef": ["生牛肉", "牛肉"],
    "cooked_beef": ["牛排", "熟牛肉", "烤牛肉", "烤牛排"],
    "mutton": ["羊肉", "生羊肉"],
    "cooked_mutton": ["熟羊肉", "烤羊肉"],
    "cooked_chicken": ["熟鸡肉", "烤鸡", "烤鸡肉"],
    "rabbit": ["兔肉", "生兔肉"],
    "cooked_rabbit": ["熟兔肉", "烤兔肉"],
    "chicken": ["生鸡肉", "鸡肉"],
    "rabbit_hide": ["兔子皮"],
    "rabbit_foot": ["兔子脚"],
    "ink_sac": ["墨囊"],
    "milk_bucket": ["牛奶", "牛奶桶", "一桶牛奶"],
    "raw_iron": ["粗铁", "生铁"],
    "raw_copper": ["粗铜", "生铜"],
    "raw_gold": ["粗金", "生金"],
    "rotten_flesh": ["腐肉"],
    "iron_ingot": ["铁锭"],
    "gold_ingot": ["金锭"],
    "copper_ingot": ["铜锭"],
    "iron_nugget": ["铁粒"],
    "gold_nugget": ["金粒"],
    "diamond": ["钻石"],
    "emerald": ["绿宝石"],
    "lapis_lazuli": ["青金石"],
    "redstone": ["红石"],
    "coal_ore": ["煤矿", "煤矿石"],
    "iron_ore": ["铁矿", "铁矿石"],
    "copper_ore": ["铜矿", "铜矿石"],
    "gold_ore": ["金矿", "金矿石"],
    "diamond_ore": ["钻石矿", "钻石矿石"],
    "redstone_ore": ["红石矿"],
    "lapis_ore": ["青金石矿"],
    "emerald_ore": ["绿宝石矿"],
    "arrow": ["箭", "箭矢"],
    "bone": ["骨头"],
    "string": ["线", "蛛丝"],
    "feather": ["羽毛"],
    "gunpowder": ["火药"],
    "ender_pearl": ["末影珍珠"],
    "book": ["书"],
    "paper": ["纸"],
    "leather": ["皮革"],
    "wheat": ["小麦"],
    "flint": ["燧石"],
    "bucket": ["桶"],
    "water_bucket": ["水桶"],
    "lava_bucket": ["岩浆桶"],
    "bed": ["床"],
    "boat": ["船"],
    "rail": ["铁轨"],
    "wool": ["羊毛"],
    "glass": ["玻璃"],
    "obsidian": ["黑曜石"],
    "planks": ["木板", "板子"],
    "_log": ["木头", "原木", "树", "木材"],
    "flint_and_steel": ["打火石"],
    "shield": ["盾牌"],
    "anvil": ["铁砧"],
    # Special/raid-drop items (spoken names for the acquisition planner).
    "totem_of_undying": ["不死图腾", "图腾"],
    "blaze_rod": ["烈焰棒"],
    "ghast_tear": ["恶魂之泪"],
    "magma_cream": ["岩浆膏"],
    "slime_ball": ["史莱姆球", "粘液球"],
    "phantom_membrane": ["幻翼膜"],
    "prismarine_shard": ["海晶碎片"],
    "prismarine_crystal": ["海晶晶体"],
    "shulker_shell": ["潜影壳"],
    "wither_skeleton_skull": ["凋灵骷髅头颅", "凋灵头颅"],
    # Probabilistic block-drop items (planner SPECIAL_BLOCK_DROPS).
    "wheat_seeds": ["小麦种子", "种子"],
    "oak_sapling": ["橡树苗", "橡树苗苗"],
    "spruce_sapling": ["云杉树苗"],
    "birch_sapling": ["白桦树苗"],
    "snowball": ["雪球"],
}
# 树苗 spoken without a tree qualifier resolves to the common oak sapling.
_EXTRA_FAMILY_ALIASES = {"树苗": "oak_sapling"}

RARE_ITEMS = {
    "diamond", "emerald", "diamond_ore", "emerald_ore", "deepslate_diamond_ore",
    "deepslate_emerald_ore", "ancient_debris", "netherite_ingot",
    "netherite_scrap", "elytra", "totem_of_undying", "enchanted_golden_apple",
    "nether_star", "dragon_egg", "beacon",
}

# Category phrases usable in requests ("把食物给我", "留下工具").
CATEGORY_PHRASES = {
    "食物": "food",
    "吃的": "food",
    "食品": "food",
    "工具": "tool",
    "武器": "weapon",
    "装备": "armor",
    "护甲": "armor",
    "防具": "armor",
    "木头": "_log",
    "木材": "_log",
    "原木": "_log",
    "木板": "planks",
    "矿石": "ore",
    "矿物": "ore",
}

HELD_RE = re.compile(r"这个|手里|手上|当前手持|手持的")
LAST_CRAFT_RE = re.compile(r"刚做|刚制作|刚合成|刚 craft|刚做好的")


def _category_of(name, *, food=False, block=False):
    if any(name.endswith(suffix) or f"{suffix}" in name for suffix in ARMOR_SUFFIXES):
        return "armor"
    if any(name.endswith(suffix) for suffix in WEAPON_SUFFIXES):
        return "weapon"
    if any(name.endswith(suffix) for suffix in TOOL_SUFFIXES):
        return "tool"
    if food:
        return "food"
    if block:
        return "block"
    return "material"


class ItemCatalog:
    """Registry-backed item lookup with Chinese aliases and categories."""

    def __init__(self, version=DEFAULT_VERSION):
        self._lock = threading.Lock()
        self._loaded = False
        self.version = version
        self.items = {}          # name -> info
        self.by_display = {}     # lowercase display name -> name
        self.alias_map = {}      # chinese/english alias -> name or family
        self._loaded = self._load(version)

    # -- loading -------------------------------------------------------------
    def _load(self, version):
        base = DATA_DIR / str(version)
        try:
            items_raw = json.loads((base / "items.json").read_text(encoding="utf-8"))
            foods_raw = json.loads((base / "foods.json").read_text(encoding="utf-8"))
            blocks_raw = json.loads((base / "blocks.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            items_raw, foods_raw, blocks_raw = [], [], []

        food_names = {row.get("name") for row in foods_raw if isinstance(row, dict)}
        block_names = {row.get("name") for row in blocks_raw if isinstance(row, dict)}
        for row in items_raw:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            name = str(row["name"])
            display = str(row.get("displayName") or name)
            self.items[name] = {
                "name": name,
                "id": row.get("id"),
                "display_name": display,
                "stack_size": int(row.get("stackSize") or 64),
                "category": _category_of(
                    name, food=name in food_names, block=name in block_names
                ),
                "food_points": next(
                    (
                        row2.get("foodPoints")
                        for row2 in foods_raw
                        if isinstance(row2, dict) and row2.get("name") == name
                    ),
                    None,
                ),
                "max_durability": row.get("maxDurability"),
                "aliases": [],
            }
            self.by_display[display.lower()] = name

        # systematic tool / weapon / armor Chinese names
        for name, info in self.items.items():
            for prefix_en, prefix_cn in MATRICES.items():
                if not name.startswith(prefix_en + "_"):
                    continue
                rest = name[len(prefix_en) + 1:]
                if rest in TOOL_CN:
                    info["aliases"].append(f"{prefix_cn}{TOOL_CN[rest]}")
                    break

        for canonical, aliases in ZH_ALIASES.items():
            for alias in aliases:
                self.alias_map[alias] = canonical
        for alias, canonical in _EXTRA_FAMILY_ALIASES.items():
            self.alias_map.setdefault(alias, canonical)
        for name, info in self.items.items():
            for alias in info["aliases"]:
                self.alias_map.setdefault(alias, name)
            # display names work as aliases too ("wooden sword")
            self.alias_map.setdefault(info["display_name"].lower(), name)

        return bool(self.items)

    # -- lookup --------------------------------------------------------------
    def lookup(self, text):
        """Canonical name (or family like ``_log``/``planks``) for a phrase."""
        value = str(text or "").strip().lower().replace(" ", "_").removeprefix("minecraft:")
        if not value:
            return None
        if value in self.items or value in ("_log", "planks"):
            return value
        if value in self.alias_map:
            return self.alias_map[value]
        # exact English name with minecraft: prefix removed above; try
        # replacing spaces already done; fall back to suffix family match.
        for name in self.items:
            if name.endswith(f"_{value}"):
                return name
        return None

    def display(self, name):
        info = self.items.get(name)
        return info["display_name"] if info else name

    def chinese(self, name):
        for alias, canonical in self.alias_map.items():
            if canonical == name:
                return alias
        return self.display(name)

    def category(self, name):
        info = self.items.get(name)
        return info["category"] if info else "material"

    def item_view(self):
        """JSON view for the desktop console search box."""
        out = []
        for info in sorted(self.items.values(), key=lambda i: i["name"]):
            out.append({
                "name": info["name"],
                "display_name": info["display_name"],
                "zh": next(
                    (alias for alias, canonical in self.alias_map.items()
                     if canonical == info["name"] and any("\u4e00" <= ch <= "\u9fff" for ch in alias)),
                    "",
                ),
                "category": info["category"],
                "stack_size": info["stack_size"],
            })
        return out

    # -- inventory references --------------------------------------------------
    def inventory_count(self, inventory, wanted):
        """Total count of a canonical name/family inside an inventory view
        (list of {name, count} as reported by the worker state).

        Variant keys ("potion/healing", "splash_potion/strong_healing") count
        only entries whose worker-reported potion identity matches the exact
        container + effect, so a night-vision bottle never satisfies a
        healing-potion goal.
        """
        wanted = str(wanted or "")
        base, slash, variant = wanted.partition("/")
        total = 0
        for entry in inventory or []:
            name = str(entry.get("name") or "")
            count = int(entry.get("count") or 0)
            if slash and variant:
                if name != base:
                    continue
                potion = entry.get("potion") or {}
                if str(potion.get("potion") or "") == variant and not potion.get("custom_effects"):
                    total += count
            elif matches_family(name, wanted):
                total += count
        return total

    def resolve_reference(self, text, *, inventory=None, held_item=None, last_craft=None):
        """Resolve an inventory reference from natural language.

        Returns a dict:
          kind: 'item' | 'family' | 'category' | 'held' | 'last_craft' | None
          names: matched inventory item names (best effort)
          candidates: ambiguous display names needing a clarification
        """
        value = str(text or "").strip()
        inventory = inventory or []

        if HELD_RE.search(value):
            if held_item:
                return {"kind": "held", "names": [held_item], "candidates": []}
        if LAST_CRAFT_RE.search(value) and last_craft:
            return {"kind": "last_craft", "names": [last_craft], "candidates": []}

        for phrase, category in CATEGORY_PHRASES.items():
            if phrase in value:
                if category in ("food", "tool", "weapon", "armor"):
                    names = sorted({
                        str(entry.get("name"))
                        for entry in inventory
                        if self.category(str(entry.get("name") or "")) == category
                    })
                    if names:
                        return {"kind": "category", "names": names, "candidates": []}
                elif category in ("_log", "planks"):
                    names = sorted({
                        str(entry.get("name"))
                        for entry in inventory
                        if matches_family(str(entry.get("name") or ""), category)
                    })
                    if names:
                        return {"kind": "family", "names": names, "candidates": []}
                elif category == "ore":
                    names = sorted({
                        str(entry.get("name"))
                        for entry in inventory
                        if str(entry.get("name") or "").endswith("_ore")
                    })
                    if names:
                        return {"kind": "category", "names": names, "candidates": []}

        canonical = self.lookup(value)
        if canonical:
            return {"kind": "item", "names": [canonical], "candidates": []}
        # Item noun embedded in a sentence ("把腐肉扔了", "木剑给我"): pick the
        # longest alias contained in the phrase; ties become clarification
        # candidates instead of a random guess.
        hits = [
            (len(alias), canonical, alias)
            for alias, canonical in self.alias_map.items()
            if alias and alias in value and any("\u4e00" <= ch <= "\u9fff" for ch in alias)
        ]
        if hits:
            hits.sort(key=lambda hit: hit[0], reverse=True)
            best_length = hits[0][0]
            best = {canonical for length, canonical, _ in hits if length == best_length}
            if len(best) == 1:
                return {"kind": "item", "names": [next(iter(best))], "candidates": []}
            return {"kind": "item", "names": [], "candidates": sorted(best)}
        return {"kind": None, "names": [], "candidates": []}

    # -- protection ------------------------------------------------------------
    def drop_is_protected(self, name, *, inventory=None, explicit=False):
        """True when the item must not be dropped by a vague request.

        Explicit, unambiguous requests (exact item + count) are allowed but
        enchanted / high-durability tools still warrant confirmation upstream.
        """
        category = self.category(name)
        if name in RARE_ITEMS:
            return True
        if category in ("tool", "weapon", "armor"):
            return True
        if not explicit and category == "food":
            # the last food stack stays
            inventory = inventory or []
            foods = [e for e in inventory
                     if self.category(str(e.get("name") or "")) == "food"]
            return len(foods) <= 1
        return False


def matches_family(name, wanted):
    """Family-aware equality (mirrors the worker's matchesItem)."""
    actual = str(name or "").removeprefix("minecraft:")
    target = str(wanted or "")
    if target == "_log":
        return actual.endswith(("_log", "_stem", "_wood", "_hyphae"))
    if target == "planks":
        return actual.endswith("_planks")
    if target == "wool":
        return actual == "wool" or actual.endswith("_wool")
    if target.endswith("_ore"):
        return actual in (target, f"deepslate_{target}")
    return actual == target


_catalog_instance = None
_catalog_lock = threading.Lock()


def item_catalog(version=DEFAULT_VERSION):
    global _catalog_instance
    with _catalog_lock:
        if _catalog_instance is None:
            _catalog_instance = ItemCatalog(version)
        return _catalog_instance
