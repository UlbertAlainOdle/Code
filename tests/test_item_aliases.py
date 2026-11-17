"""Root-cause regressions for the equip-name hijack bug family.

User-reported: 装备金头盔 became `equip gold_ore`, and even a correct
`equip gold_helmet` only put the helmet in the HAND.  Root causes fixed:

1. The old shared normalizer matched BLOCK_ALIASES by substring BEFORE the
   item catalog, so any item whose Chinese name contains an ore word
   (金/铁/铜/钻石) was hijacked: 装备金头盔 -> equip gold_ore,
   制作金苹果 -> craft gold_ore.  Item-context commands (equip/craft/smelt)
   now resolve through the registry item catalog only; collect/mine keep
   the block-alias fuzzing on purpose.
2. Armor Chinese aliases must exist on BOTH sides of the pipe (Python
   catalog and worker command.mjs) - contract-tested here.
"""

import re
from pathlib import Path

import pytest

from saya_app.services.conversation import _item_name, parse_intent
from saya_app.services.minecraft_items import item_catalog
from saya_app.services.task_intent import parse_task_intent


EQUIP_CASES = [
    ("装备金头盔", "equip golden_helmet"),
    ("装备下界合金胸甲", "equip netherite_chestplate"),
    ("装备盾牌", "equip shield"),
    ("拿着皮革靴子", "equip leather_boots"),
    ("手持金锭", "equip gold_ingot"),
    ("装备钻石头盔", "equip diamond_helmet"),
    ("装备钻石剑", "equip diamond_sword"),
    ("equip gold_helmet", "equip gold_helmet"),
]

CRAFT_CASES = [
    ("制作金苹果", "craft golden_apple 1"),
    ("做铁砧", "craft anvil 1"),
    ("制作铁胸甲", "craft iron_chestplate 1"),
]

MINING_REGRESSION_CASES = [
    # Block-context fuzzing must survive the fix.
    ("挖金矿", "collect gold_ore 1"),
    ("挖铁矿", "collect iron_ore 1"),
    ("挖点石头", "collect cobblestone 8"),
    ("砍树", "chop 4"),
]


@pytest.mark.parametrize("text,expected", EQUIP_CASES)
def test_equip_resolves_through_item_catalog(text, expected):
    assert parse_intent(text) == expected


@pytest.mark.parametrize("text,expected", CRAFT_CASES)
def test_craft_resolves_through_item_catalog(text, expected):
    assert parse_intent(text) == expected


@pytest.mark.parametrize("text,expected", MINING_REGRESSION_CASES)
def test_mining_keeps_block_alias_fuzzing(text, expected):
    assert parse_intent(text) == expected


@pytest.mark.parametrize("phrase,expected", [
    ("金头盔", "golden_helmet"),
    ("铁胸甲", "iron_chestplate"),
    ("下界合金护腿", "netherite_leggings"),
    ("锁链靴子", "chainmail_boots"),
    ("金苹果", "golden_apple"),
    ("盾牌", "shield"),
    ("铁砧", "anvil"),
    # ordinary chat / unknown words stay untouched instead of becoming ores
    ("我不知道", "我不知道"),
])
def test_item_context_never_hijacks_to_ores(phrase, expected):
    assert _item_name(phrase) == expected


def test_armor_aliases_mirror_worker():
    """The same Chinese armor aliases must exist in the worker's parser."""
    worker_source = (
        Path(__file__).resolve().parents[1] / "minecraft_worker" / "command.mjs"
    ).read_text(encoding="utf-8")
    worker_aliases = dict(re.findall(r"([\u4e00-\u9fa5]{2,8}):\s*'([a-z_]+)'", worker_source))
    catalog = item_catalog()
    for material_cn, material_en in (
        ("皮革", "leather"), ("锁链", "chainmail"), ("铁", "iron"),
        ("金", "golden"), ("钻石", "diamond"), ("下界合金", "netherite"),
    ):
        for piece_cn, piece_en in (
            ("头盔", "helmet"), ("胸甲", "chestplate"),
            ("护腿", "leggings"), ("靴子", "boots"),
        ):
            alias = f"{material_cn}{piece_cn}"
            canonical = f"{material_en}_{piece_en}"
            assert catalog.alias_map.get(alias) == canonical, alias
            assert worker_aliases.get(alias) == canonical, alias
    for alias, canonical in (("盾牌", "shield"), ("铁砧", "anvil"), ("金苹果", "golden_apple")):
        assert catalog.alias_map.get(alias) == canonical, alias
        assert worker_aliases.get(alias) == canonical, alias


# ------------------------------------------- compound material names (intent)

COMPOUND_REQUEST_CASES = [
    # (text, expected item, a SHORTER suffix that is also a valid alias for a
    #  DIFFERENT item - the old shortest-suffix resolution picked these)
    ("我需要一把钻石剑", "diamond_sword", "stone_sword"),
    ("给我一把下界合金剑", "netherite_sword", "golden_sword"),
    ("我需要一件钻石头盔", "diamond_helmet", "stone_helmet"),
    ("我需要一把钻石斧", "diamond_axe", "stone_axe"),
    # plain material names must keep resolving as themselves
    ("给我一把石剑", "stone_sword", None),
    ("给我一把铁剑", "iron_sword", None),
    ("给我一个面包", "bread", None),
]


@pytest.mark.parametrize("text,expected,wrong", COMPOUND_REQUEST_CASES)
def test_compound_material_names_resolve_to_the_longest_alias(text, expected, wrong):
    intent = parse_task_intent(text)
    assert intent is not None, text
    assert intent["item"] == expected, (text, intent["item"])
    if wrong:
        assert intent["item"] != wrong, (text, intent["item"])


PILLAR_CASES = [
    ("向上搭", "pillar 2"),
    ("向上搭方块", "pillar 2"),
    ("搭方块上来", "pillar 2"),
    ("垫一脚", "pillar 2"),
]


@pytest.mark.parametrize("text,expected", PILLAR_CASES)
def test_pillar_escape_phrases(text, expected):
    assert parse_intent(text) == expected
