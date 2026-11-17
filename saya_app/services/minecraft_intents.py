"""Shared Minecraft intent helpers (decoupled from the retired pymcbotlite
client in minecraft_bot.py).

Only two things survived the old pure-Python bot:

- ``DEFAULT_CONFIG``: the persisted bot connection settings
  (knowledge_base/minecraft_bot.json).
- ``detect_player_action``: the deterministic in-game chat matcher that
  recognizes direct movement/action requests from the owner before the LLM
  gets involved.

Per the 2026-09 optimization plan, the Chinese alias 石头 now defaults to
cobblestone (the survival-obtainable form); a request that explicitly needs
vanilla ``stone`` must be clarified.  "挖点/弄点X" without a count defaults
to 8 items.
"""

from __future__ import annotations

import os
import re

from ..config import KNOWLEDGE_BASE_DIR
from pathlib import Path

BOT_CONFIG_PATH = Path(
    os.environ.get("SAYA_MC_BOT_CONFIG")
    or KNOWLEDGE_BASE_DIR / "minecraft_bot.json"
)


def is_command_message(text):
    """True for slash-prefixed in-game lines (/home, /msg, /login ...).

    These are player commands addressed to the SERVER, not chat addressed to
    Saya - routing them into the conversation layer (or even the LLM) both
    misfires and leaks anything typed after the slash (e.g. /login passwords).
    """
    return str(text or "").lstrip().startswith("/")


DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 25565,
    "username": "Saya",
    "owner_username": os.environ.get("SAYA_MC_OWNER", ""),
    "version": "1.21.5",
    "account": "",           # offline mode when empty
    "status_interval": 30,
    "auto_discard_unplanned": False,
    "auto_attack_hostiles": False,
    "auto_hunt_animals": False,
    "auto_counterattack": False,
    "counterattack_owner": False,
    "auto_mine_ores": False,
    # Default detection distance (blocks) for the auto-mine modes below; an
    # ore mode without its own distance falls back to this.  While
    # persistently following the doctor the worker caps it to 6 so walking
    # is not constantly hijacked by digs.
    "auto_mine_range": 24,
    # Per-ore auto-mine modes (edited in the console): keys of the worker's
    # AUTO_MINE_GROUPS - coal, copper, iron, gold, redstone, lapis, diamond,
    # emerald, quartz, ancient_debris - each with {"enabled": bool,
    # "distance": int}.  Only enabled groups are ever mined; every group
    # disabled (or an empty dict) means nothing is mined even when the
    # master toggle is on.
    "auto_mine_ore_modes": {},
    # Spoken-utterance understanding (SLU) layer: "legacy" = deterministic
    # regex tables + big conversation LLM (default, unchanged behaviour);
    # "local" = route instructions/goals through the fine-tuned in-process
    # model first (slu_local_model directory), falling back to legacy on any
    # failure; "model" = same via an OpenAI-compatible HTTP endpoint.
    "slu_backend": "legacy",
    "slu_local_model": "",
    "slu_model_url": "",
    "slu_model_name": "",
    "combat_detection_range": 48,
    "follow_search_range": 64,
    # Skin selection (applied via a server-side skin mod command).  The
    # default template targets FabricTailor on Fabric 1.21.x servers:
    # "/skin set URL {model} {value}" for http(s) values, or the upload
    # variant "/skin set upload {model} {file}" for local PNGs (the file
    # upload endpoint writes into static/skins AND into the server-visible
    # directory configured here).  Empty value = leave everything as-is.
    "skin_value": "",
    "skin_command": "/skin set URL {model} {value}",
    "skin_model": "classic",
    "skin_url_base": "http://127.0.0.1:8000",
    # Where local PNG uploads are mirrored so the Minecraft server can read
    # them as FabricTailor "upload" files (empty = no local mirror).
    "skin_server_dir": "",
}

# "挖点石头 / 弄点木头" with no explicit count -> 8 (plan section 6.1).
DEFAULT_SOME_COUNT = 8

# Bounds for per-ore auto-mine detection distances (must mirror the worker's
# parseAutoMineOreModes in command.mjs).
AUTO_MINE_ORE_GROUPS = frozenset({
    "coal", "copper", "iron", "gold", "redstone", "lapis",
    "diamond", "emerald", "quartz", "ancient_debris",
})
AUTO_MINE_MIN_RANGE = 4
AUTO_MINE_MAX_RANGE = 64
AUTO_MINE_DEFAULT_RANGE = 24


