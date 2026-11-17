import asyncio
from contextvars import copy_context
import json
from unittest.mock import patch

import httpx
import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.memory_use import MemoryUseState, MemoryUseChanged, checkpoint_use_current
from saya_app.services.qq_context_links import web_history_source, QQContextLinks
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services import qq_delivery
from saya_app.services.conversation import conversation_service
from test_qq_chat_runtime import server
from test_qq_task_scheduler import make, submit
from test_qq_delivery import event, config
from saya_app.services.qq_bridge import normalize_event


def test_epoch_survives_restart_and_fences_thread_copies(tmp_path):
    state = MemoryUseState(tmp_path)
    assert state.snapshot() == '0'
    with state.bind():
        copied = copy_context()
        checkpoint_use_current()
    copied.run(checkpoint_use_current)  # Same-era deferred work remains valid.
    next_epoch = state.advance()
    assert MemoryUseState(tmp_path).snapshot() == next_epoch
    with pytest.raises(MemoryUseChanged): copied.run(checkpoint_use_current)
    assert state.project_entries([{'content':'old'}, {'content':'new', 'memory_use_epoch':next_epoch}]) == [
        {'content':'new', 'memory_use_epoch':next_epoch}]


def test_missing_or_corrupt_existing_epoch_does_not_restore_legacy_context(tmp_path):
    state=MemoryUseState(tmp_path);state.advance()
    assert state.snapshot()!='0'
    state.path.unlink()
    with pytest.raises(MemoryUseChanged):state.snapshot()
    state.path.write_text('{"version":1,"epoch":"invalid"}',encoding='utf-8')
    with pytest.raises(MemoryUseChanged):state.snapshot()


def test_failed_edit_persistence_still_invalidates_old_context(tmp_path):
    kb = KnowledgeBase(str(tmp_path/'kb'))
    memory = kb.add_memory('旧偏好', source='manual')
    with kb.memory_use.bind():
        with patch.object(kb, 'save_memories', return_value=False):
            assert kb.update_memory(memory['id'], {'content':'未保存修改'}) is False
        with pytest.raises(MemoryUseChanged): checkpoint_use_current()
    assert kb._find_memory(memory['id'])['content'] == '旧偏好'


def test_chat_files_retained_while_cross_end_source_drops_old_era(tmp_path):
    manager = MemoryManager(str(tmp_path/'chats')); state = MemoryUseState(tmp_path)
    session = 'session_fixture.jsonl'
    manager.save_message('ai', 'OLD_SOURCE', session_id=session)
    manager.save_message('user', 'ORIGINAL_USER', session_id=session)
    path = manager._safe_session_path(session); before = open(path, 'rb').read()
    source = web_history_source(manager, state)
    assert b'OLD_SOURCE' in source(session)
    state.advance()
    assert b'OLD_SOURCE' not in source(session)
    assert b'ORIGINAL_USER' in source(session)
    assert open(path, 'rb').read() == before
    with state.bind(): manager.save_message('user','NEW_SOURCE',session_id=session)
    assert b'NEW_SOURCE' in source(session)


def test_scope_reset_freezes_pending_but_preserves_sent_receipts(tmp_path):
    kb = KnowledgeBase(str(tmp_path/'kb')); bridge = make(tmp_path/'bridge',None)
    bridge.store.initialize(7); service = QQMemoryManagement(bridge,kb)
    msg,_ = normalize_event(event('长问题'),config(shared_memory=True));scope=msg.scope+':shared'
    result = qq_delivery.create(bridge.store,msg,scope,'旧回答。'*80,100,memory_epoch='0')
    with bridge.store.connect() as db:
        db.execute("update qq_delivery_parts set state='sent',receipt='99' where result_id=? and seq=0",(result,))
    memory = kb.add_memory('旧偏好',source='manual')
    kb.update_memory(memory['id'],{'content':'新偏好'})
    history, epoch = service.prepare_scope(scope,['Doctor: old','Saya: old derived'])
    assert history == ['Doctor: old'] and epoch != '0'
    assert not service.valid_delivery(scope,result,epoch)
    _,parts=qq_delivery.lookup(bridge.store,scope,result)
    assert parts[0]['state']=='sent' and parts[0]['receipt']=='99'
    assert all(p['state']!='pending' for p in parts)
    assert '长问题' not in qq_delivery.topics(bridge.store,scope)


@pytest.mark.parametrize('legacy',[False,True])
def test_edit_during_real_bridge_send_stops_remaining_old_parts(tmp_path,legacy):
    async def run():
        from test_qq_delivery import Client
        kb=KnowledgeBase(str(tmp_path/'kb'));memory=kb.add_memory('旧偏好',source='manual')
        class EditingClient(Client):
            async def send_part(self,message,text,**kwargs):
                result=await super().send_part(message,text,**kwargs)
                if len(self.sent)==1:kb.update_memory(memory['id'],{'content':'新偏好'})
                return result
        async def responder(*args): return 'OLD_DERIVED。'*500
        bridge=make(tmp_path/'bridge',responder,client=EditingClient(),delivery_mode='legacy' if legacy else 'chat')
        await bridge.start();bridge.memory_management=QQMemoryManagement(bridge,kb)
        try:
            submit(bridge,'长问题');await bridge.queue.join()
            assert sum('OLD_DERIVED' in item[1] for item in bridge.client.sent)==1
            assert not bridge.store.conversation('10000:private:20000:shared')[0]
        finally:await bridge.stop()
    asyncio.run(run())


def test_actual_web_generator_excludes_old_history_and_fences_late_answer(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    memory=kb.add_memory('旧偏好',source='manual');session='session_fixture.jsonl'
    manager.save_message('user','OLD_USER_HISTORY',session_id=session)
    manager.save_message('ai','OLD_DERIVED_HISTORY',session_id=session)
    kb.update_memory(memory['id'],{'content':'新偏好'})
    captured=[]
    async def no_candidates(*args,**kw):return []
    async def run():
        async def model(req):
            captured.append(json.loads(req.content))
            kb.update_memory(memory['id'],{'content':'再次更新'})
            data={'choices':[{'delta':{'content':'LATE_DERIVED_ANSWER'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server,'_get_api_httpx_client',lambda:client):
                response=await server._chat_stream_single(server.ChatWithMotion(user_input='你好',session_id=session,
                    model='api',api_key='fake',api_url='http://mock/v1',api_model_name='test',memory_write_enabled=False))
                output=''.join([x.decode() if isinstance(x,bytes) else x async for x in response.body_iterator])
        text=json.dumps(captured)
        assert 'OLD_DERIVED_HISTORY' not in text and 'OLD_USER_HISTORY' in text
        assert 'LATE_DERIVED_ANSWER' not in output
        assert 'memory_context_changed' in output
        assert 'LATE_DERIVED_ANSWER' not in str(manager.load_session_full(session,make_current=False))
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'save_credentials',lambda **kw:None)):
        asyncio.run(run())
