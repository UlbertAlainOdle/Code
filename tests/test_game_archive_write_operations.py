import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from core_services import KnowledgeBase,MemoryManager
from saya_app.services.conversation import ConversationService
from saya_app.services.chat_memory_writes import journal
from saya_app.services.game_archive_writes import path_for,scope_for


@pytest.fixture
def lab(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'knowledge_base'));manager=MemoryManager(str(tmp_path/'history'))
    service=ConversationService();service._kb=kb;service._memory_manager=manager
    service._ai_archive_summary=lambda items:'虚构实验：红石灯完成连接。'
    sid='session_mc_lab.jsonl'
    service._record('user','连接虚构红石灯。',{},sid)
    service._record('ai','虚构红石灯已连接。',{},sid)
    assert len(manager.load_session_full(sid,make_current=False))==2
    return service,kb,manager,sid


def test_success_and_restart_retry_preserve_single_fact(lab):
    service,kb,manager,sid=lab
    original=path_for(service,sid).read_bytes()
    first=service.archive_game_session(sid)
    assert first['archived'] and first['file_moved_to_archived'] and len(kb.memories)==1
    assert path_for(service,sid,sid).read_bytes()==original
    restarted=ConversationService();restarted._kb=kb;restarted._memory_manager=manager
    with patch.object(restarted,'_ai_archive_summary',side_effect=AssertionError('repeat model')):
        second=restarted.archive_game_session(sid)
    assert second['memory_id']==first['memory_id'] and len(kb.memories)==1


def test_concurrent_services_share_original_prepared_operation(lab):
    service,kb,manager,sid=lab;entered=threading.Event();release=threading.Event()
    def model(items):entered.set();assert release.wait(5);return '虚构总结。'
    other=ConversationService();other._kb=kb;other._memory_manager=manager
    with patch.object(service,'_ai_archive_summary',model),ThreadPoolExecutor(2) as pool:
        first=pool.submit(service.archive_game_session,sid)
        assert entered.wait(5)
        try:
            with patch.object(other,'_ai_archive_summary',side_effect=AssertionError('second model')):
                second=pool.submit(other.archive_game_session,sid).result(5)
            assert second['status']=='pending' and not second['archived']
        finally:release.set()
        result=first.result(5)
    assert result['operation']['operation_id']==second['operation']['operation_id'] and len(kb.memories)==1


@pytest.mark.parametrize('changed',['source','epoch'])
def test_source_or_permission_changes_during_model_prevent_commit(lab,changed):
    service,kb,_,sid=lab
    def model(items):
        if changed=='source':service._record('user','后来的新消息。',{},sid)
        else:
            with kb._memory_write_lock:kb.memory_use.advance()
        return '已经过期的摘要。'
    with patch.object(service,'_ai_archive_summary',model):result=service.archive_game_session(sid)
    assert result['status']=='invalid' and not result['archived'] and not kb.memories
    assert path_for(service,sid).exists()


def test_file_move_failure_then_retry_only_moves_file(lab):
    service,kb,_,sid=lab
    with patch.object(Path,'rename',side_effect=OSError('busy')):first=service.archive_game_session(sid)
    assert first['archived'] and not first['file_moved_to_archived'] and path_for(service,sid).exists()
    with patch.object(kb,'add_memory',side_effect=AssertionError('duplicate fact')),patch.object(service,'_ai_archive_summary',side_effect=AssertionError('repeat model')):
        second=service.archive_game_session(sid)
    assert second['memory_id']==first['memory_id'] and second['file_moved_to_archived'] and len(kb.memories)==1


@pytest.mark.parametrize('changed',['source','memory','destination'])
def test_file_recovery_refuses_changed_source_target_or_existing_destination(lab,changed):
    service,kb,_,sid=lab
    with patch.object(Path,'rename',side_effect=OSError('busy')):first=service.archive_game_session(sid)
    if changed=='source':service._record('user','后续新消息。',{},sid)
    elif changed=='memory':kb.update_memory(first['memory_id'],{'content':'后来的人工更正。'})
    else:path_for(service,sid,sid).write_text('do not overwrite',encoding='utf-8')
    before=path_for(service,sid).read_bytes()
    with patch.object(kb,'add_memory',side_effect=AssertionError('duplicate fact')),patch.object(service,'_ai_archive_summary',side_effect=AssertionError('repeat model')):
        second=service.archive_game_session(sid)
    assert second['recovery_blocked'] and not second['file_moved_to_archived'] and len(kb.memories)==1
    assert path_for(service,sid).read_bytes()==before
    if changed=='destination':assert path_for(service,sid,sid).read_text()=='do not overwrite'