def normalize_auto_mine_ore_modes(modes):
    """Console per-ore auto-mine modes -> canonical
    {group: {"enabled": bool, "distance": int}}.

    Unknown groups and malformed entries are dropped; distances are clamped
    to [4, 64] with the default filled in when absent.  The worker
    re-validates anyway, but this keeps the persisted config clean."""
    if not isinstance(modes, dict):
        return {}
    normalized = {}
    for group, raw in modes.items():
        key = str(group)
        if key not in AUTO_MINE_ORE_GROUPS or not isinstance(raw, dict):
            continue
        try:
            distance = int(raw.get("distance", AUTO_MINE_DEFAULT_RANGE))
        except (TypeError, ValueError):
            distance = AUTO_MINE_DEFAULT_RANGE
        normalized[key] = {
            "enabled": bool(raw.get("enabled")),
            "distance": max(AUTO_MINE_MIN_RANGE, min(AUTO_MINE_MAX_RANGE, distance)),
        }
    return normalized


_COUNT_WORD_RE = re.compile(r"(?:^|\D)(\d{1,2})(?:\D|$)")


def _count_with_cn_words(value, fallback):
    match = _COUNT_WORD_RE.search(value)
    if match:
        return min(16, int(match.group(1)))
    cn_words = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for word, number in cn_words.items():
        if word in value:
            return min(16, number)
    if "点" in value:
        return DEFAULT_SOME_COUNT
    return fallback


# Polite / softener prefixes that still leave an imperative sentence.
_POLITE_PREFIX_RE = re.compile(
    r"^(?:请你?|麻烦你?|帮我|帮忙|给我|能不能|可不可以|可以|劳驾|拜托|去|快去)+"
)
# Narration / question openers: the sentence talks about an action instead of
# requesting it ("刚才砍树挺有趣的"), so it must never trigger one.
_NARRATION_PREFIX_RE = re.compile(
    r"^(?:刚才|刚刚|之前|上次|昨天|前天|今天早上?|你还|你是|你会|你能|我(?:觉得|喜欢|想问)|如果|要是我)"
)
# Whole-sentence negations ("别跟着我看风景") are ordinary chat.  Applied after
# the emergency stop patterns so 别动/不要动 still work.
_NEGATION_PREFIX_RE = re.compile(r"^(?:别|不要|不用|无需|不想|没必要)")


