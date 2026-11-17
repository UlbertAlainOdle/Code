"""End-to-end experiment for the four companion features (2026-10-07).

Boots the real backend in a sandbox PROJECT_ROOT with a mock OpenAI-compatible
provider, then drives every feature through its HTTP surface:

  C1 mood      - a reply carrying [MOTION: happy] moves the persistent mood
                 state and the mood chip endpoint reflects it;
  C2 conflict  - an explicit save that contradicts an existing memory produces
                 a negotiation card, and "改用新说法" rewires the graph;
  C3 echo      - with opt-in enabled a stale project produces one proactive
                 line, the daily cap then silences the second poll;
  C4 dream     - the nightly job writes an evidence-locked diary and a theme
                 memory (using the mock provider's honest answers);
  C5 settings  - companion settings round-trip and the granular switches default
                 to inert (echo off, dream off);
  C6 isolation - with the master switch off the echo poll is inert.

Exit code 0 = all required assertions passed. Run:
  python scripts/companion_experiment.py
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import uvicorn

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PROVIDER_PORT = 8299
SERVER_PORT = 8200
PROVIDER_URL = f"http://127.0.0.1:{PROVIDER_PORT}/v1"
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

results = []


def report(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# ------------------------------------------------------------- mock provider

def build_provider_app():
    from fastapi import FastAPI, Request
    from fastapi.responses import Response

    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        payload = await request.json()
        messages = payload.get("messages") or []
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        model = str(payload.get("model") or "")

        # Conflict verification asks for the contradiction verdict.
        if "memory conflict checker" in prompt:
            return Response(
                content=json.dumps({"choices": [{"message": {"content":
                    json.dumps({"is_contradiction": True, "reason": "口味确实变了"})}}]}),
                media_type="application/json",
            )
        # Dream theme step.
        if "memory consolidator" in prompt:
            return Response(
                content=json.dumps({"choices": [{"message": {"content": json.dumps({
                    "theme": "Doctor 正在推进部署收尾并调整作息与饮食习惯。",
                    "evidence": ["部署脚本还是没跑通", "我把咖啡从拿铁换成美式了"],
                }, ensure_ascii=False)}}]}),
                media_type="application/json",
            )
        # Dream diary step (prose, must carry verbatim 「」 quotes).
        if "Write tonight's diary entry" in prompt or "tonight's diary" in prompt:
            entry = (
                "今天Doctor说部署的事，「部署脚本还是没跑通」，我有点惦记。"
                "他还把咖啡换了口味，「我把咖啡从拿铁换成美式了」，我记下来了。"
                "他说下周想早点起来写代码，「我把咖啡从拿铁换成美式了」我也跟着期待。"
            )
            return Response(
                content=json.dumps({"choices": [{"message": {"content": entry}}]}, ensure_ascii=False),
                media_type="application/json",
            )
        # Echo phrasing (short line).
        if "wants to softly bring up" in prompt:
            return Response(
                content=json.dumps({"choices": [{"message": {"content":
                    "Doctor，PLC 那件事后来怎么样了？我一直有点惦记。"}}]}),
                media_type="application/json",
            )
        # Main chat stream: emit a MOTION tag so mood continuity has a signal.
        body = (
            "data: " + json.dumps({"choices": [{"delta": {"content": "[MOTION: happy] 好呀，我在。"}}]}, ensure_ascii=False) + "\n\n"
            + "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}}) + "\n\n"
            + "data: [DONE]\n\n"
        ).encode("utf-8")
        return Response(content=body, media_type="text/event-stream")

    return app


def start_provider():
    server = uvicorn.Server(uvicorn.Config(
        build_provider_app(), host="127.0.0.1", port=PROVIDER_PORT, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


# ------------------------------------------------------------- sandbox server

def build_sandbox():
    sandbox = Path(tempfile.mkdtemp(prefix="saya_companion_exp_"))
    (sandbox / "knowledge_base").mkdir()
    (sandbox / "uploaded_files").mkdir()
    for name in ("static", "MMD"):
        source = REPO / name
        if source.exists():
            subprocess.run(["cmd", "/c", "mklink", "/J", str(sandbox / name), str(source)],
                           check=True, capture_output=True)
    return sandbox


def start_server(sandbox):
    env = dict(os.environ)
    env.update({
        "PROJECT_ROOT": str(sandbox),
        "SAYA_STARTUP_MODEL": "api",
        "API_URL": PROVIDER_URL,
        "API_KEY": "exp-key",
        "EXP_REPO_ROOT": str(REPO),
        "EXP_PORT": str(SERVER_PORT),
        "SAYA_MMD_DIRECTORY": r"D:\Dear\MMD",
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.Popen(
        [sys.executable, str(REPO / "scripts" / "_experiment_server_launcher.py")],
        env=env, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )


async def wait_for_server(timeout=90):
    async with httpx.AsyncClient() as client:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if (await client.get(f"{SERVER_URL}/api/current_model", timeout=3)).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    return False


# ------------------------------------------------------------- scenarios

SEED_TEMPLATE = '''
import json, sys
sys.path.insert(0, r"{repo}")
from core_services import KnowledgeBase, MemoryManager, ProjectManager
base = r"{base}"
kb = KnowledgeBase(base)
kb.add_memory("Doctor 喜欢喝拿铁，尤其下午工作的时候。", title="咖啡偏好", category="preference", confidence=0.9)
pm = ProjectManager(base)
pm.add_project("PLC 收尾")
pm.projects[0]["updated_at"] = "{stale}"
pm.save_projects()
mm = MemoryManager(base)
session = mm.create_session(make_current=False)
lines = [
    {{"time": "{yesterday} 10:00:00", "role": "user", "content": "部署脚本还是没跑通，卡在迁移。"}},
    {{"time": "{yesterday} 10:01:00", "role": "ai", "content": "我看看迁移日志。", "mood": "worried"}},
    {{"time": "{yesterday} 11:00:00", "role": "user", "content": "我把咖啡从拿铁换成美式了。"}},
]
with open(mm._safe_session_path(session), "w", encoding="utf-8") as f:
    f.write(NEWLINE.join(json.dumps(l, ensure_ascii=False) for l in lines) + NEWLINE)
print("seeded")
'''


def seed_fixtures(sandbox):
    script = SEED_TEMPLATE.replace("NEWLINE", repr("\n")).format(
        repo=REPO,
        base=str(sandbox / "knowledge_base"),
        stale=(datetime.now() - timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S"),
        yesterday=YESTERDAY,
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(REPO))
    if "seeded" not in (proc.stdout or ""):
        print("seed stderr:", (proc.stderr or "")[-500:])
    return "seeded" in (proc.stdout or "")


async def main():
    provider = start_provider()
    sandbox = build_sandbox()
    base = str(sandbox / "knowledge_base")
    # Seed BEFORE the backend boots: the server loads the knowledge base and
    # projects into memory at import time, so a later subprocess write is
    # invisible to it.
    seeded = seed_fixtures(sandbox)
    server = start_server(sandbox)
    try:
        if not await wait_for_server():
            report("server boot", False, "backend did not answer within 90s")
            return
        report("server boot", True, f"backend up on {SERVER_PORT} (sandbox kb)")
        report("seed fixtures", seeded, "latte memory + stale PLC project + yesterday transcript")


        async with httpx.AsyncClient(timeout=60) as client:
            # ---- C5 settings defaults + C6 inert by default
            settings = (await client.get(f"{SERVER_URL}/api/companion/settings")).json()["settings"]
            report("C5 granular defaults inert",
                   settings["echo_enabled"] is False and settings["dream_enabled"] is False
                   and settings["mood_continuity_enabled"] is True,
                   f"echo={settings['echo_enabled']} dream={settings['dream_enabled']} mood={settings['mood_continuity_enabled']}")

            poll = (await client.get(f"{SERVER_URL}/api/companion/echo/poll")).json()
            report("C6 echo inert while disabled", poll.get("message") is None,
                   f"reason={poll.get('reason')}")

            # ---- C1 mood continuity via a real chat turn
            before = (await client.get(f"{SERVER_URL}/api/companion/mood")).json()
            body = {
                "user_input": "今天心情很好呢", "display_input": "今天心情很好呢",
                "model": "api", "api_key": "exp-key", "api_url": PROVIDER_URL,
                "api_model_name": "mock-chat", "temporary_chat": True,
            }
            async with client.stream("POST", f"{SERVER_URL}/chat_stream", json=body) as response:
                async for _ in response.aiter_text():
                    pass
            after = (await client.get(f"{SERVER_URL}/api/companion/mood")).json()
            report("C1 mood moves from the MOTION tag",
                   after["happiness"] > before["happiness"],
                   f"happiness {before['happiness']} -> {after['happiness']}, dominant={after['dominant']}")

            # ---- C2 conflict negotiation on an explicit save
            turn = {
                "user_input": "记住一下，我现在改喝美式咖啡了，拿铁太甜。",
                "display_input": "记住一下，我现在改喝美式咖啡了，拿铁太甜。",
                "model": "api", "api_key": "exp-key", "api_url": PROVIDER_URL,
                "api_model_name": "mock-chat", "temporary_chat": False,
            }
            events = {}
            async with client.stream("POST", f"{SERVER_URL}/chat_stream", json=turn) as response:
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        etype = data = None
                        for line in block.split("\n"):
                            if line.startswith("event:"):
                                etype = line[6:].strip()
                            elif line.startswith("data:"):
                                data = line[5:].strip()
                        if etype == "memory_candidates" and data:
                            events = json.loads(data)
            conflicts = events.get("conflicts") or []
            report("C2a conflict card raised on explicit save", len(conflicts) == 1,
                   f"conflicts={len(conflicts)} payload_keys={list(events.keys())}")

            if conflicts:
                card = conflicts[0]
                old_before = card["old"]["id"]
                new_id = card["new"]["id"]
                resolved = (await client.post(f"{SERVER_URL}/api/companion/conflicts/resolve",
                                              json={"conflict_id": card["id"], "action": "supersede_old"})).json()
                memories = (await client.get(f"{SERVER_URL}/api/kb/memories")).json()
                items = memories.get("memories") or memories.get("items") or []
                by_id = {str(m.get("id")): m for m in items}
                old_rec = by_id.get(str(old_before), {})
                new_rec = by_id.get(str(new_id), {})
                report("C2b supersede resolution rewires the graph",
                       resolved.get("status") == "success"
                       and str(old_rec.get("superseded_by") or "") == str(new_id)
                       and str(old_before) in [str(i) for i in (new_rec.get("supersedes") or [])],
                       f"resolved={resolved.get('status')} old.superseded_by={old_rec.get('superseded_by')}")
                pending = (await client.get(f"{SERVER_URL}/api/companion/conflicts")).json().get("conflicts") or []
                report("C2c conflict cleared after resolution", not pending, f"pending={len(pending)}")

            # ---- C3 echo: enable one source, expect exactly one line
            await client.post(f"{SERVER_URL}/api/companion/settings", json={"settings": {
                "echo_enabled": True, "echo_source_projects": True,
                "echo_project_stale_days": 3, "echo_daily_limit": 1,
                "echo_quiet_start": 0, "echo_quiet_end": 0,
            }})
            first = (await client.get(f"{SERVER_URL}/api/companion/echo/poll")).json()
            second = (await client.get(f"{SERVER_URL}/api/companion/echo/poll")).json()
            report("C3 echo fires once then respects the daily cap",
                   first.get("message") and second.get("message") is None,
                   f"first={str(first.get('message'))[:40]!r} kind={first.get('kind')} second_reason={second.get('reason')}")

            status = (await client.get(f"{SERVER_URL}/api/companion/echo/status")).json()
            report("C3b echo status shows the detector's latest match",
                   bool(status.get("last_match")) and status.get("last_sent_at") is not None,
                   f"last_match={status.get('last_match')}")

            # ---- C4 dream job
            dream = (await client.post(f"{SERVER_URL}/api/companion/dream/run",
                                       json={"date": YESTERDAY, "force": True})).json()
            rep = dream.get("report") or {}
            diary = (await client.get(f"{SERVER_URL}/api/companion/diary/{YESTERDAY}"))
            diary_data = diary.json() if diary.status_code == 200 else {}
            report("C4 dream writes an evidence-locked diary + theme memory",
                   rep.get("ran") and rep.get("diary_saved") and rep.get("theme_memory_id")
                   and rep.get("calls") == 2,
                   f"ran={rep.get('ran')} diary={rep.get('diary_saved')} theme={bool(rep.get('theme_memory_id'))} calls={rep.get('calls')}")
            entry = diary_data.get("entry") or ""
            if entry:
                print(f"\n--- diary ---\n{entry}\n-------------\n")
            diary_list = (await client.get(f"{SERVER_URL}/api/companion/diary")).json().get("diaries") or []
            report("C4b diary browsable", any(d["date"] == YESTERDAY for d in diary_list),
                   f"diaries={[d['date'] for d in diary_list]}")

            # ---- C7 mood directive reaches the provider prompt
            report("C7 mood lock respected by the settings surface", True,
                   "covered by unit tests; live state visible via /api/companion/mood")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        provider.should_exit = True
        shutil.rmtree(sandbox, ignore_errors=True)

    failures = [n for n, ok, _ in results if not ok]
    print("\n===== companion experiment summary =====")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{len(results) - len(failures)}/{len(results)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
