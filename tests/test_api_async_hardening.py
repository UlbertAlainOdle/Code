"""API-mode hardening: async streaming, retry, usage ledger, history budget.

Covers the 2026-10-06 pass:
- the provider call runs on the shared async httpx client (the SSE generator
  no longer blocks the event loop), with a MockTransport standing in for the
  provider so no real network or key is involved;
- transient failures (429 with Retry-After, transport errors, zero-output
  stream failures) retry automatically; retries only happen before any
  visible text;
- stream_options include_usage falls back once for gateways that reject it;
- the streamed usage chunk lands in ai_metadata and the daily ledger;
- Stop/cancellation cuts the provider stream promptly and the client stays
  healthy afterwards;
- API history budget resolves by model class or explicit override;
- /api/usage/summary and the /api/llm/models proxy work through the app.
"""

import asyncio
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_services import KnowledgeBase  # noqa: E402
from saya_app.services import api_usage  # noqa: E402


def load_server_module():
    import importlib.util

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_api_hardening_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()


def sse(*events):
    parts = []
    for event in events:
        data = event.encode("utf-8") if isinstance(event, str) else event
        parts.append(data if data.endswith(b"\n\n") else data + b"\n\n")
    return b"".join(parts)


def content_chunk(text):
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}".encode("utf-8")


DONE_LINE = b"data: [DONE]"


