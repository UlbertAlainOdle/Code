"""Catalog contract for the chest/storage verbs (find, deposit, withdraw)."""

from saya_app.services.minecraft_catalog import validate_direct_action
from saya_app.services.conversation import parse_intent


def test_chest_find_builds_the_scan_command():
    assert validate_direct_action("chest_find", {}) == "chest find"


def test_parse_intent_recognizes_chest_phrases():
    """New verbs must be reachable from plain text, not just the catalog."""
    assert parse_intent("chest find") == "chest find"
    assert parse_intent("附近有什么箱子") == "chest find"
    assert parse_intent("deposit iron_ingot all") == "deposit iron_ingot all"
    assert parse_intent("withdraw bread 3") == "withdraw bread 3"
    assert parse_intent("把铁锭存进箱子") == "deposit iron_ingot all"
    assert parse_intent("存3个铁锭到箱子") == "deposit iron_ingot 3"
    assert parse_intent("从箱子里取出3个面包") == "withdraw bread 3"
    # ordinary chat mentioning a chest must not trigger anything
    assert parse_intent("那个箱子里原来有很多金子") is None


def test_chest_deposit_defaults_to_all():
    assert validate_direct_action("chest_deposit", {"item": "iron_ingot"}) == "deposit iron_ingot all"
    assert validate_direct_action("chest_deposit", {"item": "iron_ingot", "count": 5}) == "deposit iron_ingot 5"


def test_chest_deposit_rejects_bad_item_names():
    # Same contract as equip/collect: name-shaped strings pass through (the
    # worker answers honestly for unknown items), malformed ones are refused.
    assert validate_direct_action("chest_deposit", {"item": "drop table!"}) is None
    assert validate_direct_action("chest_deposit", {"item": ""}) is None
    assert validate_direct_action("chest_deposit", {"item": "not_an_item"}) == "deposit not_an_item all"


def test_chest_withdraw_builds_a_bounded_command():
    assert validate_direct_action("chest_withdraw", {"item": "bread", "count": 3}) == "withdraw bread 3"
    assert validate_direct_action("chest_withdraw", {"item": "bread"}) == "withdraw bread 1"
    # count clamps to the worker's 640 ceiling ("all")
    assert validate_direct_action("chest_withdraw", {"item": "bread", "count": 9999}) == "withdraw bread 640"
