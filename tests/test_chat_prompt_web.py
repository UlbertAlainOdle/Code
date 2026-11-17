"""Phase 4 regression tests: build_prompt off-mode equivalence + web injection.

These verify that adding the ``web_context`` parameter did not change the prompt
when it is empty (plan section 17: "off 模式下最终提示词应与改造前保持语义等价"),
and that web evidence lands in the contractually-correct position.

The server module has an interactive model-selection prompt at import time, so
we feed a model choice on stdin and capture the module via importlib.
"""

import io
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core_services import KnowledgeBase, MemoryManager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_server_module():
    """Import server5-22B----.py, bypassing its interactive model prompt."""
    import importlib.util

    # The module calls input() at top level for model selection. Feed "5"
    # (API mode) so no local GGUF model is loaded.
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_under_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()


class BuildPromptOffModeTests(unittest.TestCase):
    """Empty web_context must produce a byte-identical prompt to before."""

    def _common_args(self):
        return dict(
            user="hello",
            word_limit_msg="",
            mode="normal",
            checkin_context="",
            project_context="",
            project_id=None,
            style_settings={},
            role_mode="",
            context_window_tokens=8192,
        )

    def test_explicit_empty_web_context_equals_omitted(self):
        # Calling with web_context="" must equal calling without the kwarg.
        args = self._common_args()
        prompt_default = SERVER.build_prompt(**args)
        prompt_empty = SERVER.build_prompt(**args, web_context="")
        self.assertEqual(prompt_default, prompt_empty)

    def test_web_context_present_inserts_evidence(self):
        args = self._common_args()
        evidence = "[System: Web Research Evidence]\nuntrusted evidence here"
        prompt_without = SERVER.build_prompt(**args)
        prompt_with = SERVER.build_prompt(**args, web_context=evidence)
        # The evidence text must appear, and the prompt must be longer.
        self.assertIn("[System: Web Research Evidence]", prompt_with)
        self.assertNotIn("[System: Web Research Evidence]", prompt_without)
        self.assertGreater(len(prompt_with), len(prompt_without))

    def test_off_mode_has_no_web_marker(self):
        args = self._common_args()
        prompt = SERVER.build_prompt(**args)
        self.assertNotIn("Web Research Evidence", prompt)
        self.assertNotIn("Web Research Status", prompt)


class ApiMultimodalMessageTests(unittest.TestCase):
    def test_plain_content_accepts_timestamp_prefix(self):
        content = SERVER.prepend_api_message_text(
            SERVER.build_api_user_message_content("hello", []),
            "[09-13 18:00] ",
        )

        self.assertEqual(content, "[09-13 18:00] hello")

    def test_image_content_puts_timestamp_in_text_part(self):
        content = SERVER.prepend_api_message_text(
            SERVER.build_api_user_message_content(
                "describe this",
                [{
                    "type": "image",
                    "data_url": "data:image/png;base64,ZmFrZQ==",
                }],
            ),
            "[09-13 18:00] ",
        )

        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {
            "type": "text",
            "text": "[09-13 18:00] describe this",
        })
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,ZmFrZQ==")


class BudgetTests(unittest.TestCase):
    def test_zero_window_returns_zero(self):
        self.assertEqual(SERVER.compute_web_context_budget("x" * 100, 0, 100, True), 0)

    def test_local_hard_cap_applied(self):
        # Tiny base prompt + huge window -> capped at the local 7000-char limit.
        from saya_app.config import WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS
        budget = SERVER.compute_web_context_budget("x" * 30, 1000000, 100, True)
        self.assertEqual(budget, WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS)

    def test_api_hard_cap_applied(self):
        from saya_app.config import WEB_CONTEXT_API_HARD_LIMIT_CHARS
        budget = SERVER.compute_web_context_budget("x" * 30, 1000000, 100, False)
        self.assertEqual(budget, WEB_CONTEXT_API_HARD_LIMIT_CHARS)

    def test_output_reserve_reduces_budget(self):
        small_out = SERVER.compute_web_context_budget("x" * 30, 1000000, 100, True)
        large_out = SERVER.compute_web_context_budget("x" * 30, 1000000, 5000, True)
        # Larger output reserve must not increase the budget.
        self.assertLessEqual(large_out, small_out)


