"""Evaluate a LoRA adapter (or the bare base model) on the SLU benchmark.

Loads base + adapter directly with transformers/peft - no serving layer
needed - and scores against the same deterministic labels as
scripts/eval_slu_backend.py.  Greedy decoding, contract validation identical
to the production backend (saya_app.services.slu.validate_segments).

    python -B scripts/eval_slu_lora.py --adapter knowledge_base/slu_corpus/slu_lora_adapter
    python -B scripts/eval_slu_lora.py            # no --adapter = base zero-shot
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services import slu  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "eval_slu_backend", Path(__file__).resolve().parent / "eval_slu_backend.py")
_backend_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backend_mod)
BENCHMARK = _backend_mod.BENCHMARK
label_for = _backend_mod.label_for
_canonical_action = _backend_mod._canonical_action


def label_row(text):
    truth = label_for(text)
    payload = {
        "type": "instruction" if truth["route"] != "chat" else "chat",
        "text": text,
        "route": truth["route"],
        "direct_action": truth["direct_action"],
        "goal": truth["goal"],
    }
    return json.dumps({"segments": [payload]}, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--adapter", default="")
    parser.add_argument("--out", default="", help="write a per-phrase report JSON here")
    parser.add_argument("--max-new", type=int, default=256)
    args = parser.parse_args()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.bfloat16, device_map="cuda",
    )
    if args.adapter:
        model = PeftModel.from_pretrained(model, args.adapter)
        model = model.merge_and_unload()
        print(f"adapter loaded: {args.adapter}")
    model.eval()

    route_ok = payload_ok = 0
    report = []
    for text, _kind in BENCHMARK:
        truth = label_for(text)
        messages = [
            {"role": "system", "content": slu.SYSTEM_PROMPT + "\n\n" + slu.catalog_hint()},
            {"role": "user", "content": text},
        ]
        t0 = time.time()
        # transformers 5.x: apply_chat_template returns a BatchEncoding dict
        # by default - pass it expanded, and slice the prompt off by length.
        encoded = tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_tensors="pt", return_dict=True,
        ).to(model.device)
        with torch.no_grad():
            output = model.generate(**encoded, max_new_tokens=args.max_new, do_sample=False)
        answer = tokenizer.decode(output[0][encoded["input_ids"].shape[1]:], skip_special_tokens=True)
        dt = time.time() - t0
        segments = slu.validate_segments(slu._extract_json(answer))
        got = slu.normalize_model_result(segments) if hasattr(slu, "normalize_model_result") else None
        if got is None:
            # mirror eval_slu_backend's folding helper if not importable
            got = _normalize(segments or {"segments": []})
        got_route, got_action, got_goal = got
        got_action = _canonical_action(got_action)
        truth_payload = (truth["direct_action"], truth["goal"])
        got_payload = (got_action, got_goal)
        route_ok += got_route == truth["route"]
        payload_ok += got_payload == truth_payload
        report.append({
            "text": text, "truth": truth["route"], "got": got_route,
            "match": got_route == truth["route"],
            "payload_match": got_payload == truth_payload,
            "direct_action": got_action, "goal": got_goal,
            "seconds": round(dt, 1),
            "raw": answer[:120],
        })

    total = len(BENCHMARK)
    print(f"base={args.base}  adapter={args.adapter or 'NONE'}")
    print(f"route agreement: {route_ok}/{total} ({100.0 * route_ok / total:.1f}%)  "
          f"payload agreement: {payload_ok}/{total} ({100.0 * payload_ok / total:.1f}%)  "
          f"avg latency: {sum(r['seconds'] for r in report) / total:.1f}s")
    for row in report:
        if not row["match"] or not row["payload_match"]:
            print(f"  MISMATCH [{row['text']}] truth={row['truth']} got={row['got']} "
                  f"action={row['direct_action']!r} goal={row['goal']} raw={row['raw']!r}")
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _normalize(segments):
    route = "chat"
    action = None
    goal = None
    for segment in segments.get("segments", []):
        if segment.get("route") == "chat":
            continue
        if route == "chat":
            route = segment["route"]
        else:
            route = "mixed"
        action = action or segment.get("direct_action")
        goal = goal or segment.get("goal")
    return route, action, goal


if __name__ == "__main__":
    raise SystemExit(main())
