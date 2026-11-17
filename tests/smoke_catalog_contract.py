"""Contract tests for the unified Minecraft CommandCatalog (plan section 2.3).

Guarantees:
- every catalog entry maps to a verb the Mineflayer worker actually parses,
- every worker verb is represented in the catalog (no dark capabilities),
- the LLM whitelist never includes worker-unsupported or doctor-only verbs,
- experimental capabilities are visibly flagged,
- the exported minecraft_worker/command_catalog.json is in sync.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services.minecraft_catalog import (  # noqa: E402
    CATALOG,
    GOAL_TYPES,
    WORKER_VERBS,
    catalog_view,
    llm_whitelist,
    llm_tool_description,
    validate_direct_action,
)

EXPORT_PATH = ROOT / "minecraft_worker" / "command_catalog.json"


def test_catalog_covers_worker_verbs():
    verbs = {entry["worker_verb"] for entry in CATALOG}
    assert verbs == WORKER_VERBS, (
        f"catalog/worker verb mismatch: missing={WORKER_VERBS - verbs}, "
        f"extra={verbs - WORKER_VERBS}"
    )


def test_llm_whitelist_safe():
    whitelist = llm_whitelist()
    by_name = {entry["name"]: entry for entry in CATALOG}
    for name in whitelist:
        entry = by_name[name]
        assert entry["worker_verb"] in WORKER_VERBS
        assert not entry.get("doctor_only"), f"{name} is doctor-only"
    # raw is the doctor-only escape hatch and must never be LLM-visible
    assert "raw" not in whitelist
    assert "say" not in whitelist or by_name["say"]["supports_llm"] is False


def test_experimental_flagging():
    experimental = [e for e in CATALOG if e.get("experimental")]
    assert experimental, "at least one experimental capability expected"
    for entry in experimental:
        assert "实验" in entry["label"], entry["name"]


def test_goal_types():
    assert GOAL_TYPES == {"obtain", "craft", "obtain_and_deliver", "travel", "combat"}


def test_tool_description_mentions_every_llm_command():
    text = llm_tool_description()
    for name in llm_whitelist():
        assert name in text


def test_validate_direct_action_samples():
    assert validate_direct_action("come", {}) == "come"
    assert validate_direct_action("follow", {"target": "Kurosawa_Jin"}) == "follow Kurosawa_Jin"
    assert validate_direct_action("follow", {"target": "bad name!"}) == "follow Doctor"
    assert validate_direct_action("goto", {"x": 10, "y": -60, "z": 32}) == "goto 10 -60 32"
    assert validate_direct_action("goto", {"x": "?"}) is None
    assert validate_direct_action("collect", {"block": "iron_ore", "count": 3}) == "collect iron_ore 3"
    assert validate_direct_action("collect", {"block": "IRON ORE", "count": 99}) == "collect iron_ore 32"
    assert validate_direct_action("attack", {"target": "zombie", "count": 3}) == "attack zombie 3"
    assert validate_direct_action("cancel_task", {}) == "cancel_task"
    assert validate_direct_action("smelt", {
        "input": "raw_iron", "output": "iron_ingot", "count": 3,
    }) == "smelt raw_iron iron_ingot 3"
    assert validate_direct_action("craft", {"item": "wooden_sword", "count": 1}) == "craft wooden_sword 1"
    assert validate_direct_action("give", {"item": "bread", "count": 2}) == "give bread 2 to Doctor"
    assert validate_direct_action("drop", {"item": "rotten_flesh", "count": 5}) == "drop rotten_flesh 5"
    assert validate_direct_action("equip", {"item": "wooden_pickaxe"}) == "equip wooden_pickaxe"
    assert validate_direct_action("attack", {}) == "attack nearest"
    assert validate_direct_action("attack", {"target": "zombie"}) == "attack zombie"
    # hidden / unsupported verbs are rejected outright
    assert validate_direct_action("raw", {"text": "/tp"}) is None
    assert validate_direct_action("place", {"block": "crafting_table"}) is None
    assert validate_direct_action("teleport", {}) is None


def test_exported_json_in_sync():
    import scripts.export_command_catalog as exporter  # noqa: E402

    expected = catalog_view()
    expected["worker_verbs"] = sorted({e["worker_verb"] for e in expected["commands"]})
    on_disk = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    assert on_disk == expected, (
        "minecraft_worker/command_catalog.json is stale; run "
        "python -B scripts/export_command_catalog.py"
    )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name}: OK")
    print("\nCommandCatalog contract tests passed.")
