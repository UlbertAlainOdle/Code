"""Read-only baseline audit of the live memory store (V4 plan sections 3, 16.2, 19-phase-0).

Produces a migration-preview style report WITHOUT touching any store file:
reads knowledge.json / memory_candidates.json / memory_settings.json and
prints (or writes with --out) counts, layer/source inventories and risk
flags that phase 1 migration decisions will be built on. Never mutates.

Usage:
    python scripts/audit_memory_baseline.py [--kb-dir knowledge_base] [--out report.md]
"""

import argparse
import codecs
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DANGLING_START = ("所以", "但是", "然后", "这个", "那个", "这件事", "它", "你的", "多少", "的吗", "了吗")
QUESTION_TAIL = ("?", "？", "吗", "吗？", "呢", "呢？", "么")
SECRET_HINT = re.compile(
    r"(密码|口令|pass\s?word|passwd|token|api\s?key|secret|credential|私钥|密钥|助记词|mnemonic)",
    re.IGNORECASE,
)
PROTOCOL_RESIDUE = re.compile(r"(Title\s*:|Summary\s*:|Memory\s*:|```|MOTION)", re.IGNORECASE)


def load_json(path, default):
    try:
        with codecs.open(path, "r", "utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as exc:
        return {"__error__": str(exc)}


def short(text, n=48):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def audit(kb_dir: Path):
    memories = load_json(kb_dir / "knowledge.json", [])
    if not isinstance(memories, list):
        memories = []
    candidates = load_json(kb_dir / "memory_candidates.json", [])
    if not isinstance(candidates, list):
        candidates = []
    settings = load_json(kb_dir / "memory_settings.json", {})

    active = [m for m in memories if not m.get("is_archived")]
    archived = [m for m in memories if m.get("is_archived")]

    def count_by(key):
        out = {}
        for m in active:
            value = str(m.get(key) or "unknown")
            out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    core = [m for m in active if str(m.get("memory_layer") or "") == "core"]
    auto_explicit = [m for m in active if m.get("source") == "auto_explicit"]

    suspicious_auto = []
    for m in auto_explicit:
        content = str(m.get("content") or "").strip()
        reasons = []
        core_text = re.sub(r"[\s\W]+", "", content)
        if len(core_text) < 4:
            reasons.append("too_short")
        if content.endswith(QUESTION_TAIL):
            reasons.append("question_tail")
        if content.startswith(DANGLING_START):
            reasons.append("dangling_start")
        if SECRET_HINT.search(content):
            reasons.append("secret_hint")
        if PROTOCOL_RESIDUE.search(content):
            reasons.append("protocol_residue")
        if reasons:
            suspicious_auto.append({
                "id": m.get("id"), "reasons": reasons, "content": short(content, 60),
                "date": m.get("date"),
            })

    locked_starred = [m for m in active if m.get("is_locked") or m.get("is_important")]
    session_summaries = [
        m for m in active
        if m.get("source") in ("session_archive", "auto_merge")
        or m.get("category") == "conversation_summary"
    ]
    session_summaries_protected = [
        m for m in session_summaries if m.get("is_locked") or m.get("is_important")
    ]
    daily_like = [
        m for m in active
        if str(m.get("id") or "") == "system-daily-activity-current"
        or "Today's Activity" in str(m.get("title") or "")
        or "今日活动" in str(m.get("title") or "")
        or m.get("source") == "daily_activity"
    ]
    pending = [c for c in candidates if c.get("status") == "pending"]
    aged_pending = []
    now = datetime.now()
    for c in pending:
        try:
            created = datetime.strptime(str(c.get("creation_time") or ""), "%Y-%m-%d %H:%M:%S")
            if (now - created).days >= 3:
                aged_pending.append(c.get("id"))
        except ValueError:
            continue

    report = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "kb_dir": str(kb_dir),
        "read_only": True,
        "totals": {
            "all": len(memories),
            "active": len(active),
            "archived": len(archived),
            "locked_or_starred": len(locked_starred),
            "core_layer": len(core),
            "auto_explicit": len(auto_explicit),
            "session_summaries": len(session_summaries),
            "session_summaries_protected": len(session_summaries_protected),
            "pending_candidates": len(pending),
            "aged_pending_candidates_ge3d": len(aged_pending),
        },
        "active_by_source": count_by("source"),
        "active_by_layer": count_by("memory_layer"),
        "active_by_category": count_by("category"),
        "auto_adopt_setting": {
            "explicit_in_settings_file": "candidate_auto_adopt_enabled" in settings,
            "value_in_settings_file": settings.get("candidate_auto_adopt_enabled", "<absent: code default now False>"),
        },
        "risk_flags": {
            "suspicious_auto_explicit": suspicious_auto,
            "daily_activity_in_active": [
                {"id": m.get("id"), "title": short(m.get("title"), 60),
                 "layer": m.get("memory_layer"), "locked": bool(m.get("is_locked")),
                 "starred": bool(m.get("is_important"))}
                for m in daily_like
            ],
            "core_inventory": [
                {"id": m.get("id"), "title": short(m.get("title"), 60),
                 "source": m.get("source"), "locked": bool(m.get("is_locked")),
                 "starred": bool(m.get("is_important")),
                 "chars": len(str(m.get("content") or ""))}
                for m in core
            ],
        },
    }
    return report