def detect_player_action(text):
    """Detect direct imperative requests from the owner's in-game chat.

    Every pattern is anchored at the start of the (prefix-stripped) sentence:
    merely mentioning an action in conversation ("刚才砍树挺有趣的") must not
    trigger it (plan section 7.3).  Anything not matching falls through to the
    unified ConversationService LLM layer.
    """
    value = str(text or "").strip().casefold()
    # Pending lines normally include the sender as ``<name> message``.
    value = re.sub(r"^<[^>]{1,80}>\s*", "", value)
    value = _POLITE_PREFIX_RE.sub("", value.strip()).strip()
    if not value or _NARRATION_PREFIX_RE.match(value):
        return None

    # Emergency stop first (before the negation guard: 别动 means stop).
    if re.match(r"^(?:停下|停止|快停下|立刻停止?|别动|不要动|站住)\s*[。！!～~]*$", value) or re.match(
        r"^(?:stop|stay|wait)\s*[.!]?$", value
    ):
        return "stop"
    if re.match(r"^(?:unfollow|stop\s+following)\b", value) or re.match(
        r"^(?:取消跟随|关闭跟随|别跟了|不要跟了|别跟着我了|不用跟了)", value
    ):
        return "unfollow"
    if _NEGATION_PREFIX_RE.match(value):
        return None
    if re.match(r"^(?:status|state)\s*[.?]?$", value) or re.match(
        r"^(?:什么状态|当前状态|任务进度|做到哪了?)", value
    ):
        return "status"
    if re.match(r"^(?:drop|throw)\s+(?:stack|all)\b", value) or re.match(
        r"^(?:扔掉一组|丢掉一组|扔一组|丢一组)", value
    ):
        return "drop stack"
    if re.match(r"^(?:drop|throw)(?:\s+(?:one|item|it))?\s*[.!]?$", value) or re.match(
        r"^(?:扔一个|丢一个|扔出一件?|扔出|丢出)", value
    ):
        return "drop one"
    if re.match(r"^(?:pick\s*up|get)\b", value) or re.match(
        r"^(?:捡起来|捡东西|拾取|捡物品|捡起附近)", value
    ):
        return "pickup nearest"
    if re.match(r"^(?:eat|food)\b", value) or re.match(
        r"^(?:吃东西|吃点东西|进食|吃点|吃个?饭)", value
    ):
        return "eat"
    if re.match(r"^(?:swim|float)\b", value) or re.match(r"^(?:向上游|浮起来|游上去|游到水面)", value):
        return "swim up"
    if re.match(r"^(?:attack|fight|defend)\b", value) or re.match(
        r"^(?:攻击|打怪|反击|揍它|打一下)", value
    ):
        return "attack nearest"
    if re.match(r"^(?:jump|hop)\b", value) or re.match(r"^(?:跳一下|跳起来|跳一跳)", value):
        return "jump"
    if re.match(r"^(?:craft|make)\b", value) or re.match(r"^(?:制作|合成|做)", value):
        item_words = {
            "工作台": "crafting_table", "合成台": "crafting_table",
            "木板": "planks", "木棍": "stick",
            "木剑": "wooden_sword", "木镐": "wooden_pickaxe",
            "木斧": "wooden_axe", "石剑": "stone_sword",
            "石镐": "stone_pickaxe", "铁剑": "iron_sword",
            "铁镐": "iron_pickaxe",
        }
        item_name = next((name for word, name in item_words.items() if word in value), "")
        if not item_name:
            english = re.match(r"^(?:craft|make)\s+(?:some\s+)?([a-z_]+)", value)
            item_name = english.group(1) if english else ""
        if item_name:
            count_match = _COUNT_WORD_RE.search(value)
            count = min(64, int(count_match.group(1))) if count_match else 1
            return f"craft {item_name} {count}"
    if re.match(r"^(?:chop|woodcut|cut)\b", value) or re.match(
        r"^(?:砍|伐木|弄点木头|弄些木头|弄点树)", value
    ):
        return "chop"
    if re.match(r"^挖脚下", value) or re.match(r"^(?:mine|dig)\s+below\b", value):
        return "mine below"
    if re.match(r"^(?:挖这个|挖这方块|挖一下这)", value) or re.match(
        r"^(?:mine|dig)(?:\s+(?:this|the|a))?\s*block\b", value
    ):
        return "mine nearby"
    block_words = {
        "铁": "iron_ore", "煤": "coal_ore", "钻石": "diamond_ore",
        "金": "gold_ore", "铜": "copper_ore", "红石": "redstone_ore",
        "青金石": "lapis_ore", "绿宝石": "emerald_ore",
        # 石头默认=圆石（生存可稳定获得）；明确要原版 stone 需走澄清流程。
        "石头": "cobblestone", "圆石": "cobblestone",
        "泥土": "dirt", "沙子": "sand",
        "砂砾": "gravel", "黏土": "clay", "原木": "_log",
    }
    if re.match(r"^(?:collect|gather|harvest|mine|dig)\b", value) or re.match(
        r"^(?:收集|采集|挖|弄|采)", value
    ):
        count = _count_with_cn_words(value, 1)
        for word, block_name in block_words.items():
            if word in value:
                return f"collect {block_name} {count}"
        english = re.match(
            r"^(?:collect|gather|harvest|mine|dig)\s+(?:some\s+)?"
            r"([a-z_]+(?:\s+(?:ore|block|logs?))?)(?:\s+(\d{1,2}))?\s*$",
            value,
        )
        if english:
            block_name = english.group(1).replace(" ", "_")
            if block_name.endswith("_block"):
                block_name = block_name[:-6]
            return f"collect {block_name} {min(16, int(english.group(2) or 1))}"
    if re.match(r"^(?:follow|跟)(?:着|随)?(?:我|主人|着他|着她)?来?$", value) or re.match(
        r"^(?:跟随|跟着)(?:我|主人)?$|^跟我来$|^开启跟随$|^持续跟随$", value
    ) or re.match(r"^follow(?:\s+(?:me|us))?\s*$", value):
        return "follow Doctor"
    if re.match(r"^come(?:\s+(?:here|to\s+me))?\s*$", value) or re.match(
        r"^(?:过来|来我身边|来我这里|到我身边|到我这里|靠近我)(?:一下)?$", value
    ):
        return "come"
    return None
