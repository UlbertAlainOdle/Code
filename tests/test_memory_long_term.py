"""Synthetic long-horizon and interrupted-projection checks; no live uptime claims."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from core_services import KnowledgeBase, MemoryManager
from saya_app.routers import memories
from saya_app.services.chat_agents import AgentStore, entries_for_agent, history_for_agent
from saya_app.services.chat_time_context import web_history_times, previous_web_time, clock_context
from saya_app.services import memory_health, multi_agent_chat
from saya_app.services.memory_revisions import MemoryRevisions
from saya_app.services.memory_origin import bind_origin, ingress
from saya_app.services.memory_versions import record_version, MemoryVersionConflict
from saya_app.services.memory_v4 import MemoryItemV4, MemoryEvidence, MemoryV4Store, MemoryRetriever, MemoryQuery
from test_chat_agents import server


def test_clock_and_text_share_persona_boundary_with_same_text(tmp_path):
    manager=MemoryManager(str(tmp_path/'chat'));sid=manager.create_session()
    for agent,stamp in [('saya','2026-01-01T12:00:00'),('editor','2026-01-02T13:00:00')]:
        manager.save_message('ai','same reply',metadata={'agent_id':agent,'time':stamp},session_id=sid)
    rows=manager.load_session_full(sid,make_current=False)
    assert len(entries_for_agent(rows,'saya'))==1
    assert all(x['agent_id']=='saya' for x in web_history_times(manager,sid))
    assert 'editor' not in json.dumps(web_history_times(manager,sid))
    assert previous_web_time(manager,sid)==web_history_times(manager,sid)[-1]['time']
    assert history_for_agent(rows,'editor')==['Saya: same reply']


def test_actual_custom_stream_after_memory_epoch_keeps_own_context(server,tmp_path,monkeypatch):
    from saya_app.services.conversation import conversation_service
    manager=MemoryManager(str(tmp_path/'chat'));sid=manager.create_session();kb=KnowledgeBase(str(tmp_path/'kb'))
    agents=AgentStore(tmp_path/'agents.json');editor=agents.save('编辑','EDITOR_PROMPT')
    epoch=kb.memory_use.advance()
    for agent,text in [('saya','SAYA_PRIVATE_REPLY'),(editor['id'],'EDITOR_OWN_REPLY')]:
        manager.save_message('ai',text,metadata={'agent_id':agent,'memory_use_epoch':epoch},session_id=sid)
    captured=[]
    def provider(request):
        captured.append(json.loads(request.content))
        chunk={'choices':[{'delta':{'content':'ok'},'finish_reason':None}]}
        return httpx.Response(200,text='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with patch.object(server,'_get_api_httpx_client',lambda:client):
                req=server.ChatWithMotion(user_input='继续编辑',agent_ids=[editor['id']],session_id=sid,model='api',
                    api_key='test-key',api_url='https://example.invalid/v1',api_model_name='test-model',recall_mode='off')
                response=await server.chat_stream(req)
                return ''.join([x async for x in response.body_iterator])
    monkeypatch.setattr(server,'memory_manager',manager);monkeypatch.setattr(server,'kb_system',kb)
    monkeypatch.setattr(multi_agent_chat,'agent_store',agents)
    monkeypatch.setattr(conversation_service,'save_credentials',lambda **kwargs:None)
    asyncio.run(run())
    payload=json.dumps(captured,ensure_ascii=False)
    assert captured and 'EDITOR_OWN_REPLY' in payload and 'SAYA_PRIVATE_REPLY' not in payload


@pytest.fixture
def lab(tmp_path,monkeypatch):
    kb=KnowledgeBase(str(tmp_path/'kb'));monkeypatch.setattr(memories,'kb_system',kb)
    kb.settings['embedding_enabled']=False;kb.save_settings()
    return kb


def test_health_readonly_detects_stale_payload_lexical_vectors_then_single_version_repair(lab,monkeypatch):
    item=lab.add_memory('PRIVATE_SYNTHETIC_TEXT alpha',importance_score=.73)
    lab.settings['embedding_enabled']=True
    with lab.sqlite_store._connection() as db:
        db.execute('update memories set payload=? where id=?',(json.dumps({**item,'content':'stale'}),item['id']))
        db.execute('delete from memory_fts where memory_id=?',(item['id'],))
    before=deepcopy(lab.memories)
    files={p:p.read_bytes() for p in Path(lab.base_dir).glob('*.json')}
    monkeypatch.setattr(lab,'initialize_embedding_provider',lambda:pytest.fail('health initialized provider'))
    result=memories.memory_health()
    assert {'legacy_payload_mismatch','lexical_index_mismatch','vector_not_generated'}<={x['code'] for x in result['issues']}
    assert 'PRIVATE_SYNTHETIC_TEXT' not in json.dumps(result)
    assert result['writes']==result['model_calls']==0 and lab.memories==before
    assert all(p.read_bytes()==body for p,body in files.items())
    repaired=memories.repair_memory_projection({'action':'legacy_index','id':item['id'],'expected_version':record_version(item)})
    assert repaired['indexed']==1 and repaired['fact_writes']==0 and lab.memories==before
    assert [x['code'] for x in memory_health.inspect(lab)['issues']]==['vector_not_generated']


def test_index_repair_rejects_old_versions_pending_and_deleted(lab):
    item=lab.add_memory('synthetic row');old=record_version(item)
    lab.update_memory(item['id'],{'content':'changed'})
    for version in [old,None]:
        with pytest.raises(HTTPException) as error:
            memories.repair_memory_projection({'action':'legacy_index','id':item['id'],'expected_version':version})
        assert error.value.status_code in {400,409}
    lab._mark_v4_projection(item['id'],True)
    with pytest.raises(MemoryVersionConflict):memory_health.repair_index(lab,item['id'],record_version(item))
    lab._mark_v4_projection(item['id'],False);lab.delete_memory(item['id'])
    with pytest.raises((ValueError,MemoryVersionConflict)):memory_health.repair_index(lab,item['id'],old)
    assert lab._find_memory(item['id']) is None


def test_health_pages_without_creating_missing_stores(tmp_path):
    kb=SimpleNamespace(base_dir=str(tmp_path/'absent'),_memory_write_lock=RLock(),
        memories=[{'id':str(x),'content':'private'} for x in range(250)],settings={},_pending_v4_projections=lambda:set())
    first=memory_health.inspect(kb,limit=200)
    assert first['checked']==200 and first['next_offset']==200
    last=memory_health.inspect(kb,offset=first['next_offset'])
    assert last['checked']==50 and last['next_offset'] is None
    assert not Path(kb.base_dir).exists() and 'private' not in json.dumps(first)


def test_native_projection_cas_budget_and_restart_recovery(lab,monkeypatch):
    items=[lab.add_memory('synthetic row '+str(x)) for x in range(3)]
    for item in items:lab._mark_v4_projection(item['id'],True)
    target=items[0];native=lab.memory_v4_store.get_memory(target['id'])
    old=memory_health.native_version(native,False)
    lab.memory_v4_store.upsert_memory(replace(native,canonical_text='new native version'),operation='test')
    with pytest.raises(HTTPException) as error:
        memories.repair_memory_projection({'action':'native_projection','id':target['id'],'expected_version':old})
    assert error.value.status_code==409 and lab._find_memory(target['id'])['content']!='new native version'
    current=lab.memory_v4_store.get_memory(target['id'])
    assert memories.repair_memory_projection({'action':'native_projection','id':target['id'],
        'expected_version':memory_health.native_version(current,False)})['reconciled']==1
    assert lab._find_memory(target['id'])['content']=='new native version'
    assert lab.reconcile_v4_projections(limit=1)['remaining']==1
    again=KnowledgeBase(lab.base_dir)
    assert again._pending_v4_projections()==set() and len(again.memories)==3
    assert not memory_health.inspect(again)['issues']


def test_native_only_committed_fact_is_visible_as_pending_not_missing_duplicate(lab):
    item=MemoryItemV4(id='native-only',canonical_text='private native committed fact',salience=.65)
    lab.memory_v4_store.upsert_memory(item,[MemoryEvidence(id='e-native',memory_id=item.id,quote=item.canonical_text,
        source_type='user_message',source_id='native-message')])
    lab._mark_v4_projection(item.id,True)
    report=memory_health.inspect(lab)
    assert len(report['issues'])==1 and report['issues'][0]['code']=='native_projection_pending'
    assert 'private native' not in json.dumps(report)
    version=report['issues'][0]['native_version']
    result=memories.repair_memory_projection({'action':'native_projection','id':item.id,'expected_version':version})
    assert result['reconciled']==1 and len(lab.memories)==1 and lab._find_memory(item.id)['content']==item.canonical_text
    with pytest.raises(HTTPException) as error:
        memories.repair_memory_projection({'action':'native_projection','id':item.id,'expected_version':version})
    assert error.value.status_code==409


def test_projection_repair_cancellation_and_elapsed_budget_leave_pending(lab,monkeypatch):
    from saya_app.services.qq_tasks import TaskCancelled
    item=lab.add_memory('pending synthetic');lab._mark_v4_projection(item['id'],True)
    before=deepcopy(lab.memories)
    def cancel():raise TaskCancelled('cancelled')
    with pytest.raises(TaskCancelled):lab.reconcile_v4_projections(authorize=cancel)
    assert lab.memories==before and item['id'] in lab._pending_v4_projections()
    ticks=iter([0,3]);monkeypatch.setattr('time.monotonic',lambda:next(ticks))
    result=lab.reconcile_v4_projections()
    assert result['reconciled']==0 and result['remaining']==1


def test_vector_diagnostic_uses_current_provider_without_calling_it(lab):
    item=lab.add_memory('synthetic vector body');lab.settings['embedding_enabled']=True
    lab.embedding_provider=SimpleNamespace(key='current')
    with lab.sqlite_store._connection() as db:
        for key,digest in [('old',lab._embedding_content_hash(item)),('current','stale')]:
            db.execute('insert into memory_embeddings values(?,?,?,?,?,?)',(item['id'],key,1,b'1234',digest,'2026-01-01'))
    assert [x['code'] for x in memory_health.inspect(lab)['issues']]==['vector_content_stale']


def test_health_respects_normalized_native_text_and_original_migration_exclusions(lab):
    good=lab.add_memory('synthetic good body')
    good['content']='  '+good['content']+'\n'
    excluded=lab.add_memory('synthetic old fragment')
    lab.save_memories()
    with lab.memory_v4_store._connection() as db:db.execute('delete from memory_items where id=?',(excluded['id'],))
    with patch('saya_app.services.memory_v4.migration.MemoryV4Migration._dangling_reason',return_value='dangling_start'):
        result=memory_health.inspect(lab)
    assert [x['code'] for x in result['issues']]==['native_migration_excluded']


@pytest.mark.parametrize('days',[30,90])
def test_simulated_horizon_keeps_identity_expires_stages_and_never_resurrects_deleted(tmp_path,monkeypatch,days):
    base=datetime(2026,1,1,12);store=MemoryV4Store(tmp_path/'clock.sqlite3')
    for mid,text,valid_to,valid_from in [('identity','synthetic person persistent preference',None,None),
            ('temporary','synthetic person temporary plan',(base+timedelta(days=7)).isoformat(),None),
            ('future','synthetic person future state',None,(base+timedelta(days=days+1)).isoformat())]:
        item=MemoryItemV4(id=mid,canonical_text=text,valid_to=valid_to,valid_from=valid_from,salience=.8)
        store.upsert_memory(item,[MemoryEvidence(id='e-'+mid,memory_id=mid,quote=text,source_type='user_message',source_id=mid)])
    now=(base+timedelta(days=days)).isoformat()
    monkeypatch.setattr('saya_app.services.memory_v4.retrieval._now',lambda:now)
    result=MemoryRetriever(store).search(MemoryQuery(query='synthetic person'))
    assert [x.id for x in result.items]==['identity']
    assert {x['reason'] for x in result.omitted}=={'past_valid_to','not_yet_valid'}
    store.tombstone_memory('identity',purge=True)
    again=MemoryV4Store(store.path)
    assert not MemoryRetriever(again).search(MemoryQuery(query='persistent preference')).items


def test_validity_offsets_and_end_of_day_are_comparable(tmp_path,monkeypatch):
    store=MemoryV4Store(tmp_path/'v4.sqlite3')
    for mid,expires in [('offset','2026-01-02T04:00:00+08:00'),('date','2026-01-02')]:
        text='synthetic validity'
        store.upsert_memory(MemoryItemV4(id=mid,canonical_text=text,valid_to=expires),
            [MemoryEvidence(id='e-'+mid,memory_id=mid,quote=text,source_type='user_message',source_id=mid)])
    monkeypatch.setattr('saya_app.services.memory_v4.retrieval._now',lambda:'2026-01-01T21:00:00+00:00')
    result=MemoryRetriever(store).search(MemoryQuery(query='validity'))
    assert [x.id for x in result.items]==['date']


def test_cross_channel_long_sequence_noop_conflict_disable_and_restart(lab):
    with bind_origin(ingress('qq',subject='20000',bot_id='10000',message_id='create')):
        item=lab.add_memory('虚构长期序列：茶杯叫青禾杯。',importance_score=.73)
    mid=item['id'];origin=deepcopy(item['origin']);service=MemoryRevisions(lab,clock=lambda:1000)
    for n in range(12):
        with bind_origin(ingress('qq' if n%2 else 'web',subject='20000',bot_id='10000',message_id=str(n))):
            preview=service.preview(mid,'append',f'阶段{n}只用于验收。',expected_version=record_version(item),
                scope='owner',session_id='lab',authorize=lambda:None)
            assert service.confirm(preview['confirmation_token'],scope='owner',session_id='lab',authorize=lambda:None)['status']=='applied'
    before=record_version(item)
    assert service.preview(mid,'append','阶段11只用于验收。',expected_version=before,scope='owner',session_id='lab',authorize=lambda:None)['status']=='noop'
    assert record_version(item)==before and len(lab.memories)==1 and item['origin']==origin
    proposed=service.preview(mid,'append','并发未提交。',expected_version=before,scope='owner',session_id='lab',authorize=lambda:None)
    lab.update_memory(mid,{'importance_score':.55},expected_version=before)
    assert service.confirm(proposed['confirmation_token'],scope='owner',session_id='lab',authorize=lambda:None)['status']=='stale'
    lab.set_memory_use_policy(mid,True)
    assert not lab.query_memory_v4('青禾杯')['items']
    again=KnowledgeBase(lab.base_dir)
    assert not again.query_memory_v4('青禾杯')['items']
    assert again._find_memory(mid)['importance_score']==again.memory_v4_store.get_memory(mid).salience==.55
    assert len(again.memories)==1 and len(item['context_events'])<=16 and not memory_health.inspect(again)['issues']
