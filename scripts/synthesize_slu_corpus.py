"""Synthesize a labeled SLU corpus from templates over the item catalog.

Real gameplay chat is sparse and repetitive; this script manufactures the
BULK of the fine-tune corpus deterministically (thousands of rows in
seconds), while real utterances recorded by the live capture serve as the
held-out validation set.

Labels are computed by the deterministic layers themselves
(parse_intent / parse_task_intent) and only kept when they match the
template's EXPECTED kind and canonical target - so every row is
verified-clean by construction:

    python -B scripts/synthesize_slu_corpus.py [--out PATH] [--per-slot N] [--seed 7]

Output rows use the same schema as the live corpus
(knowledge_base/slu_corpus/utterances.jsonl), so build_slu_dataset.py
consumes both files unchanged.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.config import KNOWLEDGE_BASE_DIR  # noqa: E402
from saya_app.services.conversation import parse_intent  # noqa: E402
from saya_app.services.task_intent import parse_task_intent  # noqa: E402

OWNER = "Kurosawa_Jin"

CN_NUMERALS = ["一", "二", "两", "三", "四", "五", "六", "七", "八", "九", "十"]

# Mining phrases use the block context (BLOCK_ALIASES + registry): only slots
# whose canonical resolution is verified below are kept, so this list just
# needs to be broad.
BLOCK_SLOTS = [
    ("石头", "cobblestone"), ("圆石", "cobblestone"), ("泥土", "dirt"),
    ("沙子", "sand"), ("砾石", "gravel"), ("煤矿", "coal_ore"),
    ("铁矿", "iron_ore"), ("金矿", "gold_ore"), ("铜矿", "copper_ore"),
    ("钻石矿", "diamond_ore"), ("红石矿", "redstone_ore"), ("青金石矿", "lapis_ore"),
    ("黑曜石", "obsidian"), ("木头", "_log"), ("原木", "_log"),
]

# Item slots for goal templates: Chinese alias -> canonical, taken live from
# the item catalog so new aliases are picked up automatically.
def _item_slots():
    from saya_app.services.minecraft_items import item_catalog

    slots = []
    for alias, canonical in item_catalog().alias_map.items():
        if not re.match(r"^[\u4e00-\u9fa5]{1,6}$", alias):
            continue
        if canonical in ("_log", "planks") or canonical.endswith("_ore"):
            continue  # block-context items route through collect, not goals
        slots.append((alias, canonical))
    return sorted(set(slots))


# Potion goals live in the potion registry, not the item catalog - the goal
# templates must cover them explicitly (canonical target "potion/<id>").
POTION_SLOTS = [
    ("治疗药水", "potion/healing"), ("抗火药水", "potion/fire_resistance"),
    ("夜视药水", "potion/night_vision"), ("力量药水", "potion/strength"),
    ("迅捷药水", "potion/swiftness"), ("再生药水", "potion/regeneration"),
    ("水肺药水", "potion/water_breathing"), ("跳跃药水", "potion/leaping"),
    ("缓降药水", "potion/slow_falling"),
]

POTION_CHAT_TEMPLATES = [
    "我不需要{potion}了",
    "如果去下界可能需要{potion}",
    "你会做{potion}吗",
    "刚才那瓶{potion}效果真好",
]

# English base commands the benchmark uses - a handful so English input
# does not drift after Chinese-heavy fine-tuning.
ENGLISH_DIRECT = [
    "follow Doctor", "follow me", "come", "come here", "stop",
    "cancel task", "status", "inventory", "jump", "pickup nearest",
    "chest find", "deposit iron_ingot all", "withdraw bread 3",
    "collect dirt 4", "chop 4",
]

ENGLISH_CHAT = [
    "hello there", "what a nice day", "you are doing great",
    "I do not need anything right now",
]


def _count_forms(rng):
    forms = ["1", "2", "3", "5", "8", "16", "两", "三", "五", "八", "十"]
    return rng.choice(forms)


# ---------------------------------------------------------------- templates
# Each entry: (utterance template, expected route kind).  {item} {block}
# {count} {recipient} are substituted before labeling; the deterministic
# layer must then agree with the expectation.

GOAL_TEMPLATES = [
    "给我{c}个{item}",
    "给我{item}",
    "帮我准备{c}个{item}",
    "帮我去拿{c}个{item}",
    "我想要{c}个{item}",
    "来{c}个{item}",
    "帮我弄{c}个{item}",
    "给我弄点{item}",
    "给我{c}个{item}，交给" + OWNER,
    "帮我准备{c}个{item}，{OWNER}要用",
]

DIRECT_TEMPLATES = [
    ("跟着我", "follow Doctor"),
    ("跟随我", "follow Doctor"),
    ("来我身边", "come"),
    ("过来一下", "come"),
    ("停下", "stop"),
    ("取消任务", "cancel task"),
    ("看看当前状态", "status"),
    ("附近有什么箱子", "chest find"),
    ("把{item}存进箱子", "deposit"),
    ("存{c}个{item}到箱子", "deposit"),
    ("从箱子里取出{c}个{item}", "withdraw"),
    ("把背包里的{item}存到箱子里去", "deposit"),
    ("帮我把{item}存进附近的箱子", "deposit"),
    ("给我把{item}存进箱子", "deposit"),
    ("把{item}存到木桶里", "deposit"),
    ("从箱子里拿出{c}个{item}", "withdraw"),
    ("从木桶里取出{item}", "withdraw"),
    ("向上搭方块", "pillar 2"),
    ("捡起来", "pickup nearest"),
    ("吃点东西", "eat"),
    ("去睡觉", "sleep"),
    ("跳一下", "jump"),
]

MINE_TEMPLATES = [
    "挖{c}个{block}",
    "挖点{block}",
    "给我挖{c}个{block}",
    "帮我挖{c}个{block}",
]

# Smelting/brewing are DIRECT worker commands, not planner goals - the
# first iteration underrepresented them (6 rows) and the fine-tune kept
# misrouting them as goals.  Verification drops whatever parse_intent
# does not resolve, so over-adding is safe.
SMELT_TEMPLATES = [
    "烧炼粗铁", "帮我烧粗铁", "烧木头", "烧沙子", "熔炼金锭",
    "烧3个木头", "帮我烧沙子",
    "炼{c}个铁锭", "炼{c}个金锭", "烧{c}个猪肉", "帮我把{c}个牛肉烧熟",
    "把{c}个猪排烧熟", "烧熟猪排", "烤鸡", "烤{c}个鸡肉",
]

BREW_TEMPLATES = [
    "帮我酿造一瓶治疗药水", "酿造两瓶治疗药水", "帮我酿造{c}瓶抗火药水",
    "酿一瓶治疗药水给我", "帮我酿{c}瓶夜视药水",
]

CHAT_TEMPLATES = [
    "今天好累啊",
    "你最喜欢的食物是什么",
    "刚才那场架打得真漂亮",
    "我不需要{item}了",
    "如果去下界可能需要抗火药水",
    "你会做{item}吗",
    "不要因为我提到{block}就去挖",
    "昨天你的表现不错",
    "你觉得下雨天怎么样",
    "等下记得休息一下",
]


def label_row(text, expect_kind, expect_target=None):
    """Label via the deterministic layers; verify the expectation holds."""
    command = parse_intent(text)
    if command in ("stop", "cancel task"):
        route = "task"
    elif command:
        route = "direct_action"
    else:
        try:
            intent = parse_task_intent(text)
        except Exception:
            intent = None
        if isinstance(intent, dict) and intent.get("item") and intent.get("modality") == "request":
            goal = {
                "type": "obtain_and_deliver" if intent.get("disposition") == "deliver" else "obtain",
                "target": intent.get("item"),
                "count": intent.get("count") or 1,
                "recipient": intent.get("recipient") or "Doctor",
            }
            if expect_kind != "goal" or (expect_target and goal["target"] != expect_target):
                return None
            return {
                "route": "goal", "direct_action": None, "goal": goal,
                "commands": [], "action": None,
            }
        route = "chat"

    if expect_kind == "goal":
        return None  # a goal template must NOT be swallowed by the command layer
    if expect_kind == "chat":
        # Chat must trigger NOTHING deterministic; when it does, the slot
        # filled a command pattern and the row is rejected.
        if route != "chat":
            return None
        return {
            "route": "chat", "direct_action": None, "goal": None,
            "commands": [], "action": None,
        }
    if expect_kind == "direct" and route != "direct_action":
        return None
    if expect_kind == "direct" and expect_target:
        head = command.split(" ", 1)[0]
        if not command.startswith(expect_target) and head != expect_target:
            return None
    return {
        "route": route, "direct_action": command, "goal": None,
        "commands": [command], "action": command,
    }


def to_row(text, label):
    payload = {
        "type": "chat" if label["route"] == "chat" else "instruction",
        "text": text,
        "route": label["route"],
        "direct_action": label["direct_action"],
        "goal": label["goal"],
    }
    return {
        "ts": time.time(),
        "source": "synthetic",
        "text": text,
        "route": label["route"],
        "direct_action": label["direct_action"],
        "goal": label["goal"],
        "commands": list(label["commands"]),
        "confidence": 1.0,
        "layer": "synthetic",
        "segment_payload": payload,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(KNOWLEDGE_BASE_DIR / "slu_corpus" / "synthetic.jsonl"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    item_slots = _item_slots()
    seen = set()
    rows = []

    def add(text, kind, target=None):
        text = re.sub(r"\s+", "", text)
        if not text or text in seen:
            return
        label = label_row(text, kind, target)
        if label is None:
            return
        seen.add(text)
        rows.append(to_row(text, label))

    # goals over the item catalog
    for alias, canonical in item_slots:
        for template in GOAL_TEMPLATES:
            text = template.format(c=_count_forms(rng), item=alias, OWNER=OWNER)
            add(text, "goal", canonical)
    # potion goals (registry targets)
    for alias, canonical in POTION_SLOTS:
        for template in ("给我{c}瓶{item}", "帮我准备{c}瓶{item}", "给我{item}",
                          "帮我酿造{c}瓶{item}"):
            text = template.format(c=_count_forms(rng), item=alias, OWNER=OWNER)
            add(text, "goal", canonical)

    # mining (block context)
    for block_alias, block_canonical in BLOCK_SLOTS:
        for template in MINE_TEMPLATES:
            text = template.format(c=_count_forms(rng), block=block_alias)
            add(text, "direct", "collect")

    # direct commands
    for template, expect in DIRECT_TEMPLATES:
        if "{item}" in template:
            for alias, _canonical in item_slots:
                add(template.format(item=alias, c=_count_forms(rng)), "direct", expect)
        else:
            add(template, "direct", expect)

    # smelting / brewing direct commands
    for template in SMELT_TEMPLATES:
        text = template.format(c=_count_forms(rng))
        add(text, "direct", "smelt")
    for template in BREW_TEMPLATES:
        add(template.format(c=_count_forms(rng)), "direct", "brew")

    # potion negation / conditional / question rows must stay CHAT
    for alias, _canonical in POTION_SLOTS:
        for template in POTION_CHAT_TEMPLATES:
            add(template.format(potion=alias, item=alias, block=alias), "chat")

    # mixed utterances: chat segment + instruction segment in ONE answer
    # (the v3 model answered them as single instructions; teach the split)
    MIXED_PAIRS = [
        ("你今天很好", "给我{c}个{item}"),
        ("辛苦你啦", "再帮我拿{c}个{item}"),
        ("刚才干得漂亮", "顺便去弄点{item}"),
    ]
    for chat_part, task_part in MIXED_PAIRS:
        for alias, canonical in item_slots[:20]:
            full = f"{chat_part}，{task_part.format(c=_count_forms(rng), item=alias)}"
            if label_row(full, "goal", canonical) is None:
                continue
            label = label_row(full, "goal", canonical)
            payload_chat = {"type": "chat", "text": chat_part, "route": "chat",
                            "direct_action": None, "goal": None}
            payload_task = dict(label["segment_payload"] if "segment_payload" in label
                                else {"type": "instruction", "text": full,
                                      "route": "goal", "direct_action": None,
                                      "goal": label["goal"]})
            payload_task["type"] = "mixed"
            payload_task["text"] = full
            row = to_row(full, label)
            row["segment_payload"] = payload_task
            seen.discard(full)
            # store as a two-segment answer for the dataset builder
            row["mixed_segments"] = [payload_chat, payload_task]
            seen.add(full)
            rows.append(row)

    # english anchors
    for text in ENGLISH_DIRECT:
        add(text, "direct")
    for text in ENGLISH_CHAT:
        add(text, "chat")

    # chat (must trigger nothing)
    for template in CHAT_TEMPLATES:
        if "{item}" in template or "{block}" in template:
            for alias, _canonical in item_slots[:25]:
                add(template.format(item=alias, block=alias), "chat")
            for block_alias, _b in BLOCK_SLOTS[:10]:
                add(template.format(item=block_alias, block=block_alias), "chat")
        else:
            add(template, "chat")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_route = {}
    for row in rows:
        by_route[row["route"]] = by_route.get(row["route"], 0) + 1
    print(f"synthetic corpus: {len(rows)} rows -> {out}")
    print("  by route:", json.dumps(by_route, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
