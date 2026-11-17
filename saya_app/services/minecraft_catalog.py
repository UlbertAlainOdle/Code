"""Unified Minecraft capability catalog (2026-09 plan section 2.3).

Single source of truth shared by:

- the desktop companion console (buttons and parameterized panels),
- ConversationService's LLM tool description and command whitelist,
- argument validation for LLM-generated direct actions,
- the help text and the cross-language contract tests.

Every entry names the exact verb the Mineflayer worker's ``command.mjs``
parses (``worker_verb``), so a contract test can prove that the catalog never
promises an action the worker cannot execute.  ``minecraft_worker/
command_catalog.json`` is generated from this module (scripts/
export_command_catalog.py) and asserted in sync by both the Python and the
Node test suites.
"""

from __future__ import annotations

import json
import re

# Verbs command.mjs's parseCommand can produce (excluding the generic 'say'
# fallback which is represented by the 'say' catalog entry).
WORKER_VERBS = {
    "follow", "unfollow", "come", "stop", "cancel_task", "status", "say", "raw",
    "goto", "jump", "swim",
    "chop", "collect", "mine_at", "mine_below", "mine_nearby",
    "inventory", "equip", "drop", "give", "place", "pickup", "eat",
    "craft", "smelt", "brew", "cauldron", "attack", "auto_discard", "milk",
    "pillar",
    "chest_find", "chest_deposit", "chest_withdraw",
}

CATEGORIES = {
    "control": "常用",
    "movement": "移动",
    "resource": "采集",
    "inventory": "背包",
    "crafting": "制作",
    "combat": "战斗",
    "chat": "聊天",
    "advanced": "高级",
}

