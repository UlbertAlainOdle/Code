"""Conversation service shared by web chat, Minecraft chat and the desktop
companion console.

One persona, one Doctor identity and one long-term memory store serve every
frontend. Transcript files stay separated: web/desktop use the web session;
Minecraft uses one session per world join. Messages are routed through three
layers (2026-09 plan section 7):

1. emergency rule layer  - survival/stop actions execute instantly, no API
2. deterministic command layer - standard English commands and recommended
   Chinese phrasings execute locally (< 1s) WITH a persona-style reply
3. large-model layer - the model UNDERSTANDS GOALS, it does not script
   actions: it classifies the message (chat / direct_action / goal / mixed /
   clarify) and may emit one high-level goal; recipes, inventory math and
   step-by-step execution belong to the deterministic planner
   (minecraft_planner), never to the model.

The Minecraft worker never calls an LLM itself and never stores persona
memory; it only executes structured actions.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

import requests

from ..config import (
    DEFAULT_API_MODEL,
    DEFAULT_API_URL,
    KNOWLEDGE_BASE_DIR,
    PROFILE_PATH,
)
from .minecraft_catalog import (
    GOAL_TYPES,
    llm_tool_description,
    validate_direct_action,
)
from . import potions
from .task_intent import intent_to_goal, parse_task_intent

CREDENTIALS_FILE = Path(
    os.environ.get("SAYA_API_CREDENTIALS_FILE")
    or KNOWLEDGE_BASE_DIR / "api_credentials.json"
)
# Web and desktop have their own shared session. Minecraft conversations use
# a per-join session supplied by the game worker and must never fall back to
# this file.
SESSION_ID = "session_web_unified.jsonl"
HISTORY_TURNS = 12
MEMORY_LIMIT = 6
MAX_REPLY_CHARS = 220
LLM_TIMEOUT = (10, 60)

HAS_CJK = re.compile(r"[\u3400-\u9fff]")

BLOCK_ALIASES = {
    "木头": "_log", "原木": "_log", "树": "_log", "树干": "_log",
    # 石头默认=圆石（生存稳定获得）；要原版 stone 需走澄清。
    "石头": "cobblestone", "岩石": "cobblestone",
    "泥土": "dirt", "土": "dirt", "圆石": "cobblestone", "沙子": "sand", "砂岩": "sandstone",
    "砂砾": "gravel", "碎石": "gravel",
    "煤": "coal_ore", "煤矿": "coal_ore", "铁": "iron_ore", "铁矿": "iron_ore",
    "铜": "copper_ore", "铜矿": "copper_ore", "金": "gold_ore", "金矿": "gold_ore",
    "钻石": "diamond_ore", "钻石矿": "diamond_ore",
    "红石": "redstone_ore", "红石矿": "redstone_ore",
    "青金石": "lapis_ore", "绿宝石": "emerald_ore", "绿宝石矿": "emerald_ore",
    "黏土": "clay", "木板": "planks", "木棍": "stick",
    "工作台": "crafting_table", "合成台": "crafting_table",
    "glass": "glass", "羊毛": "wool",
}

MOB_ALIASES = {
    "僵尸": "zombie", "骷髅": "skeleton", "苦力怕": "creeper", "爬行者": "creeper",
    "蜘蛛": "spider", "末影人": "enderman", "女巫": "witch", "史莱姆": "slime",
    "溺尸": "drowned", "尸壳": "husk", "流浪者": "stray", "卫道士": "vindicator",
    "掠夺者": "pillager", "幻翼": "phantom",
    "哞菇": "mooshroom", "兔子": "rabbit", "山羊": "goat", "奶牛": "cow",
    "牛": "cow", "猪": "pig", "绵羊": "sheep", "羊": "sheep", "鸡": "chicken",
    "怪物": "nearest", "怪": "nearest", "它": "nearest",
}

SMELT_INPUT_FOR_OUTPUT = {
    "iron_ingot": "raw_iron", "gold_ingot": "raw_gold", "copper_ingot": "raw_copper",
    "glass": "sand", "charcoal": "_log", "cooked_beef": "beef",
    "cooked_porkchop": "porkchop", "cooked_chicken": "chicken",
    "cooked_mutton": "mutton", "baked_potato": "potato", "brick": "clay_ball",
    "terracotta": "clay", "green_dye": "cactus",
}
# Inverse view (raw material -> product) so smelting requests that name the
# INPUT ("烧粗铁", "烧木头") resolve to the product goal instead of dead-
# ending or - worse - being read as a request for more raw material.
SMELT_PRODUCT_FOR_INPUT = {
    source: output for output, source in SMELT_INPUT_FOR_OUTPUT.items()
}

POTION_ALIASES = {
    "治疗药水": "healing", "瞬间治疗药水": "healing", "强效治疗药水": "strong_healing",
    "力量药水": "strength", "强效力量药水": "strong_strength", "延长力量药水": "long_strength",
    "迅捷药水": "swiftness", "速度药水": "swiftness", "强效迅捷药水": "strong_swiftness",
    "延长迅捷药水": "long_swiftness", "抗火药水": "fire_resistance", "防火药水": "fire_resistance",
    "延长抗火药水": "long_fire_resistance", "水肺药水": "water_breathing",
    "延长水肺药水": "long_water_breathing", "夜视药水": "night_vision",
    "延长夜视药水": "long_night_vision", "隐身药水": "invisibility",
    "延长隐身药水": "long_invisibility", "剧毒药水": "poison",
    "强效剧毒药水": "strong_poison", "延长剧毒药水": "long_poison",
    "再生药水": "regeneration", "强效再生药水": "strong_regeneration",
    "延长再生药水": "long_regeneration", "伤害药水": "harming",
    "瞬间伤害药水": "harming", "强效伤害药水": "strong_harming",
    "虚弱药水": "weakness", "延长虚弱药水": "long_weakness",
    "缓降药水": "slow_falling", "延长缓降药水": "long_slow_falling",
    "跳跃药水": "leaping", "缓慢药水": "slowness",
}

NUMBER_WORDS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "几": 4,
}

# Layer 1: executed before anything else, never waits for an API call.
EMERGENCY_PATTERNS = re.compile(
    r"^\s*(?:stop|stay|wait)\s*$|停下|停止|别动|不要动|快停下|立刻停"
)

POLITE_PREFIX = re.compile(r"^(?:请你?|麻烦你?|帮我|帮忙|能不能|可不可以|可以|劳驾|拜托)+")
TRAILING_PARTICLES = re.compile(r"[吧啊呀呢啦吗。！!？?~～\s]+$")

NEGATION_PREFIX = re.compile(r"^(别|不要|不用|无需|不想|没必要)")

# Persona-style replies for deterministic commands (plan 7.4): the fast path
# still skips the API, but Saya always answers naturally.
DETERMINISTIC_REPLIES = {
    "stop": "明白，我停下了。",
    "cancel task": "好，我取消当前任务，跟随状态保持不变。",
    "unfollow": "好，我不跟着你了。",
    "follow Doctor": "嗯，我会一直跟着你。",
    "come": "好，我这就过来。",
    "status": "我看一下自己的状态哈。",
    "jump": "好，跳！",
    "swim up": "好，我向水面游。",
    "pickup nearest": "我去把掉落物捡起来。",
    "eat": "好，我吃点东西。",
    "sleep": "好，我找张床睡下，晚安主人。",
    "mine below": "好，我挖脚下这块。",
    "mine nearby": "好，我试试挖附近的方块（这个还不太稳）。",
    "drop one": "好，我扔一个出去。",
    "drop stack": "好，我扔一组出去。",
    "auto_discard on": "好，非任务拾取物会自动带给你。",
    "auto_discard off": "好，自动交还非任务拾取物已关闭。",
}


def _contains_cjk(text):
    return bool(HAS_CJK.search(str(text or "")))


def _strip_sender(text):
    return re.sub(r"^<[^>]{1,80}>\s*", "", str(text or "").strip())


def _number(value, fallback=1, maximum=32):
    raw = str(value or "")
    digit = re.search(r"\d{1,3}", raw)
    if digit:
        return max(1, min(maximum, int(digit.group(0))))
    for word, number in NUMBER_WORDS.items():
        if word in raw:
            return min(maximum, number)
    if "点" in raw:
        return min(maximum, 8)  # "挖点石头": an unspecified small amount
    return fallback


def _item_name(text):
    """Canonical ITEM name for item-context commands (equip/craft/smelt).

    Resolution goes to the registry item catalog FIRST and never consults
    block aliases.  The old shared helper matched BLOCK_ALIASES by substring
    ("金" in "金头盔" -> gold_ore), which hijacked every item whose Chinese
    name contained an ore word: 装备金头盔 became equip gold_ore,
    制作金苹果 became craft gold_ore.  Block aliases only make sense for
    mining/collect phrasing, which keeps using _block_name.
    """
    from .minecraft_items import item_catalog

    name = str(text or "").strip().lower().replace(" ", "_").removeprefix("minecraft:")
    canonical = item_catalog().lookup(name) if name else None
    return canonical or name or ""


def _block_name(text):
    """Canonical block/item name for BLOCK-context commands (collect/mine):
    block aliases first (挖金 -> gold_ore), then the registry item catalog."""
    name = str(text or "").strip().lower().replace(" ", "_").removeprefix("minecraft:")
    for word, mapped in BLOCK_ALIASES.items():
        if word in name:
            return mapped
    from .minecraft_items import item_catalog

    canonical = item_catalog().lookup(name)
    if canonical:
        return canonical
    return name or "stone"


def _coords(text):
    values = re.findall(r"-?\d{1,6}", str(text or ""))
    if len(values) < 3:
        return None
    return [int(value) for value in values[:3]]


def parse_intent(raw_text):
    """Deterministic layer: standard commands and recommended phrasings.

    Returns a command string ("follow Doctor", "collect dirt 4", ...) or None.
    Must stay fast (< 1 ms) and avoid false positives on ordinary chat: every
    Chinese pattern is anchored at the start of the sentence (polite prefixes
    and trailing particles are stripped first), so ordinary chat that merely
    mentions a keyword never triggers a command.
    """
    text = _strip_sender(raw_text)
    text = POLITE_PREFIX.sub("", text)
    text = TRAILING_PARTICLES.sub("", text).strip()
    text = re.sub(r"^请你", "", text).strip()
    if not text:
        return None
    lower = text.lower()

    # Layer-1 emergency commands first.
    if re.match(r"^\s*cancel(?:\s+task)?\s*$|^(?:取消|放弃)(?:当前|这个)?任务$", text, re.IGNORECASE):
        return "cancel task"
    if EMERGENCY_PATTERNS.search(text):
        return "stop"

    # Follow / unfollow -----------------------------------------------------
    if re.search(
        r"^\s*unfollow\s*$|^\s*stop\s+following\s*$|"
        r"^(?:快|赶紧|马上)?(?:取消跟随|关闭跟随|不要跟了|别跟了|不用跟了|别跟着我)$",
        text,
    ):
        return "unfollow"
    if re.search(
        r"^\s*follow(?:\s+(?:me|us))?\s*$|^\s*follow\s+[A-Za-z0-9_]{1,24}\s*$|"
        r"^持续跟随我$|^跟着我$|^跟随我$|^跟着主人$|^开启跟随$|^跟随$|^跟着$",
        text,
    ):
        return "follow Doctor"

    # Negated sentences ("别跟着我看风景") are ordinary chat, not commands.
    if NEGATION_PREFIX.match(text):
        return None

    if re.search(
        r"^\s*come(?:\s+(?:here|to me))?\s*$|^\s*come\s+[A-Za-z0-9_]{1,24}\s*$|"
        r"^过来(?:一下)?$|^来我身边$|^来我这里$|^到我身边$|^到我这里$|^靠近我$",
        text,
    ):
        return "come"
    if re.search(r"^\s*status\s*$|^\s*state\s*$|^当前状态$|^任务进度$|^什么状态$|^做到哪$", text):
        return "status"
    if re.search(r"^\s*auto[_ ]?discard\s+(?:on|enable)\s*$|^(?:开启|打开)自动(?:丢弃|交还)$", text, re.I):
        return "auto_discard on"
    if re.search(r"^\s*auto[_ ]?discard\s+(?:off|disable)\s*$|^(?:关闭|停止)自动(?:丢弃|交还)$", text, re.I):
        return "auto_discard off"

    coords = _coords(text)
    if re.search(r"^\s*(?:goto|walk)\s", lower) or re.search(r"^去坐标|^前往坐标", text):
        if coords:
            return f"goto {coords[0]} {coords[1]} {coords[2]}"

    if re.search(
        r"^\s*(?:chop|woodcut)\b|"
        r"^砍(?:\d+|[一两二三四五六七八九十几]+)?块?(?:原木|木头)$|^砍点树$|^砍树$|^伐木$",
        text,
    ):
        return f"chop {_number(text, 4, 16)}"

    # drop / pickup / eat / jump / swim --------------------------------------
    if re.search(r"^\s*drop\s+(?:stack|all)\s*$|^扔掉一组$|^丢掉一组$|^扔一组$", lower):
        return "drop stack"
    if re.search(r"^\s*drop(?:\s+(?:one|item|it))?\s*$|^扔一个$|^丢一个$|^扔出一件$|^扔出$|^丢出$", lower):
        return "drop one"
    if re.search(r"^\s*pick\s*up\b|^捡起来$|^捡东西$|^拾取$|^捡物品$|^捡起附近", text):
        return "pickup nearest"
    if re.search(r"^\s*(?:eat|food)\b|^吃东西$|^吃点东西$|^进食$|^吃点$|^吃个?饭$", lower):
        return "eat"
    if re.search(r"^\s*(?:sleep|go[\s-]?to[\s-]?bed|bed)\b|^去睡觉$|^睡觉$|^上床睡觉$|^躺下睡$|^睡一觉$", lower):
        return "sleep"
    if re.search(r"^\s*(?:jump|hop)\b|^跳一下$|^跳起来$|^跳一跳$", lower):
        return "jump"
    if re.search(r"^\s*(?:swim|float)\b|^向上游$|^浮起来$|^游上去$|^游到水面$", lower):
        return "swim up"

    # pillar escape ----------------------------------------------------------
    if re.search(r"^向上(?:搭|垫)|^搭方块(?:上来|向上)?$|^垫一?脚$|^搭个方块$", text):
        return "pillar 2"

    # milking ----------------------------------------------------------------
    if re.search(
        r"^挤(?:\d+|[一两二三])?(?:桶)?牛奶$|^挤奶$|^取牛奶$"
        r"|^给(?:我)?(?:挤|弄)(?:一桶|点)?牛奶$|^帮我挤(?:\d+|[一两二三])?(?:桶)?牛奶$",
        text,
    ):
        # "弄点牛奶" means "one bucket, please" - drop the vague 点 before
        # counting, or the generic small-amount rule would milk three.
        return f"milk {_number(re.sub(r'点', '', text), 1, 3)}"

    # inventory ---------------------------------------------------------------
    if re.search(r"^\s*(?:inventory|inv|items)\s*$|^查看背包$|^背包$|^我有什么$", text, re.IGNORECASE):
        return "inventory"
    equip_en = re.match(r"^\s*equip\s+(\S+)\s*$", text, re.IGNORECASE)
    if equip_en:
        return f"equip {_item_name(equip_en.group(1))}"
    equip_cn = re.match(r"^(?:手持|装备|拿着)(?:一下)?([\u4e00-\u9fa5]{1,8}|[a-z_]+)$", text)
    if equip_cn:
        item = _item_name(equip_cn.group(1))
        if re.match(r"^[a-z_]+$", item):
            return f"equip {item}"
    give_en = re.match(r"^\s*give\s+(.+)$", text, re.IGNORECASE)
    if give_en:
        return f"give {give_en.group(1).strip()[:80]}"
    drop_en = re.match(r"^\s*drop\s+([a-z_]+)(?:\s+(\d{1,3}|all))?\s*$", text, re.IGNORECASE)
    if drop_en:
        amount = drop_en.group(2) or "1"
        return f"drop {drop_en.group(1)} {amount}"

    # chest storage ------------------------------------------------------------
    # chest find ---------------------------------------------------------------
    if re.search(
        r"^\s*chest(?:\s+(?:find|list|nearby))?\s*$"
        r"|^附近(?:都)?有(?:什么|哪些)?箱[子吗么]|^找(?:一?下)?附近(?:的)?箱[子桶]|^列出附近箱[子桶]$",
        text, re.IGNORECASE,
    ):
        return "chest find"
    deposit_en = re.match(
        r"^\s*deposit\s+([a-z_]+)(?:\s+(\d{1,3}|all))?\s*$", text, re.IGNORECASE)
    if deposit_en:
        return f"deposit {deposit_en.group(1)} {deposit_en.group(2) or 'all'}"
    withdraw_en = re.match(
        r"^\s*withdraw\s+([a-z_]+)(?:\s+(\d{1,3}|all))?\s*$", text, re.IGNORECASE)
    if withdraw_en:
        return f"withdraw {withdraw_en.group(1)} {withdraw_en.group(2) or '1'}"
    # 存 (默认=全部)：把铁锭存进箱子 / 把背包里的圆石存到箱子里去 / 存3个铁锭到箱子
    deposit_cn = re.match(
        r"^(?:把)?(?:背包里|身上)?(?:的)?(?:(全部|所有|(\d{1,3}))个?)?([\u4e00-\u9fa5]{1,8}|[a-z_]+)"
        r"存(?:进|入|到|去)(?:附近的|最近的?)?(?:箱子|木桶|陷阱箱)(?:里|里去|中)?$",
        text)
    if deposit_cn:
        item = _item_name(deposit_cn.group(3))
        if re.match(r"^[a-z_]+$", item):
            count = deposit_cn.group(1)
            return f"deposit {item} all" if not count or count in ("全部", "所有") \
                else f"deposit {item} {count}"
    # 存 N 个 ITEM 到 容器 / 存 ITEM 到 容器（默认全部）
    deposit_cn2 = re.match(
        r"^存(?:(全部|所有|(\d{1,3}))个?)?([\u4e00-\u9fa5]{1,8}|[a-z_]+)"
        r"(?:进|入|到|去)(?:最近的?)?(?:箱子|木桶|陷阱箱)(?:里|里去|中)?$",
        text)
    if deposit_cn2:
        item = _item_name(deposit_cn2.group(3))
        if re.match(r"^[a-z_]+$", item):
            count = deposit_cn2.group(1)
            return f"deposit {item} all" if not count or count in ("全部", "所有") \
                else f"deposit {item} {count}"
    # 取：从箱子里取出3个铁锭 / 从木桶拿出铁锭 / 取出面包
    withdraw_cn = re.match(
        r"^(?:从)(?:最近的?)?(?:箱子|木桶|陷阱箱)(?:里|中)?(?:取出|拿出|拿)"
        r"(?:(\d{1,3})个?)?([\u4e00-\u9fa5]{1,8}|[a-z_]+)$", text)
    if withdraw_cn:
        item = _item_name(withdraw_cn.group(2))
        if re.match(r"^[a-z_]+$", item):
            count = withdraw_cn.group(1) or "1"
            return f"withdraw {item} {count}"

    # say --------------------------------------------------------------------
    say_match = re.search(r'^\s*(?:say|chat|tell)\s+(.+)$', text, re.IGNORECASE)
    if say_match:
        return f"say {say_match.group(1).strip()[:200]}"
    say_cn = re.search(r'^说[“"『「]?(.{1,180})[”"』」]?$', text)
    if say_cn:
        return f"say {say_cn.group(1).strip()}"

    # attack -----------------------------------------------------------------
    english_attack = re.match(
        r"^\s*(?:attack|fight|kill)(?:\s+[a-z_]+)?(?:\s+\d{1,2})?\s*$",
        lower,
    )
    entity_words = "|".join(
        re.escape(word) for word in sorted(MOB_ALIASES, key=len, reverse=True)
    )
    chinese_attack = re.match(
        rf"^(?:攻击|击杀|消灭|杀死|打)"
        rf"(?:\d{{1,2}}|[一两二三四五六七八九十几]+)?(?:只|个)?"
        rf"(?:那个|这个|那只|这只)?(?:{entity_words})$",
        text,
    )
    if english_attack or chinese_attack or text in ("反击", "揍它"):
        target = "nearest"
        count = _number(text, 1, 32)
        for word, mapped in MOB_ALIASES.items():
            if word in text:
                target = mapped
                break
        else:
            english = re.search(r"(?:attack|fight|kill)\s+([a-z_ ]+?)(?:\s+\d+)?\s*$", lower)
            if english:
                target = english.group(1).strip().replace(" ", "_") or "nearest"
        return f"attack {target} {count}" if count > 1 else f"attack {target}"

    # mine at coords / below / nearby ----------------------------------------
    if (re.search(r"^\s*mine\s+-?\d", lower) or re.search(r"^挖坐标|^在坐标", text)) and coords:
        return f"mine {coords[0]} {coords[1]} {coords[2]}"
    if re.search(r"^\s*mine\s+below\b|^\s*dig\s+below\b|^挖脚下$|^挖脚下的$", lower):
        return "mine below"
    if re.search(r"^\s*mine\s+nearby\b|^\s*mine\s+this\b|^挖这个$|^挖这个方块$|^挖一下这个$", lower):
        return "mine nearby"

    # craft ------------------------------------------------------------------
    craft = re.search(r"^\s*(?:craft|make)\s+(.+)$", text, re.IGNORECASE)
    craft_cn = re.search(
        r"^(?:制作|合成|做)(\d+|[一两二三四五六七八九十几]+)?(?:个|把|支|件|条|张|块|只)?(?:的)?"
        r"([\u4e00-\u9fa5]{1,6}|[a-z_ ]+)$",
        text,
    )
    if (craft or craft_cn) and not re.search(r"药水|potion", lower):
        if craft:
            body = craft.group(1)
            count = _number(body, 1, 64)
            item = _item_name(re.sub(r"\d+", "", body))
        else:
            count = _number(craft_cn.group(1) or "", 1, 64)
            item = _item_name(craft_cn.group(2))
        # Whitelist: known canonical names only, so chat sentences that merely
        # start with 做/制作 never become nonsense craft commands.
        if item and (item in BLOCK_ALIASES.values() or re.match(r"^[a-z_]+$", item)):
            return f"craft {item} {count}"

    # furnace ---------------------------------------------------------------
    smelt_en = re.match(r"^\s*(?:smelt|cook)\s+([a-z_]+)(?:\s+(\d{1,2}))?\s*$", lower)
    smelt_cn = re.match(
        r"^(?:烧炼|熔炼|烧|炼)(\d+|[一两二三四五六七八九十几]+)?[个块只片条张]?"
        r"([\u4e00-\u9fa5]{1,8}|[a-z_]+)$",
        text,
    )
    if smelt_en:
        input_item = _item_name(smelt_en.group(1))
        output_item = next((out for out, source in SMELT_INPUT_FOR_OUTPUT.items() if source == input_item), "")
        if output_item:
            return f"smelt {input_item} {output_item} {_number(smelt_en.group(2), 1, 8)}"
    if smelt_cn:
        from .minecraft_items import item_catalog

        named = item_catalog().lookup(smelt_cn.group(2)) or _item_name(smelt_cn.group(2))
        # The sentence may name the PRODUCT (炼铁锭) or the raw INPUT
        # (烧粗铁/烧木头).  Try both directions before giving up.
        if named in SMELT_INPUT_FOR_OUTPUT:
            input_item = SMELT_INPUT_FOR_OUTPUT[named]
            return f"smelt {input_item} {named} {_number(smelt_cn.group(1), 1, 8)}"
        product = SMELT_PRODUCT_FOR_INPUT.get(named)
        if product:
            input_item = SMELT_INPUT_FOR_OUTPUT.get(product, named)
            return f"smelt {input_item} {product} {_number(smelt_cn.group(1), 1, 8)}"

    # brewing --------------------------------------------------------------
    brew_en = re.match(r"^\s*(?:brew|make)\s+(?:potion_of_)?([a-z_]+?)(?:_potion)?(?:\s+(\d))?\s*$", lower)
    brew_cn = re.match(
        r"^(?:酿造|酿|做)(\d+|[一两二三]+)?(?:瓶|个)?(.{2,10}药水)$",
        text,
    )
    if brew_en:
        return f"brew {brew_en.group(1)} {_number(brew_en.group(2), 1, 3)}"
    if brew_cn:
        potion = POTION_ALIASES.get(brew_cn.group(2))
        if potion:
            return f"brew {potion} {_number(brew_cn.group(1), 1, 3)}"

    # cauldron -------------------------------------------------------------
    cauldron_en = re.match(
        r"^\s*cauldron\s+(fill|collect|drain|take)\s+(water|lava|powder_snow)\s*$",
        lower,
    )
    if cauldron_en:
        action = "fill" if cauldron_en.group(1) == "fill" else "collect"
        return f"cauldron {action} {cauldron_en.group(2)}"
    if re.match(r"^(?:用)?水(?:桶)?(?:装满|装进|倒进)炼药锅$|^给炼药锅装水$", text):
        return "cauldron fill water"
    if re.match(r"^(?:从炼药锅|用桶从炼药锅)(?:里)?(?:取水|打水|装水)$", text):
        return "cauldron collect water"

    # collect ----------------------------------------------------------------
    collect = re.search(
        r"^\s*(?:collect|gather|harvest|mine|dig)\s+(?:some\s+|the\s+)?([a-z_]+(?:\s+(?:ore|block|logs?))?)"
        r"(?:\s+(\d{1,2}))?\s*$",
        lower,
    )
    collect_cn = re.search(
        r"^(?:收集|采集|挖|弄|采)(\d+|[一两二三四五六七八九十几]+)?个?(?:的)?([一-龥]{1,6}|[a-z_]+)$",
        text,
    )
    if collect:
        block = _block_name(collect.group(1))
        count = _number(collect.group(2) or "", 1, 32)
        return f"collect {block} {count}"
    if collect_cn:
        count_word = collect_cn.group(1) or ""
        if not count_word and "点" in text:
            count = 8  # "挖点石头": an unspecified small amount
        else:
            count = _number(count_word, 1, 32)
        block = _block_name(collect_cn.group(2))
        if block in BLOCK_ALIASES.values() or re.match(r"^[a-z_]+$", block):
            return f"collect {block} {count}"

    return None


def _looks_like_action_request(raw_text):
    """Conservative last-line guard against an LLM swallowing a task.

    This does not decide *which* action to run.  It only distinguishes a
    likely imperative from ordinary conversation, so an unstructured or
    misclassified model answer can be retried and then clarified instead of
    ending as a friendly but actionless acknowledgement.
    """
    original = _strip_sender(raw_text)
    text = TRAILING_PARTICLES.sub("", original).strip()
    if not text or NEGATION_PREFIX.match(text):
        return False
    if parse_intent(text):
        return True
    # Capability questions are conversation, even though they contain action
    # words: "你会做木剑吗" must not start a task.
    if re.match(r"^(?:你)?(?:会不会|会|能不能|能否|能|知道怎么).*(?:吗|么|？|\?)$", text):
        return False
    if re.match(r"^(?:来)?(?:聊|聊天|讲|说|告诉|解释|评价|介绍|回答|谈)|^给我(?:讲|说|介绍|解释|推荐)", text):
        return False
    chinese = re.match(
        r"^(?:(?:请|麻烦|劳驾|拜托|可以|可不可以|能不能)?(?:你)?(?:帮我|帮忙)?)?"
        r"(?:把|去|来|到|跟|挖|采|收集|砍|做|制作|造|合成|烧|炼|酿|拿|带|"
        r"给|交|送|捡|拾|扔|丢|攻击|击杀|杀|打|装备|手持|吃|放|使用|打开|"
        r"关闭|取消|停止|找|检查|查看|建造|搭建)",
        text,
    )
    indirect = re.match(r"^我(?:想|希望|要)(?:让你|请你|叫你|你能)(?:去|来|挖|采|做|拿|带|给|交|找|建)", text)
    english = re.match(
        r"^(?:please\s+)?(?:go|come|follow|mine|collect|gather|chop|craft|make|smelt|brew|"
        r"take|bring|give|drop|attack|kill|equip|eat|place|build|find|check|use|open|close|stop)\b",
        text,
        re.IGNORECASE,
    )
    return bool(chinese or indirect or english)


# ---------------------------------------------------------------------------
# Goal / direct-action validation (LLM layer)

def validate_goal(goal):
    """Normalize and validate an LLM goal; (goal, error_message)."""
    if not isinstance(goal, dict):
        return None, "goal 格式不对"
    kind = str(goal.get("type") or "").strip().lower()
    if kind not in GOAL_TYPES:
        return None, f"未知目标类型 {kind}"
    from .minecraft_items import item_catalog

    catalog = item_catalog()
    result = {"type": kind, "constraints": []}
    if kind == "travel":
        coords = goal.get("coords")
        if isinstance(coords, (list, tuple)) and len(coords) == 3:
            try:
                result["coords"] = [int(v) for v in coords]
            except (TypeError, ValueError):
                return None, "坐标不合法"
        target = str(goal.get("target") or goal.get("recipient") or "").strip()
        if "coords" not in result and not target:
            return None, "travel 目标缺失"
        result["target"] = target
        return result, None
    if kind == "combat":
        result["target"] = str(goal.get("target") or "nearest").strip().lower().replace(" ", "_")[:24]
        try:
            result["count"] = max(1, min(32, int(goal.get("count") or 1)))
        except (TypeError, ValueError):
            result["count"] = 1
        return result, None
    target = str(goal.get("target") or goal.get("item") or "").strip()
    if not target:
        return None, "缺少目标物品"
    # Variant potions ("potion/healing", "治疗药水") validate against the
    # potion registry instead of the item catalog.
    potion = potions.parse_potion_target(target)
    if potion:
        result["target"] = potion["key"]
    else:
        canonical = catalog.lookup(target)
        if not canonical:
            return None, f"不认识的物品：{target}"
        result["target"] = canonical
    try:
        result["count"] = max(1, min(640, int(goal.get("count") or 1)))
    except (TypeError, ValueError):
        result["count"] = 1
    recipient = str(goal.get("recipient") or "Doctor").strip()
    if not re.match(r"^[A-Za-z0-9_]{1,24}$", recipient):
        recipient = "Doctor"
    result["recipient"] = recipient
    return result, None


def _explicit_delivery_goal(text):
    """Conservative fallback for a clearly imperative craft-and-deliver ask.

    The LLM remains the primary router.  This only runs when its JSON was
    truncated/malformed, and deliberately requires both an action verb and a
    delivery phrase so ordinary discussion cannot start a task.
    """
    value = _strip_sender(text).strip()
    chinese = re.search(
        r"(?:帮我)?(?:做|制作|造|合成|准备)"
        r"(?:一|两|二|三|四|五|六|七|八|九|十|\d+)?(?:把|个|件|份)?"
        r"(?P<item>[\u4e00-\u9fffA-Za-z_]{1,24}?)(?:并|然后|再)?"
        r"(?:交给|给|拿给|送给)我(?:吧|一下)?$",
        value,
    )
    english = re.search(
        r"(?:make|craft|prepare)\s+(?:me\s+)?(?:an?\s+|\d+\s+)?"
        r"(?P<item>[a-z_ ]{2,30}?)\s+(?:and\s+)?(?:give|bring|deliver)\s+(?:it\s+)?to\s+me$",
        value,
        re.IGNORECASE,
    )
    match = chinese or english
    if not match:
        return None
    raw_item = match.group("item").strip().replace("一把", "").replace("一件", "")
    from .minecraft_items import item_catalog

    canonical = item_catalog().lookup(raw_item)
    if not canonical:
        return None
    count_match = re.search(r"\d{1,2}", value)
    count = int(count_match.group()) if count_match else 1
    goal, _ = validate_goal({
        "type": "obtain_and_deliver",
        "target": canonical,
        "count": count,
        "recipient": "Doctor",
    })
    return goal


def game_session_locked(method):
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        with self._knowledge_base()._memory_write_lock,self._lock:
            return method(self,*args,**kwargs)
    return wrapped


class ConversationService:
    """Single conversation brain for all Saya frontends."""

    def __init__(self):
        self._lock = threading.RLock()
        self._memory_manager = None
        self._kb = None
        self._persona_cache = (0.0, "")

    # -- collaborators --------------------------------------------------------
    def _memory(self):
        if self._memory_manager is None:
            from core_services import memory_manager  # noqa: WPS433 (lazy)

            self._memory_manager = memory_manager
        return self._memory_manager

    def _knowledge_base(self):
        if self._kb is None:
            from core_services import kb_system  # noqa: WPS433 (lazy)

            self._kb = kb_system
        return self._kb

    # -- credentials -----------------------------------------------------------
    def credentials(self):
        """API credentials resolution order: env -> local credentials file.

        The key itself is never logged, returned by status endpoints or sent
        to the worker.
        """
        api_key = str(os.environ.get("API_KEY", "") or "").strip()
        api_url = str(os.environ.get("API_URL", "") or DEFAULT_API_URL).strip()
        model = str(os.environ.get("API_MODEL", "") or DEFAULT_API_MODEL).strip()
        stored = {}
        try:
            from .credentials_storage import read_credentials
            stored = read_credentials(CREDENTIALS_FILE)
        except (OSError, ValueError):
            pass
        if not api_key:
            api_key = str(stored.get("api_key", "") or "").strip()
            api_url = str(stored.get("api_url") or api_url).strip()
            model = str(stored.get("api_model_name") or model).strip()
        return {
            "configured": bool(api_key),
            "api_key": api_key,
            "api_url": api_url.rstrip("/") or DEFAULT_API_URL,
            "model": model or DEFAULT_API_MODEL,
            "api_source": "environment" if os.environ.get("API_KEY", "").strip() else "server_file" if api_key else "unconfigured",
            "local_preferred": stored.get('_prefer_local', True) is True,
            "local_fields": [key for key in stored if not key.startswith('_')],
            "model_routes": {key:str(stored.get(key) or '') for key in ('multimodal_model_name','strong_logic_model_name','memory_strong_model_name','autonomous_memory_model_name') if key in stored},
            "brave_search_api_key": (os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
                or str(stored.get("brave_search_api_key") or "")
                or self._legacy_search_key(stored)),
            "search_provider": stored.get('search_provider','brave'),
            "deepseek_search_api_key": (os.environ.get('DEEPSEEK_SEARCH_API_KEY','').strip()
                or str(stored.get('deepseek_search_api_key') or '')),
        }

    def _legacy_search_key(self, stored):
        if "brave_search_api_key" in stored:
            return ""  # An explicit clear must not revive the legacy key.
        from .qq_controls import QQSearchCredentialsStore
        return QQSearchCredentialsStore(KNOWLEDGE_BASE_DIR / "qq" / "search_credentials.json").key()

    def save_credentials(self, api_key="", api_url="", api_model_name="", brave_search_api_key=None, from_request=False):
        from .credentials_storage import CREDENTIALS_LOCK, read_credentials, update_credentials
        if any(not isinstance(value, str) or any(char in value for char in "\r\n\x00") for value in (api_key, api_url, api_model_name)):
            raise ValueError("invalid model credentials")
        with CREDENTIALS_LOCK:
            stored = read_credentials(CREDENTIALS_FILE)
            if stored.get('_local_profile_managed') or from_request:
                return {"saved": False, "reason": "use_versioned_local_profile"}
            payload = {key: str(value or stored.get(key) or "").strip() for key, value in {
                "api_key": api_key, "api_url": api_url, "api_model_name": api_model_name}.items()}
            if brave_search_api_key is not None:
                from .qq_controls import QQSearchCredentialsStore
                if brave_search_api_key != "":
                    if not isinstance(brave_search_api_key, str) or len(brave_search_api_key.strip()) > 512 or any(c in brave_search_api_key for c in "\r\n\x00"):
                        raise ValueError("invalid search credentials")
                payload["brave_search_api_key"] = brave_search_api_key.strip()
            update_credentials(CREDENTIALS_FILE, payload)
        return {"saved": True}

    def credentials_status(self):
        info = self.credentials()
        from .web_research.providers import selected_search
        provider,search_key=selected_search(info)
        return {
            "configured": info["configured"],
            "api_url": info["api_url"],
            "model": info["model"],
            "api_source": info.get("api_source", "unconfigured"),
            "web_search_configured": bool(search_key),"search_provider":provider,
        }

    # -- persona / memory ------------------------------------------------------
    def _persona(self):
        now = time.time()
        if now - self._persona_cache[0] < 300 and self._persona_cache[1]:
            return self._persona_cache[1]
        text = ""
        try:
            if PROFILE_PATH.exists():
                text = PROFILE_PATH.read_text(encoding="utf-8")[:2400]
        except Exception:
            text = ""
        if not text:
            text = (
                "You are Saya, Doctor's warm and attentive companion. "
                "Doctor is your owner; in Minecraft his username is Kurosawa_Jin."
            )
        self._persona_cache = (now, text)
        return text

    def _recent_history(self, session_id=SESSION_ID):
        try:
            history = self._memory().load_session(session_id, make_current=False)
            return [str(line) for line in (history or [])][-HISTORY_TURNS:]
        except Exception:
            return []

    def _memory_lines(self, text):
        try:
            # Selection alone must not bump access_count (V4 plan section 3.5).
            memories = self._knowledge_base().get_relevant_memories(
                text, mode="normal", limit=MEMORY_LIMIT, mark_accessed=False
            )
        except Exception:
            memories = []
        lines = []
        for item in memories or []:
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(f"- {content[:200]}")
        # Deep memory linkage: recent game events (plan history) join the
        # prompt so the persona can reference what Saya just did in game
        # even when no knowledge-base entry matches the query keywords.
        try:
            from . import game_memory

            events = game_memory.recent_game_events(limit=3)
            if events:
                lines = [f"[近期游戏]"] + events + lines
        except Exception:
            pass
        return lines

    @game_session_locked
    def _record(self, role, content, metadata, session_id=SESSION_ID):
        try:
            from .memory_use import current_use_epoch
            if current_use_epoch() is None:
                with self._knowledge_base().memory_use.bind():
                    self._memory().save_message(role,str(content)[:2000],metadata=metadata,session_id=session_id)
            else:
                self._memory().save_message(role,str(content)[:2000],metadata=metadata,session_id=session_id)
        except Exception:
            pass

    def history(self, limit=60, session_id=SESSION_ID):
        session_id = session_id or SESSION_ID
        try:
            items = self._memory().load_session_full(session_id, make_current=False)
        except Exception:
            items = []
        result = []
        for item in (items or [])[-max(1, min(200, int(limit or 60))):]:
            entry = {
                "role": str(item.get("role") or ""),
                "content": str(item.get("content") or ""),
                "time": str(item.get("time") or ""),
                "source": str(item.get("source") or ""),
            }
            result.append(entry)
        return result

    # -- per-session storage for game conversations (2026-09 plan) -----------
    # Each Minecraft world join gets its own session file
    # (session_mc_<time>_<host>_<port>.jsonl) so conversations from different
    # worlds and launches are stored separately, never mixed into the web/
    # desktop session and never deleted.

    GAME_SESSION_RE = re.compile(r"^session_mc_[A-Za-z0-9._-]+\.jsonl$")

    def list_game_sessions(self):
        """Game session files, newest first (file mtime)."""
        import os

        try:
            base_dir = self._memory().base_dir
        except Exception:
            return []
        result = []
        try:
            for name in os.listdir(base_dir):
                if not self.GAME_SESSION_RE.match(name):
                    continue
                path = os.path.join(base_dir, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                entries = 0
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        entries = sum(1 for line in handle if line.strip())
                except OSError:
                    continue
                result.append({
                    "id": name,
                    "entries": entries,
                    "mtime": stat.st_mtime,
                })
        except OSError:
            return []
        result.sort(key=lambda item: item["mtime"], reverse=True)
        return result

    def _ai_archive_summary(self, items):
        """Ask the configured LLM for a short memory-worthy summary of one
        game conversation.  Returns None when the API is not configured or
        fails - callers fall back to the raw digest."""
        credentials = self.credentials()
        if not credentials["configured"] or not items:
            return None
        lines = []
        for item in items[-40:]:
            who = {"user": "Doctor", "ai": "Saya"}.get(item.get("role"), "系统")
            content = str(item.get("content") or "").strip()[:100]
            if content:
                lines.append(f"{who}: {content}")
        transcript = "\n".join(lines)[-3000:]
        system = (
            "你是记忆归档助手。把这段Minecraft游戏对话归纳成3-6条值得长期记住的要点，"
            "用简体中文，一行一条，只保留Doctor提到的事实、偏好、约定、世界信息和未完成事项；"
            "不要客套话，不要复述日常寒暄。"
        )
        url = credentials["api_url"]
        if not url.casefold().endswith("/chat/completions"):
            url += "/chat/completions"
        try:
            response = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {credentials['api_key']}",
                },
                json={
                    "model": credentials["model"],
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": transcript},
                    ],
                    "stream": False,
                    "max_tokens": 320,
                    "temperature": 0.3,
                },
                timeout=(10, 45),
            )
            if response.status_code != 200:
                return None
            summary = str(
                ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content")
                or ""
            ).strip()
            return summary[:900] or None
        except Exception:
            return None

    @game_session_locked
    def merge_game_sessions(self, session_ids):
        """Merge several game sessions (e.g. multiple runs in the same world)
        into the newest one; the merged-away files move to archived/."""
        import os

        if not isinstance(session_ids, list) or len(session_ids) < 2:
            return {"merged": False, "reason": "请选择至少两个要合并的会话"}
        sessions = [str(s or "") for s in session_ids]
        for sid in sessions:
            if not self.GAME_SESSION_RE.match(sid):
                return {"merged": False, "reason": f"会话 id 不合法：{sid}"}
        entries = []
        for sid in sessions:
            path = self._memory()._safe_session_path(sid)
            if not path or not os.path.exists(path):
                return {"merged": False, "reason": f"会话不存在：{sid}"}
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        entries.append(line)
        if not entries:
            return {"merged": False, "reason": "所选会话都是空的"}
        target = max(
            sessions,
            key=lambda sid: os.path.getmtime(self._memory()._safe_session_path(sid)),
        )
        sources = [sid for sid in sessions if sid != target]
        rows = [json.loads(line) for line in entries]
        rows.sort(key=lambda row: str(row.get("time") or ""))
        target_path = self._memory()._safe_session_path(target)
        with open(target_path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        archived_dir = os.path.join(self._memory().base_dir, "archived")
        os.makedirs(archived_dir, exist_ok=True)
        moved = []
        for sid in sources:
            source_path = self._memory()._safe_session_path(sid)
            if source_path and os.path.exists(source_path):
                os.replace(source_path, os.path.join(archived_dir, sid))
                moved.append(sid)
        return {
            "merged": True,
            "target": target,
            "entries": len(rows),
            "merged_sources": moved,
        }

    def archive_game_session(self,session_id=None):
        from .game_archive_writes import archive
        return archive(self,session_id)

    # -- game bridge helpers -----------------------------------------------------
    @staticmethod
    def _mc_service():
        try:
            from .minecraft_mineflayer import minecraft_bot_service

            return minecraft_bot_service
        except Exception:
            return None

    def _worker_running(self):
        service = self._mc_service()
        try:
            return bool(service and service.status().get("running"))
        except Exception:
            return False

    def _send_command(self, command, origin="saya"):
        """Send one command with a Python-generated task id (task envelope).

        Returns the task id string when sent (truthy), "" when the worker
        pipe is unavailable - existing `if sent:` call sites keep working.
        """
        service = self._mc_service()
        if not service:
            return ""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        return task_id if service.send_command(command, origin=origin, task_id=task_id) else ""

    # -- deterministic reply + execution -------------------------------------------
    def _deterministic_reply(self, command):
        if command in DETERMINISTIC_REPLIES:
            return DETERMINISTIC_REPLIES[command]
        verb = command.split()[0] if command else ""
        if verb == "follow":
            return "嗯，我会一直跟着你。"
        if verb == "pillar":
            return "好，我向上搭方块上来。"
        if verb == "come":
            return "好，我这就过来。"
        if verb == "goto":
            return "好，我去那个坐标。"
        if verb == "chop":
            return "好，我去找附近的树，砍完就回来。"
        if verb == "collect":
            return "好，我去收集，完成后告诉你。"
        if verb == "craft":
            return "好，我来准备材料做这个。"
        if verb == "brew":
            return "好，我去酿造；每一步都会核对药水类型。"
        if verb == "cauldron":
            return "好，我去操作炼药锅，并核对方块和桶的变化。"
        if verb == "attack":
            return "好，我上！"
        if verb == "inventory":
            return "我看看背包里有什么。"
        if verb == "equip":
            return "好，我来把它装备好。"
        return "好的。"

    def _drop_guard(self, command):
        """Vague/valuable drops need confirmation (plan section 3.3)."""
        from .minecraft_items import item_catalog

        catalog = item_catalog()
        parts = command.split()
        if not parts or parts[0] != "drop":
            return True, ""
        service = self._mc_service()
        runtime = {}
        if service:
            try:
                runtime = service.status().get("runtime") or {}
            except Exception:
                runtime = {}
        inventory = runtime.get("inventory") or []
        held = runtime.get("held_item") or ""

        def protected(item_name, explicit):
            if catalog.drop_is_protected(item_name, inventory=inventory, explicit=explicit):
                return True
            try:
                from .minecraft_planner import plan_executor

                reserved = plan_executor.reserved_items()
            except Exception:
                reserved = {}
            return any(
                item_name == name or item_name.endswith(f"_{name}") or name.endswith(f"_{item_name}")
                for name in reserved
            )

        if len(parts) == 2 and parts[1] in ("one", "stack"):
            # Unspecified item: protects the held / first item.
            target = held or (inventory[0].get("name") if inventory else "")
            if target and protected(target, explicit=False):
                return False, f"{catalog.chinese(target)}对我来说很重要，确定要扔吗？"
            return True, ""
        if len(parts) >= 3:
            item = parts[1]
            if protected(item, explicit=True):
                return False, f"{catalog.chinese(item)}是重要物品（或正在被任务使用），确定要扔吗？"
        return True, ""

    def _maybe_goal_from_command(self, command, state):
        """Upgrade deterministic craft/collect commands into planner goals
        when the worker is running and preparation is actually needed."""
        if not self._worker_running():
            return None
        from .minecraft_planner import PlanError, plan_goal, plan_executor

        parts = command.split()
        goal = None
        if parts and parts[0] == "craft" and len(parts) >= 2:
            item = parts[1]
            count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            goal = {"type": "obtain", "target": item, "count": count}
        elif parts and parts[0] == "collect" and len(parts) >= 2:
            goal = {
                "type": "obtain",
                "target": parts[1],
                "count": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1,
            }
        elif parts and parts[0] == "chop":
            goal = {"type": "obtain", "target": "_log",
                    "count": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 4}
        elif parts and parts[0] == "smelt" and len(parts) >= 2:
            output = ""
            count = 1
            if len(parts) >= 3 and not parts[2].isdigit():
                output = parts[2]
                count = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            else:
                output = next(
                    (name for name, source in SMELT_INPUT_FOR_OUTPUT.items() if source == parts[1]),
                    "",
                )
                count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            if output:
                goal = {"type": "obtain", "target": output, "count": count}
        if not goal:
            return None
        try:
            plan = plan_goal(goal, state)
        except PlanError:
            return None  # fall back to the direct command / LLM clarify
        # Only worth a plan when it actually prepares something beyond the
        # single direct command itself.
        trivial = (
            len(plan["steps"]) == 1
            and plan["steps"][0]["action"] in ("collect",)
            and plan["steps"][0]["command"] == command
        ) or (len(plan["steps"]) == 1 and plan["steps"][0]["action"] == "inventory")
        if trivial:
            return None
        plan_executor.submit(plan)
        return plan

    def _execute_goal(self, goal):
        """Plan + submit a goal.  Returns (user-facing status reply, plan|None).

        A None plan means the goal was NOT submitted (worker down / planner
        refused) - callers must not treat the reply as an execution promise.
        Every submitted plan reply carries the plan id so the promise is
        always traceable to a task.
        """
        from .minecraft_planner import PlanError, plan_goal, plan_executor

        service = self._mc_service()
        if not self._worker_running():
            return "我现在没连上游戏，等进服了再做这个。", None
        try:
            plan = plan_goal(goal, service.status())
        except PlanError as exc:
            return f"这个我暂时做不到：{exc}", None
        plan_executor.submit(plan)
        return self._plan_started_reply(plan), plan

    @staticmethod
    def _plan_started_reply(plan):
        """Execution-promise reply: disposition + traceable task reference."""
        total = len(plan["steps"])
        short_id = str(plan.get("plan_id") or "").split("-")[-1]
        if plan.get("will_deliver"):
            body = f"好，我来{plan['goal_description']}，一共 {total} 步，做完向你汇报。"
        else:
            body = f"好，我来{plan['goal_description']}，一共 {total} 步，做好先放在我背包里。"
        return f"{body}（任务 {short_id}）"

    def _handle_task_intent(self, intent, result, text, source, speaker, session_id, started):
        """Route one structured task intent (task_intent.parse_task_intent).

        request  -> planner goal (delivery/keep already resolved by the
                    disposition layer); only a submitted plan may be answered
                    with an execution promise.
        negation / conditional -> deterministic acknowledgement, NEVER a task.
        """
        latency = lambda: round((time.monotonic() - started) * 1000)  # noqa: E731
        if intent["modality"] in ("negation", "conditional"):
            display = intent.get("display") or "那个"
            reply = (
                "好的，那我不准备了。"
                if intent["modality"] == "negation"
                else f"明白，{display}先不准备，到时候你说一声我马上备。"
            )
            self._record("user", text, {"source": source, "speaker": speaker, "kind": "chat"}, session_id)
            self._record("ai", reply, {"source": source, "kind": "chat", "intent": intent["modality"]}, session_id)
            result.update(route="chat", reply=reply, source="deterministic_rule", latency_ms=latency())
            return result

        try:
            goal = intent_to_goal(intent, speaker=speaker)
        except ValueError as exc:
            self._record("user", text, {"source": source, "speaker": speaker, "kind": "chat"}, session_id)
            self._record("ai", str(exc), {"source": source, "kind": "chat"}, session_id)
            result.update(route="clarify", reply=str(exc), source="deterministic_rule", latency_ms=latency())
            return result
        reply, plan = self._execute_goal(goal)
        self._record("user", text, {"source": source, "speaker": speaker, "kind": "task"}, session_id)
        self._record(
            "ai", reply,
            {"source": source, "kind": "task", "intent": "request",
             "plan_id": (plan or {}).get("plan_id"), "goal": goal},
            session_id,
        )
        result.update(
            route="goal" if plan else "clarify",
            goal=goal,
            reply=reply,
            source="deterministic_rule",
            latency_ms=latency(),
            task_id=(plan or {}).get("plan_id", ""),
        )
        return result

    # -- layer 3: large model ----------------------------------------------------
    def _system_prompt(self, owner_game_name):
        return (
            f"{self._persona()}\n\n"
            "You are chatting with Doctor inside a Minecraft companion bridge. "
            f"Doctor's in-game username is {owner_game_name}. "
            "A structured game state JSON block is provided separately; NEVER invent "
            "inventory items, positions or game facts that are not in it.\n\n"
            "YOUR JOB: understand what Doctor wants, do NOT script actions.\n"
            "- Judge the message: pure chat, a direct simple action, a high-level goal, "
            "both, or something ambiguous needing one short question.\n"
            "- For simple immediate actions you may emit ONE direct_action command from "
            "this whitelist:\n" + llm_tool_description() + "\n"
            "- For anything needing preparation (crafting with materials, tool chains, "
            "mining with the right tool, furnace smelting, delivering items) emit a goal instead; the "
            "deterministic planner computes every step. Goal types: obtain, craft, "
            "obtain_and_deliver, travel, combat.\n"
            "- NEVER emit multiple competing commands, never /tp, never admin or slash "
            "commands, never more than one direct_action.\n\n"
            "CONVERSATION RULES:\n"
            "- These are ordinary chat, NOT action requests: 刚才砍树挺有趣的 / 你会做木剑吗 / "
            "如果有镐就方便了 / 不要因为我提到石头就去挖.\n"
            "- These ARE action requests: 帮我做一把木剑 / 去挖点石头吧 / 缺工具的话你自己准备 / "
            "把刚做好的剑给我.\n"
            "- When the target is ambiguous (e.g. 石头 could mean cobblestone or "
            "silk-touch stone), route=clarify and ask ONE short question.\n"
            "- If a task is already running per the state block, keep chatting normally; "
            "do not restart or cancel it unless Doctor clearly asks.\n"
            "- reply must be one short in-game line (<=60 chars-ish) and MUST match the "
            "user's language (Chinese input -> Simplified Chinese).\n\n"
            "Reply with STRICT JSON only, no markdown fences, exactly this schema:\n"
            '{"route": "chat|direct_action|goal|mixed|clarify", "reply": "one short line", '
            '"direct_action": null, "goal": null, "memory_candidates": [], "confidence": 0.0}\n'
            "direct_action format: {\"name\": \"collect\", \"arguments\": {\"block\": \"iron_ore\", "
            "\"count\": 3}} using ONLY the whitelist above.\n"
            "goal format: {\"type\": \"obtain_and_deliver\", \"target\": \"wooden_sword\", "
            "\"count\": 1, \"recipient\": \"Doctor\", \"constraints\": []}. Potions use variant "
            "keys as target: \"potion/healing\", \"splash_potion/healing\", \"potion/strong_healing\".\n"
            "Only place items in memory_candidates when Doctor explicitly asks to "
            "remember a preference, place, plan or long-term instruction."
        )

    def _call_llm(self, text, context, credentials):
        owner_game_name = "Kurosawa_Jin"
        try:
            from ..services.minecraft_mineflayer import minecraft_bot_service

            owner_game_name = (
                minecraft_bot_service.config.get("owner_username") or owner_game_name
            )
        except Exception:
            pass
        history = self._recent_history(str(context.get("session_id") or "") or SESSION_ID)
        memory_lines = self._memory_lines(text)
        url = credentials["api_url"]
        if not url.casefold().endswith("/chat/completions"):
            url += "/chat/completions"
        user_block = ""
        game_state = context.get("game_state") if isinstance(context, dict) else None
        if game_state:
            user_block += "[Game state JSON]\n" + json.dumps(
                game_state, ensure_ascii=False
            ) + "\n\n"
        elif context.get("summary"):
            user_block += f"[Game context] {str(context['summary'])[:400]}\n"
        if memory_lines:
            user_block += "[Long-term memory]\n" + "\n".join(memory_lines) + "\n"
        if history:
            user_block += "[Recent conversation]\n" + "\n".join(history) + "\n"
        user_block += f"Doctor says: {text}"
        payload = {
            "model": credentials["model"],
            "messages": [
                {"role": "system", "content": self._system_prompt(owner_game_name)},
                {"role": "user", "content": user_block},
            ],
            "stream": False,
            # A complete goal object plus state-aware reply can exceed 320
            # tokens on DeepSeek.  Truncation used to expose half a JSON object
            # as ordinary chat and no plan was started.
            "max_tokens": 800,
            "temperature": 0.2,
        }
        started = time.monotonic()
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {credentials['api_key']}",
            },
            json=payload,
            timeout=LLM_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(f"conversation API {response.status_code}: {response.text[:160]}")
        raw = str(
            ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        ).strip()
        latency_ms = round((time.monotonic() - started) * 1000)
        parsed = self._parse_structured(raw, text)
        malformed = parsed.pop("_parse_error", False)
        swallowed_task = bool(
            _looks_like_action_request(text)
            and parsed.get("route") == "chat"
            and not parsed.get("direct_action")
            and not parsed.get("goal")
        )
        if malformed or swallowed_task:
            reason = "malformed JSON" if malformed else "task classified as empty chat"
            print(
                f"[conversation] local routing recovery ({reason}); "
                f"input={text[:80]!r}; output={raw[:180]!r}"
            )
            fallback_goal = _explicit_delivery_goal(text)
            if fallback_goal:
                parsed = {
                    "route": "goal",
                    "reply": "",
                    "direct_action": None,
                    "goal": fallback_goal,
                    "memory_candidates": [],
                    "confidence": 0.75,
                }
            elif swallowed_task or _looks_like_action_request(text):
                parsed = {
                    "route": "clarify",
                    "reply": "我知道这是任务，但还没能可靠拆成可执行步骤。请明确要我操作的物品和最终放到哪里。",
                    "direct_action": None,
                    "goal": None,
                    "memory_candidates": [],
                    "confidence": 0.0,
                    "routing_error": reason,
                }
            else:
                raise RuntimeError("conversation API returned malformed structured JSON")
        return parsed, latency_ms, credentials["model"]

    @staticmethod
    def _parse_structured(raw, text):
        candidate = raw.strip()
        fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
        if fence:
            candidate = fence.group(1).strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start:end + 1]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            # A JSON-looking fragment is almost always a truncated structured
            # response.  Never show it as dialogue; let the caller retry/fall
            # back safely.  Genuine free-text answers remain valid chat.
            json_like = candidate.lstrip().startswith(("{", "[")) or '"route"' in candidate
            reply = re.sub(r"\[MC:\s*[^\]]{1,100}\]", "", raw).strip()
            return {
                "route": "chat",
                "reply": reply[:MAX_REPLY_CHARS],
                "direct_action": None,
                "goal": None,
                "memory_candidates": [],
                "confidence": 0.5,
                "_parse_error": json_like,
            }
        route = str(data.get("route") or "chat").lower()
        if route not in {"chat", "direct_action", "goal", "mixed", "clarify"}:
            route = "chat"
        reply = str(data.get("reply") or "").strip()[:MAX_REPLY_CHARS]

        # direct_action: object form preferred; legacy "commands" list only
        # ever yields ONE command (no bursts).
        direct = data.get("direct_action")
        command = None
        if isinstance(direct, dict):
            command = validate_direct_action(direct.get("name"), direct.get("arguments"))
        elif isinstance(direct, str) and direct.strip():
            parts = direct.strip().split(None, 1)
            name = parts[0].lower()
            arguments = {}
            if len(parts) > 1:
                rest = parts[1]
                if name in ("goto", "mine"):
                    values = _coords(rest)
                    if values:
                        arguments = {"x": values[0], "y": values[1], "z": values[2]}
                elif name in ("collect", "craft", "chop", "equip"):
                    tokens = rest.split()
                    key = "block" if name == "collect" else "item"
                    if tokens:
                        arguments[key] = tokens[0]
                    if len(tokens) > 1 and tokens[-1].isdigit():
                        arguments["count"] = tokens[-1]
                elif name == "give":
                    tokens = rest.split()
                    if tokens:
                        arguments["item"] = tokens[0]
                    if len(tokens) > 1 and tokens[1].isdigit():
                        arguments["count"] = tokens[1]
                    if "to" in tokens:
                        arguments["target"] = tokens[tokens.index("to") + 1]
                elif name in ("come", "follow", "attack"):
                    tokens = rest.split()
                    arguments["target"] = tokens[0] if tokens else ""
                    if name == "attack" and len(tokens) > 1 and tokens[1].isdigit():
                        arguments["count"] = tokens[1]
                elif name == "smelt":
                    tokens = rest.split()
                    if tokens:
                        arguments["input"] = tokens[0]
                    if len(tokens) > 1:
                        arguments["output"] = tokens[1]
                    if len(tokens) > 2 and tokens[2].isdigit():
                        arguments["count"] = tokens[2]
            command = validate_direct_action(name, arguments)
        if command is None and isinstance(data.get("commands"), list) and data["commands"]:
            for item in data["commands"][:1]:
                if isinstance(item, dict):
                    command = validate_direct_action(item.get("name"), item.get("arguments"))
                    break

        goal, goal_error = (None, "")
        if data.get("goal") is not None:
            goal, goal_error = validate_goal(data.get("goal"))
            if goal is None:
                route = "clarify" if route != "chat" else route
                reply = reply or goal_error

        # Trust validated payloads over a contradictory route label.  Some
        # OpenAI-compatible models return route=chat while still supplying a
        # valid action/goal; discarding that payload creates an acknowledgement
        # with no work behind it.
        if route == "chat" and (command or goal):
            route = "mixed" if command and goal else ("direct_action" if command else "goal")
        if route in {"direct_action", "mixed"} and command is None and goal is None:
            route = "clarify" if _looks_like_action_request(text) else "chat"
            if route == "clarify" and not reply:
                reply = "我判断这是任务，但模型没有给出有效指令，请把目标或数量说清楚。"
        if route == "goal" and goal is None:
            route = "chat" if command else "clarify"

        candidates = []
        for item in data.get("memory_candidates") or []:
            content = str(item if isinstance(item, str) else item.get("content") or "").strip()
            if content:
                candidates.append(content[:300])
        wants_memory = bool(re.search(r"记住|记得|remember|以后要|别忘了", str(text or "")))
        if candidates and not wants_memory:
            candidates = []
        try:
            confidence = round(max(0.0, min(1.0, float(data.get("confidence", 0.8)))), 2)
        except (TypeError, ValueError):
            confidence = 0.8
        return {
            "route": route,
            "reply": reply,
            "direct_action": command,
            "goal": goal,
            "memory_candidates": candidates,
            "confidence": confidence,
        }

    @staticmethod
    def _fallback_reply(text):
        # Absolute last resort (SLU server AND cloud API both unreachable).
        if _contains_cjk(text):
            return "你好呀，我在这里。" if "你好" in text else "我在听，但本地模型和对话 API 都暂时联系不上。"
        return "Hi, I'm here." if "hello" in str(text).lower() else "I'm listening, but both the local model and the chat API are unreachable."

    def _offline_reply(self, text):
        """Casual reply via the fine-tuned local model when the cloud persona
        LLM is unavailable.  The merged model keeps general chat ability, so
        small talk survives API outages without the canned ask-back string.
        Falls back to _fallback_reply when no SLU endpoint answers."""
        try:
            from . import slu

            backend, settings = self._slu_settings()
            if backend in ("local", "model"):
                reply = slu.chat_reply(
                    text, backend=backend, settings=settings,
                    credentials=self.credentials(),
                )
                if reply:
                    return reply
        except Exception:
            pass
        return self._fallback_reply(text)

    # -- public API ---------------------------------------------------------------
    def _slu_settings(self):
        """(slu_backend, settings) from the bot config; env wins for local path."""
        try:
            config = dict(self._mc_service().config or {})
        except Exception:
            config = {}
        backend = str(
            os.environ.get("SAYA_SLU_BACKEND")
            or config.get("slu_backend")
            or "legacy"
        ).strip()
        settings = {
            "slu_local_model": (
                os.environ.get("SAYA_SLU_LOCAL_MODEL")
                or config.get("slu_local_model")
                or ""
            ),
            "slu_model_url": (
                os.environ.get("SAYA_SLU_MODEL_URL")
                or config.get("slu_model_url")
                or ""
            ),
            "slu_model_name": (
                os.environ.get("SAYA_SLU_MODEL_NAME")
                or config.get("slu_model_name")
                or ""
            ),
        }
        return backend, settings

    _PRAISE_RE = re.compile(r"棒|厉害|乖|可爱|喜欢|谢谢|辛苦|真好|很好|不错|表扬|爱")

    @classmethod
    def _mixed_ack(cls, chat_texts):
        """One warm spoken line for the chat half of a mixed utterance."""
        joined = " ".join(t for t in chat_texts if t).strip()
        if not joined:
            return ""
        if cls._PRAISE_RE.search(joined):
            return "嘿嘿，被主人夸得更有干劲了～"
        return "嗯嗯，我在听～"

    def _try_slu_route(self, text, result, source, speaker, session_id, started,
                       backend, settings):
        """Fine-tuned understanding layer.

        Returns True when the message was fully handled (valid action sent,
        goal submitted, or an explicit clarify).  Pure chat, model silence
        and any backend failure return False -> persona LLM.  Actions are
        re-validated through the same catalog/guard path as the deterministic
        layer, so a hallucinated command can never reach the worker.
        """
        from . import slu

        try:
            if backend == "local":
                segments, used = slu.segment_local(text, settings)
            else:
                # Config-provided endpoint wins over the conversation API:
                # the standalone SLU server (scripts/serve_slu_model.py) or
                # any OpenAI-compatible host of the fine-tuned model.
                credentials = self.credentials()
                if settings.get("slu_model_url"):
                    credentials = {
                        "api_key": credentials["api_key"] or "local",
                        "api_url": settings["slu_model_url"],
                        "model": settings["slu_model_name"] or "slu-local",
                    }
                segments, used = slu.segment_utterance(
                    text, backend="model", credentials=credentials)
        except Exception as exc:
            print(f"[conversation] SLU {backend} backend unavailable: {exc}")
            return False
        if not segments or used != backend:
            return False
        instruction = next(
            (s for s in segments["segments"] if s.get("route") != "chat"), None)
        if instruction is None:
            return False  # pure chat -> persona LLM for the reply

        finish = lambda: (  # noqa: E731
            result.update(latency_ms=round((time.monotonic() - started) * 1000)),
            self._record("user", text,
                         {"source": source, "speaker": speaker, "kind": "task"}, session_id),
            self._record("ai", result.get("reply") or "",
                         {"source": source, "kind": "task", "route": result.get("route")},
                         session_id),
        )

        route = instruction.get("route") or "clarify"
        action = instruction.get("direct_action")
        goal = instruction.get("goal")

        # Mixed utterance ("你今天很好，给我做把镐子吧"): the task part is
        # executed below, but the chat part also deserves a spoken response -
        # a short warm acknowledgment so Saya never silently ignores praise
        # or conversation just because a command was attached.  The model was
        # not trained on multi-segment splits yet, so besides explicit chat
        # segments a praise-keyword heuristic covers the common case.
        chat_texts = [s.get("text") or "" for s in segments["segments"]
                      if s is not instruction and s.get("route") == "chat"]
        if instruction.get("type") == "mixed" and not chat_texts:
            chat_texts = [instruction.get("text") or ""]
        ack = self._mixed_ack(chat_texts) if chat_texts else ""
        if not ack and self._PRAISE_RE.search(text):
            ack = self._mixed_ack([text])

        if route == "clarify" or (not action and not goal):
            result.update(
                route="clarify", source="local_slu", confidence=0.6,
                reply=(f"{ack} " if ack else "")
                + "听起来这是个任务，但我不确定具体目标，请把物品、数量或位置说清楚。",
            )
            finish()
            return True

        handled_reply = ""
        if action:
            allowed, warning = self._drop_guard(action)
            if not allowed:
                result.update(route="clarify", source="local_slu",
                              reply=warning or "这个操作被安全规则拦下了。")
                finish()
                return True
            task_id = self._send_command(action)
            if task_id:
                task_reply = self._deterministic_reply(action) or f"好，执行：{action}"
                result.update(
                    route="task", commands=[action], task_id=str(task_id),
                    source="local_slu", confidence=0.85,
                    reply=f"{ack}{task_reply}" if ack else task_reply,
                )
            else:
                result.update(
                    route="clarify", source="local_slu",
                    reply="任务已经识别，但机器人连接不可用，指令没有发出。",
                    error="worker command pipe unavailable",
                )
                finish()
                return True

        if goal and route in ("goal", "mixed"):
            status_reply, plan = self._execute_goal(goal)
            handled_reply = (
                f"{handled_reply} {status_reply}".strip() if handled_reply else status_reply
            )
            if ack and not result.get("commands"):
                handled_reply = f"{ack}{handled_reply}"
            final_route = "mixed" if result.get("commands") else "goal"
            result.update(
                route=final_route,
                goal=goal,
                source="local_slu",
                confidence=0.85,
                reply=handled_reply or status_reply,
            )

        finish()
        return True

    def handle_message(self, source, speaker, text, context=None, force_command=False):
        """Route one message, then record it (and the routing truth of the
        day) into the SLU corpus.  The SLU corpus feeds the fine-tuned
        utterance-understanding model - collection is fail-silent and can be
        disabled with SAYA_SLU_CORPUS=0."""
        result = self._handle_message_inner(source, speaker, text, context, force_command)
        try:
            from . import slu

            slu.record_turn(text, result, source=source)
        except Exception:
            pass
        return result

    def _handle_message_inner(self, source, speaker, text, context=None, force_command=False):
        """Route one message through the three layers.

        Returns a dict with: route, reply, commands (executed this turn),
        goal (started this turn), source ('emergency_rule' |
        'deterministic_rule' | 'api' | 'local_fallback'), latency_ms, model,
        memory_enabled, confidence.  Command/goal execution happens HERE so
        every caller (game chat, desktop, web) behaves identically.
        """
        text = _strip_sender(text)
        context = context if isinstance(context, dict) else {}
        result = {
            "route": "chat",
            "reply": "",
            "commands": [],
            "goal": None,
            "source": "deterministic_rule",
            "latency_ms": 0,
            "model": "",
            "memory_enabled": False,
            "confidence": 1.0,
            "error": "",
        }
        if not text:
            result.update(route="clarify", reply="", error="empty message")
            return result

        started = time.monotonic()
        # Storage separation: game conversations go to their own per-join
        # session file (context['session_id']); web/desktop keep the web
        # session. Nothing is deleted - files just stay apart on disk.
        session_id = str(context.get("session_id") or "") or SESSION_ID

        # Layer 1 + 2: emergency and deterministic commands never wait on APIs.
        command = parse_intent(text)
        if command == "stop":
            sent = self._send_command("stop")
            if sent:
                result.update(route="task", commands=["stop"], source="emergency_rule")
                result["reply"] = DETERMINISTIC_REPLIES["stop"]
            else:
                result.update(
                    route="clarify", source="emergency_rule",
                    reply="我识别到了停止指令，但机器人连接不可用，指令没有发出。",
                    error="worker command pipe unavailable",
                )
            self._record("user", text, {"source": source, "speaker": speaker, "kind": "task"}, session_id)
            self._record("ai", result["reply"], {"source": source, "kind": "task"}, session_id)
            return result
        if command == "cancel task":
            # Cancel BOTH layers, mirroring POST /minecraft/task/cancel: the
            # old chat path only killed the worker's CURRENT step, so a
            # multi-step plan immediately submitted its next step and the
            # cancel looked like it never happened (and step/plan activity
            # interleaving left the bot standing around).
            from .minecraft_planner import plan_executor

            plan_executor.cancel(reason="主人取消了当前任务")
            sent = self._send_command("cancel task")
            result.update(
                route="task",
                commands=["cancel task"],
                source="emergency_rule",
                task_id=str(sent or ""),
            )
            result["reply"] = (
                DETERMINISTIC_REPLIES["cancel task"]
                if sent
                else "我识别到了取消指令，但机器人连接不可用；计划已取消。"
            )
            self._record("user", text, {"source": source, "speaker": speaker, "kind": "task"}, session_id)
            self._record("ai", result["reply"], {"source": source, "kind": "task"}, session_id)
            return result
        # Layer 2.5: structured task intents.  Runs between the emergency
        # stop and the generic command table so disposition-bearing phrasings
        # ("帮我酿造一瓶治疗药水" = brew AND hand over) reach the planner
        # instead of collapsing into a bare worker command.  Conservative:
        # returns None for anything it cannot fully resolve.
        if not force_command:
            try:
                intent = parse_task_intent(text)
            except Exception as exc:  # never let the NLU layer kill the chat
                print(f"[conversation] task intent parse failed: {exc}")
                intent = None
            if intent is not None:
                return self._handle_task_intent(
                    intent, result, text, source, speaker, session_id, started,
                )
        if command:
            plan = self._maybe_goal_from_command(command, context.get("mc_status") or {})
            if plan is not None:
                result.update(
                    route="goal",
                    goal=plan["goal"],
                    source="deterministic_rule",
                )
                result["reply"] = self._plan_started_reply(plan)
            else:
                allowed, warning = self._drop_guard(command)
                if allowed:
                    task_id = self._send_command(command)
                    if task_id:
                        result.update(route="task", commands=[command], task_id=task_id)
                        result["reply"] = self._deterministic_reply(command)
                    else:
                        result.update(
                            route="clarify",
                            reply="任务已经识别，但机器人连接不可用，指令没有发出。",
                            error="worker command pipe unavailable",
                        )
                else:
                    result.update(route="clarify")
                    result["reply"] = warning
            self._record("user", text, {"source": source, "speaker": speaker, "kind": "task"}, session_id)
            self._record("ai", result["reply"], {"source": source, "kind": "task"}, session_id)
            return result
        if force_command:
            result.update(
                route="clarify",
                reply="这句话没有识别成命令；要用命令方式请发送标准命令（如 follow Doctor）。",
                source="deterministic_rule",
            )
            return result

        # Layer 2.9: fine-tuned local SLU model (optional, config-driven:
        # slu_backend = local | model).  Sits AFTER the deterministic tables
        # (which stay authoritative for known commands) and BEFORE the big
        # conversation LLM: the fine-tuned model resolves NEW phrasings of
        # instructions/goals cheaply and offline; only genuine chat continues
        # to the persona layer.  Any failure falls through, never blocks.
        slu_backend, slu_settings = self._slu_settings()
        if not force_command and slu_backend in ("local", "model"):
            handled = self._try_slu_route(
                text, result, source, speaker, session_id, started,
                backend=slu_backend, settings=slu_settings,
            )
            if handled:
                return result

        # Layer 3: goal-understanding large model.
        credentials = self.credentials()
        if not credentials["configured"]:
            reply = (
                "我听到了，但对话 API 还没有同步，复杂任务暂时无法规划。"
                if _contains_cjk(text)
                else "I heard you, but the conversation API is not configured yet."
            )
            self._record("user", text, {"source": source, "speaker": speaker, "kind": "chat"}, session_id)
            self._record("ai", reply, {"source": source, "kind": "chat", "backend": "local_fallback"}, session_id)
            result.update(
                reply=reply,
                source="local_fallback",
                error="conversation API credentials are not configured",
            )
            result["latency_ms"] = round((time.monotonic() - started) * 1000)
            return result

        context_summary = str(context.get("summary") or "")[:400]
        try:
            structured, latency_ms, model = self._call_llm(text, context, credentials)
            reply = structured["reply"]
            if _contains_cjk(text) and reply and not _contains_cjk(reply):
                print(f"[conversation] model replied non-CJK to CJK input: {reply[:120]!r}")
                # Language correction must never erase an already validated
                # action or goal.  The old code did exactly that, producing
                # "我听到了，主人" while silently dropping real work.
                if structured.get("direct_action"):
                    reply = self._deterministic_reply(structured["direct_action"])
                elif structured.get("goal"):
                    reply = "好，我先检查现有条件并开始规划。"
                elif structured.get("route") == "clarify":
                    reply = "我还需要确认一个细节，请把目标、数量或最终位置说清楚。"
                else:
                    reply = self._offline_reply(text)

            executed_commands = []
            started_goal = None
            if structured.get("direct_action") and structured["route"] in (
                "direct_action", "mixed"
            ):
                allowed, warning = self._drop_guard(structured["direct_action"])
                if allowed:
                    if self._send_command(structured["direct_action"]):
                        executed_commands.append(structured["direct_action"])
                    else:
                        reply = "任务已经识别，但机器人连接不可用，指令没有发出。"
                        structured["route"] = "clarify"
                        structured["routing_error"] = "worker command pipe unavailable"
                else:
                    reply = warning or reply
                    structured["route"] = "clarify"
            if structured.get("goal") and structured["route"] in ("goal", "mixed"):
                status_reply, goal_plan = self._execute_goal(structured["goal"])
                started_goal = structured["goal"]
                reply = status_reply if structured["route"] == "goal" else (
                    f"{reply} {status_reply}" if reply else status_reply
                )

            # Final invariant: an imperative must not leave this service as
            # chat with no command and no goal.  This catches future provider
            # output quirks without guessing a potentially dangerous action.
            if (
                _looks_like_action_request(text)
                and not executed_commands
                and started_goal is None
                and structured.get("route") == "chat"
            ):
                structured["route"] = "clarify"
                reply = "我知道这是任务，但还没能可靠拆成可执行步骤。请明确目标、数量和最终位置。"
                structured["routing_error"] = "final no-action invariant activated"

            if not reply:
                reply = self._offline_reply(text)

            for content in structured.get("memory_candidates") or []:
                try:
                    self._knowledge_base().add_memory_candidate(
                        content,
                        title="Minecraft/Companion memory",
                        category="preference",
                        source_detail={"type": "conversation", "source": source},
                    )
                except Exception:
                    pass
            self._record(
                "user", text,
                {"source": source, "speaker": speaker, "kind": "chat",
                 "context": context_summary},
                session_id,
            )
            self._record(
                "ai", reply or "",
                {"source": source, "kind": "chat", "route": structured["route"],
                 "commands": executed_commands, "goal": started_goal},
                session_id,
            )
            result.update(
                route=structured["route"],
                reply=reply,
                commands=executed_commands,
                goal=started_goal,
                source="api",
                latency_ms=latency_ms,
                model=model,
                memory_enabled=True,
                confidence=structured.get("confidence", 0.8),
                error=str(structured.get("routing_error") or "")[:200],
            )
        except Exception as exc:
            # Local explicit commands still work when the API times out; here
            # the message simply had no deterministic command, so degrade to a
            # local fallback reply instead of staying silent - but say WHY,
            # so "我听到了" without a task is never a silent dead end.
            print(f"[conversation] LLM call failed: {exc}")
            detail = str(exc)[:120]
            reply = (
                f"我听到了，但对话模型出问题了（{detail}），任务没有创建。"
                "可以稍后再说一次，或检查 API 设置。"
                if _contains_cjk(text)
                else f"I heard you, but the model call failed ({detail}); no task was created."
            )
            self._record("user", text, {"source": source, "speaker": speaker, "kind": "chat"}, session_id)
            self._record("ai", reply, {"source": source, "kind": "chat", "backend": "local_fallback"}, session_id)
            result.update(reply=reply, source="local_fallback", error=str(exc)[:200])
            result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result


conversation_service = ConversationService()
