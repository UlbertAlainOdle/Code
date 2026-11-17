"""M1 provenance contracts. Synthetic stores/transports, no real QQ receipts."""
import asyncio
from copy import deepcopy
import json

import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_origin import (
    MAX_EVENTS, apply_origin, bind_origin, current_origin, ingress, projection,
    unknown_origin, visible_provenance,
)
from saya_app.services.memory_versions import MemoryVersionConflict, record_version
from saya_app.services.memory_v4 import MemoryV4Store, WritePipeline
from saya_app.routers import memories
from test_qq_chat_runtime import server


def qq(message_id='100', quote='记住：虚构偏好是蓝色茶杯', group_id=None):
    return ingress('qq', subject='20001', bot_id='30001', session_id='qq-test',
        message_id=message_id, group_id=group_id, quote=quote)


@pytest.fixture
def kb(tmp_path, monkeypatch):
    value = KnowledgeBase(str(tmp_path/'kb'))
    monkeypatch.setattr(memories, 'kb_system', value)
    return value


def test_explicit_qq_web_projection_and_full_restart(kb):
    with bind_origin(qq()):
        item = kb.capture_explicit_memory('记住：虚构偏好是蓝色茶杯', session_id='qq-test')
    mid = item['id']
    expected = projection(item)
    assert expected['origin']['channel'] == 'qq'
    assert expected['origin']['verified_subject'] == '20001'
    assert expected['origin']['occurred_at'] is None
    assert expected['context_events'][0]['context_coverage'] == 'current_message_only'
    assert projection(kb.memory_v4_store.get_memory(mid).metadata) == expected
    assert next(m for m in memories.get_memories()['memories'] if m['id']==mid)['origin'] == expected['origin']
    again = KnowledgeBase(kb.base_dir)
    assert projection(again._find_memory(mid)) == expected
    assert projection(again.memory_v4_store.get_memory(mid).metadata) == expected
    assert next(m for m in again.sqlite_store.load_active_memories() if m['id']==mid)['origin'] == expected['origin']


def test_web_edit_preserves_qq_creation_events_tags_id_and_cas(kb):
    with bind_origin(qq()):
        item = kb.add_memory('虚构杯子偏好蓝色', tags=[f'tag{x}' for x in range(8)])
    mid, created = item['id'], item['creation_time']
    old = record_version(item)
    memories.update_memory({'id':mid, 'expected_version':old, 'updates':{
        'content':'虚构杯子偏好蓝色，杯柄向右',
        'origin':{'channel':'web'},
        'source_detail':{'origin':{'channel':'web'}, 'keep':'retained'}}})
    item = kb._find_memory(mid)
    assert item['origin']['channel'] == 'qq'
    assert item['last_modified_channel'] == 'web'
    assert len(item['context_events']) == 2
    assert item['context_events'][-1]['revises_event_id'] == item['context_events'][0]['id']
    assert item['creation_time'] == created
    assert item['source_detail']['keep'] == 'retained'
    assert item['source_detail']['origin'] == item['origin']
    with pytest.raises(Exception) as error:
        memories.update_memory({'id':mid, 'expected_version':old, 'updates':{'content':'stale'}})
    assert error.value.status_code == 409
    again = KnowledgeBase(kb.base_dir)
    assert projection(again.memory_v4_store.get_memory(mid).metadata) == projection(item)


@pytest.mark.parametrize('endpoint', ['legacy', 'pipeline', 'candidate'])
def test_supplied_qq_origin_cannot_be_promoted(kb, endpoint):
    forged = {'origin':qq()['origin'], 'context_events':[{'id':'forged'}], 'last_modified_channel':'qq'}
    if endpoint == 'legacy':
        item = memories.add_memory({'content':'虚构网页杯子偏好', 'metadata':forged,
            'source_detail':forged, 'source':'auto_explicit'})['memory']
        assert item['origin']['channel'] == 'web'
    elif endpoint == 'pipeline':
        result = kb.process_memory_write_v4('记住：虚构网页杯子偏好', action={'metadata':forged})
        item = result['committed'][0]['memory']['metadata']
        assert item['origin']['channel'] == 'unknown'
    else:
        item = kb.add_memory_candidate('虚构网页杯子偏好', metadata=forged, source_detail=forged)
        assert item['origin']['channel'] == 'unknown'
    assert all(e['id'] != 'forged' for e in item.get('context_events', []))