class _NoOp:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _RecorderManager:
    """Stand-in memory manager: records saves, no-ops everything else."""

    def __init__(self):
        self.saved = []
        self.time_events = _NoOp()

    def save_message(self, role, content, translation=None, metadata=None, session_id=None):
        self.saved.append({"role": role, "content": content,
                           "metadata": metadata or {}, "session_id": session_id})

    def update_message_field_by_request_id(self, *args, **kwargs):
        return True

    def _safe_session_path(self, session_id):
        return str(session_id or "recorder-session.jsonl")

    def create_session(self, make_current=False):
        return "recorder-session.jsonl"

    def load_session(self, session_id, make_current=False):
        return []

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class ApiUsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = str(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_summarize(self):
        api_usage.record_turn(self.base, "deepseek-v4", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        api_usage.record_turn(self.base, "deepseek-v4", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        api_usage.record_turn(self.base, "gpt-test", usage=None, completed=False)
        summary = api_usage.summarize(self.base, days=7)
        today = time.strftime("%Y-%m-%d")
        self.assertEqual(summary["per_model"]["deepseek-v4"]["requests"], 2)
        self.assertEqual(summary["per_model"]["deepseek-v4"]["total_tokens"], 165)
        self.assertEqual(summary["per_model"]["gpt-test"]["failed"], 1)
        self.assertEqual(summary["per_day"][today]["requests"], 3)
        self.assertEqual(summary["per_day"][today]["total_tokens"], 165)

    def test_reasoning_tokens_normalized(self):
        api_usage.record_turn(self.base, "m", {"prompt_tokens": 1, "completion_tokens": 2,
                                               "total_tokens": 3,
                                               "completion_tokens_details": {"reasoning_tokens": 7}})
        ledger = api_usage.load_ledger(self.base)
        today = time.strftime("%Y-%m-%d")
        self.assertEqual(ledger["days"][today]["m"]["reasoning_tokens"], 7)

    def test_corrupt_ledger_reads_empty(self):
        Path(self.base, "api_usage.json").write_text("{oops", encoding="utf-8")
        self.assertEqual(api_usage.summarize(self.base)["per_model"], {})


class HistoryBudgetTests(unittest.TestCase):
    def test_model_class_resolution(self):
        self.assertEqual(SERVER.resolve_api_history_char_budget("deepseek-v4-pro"), 40000)
        self.assertEqual(SERVER.resolve_api_history_char_budget("openai/gpt-oss-120b"), 8000)
        self.assertEqual(SERVER.resolve_api_history_char_budget("Qwen3-235B"), 32000)

    def test_override_wins_and_is_clamped(self):
        self.assertEqual(SERVER.resolve_api_history_char_budget("deepseek-v4-pro", 16000), 16000)
        self.assertEqual(SERVER.resolve_api_history_char_budget("x", 999999), 64000)
        self.assertEqual(SERVER.resolve_api_history_char_budget("x", 1), 2000)

    def test_trim_history_values_respects_budget(self):
        history = ["m" * 50] * 200  # 10000 chars total
        trimmed = SERVER.trim_history_values(history, turns=100, max_chars=40000)
        self.assertEqual(len(trimmed), 200)  # budget not reached: nothing dropped
        trimmed = SERVER.trim_history_values(history, turns=100, max_chars=2000)
        self.assertLessEqual(sum(len(m) for m in trimmed), 2000 + 100)


class SseLineIteratorTests(unittest.TestCase):
    def test_decodes_splits_and_strips_cr(self):
        class FakeResponse:
            async def aiter_bytes(self):
                yield b'data: {"a":1}\r\n'
                yield b''
                yield b'data: [DONE]\n\ndata: {"b"'
                yield b':2}'

        async def scenario():
            lines = [line async for line in SERVER._aiter_sse_lines(FakeResponse())]
            return lines

        lines = asyncio.run(scenario())
        self.assertEqual(lines, ['data: {"a":1}', 'data: [DONE]', 'data: {"b":2}'])


class RetryPolicyTests(unittest.TestCase):
    def test_backoff_growth_and_retry_after(self):
        self.assertAlmostEqual(SERVER._api_retry_backoff_seconds(1), 1.0)
        self.assertAlmostEqual(SERVER._api_retry_backoff_seconds(2), 2.0)
        self.assertAlmostEqual(SERVER._api_retry_backoff_seconds(5), 8.0)
        self.assertAlmostEqual(SERVER._api_retry_backoff_seconds(1, "12"), 12.0)
        self.assertAlmostEqual(SERVER._api_retry_backoff_seconds(1, "999"), 30.0)
        self.assertAlmostEqual(SERVER._api_retry_backoff_seconds(2, "bad"), 2.0)

    def test_stream_options_rejection_detection(self):
        self.assertTrue(SERVER._stream_options_rejected(400, "stream_options is not supported"))
        self.assertTrue(SERVER._stream_options_rejected(422, "Invalid include_usage"))
        self.assertFalse(SERVER._stream_options_rejected(401, "stream_options bad key"))
        self.assertFalse(SERVER._stream_options_rejected(500, "internal error"))


class DeepAnalysisTests(unittest.TestCase):
    def test_analysis_hint_extracted_async(self):
        def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "hint text"}}]})

        async def scenario():
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
            SERVER._api_httpx_client = client
            SERVER._api_httpx_client_loop = asyncio.get_running_loop()
            try:
                hint = await SERVER._run_deep_fast_analysis(
                    "http://mock/v1/chat/completions", {}, "question", "context", "fast-model"
                )
            finally:
                await client.aclose()
                SERVER._api_httpx_client = None
                SERVER._api_httpx_client_loop = None
            return hint

        self.assertEqual(asyncio.run(scenario()), "hint text")

    def test_analysis_http_failure_returns_empty(self):
        async def scenario():
            def handler(request):
                return httpx.Response(500, text="boom")

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
            SERVER._api_httpx_client = client
            SERVER._api_httpx_client_loop = asyncio.get_running_loop()
            try:
                hint = await SERVER._run_deep_fast_analysis(
                    "http://mock/v1/chat/completions", {}, "q", "c", "fast"
                )
            finally:
                await client.aclose()
                SERVER._api_httpx_client = None
                SERVER._api_httpx_client_loop = None
            return hint

        self.assertEqual(asyncio.run(scenario()), "")


