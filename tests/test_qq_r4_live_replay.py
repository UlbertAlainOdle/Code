"""Reproduce the two observed R4 regressions through the QQ generator."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from r4_web_lab import web_lab
from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
from saya_app.services.qq_bridge import QQMessage, QQSettings
from saya_app.services.qq_chat import QQChatResponder
from scripts.eval_qq_recall_r4 import seed
from test_qq_chat_runtime import server
from test_recall_reference import history


async def qq_turn(server, lab, ids, question, *, agent):
    session = 'session_qq_r4_replay.jsonl'
    for row in history(ids):
        lab.manager.save_message('ai', '棠木计划 HISTORY_CANARY', session_id=session,
                                 metadata={'memory_recall': row['memory_recall'],
                                           'memory_use_epoch': lab.knowledge.memory_use.snapshot(),
                                           'memory_history_floor': lab.knowledge.memory_use.history_floor()})
    message = QQMessage('r4:incoming', '10000:private:20000', '10000', '20000', '',
                        '101', question, True)
    message.history_times = [{'role': 'assistant', 'content': 'CLOCK_HINT_CANARY',
                              'time': '2025-11-05 17:00:00'}]

    async def generate(req, *, channel_turn):
        channel_turn['event_context'] = {'cards': [{'facts': [{'text': '棠木计划 EPISODE_CANARY'}]}]}
        channel_turn['context_link'] = {'session_id': 'session_web_old.jsonl', 'source_version': '1',
            'linked': 0, 'message_range': [], 'source_time_range': [], 'turn_count': 1,
            'turns': [{'role': 'user', 'content': '棠木计划 LINKED_CANARY'}]}
        return await server._chat_stream_single(req, channel_turn=channel_turn)

    responder = QQChatResponder(QQSettings(model='api', owner_ids={'20000'}, shared_memory=True,
        memory_write_enabled=True, agent_mode=agent, web_search_mode='off', graph_recall_mode='deep'),
        server.ChatWithMotion, generate, lambda: 'api', lambda: lab.credentials)
    answer = await responder(message, [], session)
    entries = lab.manager.load_session_full(session, make_current=False)
    assert any('HISTORY_CANARY' in row['content'] for row in entries)
    return answer, entries[-1]['memory_recall']


def test_resolved_qq_agent_return_limits_prompt_and_later_tool_reads(server, tmp_path):
    kb, ids = seed(tmp_path)
    kb.update_settings({'memory_v4_agent_allow_private': True})
    posted = []

    def post(url, **kwargs):
        body = kwargs['json']
        posted.append(body)
        tools = [item['function']['name'] for item in body['tools']]
        if len(posted) < 3:
            prefix = 'memory_query_' if len(posted) == 1 else 'memory_read_'
            arguments = {'query': '最近'} if len(posted) == 1 else {'ids': [ids['reason'], ids['no_reason']]}
            output = {'content': '', 'tool_calls': [{'id': 'call-' + str(len(posted)), 'type': 'function',
                'function': {'name': next(name for name in tools if name.startswith(prefix)),
                             'arguments': json.dumps(arguments)}}]}
        else:
            output = {'content': '青岚计划的记录说，海上作业经常断网，所以选择离线处理。'}
        return SimpleNamespace(status_code=200, json=lambda: {'choices': [{'message': output}]})

    async def run():
        with web_lab(server, tmp_path/'lab', kb) as lab:
            with patch('saya_app.services.memory_v4.OpenAICompatibleAgentStep',
                       side_effect=lambda url, key, settings: OpenAICompatibleAgentStep(url, key, settings, request_post=post)):
                answer, audit = await qq_turn(server, lab, ids, '回到那个海上作业经常断网的计划，我为什么选择离线？', agent=True)
        assert len(posted) == 3 and '青岚计划' in answer, (answer, audit)
        assert audit['history_selection'] == 'resolved_return_reference'
        assert {row['id'] for row in audit['selected']} == {ids['choice'], ids['reason']}
        for request in posted:
            prompt = json.dumps(request['messages'], ensure_ascii=False)
            assert '青岚计划' in prompt and '棠木计划' not in prompt
            assert all(marker not in prompt for marker in ('HISTORY_CANARY', 'CLOCK_HINT_CANARY', 'EPISODE_CANARY', 'LINKED_CANARY'))
    asyncio.run(run())


def test_qq_recall_command_keeps_focused_graph_and_never_proposes_a_save(server, tmp_path):
    kb, ids = seed(tmp_path)
    kb.update_settings({'autonomous_memory_enabled': True})
    posted = []
    triage = AsyncMock(return_value=[])

    def post(request):
        body = json.loads(request.content)
        posted.append(body)
        if not body['stream']:
            return httpx.Response(200, json={'choices': [{'message': {'content': '记录只给出断网理由。'}}]})
        response = {'choices': [{'delta': {'content': '目前的记录没有明确排除成本或性能因素。'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(response, ensure_ascii=False) + '\n\ndata: [DONE]\n\n')

    async def run():
        with web_lab(server, tmp_path/'lab', kb) as lab:
            async with httpx.AsyncClient(transport=httpx.MockTransport(post)) as client:
                with (patch.object(server, '_get_api_httpx_client', return_value=client),
                      patch.object(server, 'maybe_create_autonomous_memory_candidates', triage),
                      patch.object(kb, '_expand_graph_memories', return_value=(
                          [kb._find_memory(ids['no_reason'])], {'status': 'expanded', 'expanded_count': 1}))):
                    answer, audit = await qq_turn(server, lab, ids,
                        '/recall 我之前在青岚计划的记录有没有明确排除成本或性能？', agent=True)
        assert '没有明确排除' in answer
        assert audit['history_selection'] == 'explicit_focused_recall'
        assert audit['graph']['included_count'] == 0
        assert {row['id'] for row in audit['selected']} == {ids['choice'], ids['reason']}
        assert [request['stream'] for request in posted] == [False, True]
        for request in posted:
            prompt = json.dumps(request['messages'], ensure_ascii=False)
            assert '棠木计划' not in prompt and '海上作业经常断网' in prompt
            assert all(marker not in prompt for marker in ('HISTORY_CANARY', 'CLOCK_HINT_CANARY', 'EPISODE_CANARY', 'LINKED_CANARY'))
        triage.assert_not_awaited()
    asyncio.run(run())
