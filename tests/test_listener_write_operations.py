import asyncio,json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from core_services import KnowledgeBase,MemoryManager
from saya_app.services.listener import ListenerService,ListenerError
from saya_app.services.listener_writes import recover
from saya_app.services.memory_versions import MemoryVersionConflict,record_version


@pytest.fixture
def lab(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'knowledge_base'));manager=MemoryManager(str(tmp_path/'history'))
    def model(*args):return json.dumps({'type':'proposal','title':'虚构偏好','content':'虚构青杯偏好蓝色。','layer':'semantic','category':'preference'})
    svc=ListenerService(manager,kb,model,Path(kb.base_dir)/'listener_state.json')
    sid=svc.open_session();svc.handle_message(sid,'请录入虚构青杯偏好。')
    version=svc.derive_state(sid)['confirmation_version']
    return svc,kb,manager,sid,version


def confirm(lab,token='a'*32,content='虚构青杯偏好蓝色。'):
    svc,_,_,sid,version=lab
    return svc.confirm(sid,content,'semantic','preference','虚构偏好',expected_version=version,operation_id=token)


def test_parallel_same_operation_across_service_instances_commits_once(lab):
    svc,kb,manager,sid,version=lab
    other=ListenerService(manager,kb,svc._llm_call,svc._state_file)
    def invoke(service):return service.confirm(sid,'虚构青杯偏好蓝色。','semantic',expected_version=version,operation_id='a'*32)
    with patch.object(kb,'add_memory',wraps=kb.add_memory) as add,ThreadPoolExecutor(2) as pool:
        results=list(pool.map(invoke,[svc,other]))
    assert add.call_count==1 and len(kb.memories)==1
    assert results[0]['memory_id']==results[1]['memory_id']
    assert len([row for row in svc._entries(sid) if row.get('listener')=='confirm'])==1


def test_concurrent_different_plans_cannot_create_two_memories(lab):
    def invoke(i):
        try:return confirm(lab,token=str(i)*32,content='虚构青杯颜色'+str(i))
        except MemoryVersionConflict:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(invoke,[1,2]))
    assert sum(result is not None for result in results)==1 and len(lab[1].memories)==1


@pytest.mark.parametrize('failure',['link','receipt_before','receipt_after'])
def test_postcommit_file_failure_recovers_after_restart_without_fact_write(lab,failure):
    svc,kb,manager,sid,_=lab
    actual=svc._record
    def record(*args,**kwargs):
        if args[3].get('listener')=='confirm':
            if failure=='receipt_after':actual(*args,**kwargs)
            raise OSError('private fixture error')
        return actual(*args,**kwargs)
    with patch.object(svc,'_save_state',side_effect=OSError('file unavailable')) if failure=='link' else patch.object(svc,'_record',record):
        result=confirm(lab)
    assert result['type']=='recovery_pending' and result['operation']['committed'] and len(kb.memories)==1
    restored_kb=KnowledgeBase(kb.base_dir)
    restored=ListenerService(manager,restored_kb,svc._llm_call,svc._state_file)
    assert restored.derive_state(sid)['pending_operation']['operation_id']==result['operation']['operation_id']
    with patch.object(restored_kb,'add_memory',side_effect=AssertionError('no new fact')),patch.object(restored_kb,'update_memory',side_effect=AssertionError('no fact update')):
        repaired=recover(restored,sid,result['operation']['operation_id'])
        again=recover(restored,sid,result['operation']['operation_id'])
    assert not repaired['recovery_pending'] and not again['recovery_pending']
    assert restored.linked_memory_id(sid)==result['memory_id']
    assert len([row for row in restored._entries(sid) if row.get('listener_operation_id')==result['operation']['operation_id']])==1


@pytest.mark.parametrize('changed',['memory','history','link'])
def test_recovery_refuses_changed_committed_target_or_session(lab,changed):
    svc,kb,manager,sid,_=lab
    with patch.object(svc,'_record',side_effect=OSError('receipt failed')):result=confirm(lab)
    if changed=='memory':kb.update_memory(result['memory_id'],{'content':'用户后来改过的内容。'})
    elif changed=='history':manager.truncate_session(1,session_id=sid)
    else:
        mapping=svc._load_state();mapping[sid]['memory_id']='different';svc._save_state(mapping)
    before=[record_version(x) for x in kb.memories]
    repaired=recover(svc,sid,result['operation']['operation_id'])
    assert repaired['recovery_blocked'] and repaired['recovery_pending']
    assert before==[record_version(x) for x in kb.memories]


def test_unknown_commit_is_queryable_and_never_reexecuted_or_expired(lab):
    svc,kb,_,sid,_=lab;actual=kb.add_memory
    def fail(*args,**kwargs):actual(*args,**kwargs);raise OSError('after commit')
    with patch.object(kb,'add_memory',fail):first=confirm(lab)
    assert not first['operation']['committed'] and first['operation']['items'][0]['state']=='unknown'
    from saya_app.services.chat_memory_writes import journal
    ledger=journal(kb);ledger.clock=lambda:10**12
    with patch.object(kb,'add_memory',side_effect=AssertionError('duplicate')):
        second=confirm(lab)
        with pytest.raises(MemoryVersionConflict):confirm(lab,token='b'*32)
    assert second['operation']['items'][0]['state']=='unknown' and len(kb.memories)==1


