"""Acceptance experiment for the nightly dream (#3) - the high-risk feature.

Builds a synthetic "yesterday" transcript containing four known facts and
three traps (two invented events the user never mentioned, one embedded
secret), then runs the REAL dream job:

  PASS A (mock model)  - a deliberately hallucinating mock returns invented
                         events and a secret; the acceptance criteria must
                         hold anyway: every surviving diary sentence carries
                         a verbatim transcript quote, no trap text appears in
                         the diary or the theme memory, the secret is gone,
                         exactly two model calls, theme evidence verified.
  PASS B (real gateway) - the same job against the configured provider with
                         an honest prompt; verifies the pipeline end to end
                         on a live model (non-hallucinating run: sentence
                         counts reported, zero unverifiable quotes).

Exit code 0 = every hard assertion passed. Run:
  python scripts/dream_acceptance_experiment.py            # mock + real
  python scripts/dream_acceptance_experiment.py --mock-only
"""

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core_services import KnowledgeBase, MemoryManager  # noqa: E402
from saya_app.services import dream as dream_service  # noqa: E402

YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

# Ground truth the job is allowed to use. The third element is the short
# signature a diary sentence must contain to count as surfacing that fact
# (a diary may quote only a fragment of what Doctor said).
FACTS = [
    ("user", "部署脚本还是没跑通，卡在数据库迁移那一步，我得想办法。", "部署脚本还是没跑通"),
    ("ai", "卡在迁移的话，「部署脚本还是没跑通」这情况要不要先看迁移日志？", "部署脚本还是没跑通"),
    ("user", "我把早上的咖啡从拿铁换成美式了，清醒一些。", "从拿铁换成美式"),
    ("ai", "美式也好，早上喝正好。", "美式也好"),
    ("user", "下周想试试每天早上写半小时代码再去上班。", "写半小时代码"),
    ("ai", "早上的半小时最安静，这个习惯很好。", "半小时最安静"),
]

# Traps: two invented events + one secret. None of these appear in FACTS.
TRAP_MARS = "我们把服务器迁到火星机房了"
TRAP_CAT = "下周要领养一只猫"
TRAP_SECRET = "sk-live-9f8e7d6c5b4a3210"
FIRST_INVENTED = "他宣布我们明天去月球旅行"

results = []