@pytest.mark.parametrize('capture,confirm', [('qq','web'),('web','qq')])
def test_candidate_creation_and_confirmation_have_separate_origins(kb, capture, confirm):
    contexts = {'qq':qq(), 'web':ingress('web', message_id='web-1', quote='虚构杯子偏好')}
    with bind_origin(contexts[capture]):
        candidate = kb.add_memory_candidate('虚构杯子偏好蓝色', tags=[f'tag{x}' for x in range(8)])
    with bind_origin(contexts[confirm]):
        result = kb.confirm_memory_candidate(candidate['id'], expected_version=record_version(candidate))
    item = result['memory']
    assert item['origin']['channel'] == capture
    assert item['last_modified_channel'] == confirm
    assert [e['reason'] for e in item['context_events']] == ['candidate_capture','candidate_confirm']
    assert item['tags'] == [f'tag{x}' for x in range(8)]
    assert projection(kb.memory_v4_store.get_memory(item['id']).metadata) == projection(item)


def test_existing_unproven_record_remains_unknown_when_edited(kb):
    item = kb.add_memory('虚构旧记忆', source_detail={'type':'qq', 'chat_session_id':'qq-filename'})
    for key in projection(item):
        item.pop(key, None)
    before = deepcopy(item)
    displayed = memories.versioned(item)
    assert displayed['origin']['channel'] == 'unknown' and item == before
    with bind_origin(qq('new')):
        kb.update_memory(item['id'], {'content':'虚构旧记忆的新版本'})
    assert item['origin']['channel'] == 'unknown'
    assert item['last_modified_channel'] == 'qq'


def test_pipeline_correction_preserves_creation_and_source_events(kb):
    with bind_origin(qq()):
        result = kb.process_memory_write_v4('记住：虚构杯子偏好蓝色', action={'predicate':'synthetic_cup'})
    item = result['committed'][0]['memory']
    mid, created = item['id'], item['created_at']
    with bind_origin(ingress('web', quote='更正为绿色')):
        changed = kb.process_memory_write_v4('更正：虚构杯子偏好绿色', action={
            'operation':'correct','memory_id':mid,'predicate':'synthetic_cup'})
    item = changed['committed'][0]['memory']
    assert item['id'] == mid and item['created_at'] == created
    assert item['metadata']['origin']['channel'] == 'qq'
    assert item['metadata']['last_modified_channel'] == 'web'
    assert len(item['metadata']['context_events']) == 2
    assert projection(kb._find_memory(mid)) == projection(item['metadata'])


def test_v4_control_and_restore_preserve_creation_not_request_origin(kb):
    with bind_origin(qq()):
        item = kb.capture_explicit_memory('记住：虚构茶杯偏好蓝色')
    mid = item['id']
    revision = kb.memory_v4_store.get_memory(mid).revision
    response = memories.apply_memory_v4_control({'operation':'correct', 'memory_id':mid,
        'updates':{'canonical_text':'虚构茶杯偏好绿色', 'metadata':{'origin':{'channel':'web'}}}})
    assert response['status'] == 'success'
    assert kb._find_memory(mid)['origin']['channel'] == 'qq'
    assert kb._find_memory(mid)['last_modified_channel'] == 'web'
    restored = memories.apply_memory_v4_control({'operation':'restore','memory_id':mid,'revision':revision})
    assert restored['status'] == 'success'
    assert kb._find_memory(mid)['origin']['channel'] == 'qq'
    assert len(kb._find_memory(mid)['context_events']) == 3


def test_shared_source_event_multiple_facts_and_replay_is_bounded(tmp_path):
    pipeline = WritePipeline(MemoryV4Store(tmp_path/'v4.sqlite3'))
    with bind_origin(qq()):
        result = pipeline.process('记住：我喜欢蓝色茶杯；我喜欢绿色碟子')
    items = [c['memory'] for c in result['committed']]
    assert len(items) == 2
    assert items[0]['metadata']['context_events'][0]['id'] == items[1]['metadata']['context_events'][0]['id']
    state = {}
    for n in range(100):
        previous = deepcopy(state) if state else None
        with bind_origin(qq(str(n))):
            apply_origin(state, previous=previous, reason='edit')
    assert len(state['context_events']) == MAX_EVENTS
    assert state['context_events'][0]['origin']['message_id'] == '0'
    previous = deepcopy(state)
    with bind_origin(qq('99')):
        apply_origin(state, previous=previous, reason='edit')
    assert state == previous


def test_pipeline_replay_does_not_duplicate_source_event(kb):
    with bind_origin(qq()):
        first = kb.capture_explicit_memory('记住：虚构杯子偏好蓝色')
        second = kb.capture_explicit_memory('记住：虚构杯子偏好蓝色')
    assert first['id'] == second['id']
    assert len(second['context_events']) == 1


