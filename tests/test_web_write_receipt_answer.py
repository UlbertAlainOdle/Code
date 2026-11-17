import asyncio
import json

import httpx
import pytest

from core_services import KnowledgeBase,MemoryManager
from saya_app.services.conversation import conversation_service
from test_qq_chat_runtime import server


def test_read_only_query_does_not_look_like_a_failed_write(server,tmp_path):
    from r4_web_lab import web_lab
    from saya_app.services.chat_memory_writes import prompt_receipt
    kb=KnowledgeBase(str(tmp_path/'kb'))
    kb.update_settings({'embedding_enabled':False,'memory_core_limit':0})
    kb.capture_explicit_memory('记住：虚构的青岚计划采用离线处理。')
    def model(request):
        messages=json.loads(request.content)['messages']
        prompt=json.dumps(messages,ensure_ascii=False)
        assert '虚构的青岚计划采用离线处理' in prompt
        assert '[Host memory write result]' not in prompt
        data={'choices':[{'delta':{'content':'记录里采用离线处理。'},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
    async def run():
        with web_lab(server,tmp_path/'web',kb) as lab:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
                result=await lab.turn(client,'青岚计划现在采用什么处理方式？')
        assert '离线' in result['answer']
    asyncio.run(run())
    disabled=prompt_receipt(kb,None,False,request_text='记住：虚构禁写样例。')
    assert '[Host memory write result]' in disabled and '"saving_enabled": false' in disabled


@pytest.mark.parametrize('failed',[False,True])
def test_model_and_ui_receive_the_same_host_result_before_answer(server,tmp_path,monkeypatch,failed):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    manager=MemoryManager(str(tmp_path/'history'))
    monkeypatch.setattr(server,'kb_system',kb)
    monkeypatch.setattr(server,'memory_manager',manager)
    monkeypatch.setattr(server,'refresh_daily_activity_memory',lambda:None)
    monkeypatch.setattr(server.companion_router,'check_conflict_for_memory',lambda *args:None)
    monkeypatch.setattr(conversation_service,'save_credentials',lambda **kwargs:None)
    async def no_candidates(*args,**kwargs):
        raise AssertionError('a processed save must not become another candidate')
    monkeypatch.setattr(server,'maybe_create_autonomous_memory_candidates',no_candidates)
    if failed:
        def fail(*args,**kwargs):
            raise OSError('private failure detail')
        monkeypatch.setattr(kb,'_capture_explicit_memory_impl',fail)
    captured=[]
    async def run():
        def model(request):
            assert len(kb.memories)==(0 if failed else 1)
            messages=json.loads(request.content)['messages']
            prompt='\n'.join(str(message['content']) for message in messages)
            assert '[Host memory write result]' in prompt
            assert ('"committed": false' if failed else '"committed": true') in prompt
            assert 'private failure detail' not in prompt
            captured.append(prompt)
            data={'choices':[{'delta':{'content':'以这次保存的实际回执为准。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            monkeypatch.setattr(server,'_get_api_httpx_client',lambda:client)
            req=server.ChatWithMotion(user_input='记住：虚构书签名叫浅蓝签。',request_id='a'*32,
                model='api',api_key='fixture',api_url='http://mock/v1',api_model_name='fixture',
                recall_mode='off',agent_mode=False)
            response=await server._chat_stream_single(req)
            chunks=[]
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk,bytes) else chunk)
        text=''.join(chunks)
        assert text.index('event: memory_candidates')<text.index('event: answer_complete')
        receipts=[json.loads(part.split('data: ',1)[1])['operation'] for part in text.split('\n\n')
                  if part.startswith('event: memory_candidates')]
        assert receipts and all(result['committed'] is (not failed) for result in receipts)
        assert len({result['operation_id'] for result in receipts})==1
        session='session_request_'+req.request_id+'.jsonl'
        user=next(item for item in manager.load_session_full(session,make_current=False) if item['role']=='user')
        assert user['memory_confirm']['operation']['operation_id']==receipts[0]['operation_id']
        assert captured
    asyncio.run(run())


def test_stopping_reply_after_host_receipt_keeps_queryable_commit(server,tmp_path,monkeypatch):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    manager=MemoryManager(str(tmp_path/'history'))
    monkeypatch.setattr(server,'kb_system',kb)
    monkeypatch.setattr(server,'memory_manager',manager)
    monkeypatch.setattr(conversation_service,'save_credentials',lambda **kwargs:None)
    async def run():
        req=server.ChatWithMotion(user_input='记住：虚构笔记本名叫晴杉册。',request_id='b'*32,
            model='api',api_key='fixture',api_url='http://mock/v1',api_model_name='fixture',agent_mode=False)
        response=await server._chat_stream_single(req)
        result=None
        async for chunk in response.body_iterator:
            text=chunk.decode() if isinstance(chunk,bytes) else chunk
            if text.startswith('event: memory_candidates'):
                result=json.loads(text.split('data: ',1)[1])['operation']
                break
        await response.body_iterator.aclose()
        assert result and result['committed'] and len(kb.memories)==1
        session='session_request_'+req.request_id+'.jsonl'
        stored=manager.load_session_full(session,make_current=False)
        assert stored[0]['memory_confirm']['operation']['operation_id']==result['operation_id']
    asyncio.run(run())