CATALOG = [
    {
        "name": "come",
        "label": "过来",
        "category": "control",
        "worker_verb": "come",
        "arguments": {"target": {"type": "player", "required": False, "default": "Doctor"}},
        "supports_llm": True,
        "experimental": False,
        "description": "走到指定玩家身边",
    },
    {
        "name": "follow",
        "label": "持续跟随",
        "category": "control",
        "worker_verb": "follow",
        "arguments": {"target": {"type": "player", "required": False, "default": "Doctor"}},
        "supports_llm": True,
        "experimental": False,
        "description": "开启持久跟随，任务期间自动暂停并恢复",
    },
    {
        "name": "unfollow",
        "label": "停止跟随",
        "category": "control",
        "worker_verb": "unfollow",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "关闭持久跟随",
    },
    {
        "name": "stop",
        "label": "停下",
        "category": "control",
        "worker_verb": "stop",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "停止任务、跟随并退出紧急状态",
    },
    {
        "name": "cancel_task",
        "label": "取消当前任务",
        "category": "control",
        "worker_verb": "cancel_task",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "只取消当前任务并保留持续跟随开关",
    },
    {
        "name": "status",
        "label": "状态",
        "category": "control",
        "worker_verb": "status",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "汇报当前任务、跟随与生命状态",
    },
    {
        "name": "auto_discard",
        "label": "自动交还非任务拾取物",
        "category": "control",
        "worker_verb": "auto_discard",
        "arguments": {"mode": {"type": "enum", "required": True, "values": ["on", "off", "status"]}},
        "supports_llm": False,   # automation toggle; deterministic layer only
        "experimental": False,
        "description": "把非任务的掉落物自动带给主人（on / off / status）",
    },
    {
        "name": "goto",
        "label": "前往坐标",
        "category": "movement",
        "worker_verb": "goto",
        "arguments": {
            "x": {"type": "integer", "required": True, "min": -30000000, "max": 30000000},
            "y": {"type": "integer", "required": True, "min": -64, "max": 320},
            "z": {"type": "integer", "required": True, "min": -30000000, "max": 30000000},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "走到指定坐标附近",
    },
    {
        "name": "jump",
        "label": "跳一下",
        "category": "movement",
        "worker_verb": "jump",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "原地跳跃一次",
    },
    {
        "name": "swim",
        "label": "上浮",
        "category": "movement",
        "worker_verb": "swim",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "在水中向水面游动",
    },
    {
        "name": "chop",
        "label": "砍树",
        "category": "resource",
        "worker_verb": "chop",
        "arguments": {"count": {"type": "integer", "required": False, "default": 4, "min": 1, "max": 16}},
        "supports_llm": True,
        "experimental": False,
        "description": "采集若干原木（自动含工具准备规划）",
    },
    {
        "name": "collect",
        "label": "收集方块",
        "category": "resource",
        "worker_verb": "collect",
        "arguments": {
            "block": {"type": "block", "required": True},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 32},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "寻找并采集指定方块（挖矿类会自动准备工具）",
    },
    {
        "name": "mine",
        "label": "挖掘坐标",
        "category": "resource",
        "worker_verb": "mine_at",
        "arguments": {
            "x": {"type": "integer", "required": True, "min": -30000000, "max": 30000000},
            "y": {"type": "integer", "required": True, "min": -64, "max": 320},
            "z": {"type": "integer", "required": True, "min": -30000000, "max": 30000000},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "接近并挖掘指定坐标的方块",
    },
    {
        "name": "mine_below",
        "label": "挖脚下",
        "category": "resource",
        "worker_verb": "mine_below",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "挖掘脚下方块",
    },
    {
        "name": "mine_nearby",
        "label": "挖附近方块（实验性）",
        "category": "resource",
        "worker_verb": "mine_nearby",
        "arguments": {},
        "supports_llm": True,
        "experimental": True,
        "description": "挖掘视线或附近可达方块；目标识别尚不稳定",
    },
    {
        "name": "inventory",
        "label": "查看背包",
        "category": "inventory",
        "worker_verb": "inventory",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "汇报背包概况（完整明细见状态面板）",
    },
    {
        "name": "chest_find",
        "label": "查找附近箱子",
        "category": "inventory",
        "worker_verb": "chest_find",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "列出 32 格内的箱子/陷阱箱/木桶（名称、坐标、距离）",
    },
    {
        "name": "chest_deposit",
        "label": "存入箱子",
        "category": "inventory",
        "worker_verb": "chest_deposit",
        "arguments": {
            "item": {"type": "item", "required": True},
            "count": {"type": "integer", "required": False, "default": 0, "min": 0, "max": 640},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "走到最近的箱子/木桶并存入指定物品（count 0 或缺省=全部）",
    },
    {
        "name": "chest_withdraw",
        "label": "从箱子取出",
        "category": "inventory",
        "worker_verb": "chest_withdraw",
        "arguments": {
            "item": {"type": "item", "required": True},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 640},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "从最近的箱子/木桶取出指定物品，实际取出 min(count, 箱内数量)；要全部时填 640",
    },
    {
        "name": "equip",
        "label": "手持物品",
        "category": "inventory",
        "worker_verb": "equip",
        "arguments": {"item": {"type": "item", "required": True}},
        "supports_llm": True,
        "experimental": False,
        "description": "把背包中的物品拿到手上",
    },
    {
        "name": "pickup",
        "label": "捡起掉落物",
        "category": "inventory",
        "worker_verb": "pickup",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "捡起最近的掉落物",
    },
    {
        "name": "drop",
        "label": "丢出物品",
        "category": "inventory",
        "worker_verb": "drop",
        "arguments": {
            "item": {"type": "item", "required": False},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 640},
            "mode": {"type": "enum", "required": False, "options": ["one", "stack", "all"]},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "按物品和数量丢出；保护规则阻止误丢贵重物品",
    },
    {
        "name": "give",
        "label": "交给玩家",
        "category": "inventory",
        "worker_verb": "give",
        "arguments": {
            "item": {"type": "item", "required": True},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 640},
            "target": {"type": "player", "required": False, "default": "Doctor"},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "靠近玩家并丢出指定物品等待其拾取",
    },
    {
        "name": "eat",
        "label": "吃东西",
        "category": "inventory",
        "worker_verb": "eat",
        "arguments": {},
        "supports_llm": True,
        "experimental": False,
        "description": "吃背包里最合适的食物",
    },
    {
        "name": "craft",
        "label": "制作物品",
        "category": "crafting",
        "worker_verb": "craft",
        "arguments": {
            "item": {"type": "item", "required": True},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 64},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "制作物品；复杂链路由规划器自动准备材料和工作台",
    },
    {
        "name": "smelt",
        "label": "熔炉烧炼",
        "category": "crafting",
        "worker_verb": "smelt",
        "arguments": {
            "input": {"type": "item", "required": True},
            "output": {"type": "item", "required": True},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 8},
            "fuel": {"type": "item", "required": False},
            "fuel_count": {"type": "integer", "required": False, "default": 0, "min": 0, "max": 64},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "在普通熔炉投入原料和燃料，等待并取出实际产物",
    },
    {
        "name": "brew",
        "label": "酿造药水（实验）",
        "category": "crafting",
        "worker_verb": "brew",
        "arguments": {
            "potion": {"type": "enum", "required": True, "values": [
                "night_vision", "invisibility", "leaping", "fire_resistance",
                "swiftness", "slowness", "water_breathing", "healing", "harming",
                "poison", "regeneration", "strength", "weakness", "slow_falling",
                "strong_healing", "strong_harming", "strong_poison",
                "strong_regeneration", "strong_strength", "strong_swiftness",
                "long_night_vision", "long_invisibility", "long_fire_resistance",
                "long_swiftness", "long_water_breathing", "long_poison",
                "long_regeneration", "long_strength", "long_weakness", "long_slow_falling",
            ]},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 3},
        },
        "supports_llm": True,
        "experimental": True,
        "description": "在酿造台按真实配方逐阶段酿造 1 至 3 瓶，并核对药水类型后取出",
    },
    {
        "name": "pillar",
        "label": "向上搭方块脱困",
        "category": "movement",
        "worker_verb": "pillar",
        "arguments": {
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 8},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "跳着向脚下垫普通方块（泥土/圆石等），用于脱离深坑",
    },
    {
        "name": "milk",
        "label": "挤牛奶（实验）",
        "category": "crafting",
        "worker_verb": "milk",
        "arguments": {
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 3},
        },
        "supports_llm": True,
        "experimental": True,
        "description": "手持铁桶对附近的奶牛挤奶，核对牛奶桶数量（一桶牛奶消耗一个铁桶）",
    },
    {
        "name": "cauldron",
        "label": "炼药锅装取液体（实验）",
        "category": "crafting",
        "worker_verb": "cauldron",
        "arguments": {
            "action": {"type": "enum", "required": True, "values": ["fill", "collect"]},
            "fluid": {"type": "enum", "required": True, "values": ["water", "lava", "powder_snow"]},
        },
        "supports_llm": True,
        "experimental": True,
        "description": "用桶装取水、熔岩或细雪；方块和背包同时变化才算完成",
    },
    {
        "name": "attack",
        "label": "攻击",
        "category": "combat",
        "worker_verb": "attack",
        "arguments": {
            "target": {"type": "entity", "required": False, "default": "nearest"},
            "count": {"type": "integer", "required": False, "default": 1, "min": 1, "max": 32},
        },
        "supports_llm": True,
        "experimental": False,
        "description": "击杀指定数量的目标；仅实体死亡事件确认后才算完成",
    },
    {
        "name": "say",
        "label": "游戏内说话",
        "category": "chat",
        "worker_verb": "say",
        "arguments": {"text": {"type": "string", "required": True, "max_length": 200}},
        "supports_llm": False,   # the reply itself is already spoken in game
        "experimental": False,
        "description": "在游戏聊天里说一句话",
    },
    {
        "name": "place",
        "label": "放置方块（高级）",
        "category": "advanced",
        "worker_verb": "place",
        "arguments": {"block": {"type": "item", "required": True}},
        "supports_llm": False,   # only the deterministic planner / Doctor UI
        "experimental": False,
        "description": "在身边安全空位放置方块（供规划器与高级面板使用）",
    },
    {
        "name": "raw",
        "label": "原始命令（仅 Doctor）",
        "category": "advanced",
        "worker_verb": "raw",
        "arguments": {"text": {"type": "string", "required": True, "max_length": 200}},
        "supports_llm": False,   # slash/admin commands: trusted doctor input only
        "doctor_only": True,
        "experimental": False,
        "description": "以 Doctor 身份直发斜杠命令；绝不暴露给大模型",
    },
]

# High-level goal types the LLM may emit (plan section 7.1).  They are
# expanded by the deterministic planner, never by the model itself.
GOAL_TYPES = {"obtain", "craft", "obtain_and_deliver", "travel", "combat"}


def by_name(name):
    return next((entry for entry in CATALOG if entry["name"] == str(name)), None)


def llm_whitelist():
    """Commands the LLM may emit as direct actions (plan section 5.1: one at
    a time; never a burst of competing commands)."""
    return {entry["name"] for entry in CATALOG if entry["supports_llm"]}


def llm_tool_description():
    """Compact whitelist text embedded in the conversation system prompt."""
    lines = []
    for entry in CATALOG:
        if not entry["supports_llm"]:
            continue
        args = ", ".join(sorted(entry.get("arguments") or {})) or "-"
        flag = " [experimental]" if entry.get("experimental") else ""
        lines.append(f"- {entry['name']} ({args}){flag}: {entry['description']}")
    return "\n".join(lines)


_NAME_RE = re.compile(r"^[a-z_]{1,40}$")
_PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,24}$")