def test_group_fact_grant_does_not_disclose_private_source_story():
    private = {}
    with bind_origin(qq(quote='PRIVATE_ORIGIN_CANARY')):
        apply_origin(private)
    with bind_origin(qq('public', 'GROUP_EVIDENCE', '90000')):
        public = apply_origin({}, previous=private, reason='edit')
    view = visible_provenance(public, group_id='90000')
    assert 'PRIVATE_ORIGIN_CANARY' not in json.dumps(view)
    assert 'origin' not in view
    assert len(view['context_events']) == 1
    assert visible_provenance(public, group_id='90001') == {'context_events':[]}


def test_contexts_are_task_local_and_to_thread_receives_only_own_origin():
    async def worker(mid):
        with bind_origin(qq(mid)):
            await asyncio.sleep(0)
            return await asyncio.to_thread(current_origin)
    async def run():
        return await asyncio.gather(worker('a'),worker('b'))
    assert [c['origin']['message_id'] for c in asyncio.run(run())] == ['a','b']
    assert current_origin() is None


@pytest.mark.parametrize('subject,bot', [('nickname','30001'),('20001','fake'),('', '')])
def test_unverified_identity_does_not_become_qq_origin(subject, bot):
    assert ingress('qq',subject=subject,bot_id=bot)['origin'] == unknown_origin()


def test_background_derived_write_does_not_become_user_evidence(kb):
    with bind_origin(qq()):
        item = kb.add_memory('虚构后台摘要', source='ai_compression')
    assert item['origin']['channel'] == 'unknown'
    assert item['context_events'] == []


def test_explicit_web_channel_comes_from_handler_even_with_custom_source(kb):
    item = memories.add_memory({'content':'虚构网页来源', 'source':'pretend_qq'})['memory']
    assert item['origin']['channel'] == 'web'


def test_real_group_projection_keeps_fact_and_hides_private_source(kb, tmp_path):
    from saya_app.services.qq_group_context import QQGroupContext, GroupPolicy
    from saya_app.services.qq_memory_management import QQMemoryManagement, policy_content_revision
    from saya_app.services.qq_memory_access import MemoryActor
    from test_qq_task_scheduler import make
    from test_qq_group_context import message
    bridge = make(tmp_path/'bridge',None)
    bridge.store.initialize(7)
    bridge.store.set_group('10000','90000',True)
    management = QQMemoryManagement(bridge,kb)
    service = QQGroupContext(bridge,kb,clock=lambda:1000.0)
    bridge.group_context = service
    service.configure('20000','90000',GroupPolicy(memory_enabled=True).model_dump(),0,confirmed=True)
    with bind_origin(qq(quote='PRIVATE_ORIGIN_STORY_CANARY')):
        fact = kb.add_memory('虚构公开茶杯偏好蓝色')
    management.access.set(MemoryActor('10000','20000',True,True),fact['id'],
        policy_content_revision(fact),'20000',visibility='groups',groups=('90000',))
    snapshot = service.snapshot(message(text='记忆'))
    assert snapshot['facts'][0]['id'] == fact['id']
    assert snapshot['facts'][0]['text'] == fact['content']
    assert 'PRIVATE_ORIGIN_STORY_CANARY' not in json.dumps(snapshot)
    assert 'context_events' not in json.dumps(snapshot)


def test_failed_save_restores_previous_origin_and_cas(kb, monkeypatch):
    with bind_origin(qq()):
        item = kb.add_memory('虚构杯子偏好蓝色')
    before = deepcopy(item)
    monkeypatch.setattr(kb,'save_memories',lambda **kw:False)
    with bind_origin(ingress('web')):
        assert not kb.update_memory(item['id'],{'content':'虚构未成功的更正'},expected_version=record_version(item))
    assert kb._find_memory(item['id']) == before
    assert projection(kb.memory_v4_store.get_memory(item['id']).metadata) == projection(before)


def test_candidate_recurrence_retains_both_channel_evidence(kb):
    with bind_origin(qq()):
        item = kb.add_memory_candidate('虚构杯子偏好蓝色')
    with bind_origin(ingress('web',quote='虚构杯子偏好蓝色')):
        again = kb.add_memory_candidate('虚构杯子偏好蓝色')
    assert again['id'] == item['id']
    assert again['origin']['channel'] == 'qq'
    assert [e['origin']['channel'] for e in again['context_events']] == ['qq','web']


