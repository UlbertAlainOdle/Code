import asyncio,io
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from PIL import Image
from fastapi import FastAPI
from saya_app.services.qq_favorites import QQFavorites,FavoriteConflict
from saya_app.routers import qq as routes
from test_qq_task_scheduler import make
from test_qq_group_integration import response
from test_qq_chat_runtime import server


def lab(tmp_path):
    b=make(tmp_path,None);b.store.initialize(7);b.memory_management=SimpleNamespace()
    calls=[]
    async def call(action,payload):calls.append(action);return [{'md5':'a'*32,'url':'http://p.qpic.cn/test/a.gif','desc':'example'}]
    b.client=SimpleNamespace(call=call);b.favorites=QQFavorites(b)
    return b,calls


def test_refresh_and_owner_selection_never_mutate_QQ_collection(tmp_path):
    b,calls=lab(tmp_path);s=b.favorites
    state=asyncio.run(s.refresh());assert calls==['fetch_custom_face_detail']
    assert not state['items'][0]['selected'] and 'url' not in str(state) and 'p.qpic.cn' not in str(state)
    key=state['items'][0]['id'];s.select(1,[{'id':key,'selected':True,'meaning':'适合打招呼'}])
    with pytest.raises(FavoriteConflict):s.select(1,[])
    assert s.state()['items'][0]['meaning']=='适合打招呼'
    state=asyncio.run(s.refresh());assert state['items'][0]['selected']
    async def empty(*a):return []
    b.client.call=empty;assert not asyncio.run(s.refresh())['items']


def test_sync_follows_real_additions_and_deletions_without_changing_surviving_owner_choices(tmp_path):
    async def run():
        b,_=lab(tmp_path);s=b.favorites;state=await s.refresh();first=state['items'][0]['id']
        s.select(1,[{'id':first,'selected':True,'meaning':'keep my choice'}])
        values=[{'md5':'a'*32,'url':'http://p.qpic.cn/test/a.gif','desc':'example'},
            {'md5':'b'*32,'url':'http://p.qpic.cn/test/b.gif','desc':'added'}]
        async def current(*args):return values
        b.client.call=current;state=await s.refresh(2)
        assert len(state['items'])==2 and state['items'][0]['selected'] and state['items'][0]['meaning']=='keep my choice'
        values.pop(0);state=await s.refresh(3)
        assert [x['id'] for x in state['items']]==['b'*32] and not state['items'][0]['selected']
    asyncio.run(run())


@pytest.mark.parametrize('url',['http://127.0.0.1/a','https://p.qpic.cn.evil/a','file:///a','https://user@p.qpic.cn/a','https://p.qpic.cn:9000/a'])
def test_preview_only_allows_qq_favorite_cdn(url):
    with pytest.raises(ValueError):QQFavorites.image_url(url)


def test_first_frame_preview_is_static_and_does_not_inherit_hidden_frames():
    out=io.BytesIO();Image.new('RGB',(4,3),'red').save(out,format='GIF',save_all=True,append_images=[Image.new('RGB',(4,3),'blue')])
    png=QQFavorites._png(out.getvalue());image=Image.open(io.BytesIO(png))
    assert image.n_frames==1 and image.convert('RGB').getpixel((0,0))==(255,0,0)


def test_only_selected_images_are_analyzed_without_memory_or_tools(tmp_path,server):
    async def run():
        b,_=lab(tmp_path);s=b.favorites;state=await s.refresh();key=state['items'][0]['id'];captured=[]
        async def preview(*a):return b'fixture-png'
        s.preview=preview
        async def single(req,channel_turn):captured.append(req);return response('含义：惊讶。情绪：好奇。适合：意外消息。避免：严肃安慰。')
        b.responder=SimpleNamespace(credentials=lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'text','model_routes':{'multimodal_model_name':'vision'}},request_type=server.ChatWithMotion,single_response=single)
        with pytest.raises(ValueError):await s.analyze(key,1)
        assert not captured
        s.select(1,[{'id':key,'selected':True,'meaning':''}]);state=await s.analyze(key,2)
        assert state['items'][0]['analyzed'] and '惊讶' in state['items'][0]['meaning']
        req=captured[0];assert not req.agent_mode and not req.memory_write_enabled and req.temporary_chat and not req.session_id
        assert req.web_search_mode=='off' and req.recall_mode=='off' and req.multimodal_model_name=='vision'
    asyncio.run(run())


