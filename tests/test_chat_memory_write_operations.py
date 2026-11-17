import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.chat_runtime import ChatRuntimeManager
from saya_app.services.memory_origin import bind_origin, ingress
from test_qq_chat_runtime import server


@pytest.mark.parametrize('v4', [False, True])
def test_web_save_replay_after_restart_preserves_the_original_commit(tmp_path, v4):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    kb.settings['memory_v4_write_enabled'] = v4
    source = ingress('web', session_id='session_write.jsonl', message_id='a' * 32)
    with bind_origin(source):
        result = kb.capture_explicit_memory_result('记住：我喜欢晚上安静读书。', session_id='session_write.jsonl')
    assert result.committed and len(kb.memories) == 1
    restored = KnowledgeBase(kb.base_dir)
    restored.settings['memory_v4_write_enabled'] = v4
    with bind_origin(source), patch.object(restored, '_capture_explicit_memory_impl', side_effect=AssertionError('duplicate write')):
        repeated = restored.capture_explicit_memory_result('记住：我喜欢晚上安静读书。', session_id='session_write.jsonl')
    assert repeated.items == result.items and not repeated.fresh
    with bind_origin(source), pytest.raises(ValueError, match='mismatch'):
        restored.capture_explicit_memory_result('记住：同一编号改写另一段正文。', session_id='session_write.jsonl')


