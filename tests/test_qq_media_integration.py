import asyncio,json
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from PIL import Image
from core_services import KnowledgeBase,MemoryManager
from saya_app.services.qq_media import QQMedia,MediaPolicy
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.conversation import conversation_service
from test_qq_media import image_event,MediaClient,CREDS,configure
from test_qq_task_scheduler import make,until
from test_qq_group_integration import response
from test_qq_chat_runtime import server


def setup_media(tmp_path,raw):
    cache=tmp_path/'dedicated';cache.mkdir();path=cache/'image.bin';Image.new('RGB',(12,10),'red').save(path,format='PNG')
    original=dict(raw);original['message_id']=str(raw['message_id']);client=MediaClient(original,path)
    bridge=make(tmp_path/'bridge',None,client=client)
    return bridge,client,cache


def test_real_generator_routes_current_picture_to_visual_model_without_private_group_history(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    kb.add_memory('PRIVATE_MEMORY_MUST_NOT_APPEAR',source='manual')
    manager.save_message('user','PRIVATE_WEB_HISTORY_MUST_NOT_APPEAR',session_id='session_web_private.jsonl')
    captured=[]
    async def run():
        raw=image_event(group=90000);bridge,client,cache=setup_media(tmp_path,raw)
        await bridge.start();media=QQMedia(bridge,tmp_path,trusted_roots=[cache]);bridge.media=media;configure(media)
        def model(request):
            captured.append(json.loads(request.content));delta={'choices':[{'delta':{'content':'图中有红色。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(delta)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:CREDS)
                responder.media=media;bridge.responder=responder
                try:
                    assert bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound')[0]=='queued'
                    await asyncio.wait_for(bridge.queue.join(),10)
                    assert any('红色' in str(r[1]) for r in client.sent)
                finally:await bridge.stop()
    with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'refresh_daily_activity_memory',lambda:None):
        asyncio.run(run())
    assert len(captured)==1 and captured[0]['model']=='vision-fixture'
    messages=captured[0]['messages'];assert any(isinstance(m.get('content'),list) and any(p.get('type')=='image_url' for p in m['content']) for m in messages)
    serialized=json.dumps(messages,ensure_ascii=False)
    assert 'PRIVATE_MEMORY_MUST_NOT_APPEAR' not in serialized and 'PRIVATE_WEB_HISTORY_MUST_NOT_APPEAR' not in serialized
    for path in (tmp_path/'chats').rglob('*.jsonl'):
        assert 'data:image' not in path.read_text(encoding='utf-8')


def test_real_bridge_cancel_during_image_read_never_calls_model_or_sends_late_answer(tmp_path):
    async def run():
        raw=image_event();bridge,client,cache=setup_media(tmp_path,raw);called=[]
        await bridge.start();media=QQMedia(bridge,tmp_path,trusted_roots=[cache]);bridge.media=media;configure(media)
        client.entered=asyncio.Event();client.release=asyncio.Event()
        async def single(*a,**kw):called.append(1);return response('LATE_IMAGE_ANSWER')
        responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',lambda:CREDS)
        responder.media=media;bridge.responder=responder
        try:
            bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound');await asyncio.wait_for(client.entered.wait(),3)
            task=bridge.task_registry.list_scope('10000:private:20000:shared')[0]
            bridge.task_registry.cancel(task['id'],task['scope']);client.release.set()
            await asyncio.wait_for(bridge.queue.join(),4)
            assert not called and not any('LATE_IMAGE_ANSWER' in str(r[1]) for r in client.sent)
            assert bridge.task_registry.list_scope(task['scope'])[0]['state']=='cancelled'
        finally:client.release.set();await bridge.stop()
    asyncio.run(run())


def test_pure_image_default_off_receipt_does_not_invoke_model_and_text_failure_keeps_question(tmp_path):
    async def run():
        raw=image_event(text='');bridge,client,cache=setup_media(tmp_path,raw);captured=[]
        await bridge.start();media=QQMedia(bridge,tmp_path,trusted_roots=[cache]);bridge.media=media
        async def single(req,channel_turn):captured.append(req.user_input);return response('TEXT_ONLY_ANSWER')
        responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',lambda:CREDS)
        responder.media=media;bridge.responder=responder
        try:
            bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound');await bridge.queue.join()
            assert not captured and not client.calls and any('未开启' in str(r[1]) for r in client.sent)
            raw=image_event('保留这个纯文字问题',mid=2);client.raw=raw
            bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound');await bridge.queue.join()
            assert captured==['保留这个纯文字问题']
            assert any('TEXT_ONLY_ANSWER' in str(r[1]) for r in client.sent)
        finally:await bridge.stop()
    asyncio.run(run())


def test_visual_model_failure_is_a_truthful_receipt_not_a_guessed_image(tmp_path):
    async def run():
        raw=image_event();bridge,client,cache=setup_media(tmp_path,raw)
        await bridge.start();media=QQMedia(bridge,tmp_path,trusted_roots=[cache]);configure(media);bridge.media=media
        async def single(*a,**kw):
            async def body():yield 'event: error\ndata: '+json.dumps({'message':'provider rejected image'})+'\n\n'
            from starlette.responses import StreamingResponse
            return StreamingResponse(body())
        responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',lambda:CREDS)
        responder.media=media;bridge.responder=responder
        try:
            bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound');await bridge.queue.join()
            assert any('未获得可用的识图结果' in str(r[1]) for r in client.sent)
            assert not bridge.store.conversation('10000:private:20000:shared')[0]
        finally:await bridge.stop()
    asyncio.run(run())