def test_rollback_gate_detects_persisted_origin(kb, tmp_path):
    from saya_app.services.qq_rollback import memory_rollback_requirements, check_memory_rollback
    root = tmp_path/'root'
    base = root/'knowledge_base'
    base.mkdir(parents=True)
    (base/'knowledge.json').write_text(json.dumps([{'origin':qq()['origin']}]), encoding='utf-8')
    assert 'memory_origin_context_v1' in memory_rollback_requirements(root)
    assert check_memory_rollback(root, {'memory_compatibility':[]})['missing'] == ['memory_origin_context_v1']


@pytest.mark.parametrize('channel', ['web','qq'])
def test_actual_stream_and_responder_bind_transport_origin(server, tmp_path, monkeypatch, channel):
    import httpx
    from core_services import MemoryManager
    from saya_app.services.conversation import conversation_service
    from saya_app.services.qq_bridge import normalize_event
    from saya_app.services.qq_chat import QQChatResponder
    from test_qq_delivery import config, event
    kb = KnowledgeBase(str(tmp_path/'kb'))
    manager = MemoryManager(str(tmp_path/'chats'))
    manager.legacy_sessions_file = str(tmp_path/'no-legacy.json')
    monkeypatch.setattr(server, 'kb_system', kb)
    monkeypatch.setattr(server, 'memory_manager', manager)
    monkeypatch.setattr(server, 'refresh_daily_activity_memory', lambda:None)
    monkeypatch.setattr(server.companion_router,'check_conflict_for_memory',lambda *a:None)
    monkeypatch.setattr(conversation_service,'save_credentials',lambda **kw:None)
    seen = []
    def model(req):
        seen.append(current_origin())
        data = {'choices':[{'delta':{'content':'明白了。'},'finish_reason':'stop'}]}
        return httpx.Response(200, text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            monkeypatch.setattr(server,'_get_api_httpx_client',lambda:api)
            if channel == 'qq':
                settings = config(shared_memory=True, recall_mode='off')
                responder = QQChatResponder(settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',
                    lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test'})
                message,_ = normalize_event(event('记住：虚构茶杯偏好蓝色',message_id=123),settings)
                await responder(message,[],'session_qq_origin.jsonl')
            else:
                req = server.ChatWithMotion(user_input='记住：虚构茶杯偏好蓝色',session_id='session_web_origin.jsonl',
                    model='api',api_key='fake',api_url='http://mock/v1',agent_mode=False,recall_mode='off',web_search_mode='off')
                response = await server._chat_stream_single(req)
                assert [chunk async for chunk in response.body_iterator]
    asyncio.run(run())
    actual = [m for m in kb.memories if '虚构茶杯' in m.get('content','')]
    assert len(actual) == 1
    assert actual[0]['origin']['channel'] == channel
    assert actual[0]['context_events'][0]['summary'] == '记住：虚构茶杯偏好蓝色'
    if channel == 'qq':
        assert actual[0]['origin']['message_id'] == '123'
        assert actual[0]['origin']['verified_subject'] == '20000'
    assert seen and all(c['origin']['channel']==channel for c in seen)
    assert current_origin() is None


def test_deferred_worker_uses_saved_job_origin_not_another_active_turn(server, kb, monkeypatch):
    context = qq('saved-job')
    job = {'session_id':'qq-test','enqueued_at':'2026-11-03T00:00:00','memory_origin':context}
    entry = {'job':job,'future':None}
    seen = []
    async def execute(model, job):
        seen.append(current_origin())
        kb.add_memory_candidate('虚构茶杯偏好蓝色')
        return {'candidates':[], 'auto_confirmed':[]}
    monkeypatch.setattr(server,'kb_system',kb)
    monkeypatch.setattr(server,'_deferred_triage_job_expired',lambda *a,**kw:False)
    monkeypatch.setattr(server,'_user_generation_demand',0)
    monkeypatch.setattr(server,'llama',object())
    monkeypatch.setattr(server,'_execute_deferred_triage_job',execute)
    monkeypatch.setattr(server,'_deferred_triage_queue',[entry])
    monkeypatch.setattr(server,'_deferred_triage_jobs_save',lambda:None)
    kb.settings['autonomous_memory_enabled'] = True
    async def run():
        monkeypatch.setattr(server,'llama_lock',asyncio.Lock())
        with bind_origin(ingress('web',message_id='other-turn')):
            assert await server._run_deferred_triage_entry(entry)
            assert current_origin()['origin']['channel'] == 'web'
    asyncio.run(run())
    assert seen == [context]
    assert kb.memory_candidates[0]['origin']['message_id'] == 'saved-job'