def test_v4_structured_replay_uses_storage_result_without_incrementing_revision(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    kb.settings['memory_v4_write_enabled'] = True
    options = {'action': {'intent': 'explicit_save', 'content': '我爱喝清茶。'}, 'turn_id': 'session_v4.jsonl',
               'message_id': 'source-one'}
    first = kb.process_memory_write_v4('记住：我爱喝清茶。', **options)
    assert first['operation_state'] == 'committed'
    stored = first['committed'][0]['memory']
    with patch.object(kb, '_process_memory_write_v4_impl', side_effect=AssertionError('repeat')):
        repeated = kb.process_memory_write_v4('记住：我爱喝清茶。', operation_id=first['operation_id'], **options)
    assert repeated['operation_id'] == first['operation_id'] and repeated['replayed']
    assert repeated['committed'][0]['memory']['revision'] == stored['revision']
    assert kb.memory_v4_store.get_memory(stored['id']).revision == stored['revision']


def test_v4_correction_and_deletion_replays_do_not_reapply_a_mutation(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    kb.settings['memory_v4_write_enabled'] = True
    saved = kb.process_memory_write_v4('记住：我常用的编辑器是初始版。', message_id='create')
    memory = saved['committed'][0]['memory']
    corrected = kb.process_memory_write_v4('', message_id='correct', action={
        'intent': 'explicit_correct', 'memory_id': memory['id'],
        'proposal': {'canonical_text': '我常用的编辑器是新版。', 'predicate': memory['predicate']}})
    assert corrected['operation_state'] == 'committed'
    deleted = kb.process_memory_write_v4('删除指定记录', message_id='delete', action={
        'intent': 'explicit_forget', 'memory_id': memory['id']})
    with patch.object(kb, '_process_memory_write_v4_impl', side_effect=AssertionError('repeat')):
        replay = kb.process_memory_write_v4('删除指定记录', message_id='delete', action={
            'intent': 'explicit_forget', 'memory_id': memory['id']}, operation_id=deleted['operation_id'])
    assert replay['forgotten'] == deleted['forgotten'] == [memory['id']]
    assert replay['operation_state'] == 'committed' and replay['replayed']


def test_pipeline_materializes_generator_context_once_before_binding_the_plan(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    kb.settings['memory_v4_write_enabled'] = True
    context = ({'role': role, 'content': text} for role, text in [('user', '我喜欢读书'), ('assistant', '好的')])
    result = kb.process_memory_write_v4('记住：我的测试书签是浅蓝色。', context=context, message_id='context')
    assert result['operation_state'] == 'committed'


def test_uncertain_postcommit_save_stays_queryable_and_never_reexecutes(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    source = ingress('web', session_id='session_write.jsonl', message_id='b' * 32)
    actual = kb._capture_explicit_memory_impl
    def fail(*args, **kwargs):
        actual(*args, **kwargs)
        raise OSError('after commit')
    with bind_origin(source), patch.object(kb, '_capture_explicit_memory_impl', side_effect=fail) as calls:
        first = kb.capture_explicit_memory_result('记住：虚构茶杯的名字叫晨雾杯。', session_id='session_write.jsonl')
        second = kb.capture_explicit_memory_result('记住：虚构茶杯的名字叫晨雾杯。', session_id='session_write.jsonl')
    assert first.items == second.items == [{'state': 'unknown'}] and calls.call_count == 1
    assert len(kb.memories) == 1


def test_first_web_turn_has_a_stable_session_even_if_runtime_event_is_lost(tmp_path):
    manager = MemoryManager(str(tmp_path / 'history'))
    runtime = ChatRuntimeManager()
    first = runtime.create(manager, request_id='c' * 32)
    second = runtime.create(manager, request_id='c' * 32)
    assert first.session_id == second.session_id and first.request_id == second.request_id
    assert runtime.create(manager, request_id='d' * 32).session_id != first.session_id
    with pytest.raises(ValueError):
        runtime.create(manager, request_id='../invalid')


def test_structured_override_cannot_turn_an_unresolved_reference_into_a_save(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    kb.settings['memory_v4_write_enabled'] = True
    result = kb.process_memory_write_v4('记住你刚才的解释', intent='explicit_save',
                                      action={'content': '不允许替换成另一个完整事实'}, message_id='unresolved')
    assert result['operation_state'] == 'not_saved'
    assert not kb.memories and not kb.memory_v4_store.list_memories()


def test_web_result_query_is_bound_to_session_and_hides_memory_body(tmp_path):
    from fastapi import FastAPI
    from saya_app.routers import memories
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    manager = MemoryManager(str(tmp_path / 'history'))
    session = 'session_write.jsonl'
    with bind_origin(ingress('web', session_id=session, message_id='e' * 32)):
        result = kb.capture_explicit_memory_result('记住：私有测试内容只放在记忆正文。', session_id=session)
    app = FastAPI()
    app.include_router(memories.router)
    async def run():
        with patch.object(memories, 'kb_system', kb), patch.object(memories, 'memory_manager', manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
                response = await client.get('/api/kb/write-result', params={'session_id': session, 'operation_id': result.id})
                assert response.status_code == 200 and response.json()['committed']
                assert '私有测试内容' not in response.text
                other = await client.get('/api/kb/write-result', params={'session_id': 'session_other.jsonl', 'operation_id': result.id})
                assert other.status_code == 404
                qq = await client.get('/api/kb/write-result', params={'session_id': 'session_qq_test.jsonl', 'operation_id': result.id})
                assert qq.status_code == 400
    asyncio.run(run())


def test_real_generator_reuses_the_same_write_result_on_regeneration(server, tmp_path, monkeypatch):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    manager = MemoryManager(str(tmp_path / 'history'))
    async def no_candidates(*args, **kwargs):
        raise AssertionError('explicit command must not become a passive candidate')
    monkeypatch.setattr(server, 'kb_system', kb)
    monkeypatch.setattr(server, 'memory_manager', manager)
    monkeypatch.setattr(server, 'refresh_daily_activity_memory', lambda: None)
    monkeypatch.setattr(server.companion_router, 'check_conflict_for_memory', lambda *args: None)
    monkeypatch.setattr(server, 'maybe_create_autonomous_memory_candidates', no_candidates)
    from saya_app.services.conversation import conversation_service
    monkeypatch.setattr(conversation_service, 'save_credentials', lambda **kwargs: None)
    async def run():
        def model(request):
            data = {'choices': [{'delta': {'content': '明白了。'}, 'finish_reason': 'stop'}]}
            return httpx.Response(200, text='data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n')
        receipts = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            monkeypatch.setattr(server, '_get_api_httpx_client', lambda: client)
            for index in range(3):
                req = server.ChatWithMotion(user_input=('记住：我常用的书签是浅蓝色。' if index < 2 else '记住：同一编号的另一份正文。'), request_id='f' * 32,
                                            model='api', api_key='fixture', api_url='http://mock/v1',
                                            api_model_name='fixture', recall_mode='off', agent_mode=False)
                response = await server._chat_stream_single(req)
                body = ''
                async for chunk in response.body_iterator:
                    body += chunk.decode() if isinstance(chunk, bytes) else chunk
                operation = [json.loads(part.split('data: ', 1)[1])['operation'] for part in body.split('\n\n')
                             if part.startswith('event: memory_candidates')][0]
                receipts.append(operation)
        assert len(kb.memories) == 1
        assert receipts[0]['operation_id'] == receipts[1]['operation_id']
        assert receipts[0]['committed'] and receipts[1]['committed'] and receipts[1]['replayed']
        assert not receipts[2]['committed'] and receipts[2]['items'] == [{'state': 'invalid'}]
    asyncio.run(run())