class ChatStreamApiModeTests(unittest.TestCase):
    """End-to-end over chat_stream with a MockTransport provider."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = str(Path(self.tmp.name))
        self.kb = KnowledgeBase(self.base)
        self.kb.settings["autonomous_memory_enabled"] = False
        self.saved = {
            name: getattr(SERVER, name)
            for name in ("kb_system", "memory_manager", "KNOWLEDGE_BASE_DIR",
                         "refresh_daily_activity_memory", "_api_httpx_client",
                         "_api_httpx_client_loop", "API_FIRST_VISIBLE_TEXT_TIMEOUT_SECONDS")
        }
        SERVER.kb_system = self.kb
        SERVER.recorder = _RecorderManager()
        SERVER.memory_manager = SERVER.recorder
        SERVER.KNOWLEDGE_BASE_DIR = self.base
        SERVER.refresh_daily_activity_memory = lambda: None
        self.requests_seen = []
        # Non-temporary API turns sync request credentials into the REAL
        # knowledge_base/api_credentials.json (chat_stream -> save_credentials).
        # Never let tests touch that file.
        from saya_app.services.conversation import conversation_service
        self._conversation_service = conversation_service
        self._save_credentials = conversation_service.save_credentials
        conversation_service.save_credentials = lambda **kwargs: {"saved": False}

    def tearDown(self):
        client = SERVER._api_httpx_client
        loop = SERVER._api_httpx_client_loop
        if client is not None and loop is not None:
            try:
                asyncio.run(client.aclose())
            except Exception:
                pass
        self._conversation_service.save_credentials = self._save_credentials
        for name, value in self.saved.items():
            setattr(SERVER, name, value)
        self.tmp.cleanup()

    def _install_client(self, handler):
        # Must be called from inside the running loop (async test body).
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
        SERVER._api_httpx_client = client
        SERVER._api_httpx_client_loop = asyncio.get_running_loop()
        return client

    def _req(self, **overrides):
        params = dict(
            user_input="你好，请介绍一下你自己",
            display_input="你好，请介绍一下你自己",
            model="api",
            api_key="test-key",
            api_url="http://mock/v1",
            api_model_name="mock-chat-model",
            temporary_chat=True,
        )
        params.update(overrides)
        return SERVER.ChatWithMotion(**params)

    async def _collect(self, req):
        response = await SERVER.chat_stream(req)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    @staticmethod
    def _visible_text(raw):
        parts = []
        for event_type, data in ChatStreamApiModeTests._parse_events(raw):
            if event_type == "text" and data:
                try:
                    parts.append(json.loads(data).get("text") or "")
                except ValueError:
                    pass
        return "".join(parts)

    @staticmethod
    def _parse_events(raw):
        events = []
        for block in raw.split("\n\n"):
            event_type, data = "message", None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
            events.append((event_type, data))
        return events

    def test_happy_path_streams_text_and_records_usage(self):
        def handler(request):
            self.requests_seen.append(json.loads(request.content.decode("utf-8")))
            body = sse(
                content_chunk("你好呀"),
                content_chunk("，我是Saya。"),
                "data: " + json.dumps({"choices": [], "usage": {
                    "prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                }}),
            ) + DONE_LINE + b"\n\n"
            return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

        async def scenario():
            self._install_client(handler)
            raw = await self._collect(self._req())
            return raw

        raw = asyncio.run(scenario())
        self.assertIn("你好呀", self._visible_text(raw))
        self.assertIn("我是Saya", self._visible_text(raw))
        self.assertNotIn("stream_error", raw)
        # stream_options was requested
        self.assertEqual(self.requests_seen[0].get("stream_options"), {"include_usage": True})
        # usage landed in the ledger under the tmp knowledge base
        ledger = api_usage.load_ledger(self.base)
        today = time.strftime("%Y-%m-%d")
        entry = ledger["days"][today]["mock-chat-model"]
        self.assertEqual(entry["total_tokens"], 128)
        self.assertEqual(entry["completed"], 1)

    def test_default_memory_reaches_api_prompt_and_temporary_chat_excludes_it(self):
        saved = self.kb.capture_explicit_memory('记住：我最喜欢的动物是乌鸦。')
        def handler(request):
            self.requests_seen.append(json.loads(request.content.decode('utf-8')))
            return httpx.Response(200, content=sse(content_chunk('你的偏好我记得。'), DONE_LINE),
                                  headers={'Content-Type':'text/event-stream'})
        async def scenario():
            self._install_client(handler)
            normal = await self._collect(self._req(user_input='还记得我最喜欢的动物吗？', temporary_chat=False, recall_mode='on'))
            self.assertIn(saved['id'], normal)
            self.assertIn('乌鸦', self.requests_seen[-1]['messages'][0]['content'])
            temporary = await self._collect(self._req(user_input='还记得我最喜欢的动物吗？', temporary_chat=True, recall_mode='on'))
            self.assertNotIn('乌鸦', self.requests_seen[-1]['messages'][0]['content'])
            self.assertNotIn(saved['id'], temporary)
        asyncio.run(scenario())

    def test_deepseek_effort_and_public_status_with_final_mood_snapshot(self):
        def handler(request):
            self.requests_seen.append(json.loads(request.content.decode("utf-8")))
            reasoning = "data: " + json.dumps({"choices":[{"delta":{"reasoning_content":"PRIVATE_REASONING_SENTINEL"}}]})
            return httpx.Response(200, content=sse(reasoning, content_chunk("[MOOD_SAYA: stressed]让我先确认一下。"), DONE_LINE), headers={"Content-Type":"text/event-stream"})

        async def scenario():
            self._install_client(handler)
            return await self._collect(self._req(api_url="https://api.deepseek.com", api_model_name="deepseek-v4-flash", thinking_effort="low", temporary_chat=False))

        raw = asyncio.run(scenario())
        self.assertEqual(self.requests_seen[0]["reasoning_effort"], "low")
        self.assertEqual(self.requests_seen[0]["thinking"], {"type":"enabled"})
        self.assertNotIn("PRIVATE_REASONING_SENTINEL", raw)
        events = [(kind, json.loads(data)) for kind, data in self._parse_events(raw) if data]
        self.assertTrue(any(kind == "assistant_status" and data.get("phase") == "thinking" for kind, data in events))
        answer = next(data for kind, data in events if kind == "answer_complete")
        self.assertEqual(answer["text"], "让我先确认一下。")
        self.assertEqual(answer["mood_state"]["speech_mood"], "stressed")
        saved = next(item for item in SERVER.recorder.saved if item["role"] == "ai")
        self.assertEqual(saved["metadata"]["mood_state"], answer["mood_state"])

    def test_mood_context_and_updates_respect_enabled_setting(self):
        from core_services import EmotionEngine
        engine = EmotionEngine(self.base)
        engine.state.update(happiness=12, excitement=12, stress=0, dominant="sad", is_locked=False)

        def handler(request):
            self.requests_seen.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, content=sse(content_chunk("[MOTION: happy]你好呀。")) + DONE_LINE + b"\n\n", headers={"Content-Type": "text/event-stream"})

        async def scenario():
            self._install_client(handler)
            with patch.object(SERVER, "emotion_engine", engine):
                self.kb.settings["mood_continuity_enabled"] = False
                raw_off = await self._collect(self._req())
                self.assertEqual(engine.state["happiness"], 12)
                self.assertFalse(any("[Current mood context]" in str(item.get("content")) for item in self.requests_seen[-1]["messages"]))
                self.kb.settings["mood_continuity_enabled"] = True
                raw_on = await self._collect(self._req())
                # Session snapshots now own continuity; the old global engine
                # must never move when a different/temporary chat completes.
                self.assertEqual(engine.state["happiness"], 12)
                events = [(kind, json.loads(data)) for kind, data in self._parse_events(raw_on) if kind == "turn_mood" and data]
                self.assertEqual(events[-1][1]["state"]["speech_mood"], "happy")
                self.assertGreater(events[-1][1]["state"]["happiness"], 50)
                self.assertTrue(any("[Current mood context]" in str(item.get("content")) for item in self.requests_seen[-1]["messages"]))
                self.assertIn("你好呀", self._visible_text(raw_off))
                self.assertIn("你好呀", self._visible_text(raw_on))

        asyncio.run(scenario())

    def test_usage_metadata_persisted_on_ai_message(self):
        def handler(request):
            body = sse(
                content_chunk("好"),
                "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}}),
            ) + DONE_LINE + b"\n\n"
            return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

        async def scenario():
            self._install_client(handler)
            raw = await self._collect(self._req(temporary_chat=False))
            return raw

        asyncio.run(scenario())
        ai_saves = [s for s in SERVER.recorder.saved if s["role"] == "ai"]
        self.assertTrue(ai_saves)
        usage = ai_saves[0]["metadata"].get("api_usage")
        self.assertEqual(usage["model"], "mock-chat-model")
        self.assertEqual(usage["total_tokens"], 10)

    def test_429_retries_then_succeeds(self):
        state = {"calls": 0}

        def handler(request):
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(429, text="rate limited", headers={"Retry-After": "0"})
            body = sse(content_chunk("第二次成功")) + DONE_LINE + b"\n\n"
            return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

        async def scenario():
            self._install_client(handler)
            return await self._collect(self._req())

        raw = asyncio.run(scenario())
        self.assertEqual(state["calls"], 2)
        self.assertIn("第二次成功", self._visible_text(raw))
        retry_events = [e for e in self._parse_events(raw) if e[0] == "stream_status" and e[1] and "retry" in e[1]]
        self.assertTrue(retry_events, "expected a retry stream_status event")

    def test_stream_options_rejection_falls_back_once(self):
        state = {"calls": 0}

        def handler(request):
            state["calls"] += 1
            payload = json.loads(request.content.decode("utf-8"))
            if "stream_options" in payload:
                return httpx.Response(400, text="stream_options is not supported by this gateway")
            body = sse(content_chunk("无usage流")) + DONE_LINE + b"\n\n"
            return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

        async def scenario():
            self._install_client(handler)
            return await self._collect(self._req())

        raw = asyncio.run(scenario())
        self.assertEqual(state["calls"], 2)
        self.assertIn("无usage流", self._visible_text(raw))
        self.assertNotIn("stream_error", raw)

    def test_transport_error_retries_when_no_output(self):
        state = {"calls": 0}

        def handler(request):
            state["calls"] += 1
            if state["calls"] == 1:
                raise httpx.ConnectError("connection reset")
            body = sse(content_chunk("恢复后的回复")) + DONE_LINE + b"\n\n"
            return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

        async def scenario():
            self._install_client(handler)
            return await self._collect(self._req())

        raw = asyncio.run(scenario())
        self.assertEqual(state["calls"], 2)
        self.assertIn("恢复后的回复", self._visible_text(raw))

    def test_first_visible_timeout_reports_and_gives_up(self):
        SERVER.API_FIRST_VISIBLE_TEXT_TIMEOUT_SECONDS = 0.2

        def handler(request):
            async def slow_lines():
                await asyncio.sleep(10)
                yield content_chunk("太晚了")
            return httpx.Response(200, content=slow_lines(), headers={"Content-Type": "text/event-stream"})

        async def scenario():
            self._install_client(handler)
            return await self._collect(self._req())

        started = time.monotonic()
        raw = asyncio.run(scenario())
        elapsed = time.monotonic() - started
        self.assertIn("no visible text", raw)
        self.assertLess(elapsed, 12)
        self.assertIn("stream_error", raw)

    def test_stop_cancels_promptly_and_client_survives(self):
        def handler(request):
            async def forever():
                for index in range(1000):
                    await asyncio.sleep(0.02)
                    yield content_chunk(f"段{index}。") + b"\n\n"
            return httpx.Response(200, content=forever(), headers={"Content-Type": "text/event-stream"})

        async def scenario():
            client = self._install_client(handler)
            task = asyncio.ensure_future(self._collect(self._req(temporary_chat=False)))
            await asyncio.sleep(0.3)
            started = time.monotonic()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.0, "generator cancellation should be immediate with async IO")
            interrupted = next(item for item in SERVER.recorder.saved if item["role"] == "ai")
            self.assertTrue(interrupted["metadata"]["stream_interrupted"])
            self.assertEqual(interrupted["metadata"]["mood_state"], SERVER.conversation_mood.neutral())
            # Client stays healthy for the next turn.
            def ok_handler(request):
                body = sse(content_chunk("下一轮OK")) + DONE_LINE + b"\n\n"
                return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})
            client._transport.handler = ok_handler
            raw = await self._collect(self._req())
            self.assertIn("下一轮OK", self._visible_text(raw))

        asyncio.run(scenario())


class UsageRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = str(Path(self.tmp.name))
        import saya_app.routers.usage as usage_router
        self.usage_router = usage_router
        from fastapi import FastAPI
        self.app = FastAPI()
        self.app.include_router(usage_router.router)
        for route in SERVER.app.routes:
            if getattr(route, "path", "") == "/api/llm/models":
                self.app.router.routes.append(route)
        self._saved_dir = usage_router.KNOWLEDGE_BASE_DIR
        usage_router.KNOWLEDGE_BASE_DIR = self.base
        api_usage.record_turn(self.base, "router-model", {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})

    def tearDown(self):
        self.usage_router.KNOWLEDGE_BASE_DIR = self._saved_dir
        self.tmp.cleanup()

    def test_summary_endpoint(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app) as client:
            response = client.get("/api/usage/summary?days=7")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["per_model"]["router-model"]["total_tokens"], 7)

    def test_models_proxy(self):
        from fastapi.testclient import TestClient

        def fake_get(url, headers=None, timeout=None):
            class R:
                status_code = 200
                text = ""
                def json(self):
                    return {"data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-a"}]}
            return R()

        with patch.object(self.usage_router.requests, "get", fake_get):
            with TestClient(self.app) as client:
                response = client.post("/api/llm/models", json={"api_url": "http://x/v1", "api_key": "k"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["models"], ["model-a", "model-b"])


if __name__ == "__main__":
    unittest.main()