class ChatRequestModelTests(unittest.TestCase):
    def test_default_web_fields_are_off_and_empty(self):
        req = SERVER.ChatWithMotion(user_input="hi")
        self.assertEqual(req.web_search_mode, "off")
        self.assertEqual(req.web_search_api_key, "")
        self.assertEqual(req.web_source_strategy, "official_first")
        self.assertEqual(req.web_safe_search, "moderate")

    def test_web_fields_accepted(self):
        req = SERVER.ChatWithMotion(
            user_input="hi", web_search_mode="force", web_search_api_key="KEY",
            web_search_count=16, web_final_sources=5,
            web_source_strategy="balanced", web_safe_search="strict",
        )
        self.assertEqual(req.web_search_mode, "force")
        self.assertEqual(req.web_search_api_key, "KEY")
        self.assertEqual(req.web_search_count, 16)
        self.assertEqual(req.web_final_sources, 5)
        self.assertEqual(req.web_source_strategy, "balanced")
        self.assertEqual(req.web_safe_search, "strict")


class ChatAgentStreamIntegrationTests(unittest.TestCase):
    def test_leaving_during_agent_keeps_the_user_turn(self):
        import saya_app.services.memory_v4 as memory_v4
        from saya_app.services.conversation import conversation_service

        class UnusedAgentStep:
            def __init__(self, *args, **kwargs):
                pass

            def __call__(self, context, capabilities):
                return {"final_answer": "unused"}

        async def start_then_disconnect(req):
            response = await SERVER.chat_stream(req)
            iterator = response.body_iterator
            await anext(iterator)
            await iterator.aclose()

        with tempfile.TemporaryDirectory() as root:
            manager = MemoryManager(str(Path(root) / "chats"))
            knowledge = KnowledgeBase(str(Path(root) / "kb"))
            session_id = manager.create_session()
            with (
                patch.object(SERVER, "memory_manager", manager),
                patch.object(SERVER, "kb_system", knowledge),
                patch.object(SERVER, "refresh_daily_activity_memory", lambda: None),
                patch.object(memory_v4, "OpenAICompatibleAgentStep", UnusedAgentStep),
                patch.object(conversation_service, "save_credentials", lambda **kwargs: None),
            ):
                asyncio.run(start_then_disconnect(SERVER.ChatWithMotion(
                    user_input="中途离开也应保存",
                    display_input="中途离开也应保存",
                    session_id=session_id,
                    model="api",
                    api_key="test-key",
                    api_url="https://example.invalid/v1",
                    api_model_name="test-model",
                    agent_mode=True,
                )))

            saved = manager.load_session_full(session_id, make_current=False)
            self.assertEqual([item["role"] for item in saved], ["user"])
            self.assertEqual(saved[0]["content"], "中途离开也应保存")

    def test_agent_memory_round_trip_streams_and_persists_reply(self):
        import saya_app.services.memory_v4 as memory_v4
        from saya_app.services.conversation import conversation_service

        class FakeOpenAIAgentStep:
            def __init__(self, *args, **kwargs):
                pass

            def __call__(self, context, capabilities):
                if any(item.get("role") == "tool" for item in context):
                    return {"final_answer": "[MOTION: giving_answer] Agent 集成回复"}
                return {
                    "tool_calls": [{
                        "id": "memory-1",
                        "name": "memory.query",
                        "arguments": {"query": "回答偏好"},
                    }],
                }

        async def collect_stream(req):
            response = await SERVER.chat_stream(req)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
            return "".join(chunks)

        with tempfile.TemporaryDirectory() as root:
            manager = MemoryManager(str(Path(root) / "chats"))
            knowledge = KnowledgeBase(str(Path(root) / "kb"))
            knowledge.update_settings({"memory_v4_agent_allow_private": True})
            session_id = manager.create_session()

            with (
                patch.object(SERVER, "memory_manager", manager),
                patch.object(SERVER, "kb_system", knowledge),
                patch.object(SERVER, "refresh_daily_activity_memory", lambda: None),
                patch.object(memory_v4, "OpenAICompatibleAgentStep", FakeOpenAIAgentStep),
                patch.object(conversation_service, "save_credentials", lambda **kwargs: None),
            ):
                stream = asyncio.run(collect_stream(SERVER.ChatWithMotion(
                    user_input="请根据记忆回答",
                    display_input="请根据记忆回答",
                    session_id=session_id,
                    model="api",
                    api_key="test-key",
                    api_url="https://example.invalid/v1",
                    api_model_name="test-model",
                    agent_mode=True,
                )))

            self.assertIn("event: agent_event", stream)
            self.assertIn("event: agent_result", stream)
            self.assertIn("event: text", stream)
            self.assertIn("Agent 集成回复", stream)
            saved = manager.load_session_full(session_id, make_current=False)
            self.assertEqual([item["role"] for item in saved], ["user", "ai"])
            self.assertEqual(saved[1]["content"], "Agent 集成回复")

    def test_api_without_visible_text_times_out_and_persists_error_reply(self):
        from saya_app.services.conversation import conversation_service

        captured_payload = {}

        import httpx

        def mock_handler(request):
            captured_payload.update(json.loads(request.content.decode("utf-8")))

            async def keep_alive_only():
                while True:
                    await asyncio.sleep(0.03)
                    yield b": keep-alive\n\n"

            return httpx.Response(
                200, content=keep_alive_only(), headers={"Content-Type": "text/event-stream"}
            )

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(mock_handler), base_url="https://api.deepseek.com"
        )

        async def collect_stream(req):
            response = await SERVER.chat_stream(req)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
            return "".join(chunks)

        with tempfile.TemporaryDirectory() as root:
            manager = MemoryManager(str(Path(root) / "chats"))
            knowledge = KnowledgeBase(str(Path(root) / "kb"))
            session_id = manager.create_session()
            with (
                patch.object(SERVER, "memory_manager", manager),
                patch.object(SERVER, "kb_system", knowledge),
                patch.object(SERVER, "refresh_daily_activity_memory", lambda: None),
                patch.object(SERVER, "_get_api_httpx_client", lambda: mock_client),
                patch.object(SERVER, "API_FIRST_VISIBLE_TEXT_TIMEOUT_SECONDS", 0.01),
                patch.object(conversation_service, "save_credentials", lambda **kwargs: None),
            ):
                stream = asyncio.run(collect_stream(SERVER.ChatWithMotion(
                    user_input="测试无正文超时",
                    display_input="测试无正文超时",
                    session_id=session_id,
                    model="api",
                    api_key="test-key",
                    api_url="https://api.deepseek.com",
                    api_model_name="deepseek-v4-flash",
                    thinking_effort="none",
                    recall_mode="off",
                )))

            self.assertEqual(captured_payload["thinking"], {"type": "disabled"})
            self.assertIn("event: text", stream)
            self.assertIn("no visible text", stream)
            saved = manager.load_session_full(session_id, make_current=False)
            self.assertEqual([item["role"] for item in saved], ["user", "ai"])
            self.assertIn("no visible text", saved[1]["content"])

    def test_server_wires_weather_and_time_into_agent_registry(self):
        with tempfile.TemporaryDirectory() as root:
            manager = MemoryManager(str(Path(root) / "chats"))
            knowledge = KnowledgeBase(str(Path(root) / "kb"))
            manager.time_events.record_backend_started("2026-09-15T08:00:00+08:00")

            def fake_weather(req):
                return {
                    "location": req.location,
                    "current": {"temperature": 21, "text_cn": "晴"},
                    "daily": [],
                    "summary_cn": f"{req.location}当前21°C。",
                }

            with (
                patch.object(SERVER, "memory_manager", manager),
                patch.object(SERVER, "kb_system", knowledge),
                patch.object(SERVER.integrations, "weather", fake_weather),
            ):
                SERVER.configure_agent_component_handlers()
                registry = knowledge._memory_v4_capability_registry()
                weather = registry.execute("weather.query", {"location": "上海"})
                timing = registry.execute("time.query", {"limit": 5})

            self.assertEqual(weather["status"], "success")
            self.assertEqual(weather["result"]["current"]["temperature"], 21)
            self.assertEqual(timing["status"], "success")
            self.assertEqual(timing["result"]["backend_started_at"], "2026-09-15T08:00:00+08:00")


