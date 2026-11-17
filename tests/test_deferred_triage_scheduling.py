"""Deferred memory triage: idle-window scheduling, demand yield, targeted persist.

Covers the 2026-10-02 change: local-model triage no longer holds llama_lock
at the tail of a reply stream (a queued follow-up turn waited up to a full
8-26s inference behind it). It now runs through a deferred queue that
- only takes the model when no user-facing generation demands it
  (user_llama_lock counts demand),
- streams and aborts mid-inference the moment demand reappears,
- persists the confirm payload onto the exact message via
  chat_runtime.request_id, so a payload written after later messages were
  appended still lands on its own reply and merges with an explicit-save box.
"""

import asyncio
import io
import json
import math
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_services import KnowledgeBase, MemoryManager  # noqa: E402


def load_server_module():
    import importlib.util

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_deferred_triage_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()

USER_TEXT = "以后注意一下，我喜欢简洁但保留必要推理的回答，太长的解释我一般不看。"
ASSISTANT_TEXT = "好的，我会按照这个偏好来调整以后的回答方式。"
TRIAGE_TEXT = json.dumps({
    "candidates": [
        {
            "title": "回答风格偏好",
            "content": "Doctor喜欢简洁但保留必要推理的回答。",
            "evidence": "我喜欢简洁但保留必要推理的回答",
            "category": "preference",
            "tags": ["style"],
            "confidence": 0.9,
            "importance_score": 0.8,
        }
    ]
}, ensure_ascii=False)


class FakeStreamingModel:
    """llama-cpp stand-in: non-streaming dict or a chunk generator."""

    def __init__(self, text, delay=0.0, on_chunk=None):
        self.text = text
        self.delay = delay
        self.on_chunk = on_chunk
        self.calls = []

    def create_chat_completion(self, messages=None, max_tokens=None, temperature=None, stream=False):
        self.calls.append({"stream": bool(stream)})
        if not stream:
            return {"choices": [{"message": {"content": self.text}}]}

        def gen():
            step = max(1, math.ceil(len(self.text) / 6))
            for index in range(0, len(self.text), step):
                if self.delay:
                    time.sleep(self.delay)
                yield {"choices": [{"delta": {"content": self.text[index:index + step]}}]}
                if self.on_chunk:
                    self.on_chunk()

        return gen()


def make_job(**overrides):
    job = {
        "request_id": "req-1",
        "session_id": "session-deferred-1",
        "user_text": USER_TEXT,
        "assistant_text": ASSISTANT_TEXT,
        "project_id": None,
        "project_name": "",
        "model_tag": "qwen3-test",
        "enqueued_at": datetime.now().isoformat(timespec="seconds"),
        "aborts": 0,
    }
    job.update(overrides)
    return job


