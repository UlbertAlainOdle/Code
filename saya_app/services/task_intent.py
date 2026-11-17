"""Structured task-intent layer: text -> TaskIntent for the Minecraft chat.

Sits between the deterministic command matcher (``conversation.parse_intent``,
unchanged "text -> one command") and the LLM layer.  It resolves the *high
frequency* request phrasings that anchored regexes cannot enumerate, and the
disposition defaults the old pipeline lacked:

    我需要一瓶治疗药水   -> request  potion/healing x1 -> deliver to speaker
    给我一瓶治疗药水     -> request, deliver first from existing stock
    帮我酿造一瓶治疗药水 -> request, method=brew, deliver
    酿造一瓶治疗药水     -> request, method=brew, Saya keeps and reports
    给自己留两瓶治疗药水 -> request, Saya keeps
    我不需要治疗药水了   -> negation  (never creates a task)
    如果去下界可能需要抗火药水 -> conditional (never creates a task)

The parser is deliberately conservative: it returns None whenever it cannot
fully resolve a request (unknown item, question form, ambiguous recipient) so
the message falls through to the LLM layer unchanged.
"""

from __future__ import annotations

import re

from . import potions
from .minecraft_items import item_catalog

# Politeness stripped for matching, but 帮我/帮忙 are KEPT: they carry the
# personal-benefit signal that decides the delivery default.
_POLITENESS_RE = re.compile(r"^(?:请你?|麻烦你?|劳驾|拜托|能不能|可不可以|可以|好的?[,，]?)+")
_TRAILING_RE = re.compile(r"[吧啊呀呢啦哦嘛么吗了。！!？?~～\s]+$")

# Negation is judged together with task content: the marker starts the
# sentence AND a resolvable item follows, so "不要因为我提到石头就去挖"
# (item not adjacent, unresolvable tail) stays ordinary chat.
_NEGATION_RE = re.compile(r"^(?:我)?(?:不需要|不用|不必|别|不想|无需|没必要)")
_CONDITIONAL_RE = re.compile(r"如果|万一|要是|假如|若是|的话")
_QUESTION_RE = re.compile(r"(?:你会|你会不会|你会做|能不能做|可不可以做|知道怎么)|(?:有没有)|(?:吗|么|？|\?)\s*$")

# Personal-need verbs: the speaker is the beneficiary -> deliver by default.
_NEED_RE = re.compile(
    r"(?:我)?(?:需要|想要|想喝|想用)|(?:给我)(?:来|搞|弄|准备|拿|带|带点)|(?:来一|来瓶|来个|来把)|(?:替我)|"
    r"(?:缺)(?:一|瓶|个|点)?|(?:帮我)(?:准备|搞|弄|拿|带)|(?:准备一)"
)
# 帮我 + action verb: explicit request aimed at the speaker -> deliver.
# 烧/炼 excluded: smelting phrasings resolve through the deterministic smelt
# handler, which knows the input->product mapping.
_HELP_ACTION_RE = re.compile(
    r"(?:帮我|帮忙)(?:酿造|酿|做|制作|合成|收集|挖|采|砍|找|拿|带)"
)
# Bare action verbs without any beneficiary marker -> Saya keeps the result.
# Deliberately EXCLUDED: 烧/炼 (smelting) and 收集/挖/采/砍 (gathering) -
# those have dedicated deterministic handlers whose semantics the bare-goal
# layer cannot reproduce.  The old set turned 烧炼粗铁 into "obtain raw_iron"
# (go mining!) instead of smelting it into ingots.
_ACTION_RE = re.compile(r"酿造|酿一|制作|合成|^做")
_NEED_ALL_RE = re.compile(r"我们需要|大家一起|备用|准备着")
_KEEP_RE = re.compile(
    r"给自己留|自己留|你留着|先拿着|先放着|自用|留着自|不用给我|自己收着|存着"
)
_RECIPIENT_RE = re.compile(r"(?:交给|送给|拿给|递给|丢给)(?P<who>[A-Za-z0-9_]{1,24}|我)")
_GIVE_ME_RE = re.compile(r"给我|交给我|送给我|递给我")

_COUNT_RE = re.compile(r"(?P<count>\d{1,3}|[一二两三四五六七八九十]+)\s*(?:瓶|个|把|件|份|支|组|块|桶|片)")

# End-anchored item noun for non-potion requests: "...给我一把木剑" -> 木剑.
_TAIL_ITEM_RE = re.compile(
    r"(?:(?:一|两|二|三|四|五|六|七|八|九|十|\d{1,3})\s*(?:瓶|个|把|件|份|支|组|块|桶|片))?"
    r"(?P<name>[\u4e00-\u9fa5A-Za-z0-9_]{1,20})\s*$"
)

_COUNT_WORDS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _clean(text):
    text = _POLITENESS_RE.sub("", str(text or "")).strip()
    text = _TRAILING_RE.sub("", text).strip()
    text = re.sub(r"^帮个忙[，,]?", "", text).strip()
    return text


def _count_of(text, default=1):
    match = _COUNT_RE.search(text)
    if not match:
        return default
    raw = match.group("count")
    if raw.isdigit():
        return max(1, min(640, int(raw)))
    return max(1, min(640, _COUNT_WORDS.get(raw, default)))


