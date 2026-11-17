import asyncio
from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_tasks import current_guard, QQTaskGuard
from test_qq_chat_runtime import server
from test_qq_task_scheduler import make, submit, until
from test_qq_task_memory import fixture
from test_qq_delivery import Client


def test_real_generator_cancel_blocks_late_save_and_preserves_parallel_web(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    async def no_candidates(*args,**kwargs):return []
    async def run():
        qq_entered,qq_release=asyncio.Event(),asyncio.Event()
        web_entered,web_release=asyncio.Event(),asyncio.Event()
        async def model(request):
            is_web=b'WEB_CANARY' in request.content
            (web_entered if is_web else qq_entered).set()
            await (web_release if is_web else qq_release).wait()
            data={'choices':[{'delta':{'content':'web answer' if is_web else 'late QQ answer'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                wire=Client();bridge=make(tmp_path/'bridge',None,wire)
                bridge.responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:{
                    'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test-model'})
                await bridge.start()
                async def web():
                    response=await server._chat_stream_single(server.ChatWithMotion(user_input='WEB_CANARY',temporary_chat=True,
                        model='api',api_key='fake',api_url='http://mock/v1',agent_mode=False,recall_mode='off'))
                    return ''.join([chunk.decode() if isinstance(chunk,bytes) else chunk async for chunk in response.body_iterator])
                web_task=None
                try:
                    submit(bridge,'请记住：我喜欢清淡的绿茶。',1)
                    await asyncio.wait_for(qq_entered.wait(),3)
                    web_task=asyncio.create_task(web())
                    await asyncio.sleep(0)
                    submit(bridge,'/cancel',2);await bridge.task_controls.join()
                    qq_release.set();await bridge.queue.join();await until(lambda:not bridge._task_workers)
                    assert not any('late QQ answer' in item[1] for item in wire.sent)
                    assert not any('绿茶' in str(m.get('content')) for m in kb.memories)
                    assert not api.is_closed and not web_task.done()
                    await asyncio.wait_for(web_entered.wait(),3)
                    web_release.set();assert 'web answer' in await web_task
                    task=bridge.task_registry.list_scope('10000:private:20000:shared')[0]
                    assert task['state']=='cancelled'
                finally:
                    qq_release.set();web_release.set()
                    if web_task:await asyncio.gather(web_task,return_exceptions=True)
                    await bridge.stop()
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
        patch.object(server,'refresh_daily_activity_memory',lambda:None),
        patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
        patch.object(conversation_service,'save_credentials',lambda **kwargs:None),
        patch.object(server.companion_router,'check_conflict_for_memory',return_value=None)):
        asyncio.run(run())


def test_deferred_qq_guard_is_private_in_memory_and_never_replayed_unbound(server,tmp_path):
    async def run():
        (tmp_path/'task').mkdir()
        registry,task,guard,_=fixture(tmp_path/'task')
        queue=[];path=tmp_path/'deferred.json'
        with (patch.object(server,'_deferred_triage_queue',queue),patch.object(server,'_DEFERRED_TRIAGE_PATH',str(path)),
              patch.object(server,'_ensure_deferred_triage_drainer',lambda loop:None)):
            with guard.bind():
                future=server._enqueue_deferred_triage({'request_id':'qq-req','user_text':'synthetic',
                    'enqueued_at':datetime.now().isoformat(),'session_id':'session_qq_fixture.jsonl'})
            assert queue[0]['qq_guard'] is guard
            saved=json.loads(path.read_text('utf-8'))
            assert saved['jobs'][0]['qq_task_id']==task['id']
            assert 'qq_guard' not in path.read_text('utf-8')
            saved['jobs'].append({'request_id':'web-req','user_text':'web synthetic','enqueued_at':datetime.now().isoformat()})
            path.write_text(json.dumps(saved),encoding='utf-8')
            server._deferred_triage_jobs_load()
            assert len(queue)==1 and queue[0]['job']['request_id']=='web-req'
            future.cancel()
    asyncio.run(run())