def test_stale_linked_memory_version_cannot_be_updated(lab):
    svc,kb,_,sid,_=lab;result=confirm(lab)
    version=svc.derive_state(sid)['confirmation_version']
    kb.update_memory(result['memory_id'],{'content':'别处的新版本。'})
    with pytest.raises(MemoryVersionConflict):
        svc.update_saved(sid,'过期网页的新内容。','core',expected_version=version,operation_id='b'*32)
    assert kb._find_memory(result['memory_id'])['content']=='别处的新版本。'


def test_corrupt_link_file_is_not_replaced_by_defaults(lab):
    svc,kb,_,_,_=lab
    svc._state_file.write_text('{broken',encoding='utf-8')
    with pytest.raises(ListenerError):confirm(lab)
    assert svc._state_file.read_text()=='{broken' and not kb.memories


def test_model_result_is_discarded_after_conversation_changes(lab):
    svc,kb,manager,sid,_=lab;entered=threading.Event();release=threading.Event()
    def model(*args):entered.set();release.wait(5);return json.dumps({'type':'proposal','content':'等待后旧内容','layer':'semantic'})
    svc._llm_call=model
    with ThreadPoolExecutor(1) as pool:
        task=pool.submit(svc.handle_message,sid,'又一条输入');assert entered.wait(5)
        manager.truncate_session(1,session_id=sid);release.set()
        with pytest.raises(MemoryVersionConflict):task.result()
    assert '等待后旧内容' not in str(svc._entries(sid)) and not kb.memories


def test_operation_cannot_be_reused_for_another_edited_body(lab):
    confirm(lab)
    with pytest.raises(ListenerError,match='mismatch'):confirm(lab,content='不同的新正文。')
    assert len(lab[1].memories)==1


@pytest.mark.parametrize('sid',['session_qq_private.jsonl','../session_bad.jsonl','session_not_registered.jsonl'])
def test_unregistered_or_other_channel_session_is_not_a_listener_grant(lab,sid):
    svc,kb,_,_,_=lab
    with pytest.raises(ListenerError):svc.confirm(sid,'不能据此另存的内容','semantic')
    assert not kb.memories


def test_http_version_required_and_result_scoped_without_memory_body(lab,monkeypatch):
    from saya_app.routers import listener as routes
    svc,kb,_,sid,version=lab;monkeypatch.setattr(routes,'listener_service',svc)
    app=FastAPI();app.include_router(routes.router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            body={'session_id':sid,'content':'私有Listener正文','layer':'semantic'}
            assert (await client.post('/api/listener/confirm',json=body)).status_code==422
            result=await client.post('/api/listener/confirm',json={**body,'expected_version':version,'operation_id':'a'*32})
            assert result.status_code==200
            operation=result.json()['operation']['operation_id']
            query=await client.get('/api/listener/result',params={'session_id':sid,'operation_id':operation})
            assert query.status_code==200 and query.json()['committed'] and '私有Listener正文' not in query.text
            bad=await client.post('/api/listener/update',json={**body,'expected_version':version,'operation_id':'b'*32})
            assert bad.status_code==409
    asyncio.run(run())


def test_saved_card_reload_shows_actual_external_edit_without_replaying_old_body(lab):
    svc,kb,_,sid,_=lab;result=confirm(lab)
    kb.update_memory(result['memory_id'],{'content':'外部编辑后的当前正文。'})
    replay=confirm(lab)
    assert replay['content_changed'] and 'proposal' not in replay
    assert svc.derive_state(sid)['proposal']['content']=='外部编辑后的当前正文。'


def test_update_keeps_user_chosen_category_and_existing_tags(lab):
    svc,kb,_,sid,_=lab;result=confirm(lab)
    version=svc.derive_state(sid)['confirmation_version']
    updated=svc.update_saved(sid,'虚构安排改成下午。','episodic','schedule','虚构安排',
                             expected_version=version,operation_id='b'*32)
    assert updated['proposal']['category']=='schedule'
    assert 'listener' in kb._find_memory(result['memory_id'])['tags']


def test_changed_sidecar_cannot_redirect_a_saved_listener_card_to_another_memory(lab):
    svc,kb,_,sid,_=lab;first=confirm(lab)
    other=kb.add_memory('另一条无关的记忆。')
    mapping=svc._load_state();mapping[sid]['memory_id']=other['id'];svc._save_state(mapping)
    with pytest.raises(MemoryVersionConflict):svc.derive_state(sid)
    with pytest.raises(MemoryVersionConflict):svc.update_saved(sid,'不允许覆盖无关条目。','semantic')
    assert kb._find_memory(first['memory_id'])['content']=='虚构青杯偏好蓝色。'
    assert kb._find_memory(other['id'])['content']=='另一条无关的记忆。'


def test_oversized_edited_body_is_rejected_instead_of_silently_truncated(lab):
    with pytest.raises(ListenerError,match='2000'):confirm(lab,content='甲'*2001)
    assert not lab[1].memories