class ServerStateMixin(unittest.TestCase):
    """Bind a temp KB + message store onto the server module, restore after."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = str(Path(self.tmp.name))
        self.kb = KnowledgeBase(base)
        self.kb.settings["autonomous_memory_enabled"] = True
        self.manager = MemoryManager(base)
        self.session_id = self.manager.create_session(make_current=True)
        self.manager.save_message(
            "user", USER_TEXT,
            metadata={"chat_runtime": {"request_id": "req-1", "session_id": self.session_id}},
            session_id=self.session_id,
        )
        self.manager.save_message(
            "ai", ASSISTANT_TEXT,
            metadata={"chat_runtime": {"request_id": "req-1", "session_id": self.session_id}},
            session_id=self.session_id,
        )
        self._saved = {
            name: getattr(SERVER, name)
            for name in ("kb_system", "memory_manager", "llama", "_DEFERRED_TRIAGE_PATH")
        }
        SERVER.kb_system = self.kb
        SERVER.memory_manager = self.manager
        SERVER.llama = None
        SERVER._DEFERRED_TRIAGE_PATH = os.path.join(base, "deferred_triage.json")
        SERVER._deferred_triage_queue.clear()
        SERVER._deferred_triage_wake.clear()
        SERVER._user_generation_demand = 0

    def tearDown(self):
        task = SERVER._deferred_triage_task
        if task is not None and not task.done():
            task.cancel()
        SERVER._deferred_triage_task = None
        SERVER._deferred_triage_queue.clear()
        SERVER._deferred_triage_wake.clear()
        SERVER._user_generation_demand = 0
        for name, value in self._saved.items():
            setattr(SERVER, name, value)
        self.tmp.cleanup()

    def read_session_entries(self):
        path = self.manager._safe_session_path(self.session_id)
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


class LocalRequestStreamingTests(unittest.TestCase):
    def test_streaming_accumulates_chunks(self):
        model = FakeStreamingModel(TRIAGE_TEXT)
        out = SERVER._autonomous_memory_local_request("prompt", model, 512, lambda: False)
        self.assertEqual(out, TRIAGE_TEXT)
        self.assertTrue(model.calls and model.calls[0]["stream"] is True)

    def test_should_stop_aborts_between_chunks(self):
        model = FakeStreamingModel(TRIAGE_TEXT)
        with self.assertRaises(SERVER._TriageAborted):
            SERVER._autonomous_memory_local_request("prompt", model, 512, lambda: True)
        self.assertTrue(model.calls and model.calls[0]["stream"] is True)

    def test_without_should_stop_keeps_non_streaming_path(self):
        model = FakeStreamingModel(TRIAGE_TEXT)
        out = SERVER._autonomous_memory_local_request("prompt", model)
        self.assertEqual(out, TRIAGE_TEXT)
        self.assertEqual(model.calls, [{"stream": False}])


class UserLlamaLockTests(unittest.TestCase):
    def test_demand_counted_while_holding_and_wake_on_release(self):
        async def scenario():
            SERVER._deferred_triage_wake.clear()
            self.assertEqual(SERVER._user_generation_demand, 0)
            async with SERVER.user_llama_lock():
                self.assertEqual(SERVER._user_generation_demand, 1)
                self.assertTrue(SERVER.llama_lock.locked())
            self.assertEqual(SERVER._user_generation_demand, 0)
            self.assertFalse(SERVER.llama_lock.locked())
            self.assertTrue(SERVER._deferred_triage_wake.is_set())

        asyncio.run(scenario())

    def test_demand_counts_while_queued_behind_a_generation(self):
        async def scenario():
            async def queued_turn():
                async with SERVER.user_llama_lock():
                    return "done"

            async with SERVER.user_llama_lock():
                task = asyncio.ensure_future(queued_turn())
                await asyncio.sleep(0.05)
                self.assertEqual(SERVER._user_generation_demand, 2)
                self.assertFalse(task.done())
            self.assertEqual(await task, "done")
            self.assertEqual(SERVER._user_generation_demand, 0)

        asyncio.run(scenario())


class DeferredEntryTests(ServerStateMixin):
    def _entry(self, job=None, with_future=True):
        job = job or make_job(session_id=self.session_id)
        if not with_future:
            return {"job": job, "future": None}
        future = asyncio.get_running_loop().create_future()
        return {"job": job, "future": future}

    def test_idle_run_completes_persists_and_resolves(self):
        async def scenario():
            SERVER.llama = FakeStreamingModel(TRIAGE_TEXT)
            entry = self._entry()
            consumed = await SERVER._run_deferred_triage_entry(entry)
            self.assertTrue(consumed)
            self.assertEqual(SERVER._deferred_triage_queue, [])
            result = entry["future"].result()
            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(len(self.kb.memory_candidates), 1)
            payload = result["payload"]
            self.assertEqual(len(payload["candidates"]), 1)

            entries = self.read_session_entries()
            ai_entry = next(e for e in entries if e.get("role") == "ai")
            self.assertEqual(ai_entry.get("memory_confirm"), payload)

        asyncio.run(scenario())

    def test_parks_when_generation_demanded_then_runs_after(self):
        async def scenario():
            SERVER.llama = FakeStreamingModel(TRIAGE_TEXT)
            entry = self._entry()
            stream_future = entry["future"]
            SERVER._user_generation_demand = 1
            self.assertFalse(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(len(SERVER._deferred_triage_queue), 0)  # direct call: entry not in queue
            # The stream's own future resolves None immediately...
            self.assertTrue(stream_future.done())
            self.assertIsNone(stream_future.result())

            SERVER._user_generation_demand = 0
            SERVER._deferred_triage_queue.append(entry)
            self.assertTrue(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(len(SERVER._deferred_triage_queue), 0)
            # ...and the replacement future carries the background result.
            refreshed = entry["future"]
            self.assertIsNot(refreshed, stream_future)
            self.assertTrue(refreshed.done())
            self.assertEqual(len(refreshed.result()["candidates"]), 1)

        asyncio.run(scenario())

    def test_mid_inference_demand_aborts_and_requeues(self):
        def raise_demand():
            SERVER._user_generation_demand = 1

        async def scenario():
            SERVER.llama = FakeStreamingModel(TRIAGE_TEXT, on_chunk=raise_demand)
            entry = self._entry()
            SERVER._deferred_triage_queue.append(entry)
            self.assertFalse(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(len(SERVER._deferred_triage_queue), 1)
            self.assertEqual(entry["job"]["aborts"], 1)
            self.assertFalse(entry["job"].get("started"))
            self.assertEqual(len(self.kb.memory_candidates), 0)  # nothing half-saved

            SERVER._user_generation_demand = 0
            SERVER.llama = FakeStreamingModel(TRIAGE_TEXT)
            self.assertTrue(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(len(entry["future"].result()["candidates"]), 1)

        asyncio.run(scenario())

    def test_abort_cap_drops_job(self):
        async def scenario():
            entry = self._entry(make_job(session_id=self.session_id, aborts=SERVER._DEFERRED_TRIAGE_MAX_ABORTS - 1))

            def raise_demand():
                SERVER._user_generation_demand = 1

            SERVER.llama = FakeStreamingModel(TRIAGE_TEXT, on_chunk=raise_demand)
            SERVER._deferred_triage_queue.append(entry)
            self.assertTrue(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(SERVER._deferred_triage_queue, [])
            self.assertTrue(entry["future"].done())
            self.assertIsNone(entry["future"].result())

        asyncio.run(scenario())

    def test_expired_job_dropped_without_model(self):
        async def scenario():
            stale = make_job(
                session_id=self.session_id,
                enqueued_at=(datetime.now() - timedelta(days=2)).isoformat(timespec="seconds"),
            )
            entry = self._entry(stale)
            SERVER._deferred_triage_queue.append(entry)
            self.assertTrue(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(SERVER._deferred_triage_queue, [])
            self.assertIsNone(SERVER.llama)  # never touched the model

        asyncio.run(scenario())

    def test_disabled_flag_drops_job(self):
        async def scenario():
            self.kb.settings["autonomous_memory_enabled"] = False
            entry = self._entry()
            SERVER._deferred_triage_queue.append(entry)
            self.assertTrue(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(SERVER._deferred_triage_queue, [])
            self.assertEqual(len(self.kb.memory_candidates), 0)

        asyncio.run(scenario())

    def test_missing_model_parks_recent_job(self):
        async def scenario():
            entry = self._entry()
            SERVER._deferred_triage_queue.append(entry)
            self.assertFalse(await SERVER._run_deferred_triage_entry(entry))
            self.assertEqual(len(SERVER._deferred_triage_queue), 1)

        asyncio.run(scenario())


class StreamAwaitTests(ServerStateMixin):
    def test_completed_triage_returns_result_to_stream(self):
        async def scenario():
            SERVER.llama = FakeStreamingModel(TRIAGE_TEXT)
            result = await SERVER.await_deferred_triage_for_stream(make_job(session_id=self.session_id))
            self.assertIsNotNone(result)
            self.assertEqual(len(result["candidates"]), 1)
            entries = self.read_session_entries()
            ai_entry = next(e for e in entries if e.get("role") == "ai")
            self.assertEqual(ai_entry.get("memory_confirm"), result["payload"])

        asyncio.run(scenario())

    def test_parked_triage_returns_none_and_defers_to_background(self):
        async def scenario():
            SERVER.llama = FakeStreamingModel(TRIAGE_TEXT)
            SERVER._user_generation_demand = 1
            started = time.monotonic()
            result = await SERVER.await_deferred_triage_for_stream(make_job(session_id=self.session_id))
            elapsed = time.monotonic() - started
            self.assertIsNone(result)
            self.assertLess(elapsed, SERVER._DEFERRED_TRIAGE_PARK_WINDOW_SECONDS + 1.0)
            self.assertEqual(len(SERVER._deferred_triage_queue), 1)

            # The parked job still completes in the background drainer.
            SERVER._user_generation_demand = 0
            SERVER._deferred_triage_wake.set()
            for _ in range(100):
                if not SERVER._deferred_triage_queue:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(SERVER._deferred_triage_queue, [])
            entries = self.read_session_entries()
            ai_entry = next(e for e in entries if e.get("role") == "ai")
            self.assertEqual(len(ai_entry["memory_confirm"]["candidates"]), 1)

        asyncio.run(scenario())

    def test_slow_triage_persists_after_stream_window(self):
        async def scenario():
            original_window = SERVER._DEFERRED_TRIAGE_STREAM_WINDOW_SECONDS
            SERVER._DEFERRED_TRIAGE_STREAM_WINDOW_SECONDS = 0.2
            try:
                SERVER.llama = FakeStreamingModel(TRIAGE_TEXT, delay=0.5)
                result = await SERVER.await_deferred_triage_for_stream(make_job(session_id=self.session_id))
                self.assertIsNone(result)  # stream gave up waiting, job continues
                for _ in range(200):
                    if not SERVER._deferred_triage_queue:
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual(SERVER._deferred_triage_queue, [])
                entries = self.read_session_entries()
                ai_entry = next(e for e in entries if e.get("role") == "ai")
                self.assertEqual(len(ai_entry["memory_confirm"]["candidates"]), 1)
            finally:
                SERVER._DEFERRED_TRIAGE_STREAM_WINDOW_SECONDS = original_window

        asyncio.run(scenario())


class QueuePersistenceTests(ServerStateMixin):
    def test_queue_round_trip_keeps_pending_jobs_and_drops_done(self):
        SERVER._deferred_triage_queue.append({"job": make_job(session_id="s-keep"), "future": None})
        SERVER._deferred_triage_queue.append({
            "job": make_job(request_id="req-old", session_id="s-stale",
                            enqueued_at=(datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")),
            "future": None,
        })
        SERVER._deferred_triage_jobs_save()

        SERVER._deferred_triage_queue.clear()
        SERVER._deferred_triage_jobs_load()
        self.assertEqual(len(SERVER._deferred_triage_queue), 1)
        self.assertEqual(SERVER._deferred_triage_queue[0]["job"]["request_id"], "req-1")

    def test_malformed_queue_file_is_ignored(self):
        Path(SERVER._DEFERRED_TRIAGE_PATH).write_text("{not json", encoding="utf-8")
        SERVER._deferred_triage_jobs_load()
        self.assertEqual(SERVER._deferred_triage_queue, [])


class AbortPropagationTests(ServerStateMixin):
    def test_maybe_create_rethrows_triage_abort(self):
        async def aborting_invoke(prompt):
            raise SERVER._TriageAborted()

        with patch.dict(os.environ, {"API_KEY": ""}):
            with self.assertRaises(SERVER._TriageAborted):
                asyncio.run(SERVER.maybe_create_autonomous_memory_candidates(
                    _ReqStub(), USER_TEXT, ASSISTANT_TEXT,
                    session_id=self.session_id,
                    local_model_invoke=aborting_invoke,
                ))
        self.assertEqual(len(self.kb.memory_candidates), 0)


class _ReqStub:
    api_key = ""
    api_model_name = ""
    autonomous_memory_model_name = ""


class RequestIDFieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = MemoryManager(str(Path(self.tmp.name)))
        self.session_id = self.manager.create_session(make_current=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, role, content, request_id):
        self.manager.save_message(
            role, content,
            metadata={"chat_runtime": {"request_id": request_id, "session_id": self.session_id}},
            session_id=self.session_id,
        )

    def _entries(self):
        path = self.manager._safe_session_path(self.session_id)
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_attaches_to_exact_message_even_with_newer_tail(self):
        self._save("user", "q1", "req-a")
        self._save("ai", "reply a", "req-a")
        self._save("user", "q2", "req-b")  # appended after the reply
        self._save("ai", "reply b", "req-b")
        ok = self.manager.update_message_field_by_request_id(
            self.session_id, "req-a", "memory_confirm", {"candidates": [{"id": "c-1"}]}, "ai"
        )
        self.assertTrue(ok)
        entries = self._entries()
        self.assertEqual(entries[1].get("memory_confirm"), {"candidates": [{"id": "c-1"}]})
        self.assertNotIn("memory_confirm", entries[3])

    def test_merge_payload_combines_lists_and_dedupes(self):
        self._save("ai", "reply", "req-m")
        first = {"explicit": [{"memory_id": "m-1", "content": "a"}]}
        second = {"explicit": [{"memory_id": "m-1", "content": "a"}, {"memory_id": "m-2", "content": "b"}],
                  "candidates": [{"id": "c-1"}]}
        self.assertTrue(self.manager.update_message_field_by_request_id(
            self.session_id, "req-m", "memory_confirm", first, "ai", True))
        self.assertTrue(self.manager.update_message_field_by_request_id(
            self.session_id, "req-m", "memory_confirm", second, "ai", True))
        merged = self._entries()[0]["memory_confirm"]
        self.assertEqual([e["memory_id"] for e in merged["explicit"]], ["m-1", "m-2"])
        self.assertEqual([c["id"] for c in merged["candidates"]], ["c-1"])

    def test_role_guard_rejects_user_message(self):
        self._save("user", "q", "req-u")
        self.assertFalse(self.manager.update_message_field_by_request_id(
            self.session_id, "req-u", "memory_confirm", {"x": 1}, "ai"))

    def test_unknown_request_id_returns_false(self):
        self._save("ai", "reply", "req-x")
        self.assertFalse(self.manager.update_message_field_by_request_id(
            self.session_id, "req-missing", "memory_confirm", {"x": 1}, "ai"))

    def test_blank_request_id_returns_false(self):
        self._save("ai", "reply", "req-y")
        self.assertFalse(self.manager.update_message_field_by_request_id(
            self.session_id, "", "memory_confirm", {"x": 1}, "ai"))


if __name__ == "__main__":
    unittest.main()
