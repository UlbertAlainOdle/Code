"""Evaluate an SLU backend against deterministically-labeled utterances.

The benchmark labels are computed from the deterministic layers themselves
(parse_intent / parse_task_intent) over phrases pinned by the existing test
suites, so they are ground truth by construction.  The model backend is then
measured on route agreement (and action/goal exactness where applicable).

    python -B scripts/eval_slu_backend.py --backend legacy
    python -B scripts/eval_slu_backend.py --backend model   # uses the
        conversation API credentials, or SAYA_SLU_MODEL_URL/MODEL override
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services import slu  # noqa: E402
from saya_app.services.conversation import parse_intent  # noqa: E402
from saya_app.services.task_intent import parse_task_intent  # noqa: E402

# Phrases pinned by tests/test_smelt_routing.py, tests/test_meat_phrases.py,
# tests/test_potion_intent_corpus.py and smoke_conversation_routing.py.
BENCHMARK = [
    # (text, "chat") for pure conversation
    ("跟着我", "auto"), ("follow Doctor", "auto"), ("停下", "auto"),
    ("come here", "auto"), ("status", "auto"), ("inventory", "auto"),
    ("jump", "auto"), ("pickup nearest", "auto"),
    ("烧炼粗铁", "auto"), ("帮我烧粗铁", "auto"), ("炼3个铁锭", "auto"),
    ("挖点石头", "auto"), ("挖铁矿", "auto"), ("砍树", "auto"),
    ("deposit iron_ingot all", "auto"), ("withdraw bread 3", "auto"),
    ("chest find", "auto"),
    ("把铁锭存进箱子", "auto"), ("存3个铁锭到箱子", "auto"),
    ("从箱子里取出3个面包", "auto"),
    ("把背包里的泥土存到箱子里去", "auto"),
    ("帮我把圆石存进附近的箱子", "auto"),
    ("从箱子里拿出2个铁锭", "auto"),
    ("从木桶里取出面包", "auto"),
    # goals (TaskIntent layer)
    ("我需要一瓶治疗药水", "auto"), ("给我一瓶治疗药水", "auto"),
    ("帮我酿造一瓶治疗药水", "auto"), ("请帮我酿造两瓶治疗药水吧", "auto"),
    ("给我准备一瓶抗火药水", "auto"), ("给我一把木剑", "auto"),
    ("酿造一瓶治疗药水", "auto"), ("给我自己留两瓶治疗药水", "auto"),
    ("给我猪肉", "auto"), ("给我熟猪排", "auto"), ("给我烤鸡", "auto"),
    ("给我8个铁锭", "auto"),
    # chat (negative corpus)
    ("我不需要治疗药水了", "chat"), ("如果去下界可能需要抗火药水", "chat"),
    ("你会做治疗药水吗", "chat"), ("不要因为我提到石头就去挖", "chat"),
    ("今天天气真不错啊", "chat"), ("你最喜欢玩什么游戏", "chat"),
    ("刚才砍树挺有趣的", "chat"),
]


def label_for(text):
    """Deterministic ground truth: route + canonical payload."""
    command = parse_intent(text)
    if command in ("stop", "cancel task"):
        # Conversation routes these as task-control (emergency layer), and
        # the SLU contract's "task" covers them.
        return {"route": "task", "direct_action": command, "goal": None}
    if command:
        return {"route": "direct_action", "direct_action": command, "goal": None}
    try:
        intent = parse_task_intent(text)
    except Exception:
        intent = None
    # Mirror the real heuristic exactly: only REQUEST modality is a goal
    # (negation/conditional phrases carry an item but are chat).
    if isinstance(intent, dict) and intent.get("item") and intent.get("modality") == "request":
        goal = {
            "type": "obtain_and_deliver" if intent.get("disposition") == "deliver" else "obtain",
            "target": intent.get("item"),
            "count": intent.get("count") or 1,
            "recipient": intent.get("recipient") or "Doctor",
        }
        return {"route": "goal", "direct_action": None, "goal": goal}
    return {"route": "chat", "direct_action": None, "goal": None}


def _canonical_action(action):
    """Fold actions the worker treats identically ('come Doctor' == 'come')."""
    if action == "come Doctor":
        return "come"
    return action


def normalize_model_result(segments):
    """Fold the segment list into one (route, action, goal) comparison key.

    A mixed utterance becomes route=mixed with its first instruction payload.
    """
    route = "chat"
    action = None
    goal = None
    for segment in segments.get("segments", []):
        if segment["route"] == "chat":
            continue
        if segment["route"] == "clarify":
            route = route if route != "chat" else "clarify"
            continue
        route = "mixed" if route != "chat" else segment["route"]
        action = action or segment.get("direct_action")
        goal = goal or segment.get("goal")
    return route, _canonical_action(action), goal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("legacy", "model"), default="legacy")
    parser.add_argument("--out", default="", help="write a per-phrase report JSON here")
    args = parser.parse_args()

    credentials = None
    if args.backend == "model":
        try:
            from saya_app.services.conversation import ConversationService

            credentials = ConversationService().credentials()
        except Exception as exc:
            print(f"cannot load conversation credentials: {exc}")
            return 1

    route_total = route_ok = payload_ok = 0
    report = []
    for text, _kind in BENCHMARK:
        truth = label_for(text)
        segments, used = slu.segment_utterance(text, backend=args.backend, credentials=credentials)
        got_route, got_action, got_goal = normalize_model_result(segments or {"segments": []})
        truth_payload = (truth["direct_action"], truth["goal"])
        got_payload = (got_action, got_goal)
        route_total += 1
        route_ok += got_route == truth["route"]
        payload_ok += got_payload == truth_payload
        report.append({
            "text": text,
            "truth": truth["route"],
            "got": got_route,
            "backend": used,
            "match": got_route == truth["route"],
            "payload_match": got_payload == truth_payload,
            "direct_action": got_action,
            "goal": got_goal,
        })

    print(f"backend={args.backend}  route agreement: {route_ok}/{route_total} "
          f"({100.0 * route_ok / max(1, route_total):.1f}%)  "
          f"payload agreement: {payload_ok}/{route_total} "
          f"({100.0 * payload_ok / max(1, route_total):.1f}%)")
    model_rows = sum(1 for row in report if row["backend"] == "model")
    if args.backend == "model" and model_rows < route_total:
        print(f"  WARNING: only {model_rows}/{route_total} rows were answered by the "
              f"model - the rest silently fell back to legacy and INFLATE the score. "
              f"Set SAYA_SLU_DEBUG=1 to see why.")
    for row in report:
        if not row["match"] or not row["payload_match"]:
            print(f"  MISMATCH [{row['text']}] truth={row['truth']} got={row['got']} "
                  f"action={row['direct_action']!r} goal={row['goal']}")
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