class AutonomousMemoryQualityTests(unittest.TestCase):
    def test_chinese_candidate_requires_same_language_and_exact_user_evidence(self):
        prompt = SERVER._build_autonomous_memory_prompt(
            "我长期偏好简洁的中文回答。", "明白。", language="zh-CN",
        )
        self.assertIn('Return {"candidates":[]}', prompt)
        self.assertNotIn("Return []", prompt)
        user_text = "我长期偏好简洁的中文回答，并且不喜欢多余格式。"
        valid = json.dumps({"candidates": [{
            "title": "回答偏好",
            "content": "Doctor长期偏好简洁的中文回答。",
            "evidence": "我长期偏好简洁的中文回答",
            "category": "preference",
            "tags": ["简洁回答"],
            "confidence": 0.9,
            "importance_score": 0.7,
        }]}, ensure_ascii=False)
        parsed = SERVER._parse_autonomous_memory_candidates(
            valid,
            user_text=user_text,
            expected_language="zh-CN",
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["language"], "zh-CN")
        self.assertTrue(parsed[0]["evidence_hash"])

        wrong_language = json.dumps({"candidates": [{
            "title": "Answer preference",
            "content": "Doctor prefers concise Chinese answers.",
            "evidence": "我长期偏好简洁的中文回答",
            "category": "preference",
        }]}, ensure_ascii=False)
        self.assertEqual(SERVER._parse_autonomous_memory_candidates(
            wrong_language,
            user_text=user_text,
            expected_language="zh-CN",
        ), [])

    def test_rejects_unrelated_missing_or_assistant_only_evidence(self):
        user_text = "我长期偏好简洁的中文回答。"
        unrelated = json.dumps({"candidates": [{
            "title": "运动习惯",
            "content": "Doctor每周固定进行攀岩训练。",
            "evidence": "我长期偏好简洁的中文回答",
            "category": "preference",
        }]}, ensure_ascii=False)
        missing = json.dumps({"candidates": [{
            "title": "回答偏好",
            "content": "Doctor长期偏好简洁的中文回答。",
            "category": "preference",
        }]}, ensure_ascii=False)
        assistant_only = json.dumps({"candidates": [{
            "title": "居住地点",
            "content": "Doctor目前居住在上海。",
            "evidence": "你目前居住在上海",
            "category": "identity",
        }]}, ensure_ascii=False)
        self.assertEqual(SERVER._parse_autonomous_memory_candidates(
            unrelated, user_text=user_text, expected_language="zh-CN",
        ), [])
        self.assertEqual(SERVER._parse_autonomous_memory_candidates(
            missing, user_text=user_text, expected_language="zh-CN",
        ), [])
        self.assertEqual(SERVER._parse_autonomous_memory_candidates(
            assistant_only, user_text="今天天气怎么样？", expected_language="zh-CN",
        ), [])

        superficially_related = json.dumps({"candidates": [{
            "title": "天气部署",
            "content": "Saya的天气系统已经部署在上海。",
            "evidence": "我正在开发Saya的记忆系统",
            "category": "project",
        }]}, ensure_ascii=False)
        self.assertEqual(SERVER._parse_autonomous_memory_candidates(
            superficially_related,
            user_text="我正在开发Saya的记忆系统。",
            expected_language="zh-CN",
        ), [])

    def test_rejects_chinese_credentials_even_with_grounded_evidence(self):
        user_text = "请记住我的API密钥是sk-example-secret-value。"
        payload = json.dumps({"candidates": [{
            "title": "接口配置",
            "content": "Doctor的API密钥是sk-example-secret-value。",
            "evidence": "我的API密钥是sk-example-secret-value",
            "category": "project",
        }]}, ensure_ascii=False)
        self.assertEqual(SERVER._parse_autonomous_memory_candidates(
            payload, user_text=user_text, expected_language="zh-CN",
        ), [])

    def test_deepseek_autonomous_memory_uses_non_thinking_json_mode(self):
        captured = {}

        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": '{"candidates":[]}'}}]}

        def fake_post(*args, **kwargs):
            captured.update(kwargs.get("json") or {})
            return Response()

        request = SERVER.ChatWithMotion(
            user_input="test",
            model="api",
            api_key="test-key",
            api_url="https://api.deepseek.com",
            api_model_name="deepseek-v4-flash",
        )
        with patch.object(SERVER.requests, "post", fake_post):
            output = SERVER._autonomous_memory_api_request(
                "prompt", request, "deepseek-v4-flash",
            )
        self.assertEqual(output, '{"candidates":[]}')
        self.assertEqual(captured["thinking"], {"type": "disabled"})
        self.assertEqual(captured["response_format"], {"type": "json_object"})