def test_existing_archive_not_overwritten(lab):
    service,_,_,sid=lab;target=path_for(service,sid,sid);target.parent.mkdir()
    target.write_text('original archive',encoding='utf-8')
    result=service.archive_game_session(sid)
    assert result['file_moved_to_archived'] and target.read_text()=='original archive'
    assert len(list(target.parent.glob('*.jsonl')))==2


def test_move_completed_then_result_persistence_failed_recovers_from_file_hash(lab):
    service,kb,_,sid=lab;ledger=journal(kb);actual=ledger._record
    def fail(operation,items):
        if items[0].get('file_pending') is False:raise OSError('after move')
        return actual(operation,items)
    with patch.object(ledger,'_record',fail),pytest.raises(OSError):service.archive_game_session(sid)
    assert not path_for(service,sid).exists() and len(kb.memories)==1
    with patch.object(kb,'add_memory',side_effect=AssertionError('repeat')),patch.object(service,'_ai_archive_summary',side_effect=AssertionError('repeat')):
        result=service.archive_game_session(sid)
    assert result['file_moved_to_archived'] and len(kb.memories)==1


def test_model_cancellation_never_archives_or_adds_fact(lab):
    from saya_app.services.qq_tasks import TaskCancelled
    service,kb,_,sid=lab
    with patch.object(service,'_ai_archive_summary',side_effect=TaskCancelled('cancel')),pytest.raises(TaskCancelled):
        service.archive_game_session(sid)
    assert path_for(service,sid).exists() and not kb.memories


def test_game_archive_requires_compatible_rollback(lab):
    from saya_app.services.qq_rollback import memory_rollback_requirements
    service,kb,_,sid=lab;service.archive_game_session(sid)
    assert 'game_archive_write_operations_v1' in memory_rollback_requirements(Path(kb.base_dir).parent)


def test_unknown_after_storage_survives_expiry_and_cannot_repeat(lab):
    service,kb,_,sid=lab;actual=kb.add_memory
    def fail(*args,**kwargs):actual(*args,**kwargs);raise OSError('after commit')
    with patch.object(kb,'add_memory',fail):first=service.archive_game_session(sid)
    assert first['status']=='unknown' and len(kb.memories)==1
    ledger=journal(kb);ledger.clock=lambda:10**12
    ledger.run('b'*32,'other','other','capture',[{}],verify=lambda db:True,valid=lambda p:True,write=lambda p:{'state':'not_saved'},retention=1)
    with patch.object(kb,'add_memory',side_effect=AssertionError('repeat')),patch.object(service,'_ai_archive_summary',side_effect=AssertionError('repeat')):
        result=service.archive_game_session(sid)
    assert result['status']=='unknown' and result['operation']['operation_id']==first['operation']['operation_id']
    assert path_for(service,sid).exists() and len(kb.memories)==1


def test_current_history_projection_excludes_old_assistant_and_receipts(lab):
    service,kb,manager,sid=lab
    with kb._memory_write_lock:kb.memory_use.advance()
    service._record('ai','原生控制回执。',{'content_kind':'control_receipt'},sid)
    service._record('ai','目前有效的新回答。',{},sid)
    captured=[]
    with patch.object(service,'_ai_archive_summary',lambda items:captured.extend(items) or '当前摘要。'):
        result=service.archive_game_session(sid)
    assert result['archived']
    assert [x['content'] for x in captured]==['连接虚构红石灯。','目前有效的新回答。']
    assert captured[-1]['memory_use_epoch']==kb.memory_use.snapshot()


def test_result_query_is_read_only_scoped_and_without_summary(lab,monkeypatch):
    service,kb,_,sid=lab
    with patch.object(Path,'rename',side_effect=OSError('busy')):first=service.archive_game_session(sid)
    from saya_app.services import conversation
    from saya_app.routers.bridge import router
    monkeypatch.setattr(conversation,'conversation_service',service)
    app=FastAPI();app.include_router(router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://lab') as client:
            url='/api/bridge/minecraft/chat/archive-result'
            query={'session':sid,'operation_id':first['operation']['operation_id']}
            reply=await client.get(url,params=query)
            assert reply.status_code==200,reply.text
            assert reply.json()['committed'] and '红石灯' not in reply.text
            assert (await client.get(url,params={**query,'session':'session_mc_other.jsonl'})).status_code==404
            assert (await client.get(url,params={**query,'session':'../x.jsonl'})).status_code==400
    with patch.object(Path,'rename',side_effect=AssertionError('GET moved file')):asyncio.run(run())
    assert path_for(service,sid).exists() and len(kb.memories)==1
