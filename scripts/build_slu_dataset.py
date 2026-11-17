"""Build a fine-tune dataset for the SLU understanding model.

Input : knowledge_base/slu_corpus/utterances.jsonl (record_turn output)
Output: JSONL of {"messages": [system, user, assistant]} chat-format rows,
        ready for LoRA SFT (scripts/train_slu_lora.py) or any LLM trainer.

The assistant payload uses the exact segmentation contract from
saya_app/services/slu.py, so the fine-tuned model drops straight into the
"model" backend without prompt changes.

    python -B scripts/build_slu_dataset.py [--source PATH] [--out PATH] [--limit N]

Filtering: rows without a usable route label are skipped; chat-by-default
rows (route=chat with no action) are KEPT at a bounded ratio because the
model must learn what NOT to act on - all-chat skews toward inaction, but
zero-chat makes every utterance a command.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.config import KNOWLEDGE_BASE_DIR  # noqa: E402
from saya_app.services import slu  # noqa: E402

CHAT_KEEP_RATIO = 0.5


def label_to_assistant(row):
    route = str(row.get("route") or "")
    action = row.get("direct_action")
    goal = row.get("goal")
    if route == "task":
        payload = {
            "type": "instruction",
            "route": "task",
            "direct_action": action or (row.get("commands") or [None])[0],
            "goal": None,
        }
    elif route in ("direct_action", "task_command"):
        payload = {
            "type": "instruction",
            "route": "direct_action",
            "direct_action": action or (row.get("commands") or [None])[0],
            "goal": None,
        }
    elif route in ("goal", "mixed") and isinstance(goal, dict) and goal.get("target"):
        payload = {
            "type": "instruction" if route == "goal" else "mixed",
            "route": "goal",
            "direct_action": action,
            "goal": goal,
        }
    elif route == "chat":
        payload = {"type": "chat", "route": "chat", "direct_action": None, "goal": None}
    else:
        return None
    payload["text"] = str(row.get("text") or "")
    # Mixed utterances carry their multi-segment answer (chat + task) from
    # the synthesizer - prefer it over the single-segment fold.
    if isinstance(row.get("mixed_segments"), list) and row["mixed_segments"]:
        return json.dumps({"segments": row["mixed_segments"]}, ensure_ascii=False)
    return json.dumps({"segments": [payload]}, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", default=None,
                        help="corpus JSONL file; repeatable. Defaults to the "
                             "live capture + the synthetic corpus.")
    parser.add_argument("--out", default=str(KNOWLEDGE_BASE_DIR / "slu_corpus" / "slu_sft_dataset.jsonl"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    sources = [Path(p) for p in (args.source or [str(slu.CORPUS_FILE), str(
        KNOWLEDGE_BASE_DIR / "slu_corpus" / "synthetic.jsonl")])]
    missing = [str(p) for p in sources if not p.exists()]
    sources = [p for p in sources if p.exists()]
    if not sources:
        print(f"no corpus files found (missing: {missing})")
        return 1

    rows = []
    for source in sources:
        if str(source) == str(slu.CORPUS_FILE):
            rows.extend(slu.load_corpus())
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Same utterance may appear in both the live capture and synthesis.
    deduped = {}
    for row in rows:
        text = str(row.get("text") or "").strip()
        if text:
            deduped.setdefault(text, row)
    rows = list(deduped.values())

    labeled = []
    skipped = 0
    for row in rows:
        assistant = label_to_assistant(row)
        text = str(row.get("text") or "").strip()
        if not assistant or not text:
            skipped += 1
            continue
        labeled.append((text, assistant))

    # Bound the chat share so the model keeps a working act/idle balance.
    rng = random.Random(args.seed)
    chat_rows = [(t, a) for t, a in labeled if '"route": "chat"' in a]
    task_rows = [(t, a) for t, a in labeled if '"route": "chat"' not in a]
    keep_chats = int(len(task_rows) * CHAT_KEEP_RATIO)
    if len(chat_rows) > keep_chats:
        chat_rows = rng.sample(chat_rows, keep_chats)
    final = task_rows + chat_rows
    rng.shuffle(final)
    if args.limit:
        final = final[: args.limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for text, assistant in final:
            handle.write(json.dumps({
                "messages": [
                    {"role": "system", "content": slu.SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": assistant},
                ],
            }, ensure_ascii=False) + "\n")

    print(f"dataset: {len(final)} rows -> {out} (skipped {skipped} unlabeled, "
          f"chat kept {len(chat_rows)}/{len(chat_rows) + len(task_rows)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