def validate_direct_action(name, arguments):
    """Validate one LLM direct action against the catalog; returns executable
    command text or None.  Raw/hidden verbs are rejected here by design."""
    entry = by_name(name)
    if not entry or not entry.get("supports_llm"):
        return None
    arguments = arguments if isinstance(arguments, dict) else {}
    name = entry["name"]

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(arguments.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _token(key, default, pattern):
        value = str(arguments.get(key) or default).strip()
        return value if pattern.match(value) else default

    if name in ("unfollow", "stop", "cancel_task", "status", "jump", "swim", "inventory",
                "pickup", "eat", "mine_below", "mine_nearby"):
        return name
    if name == "come":
        target = _token("target", "", _PLAYER_RE)
        return f"come {target}" if target else "come"
    if name == "follow":
        target = _token("target", "Doctor", _PLAYER_RE)
        return f"follow {target}"
    if name == "goto":
        try:
            x, y, z = int(arguments["x"]), int(arguments["y"]), int(arguments["z"])
        except (KeyError, TypeError, ValueError):
            return None
        return f"goto {x} {y} {z}"
    if name == "mine":
        try:
            x, y, z = int(arguments["x"]), int(arguments["y"]), int(arguments["z"])
        except (KeyError, TypeError, ValueError):
            return None
        return f"mine {x} {y} {z}"
    if name == "chop":
        return f"chop {_int('count', 4, 1, 16)}"
    if name == "collect":
        block = str(arguments.get("block") or "").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(block):
            return None
        return f"collect {block} {_int('count', 1, 1, 32)}"
    if name == "craft":
        item = str(arguments.get("item") or "").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(item):
            return None
        return f"craft {item} {_int('count', 1, 1, 64)}"
    if name == "smelt":
        input_item = str(arguments.get("input") or "").strip().lower().replace(" ", "_")
        output_item = str(arguments.get("output") or "").strip().lower().replace(" ", "_")
        fuel = str(arguments.get("fuel") or "").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(input_item) or not _NAME_RE.match(output_item):
            return None
        command = f"smelt {input_item} {output_item} {_int('count', 1, 1, 8)}"
        if fuel and _NAME_RE.match(fuel):
            command += f" {fuel} {_int('fuel_count', 0, 0, 64)}"
        return command
    if name == "brew":
        potion = str(arguments.get("potion") or "").strip().lower().replace(" ", "_")
        allowed = set((entry.get("arguments") or {}).get("potion", {}).get("values") or [])
        if potion not in allowed:
            return None
        return f"brew {potion} {_int('count', 1, 1, 3)}"
    if name == "milk":
        return f"milk {_int('count', 1, 1, 3)}"
    if name == "pillar":
        return f"pillar {_int('count', 1, 1, 8)}"
    if name == "chest_find":
        return "chest find"
    if name == "chest_deposit":
        item = str(arguments.get("item") or "").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(item):
            return None
        count = _int("count", 0, 0, 640)
        return f"deposit {item} all" if count == 0 else f"deposit {item} {count}"
    if name == "chest_withdraw":
        item = str(arguments.get("item") or "").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(item):
            return None
        count = _int("count", 1, 1, 640)
        return f"withdraw {item} {count}"
    if name == "cauldron":
        action = str(arguments.get("action") or "").strip().lower()
        fluid = str(arguments.get("fluid") or "").strip().lower()
        if action not in ("fill", "collect") or fluid not in ("water", "lava", "powder_snow"):
            return None
        return f"cauldron {action} {fluid}"
    if name == "equip":
        item = str(arguments.get("item") or "").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(item):
            return None
        return f"equip {item}"
    if name == "drop":
        item = str(arguments.get("item") or "").strip().lower().replace(" ", "_")
        mode = str(arguments.get("mode") or "").strip().lower()
        if mode in ("stack", "all"):
            return f"drop {item} all" if _NAME_RE.match(item) else "drop stack"
        if not _NAME_RE.match(item):
            return "drop one"
        return f"drop {item} {_int('count', 1, 1, 640)}"
    if name == "give":
        item = str(arguments.get("item") or "").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(item):
            return None
        target = _token("target", "Doctor", _PLAYER_RE)
        return f"give {item} {_int('count', 1, 1, 640)} to {target}"
    if name == "attack":
        target = str(arguments.get("target") or "nearest").strip().lower().replace(" ", "_")
        if not _NAME_RE.match(target):
            target = "nearest"
        count = _int("count", 1, 1, 32)
        return f"attack {target} {count}" if count > 1 else f"attack {target}"
    return None


def catalog_view():
    """JSON-serializable view served to the desktop console."""
    return {
        "version": 2,
        "categories": CATEGORIES,
        "commands": json.loads(json.dumps(CATALOG, ensure_ascii=False)),
        "goal_types": sorted(GOAL_TYPES),
    }
