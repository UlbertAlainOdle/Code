from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase
from saya_app.services.qq_memory_management import record_version


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(str(tmp_path / 'kb'))


def test_update_content_cas_rejects_old_web_or_qq_snapshot(kb):
    m = kb.add_memory('旧的偏好', source='manual')
    version = record_version(m)
    assert kb.update_memory(m['id'], {'content': '新的偏好'}, expected_version=version)
    with pytest.raises(ValueError, match='version'):
        kb.update_memory(m['id'], {'content': '陈旧页面'}, expected_version=version)
    assert kb._find_memory(m['id'])['content'] == '新的偏好'


def test_delete_confirmation_binds_content_not_only_relationships(kb):
    m = kb.add_memory('旧正文', source='manual')
    preview = kb.preview_memory_deletion([m['id']], reason='user_delete')
    assert kb.update_memory(m['id'], {'content': '修改后的正文'})
    result = kb.delete_memories([m['id']], reason='user_delete', confirmation_token=preview['confirmation_token'])
    assert result['status'] == 'confirmation_required'
    assert kb._find_memory(m['id'])['content'] == '修改后的正文'


def test_delete_content_cas_rechecked_under_storage_lock(kb):
    m = kb.add_memory('需要确认的内容', source='manual')
    version = record_version(m)
    kb.update_memory(m['id'], {'title': '新标题'})
    with pytest.raises(ValueError, match='version'):
        kb.delete_memories([m['id']], expected_versions={m['id']: version})
    assert kb._find_memory(m['id'])


