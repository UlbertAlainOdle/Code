"""Offline item catalog tests: aliases, categories, references, protection
(2026-09 plan section 3)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saya_app.services.minecraft_items import item_catalog, matches_family  # noqa: E402

cat = item_catalog()
assert len(cat.items) > 1000, "registry failed to load"

INVENTORY = [
    {"name": "oak_log", "count": 12},
    {"name": "spruce_planks", "count": 20},
    {"name": "wooden_pickaxe", "count": 1},
    {"name": "bread", "count": 3},
    {"name": "rotten_flesh", "count": 7},
]

# 1. aliases: systematic tool names + curated survival items
assert cat.lookup("木镐") == "wooden_pickaxe"
assert cat.lookup("钻石剑") == "diamond_sword"
assert cat.lookup("下界合金镐") == "netherite_pickaxe"
assert cat.lookup("工作台") == "crafting_table"
assert cat.lookup("圆石") == "cobblestone"
assert cat.lookup("腐肉") == "rotten_flesh"
assert cat.lookup("粗铁") == "raw_iron"
assert cat.lookup("粗铜") == "raw_copper"
assert cat.lookup("粗金") == "raw_gold"
assert cat.lookup("wooden sword") == "wooden_sword"
assert cat.lookup("石头") == "stone"  # English exact name stays exact

# 2. families + categories
assert matches_family("oak_log", "_log") and matches_family("crimson_stem", "_log")
assert matches_family("spruce_planks", "planks")
assert cat.inventory_count(INVENTORY, "_log") == 12
assert cat.inventory_count(INVENTORY, "planks") == 20
assert cat.category("wooden_pickaxe") == "tool"
assert cat.category("bread") == "food"
assert cat.category("wooden_sword") == "weapon"
assert cat.category("iron_helmet") == "armor"
assert cat.category("redstone") == "material"

# 3. contextual references (plan 3.2)
assert cat.resolve_reference("手里的", inventory=INVENTORY,
                             held_item="wooden_pickaxe")["names"] == ["wooden_pickaxe"]
assert cat.resolve_reference("刚做好的", inventory=INVENTORY,
                             last_craft="wooden_sword")["kind"] == "last_craft"
assert cat.resolve_reference("把木头给我", inventory=INVENTORY)["names"] == ["oak_log"]
r = cat.resolve_reference("食物", inventory=INVENTORY)
assert "bread" in r["names"] and "rotten_flesh" in r["names"]
r = cat.resolve_reference("工具", inventory=INVENTORY)
assert r["names"] == ["wooden_pickaxe"]
assert cat.resolve_reference("把腐肉扔了", inventory=INVENTORY)["names"] == ["rotten_flesh"]
assert cat.resolve_reference("木剑给我", inventory=INVENTORY)["names"] == ["wooden_sword"]
assert cat.resolve_reference("你好呀", inventory=INVENTORY)["kind"] is None

# 4. drop protection (plan 3.3)
assert cat.drop_is_protected("wooden_pickaxe", inventory=INVENTORY)      # tools
assert cat.drop_is_protected("diamond", inventory=INVENTORY)             # rare
assert cat.drop_is_protected("diamond_sword", inventory=INVENTORY)       # weapon
assert not cat.drop_is_protected("rotten_flesh", inventory=INVENTORY)    # junk
assert not cat.drop_is_protected("bread", inventory=INVENTORY, explicit=True)
assert cat.drop_is_protected(  # the LAST food stack stays when vague
    "bread", inventory=[{"name": "bread", "count": 3}], explicit=False)

# 5. item view feeds the desktop crafting search
view = cat.item_view()
assert view and {"name", "display_name", "zh", "category"} <= set(view[0])
assert any(item["name"] == "wooden_sword" for item in view)

print("item catalog smoke tests passed.")