def _resolve_item(text):
    """Return (canonical_target, display) or (None, None).

    Potions resolve through the variant-aware registry (``potion/healing``);
    everything else through the item catalog's Chinese aliases.
    """
    potion = potions.parse_potion_target(text)
    if potion:
        label = potions.potion_chinese(potion["container"], potion["potion"])
        return potion["key"], label
    match = _TAIL_ITEM_RE.search(text.strip())
    if not match:
        return None, None
    name = match.group("name").strip()
    if not name:
        return None, None
    catalog = item_catalog()
    # The end-anchored regex swallows leading sentence words ("给我一把木剑"
    # matches as one blob), so resolve suffixes of the tail noun.  LONGEST
    # first: Chinese item names are material+piece compounds, and a shorter
    # suffix is often a valid alias for a DIFFERENT item - shortest-first
    # turned 钻石剑 into 石剑 (stone_sword) and 钻石头盔 into 石头盔.  Same
    # most-specific-wins policy as resolve_reference and the potion parser.
    for cut in range(len(name)):
        candidate = name[cut:]
        canonical = catalog.lookup(candidate)
        if canonical:
            return canonical, catalog.chinese(canonical) or canonical
    return None, None


def _recipient_of(text, speaker="Doctor"):
    match = _RECIPIENT_RE.search(text)
    if match:
        who = match.group("who")
        if who == "我":
            return speaker
        if re.match(r"^[A-Za-z0-9_]{1,24}$", who):
            return who
    if _GIVE_ME_RE.search(text):
        return speaker
    return None


def parse_task_intent(raw_text, speaker="Doctor"):
    """Parse one message into a TaskIntent dict, or None when this layer
    should not handle it (ordinary chat, questions, unresolvable items).

    Intent fields:
      modality    request | negation | conditional
      item        canonical target ("potion/healing", "wooden_sword")
      display     human label for replies
      count       1..640
      recipient   player who should end up with the item (deliver only)
      disposition deliver | keep
      method      brew | unspecified
      confidence  0..1
    """
    original = str(raw_text or "").strip()
    text = _clean(original)
    if not text:
        return None
    if _QUESTION_RE.search(text):
        return None

    item, display = _resolve_item(text)
    has_task_content = item is not None
    if _CONDITIONAL_RE.search(text):
        if has_task_content:
            return {"modality": "conditional", "item": item, "display": display,
                    "count": 0, "recipient": "", "disposition": "keep",
                    "method": "unspecified", "confidence": 0.9}
        return None
    if _NEGATION_RE.match(text):
        if has_task_content:
            return {"modality": "negation", "item": item, "display": display,
                    "count": 0, "recipient": "", "disposition": "keep",
                    "method": "unspecified", "confidence": 0.9}
        return None
    if not has_task_content:
        return None

    need = bool(_NEED_RE.search(text))
    help_action = bool(_HELP_ACTION_RE.search(text))
    bare_action = bool(_ACTION_RE.search(text))
    need_all = bool(_NEED_ALL_RE.search(text))
    keep_marked = bool(_KEEP_RE.search(text))
    recipient = _recipient_of(text, speaker)

    is_request = need or help_action or bare_action or keep_marked or recipient is not None
    if not is_request:
        return None

    method = "brew" if re.search(r"酿造|酿一|^(?:酿)", text) else "unspecified"

    if keep_marked:
        disposition, resolved_recipient = "keep", ""
    elif recipient is not None:
        disposition, resolved_recipient = "deliver", recipient
    elif need or help_action:
        disposition, resolved_recipient = "deliver", speaker
    elif need_all:
        disposition, resolved_recipient = "keep", ""
    else:
        # Bare action ("酿造一瓶治疗药水"): Saya keeps the result and reports.
        disposition, resolved_recipient = "keep", ""

    count = _count_of(text, 1)
    if count <= 0:
        count = 1
    confidence = 0.85 if disposition == "deliver" else 0.8
    return {
        "modality": "request",
        "item": item,
        "display": display or item,
        "count": count,
        "recipient": resolved_recipient,
        "disposition": disposition,
        "method": method,
        "confidence": confidence,
    }


def intent_to_goal(intent, speaker="Doctor"):
    """Map a request intent onto a planner goal object.

    deliver -> obtain_and_deliver (existing stock is consumed first by the
    planner); keep -> obtain.  Raises ValueError on an unexecutable intent.
    """
    if not intent or intent.get("modality") != "request":
        raise ValueError("不是可执行的任务意图")
    target = str(intent.get("item") or "").strip()
    if not target:
        raise ValueError("缺少目标物品")
    try:
        count = max(1, min(640, int(intent.get("count") or 1)))
    except (TypeError, ValueError):
        count = 1
    if intent.get("disposition") == "deliver":
        recipient = str(intent.get("recipient") or speaker).strip()
        if not re.match(r"^[A-Za-z0-9_]{1,24}$", recipient):
            recipient = speaker
        return {"type": "obtain_and_deliver", "target": target, "count": count, "recipient": recipient}
    return {"type": "obtain", "target": target, "count": count}
