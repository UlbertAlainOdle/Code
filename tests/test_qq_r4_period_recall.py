"""Period and version selection through the real memory store and prompt path."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_recall_contract import named_period
from saya_app.services.memory_recall_reference import inspect
from r4_web_lab import web_lab
from test_qq_chat_runtime import server


NOW = '渡杉计划现在采用哪一种标记？'
PAST = '我想核对渡杉计划 2025 年的做法和理由，记录里能找到什么？'
PREVIOUS = '渡杉计划改成白色之前为什么用上一版标记？'


@pytest.fixture
def records(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'knowledge_base'))
    kb.update_settings({'memory_v4_read_enabled': True, 'memory_v4_write_enabled': True,
                        'embedding_enabled': False, 'memory_core_limit': 0})
    texts = {
        'past': '渡杉计划在 2025 年用蓝色标记，因为当年只有蓝色标签可用。',
        'current': '渡杉计划现在用黄色标记。',
        'near_name': '渡松计划在 2025 年用灰色标记，因为标签需要防潮。',
    }
    ids = {key: kb.capture_explicit_memory('记住：这是虚构的 R4 验收材料。' + text)['id']
           for key, text in texts.items()}
    return kb, ids


def test_period_selection_survives_graph_expansion_and_current_record_edit(records):
    kb, ids = records

    def read(question, expected):
        # The other period and a near name both arrive through the graph.
        distractors = [kb._find_memory(mid) for key, mid in ids.items() if key not in expected]
        with patch.object(kb, '_expand_graph_memories', return_value=(distractors, {'status': 'expanded'})):
            result = kb.build_memory_v4_context_with_audit(question, depth='deep', graph_mode='deep')
        assert {row['id'] for row in result['audit']['selected']} == {ids[key] for key in expected}
        return result['context']

    assert '黄色标记' in read(NOW, {'current'})
    assert '只有蓝色标签' in read('回想 2025 年那批标签，当时渡杉计划为什么用蓝色？', {'past'})
    assert '只有蓝色标签' in read(PAST, {'past'})
    assert kb.update_memory(ids['current'], {'content': '这是虚构的 R4 验收材料。渡杉计划现在用白色标记。'})
    current = read(NOW, {'current'})
    assert '白色标记' in current and '黄色标记' not in current and '蓝色标记' not in current
    assert '只有蓝色标签' in read(PAST, {'past'})
    previous = read(PREVIOUS, set())
    assert all(color not in previous for color in ('蓝色标记', '黄色标记', '白色标记'))


def test_agent_period_stays_bound_when_tool_query_changes_and_ids_are_read(records):
    kb, ids = records
    tools = kb._memory_v4_capability_registry(memory_query_options={'question_text': PAST})
    query = tools.get('memory.query').handler
    read = tools.get('memory.read').handler
    assert [row['id'] for row in query({'query': '最近'})['items']] == [ids['past']]
    assert all(row['id'] == ids['past'] for row in query({'query': '渡松计划'})['items'])
    assert [row['id'] for row in read({'ids': list(ids.values())})['items']] == [ids['past']]
    kb.set_memory_use_policy(ids['past'], True)
    assert read({'ids': list(ids.values())})['items'] == []
    assert query({'query': '渡杉计划'})['items'] == []


@pytest.mark.parametrize('question', [
    '渡杉计划 2025 年和现在有什么区别？',
    '渡杉计划 2024 年和 2025 年分别怎么做？',
    '请解释“渡杉计划现在”这句话',
    '说回那个曾经只剩一种标签能用的计划，当时为什么那样做？',
])
def test_ambiguous_or_comparative_queries_keep_the_original_retrieval(question):
    assert named_period(question) is None


@pytest.mark.parametrize('agent', [False, True])
def test_missing_previous_reason_answers_about_that_period_without_model(server, records, tmp_path, agent):
    kb, _ = records
    kb.update_settings({'memory_v4_agent_allow_private': True})

    def model(request):
        pytest.fail('A checked empty previous-period read needs no model request')

    async def run():
        with web_lab(server, tmp_path / 'lab', kb) as lab:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
                result = await lab.turn(client, PREVIOUS, agent=agent)
        assert '渡杉计划」紧接这次更改之前的那一版' in result['answer']
        assert '没有说明当时为什么' in result['answer']
        assert result['audit']['status'] == 'no_match_in_named_period'
        assert result['audit']['selected'] == []

    asyncio.run(run())


def reference_history(ids):
    return [{'role': 'ai', 'memory_recall': {'evidence_contract': {'grounded_literal_focus': focus},
            'selected': [{'id': ids[key]} for key in keys]}}
            for focus, keys in [('渡杉计划', ['past', 'current']), ('渡松计划', ['near_name'])]]


def test_exclusive_resource_paraphrase_freezes_only_the_matching_period(records):
    kb, ids = records
    question = '说回那个曾经只剩一种标签能用的计划，当时为什么那样做？'
    history = reference_history(ids)
    plan = inspect(kb, question, history)
    assert plan['method'] == 'unique_exclusive_resource' and plan['source_ids'] == [ids['past']]
    ordinary = kb.build_memory_v4_context_with_audit(question, reference_history=history, graph_mode='deep')
    assert [row['id'] for row in ordinary['audit']['selected']] == [ids['past']]
    tools = kb._memory_v4_capability_registry(memory_query_options={
        'question_text': question, 'topic_seed': plan['focus'], 'reference_plan': plan})
    query = tools.get('memory.query').handler
    read = tools.get('memory.read').handler
    assert [row['id'] for row in query({'query': '最近'})['items']] == [ids['past']]
    assert [row['id'] for row in read({'ids': list(ids.values())})['items']] == [ids['past']]
    kb.set_memory_use_policy(ids['past'], True)
    assert inspect(kb, question, history)['status'] == 'clarify'
    assert read({'ids': list(ids.values())})['status'] == 'clarification_required'


def test_exclusive_resource_requires_one_source_and_rejects_a_list_of_options(records):
    kb, ids = records
    question = '说回那个曾经只剩一种标签能用的计划，当时为什么那样做？'
    history = reference_history(ids)
    assert kb.update_memory(ids['near_name'], {'content': '渡松计划当时只有灰色标签可用。'})
    assert inspect(kb, question, history)['reason'] == 'multiple_descriptor_matches'
    assert kb.update_memory(ids['near_name'], {'content': '渡松计划当时只有灰色和红色标签可用。'})
    assert inspect(kb, question, history)['source_ids'] == [ids['past']]


@pytest.mark.parametrize('agent', [False, True])
def test_scoped_reason_reaches_qq_delivery_without_the_post_chat_write_tail(server, records, tmp_path, agent):
    import json
    from types import SimpleNamespace

    from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
    from saya_app.services.qq_chat import QQChatResponder
    from test_qq_task_scheduler import make, submit, until

    kb, ids = records
    kb.update_settings({'memory_v4_agent_allow_private': True, 'autonomous_memory_enabled': True})
    question = '说回那个曾经只剩一种标签能用的计划，当时为什么那样做？'
    reason = '渡杉计划在 2025 年使用蓝色标记，因为当年只有蓝色标签可用。'
    posted = []

    def agent_post(url, **kwargs):
        body = kwargs['json']
        posted.append(body)
        if len(posted) == 1:
            name = next(x['function']['name'] for x in body['tools']
                        if x['function']['name'].startswith('memory_query_'))
            message = {'content': '', 'tool_calls': [{'id': 'lookup', 'type': 'function',
                       'function': {'name': name, 'arguments': '{"query":"最近"}'}}]}
        else:
            message = {'content': reason}
        return SimpleNamespace(status_code=200, json=lambda: {'choices': [{'message': message}]})

    def ordinary_post(request):
        body = json.loads(request.content)
        if not body['stream']:
            return httpx.Response(200, json={'choices': [{'message': {'content': '渡杉计划历史理由'}}]})
        event = {'choices': [{'delta': {'content': reason}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')

    async def run():
        session = 'session_qq_r4_delivery.jsonl'
        scope = '10000:private:20000:shared'
        with web_lab(server, tmp_path / 'lab', kb) as lab:
            bridge = make(tmp_path / 'bridge', None, model='api', agent_mode=agent,
                          recall_mode='auto' if agent else 'on', memory_write_enabled=True,
                          web_search_mode='off')
            bridge.bind_responder(QQChatResponder(bridge.settings, server.ChatWithMotion,
                                  server._chat_stream_single, lambda: 'api', lambda: lab.credentials))
            for row in reference_history(ids):
                lab.manager.save_message('ai', '先前已读记录。', session_id=session,
                    metadata={'memory_recall': row['memory_recall'],
                              'memory_use_epoch': kb.memory_use.snapshot(),
                              'memory_history_floor': kb.memory_use.history_floor()})
            await bridge.start()
            bridge.store.save_conversation(scope, [], session)
            async def cancelled_triage(*args, **kwargs):
                raise asyncio.CancelledError()
            triage = AsyncMock(side_effect=cancelled_triage)
            maintenance = []
            try:
                async with httpx.AsyncClient(transport=httpx.MockTransport(ordinary_post)) as client:
                    with (patch.object(server, '_get_api_httpx_client', return_value=client),
                          patch.object(server, 'maybe_create_autonomous_memory_candidates', triage),
                          patch.object(kb, 'maybe_auto_merge', side_effect=lambda: maintenance.append(True)),
                          patch('saya_app.services.memory_v4.OpenAICompatibleAgentStep',
                                side_effect=lambda url, key, settings:
                                    OpenAICompatibleAgentStep(url, key, settings, request_post=agent_post))):
                        submit(bridge, question)
                        await asyncio.wait_for(bridge.queue.join(), 5)
                        await until(lambda: not bridge._task_workers)
                assert [part[1] for part in bridge.client.sent] == [reason]
                assert bridge.task_registry.list_scope(scope)[0]['state'] == 'completed'
                audit = lab.manager.load_session_full(session, make_current=False)[-1]['memory_recall']
                assert [item['id'] for item in audit['selected']] == [ids['past']]
                triage.assert_not_awaited()
                assert not maintenance
            finally:
                await bridge.stop()

    asyncio.run(run())
