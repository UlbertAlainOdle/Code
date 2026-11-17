"""Real shared generator with local mock model; no actual QQ acceptance."""
import asyncio,json
from unittest.mock import patch

import httpx
import pytest

from core_services import KnowledgeBase,MemoryManager
from saya_app.services.conversation import conversation_service
from test_qq_chat_runtime import server


@pytest.fixture
def setup(server,tmp_path,monkeypatch):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    manager=MemoryManager(str(tmp_path/'history'))
    monkeypatch.setattr(server,'kb_system',kb)
    monkeypatch.setattr(server,'memory_manager',manager)
    monkeypatch.setattr(server,'refresh_daily_activity_memory',lambda:None)
    monkeypatch.setattr(server.companion_router,'check_conflict_for_memory',lambda *a:None)
    monkeypatch.setattr(conversation_service,'save_credentials',lambda **kw:None)
    async def no_candidates(*a,**kw):raise AssertionError('handled mutation must not become a candidate')
    monkeypatch.setattr(server,'maybe_create_autonomous_memory_candidates',no_candidates)
    return kb,manager


@pytest.mark.parametrize('kind',['correct','forget','missing','failed'])
def test_web_model_receives_actual_mutation_result_before_answer(server,setup,monkeypatch,kind):
    kb,manager=setup
    item=kb.capture_explicit_memory('记住：我喜欢咖啡。')
    before=kb.memory_use.snapshot()
    text='更正：我不再喜欢咖啡，改喝清茶。' if kind=='correct' else '忘掉'+('从未存在的虚构标记' if kind=='missing' else '我喜欢咖啡')
    if kind=='failed':
        monkeypatch.setattr(kb,'_process_memory_write_v4_impl',lambda *a,**kw: {'status':'error'})
    async def run():
        def model(request):
            prompt='\n'.join(str(x.get('content','')) for x in json.loads(request.content)['messages'])
            assert '[Host memory write result]' in prompt
            assert 'Never promise that no traces remain.' in prompt
            assert 'do not restate the forgotten content from history' in prompt
            receipt=json.loads(prompt.split('[Host memory write result]\n',1)[1].split('\n',1)[0])
            assert receipt['committed'] is (kind in {'correct','forget'})
            if kind=='correct':
                assert '清茶' in kb._find_memory(item['id'])['content']
                assert '清茶' in receipt['saved_content']
            elif kind=='forget':
                assert kb._find_memory(item['id']) is None
                assert receipt['items'][0]['forgotten']==[item['id']]
            elif kind=='failed':assert receipt['items'][0]['state']=='unknown'
            data={'choices':[{'delta':{'content':'以本次实际操作回执为准。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            monkeypatch.setattr(server,'_get_api_httpx_client',lambda:client)
            req=server.ChatWithMotion(user_input=text,request_id='a'*32,model='api',api_key='fixture',
                api_url='http://mock/v1',api_model_name='fixture',recall_mode='off',agent_mode=False)
            response=await server._chat_stream_single(req)
            chunks=[chunk async for chunk in response.body_iterator]
        output=''.join(chunks)
        assert 'memory_context_changed' not in output and 'event: answer_complete' in output
        assert output.index('event: memory_candidates')<output.index('event: answer_complete')
        assert before!=kb.memory_use.snapshot()
        saved_epoch=kb.memory_use.snapshot()
        with patch.object(kb,'_process_memory_write_v4_impl',side_effect=AssertionError('duplicate mutation')):
            response=await server._chat_stream_single(req)
            repeat=''.join([chunk async for chunk in response.body_iterator])
        assert 'event: answer_complete' in repeat and kb.memory_use.snapshot()==saved_epoch
        rows=manager.load_session_full('session_request_'+req.request_id+'.jsonl',make_current=False)
        user=next(row for row in rows if row['role']=='user')
        assert user['memory_confirm']['operation']['committed'] is (kind in {'correct','forget'})
    asyncio.run(run())


def test_cancel_after_mutation_receipt_keeps_commit_and_invalidates_old_context(server,setup,monkeypatch):
    kb,manager=setup
    item=kb.capture_explicit_memory('记住：虚构回执测试书签。')
    async def run():
        req=server.ChatWithMotion(user_input='忘掉虚构回执测试书签',request_id='b'*32,model='api',
            api_key='fixture',api_url='http://mock/v1',agent_mode=False)
        with kb.memory_use.bind():
            # A separately captured old reader remains stale after the mutation.
            from contextvars import copy_context
            old=copy_context()
        response=await server._chat_stream_single(req)
        receipt=None
        async for chunk in response.body_iterator:
            if chunk.startswith('event: memory_candidates'):
                receipt=json.loads(chunk.split('data: ',1)[1])['operation'];break
        await response.body_iterator.aclose()
        assert receipt['committed'] and kb._find_memory(item['id']) is None
        from saya_app.services.memory_use import checkpoint_use_current,MemoryUseChanged
        with pytest.raises(MemoryUseChanged):old.run(checkpoint_use_current)
        rows=manager.load_session_full('session_request_'+req.request_id+'.jsonl',make_current=False)
        assert rows[0]['memory_confirm']['operation']['operation_id']==receipt['operation_id']
    asyncio.run(run())


def test_native_mutation_invalidates_other_readers_and_reports_failed_projection(setup,monkeypatch):
    kb,_=setup
    item=kb.capture_explicit_memory('记住：虚构旧投影条目。')
    from contextvars import copy_context
    from saya_app.services.memory_use import checkpoint_use_current,MemoryUseChanged
    with kb.memory_use.bind():old=copy_context()
    monkeypatch.setattr(kb,'_mirror_v4_write_to_legacy',lambda response:False)
    result=kb.process_memory_write_v4('忘掉虚构旧投影条目',message_id='partial-projection')
    assert result['operation_state']==result['status']=='partial'
    assert result['projection_pending']==[item['id']] and item['id'] in kb._pending_v4_projections()
    assert kb.memory_v4_store.is_tombstoned(item['id'])
    with pytest.raises(MemoryUseChanged):old.run(checkpoint_use_current)
    epoch=kb.memory_use.snapshot()
    replay=kb.process_memory_write_v4('忘掉虚构旧投影条目',message_id='partial-projection')
    assert replay['replayed'] and replay['projection_pending']==[item['id']]
    assert kb.memory_use.snapshot()==epoch
    rejected=kb.process_memory_write_v4('忘掉虚构旧投影条目',message_id='different-operation')
    assert rejected['status']=='rejected' and item['id'] in kb._pending_v4_projections()


@pytest.mark.parametrize('text',['更正：我不再喜欢虚构的旧茶杯。','忘掉一个没有定位的虚构条目'])
def test_unhandled_qq_mutation_never_falls_through_to_model_or_old_capture(text):
    from saya_app.services.qq_bridge import QQMessage
    from saya_app.services.qq_chat import QQChatResponder
    from test_qq_delivery import config
    async def unexpected(*args,**kwargs):raise AssertionError('unhandled mutation must not enter model')
    responder=QQChatResponder(config(shared_memory=True),None,unexpected,lambda:'api',lambda:{})
    answer=asyncio.run(responder(QQMessage('event','10000:private:20000','10000','20000','',
        'message',text,True),[],'session_synthetic.jsonl'))
    assert '本次没有修改' in answer
