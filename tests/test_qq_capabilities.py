import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.responses import StreamingResponse
import pytest

from core_services import KnowledgeBase
from saya_app.services.qq_bridge import QQMessage, QQSettings
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.memory_v4 import AgentModelSettings, OpenAICompatibleAgentStep, MemoryItemV4, MemoryEvidence


def config(**changes):
    return QQSettings(**(dict(owner_ids={"20000"}, shared_memory=True, model="api", agent_mode=True,
        recall_mode="on", recall_memory_count=50, memory_token_budget=16000, graph_recall_mode="deep",
        thinking_effort="high", web_search_mode="auto") | changes))


def message(text="问题", **changes):
    return replace(QQMessage("key", "scope", "10000", "20000", "", "1", text, True), **changes)


def responder(settings, capture, events=None, actions=None):
    async def single(request, channel_turn):
        capture.append((request, channel_turn))
        async def stream():
            for event, value in events or [("answer_complete", {"text": "回答"})]:
                yield f"event: {event}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
        return StreamingResponse(stream())
    return QQChatResponder(settings, SimpleNamespace, single, lambda: "api", lambda: {
        "configured": True, "api_key": "fake-model-secret", "api_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "brave_search_api_key": "fake-search-secret"}, actions)


def test_owner_controls_reach_same_chat_request_and_no_tools_for_other_scopes():
    async def run():
        captured = []
        reply = responder(config(), captured)
        await reply(message(), [], "session_qq_test.jsonl")
        req, turn = captured[-1]
        assert req.agent_mode and req.thinking_effort == "high" and req.web_search_mode == "auto"
        assert req.recall_memory_count == 50 and req.memory_token_budget == 16000
        assert req.graph_recall_mode == "deep" and turn["memory_write_enabled"]
        assert turn["memory_query_options"]["max_items"] == 50
        for incoming in [message(group_id="90000"), message(user_id="30000")]:
            await reply(incoming, [], "ignored-session")
            req, turn = captured[-1]
            assert req.temporary_chat and not req.agent_mode and req.recall_mode == "off"
            assert req.web_search_mode == ("auto" if incoming.user_id=="20000" else "off")
            assert bool(req.web_search_api_key) is (incoming.user_id=="20000")
            assert req.recall_memory_count == 0 and not turn["memory_write_enabled"]
    asyncio.run(run())


@pytest.mark.parametrize('text,mode,expect_agent', [
    ('今天有什么大新闻', 'auto', False),
    ('今天的天气怎么样', 'auto', False),
    ('查询最新汇率', 'auto', False),
    ('帮我查一下DeepSeek官方文档', 'auto', False),
    ('读一下 https://example.org/article', 'auto', False),
    ('陪我聊聊天，我今天很开心', 'auto', True),
    ('还记得我喜欢喝什么吗', 'auto', True),
    ('你好，介绍一下你自己', 'auto', True),
    ('今天有什么大新闻', 'off', True),
    ('你好', 'force', False),
])
def test_automatic_research_uses_web_policy_without_disabling_ordinary_agent_turns(text, mode, expect_agent):
    captured = []
    asyncio.run(responder(config(web_search_mode=mode), captured)(message(text), [], 'session_qq_fixture.jsonl'))
    request, turn = captured[-1]
    assert request.agent_mode is expect_agent
    assert request.web_search_mode == turn['web_search_mode'] == mode
    assert request.recall_mode == 'on' and request.memory_write_enabled
    assert not request.temporary_chat


@pytest.mark.parametrize('identity', [{'group_id': '90000'}, {'user_id': '30000'}])
def test_automatic_news_search_keeps_existing_scope_permissions(identity):
    captured = []
    asyncio.run(responder(config(), captured)(message('今天有什么大新闻', **identity), [], 'unused'))
    request, turn = captured[-1]
    assert request.web_search_mode == ('auto' if identity.get('group_id') else 'off')
    assert bool(request.web_search_api_key) is bool(identity.get('group_id'))
    assert not request.agent_mode and request.temporary_chat and not turn['shared']
    assert request.recall_mode=='off' and not request.memory_write_enabled


@pytest.mark.parametrize("text,mode,web,user", [
    ("/recall 乌鸦", "on", "auto", "乌鸦"),
    ("/search 今日新闻", "on", "force", "今日新闻"),
    ("/remember 我喜欢乌龙茶", "on", "auto", "请记住：我喜欢乌龙茶"),
])
def test_explicit_operations_use_deterministic_paths(text, mode, web, user):
    async def run():
        captured = []
        await responder(config(), captured)(message(text), [], "session_qq_test.jsonl")
        request, _ = captured[-1]
        assert not request.agent_mode and request.recall_mode == mode
        assert request.web_search_mode == web and request.user_input == user
    asyncio.run(run())


def test_write_switch_and_candidate_permissions():
    async def run():
        captured, actions = [], []
        callback = lambda command, argument: actions.append((command, argument)) or "候选处理成功"
        read_only = responder(config(memory_write_enabled=False), captured, actions=callback)
        assert "已关闭" in await read_only(message("/remember 爱喝茶"), [], "s")
        assert "已关闭" in await read_only(message("/memory-save candidate-1"), [], "s")
        assert not actions and not captured
        normal = responder(config(), captured, actions=callback)
        assert "完整候选预览服务暂不可用" in await normal(message("/memory-save candidate-1"), [], "s")
        assert not actions
        assert "仅供" in await normal(message("/memories", group_id="90000"), [], "s")
        assert "仅供" in await normal(message("/search 新闻", user_id="30000"), [], "s")
    asyncio.run(run())


def test_actual_receipts_and_missing_save_confirmation_remain_visible_while_sources_are_hidden():
    async def run():
        events = [("web_sources", {"sources": [{"title": "来源", "url": "https://example.org/evidence"}]}),
            ("web_warning", {"message": "部分网页无法读取"}),
            ("answer_complete", {"text": "回答"}),
            ("memory_candidates", {"explicit": [{"id": "m1", "title": "乌龙茶偏好"}],
                "candidates": [{"id": "c1", "title": "候选"}]})]
        answer = await responder(config(), [], events)(message("/remember 乌龙茶"), [], "s")
        assert "https://example.org/evidence" not in answer and "部分网页无法读取" in answer
        assert "已保存长期记忆：乌龙茶偏好" in answer and "/memory-save c1" in answer
        ordinary = await responder(config(), [], events)(message("聊聊乌龙茶"), [], "s")
        assert "已保存长期记忆：乌龙茶偏好" in ordinary and "待确认记忆：候选" in ordinary
        assert '/memory-save' not in ordinary and 'c1' not in ordinary
        missing = await responder(config(), [])(message("/remember 乌龙茶"), [], "s")
        assert "未收到长期记忆写入确认" in missing
    asyncio.run(run())


@pytest.mark.parametrize('mode',['chat','legacy'])
def test_long_projected_result_keeps_receipts_and_chat_can_continue(mode):
    async def run():
        captured=[]
        events=[('answer_complete',{'text':'完整正文。'*1600}),
            ('memory_candidates',{'explicit':[{'id':'m1','title':'真实保存回执'}]})]
        answer=await responder(config(delivery_mode=mode),captured,events)(message('/remember 内容'),[],'s')
        assert len(captured)==1 and answer.count('已保存长期记忆：真实保存回执')==1
        if mode=='chat':
            assert answer.body=='完整正文。'*1600
            assert answer.notes==('已保存长期记忆：真实保存回执',)
            assert '截断' not in answer
        else:
            assert len(answer)<=6000 and '已截断' in answer
    asyncio.run(run())


def test_deepseek_tools_replay_reasoning_privately_and_honor_effort():
    posted = []
    def post(url, **kwargs):
        posted.append(kwargs["json"])
        if len(posted) == 1:
            query_name = kwargs["json"]["tools"][0]["function"]["name"]
            msg = {"content": "", "reasoning_content": "PRIVATE_REASONING_MARKER", "tool_calls": [
                {"id": "call1", "type": "function", "function": {"name": query_name, "arguments": '{"query":"茶"}'}}]}
        else:
            msg = {"content": "查询完成", "reasoning_content": "PRIVATE_FINAL_REASONING"}
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": msg}]})
    adapter = OpenAICompatibleAgentStep("https://api.deepseek.com", "fake-key",
        AgentModelSettings("deepseek-flash", thinking_effort="high"), request_post=post)
    capabilities = [{"name": "memory.query", "description": "search", "input_schema": {"type": "object"}}]
    first = adapter([{"role": "user", "content": "记忆"}], capabilities)
    assert first["tool_calls"][0]["name"] == "memory.query"
    assert "PRIVATE_REASONING" not in json.dumps(first)
    second = adapter([{"role": "user", "content": "记忆"}, {"role": "model", "tool_calls": first["tool_calls"]},
        {"role": "tool", "name": "memory.query", "tool_call_id": "call1", "content": "茶"}], capabilities)
    assert second == {"final_answer": "查询完成"}
    assert posted[1]["messages"][2]["reasoning_content"] == "PRIVATE_REASONING_MARKER"
    assert all(p["thinking"] == {"type": "enabled"} and p["reasoning_effort"] == "high" for p in posted)


def test_wider_memory_request_uses_real_retrieval_budget(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    for number in range(60):
        kb.memory_v4_store.upsert_memory(MemoryItemV4(id=f"memory-{number}", predicate=f"fact_{number}",
            canonical_text=f"WIDE_MEMORY_TEST 偏好条目 {number}，包括详细说明与对应经历。", authority="user_confirmed"),
            evidence=[MemoryEvidence(id=f"evidence-{number}", memory_id=f"memory-{number}", source_type="chat", source_id="test", quote="测试证据")])
    result = kb.build_memory_v4_context_with_audit("WIDE_MEMORY_TEST", depth="deep", memory_count=50, token_budget=16000)
    assert result["status"] == "ready"
    assert result["audit"]["selected_count"] > 8
    with patch.object(kb, "query_memory_v4", return_value={"status": "ready", "items": []}) as query:
        registry = kb._memory_v4_capability_registry(allow_legacy_memory=True,
            memory_query_options={"max_items": 50, "max_candidates": 200, "token_budget": 16000, "depth": "deep"})
        registry.get("memory.query").handler({"query": "茶"})
        assert query.call_args.kwargs["max_items"] == 50 and query.call_args.kwargs["token_budget"] == 16000