def test_concurrent_candidate_confirm_and_ignore_have_single_transition(kb):
    c = kb.add_memory_candidate('并发测试偏好', title='独立候选')
    def confirm():
        return bool(kb.confirm_memory_candidate(c['id']))
    def ignore():
        return kb.ignore_memory_candidate(c['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm), pool.submit(ignore)]
        outcomes = [f.result() for f in futures]
    assert sum(outcomes) == 1
    assert kb._find_memory_candidate(c['id'])['status'] in {'confirmed', 'ignored'}
    assert kb.ignore_memory_candidate(c['id']) is False


def test_candidate_state_failure_preserves_pending_and_no_false_success(kb):
    c = kb.add_memory_candidate('候选忽略失败', title='候选')
    with patch.object(kb, 'save_memory_candidates', return_value=False):
        with pytest.raises(OSError):
            kb.ignore_memory_candidate(c['id'])
    assert kb._find_memory_candidate(c['id'])['status'] == 'pending'


def test_candidate_final_state_failure_can_reconcile_without_second_memory(kb):
    c = kb.add_memory_candidate('候选保存状态失败', title='偏好')
    with patch.object(kb, 'save_memory_candidates', return_value=False):
        with pytest.raises(OSError):
            kb.confirm_memory_candidate(c['id'])
    matches = [m for m in kb.memories if m.get('source_detail', {}).get('candidate_id') == c['id']]
    assert len(matches) == 1
    assert kb._find_memory_candidate(c['id'])['status'] == 'pending'
    result = kb.confirm_memory_candidate(c['id'])
    assert result['status'] == 'success' and result['memory']['id'] == matches[0]['id']
    assert kb.confirm_memory_candidate(c['id']) is None
    assert len([m for m in kb.memories if m.get('source_detail', {}).get('candidate_id') == c['id']]) == 1


def test_candidate_version_conflict_does_not_resolve(kb):
    c = kb.add_memory_candidate('候选初稿', title='候选')
    version = record_version(c)
    c['content'] = '候选已经更新'
    with pytest.raises(ValueError, match='version'):
        kb.confirm_memory_candidate(c['id'], expected_version=version)
    assert c['status'] == 'pending'


def test_optional_trusted_write_check_runs_inside_same_lock(kb):
    m = kb.add_memory('仍然只读', source='manual')
    def deny():
        assert kb._memory_write_lock._is_owned()
        raise PermissionError('readonly')
    with pytest.raises(PermissionError):
        kb.update_memory(m['id'], {'content': '不可写'}, write_check=deny)
    assert kb._find_memory(m['id'])['content'] == '仍然只读'


def test_web_management_returns_and_enforces_shared_content_versions(kb):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from saya_app.routers import memories as routes
    m=kb.add_memory('网页初始内容',source='manual')
    async def run():
        app=FastAPI();app.include_router(routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            listed=(await client.get('/api/kb/memories')).json()['memories']
            version=next(x['content_version'] for x in listed if x['id']==m['id'])
            body={'id':m['id'],'expected_version':version,'updates':{'content':'新版内容'}}
            assert (await client.post('/api/kb/update',json=body)).status_code==200
            assert (await client.post('/api/kb/update',json=body)).status_code==409
            assert (await client.post('/api/kb/delete',json={'id':m['id'],'expected_version':version})).status_code==409
            assert (await client.post('/api/kb/delete',json={'id':m['id'],'expected_version':'invalid'})).status_code==400
    with patch.object(routes,'kb_system',kb):asyncio_run=asyncio.run(run())


def test_version_bound_natural_candidate_consent_rejects_changed_content(kb,tmp_path):
    from saya_app.services.qq_memory_access import MemoryActor
    from saya_app.services.qq_memory_management import QQMemoryManagement
    from test_qq_task_scheduler import make
    bridge=make(tmp_path/'bridge',None);bridge.store.initialize(7);service=QQMemoryManagement(bridge,kb)
    actor=MemoryActor('10000','20000',True,True)
    c=kb.add_memory_candidate('初始候选',title='候选')
    payload,_=service.candidate_proposal(actor,c['id'])
    c['content']='另一端已经更改'
    with pytest.raises(ValueError,match='version'):
        service.confirm_candidate(actor,c['id'],payload['candidate_version'])
    assert c['status']=='pending' and not kb.memories


@pytest.mark.parametrize('changed',[False,True])
def test_real_bridge_natural_candidate_consent_is_bound_to_reviewed_version(kb,tmp_path,changed):
    import asyncio
    from types import SimpleNamespace
    from saya_app.services.qq_action_proposals import QQActionProposals
    from saya_app.services.qq_chat import QQChatResponder
    from saya_app.services.qq_memory_management import QQMemoryManagement
    from test_qq_task_scheduler import make,submit
    async def run():
        bridge=make(tmp_path/'bridge',None);await bridge.start()
        c=kb.add_memory_candidate('主人审核的初稿',title='测试偏好')
        service=QQMemoryManagement(bridge,kb);bridge.memory_management=service
        proposals=QQActionProposals(bridge.store);bridge.action_proposals=proposals
        async def generate(*args,**kwargs):
            async def stream():
                yield 'event: memory_candidates\ndata: '+json.dumps({'candidates':[c]})+'\n\n'
                yield 'event: answer_complete\ndata: {"text":"明白了。"}\n\n'
            return SimpleNamespace(body_iterator=stream())
        responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),generate,lambda:'api',
            lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test'},lambda *args:None)
        responder.memory_management=service;responder.action_proposals=proposals;responder.configuration_provider=bridge.controls
        bridge.responder=responder
        try:
            submit(bridge,'一个普通问题');await bridge.queue.join()
            assert any('要查看完整正文' in item[1] for item in bridge.client.sent)
            if changed:
                with kb._memory_write_lock:c['content']='未审核的新版本';kb.save_memory_candidates()
            submit(bridge,'同意',2);await bridge.queue.join()
            if changed:
                assert not kb.memories and any('已变化' in item[1] for item in bridge.client.sent)
            else:
                assert not kb.memories and any(c['content'] in item[1] for item in bridge.client.sent)
                submit(bridge,'记住',3);await bridge.queue.join()
                assert len(kb.memories)==1 and kb._find_memory_candidate(c['id'])['status']=='confirmed'
        finally:await bridge.stop()
    asyncio.run(run())
