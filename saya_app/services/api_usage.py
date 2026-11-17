"""Per-day, per-model API usage ledger.

The API-mode chat stream records every completed provider turn here so the
frontend can show what daily usage actually looks like. The ledger is a
single JSON file under the knowledge base; writes happen off the event loop
via to_thread, and the single-process server only has one writer at a time,
so a simple load-merge-save is enough.
"""

import json
import os
import threading
from datetime import datetime, timedelta

LEDGER_LOCK = threading.Lock()

_COUNT_FIELDS = ("requests", "completed", "failed", "prompt_tokens",
                 "completion_tokens", "total_tokens", "reasoning_tokens")


def _ledger_path(base_dir):
    return os.path.join(str(base_dir), "api_usage.json")


def _empty_model_entry():
    return {field: 0 for field in _COUNT_FIELDS}


def _normalize_usage(usage):
    """Pull the token counters out of an OpenAI-style usage object."""
    if not isinstance(usage, dict):
        return {}
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            result[key] = int(value)
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        value = details.get("reasoning_tokens")
        if isinstance(value, (int, float)) and value >= 0:
            result["reasoning_tokens"] = int(value)
    return result


def _today_key():
    return datetime.now().strftime("%Y-%m-%d")


def load_ledger(base_dir):
    path = _ledger_path(base_dir)
    if not os.path.exists(path):
        return {"days": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            return {"days": {}}
        return data
    except (OSError, ValueError):
        return {"days": {}}


def save_ledger(base_dir, ledger):
    path = _ledger_path(base_dir)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False)
    os.replace(tmp_path, path)


def record_turn(base_dir, model_name, usage=None, completed=True, *, stage=None, elapsed_ms=None):
    """Add one provider turn (token counters optional; failures still count)."""
    model_name = str(model_name or "unknown").strip() or "unknown"
    with LEDGER_LOCK:
        ledger = load_ledger(base_dir)
        day = ledger["days"].setdefault(_today_key(), {})
        entry = day.setdefault(model_name, _empty_model_entry())
        entry["requests"] += 1
        entry["completed" if completed else "failed"] += 1
        for key, value in _normalize_usage(usage).items():
            entry[key] = (entry.get(key) or 0) + value
        if stage in {'qq_answer', 'web_answer', 'qq_selector', 'qq_topic_route', 'qq_topic_state',
                     'qq_event_operation', 'qq_memory_reference', 'qq_mood_state', 'qq_expression_select',
                     'qq_daily_memory', 'qq_memory_lookup', 'qq_library_review', 'qq_memory_overlap'}:
            detail = entry.setdefault('stages', {}).setdefault(stage, _empty_model_entry())
            detail['requests'] += 1
            detail['completed' if completed else 'failed'] += 1
            normalized = _normalize_usage(usage)
            detail['usage_unknown'] = detail.get('usage_unknown', 0) + int(not normalized)
            for key, value in normalized.items():
                detail[key] = detail.get(key, 0) + value
            if isinstance(elapsed_ms, (int, float)) and elapsed_ms >= 0:
                detail['elapsed_ms'] = detail.get('elapsed_ms', 0) + int(elapsed_ms)
        save_ledger(base_dir, ledger)
    return True


def summarize(base_dir, days=7):
    """Aggregate the last N days into per-day and per-model totals."""
    try:
        days = max(1, min(int(days), 90))
    except (TypeError, ValueError):
        days = 7
    ledger = load_ledger(base_dir)
    cutoff = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    per_day = {}
    per_model = {}
    for day_key in sorted(k for k in ledger["days"] if k >= cutoff):
        day_total = {"requests": 0, "failed": 0, "total_tokens": 0}
        for model_name, entry in ledger["days"][day_key].items():
            if not isinstance(entry, dict):
                continue
            for field in _COUNT_FIELDS:
                value = entry.get(field)
                if not isinstance(value, (int, float)):
                    continue
                value = int(value)
                model_agg = per_model.setdefault(
                    model_name, {f: 0 for f in _COUNT_FIELDS}
                )
                model_agg[field] += value
                if field == "requests":
                    day_total["requests"] += value
                elif field == "failed":
                    day_total["failed"] += value
                elif field == "total_tokens":
                    day_total["total_tokens"] += value
        per_day[day_key] = day_total
    return {"days": days, "per_day": per_day, "per_model": per_model}
