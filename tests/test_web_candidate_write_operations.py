import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

from fastapi import FastAPI
import httpx
import pytest

from core_services import KnowledgeBase
from saya_app.routers import memories as routes
from saya_app.services.memory_origin import bind_origin, ingress
from saya_app.services.memory_versions import CandidateStateSaveError, record_version
from saya_app.services.web_candidate_writes import confirm


@pytest.fixture
def lab(tmp_path, monkeypatch):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    candidate=kb.add_memory_candidate('虚构读书偏好是先整理浅蓝书签。',title='阅读偏好')
    body={'id':candidate['id'],'expected_version':record_version(candidate),'updates':{'content':'虚构读书偏好是先整理浅蓝书签，再逐段阅读。'}}
    app=FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes,'kb_system',kb)
    monkeypatch.setattr('saya_app.routers.companion.check_conflict_for_memory',lambda *args:None)
    return kb,candidate,body,app


def test_confirmation_replays_one_committed_result(lab):
    kb,candidate,body,app=lab
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            with patch.object(kb,'confirm_memory_candidate',wraps=kb.confirm_memory_candidate) as raw:
                first=await client.post('/api/kb/candidates/confirm',json=body)
                second=await client.post('/api/kb/candidates/confirm',json=body)
            assert first.status_code==second.status_code==200
            a,b=first.json(),second.json()
            assert a['operation']['committed'] and b['operation']['replayed']
            assert a['operation']['operation_id']==b['operation']['operation_id'] and raw.call_count==1
            assert a['memory']['id']==b['memory']['id'] and len(kb.memories)==1
    asyncio.run(run())


def test_failed_candidate_file_recovers_after_reload_without_body_write(lab):
    kb,candidate,body,app=lab
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            with patch.object(kb,'save_memory_candidates',return_value=False):
                first=await client.post('/api/kb/candidates/confirm',json=body)
            data=first.json()
            assert first.status_code==200 and data['operation']['committed'] and data['candidate_status_pending']
            operation=data['operation']['operation_id']
            assert kb._find_memory_candidate(candidate['id'])['status']=='pending' and len(kb.memories)==1
            listed=(await client.get('/api/kb/candidates')).json()['candidates'][0]
            assert listed['confirmation_operation']=={'operation_id':operation,'committed':True}
            with patch.object(kb,'add_memory',side_effect=AssertionError('must never create again')):
                recovered=await client.post('/api/kb/candidates/reconcile',json={'operation_id':operation})
            assert recovered.status_code==200 and not recovered.json()['candidate_status_pending']
            assert kb._find_memory_candidate(candidate['id'])['status']=='confirmed'
            queried=await client.get('/api/kb/write-result',params={'operation_id':operation})
            assert queried.status_code==200 and queried.json()['committed']
            assert body['updates']['content'] not in queried.text
            wrong=await client.get('/api/kb/write-result',params={'operation_id':operation,'session_id':'session_other.jsonl'})
            assert wrong.status_code==404 and len(kb.memories)==1
    asyncio.run(run())


@pytest.mark.parametrize('change',['candidate','memory'])
def test_recovery_refuses_changed_versions_without_writes(lab,change):
    kb,candidate,body,app=lab
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            with patch.object(kb,'save_memory_candidates',return_value=False):
                data=(await client.post('/api/kb/candidates/confirm',json=body)).json()
            if change=='candidate':
                kb.update_memory_candidate(candidate['id'],{'content':'另一份更改后的候选。'},expected_version=body['expected_version'])
            else:
                memory=kb.memories[0]
                kb.update_memory(memory['id'],{'content':'另一份更改后的已存正文。'},expected_version=record_version(memory))
            with patch.object(kb,'add_memory',side_effect=AssertionError('must not write')):
                repaired=await client.post('/api/kb/candidates/reconcile',json={'operation_id':data['operation']['operation_id']})
            assert repaired.status_code==200 and repaired.json()['candidate_status_pending']
            assert kb._find_memory_candidate(candidate['id'])['status']=='pending' and len(kb.memories)==1
    asyncio.run(run())