def test_revocation_during_analysis_does_not_save_late_semantics(tmp_path):
    async def run():
        b,_=lab(tmp_path);s=b.favorites;state=await s.refresh();key=state['items'][0]['id'];s.select(1,[{'id':key,'selected':True,'meaning':''}])
        async def preview(*a):return b'fixture'
        s.preview=preview
        async def single(*a,**kw):
            s.select(2,[{'id':key,'selected':False,'meaning':'keep'}]);return response('LATE')
        b.responder=SimpleNamespace(credentials=lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'text','model_routes':{'multimodal_model_name':'vision'}},request_type=lambda **kw:SimpleNamespace(**kw),single_response=single)
        with pytest.raises(FavoriteConflict):await s.analyze(key,2)
        assert not s.state()['items'][0]['selected'] and s.state()['items'][0]['meaning']=='keep'
    asyncio.run(run())


def test_management_api_needs_local_owner_confirmation_and_revision(tmp_path):
    async def run():
        b,calls=lab(tmp_path);b.task=SimpleNamespace(done=lambda:False);app=FastAPI();app.include_router(routes.router)
        with patch.object(routes,'qq_bridge',b):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as web:
                assert (await web.get('/api/qq/favorites')).status_code==401
                headers={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'};await web.post('/api/qq/ui-session',headers=headers)
                assert (await web.get('/api/qq/favorites?owner_id=30000')).status_code==403
                body={'owner_id':'20000','catalogue_revision':0,'confirmed':False}
                assert (await web.post('/api/qq/favorites/refresh',headers=headers,json=body)).status_code==403
                body['confirmed']=True
                state=(await web.post('/api/qq/favorites/refresh',headers=headers,json=body)).json()
                assert state['catalogue_revision']==1
                assert (await web.post('/api/qq/favorites/select',headers=headers,json=body)).status_code==409
                assert calls==['fetch_custom_face_detail']
    asyncio.run(run())


def test_analysis_reaches_actual_generator_visual_request_without_private_context(tmp_path,server):
    from core_services import KnowledgeBase,MemoryManager
    from saya_app.services.conversation import conversation_service
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    kb.add_memory('PRIVATE_FAVORITES_TEST_MUST_NOT_APPEAR',source='manual')
    async def run():
        b,_=lab(tmp_path/'bridge');s=b.favorites;state=await s.refresh();key=state['items'][0]['id']
        s.select(1,[{'id':key,'selected':True,'meaning':''}])
        out=io.BytesIO();Image.new('RGB',(8,8),'red').save(out,format='PNG')
        async def preview(*a):return out.getvalue()
        s.preview=preview;requests=[]
        def model(request):
            payload=__import__('json').loads(request.content);requests.append(payload)
            delta={'choices':[{'delta':{'content':'画面是红色，不能仅凭色块判断具体情绪。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+__import__('json').dumps(delta)+'\n\ndata: [DONE]\n\n')
        credentials={'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'text','model_routes':{'multimodal_model_name':'vision'}}
        b.responder=SimpleNamespace(credentials=lambda:credentials,request_type=server.ChatWithMotion,single_response=server._chat_stream_single)
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(server,'_get_api_httpx_client',lambda:api):
                state=await s.analyze(key,2)
        assert state['items'][0]['analyzed'] and requests[0]['model']=='vision'
        serial=__import__('json').dumps(requests,ensure_ascii=False)
        assert 'PRIVATE_FAVORITES_TEST_MUST_NOT_APPEAR' not in serial and 'image_url' in serial
    asyncio.run(run())
