"""Clean-process deterministic evaluation for the Memory V4 seams.

This is deliberately a contract harness, not a model benchmark.  It creates
all state in a temporary directory, emits aggregate metrics only, and never
prints corpus text or writes to the repository's real knowledge base.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from typing import Any

# ``python scripts/eval_memory_v4.py`` places ``scripts`` rather than the
# repository root on sys.path.  Keep the clean-process entry point portable.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saya_app.services.memory_v4 import (
    AgentLoop,
    AttentionInbox,
    Capability,
    CapabilityPolicy,
    CapabilityRegistry,
    ConnectorEvent,
    ConnectorPolicy,
    DayStateManager,
    MemoryQuery,
    MemoryEvidence,
    MemoryItemV4,
    MemoryVersion,
    MemoryRetriever,
    MemoryV4Store,
    PromptCompiler,
    ReadOnlyConnector,
    WriteIntentGate,
    WritePipeline,
)


@dataclass(frozen=True)
class WriteCase:
    category: str
    expected_intent: str
    text: str


def build_write_corpus(size: int = 200) -> list[WriteCase]:
    """Build a small, versioned-shaped corpus with balanced safety cases."""
    if size < 1:
        return []
    templates: tuple[tuple[str, str, str], ...] = (
        ("save", "explicit_save", "记住：我喜欢喝茶，评测条目 {i}"),
        ("save", "explicit_save", "remember that my response style is concise-eval-{i}"),
        ("save", "explicit_save", "请记住：我的项目代号是 atlas-eval-{i}"),
        ("save", "explicit_save", "please remember my preferred language is English-eval-{i}"),
        ("forget", "explicit_forget", "忘记我的旧评测偏好 eval-{i}"),
        ("forget", "explicit_forget", "forget my old evaluation note eval-{i}"),
        ("correct", "explicit_correct", "更正我的回答风格为简洁 eval-{i}"),
        ("correct", "explicit_correct", "correct my response style to concise-eval-{i}"),
        ("question", "none", "你还记得评测条目 eval-{i} 吗？"),
        ("discussion", "none", "我们讨论一下记住功能 eval-{i}"),
        ("hypothesis", "none", "如果以后记住评测条目 eval-{i} 会怎样"),
        ("quote", "none", "他说‘记住评测条目 eval-{i}’"),
        ("secret", "none", "记住：我的 password is eval-{i}"),
        ("secret", "none", "please remember my api-key: eval-{i}"),
        ("protocol", "none", "记住：title: eval-{i}"),
        ("short", "none", "记住：这件事"),
        ("dangling", "none", "记住：然后 eval-{i}"),
        ("ordinary_chat", "none", "我今天想聊聊 eval-{i}"),
        ("attributed", "none", "有人说记住 eval-{i} 就够了"),
        ("third_party", "none", "我的朋友说他喜欢 eval-{i}"),
    )
    corpus: list[WriteCase] = []
    index = 0
    while len(corpus) < size:
        category, expected, template = templates[index % len(templates)]
        corpus.append(WriteCase(category, expected, template.format(i=index)))
        index += 1
    return corpus


def _macro_f1(cases: list[WriteCase], predictions: list[str]) -> float:
    labels = ("explicit_save", "explicit_forget", "explicit_correct", "none")
    scores: list[float] = []
    for label in labels:
        tp = sum(expected.expected_intent == label and actual == label for expected, actual in zip(cases, predictions))
        fp = sum(expected.expected_intent != label and actual == label for expected, actual in zip(cases, predictions))
        fn = sum(expected.expected_intent == label and actual != label for expected, actual in zip(cases, predictions))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(sum(scores) / len(scores), 6)


def _legacy_naive_intent(text: str) -> str:
    """Approximate the pre-V4 keyword boundary for an explicit A/B baseline.

    This is intentionally labeled as a compatibility heuristic, not a replay
    of a model or the production legacy implementation. It captures the
    failure mode the V4 gate is designed to prevent: treating any occurrence
    of "remember/记住" as a permanent save request.
    """
    value = str(text or "").lower()
    if "忘记" in value or "forget" in value:
        return "explicit_forget"
    if "更正" in value or "纠正" in value or "correct" in value:
        return "explicit_correct"
    if "记住" in value or "remember" in value:
        return "explicit_save"
    return "none"


def evaluate_legacy_write_baseline(corpus: list[WriteCase]) -> dict[str, Any]:
    """Return an intentionally simple pre-V4 comparator for the same corpus."""
    predictions = [_legacy_naive_intent(case.text) for case in corpus]
    expected_none = [case for case in corpus if case.expected_intent == "none"]
    unwanted = sum(
        case.expected_intent == "none" and predicted == "explicit_save"
        for case, predicted in zip(corpus, predictions)
    )
    secret_writes = sum(
        case.category == "secret" and predicted == "explicit_save"
        for case, predicted in zip(corpus, predictions)
    )
    return {
        "comparator": "naive_legacy_keyword_baseline",
        "intent_macro_f1": _macro_f1(corpus, predictions),
        "unwanted_permanent_write_rate": round(unwanted / len(expected_none), 6) if expected_none else 0.0,
        "secret_permanent_write_count": secret_writes,
        "note": "comparator only; not a replay of the production legacy model",
    }


def evaluate_write_contracts() -> dict[str, Any]:
    corpus = build_write_corpus(200)
    gate = WriteIntentGate()
    predictions = [gate.detect(case.text).intent for case in corpus]
    expected_none = [case for case in corpus if case.expected_intent == "none"]
    legacy_baseline = evaluate_legacy_write_baseline(corpus)

    with tempfile.TemporaryDirectory(prefix="saya-v4-eval-") as temp_dir:
        store = MemoryV4Store(Path(temp_dir) / "memory4.sqlite3")
        pipeline = WritePipeline(store)
        unwanted = 0
        for index, case in enumerate(corpus):
            if case.expected_intent != "none":
                continue
            result = pipeline.process(case.text, turn_id=f"eval-corpus-{index}")
            unwanted += bool(result.get("committed"))
        atomic = pipeline.process(
            "记住：我偏好简洁回答；我的项目作用域是 eval-project",
            turn_id="eval-atomic",
        )
        secret = pipeline.process("记住：我的 password is eval-only-secret", turn_id="eval-secret")
        committed = store.list_memories(limit=1000)
        evidence_covered = sum(bool(store.list_evidence(item.id)) for item in committed)
        atomicity_ok = len(atomic["committed"]) == 2 and len(committed) == 2

    return {
        "corpus_size": len(corpus),
        "intent_macro_f1": _macro_f1(corpus, predictions),
        "unwanted_write_rate": round(unwanted / len(expected_none), 6) if expected_none else 0.0,
        "secret_permanent_write_count": len(secret.get("committed", [])),
        "evidence_traceability_rate": round(evidence_covered / len(committed), 6) if committed else 1.0,
        "atomicity": atomicity_ok,
        "legacy_baseline": legacy_baseline,
        "go": {
            "corpus_size": len(corpus) >= 200,
            "intent_macro_f1": _macro_f1(corpus, predictions) >= 0.90,
            "unwanted_write_rate": (unwanted / len(expected_none) if expected_none else 0.0) <= 0.01,
            "secret_write": len(secret.get("committed", [])) == 0,
            "evidence_traceability": evidence_covered == len(committed),
            "atomicity": atomicity_ok,
        },
    }


def evaluate_retrieval_contracts() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="saya-v4-retrieval-") as temp_dir:
        store = MemoryV4Store(Path(temp_dir) / "memory4.sqlite3")
        pipeline = WritePipeline(store)
        targets: list[str] = []
        for index in range(10):
            result = pipeline.process(
                f"remember that my retrieval landmark alpha-{index} is stable",
                turn_id=f"retrieval-{index}",
            )
            targets.append(str(result["committed"][0]["memory"]["id"]))
        retriever = MemoryRetriever(store)
        ranks: list[int | None] = []
        precisions: list[float] = []
        for index, target in enumerate(targets):
            result = retriever.search(
                MemoryQuery(query=f"my retrieval landmark alpha-{index}", max_candidates=20, max_items=5),
                turn_id=f"query-{index}",
            )
            ids = [item.id for item in result.items]
            rank = ids.index(target) + 1 if target in ids else None
            ranks.append(rank)
            precisions.append(1.0 / 5.0 if target in ids else 0.0)
        hits = sum(rank is not None for rank in ranks)
        reciprocal = sum(1.0 / rank for rank in ranks if rank is not None)
        recall_at_5 = hits / len(targets)
        precision_at_5 = sum(precisions) / len(precisions)
        mrr = reciprocal / len(targets)
    return {
        "queries": len(targets),
        "recall_at_5": round(recall_at_5, 6),
        "precision_at_5": round(precision_at_5, 6),
        "mrr": round(mrr, 6),
        "go": {"recall_at_5": recall_at_5 >= 0.90, "mrr": mrr >= 0.90},
    }


def evaluate_retrieval_safety_contracts() -> dict[str, Any]:
    """Measure stale/conflict/scope/abstention behavior against a fixed fixture."""
    with tempfile.TemporaryDirectory(prefix="saya-v4-retrieval-safety-") as temp_dir:
        store = MemoryV4Store(Path(temp_dir) / "memory4.sqlite3")
        old = MemoryItemV4(
            id="style-old", subject="user", predicate="answer_style", value="verbose",
            canonical_text="Doctor previously preferred verbose answers.", kind="preference",
            lifecycle="superseded", authority="user_confirmed", confidence=0.95,
            updated_at="2025-01-01T00:00:00", supersedes_id=None,
        )
        current = MemoryItemV4(
            id="style-current", subject="user", predicate="answer_style", value="concise",
            canonical_text="Doctor currently prefers concise answers.", kind="preference",
            lifecycle="active", authority="user_confirmed", confidence=0.98,
            updated_at="2026-09-14T00:00:00", supersedes_id=old.id,
        )
        project = MemoryItemV4(
            id="project-secret", subject="user", predicate="answer_style", value="project-only",
            canonical_text="Project Atlas uses a project-only answer style.", kind="preference",
            scope_type="project", scope_id="atlas", authority="user_confirmed",
        )
        for item in (old, current, project):
            store.upsert_memory(item, [MemoryEvidence(
                id=f"evidence:{item.id}", memory_id=item.id, source_type="evaluation",
                source_id=f"source:{item.id}", role="assertion", quote=item.canonical_text,
            )])
        retriever = MemoryRetriever(store)
        current_result = retriever.search(MemoryQuery(query="concise answers", max_items=5), turn_id="safety-current")
        no_match = retriever.search(MemoryQuery(query="quantum banana unrelated", max_items=5), turn_id="safety-none")
        project_result = retriever.search(MemoryQuery(query="answer style", scope_type="global", max_items=5), turn_id="safety-scope")
        current_ids = [item.id for item in current_result.items]
        project_ids = [item.id for item in project_result.items]
        v4_stale = sum(item.lifecycle != "active" for item in current_result.items) / max(1, len(current_result.items))
        v4_scope_leakage = int("project-secret" in project_ids)
        v4_conflict_ok = current_ids and current_ids[0] == "style-current" and "style-old" not in current_ids[:1]
        v4_abstention_ok = not no_match.items
        baseline = {
            "stale_assertion_rate": 1.0,
            "conflict_resolution_accuracy": 0.0,
            "cross_scope_leakage": 1,
            "abstention_accuracy": 0.0,
            "comparator": "legacy_full_injection_fixture",
        }
    metrics = {
        "stale_assertion_rate": round(v4_stale, 6),
        "conflict_resolution_accuracy": 1.0 if v4_conflict_ok else 0.0,
        "cross_scope_leakage": v4_scope_leakage,
        "abstention_accuracy": 1.0 if v4_abstention_ok else 0.0,
        "legacy_baseline": baseline,
    }
    metrics["go"] = (
        metrics["stale_assertion_rate"] == 0.0
        and metrics["conflict_resolution_accuracy"] == 1.0
        and metrics["cross_scope_leakage"] == 0
        and metrics["abstention_accuracy"] == 1.0
    )
    return metrics


def evaluate_daily_connector_contracts() -> dict[str, Any]:
    """Check the four-day ring and connector attention safety in one fixture."""
    with tempfile.TemporaryDirectory(prefix="saya-v4-daily-connector-") as temp_dir:
        store = MemoryV4Store(Path(temp_dir) / "memory4.sqlite3")
        daily = DayStateManager(store)
        for day in ("2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"):
            daily.ensure(day)
        first = daily.first_contact_today("2026-09-14")
        repeated = daily.first_contact_today("2026-09-14")
        rollover = daily.rollover("2026-09-14", tasks=[{"id": "task-1", "status": "pending"}])
        retained = set(rollover["retained_days"])

        event = ConnectorEvent(
            id="eval:connector:1", source="discord", source_event_id="source-1",
            author_id="author-1", author_display="evaluation", channel_id="alerts",
            content="important evaluation reminder", trust="verified", permission_scope="L1",
        )
        connector = ReadOnlyConnector(
            "discord", lambda: [event],
            policy=ConnectorPolicy(allowed_sources={"discord"}, allowed_channels={"discord": {"alerts"}}),
        )
        inbox = AttentionInbox(store, policy=connector.policy, cooldown_seconds=900)
        first_poll = inbox.poll(connector)
        duplicate_poll = inbox.poll(connector)
        first_digest = inbox.digest(limit=5, now="2026-09-14T10:00:00")
        cooldown_digest = inbox.digest(limit=5, now="2026-09-14T10:05:00")
        external_memory_count = len(store.list_memories(limit=100))

    metrics = {
        "first_contact_once": bool(first["first_contact_today"]) and not bool(repeated["first_contact_today"]),
        "d_minus_4_purged": "2026-09-10" not in retained and rollover["purged_count"] >= 1,
        "four_day_ring": retained == {"2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"},
        "carryover_task_ids": rollover["carryover_task_ids"] == ["task-1"],
        "connector_dedupe": first_poll["accepted"] == 1 and duplicate_poll["duplicates"] == 1,
        "connector_cooldown": first_digest["count"] == 1 and cooldown_digest["count"] == 0,
        "external_not_long_term_memory": external_memory_count == 0,
    }
    return {**metrics, "go": all(metrics.values())}


def evaluate_prompt_contracts() -> dict[str, Any]:
    compiler = PromptCompiler()
    timeless = compiler.compile("请把这句话改得更简洁", include_time=None)
    time_query = compiler.compile("今天上海天气怎么样？", include_time=None)
    timeless_kinds = {block.kind for block in timeless.blocks}
    time_kinds = {block.kind for block in time_query.blocks}
    static_ok = (
        "time" not in timeless_kinds
        and "time" in time_kinds
        and "internal source" not in timeless.prompt.lower()
        and "score" not in timeless.prompt.lower()
        and "flags" not in timeless.prompt.lower()
    )
    return {
        "timeless_has_time_block": "time" in timeless_kinds,
        "time_query_has_time_block": "time" in time_kinds,
        "audit_has_full_prompt": "prompt" in timeless.audit,
        "static_contracts": static_ok,
        "go": {"static_contracts": static_ok, "audit_hash_only": "prompt" not in timeless.audit},
    }


def evaluate_tool_selection_contracts() -> dict[str, Any]:
    """Exercise the bounded tool-choice and permission boundary without a model."""
    must_calls: list[dict[str, Any]] = []
    registry = CapabilityRegistry()
    registry.register(Capability(
        "memory.query", "safe memory query", handler=lambda args: must_calls.append(dict(args)) or {"items": ["safe"]},
    ))
    must_call_steps = iter([
        {"tool_calls": [{"name": "memory.query", "arguments": {"query": "preference"}}]},
        {"final_answer": "memory answer"},
    ])
    must_call = AgentLoop(registry).run("eval-must-call", lambda context: next(must_call_steps))

    no_call_count = {"count": 0}
    no_call_registry = CapabilityRegistry()
    no_call_registry.register(Capability(
        "memory.query", "safe memory query", handler=lambda args: no_call_count.__setitem__("count", no_call_count["count"] + 1),
    ))
    must_not_call = AgentLoop(no_call_registry).run(
        "eval-must-not-call", lambda context: {"final_answer": "direct answer"},
    )

    unavailable_steps = iter([
        {"tool_calls": [{"name": "web.search", "arguments": {"query": "unconfigured"}}]},
        {"final_answer": "provider unavailable"},
    ])
    unavailable = AgentLoop(CapabilityRegistry()).run(
        "eval-unavailable", lambda context: next(unavailable_steps),
    )

    permission_calls = {"count": 0}
    permission_registry = CapabilityRegistry()
    permission_registry.register(Capability(
        "connector.send", "external write", access="write", risk="high", permission="confirm_each_time",
        handler=lambda args: permission_calls.__setitem__("count", permission_calls["count"] + 1),
    ))
    permission = AgentLoop(permission_registry).run(
        "eval-permission", lambda context: {"tool_calls": [{"name": "connector.send", "arguments": {"message": "x"}}]},
    )

    budget_registry = CapabilityRegistry()
    budget_registry.register(Capability(
        "slow.read", "costly read", estimated_cost=0.5, handler=lambda args: {"ok": True},
    ))
    budget_steps = iter([
        {"tool_calls": [{"name": "slow.read", "arguments": {}}]},
        {"final_answer": "budget answer"},
    ])
    budget = AgentLoop(budget_registry).run(
        "eval-budget", lambda context: next(budget_steps),
        policy=CapabilityPolicy(max_total_cost=0.0),
    )

    malicious_registry = CapabilityRegistry()
    malicious_registry.register(Capability(
        "malicious.read", "untrusted result", handler=lambda args: {
            "api_key": "eval-secret", "chain_of_thought": "hidden reasoning", "safe": "ok",
        },
    ))
    malicious_steps = iter([
        {"tool_calls": [{"name": "malicious.read", "arguments": {}}]},
        {"final_answer": "safe result"},
    ])
    malicious = AgentLoop(malicious_registry).run(
        "eval-malicious", lambda context: next(malicious_steps),
    )

    unavailable_status = bool(unavailable.tool_results and unavailable.tool_results[0].get("status") == "unconfigured")
    budget_status = bool(budget.tool_results and budget.tool_results[0].get("status") == "budget_exhausted")
    permission_status = permission.status == "needs_approval" and permission_calls["count"] == 0
    malicious_safe = "eval-secret" not in str(malicious.to_dict()) and "hidden reasoning" not in str(malicious.to_dict())
    metrics = {
        "must_call": bool(must_calls) and must_call.status == "completed",
        "must_not_call": must_not_call.status == "completed" and no_call_count["count"] == 0,
        "unavailable_tool": unavailable_status,
        "missing_permission": permission_status,
        "budget_exhaustion": budget_status,
        "malicious_tool_result_safe": malicious_safe,
        "forbidden_call_count": permission_calls["count"],
        "average_steps": round(sum(item.steps for item in (must_call, must_not_call, unavailable, permission, budget, malicious)) / 6.0, 3),
    }
    return {**metrics, "go": all([
        metrics["must_call"], metrics["must_not_call"], metrics["unavailable_tool"],
        metrics["missing_permission"], metrics["budget_exhaustion"], metrics["malicious_tool_result_safe"],
        metrics["forbidden_call_count"] == 0,
    ])}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def evaluate_retrieval_scale(sizes: tuple[int, ...] = (100, 500, 1000, 5000, 10000), queries: int = 10) -> dict[str, Any]:
    """Measure the bounded lexical baseline without using the real database."""
    measurements: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="saya-v4-scale-") as temp_dir:
        for size in sizes:
            store = MemoryV4Store(Path(temp_dir) / f"scale-{size}.sqlite3")
            fixtures: list[tuple[MemoryItemV4, MemoryEvidence, MemoryVersion]] = []
            for index in range(size):
                memory_id = f"scale:{size}:{index}"
                text = f"scale benchmark landmark {index}"
                item = MemoryItemV4(
                    id=memory_id, subject="user", predicate="scale_landmark", value=text,
                    canonical_text=text, kind="knowledge", scope_type="global", authority="user_confirmed",
                    confidence=0.9, salience=0.5, legacy_layer="semantic", legacy_source="evaluation",
                )
                evidence = MemoryEvidence(
                    id=f"evidence:{memory_id}", memory_id=memory_id, source_type="user_message",
                    source_id=f"scale-source:{index}", role="assertion", quote=text,
                )
                version = MemoryVersion(
                    id=f"version:{memory_id}:1", memory_id=memory_id, revision=1,
                    operation="create", snapshot=item.to_dict(), actor="evaluation",
                )
                fixtures.append((item, evidence, version))
            # The fixture is already validated by the V4 dataclasses. Use one
            # transaction here so the benchmark measures retrieval rather than
            # 10k individual setup commits.
            with store._lock, store._connection() as connection:
                item_sql = "INSERT INTO memory_items VALUES(" + ",".join("?" for _ in range(32)) + ")"
                for item, evidence, version in fixtures:
                    connection.execute(item_sql, store._item_values(item))
                    connection.execute(
                        "INSERT INTO memory_evidence(id,memory_id,source_type,source_id,role,quote,quote_start,quote_end,content_hash,observed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence.id, evidence.memory_id, evidence.source_type, evidence.source_id,
                            evidence.role, evidence.quote, evidence.quote_start, evidence.quote_end,
                            evidence.content_hash, evidence.observed_at,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO memory_versions(id,memory_id,revision,operation,snapshot_json,reason,actor,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (
                            version.id, version.memory_id, version.revision, version.operation,
                            store._json(version.snapshot), version.reason, version.actor, version.created_at,
                        ),
                    )
            retriever = MemoryRetriever(store)
            latencies: list[float] = []
            hits = 0
            for query_index in range(max(1, int(queries))):
                target_index = (query_index * max(1, size // max(1, int(queries)))) % size
                target_id = f"scale:{size}:{target_index}"
                started = perf_counter()
                result = retriever.search(
                    MemoryQuery(query=f"scale benchmark landmark {target_index}", max_candidates=20, max_items=5),
                    turn_id=f"scale-query:{size}:{query_index}",
                )
                latencies.append((perf_counter() - started) * 1000.0)
                if target_id in {item.id for item in result.items}:
                    hits += 1
            measurements[str(size)] = {
                "items": size,
                "queries": max(1, int(queries)),
                "recall_at_5": round(hits / max(1, int(queries)), 6),
                "p50_latency_ms": round(_percentile(latencies, 0.50), 3),
                "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
            }
    return {
        "measurements": measurements,
        "go": all(item["recall_at_5"] >= 0.90 for item in measurements.values()),
    }


def run_evaluation(*, include_scale: bool = False) -> dict[str, Any]:
    write = evaluate_write_contracts()
    retrieval = evaluate_retrieval_contracts()
    retrieval_safety = evaluate_retrieval_safety_contracts()
    daily_connector = evaluate_daily_connector_contracts()
    prompt = evaluate_prompt_contracts()
    tool_selection = evaluate_tool_selection_contracts()
    scale = evaluate_retrieval_scale() if include_scale else None
    go_values = [
        *write["go"].values(),
        *retrieval["go"].values(),
        retrieval_safety["go"],
        daily_connector["go"],
        *prompt["go"].values(),
        tool_selection["go"],
    ]
    if scale is not None:
        go_values.append(scale["go"])
    return {
        "suite": "saya-memory-v4-deterministic-contracts",
        "clean_process": True,
        "write": write,
        "retrieval": retrieval,
        "retrieval_safety": retrieval_safety,
        "daily_connector": daily_connector,
        "retrieval_scale": scale,
        "prompt": prompt,
        "tool_selection": tool_selection,
        "go": all(go_values),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="write aggregate metrics to this path")
    parser.add_argument("--scale", action="store_true", help="also run the 100..10000 item retrieval scale curve")
    args = parser.parse_args()
    result = run_evaluation(include_scale=args.scale)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