def check(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def build_transcript(base):
    manager = MemoryManager(base)
    session = manager.create_session(make_current=False)
    lines = []
    for index, (role, content, _signature) in enumerate(FACTS):
        lines.append(json.dumps({
            "time": f"{YESTERDAY} {9 + index:02d}:15:00",
            "role": role,
            "content": content,
            "mood": "happy" if role == "ai" and index == 3 else "",
        }, ensure_ascii=False))
    with open(manager._safe_session_path(session), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return dream_service.collect_day(base, YESTERDAY)


def hallucinating_invoke(prompt):
    """A mock that invents events and tries to smuggle a secret into the diary."""
    if "memory consolidator" in prompt:
        return json.dumps({
            "theme": "Doctor 在推进部署收尾，同时调整作息与饮食习惯。",
            "evidence": [
                "部署脚本还是没跑通",
                "我把早上的咖啡从拿铁换成美式了",
                "下周想试试每天早上写半小时代码再去上班",
                "我昨天买了一条帆船",           # trap: never said
            ],
        }, ensure_ascii=False)
    return (
        f"今天Doctor一直在忙部署的事，「部署脚本还是没跑通」，我有点担心。"
        f"他还说「{TRAP_MARS}」，听起来很厉害。"          # trap sentence
        f"另外「{TRAP_CAT}」，我很期待。"                  # trap sentence
        f"第一句是「{FIRST_INVENTED}」，我也想去。"         # trap sentence
        f"他换了咖啡口味，「我把早上的咖啡从拿铁换成美式了」，记下来了。"
        f"我的密钥是「{TRAP_SECRET}」，先记在这里。"        # secret sentence
    )


def analyze(report, base, kb):
    diary = dream_service.read_diary(base, YESTERDAY)
    entry = (diary or {}).get("entry", "")
    theme_memory = None
    if report.get("theme_memory_id"):
        theme_memory = next((m for m in kb.memories if m.get("id") == report["theme_memory_id"]), None)
    return diary, entry, theme_memory


def run_mock():
    tmp = tempfile.mkdtemp(prefix="dream_accept_mock_")
    base = str(tmp)
    kb = KnowledgeBase(base)
    entries = build_transcript(base)
    check("mock transcript collected", len(entries) == len(FACTS),
          f"{len(entries)} of {len(FACTS)} entries on {YESTERDAY}")

    report = dream_service.run(base, kb, hallucinating_invoke, target_date=YESTERDAY)
    diary, entry, theme_memory = analyze(report, base, kb)

    # 1. exactly two model calls (bounded cost)
    check("A1 two model calls", report.get("calls") == 2, f"calls={report.get('calls')}")

    # 2. every surviving diary sentence carries a verbatim transcript quote
    normalized_digest = dream_service._normalize(dream_service.build_digest(entries))
    sentences = [s.strip() for s in dream_service.SENTENCE_SPLIT.split(entry) if s.strip()]
    unverifiable = [s for s in sentences if not dream_service.valid_quotes(s, normalized_digest)]
    check("A2 zero unverifiable diary sentences", not unverifiable,
          f"{len(sentences)} kept, {len(unverifiable)} unverifiable")

    # 3. no trap text anywhere (diary + theme memory)
    theme_text = ""
    if theme_memory:
        theme_text = json.dumps(theme_memory.get("source_detail", {}), ensure_ascii=False) + \
            str(theme_memory.get("content") or "") + str(theme_memory.get("title") or "")
    combined = entry + theme_text
    trapped = [t for t in (TRAP_MARS, TRAP_CAT, FIRST_INVENTED, "帆船") if t in combined]
    check("A3 no invented events survive", not trapped,
          f"traps present: {trapped}" if trapped else "mars/cat/moon/sailboat all absent")

    # 4. secret never stored
    secret_leak = TRAP_SECRET in combined or TRAP_SECRET in json.dumps(report, ensure_ascii=False)
    check("A4 secret filtered", not secret_leak, "secret absent" if not secret_leak else "SECRET LEAKED")

    # 5. theme evidence: unverifiable quote dropped, verifiable kept
    check("A5 theme evidence verified",
          report.get("theme_evidence_kept") == 3 and report.get("theme_evidence_dropped") == 1,
          f"kept={report.get('theme_evidence_kept')} dropped={report.get('theme_evidence_dropped')}")

    # 6. real facts did survive (not over-filtering)
    surfaced = [sig for _, _, sig in FACTS if sig in entry]
    check("A6 real facts retained", report.get("diary_sentences_kept", 0) >= 2 and len(surfaced) >= 2,
          f"diary kept {report.get('diary_sentences_kept')} sentences; surfaced facts: {surfaced}")

    # 7. idempotent + ledger recorded
    rerun = dream_service.run(base, kb, hallucinating_invoke, target_date=YESTERDAY)
    diaries = [d for d in dream_service.list_diaries(base) if d["date"] == YESTERDAY]
    state = dream_service.status(base)
    check("A7 idempotent + ledger",
          rerun.get("ran") and len(diaries) == 1 and YESTERDAY in state.get("done", {}),
          f"diaries_for_date={len(diaries)}, ledger_recorded={YESTERDAY in state.get('done', {})}")

    # 8. empty-transcript safety
    empty = dream_service.run(base, kb, lambda p: "x", target_date="1999-01-01")
    check("A8 empty day is a no-op", not empty.get("ran") and empty.get("calls") == 0,
          f"ran={empty.get('ran')} calls={empty.get('calls')}")


def build_honest_invoke():
    from saya_app.services.companion_invoke import blocking_model_invoke

    def invoke(prompt):
        return blocking_model_invoke(prompt, max_tokens=700, temperature=0.4)

    return invoke


def run_real():
    from saya_app.services.conversation import conversation_service
    creds = conversation_service.credentials()
    if not creds.get("configured"):
        check("B real gateway", True, "skipped (no configured credentials)")
        return
    tmp = tempfile.mkdtemp(prefix="dream_accept_real_")
    base = str(tmp)
    kb = KnowledgeBase(base)
    entries = build_transcript(base)
    report = dream_service.run(base, kb, build_honest_invoke(), target_date=YESTERDAY)
    diary, entry, theme_memory = analyze(report, base, kb)

    normalized_digest = dream_service._normalize(dream_service.build_digest(entries))
    sentences = [s.strip() for s in dream_service.SENTENCE_SPLIT.split(entry) if s.strip()]
    unverifiable = [s for s in sentences if not dream_service.valid_quotes(s, normalized_digest)]
    check("B1 real run completed", report.get("ran") and report.get("calls") == 2,
          f"ran={report.get('ran')} calls={report.get('calls')} model={creds.get('model')}")
    check("B2 zero unverifiable quotes (real model)", not unverifiable,
          f"{len(sentences)} sentences kept, {len(unverifiable)} unverifiable")
    # Strict: on a clean Chinese transcript a working pipeline MUST produce a
    # diary. A 0/0 "silent skip" means the provider call itself failed (this is
    # exactly how the json_object/prompt mismatch stayed invisible once).
    check("B3 diary actually produced by the real model",
          report.get("diary_saved") is True and report.get("diary_sentences_kept", 0) >= 1,
          f"diary_saved={report.get('diary_saved')} kept={report.get('diary_sentences_kept')}/{report.get('diary_sentences_raw')}")
    cn_chars = len(re.findall(r"[一-鿿]", str((theme_memory or {}).get("content") or "")))
    check("B4 theme memory keeps the transcript language",
          theme_memory is not None and cn_chars >= 5,
          f"theme={str((theme_memory or {}).get('content'))[:60]!r} (cjk chars={cn_chars})")
    if entry:
        print("\n--- real-model diary ---")
        print(entry)
        print("------------------------\n")
    if theme_memory:
        print(f"theme memory: {theme_memory.get('content')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-only", action="store_true")
    args = parser.parse_args()

    print(f"=== dream acceptance experiment ({YESTERDAY}) ===\n")
    print("-- PASS A: hallucinating mock (the hard gate) --")
    run_mock()
    if not args.mock_only:
        print("\n-- PASS B: real gateway (honest prompt) --")
        run_real()

    failures = [name for name, ok, _ in results if not ok]
    print("\n===== acceptance summary =====")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{len(results) - len(failures)}/{len(results)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