class MemoryV4ChatWiringTests(unittest.TestCase):
    def test_v4_main_read_records_inclusion_without_claiming_verified_answer_use(self):
        source = (ROOT / "server5-22B----.py").read_text(encoding="utf-8")
        self.assertIn("runtime.memory_recall.get(\"version\") == \"v4\"", source)
        self.assertIn("kb_system.acknowledge_memory_v4_usage", source)
        self.assertIn('"event: memory_usage', source)
        self.assertIn('feedback="prompt_included_unverified"', source)
        self.assertIn("used=False", source)
        self.assertNotIn('feedback="chat_response"', source)


class WebResearchMetadataTests(unittest.TestCase):
    """Public source metadata contract (plan section 18)."""

    def test_public_source_dict_excludes_text_and_key(self):
        from saya_app.services.web_research.models import WebSource
        src = WebSource(
            id=1, title="t", url="https://a.com/p", domain="a.com",
            snippet="s", status="read", text="SECRET PAGE BODY",
        )
        public = src.to_public_dict()
        # Public schema fields only.
        self.assertEqual(
            set(public.keys()),
            {
                "id", "title", "url", "domain", "snippet", "published_at",
                "status", "source_type", "authority",
            },
        )
        self.assertNotIn("text", public)
        self.assertNotIn("SECRET PAGE BODY", json.dumps(public))

    def test_metadata_shape_matches_contract(self):
        # The exact dict shape written to ai_metadata["web_research"].
        from saya_app.services.web_research.models import ResearchBundle, WebSource
        bundle = ResearchBundle(
            query="q", performed=True,
            sources=[WebSource(id=1, title="t", url="https://a.com", domain="a.com",
                               snippet="s", status="read")],
        )
        meta = {
            "performed": bundle.performed,
            "mode": "auto",
            "query": bundle.query,
            "sources": [s.to_public_dict() for s in bundle.sources],
            "diagnostics": bundle.to_public_diagnostics(),
        }
        self.assertTrue(meta["performed"])
        self.assertEqual(meta["sources"][0]["id"], 1)
        self.assertNotIn("text", meta["sources"][0])
        self.assertIn("returned_sources", meta["diagnostics"])


if __name__ == "__main__":
    unittest.main()
