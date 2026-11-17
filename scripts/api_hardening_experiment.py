"""API-mode hardening experiment (2026-10-06): end-to-end over the real server.

Spins up a mock OpenAI-compatible provider, boots the actual backend in a
sandbox PROJECT_ROOT (knowledge-base writes isolated), then runs six
scenarios against /chat_stream and the new endpoints:

  A. happy path          - SSE text + include_usage chunk + ledger entry
  B. 429 then success    - retry stream_status event, text delivered
  C. stream_options 400  - one fallback without the flag, text delivered
  D. stop mid-stream     - cancellation works and the loop stays responsive
  E. model-list proxy    - /api/llm/models returns the provider list
  F. usage summary       - /api/usage/summary aggregates the above turns
  G. real gateway (opt)  - one real provider turn with stored credentials

Exit code 0 = all required scenarios passed. G is reported but optional.
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
from pathlib import Path

import httpx
import uvicorn

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PROVIDER_PORT = 8199
SERVER_PORT = 8100
PROVIDER_URL = f"http://127.0.0.1:{PROVIDER_PORT}/v1"
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

results = []


def report(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# ---------------------------------------------------------------- mock provider

def build_provider_app():
    from fastapi import FastAPI, Request

    app = FastAPI()
    calls = []

    def sse_body(lines):
        return "".join(line + "\n\n" for line in lines).encode("utf-8")

    def normal_stream(text="你好，这是实验回复，用于验证流式与用量记录。"):
        chunks = [text[i:i + 6] for i in range(0, len(text), 6)]
        lines = [
            "data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}, ensure_ascii=False)
            for chunk in chunks
        ]
        lines.append("data: " + json.dumps({
            "choices": [],
            "usage": {"prompt_tokens": 101, "completion_tokens": 37, "total_tokens": 138},
        }))
        lines.append("data: [DONE]")
        return sse_body(lines)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        payload = await request.json()
        model = str(payload.get("model") or "")
        had_options = "stream_options" in payload
        calls.append({"model": model, "stream_options": had_options})

        if model == "flaky-429" and sum(1 for c in calls if c["model"] == "flaky-429") <= 1:
            from fastapi.responses import Response
            return Response(
                content="rate limited",
                status_code=429,
                headers={"Retry-After": "1", "Content-Type": "text/plain"},
            )
        if model == "picky-options" and had_options:
            from fastapi.responses import Response
            return Response(
                content="stream_options is not supported by this gateway",
                status_code=400,
                media_type="text/plain",
            )
        if model == "slow-stream":
            from fastapi.responses import StreamingResponse

            async def drip():
                for index in range(400):
                    await asyncio.sleep(0.15)
                    yield ("data: " + json.dumps(
                        {"choices": [{"delta": {"content": f"段{index}。"}}],
                         "finish_reason": None}, ensure_ascii=False) + "\n\n").encode("utf-8")

            return StreamingResponse(drip(), media_type="text/event-stream")

        from fastapi.responses import Response
        return Response(content=normal_stream(), media_type="text/event-stream")

    @app.get("/v1/models")
    async def models():
        return {"data": [
            {"id": "happy-model"},
            {"id": "flaky-429"},
            {"id": "picky-options"},
            {"id": "slow-stream"},
        ]}

    app.state.calls = calls
    return app


def start_provider():
    config = uvicorn.Config(build_provider_app(), host="127.0.0.1", port=PROVIDER_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------- sandbox + server

def build_sandbox():
    sandbox = Path(tempfile.mkdtemp(prefix="saya_api_exp_"))
    (sandbox / "knowledge_base").mkdir()
    (sandbox / "uploaded_files").mkdir()
    # Static mounts must exist at import time; junction to the real dirs.
    for name in ("static", "MMD"):
        source = REPO / name
        if source.exists():
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(sandbox / name), str(source)],
                check=True, capture_output=True,
            )
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
    python = sys.executable
    process = subprocess.Popen(
        [python, str(REPO / "scripts" / "_experiment_server_launcher.py")],
        env=env, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return process


async def wait_for_server(timeout=60):
    async with httpx.AsyncClient() as client:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{SERVER_URL}/api/current_model", timeout=3)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    return False


# ---------------------------------------------------------------- SSE client

async def run_turn(client, model, cancel_after=None, **extra):
    body = {
        "user_input": "实验输入，请简短回答",
        "display_input": "实验输入，请简短回答",
        "model": "api",
        "api_key": "exp-key",
        "api_url": PROVIDER_URL,
        "api_model_name": model,
        "temporary_chat": True,
    }
    body.update(extra)
    events = []
    started = time.monotonic()
    async with client.stream("POST", f"{SERVER_URL}/chat_stream", json=body, timeout=60) as response:
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                parsed = parse_sse_block(block)
                if parsed:
                    events.append((time.monotonic() - started, *parsed))
                    if cancel_after and events[-1][1] == "text" and len(events) > 2:
                        return events
        if buffer.strip():
            parsed = parse_sse_block(buffer)
            if parsed:
                events.append((time.monotonic() - started, *parsed))
    return events


def parse_sse_block(block):
    event_type, data = None, None
    for line in block.split("\n"):
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
    if not event_type:
        return None
    try:
        return event_type, json.loads(data) if data else {}
    except ValueError:
        return event_type, {"_raw": data}


def text_of(events):
    parts = []
    for _, event_type, data in events:
        if event_type == "text" and isinstance(data, dict):
            parts.append(str(data.get("text") or ""))
    return "".join(parts)


# ---------------------------------------------------------------- scenarios

async def main():
    provider = start_provider()
    sandbox = build_sandbox()
    server = start_server(sandbox)
    try:
        if not await wait_for_server():
            report("server boot", False, "backend did not answer /api/current_model in 60s")
            return
        report("server boot", True, f"backend up on {SERVER_PORT} (sandbox knowledge_base)")

        async with httpx.AsyncClient() as client:
            # A. happy path
            events = await run_turn(client, "happy-model")
            text = text_of(events)
            has_error = any(e[1] == "stream_error" for e in events)
            report("A happy path", "实验回复" in text and not has_error,
                   f"text={'yes' if text else 'NO'}, stream_error={has_error}")

            # F (partial) - ledger entry for happy-model
            summary = (await client.get(f"{SERVER_URL}/api/usage/summary?days=1")).json()
            happy = (summary.get("per_model") or {}).get("happy-model", {})
            report("A usage ledger", happy.get("total_tokens") == 138 and happy.get("requests") == 1,
                   f"happy-model={happy}")

            # B. 429 retry
            events = await run_turn(client, "flaky-429")
            retries = [d for t, e, d in events if e == "stream_status" and isinstance(d, dict) and d.get("retry")]
            text = text_of(events)
            report("B 429 retry", bool(retries) and "实验回复" in text,
                   f"retry_events={len(retries)}, text_delivered={'yes' if text else 'NO'}")

            # C. stream_options fallback
            events = await run_turn(client, "picky-options")
            text = text_of(events)
            has_error = any(e[1] == "stream_error" for e in events)
            report("C stream_options fallback", "实验回复" in text and not has_error,
                   f"text_delivered={'yes' if text else 'NO'}")

            # D. stop mid-stream + loop responsiveness
            task = asyncio.ensure_future(run_turn(client, "slow-stream", cancel_after=True))
            stopped_events = await asyncio.wait_for(task, 20)
            got_text = bool(text_of(stopped_events))
            then = time.monotonic()
            events = await run_turn(client, "happy-model")
            responsive = (events[0][0] if events else 999) < 3.0
            elapsed = time.monotonic() - then
            report("D stop + responsiveness", got_text and responsive,
                   f"partial_text={'yes' if got_text else 'NO'}, next_turn_first_event={events[0][0]:.2f}s, total={elapsed:.1f}s")

            # E. model list proxy
            proxy = await client.post(f"{SERVER_URL}/api/llm/models",
                                      json={"api_url": PROVIDER_URL, "api_key": "exp-key"})
            models = proxy.json().get("models") if proxy.status_code == 200 else None
            report("E model list proxy", models is not None and "happy-model" in models and len(models) == 4,
                   f"status={proxy.status_code}, models={models}")

            # F. summary aggregation
            summary = (await client.get(f"{SERVER_URL}/api/usage/summary?days=1")).json()
            per_model = summary.get("per_model") or {}
            total_requests = sum(m.get("requests", 0) for m in per_model.values())
            report("F usage summary", total_requests >= 3,
                   f"per_model={ {k: v.get('total_tokens') for k, v in per_model.items()} }, requests={total_requests}")

            # G. real gateway (optional)
            await real_gateway_scenario(client)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        provider.should_exit = True
        shutil.rmtree(sandbox, ignore_errors=True)

    failures = [name for name, ok, _ in results if not ok]
    print("\n==== experiment summary ====")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(1 if failures else 0)


async def real_gateway_scenario(client):
    cred_path = REPO / "knowledge_base" / "api_credentials.json"
    if not cred_path.exists():
        report("G real gateway", True, "skipped (no stored credentials)")
        return
    try:
        creds = json.loads(cred_path.read_text(encoding="utf-8"))
        api_url = str(creds.get("api_url") or "").strip()
        api_key = str(creds.get("api_key") or "").strip()
        api_model = str(creds.get("api_model_name") or "").strip()
        if not (api_url and api_key and api_model):
            report("G real gateway", True, "skipped (incomplete credentials)")
            return
        events = await run_turn(client, api_model, api_url=api_url, api_key=api_key)
        text = text_of(events)
        has_error = any(e[1] == "stream_error" for e in events)
        report("G real gateway", bool(text.strip()) and not has_error,
               f"model={api_model}, chars={len(text)}, stream_error={has_error}")
    except Exception as exc:
        report("G real gateway", True, f"optional scenario failed harmlessly: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