def to_markdown(report):
    t = report["totals"]
    lines = [
        "# Memory Baseline Audit (read-only)",
        "",
        f"Generated: {report['generated_at']}  ",
        f"Store: `{report['kb_dir']}` — nothing was modified.",
        "",
        "## Totals",
        "",
        "| metric | count |",
        "|---|---:|",
    ]
    for key, value in t.items():
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Active by source", ""]
    for key, value in report["active_by_source"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Active by layer", ""]
    for key, value in report["active_by_layer"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Auto-adopt setting", ""]
    a = report["auto_adopt_setting"]
    lines.append(
        f"- in settings file: {a['explicit_in_settings_file']} "
        f"(value: {a['value_in_settings_file']})"
    )
    flags = report["risk_flags"]
    lines += ["", f"## Suspicious auto_explicit records ({len(flags['suspicious_auto_explicit'])})", ""]
    for item in flags["suspicious_auto_explicit"]:
        lines.append(f"- `{item['id']}` {item['reasons']} — “{item['content']}” ({item['date']})")
    lines += ["", f"## Daily-activity records in active store ({len(flags['daily_activity_in_active'])})", ""]
    for item in flags["daily_activity_in_active"]:
        lines.append(
            f"- `{item['id']}` “{item['title']}” layer={item['layer']} "
            f"locked={item['locked']} starred={item['starred']}"
        )
    lines += ["", f"## Core layer inventory ({len(flags['core_inventory'])})", ""]
    for item in flags["core_inventory"]:
        lines.append(
            f"- `{item['id']}` “{item['title']}” src={item['source']} "
            f"locked={item['locked']} starred={item['starred']} chars={item['chars']}"
        )
    lines += [
        "",
        "## Phase-1 migration notes",
        "",
        "- Daily Activity records should migrate to DayState D0 and leave Core (plan section 16.2).",
        "- Locked/starred session summaries must be surfaced as suggest_unlock/suggest_unpin in the",
        "  migration preview; this audit lists them but changes nothing.",
        "- Suspicious auto_explicit records become `quarantined` proposals, never auto-deleted.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-dir", default=str(ROOT / "knowledge_base"))
    parser.add_argument("--out", default="", help="optional markdown output path")
    parser.add_argument("--json-out", default="", help="optional JSON output path")
    args = parser.parse_args()

    report = audit(Path(args.kb_dir))
    markdown = to_markdown(report)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"wrote {args.out}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.json_out}")
    if not args.out and not args.json_out:
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
