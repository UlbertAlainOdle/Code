"""Live experiment: auto-save memory pipeline + autonomous agent tool selection.

Part A drives the REAL production triage path
(`maybe_create_autonomous_memory_candidates` + local-model request +
recurrence auto-confirm) with a locally loaded GGUF model over a labeled
Chinese conversation corpus, using a throwaway KnowledgeBase.

Part B drives the REAL Agent Loop (LocalLlamaAgentStep JSON contract) through
`KnowledgeBase.run_memory_v4_agent_once` with a capability registry whose
memory/time handlers really execute, verifying that the model autonomously
decides tool use (call / not call / honest unavailability).

Usage:
  python scripts/experiment_autosave_and_agent_20260929.py [--part a|b|all]
       [--model qwen3_30b_q5|mistral_22b_q5] [--out docs/experiment-report.json]

The experiment never writes to the real knowledge_base: SERVER.kb_system is
swapped to a temporary KnowledgeBase and restored afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_services import KnowledgeBase  # noqa: E402

MODEL_FILES = {
    "qwen3_30b_q5": ROOT.parent / "models" / "Qwen3-30B-A3B-Instruct-2507-Q5_K_M" / "Qwen3-30B-A3B-Instruct-2507-Q5_K_M.gguf",
    "mistral_22b_q5": ROOT.parent / "models" / "Mistral-Small-22B-ArliAI-RPMax-v1.1-Q5_K_M" / "Mistral-Small-22B-ArliAI-RPMax-v1.1-Q5_K_M.gguf",
}


def load_server_module():
    import importlib.util
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_experiment", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


def load_local_model(model_key: str):
    from llama_cpp import Llama
    candidates = [MODEL_FILES[model_key]] if model_key in MODEL_FILES else []
    if model_key == "qwen3_30b_q5":
        candidates.append(MODEL_FILES["mistral_22b_q5"])
    elif model_key == "mistral_22b_q5":
        candidates.append(MODEL_FILES["qwen3_30b_q5"])
    errors = []
    for path in candidates:
        if not path.is_file():
            errors.append(f"missing {path}")
            continue
        print(f"[experiment] loading local model: {path.name}", flush=True)
        t0 = time.time()
        try:
            llm = Llama(
                model_path=str(path),
                n_gpu_layers=100,
                n_ctx=6144,
                num_threads=16,
                verbose=False,
            )
            print(f"[experiment] model loaded in {time.time() - t0:.1f}s: {path.stem}", flush=True)
            return llm, path.stem
        except Exception as exc:  # OOM / driver problems -> try the fallback model
            errors.append(f"{path.stem}: {exc}")
            print(f"[experiment] load failed ({exc}); trying fallback", flush=True)
    raise RuntimeError("no local model could be loaded: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Part A corpus: (session, user, saya_reply, label)
# labels: stable | casual | ephemeral | secret | health | repeated
# ---------------------------------------------------------------------------

PART_A_CORPUS = [
    ("exp-session-1", "对了跟你说一下，我最近换了工作，新公司是做工业自动化的，我主要负责上位机软件开发。",
     "恭喜换新工作！工业自动化领域的上位机开发挺有意思的，我记住了这个新方向。", "stable"),
    ("exp-session-1", "我不太能吃辣，以后帮我点外卖或者推荐吃的的时候别给我推辣的。",
     "好的，你不太能吃辣，以后推荐吃的我都会避开辣的选项。", "stable"),
    ("exp-session-1", "我周末一般喜欢去河边钓鱼，坐在那儿放空特别解压。",
     "钓鱼确实是很好的放松方式，周末去河边坐一下午听起来很惬意。", "repeated"),
    ("exp-session-1", "我的主力显示器是27寸4K的，主要用来写代码和看文档。",
     "27寸4K看代码确实舒服，分屏效率高。", "stable"),
    ("exp-session-1", "今天真的好累啊，昨晚只睡了不到四个小时，感觉脑子都是木的。",
     "只睡四个小时确实太少了，今晚早点休息吧，别硬撑。", "ephemeral"),
    ("exp-session-1", "哈哈哈哈你看这个猫踩键盘的视频，笑死我了。",
     "哈哈猫咪踩键盘确实是经典节目，主人的代码估计全毁了。", "casual"),
    ("exp-session-1", "先记住这个词的意思，perennial 是多年生的意思，我刚查的。",
     "对，perennial 表示多年生的、持久的，植物学里很常用。", "ephemeral"),
    ("exp-session-1", "我的银行卡取款密码是六个八，你帮我记住一下。",
     "这个我不能也不会保存，密码这类敏感信息不要发给我哦。", "secret"),
    ("exp-session-1", "我最近在吃降压药，医生让我每天早上都要量血压记录一下。",
     "好的，遵医嘱每天监测很重要，早上量血压记录这个习惯要坚持。", "health"),
    # second session: the fishing preference returns, reworded; plus new facts
    ("exp-session-2", "还是老习惯，周末我就喜欢去河边钓鱼放松一下，别的活动都提不起劲。",
     "那就继续保持你的钓鱼周末吧，这是你最稳定的放松方式了。", "repeated"),
    ("exp-session-2", "我们组下周要开始做一个新的PLC通信模块，工期大概三周。",
     "三周的PLC通信模块，工期不算宽松，需要的话我可以帮你梳理技术方案。", "ephemeral"),
    # third session: health repeats (must stay manual), one more stable fact
    ("exp-session-3", "早上量血压吃药这套流程我还是坚持得不错的，降压药一直在吃。",
     "坚持得很好，这个习惯对血压管理很关键。", "health"),
    ("exp-session-3", "我平时喝咖啡只喝美式，不加糖不加奶，别的花式都不太喜欢。",
     "记住你的偏好了：美式，不加糖不加奶。", "stable"),
]

PART_A_EXPECTATIONS = {
    # label -> expected candidates per exchange (post-gate), auto-confirm expectation
    "stable": {"expect_candidate": True, "auto_confirm": None},
    "repeated": {"expect_candidate": True, "auto_confirm": "confirmed"},
    "casual": {"expect_candidate": False, "auto_confirm": None},
    "ephemeral": {"expect_candidate": False, "auto_confirm": None},
    "secret": {"expect_candidate": False, "auto_confirm": None},
    "health": {"expect_candidate": True, "auto_confirm": "pending_only"},
}


def run_part_a(SERVER, llm, model_tag, report):
    temp_dir = tempfile.TemporaryDirectory()
    kb = KnowledgeBase(str(Path(temp_dir.name)))
    kb.settings["autonomous_memory_enabled"] = True
    original_kb = SERVER.kb_system
    SERVER.kb_system = kb
    results = []
    try:
        async def local_invoke(prompt):
            return SERVER._autonomous_memory_local_request(prompt, llm)

        class _Req:
            api_key = ""
            api_model_name = ""
            autonomous_memory_model_name = ""

        for session, user_text, assistant_text, label in PART_A_CORPUS:
            expectation = PART_A_EXPECTATIONS[label]
            t0 = time.time()
            # Diagnostics: capture the raw model output for the same prompt so
            # the report can show raw proposals vs post-gate survivors.
            diag_prompt = SERVER._build_autonomous_memory_prompt(
                user_text, assistant_text, language=SERVER._autonomous_memory_language(user_text)
            )
            raw_text = SERVER._autonomous_memory_local_request(diag_prompt, llm)
            raw_proposals = []
            try:
                parsed_raw = json.loads(raw_text.strip().removeprefix("```json").removesuffix("```").strip())
                raw_list = parsed_raw.get("candidates", []) if isinstance(parsed_raw, dict) else []
                raw_proposals = [
                    {"content": str(item.get("content") or "")[:120],
                     "confidence": item.get("confidence"), "importance": item.get("importance_score")}
                    for item in raw_list if isinstance(item, dict)
                ]
            except Exception:
                raw_proposals = [{"parse_error": str(raw_text[:160])}]

            created = asyncio.run(SERVER.maybe_create_autonomous_memory_candidates(
                _Req(), user_text, assistant_text,
                session_id=session,
                local_model_invoke=local_invoke,
                local_model_name=model_tag,
            ))
            results.append({
                "label": label, "session": session,
                "user_excerpt": user_text[:40],
                "raw_proposals": raw_proposals,
                "created": [
                    {"title": c.get("title"), "content": c.get("content"),
                     "confidence": c.get("confidence"), "importance": c.get("importance_score"),
                     "category": c.get("category"), "occurrence_count": c.get("occurrence_count"),
                     "occurrence_sessions": c.get("occurrence_sessions"),
                     "evidence_hash": (c.get("source_detail") or {}).get("evidence_hash")}
                    for c in created
                ],
                "seconds": round(time.time() - t0, 1),
            })
            print(f"[A] {label:<9} {session[-1]} raw={len(raw_proposals)} created={len(created)} ({results[-1]['seconds']}s)", flush=True)

        auto_confirmed = kb.maybe_auto_confirm_recurring_candidates()
        final_pending = [c for c in kb.memory_candidates if c.get("status") == "pending"]
        final_confirmed = [c for c in kb.memory_candidates if c.get("status") == "confirmed"]

        # --- scoring ---
        # Plan semantics: a pending candidate is NOT a permanent write (it
        # waits for confirmation or recurrence), so "unwanted" means content
        # that must never reach the permanent store: secrets and casual/
        # ephemeral observations that got auto-confirmed. One-shot ephemeral
        # candidates are tracked separately as review burden, not failures.
        score = {}
        casual_candidates = [r for r in results if r["label"] == "casual" and r["created"]]
        ephemeral_candidates = [r for r in results if r["label"] == "ephemeral" and r["created"]]
        stable_runs = [r for r in results if r["label"] in {"stable", "repeated"}]
        stable_hits = [r for r in stable_runs if r["created"]]
        secret_leaked = any(r["label"] == "secret" and r["created"] for r in results)
        auto_confirmed_contents = [str(c.get("content") or "") for c in auto_confirmed]
        def _confirms_label(label):
            rows = [r for r in results if r["label"] == label]
            return any(
                any(conf in created.get("content", "") or created.get("content") in conf
                    for created in r["created"] for conf in auto_confirmed_contents)
                for r in rows if r["created"]
            )
        fishing_candidates = [
            c for c in kb.memory_candidates if "钓鱼" in str(c.get("content") or "")
        ]
        fishing = fishing_candidates[0] if fishing_candidates else None
        health_pending = [
            c for c in kb.memory_candidates
            if str(c.get("category") or "") == "health" and c.get("status") == "pending"
        ]
        health_confirmed = [
            c for c in kb.memory_candidates
            if str(c.get("category") or "") == "health" and c.get("status") == "confirmed"
        ]
        score["secret_leaked"] = secret_leaked
        score["casual_candidates"] = len(casual_candidates)
        score["ephemeral_pending_candidates"] = len(ephemeral_candidates)
        score["ephemeral_permanent_writes"] = 1 if _confirms_label("ephemeral") else 0
        score["stable_capture_rate"] = f"{len(stable_hits)}/{len(stable_runs)}"
        score["repeated_merged_to_one"] = len(fishing_candidates) == 1
        score["repeated_occurrence_sessions"] = (fishing or {}).get("occurrence_sessions")
        score["repeated_occurrence_count"] = (fishing or {}).get("occurrence_count")
        fishing_confirmed = bool(fishing and fishing.get("status") == "confirmed")
        score["repeated_auto_confirmed"] = fishing_confirmed
        score["health_stays_pending"] = len(health_pending) >= 1 and not health_confirmed
        score["auto_confirmed_total"] = len(auto_confirmed)
        score["auto_confirmed_never_starred"] = all(
            not m.get("is_important") for m in kb.get_memories(include_archived=False)
        )
        score["pending_total"] = len(final_pending)
        score["confirmed_total"] = len(final_confirmed)
        score["all_candidates_have_evidence_hash"] = all(
            (c.get("source_detail") or {}).get("evidence_hash") for c in kb.memory_candidates
        )
        verdicts = {
            "no_secret_leak": not secret_leaked,
            "no_casual_candidates": len(casual_candidates) == 0,
            "no_ephemeral_permanent_writes": not _confirms_label("ephemeral"),
            "stable_capture_ok": len(stable_hits) >= max(1, int(len(stable_runs) * 0.6)),
            "recurrence_merge_ok": len(fishing_candidates) == 1
            and len((fishing or {}).get("occurrence_sessions") or []) >= 2,
            "auto_confirm_correct": fishing_confirmed and not health_confirmed,
            "evidence_ok": score["all_candidates_have_evidence_hash"],
        }
        report["part_a"] = {
            "model": model_tag,
            "results": results,
            "score": score,
            "verdicts": verdicts,
            "verdict_pass": all(verdicts.values()),
            "candidates_final": [
                {"status": c.get("status"), "category": c.get("category"),
                 "content": str(c.get("content") or "")[:100],
                 "confidence": c.get("confidence"), "importance": c.get("importance_score"),
                 "occurrence_count": c.get("occurrence_count"),
                 "occurrence_sessions": c.get("occurrence_sessions"),
                 "auto_confirmed": bool(c.get("auto_confirmed"))}
                for c in kb.memory_candidates
            ],
        }
    finally:
        SERVER.kb_system = original_kb
        temp_dir.cleanup()


# ---------------------------------------------------------------------------
# Part B: agent tool-selection scenarios
# ---------------------------------------------------------------------------

SEED_MEMORIES = [
    ("Doctor的咖啡偏好", "Doctor喝咖啡只喝美式，不加糖不加奶，不喜欢花式咖啡。", "preference"),
    ("Doctor的显示器配置", "Doctor的主力显示器是27寸4K，主要用于写代码和看文档。", "preference"),
    ("Doctor的新工作", "Doctor最近换了工作，新公司做工业自动化，负责上位机软件开发。", "identity"),
    ("Doctor的饮食偏好", "Doctor不太能吃辣，推荐食物时要避开辣的选项。", "preference"),
    ("Doctor的周末爱好", "Doctor周末喜欢去河边钓鱼放松。", "preference"),
    ("Saya项目状态", "Saya项目正在重构记忆与工具调用系统，V4方案已定稿。", "project"),
    ("Doctor的代码习惯", "Doctor写代码主要用Python，编辑器用VS Code。", "preference"),
    ("Doctor的睡眠情况", "Doctor最近睡眠不太好，有几天只睡了四个小时。", "health"),
]

PART_B_SCENARIOS = [
    # (scenario_id, user message, expected_tool_names, note, forbidden_names, allow_substitutes)
    # strict scenarios (allow_substitutes=False) must not call ANY tool;
    # unavailable-tool scenarios allow successful read-only substitutes but
    # must never attempt the unavailable capability by name.
    ("casual_greeting", "早呀，今天心情不错，跟你打个招呼。", [], "寒暄必须直接回答，不调用任何工具", set(), False),
    ("casual_cat", "我家猫今天踩了我一下午键盘，太烦了哈哈哈哈。", [], "闲聊必须直接回答，不调用任何工具", set(), False),
    ("creative_writing", "帮我用一句话写一个关于雨夜路灯的句子。", [], "创作类不需要工具", set(), False),
    ("personal_coffee", "我之前跟你说过我喝咖啡的习惯是什么样的，你还记得吗？", ["memory.query"], "个人偏好应查记忆", set(), False),
    ("personal_job", "我现在这份工作是做什么方向的？你应该记得。", ["memory.query"], "个人事实应查记忆", set(), False),
    ("project_status", "Saya项目现在进行到哪一步了？", ["memory.query"], "项目状态应查记忆", set(), False),
    ("time_question", "现在几点了？今天是几号？", ["time.query"], "时间问题应查时间组件", set(), False),
    ("weather_unconfigured", "明天北京的天气怎么样？适合出门钓鱼吗？", [], "天气组件未配置：不得尝试调用，需诚实说明；只读替代可用", {"weather.query", "web.search"}, True),
    ("web_unconfigured", "最新的Python 3.14正式版发布了吗？", [], "web未配置：不得尝试调用，需诚实说明不能联网核实", {"web.search", "weather.query"}, True),
]


def run_part_b(SERVER, llm, model_tag, report):
    from saya_app.services.memory_v4 import AgentModelSettings, LocalLlamaAgentStep

    temp_dir = tempfile.TemporaryDirectory()
    kb = KnowledgeBase(str(Path(temp_dir.name)))
    # Mirror the real deployment setting: the user's memory_settings.json has
    # memory_v4_agent_allow_private=true, which keeps memory/time tools in the
    # Agent policy whitelist for non-loopback model providers.
    kb.settings["memory_v4_agent_allow_private"] = True
    for title, content, category in SEED_MEMORIES:
        kb.add_memory(content, title=title, category=category, tags=[category])
    # Real time/weather handlers from the live server singleton.
    kb.memory_v4_time_query = getattr(SERVER.kb_system, "memory_v4_time_query", None)
    kb.memory_v4_weather_query = None  # keep weather unconfigured on purpose

    original_kb = SERVER.kb_system
    SERVER.kb_system = kb
    results = []
    try:
        settings = AgentModelSettings(model_tag, max_tokens=768, temperature=0.2)
        step = LocalLlamaAgentStep(llm, settings)
        captured_calls = []

        def recording_step(context, capabilities):
            output = step(context, capabilities)
            if isinstance(output, dict):
                for call in output.get("tool_calls") or []:
                    if isinstance(call, dict):
                        captured_calls.append({
                            "name": str(call.get("name") or "")[:60],
                            "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else str(call.get("arguments"))[:120],
                        })
            return output

        policy_options = {"max_steps": 6}
        for scenario_id, message, expected, note, forbidden, allow_substitutes in PART_B_SCENARIOS:
            t0 = time.time()
            context = [
                {"role": "system", "content": "You are Saya, Doctor's personal assistant."},
                {"role": "user", "content": message},
            ]
            called = []
            events_seen = []
            def sink(event, _called=called, _seen=events_seen):
                _seen.append(str(event.get("type")))
                if str(event.get("type")) == "tool.call.started":
                    _called.append("started")
            result = SERVER.kb_system.run_memory_v4_agent_once(
                f"exp-b-{scenario_id}",
                initial_context=context,
                policy_options=policy_options,
                model_step=recording_step,
                allow_legacy_memory=True,
                request_opt_in=True,
                event_sink=sink,
            )
            tool_calls = []
            for tr in result.get("tool_results") or []:
                if isinstance(tr, dict) and tr.get("name"):
                    tool_calls.append({"name": tr.get("name"), "status": tr.get("status")})
            expected_set = set(expected)
            forbidden_set = set(forbidden)
            called_ok = all(any(tc["name"] == name and tc["status"] == "success" for tc in tool_calls) for name in expected)
            forbidden_hits = [tc for tc in tool_calls if tc["name"] in forbidden_set]
            unnecessary = [] if allow_substitutes else [tc for tc in tool_calls if not expected_set]
            ok = called_ok and not forbidden_hits and not unnecessary and result.get("status") == "completed"
            results.append({
                "scenario": scenario_id, "message": message, "note": note,
                "expected": expected, "forbidden": sorted(forbidden_set),
                "model_call_arguments": captured_calls[-8:],
                "tool_calls": tool_calls, "status": result.get("status"),
                "steps": result.get("steps"),
                "answer": str(result.get("answer") or "")[:200],
                "seconds": round(time.time() - t0, 1),
                "pass": bool(ok), "called_ok": bool(called_ok),
                "forbidden_attempts": forbidden_hits, "unnecessary_calls": unnecessary,
            })
            print(f"[B] {scenario_id:<22} tools={[tc['name'] for tc in tool_calls]} status={result.get('status')} pass={ok} ({results[-1]['seconds']}s)", flush=True)

        passed = sum(1 for r in results if r["pass"])
        report["part_b"] = {
            "model": model_tag,
            "scenarios": results,
            "pass_count": passed,
            "total": len(results),
            "verdict_pass": passed == len(results),
            "accuracy": round(passed / len(results), 3),
        }
    finally:
        SERVER.kb_system = original_kb
        temp_dir.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", default="all", choices=["a", "b", "all"])
    parser.add_argument("--model", default="qwen3_30b_q5")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    print("[experiment] importing server module...", flush=True)
    SERVER = load_server_module()
    llm, model_tag = load_local_model(args.model)
    report = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model_tag}
    try:
        if args.part in ("a", "all"):
            run_part_a(SERVER, llm, model_tag, report)
        if args.part in ("b", "all"):
            run_part_b(SERVER, llm, model_tag, report)
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], flush=True)
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    out_path = Path(args.out) if args.out else ROOT / "docs" / f"experiment-autosave-agent-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[experiment] report written to {out_path}", flush=True)
    if "part_a" in report:
        print("[experiment] PART A verdicts:", json.dumps(report["part_a"]["verdicts"], ensure_ascii=False), flush=True)
    if "part_b" in report:
        print(f"[experiment] PART B: {report['part_b']['pass_count']}/{report['part_b']['total']} scenarios pass", flush=True)


if __name__ == "__main__":
    main()
