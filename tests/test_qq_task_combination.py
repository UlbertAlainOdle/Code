"""Isolated actual generator/bridge experiment; mock HTTP and QQ wire, no live acceptance."""
import asyncio
import json
from unittest.mock import patch

import httpx

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.qq_chat import QQChatResponder
from test_qq_chat_runtime import server
from test_qq_delivery import Client, event
from test_qq_task_scheduler import make, submit, until


def test_web_generation_group_cancel_and_private_pair_keep_shared_client_and_order(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    async def no_candidates(*args,**kwargs):return []
    async def run():
        web_entered,web_release=asyncio.Event(),asyncio.Event();calls=[]
        async def model(request):
            messages=json.loads(request.content)['messages']
            last=next(m['content'] for m in reversed(messages) if m['role']=='user')
            marker=next(k for k in ['PRIVATE_TWO','PRIVATE_ONE','GROUP_JOB','WEB_JOB'] if k in str(last))
            calls.append(marker)
            if marker=='WEB_JOB':web_entered.set();await web_release.wait()
            data={'choices':[{'delta':{'content':marker+'_ANSWER'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                wire=Client();bridge=make(tmp_path/'bridge',None,wire)
                bridge.responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:{
                    'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test-model'})
                await bridge.start()
                async def web():
                    response=await server._chat_stream_single(server.ChatWithMotion(user_input='WEB_JOB',temporary_chat=True,
                        model='api',api_key='fake',api_url='http://mock/v1',agent_mode=False,recall_mode='off'))
                    return ''.join([c.decode() if isinstance(c,bytes) else c async for c in response.body_iterator])
                def group(text,mid):
                    return bridge.submit_signed(json.dumps(event(text,mid,group_id=90000)).encode(),'Bearer inbound')
                web_task=asyncio.create_task(web())
                try:
                    await asyncio.wait_for(web_entered.wait(),5)
                    assert group('GROUP_JOB',1)[0]=='queued'
                    assert submit(bridge,'PRIVATE_ONE',2)[0]=='queued'
                    assert submit(bridge,'PRIVATE_TWO',3)[0]=='queued'
                    await until(lambda:len(bridge.task_registry.list_active())==2)
                    assert group('/status',4)[0]=='queued'
                    await asyncio.wait_for(bridge.task_controls.join(),2)
                    assert any(m.group_id=='90000' and '运行中' in text for m,text,*_ in wire.sent)
                    await asyncio.sleep(.8)
                    assert group('/help',5)[0]=='queued'
                    await asyncio.wait_for(bridge.task_controls.join(),2)
                    await asyncio.sleep(.8)
                    assert group('/cancel',6)[0]=='queued'
                    await asyncio.wait_for(bridge.task_controls.join(),2)
                    assert any(m.group_id=='90000' and '请求取消' in text for m,text,*_ in wire.sent)
                    assert not web_task.done() and not api.is_closed
                    assert calls==['WEB_JOB']  # Original shared model lock remains intact.
                    web_release.set()
                    assert 'WEB_JOB_ANSWER' in await asyncio.wait_for(web_task,5)
                    await asyncio.wait_for(bridge.queue.join(),8)
                    await until(lambda:not bridge._task_workers)
                    assert calls==['WEB_JOB','PRIVATE_ONE','PRIVATE_TWO']
                    answers=[text for m,text,*_ in wire.sent if not m.group_id and text.endswith('_ANSWER')]
                    assert answers==['PRIVATE_ONE_ANSWER','PRIVATE_TWO_ANSWER']
                    assert not any('GROUP_JOB_ANSWER' in text for _,text,*_ in wire.sent)
                    group_task=bridge.task_registry.list_scope('10000:group:90000:user:20000:isolated')[0]
                    assert group_task['state']=='cancelled'
                    assert all(t['state']=='completed' for t in bridge.task_registry.list_scope('10000:private:20000:shared'))
                    assert not api.is_closed
                finally:
                    web_release.set();await asyncio.gather(web_task,return_exceptions=True);await bridge.stop()
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'save_credentials',lambda **kwargs:None),
          patch.object(server.companion_router,'check_conflict_for_memory',return_value=None)):
        asyncio.run(run())