def test_unknown_result_is_queryable_and_not_reexecuted(lab):
    kb,candidate,body,app=lab
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            with patch.object(kb,'confirm_memory_candidate',side_effect=OSError('private diagnostic')) as raw:
                first=await client.post('/api/kb/candidates/confirm',json=body)
                second=await client.post('/api/kb/candidates/confirm',json=body)
            assert first.status_code==second.status_code==503 and raw.call_count==1
            data=first.json()
            assert not data['operation']['committed'] and 'private diagnostic' not in first.text
            operation=data['operation']['operation_id']
            listed=(await client.get('/api/kb/candidates')).json()['candidates'][0]
            assert listed['confirmation_operation']=={'operation_id':operation,'committed':False}
            assert (await client.get('/api/kb/write-result',params={'operation_id':operation})).json()['items'][0]['state']=='unknown'
            assert not kb.memories
    asyncio.run(run())


def test_missing_and_stale_versions_reject_without_creating_memory(lab):
    kb,candidate,body,app=lab
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            missing=await client.post('/api/kb/candidates/confirm',json={'id':candidate['id']})
            assert missing.status_code==400
            kb.update_memory_candidate(candidate['id'],{'content':'已经在另一端更改。'},expected_version=body['expected_version'])
            stale=await client.post('/api/kb/candidates/confirm',json=body)
            assert stale.status_code==409 and not kb.memories
    asyncio.run(run())


def test_changed_plan_cannot_reconcile_a_different_submitted_body(lab):
    kb,candidate,body,app=lab
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            with patch.object(kb,'save_memory_candidates',return_value=False):
                first=await client.post('/api/kb/candidates/confirm',json=body)
            replacement={**body,'updates':{'content':'这是一份不同的确认正文。'}}
            changed=await client.post('/api/kb/candidates/confirm',json=replacement)
            assert first.status_code==200 and changed.status_code==409
            assert len(kb.memories)==1 and kb.memories[0]['content']==body['updates']['content']
            assert kb._find_memory_candidate(candidate['id'])['status']=='pending'
    asyncio.run(run())


def test_concurrent_confirmation_claims_one_operation(lab):
    kb,candidate,body,app=lab
    start=threading.Barrier(2)
    def run():
        start.wait(timeout=5)
        with bind_origin(ingress('web')):
            return confirm(kb,body['id'],body['updates'],body['expected_version'])
    with patch.object(kb,'confirm_memory_candidate',wraps=kb.confirm_memory_candidate) as raw:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=[future.result(timeout=10) for future in [pool.submit(run),pool.submit(run)]]
    assert raw.call_count==1 and len(kb.memories)==1
    assert results[0][0].id==results[1][0].id


def test_old_pending_confirmation_recovers_without_another_memory(lab):
    kb,candidate,body,app=lab
    with patch.object(kb,'save_memory_candidates',return_value=False),pytest.raises(CandidateStateSaveError):
        kb.confirm_memory_candidate(body['id'],body['updates'],expected_version=body['expected_version'])
    kb.memories[0]['source_detail'].pop('candidate_confirmation_plan')
    kb.save_memories()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            with patch.object(kb,'add_memory',side_effect=AssertionError('old commit must be kept')):
                response=await client.post('/api/kb/candidates/confirm',json=body)
            assert response.status_code==200 and response.json()['operation']['committed']
            assert kb._find_memory_candidate(candidate['id'])['status']=='confirmed' and len(kb.memories)==1
    asyncio.run(run())


def test_candidate_plan_copies_caller_lists_before_storage(lab):
    kb,candidate,body,app=lab
    updates={**body['updates'],'tags':['original-tag']}
    matcher=kb._has_matching_memory_content
    def mutate_caller(*args,**kwargs):
        updates['tags'].append('late-injected-tag')
        return matcher(*args,**kwargs)
    with patch.object(kb,'_has_matching_memory_content',side_effect=mutate_caller):
        result=kb.confirm_memory_candidate(candidate['id'],updates,expected_version=body['expected_version'])
    assert 'original-tag' in result['memory']['tags']
    assert 'late-injected-tag' not in result['memory']['tags']
