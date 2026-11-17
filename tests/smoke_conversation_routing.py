"""Offline routing tests for the unified ConversationService (2026-09 plan
section 7 + 8.2 corpus subset).

Covers the deterministic command layer, the catalog-backed direct-action
validation, goal validation, the new LLM output schema and the negative
corpus - without touching core_services or any network API.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
tmp.close()
os.environ["SAYA_MC_BOT_CONFIG"] = tmp.name
# Smoke phrases must not pollute the live SLU corpus.
os.environ["SAYA_SLU_CORPUS"] = "0"

from saya_app.services.conversation import (  # noqa: E402
    ConversationService,
    _explicit_delivery_goal,
    _looks_like_action_request,
    parse_intent,
    validate_goal,
)
from saya_app.services.minecraft_catalog import validate_direct_action  # noqa: E402

# 1. Standard English commands must map 1:1.
standard = {
    "follow Doctor": "follow Doctor",
    "follow me": "follow Doctor",
    "unfollow": "unfollow",
    "come": "come",
    "stop": "stop",
    "status": "status",
    "goto -100 64 200": "goto -100 64 200",
    "chop 4": "chop 4",
    "collect dirt 4": "collect dirt 4",
    "collect iron_ore 3": "collect iron_ore 3",
    "mine -100 63 200": "mine -100 63 200",
    "craft planks 4": "craft planks 4",
    "brew healing 2": "brew healing 2",
    "cauldron fill water": "cauldron fill water",
    "drop one": "drop one",
    "drop stack": "drop stack",
    "pickup nearest": "pickup nearest",
    "eat": "eat",
    "jump": "jump",
    "swim up": "swim up",
    "attack zombie": "attack zombie",
    "say 你好": "say 你好",
    "inventory": "inventory",
    "equip wooden_pickaxe": "equip wooden_pickaxe",
    "give bread 2 to Doctor": "give bread 2 to Doctor",
    "drop rotten_flesh 5": "drop rotten_flesh 5",
}
for text, expected in standard.items():
    got = parse_intent(text)
    assert got == expected, f"{text!r} -> {got!r}, expected {expected!r}"
print("standard commands: OK")

# 2. Recommended Chinese phrasings.
chinese = {
    "跟着我": "follow Doctor",
    "不要跟了": "unfollow",
    "过来": "come",
    "停下": "stop",
    "当前状态": "status",
    "去坐标 -100 64 200": "goto -100 64 200",
    "砍4块原木": "chop 4",
    "砍点树": "chop 8",
    "收集4个泥土": "collect dirt 4",
    "挖3个铁矿": "collect iron_ore 3",
    "击杀3只僵尸": "attack zombie 3",
    "kill zombie 4": "attack zombie 4",
    "烧炼3个铁锭": "smelt raw_iron iron_ingot 3",
    "酿造2瓶治疗药水": "brew healing 2",
    "酿造治疗药水": "brew healing 1",
    "做一瓶强效力量药水": "brew strong_strength 1",
    "给炼药锅装水": "cauldron fill water",
    "取消当前任务": "cancel task",
    "挖点石头": "collect cobblestone 8",   # 石头 defaults to cobblestone (plan 4.4)
    "挖坐标 -100 63 200": "mine -100 63 200",
    "制作4个木板": "craft planks 4",
    "做一把木剑": "craft wooden_sword 1",
    "扔一个": "drop one",
    "扔掉一组": "drop stack",
    "捡起附近物品": "pickup nearest",
    "吃东西": "eat",
    "跳一下": "jump",
    "向上游": "swim up",
    "攻击僵尸": "attack zombie",
}
for text, expected in chinese.items():
    got = parse_intent(text)
    assert got == expected, f"{text!r} -> {got!r}, expected {expected!r}"
print("recommended Chinese phrasings: OK")

# 3. Polite variants still route.
polite = {
    "请你跟着我": "follow Doctor",
    "麻烦过来一下": "come",
    "帮我砍4块原木": "chop 4",
    "请你停下": "stop",
    "帮我挖点石头": "collect cobblestone 8",
}
for text, expected in polite.items():
    got = parse_intent(text)
    assert got == expected, f"{text!r} -> {got!r}, expected {expected!r}"
print("polite Chinese variants: OK")

# 4. Ordinary chat that merely mentions keywords must NOT trigger commands.
counter_examples = [
    "我昨天砍了很多树",
    "我昨天挖到3个钻石",
    "你知道怎么制作木板吗",
    "讲个关于跟着我的故事",
    "什么是minecraft里的iron ore",
    "别跟着我看风景",
    # plan 7.3 negative examples
    "刚才砍树挺有趣的",
    "你会做木剑吗",
    "如果有镐就方便了",
    "不要因为我提到石头就去挖",
]
for text in counter_examples:
    got = parse_intent(text)
    assert got is None, f"false positive: {text!r} -> {got!r}"
print("ordinary chat counter-examples: OK")

# 5. Catalog direct-action validation: dangerous/malformed commands rejected.
assert validate_direct_action("tp", {"x": 1, "y": 2, "z": 3}) is None
assert validate_direct_action("say", {"text": "/tp Doctor Saya"}) is None
assert validate_direct_action("raw", {"text": "/time set day"}) is None
assert validate_direct_action("place", {"block": "tnt"}) is None
assert validate_direct_action("collect", {"block": "dirt", "count": 3}) == "collect dirt 3"
assert validate_direct_action("collect", {"block": "iron_ore", "count": 99}) == "collect iron_ore 32"
assert validate_direct_action("goto", {"x": -100, "y": 64, "z": 200}) == "goto -100 64 200"
assert validate_direct_action("goto", {"x": "a"}) is None
assert validate_direct_action("follow", {"target": "Kurosawa_Jin"}) == "follow Kurosawa_Jin"
assert validate_direct_action("follow", {"target": "bad name!"}) == "follow Doctor"
assert validate_direct_action("attack", {"target": "zombie"}) == "attack zombie"
assert validate_direct_action("drop", {"mode": "stack"}) == "drop stack"
assert validate_direct_action("give", {"item": "bread", "count": 2}) == "give bread 2 to Doctor"
assert validate_direct_action("equip", {"item": "wooden_pickaxe"}) == "equip wooden_pickaxe"
assert validate_direct_action("brew", {"potion": "healing", "count": 2}) == "brew healing 2"
assert validate_direct_action("brew", {"potion": "luck", "count": 1}) is None
assert validate_direct_action("cauldron", {"action": "fill", "fluid": "water"}) == "cauldron fill water"
print("catalog direct-action validation: OK")

# 6. Goal validation (plan 7.1 schema).
goal, err = validate_goal({"type": "obtain_and_deliver", "target": "木剑", "count": 1, "recipient": "Doctor"})
assert err is None and goal["target"] == "wooden_sword" and goal["count"] == 1, (goal, err)
goal, err = validate_goal({"type": "craft", "target": "工作台", "count": 2})
assert goal["target"] == "crafting_table" and goal["count"] == 2
goal, err = validate_goal({"type": "travel", "coords": [10, 64, -32]})
assert goal["coords"] == [10, 64, -32]
goal, err = validate_goal({"type": "combat", "target": "zombie", "count": 3})
assert goal["target"] == "zombie" and goal["count"] == 3
assert validate_goal({"type": "obtain", "target": "不存在的物品"})[0] is None
assert validate_goal({"type": "teleport", "target": "x"})[0] is None
assert validate_goal({"type": "obtain"})[0] is None
assert validate_goal(None)[0] is None
print("goal validation: OK")

# 7. New LLM output schema parsing.
parse = ConversationService._parse_structured
result = parse(json.dumps({
    "route": "goal",
    "reply": "好呀，我来做。",
    "goal": {"type": "obtain_and_deliver", "target": "wooden_sword", "count": 1, "recipient": "Doctor"},
    "confidence": 0.9,
}), "帮我做一把木剑并给我")
assert result["route"] == "goal" and result["goal"]["target"] == "wooden_sword", result

result = parse(json.dumps({
    "route": "direct_action",
    "reply": "好，我过来。",
    "direct_action": {"name": "come", "arguments": {}},
}), "过来")
assert result["direct_action"] == "come", result

# A contradictory route label must not discard a valid payload.
result = parse(json.dumps({
    "route": "chat",
    "reply": "I will come over.",
    "direct_action": {"name": "come", "arguments": {}},
}), "到我这里")
assert result["route"] == "direct_action" and result["direct_action"] == "come", result

assert _looks_like_action_request("到我这里带走熔炉") is True
assert _looks_like_action_request("帮我做一把木剑") is True
assert _looks_like_action_request("你会做木剑吗") is False
assert _looks_like_action_request("刚才砍树挺有趣的") is False
assert _looks_like_action_request("给我讲个故事") is False
assert _looks_like_action_request("来聊聊今天发生的事") is False

# legacy multi-command output: only ONE command survives, never a burst
result = parse(json.dumps({
    "route": "task",
    "reply": "ok",
    "commands": [
        {"name": "collect", "arguments": {"block": "dirt", "count": 2}},
        {"name": "collect", "arguments": {"block": "sand", "count": 2}},
    ],
}), "collect stuff")
assert result["direct_action"] == "collect dirt 2", result

# string direct_action form
result = parse(json.dumps({
    "route": "direct_action", "reply": "ok", "direct_action": "collect iron_ore 3",
}), "mine iron")
assert result["direct_action"] == "collect iron_ore 3", result

# dangerous string action is dropped
result = parse(json.dumps({
    "route": "direct_action", "reply": "ok", "direct_action": "/tp Doctor Saya",
}), "tp me")
assert result["direct_action"] is None and result["route"] == "chat", result

# invalid goal -> clarify
result = parse(json.dumps({
    "route": "goal", "reply": "", "goal": {"type": "obtain", "target": "火星石头"},
}), "挖火星石头")
assert result["route"] == "clarify", result

# free text -> chat
result = parse("今天天气真不错呀", "今天天气真不错呀")
assert result["route"] == "chat" and result["reply"], result

# Truncated JSON is never exposed as dialogue.  A clearly imperative
# craft-and-deliver request has a conservative local goal fallback.
result = parse('{"route": "goal", "reply": "好"', "帮我做一把木剑并交给我")
assert result.get("_parse_error") is True, result
fallback = _explicit_delivery_goal("帮我做一把木剑并交给我")
assert fallback == {
    "type": "obtain_and_deliver", "constraints": [],
    "target": "wooden_sword", "count": 1, "recipient": "Doctor",
}, fallback
assert _explicit_delivery_goal("你会做木剑并给我吗") is None
print("LLM schema parsing: OK")

# 7b. Regression: a Chinese task with an English model reply keeps its valid
# action instead of being rewritten to chat and losing all work.
guard_sent = []
guard = ConversationService()
guard.credentials = lambda: {"configured": True, "api_url": "stub", "api_key": "stub", "model": "stub"}
guard._call_llm = lambda *a, **k: ({
    "route": "direct_action",
    "reply": "I will come over and handle it.",
    "direct_action": "come",
    "goal": None,
    "memory_candidates": [],
    "confidence": 0.9,
}, 1, "stub")
guard._send_command = lambda command, origin="saya": guard_sent.append(command) or True
guard._record = lambda *a, **k: None
result = guard.handle_message("desktop", "Doctor", "到我这里带走熔炉", context={})
assert result["route"] == "direct_action", result
assert result["commands"] == ["come"] and guard_sent == ["come"], result
assert "我听到了" not in result["reply"] and any("\u4e00" <= ch <= "\u9fff" for ch in result["reply"]), result

# The same language correction preserves a valid high-level goal and starts
# planning rather than dropping it.
guard._call_llm = lambda *a, **k: ({
    "route": "goal", "reply": "I will prepare it.", "direct_action": None,
    "goal": {"type": "obtain", "target": "stick", "count": 1, "constraints": []},
    "memory_candidates": [], "confidence": 0.9,
}, 1, "stub")
guard._execute_goal = lambda goal: ("好，我已经创建了制作木棍的计划。", {"plan_id": "plan-smoke"})
result = guard.handle_message("desktop", "Doctor", "帮我准备一根木棍", context={})
assert result["route"] == "goal" and result["goal"]["target"] == "stick", result
assert "创建了" in result["reply"] and "我听到了" not in result["reply"], result

# Future empty-chat provider regressions are surfaced as clarification.
guard._call_llm = lambda *a, **k: ({
    "route": "chat", "reply": "I heard you.", "direct_action": None,
    "goal": None, "memory_candidates": [], "confidence": 0.2,
}, 1, "stub")
result = guard.handle_message("desktop", "Doctor", "到我这里带走熔炉", context={})
assert result["route"] == "clarify" and result["commands"] == [], result
assert "任务" in result["reply"] and "我听到了" not in result["reply"], result

# Correct parsing plus a broken worker pipe is not reported as execution.
pipe_guard = ConversationService()
pipe_guard._record = lambda *a, **k: None
pipe_guard._send_command = lambda *a, **k: False
result = pipe_guard.handle_message("desktop", "Doctor", "过来", context={})
assert result["route"] == "clarify" and result["commands"] == [], result
assert "没有发出" in result["reply"] and result["error"], result
print("no-action acknowledgement regressions: OK")

# 8. Deterministic commands produce a persona reply (plan 7.4) via a stub
#    worker service that records sends.
from saya_app.services import minecraft_mineflayer  # noqa: E402

sent = []
minecraft_mineflayer.minecraft_bot_service._send = lambda payload, process=None: (
    sent.append(payload) or True
)
minecraft_mineflayer.minecraft_bot_service.state["running"] = True

service = ConversationService()
service._record = lambda *a, **k: None  # keep the test off core_services


def handle(text):
    return service.handle_message("desktop", "Doctor", text, context={"mc_status": {}})


result = handle("过来。")
assert result["commands"] == ["come"] and result["reply"] == "好，我这就过来。", result
assert sent[-1]["type"] == "command" and sent[-1]["text"] == "come" and sent[-1]["origin"] == "saya" and sent[-1]["quiet"] is False and sent[-1].get("task_id")

result = handle("跟着我")
assert result["reply"] == "嗯，我会一直跟着你。"

result = handle("停止")
assert result["source"] == "emergency_rule" and sent[-1]["text"] == "stop"

result = handle("你会做木剑吗")
assert result["commands"] == []  # ordinary chat, no action

# vague drop of a protected held item asks instead of acting
minecraft_mineflayer.minecraft_bot_service.state["runtime"] = {
    "inventory": [{"name": "diamond_sword", "count": 1, "slot": 36}],
    "held_item": "diamond_sword",
}
result = handle("扔一个")
assert result["route"] == "clarify" and "确定要扔" in result["reply"], result
assert all(p["text"] != "drop one" for p in sent[-3:])

# explicit junk drop goes through (dirt is not protected; rotten flesh as
# the LAST food stack would correctly stay protected per plan section 3.3)
minecraft_mineflayer.minecraft_bot_service.state["runtime"] = {
    "inventory": [{"name": "dirt", "count": 7, "slot": 36}],
    "held_item": "dirt",
}
result = handle("扔一个")
assert result["commands"] == ["drop one"], result

minecraft_mineflayer.minecraft_bot_service.state["running"] = False
minecraft_mineflayer.minecraft_bot_service.state["runtime"] = {}
print("deterministic replies + drop guard: OK")

# 9. Routing corpus (8.2): deterministic-layer subset, offline.  'goal' rows
# are the LLM-evaluation set (complex goals); they are intentionally NOT
# asserted here beyond never being plain false positives.
corpus_path = Path(__file__).resolve().parent / "data" / "routing_corpus.json"
corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
chat_corpus = [row for row in corpus if row["expected_route"] == "chat"]
task_corpus = [row for row in corpus if row["expected_route"] == "task"]
clarify_corpus = [row for row in corpus if row["expected_route"] == "clarify"]
goal_corpus = [row for row in corpus if row["expected_route"] == "goal"]
for row in chat_corpus:
    got = parse_intent(row["text"])
    assert got is None, f"corpus false positive: {row['text']!r} -> {got!r}"
for row in task_corpus:
    got = parse_intent(row["text"])
    assert got is not None, f"corpus miss: {row['text']!r}"
for row in clarify_corpus:
    got = parse_intent(row["text"])
    assert got is None, f"corpus clarify must not fire deterministically: {row['text']!r}"
print(f"routing corpus ({len(corpus)} rows: {len(chat_corpus)} chat / "
      f"{len(task_corpus)} task / {len(goal_corpus)} goal(llm-set) / "
      f"{len(clarify_corpus)} clarify): OK")

Path(tmp.name).unlink(missing_ok=True)
print("\nConversation routing smoke test passed.")
