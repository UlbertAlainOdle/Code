import asyncio,io,json,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from PIL import Image
from fastapi import FastAPI
from saya_app.services.qq_bridge import OneBotClient,normalize_event
from saya_app.services.qq_expressions import QQExpressions,ExpressionConflict,KAOMOJI,plain_body
from saya_app.services.qq_favorites import QQFavorites,FavoriteConflict
from saya_app.services.qq_media import QQMedia,MediaPolicy
from saya_app.routers import qq as routes
from test_qq_task_scheduler import make,until
from test_qq_delivery import Client,event
from test_qq_voice import FakeTTS,VoiceClient,submit
from test_qq_group_integration import response
from test_qq_chat_runtime import server


class ReactionClient(VoiceClient):
    def __init__(self):super().__init__();self.reactions=[];self.fail=False
    async def send_expression(self,message,kind,key=None,path=None):
        self.reactions.append((message,kind,key,Path(path).read_bytes() if path else None,len(self.sent)))
        if self.fail:raise TimeoutError('transport unknown')
        return '800'
    async def send_inline(self,message,text,*,kind,key,offset,reference=False,mention=True):
        receipt=await self.send_part(message,text,reference=reference,mention=mention)
        self.reactions.append((message,kind,key,None,len(self.sent)))
        if self.fail:raise TimeoutError('transport unknown')
        return receipt


async def lab(tmp_path,answer='太好了。(^_^)',mode='auto',choice=None,**changes):
    async def model(*a):return answer
    c=ReactionClient();b=make(tmp_path,model,client=c,**changes);await b.start()
    b.media=QQMedia(b,tmp_path);b.media.configure('20000',0,MediaPolicy(voice_reply_enabled=True).model_dump(),confirmed=True)
    b.favorites=QQFavorites(b)
    async def native(*a):return [{'md5':'a'*32,'url':'https://p.qpic.cn/test.gif','desc':'example'}]
    c.call=native;state=await b.favorites.refresh()
    b.favorites.select(state['catalogue_revision'],[{'id':'a'*32,'selected':True,'meaning':'开心、赞同；适合好消息，避免严肃话题。'}])
    b.expressions=QQExpressions(b,tmp_path)
    if mode is not None:b.expressions.configure('20000',0,mode,confirmed=True)
    async def select(*a):
        kind=mode if mode in {'sticker','kaomoji'} else 'sticker'
        value=choice if choice is not None else {'kind':kind,'id':{'sticker':'a'*32,'kaomoji':'happy'}[kind]}
        return value|{'anchor':value.get('anchor','太好了'),'placement':value.get('placement','before')}
    b.expressions._select=select
    async def read(*a):return b'original-gif','gif'
    b.favorites.read_for_send=read
    return b,c


def row(b):
    with b.store.connect() as db:r=db.execute('select * from qq_expression_deliveries order by rowid desc limit 1').fetchone()
    return dict(r) if r else None


@pytest.mark.parametrize('choice,expected',[
    ({'kind':'sticker','id':'a'*32},'sticker'),
    ({'kind':'face','id':'13','alternatives':[{'kind':'sticker','id':'a'*32}]},None),
    ({'kind':'kaomoji','id':'shy','alternatives':[{'kind':'face','id':'14'},{'kind':'sticker','id':'a'*32}]},'kaomoji'),
    ({'kind':'none','alternatives':[{'kind':'face','id':'14'}]},None),
    ({'kind':[],'id':{}},None),
    ({'kind':'face','id':{}},None),
    ({'kind':'sticker','id':'https://evil.invalid/a','alternatives':[{'kind':'face','id':'14'}]},None),
    ({'kind':'voice','alternatives':[{'kind':'face','id':'14'}]},None),
    ({'channel':'expression','sticker':None,'face':'14','kaomoji':'smile'},None)])
def test_real_bridge_honours_single_semantic_verdict_without_category_fallback(tmp_path,choice,expected):
    async def run():
        b,c=await lab(tmp_path,choice=choice)
        try:
            submit(b);await b.queue.join()
            assert len(c.sent)==1 and c.sent[0][1]=='太好了。' and not c.voices
            assert [x[1] for x in c.reactions]==([expected] if expected else [])
            r=row(b);assert r['state']==('sent' if expected else 'skipped')
            if expected:
                assert c.reactions[0][4]==1 and r['receipt']==('800' if expected=='sticker' else '501')
                if expected=='sticker':assert c.reactions[0][3]==b'original-gif'
                await b.expressions.after_text(c.sent[0][0],r['scope'],r['result_id']);assert len(c.reactions)==1
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('mode,expected',[('off',None),('kaomoji','kaomoji'),('sticker','sticker'),('random','sticker')])
def test_explicit_mode_and_random_only_use_valid_matching_candidates(tmp_path,mode,expected):
    async def run():
        b,c=await lab(tmp_path,mode=mode)
        try:
            with patch('saya_app.services.qq_expressions.secrets.choice',side_effect=lambda xs:xs[0]):
                submit(b);await b.queue.join()
            assert [x[1] for x in c.reactions]==([expected] if expected else []) and not c.voices
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('answer,question',[
    ('技术说明。'*300,'你好'),('```python\nx=(1+2)\n```','你好'),('资料 https://example.invalid','你好'),('你好。','只用文字'),('你好。','不要任何表情')])
def test_no_decoration_for_long_code_links_and_user_optout(tmp_path,answer,question):
    async def run():
        b,c=await lab(tmp_path,answer)
        try:submit(b,question);await b.queue.join();assert c.sent and not c.reactions and not c.voices
        finally:await b.stop()
    asyncio.run(run())


def test_voice_exclusivity_reuses_original_tts_and_off_blocks_legacy_voice(tmp_path):
    from saya_app.services.qq_voice import QQVoice
    async def run():
        b,c=await lab(tmp_path,mode='voice');tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b);await b.queue.join()
            assert len(c.voices)==1 and len(tts.calls)==1 and not c.reactions and row(b)['state']=='sent'
            assert c.sent[0][1]=='太好了。' and tts.calls[0][0]=='太好了。'
            b.expressions.configure('20000',1,'off',confirmed=True)
            submit(b,mid=2);await b.queue.join();assert len(c.voices)==1 and not c.reactions
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change',['selection','mode','group','cancel'])
def test_revocation_or_cancel_during_preparation_never_sends_late_sticker(tmp_path,change):
    async def run():
        b,c=await lab(tmp_path);entered=asyncio.Event();release=asyncio.Event()
        async def read(*a):entered.set();await release.wait();return b'image','gif'
        b.favorites.read_for_send=read
        try:
            if change=='group':
                b.submit_signed(json.dumps(event('好消息',1,group_id=90000)).encode(),'Bearer inbound')
            else:submit(b)
            await entered.wait()
            if change=='selection':b.favorites.select(2,[{'id':'a'*32,'selected':False,'meaning':'keep'}])
            elif change=='mode':b.expressions.configure('20000',1,'off',confirmed=True)
            elif change=='group':b.settings=b.settings.model_copy(update={'allowed_groups':set()})
            else:
                submit(b,'/cancel',2);await b.task_controls.join()
            release.set();await b.queue.join()
            assert not c.reactions and row(b)['state'] in {'cancelled','failed','skipped'}
        finally:release.set();await b.stop()
    asyncio.run(run())


def test_transport_unknown_never_retries_on_reentry_or_restart(tmp_path):
    async def run():
        b,c=await lab(tmp_path);c.fail=True
        try:
            submit(b);await b.queue.join();r=row(b)
            assert r['state']=='unknown' and len(c.reactions)==1 and c.sent
            b.expressions=QQExpressions(b,tmp_path)
            await b.expressions.after_text(c.sent[0][0],r['scope'],r['result_id'])
            assert row(b)['state']=='unknown' and len(c.reactions)==1
        finally:await b.stop()
    asyncio.run(run())


def test_five_message_budget_leaves_no_sixth_reaction(tmp_path):
    async def run():
        b,c=await lab(tmp_path,reply_max_messages=1)
        try:submit(b);await b.queue.join();assert len(c.sent)==1 and not c.reactions and row(b)['state']=='skipped'
        finally:await b.stop()
    asyncio.run(run())


def test_owner_private_mode_command_bypasses_running_generation(tmp_path):
    async def run():
        b,c=await lab(tmp_path);entered=asyncio.Event();release=asyncio.Event()
        async def model(*a):entered.set();await release.wait();return '好。'
        b.responder=model
        try:
            submit(b);await entered.wait();submit(b,'/expression off',2)
            await asyncio.wait_for(b.task_controls.join(),1)
            assert b.expressions.state()['mode']=='off' and 'off' in c.sent[0][1]
            message=normalize_event(event('/expression auto',3,30000),b.settings)[0]
            assert '主人' in b.expressions.command(message) and b.expressions.state()['mode']=='off'
            release.set();await b.queue.join();assert not c.reactions
        finally:release.set();await b.stop()
    asyncio.run(run())


def test_mode_api_requires_local_owner_confirmation_and_cas(tmp_path):
    async def run():
        b,c=await lab(tmp_path,mode=None);b.memory_management=SimpleNamespace();app=FastAPI();app.include_router(routes.router)
        try:
            with patch.object(routes,'qq_bridge',b):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as web:
                    assert (await web.get('/api/qq/expression-policy')).status_code==401
                    headers={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'};await web.post('/api/qq/ui-session',headers=headers)
                    assert (await web.get('/api/qq/expression-policy?owner_id=30000')).status_code==403
                    body={'owner_id':'20000','expression_revision':0,'confirmed':False,'mode':'auto'}
                    assert (await web.put('/api/qq/expression-policy',json=body,headers=headers)).status_code==403
                    body['confirmed']=True
                    assert (await web.put('/api/qq/expression-policy',json=body,headers=headers)).json()['mode']=='auto'
                    assert (await web.put('/api/qq/expression-policy',json=body,headers=headers)).status_code==409
                    body.update(expression_revision=1,mode='bad')
                    assert (await web.put('/api/qq/expression-policy',json=body,headers=headers)).status_code==422
        finally:await b.stop()
    asyncio.run(run())


def test_selector_uses_curated_semantics_without_urls_history_or_tools(tmp_path,server):
    async def run():
        b,c=await lab(tmp_path);captured=[]
        async def single(req,channel_turn):captured.append((req,channel_turn));return response(json.dumps({'kind':'sticker','id':'a'*32}))
        b.responder=SimpleNamespace(credentials=lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'},request_type=server.ChatWithMotion,single_response=single)
        try:
            message=normalize_event(event(),b.settings)[0]
            chosen=await QQExpressions._select(b.expressions,message,'好消息',b.expressions._candidates()[1],True)
            req,turn=captured[0];assert chosen['id']=='a'*32
            assert '开心' in req.user_input and 'p.qpic.cn' not in req.user_input
            assert not req.agent_mode and not req.memory_write_enabled and req.temporary_chat and not req.session_id
            assert req.recall_mode==req.graph_recall_mode==req.web_search_mode=='off' and turn['history']==[]
        finally:await b.stop()
    asyncio.run(run())


def test_selector_reaches_actual_generator_without_recall_or_conversation_writes(tmp_path,server):
    from core_services import KnowledgeBase,MemoryManager
    from saya_app.services.conversation import conversation_service
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'));before_sessions=manager.get_session_list()
    kb.add_memory('PRIVATE_EXPRESSION_MUST_NOT_LEAK',source='manual')
    async def run():
        b,c=await lab(tmp_path/'bridge');requests=[]
        def model(request):
            payload=json.loads(request.content);requests.append(payload)
            assert payload['stream'] is False
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'kind':'sticker','id':'a'*32})},'finish_reason':'stop'}]})
        b.responder=SimpleNamespace(credentials=lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'},request_type=server.ChatWithMotion,single_response=server._chat_stream_single)
        message=normalize_event(event(),b.settings)[0]
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
                with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(server,'_get_api_httpx_client',lambda:api):
                    chosen=await QQExpressions._select(b.expressions,message,'太好了',b.expressions._candidates()[1],False)
            assert chosen['id']=='a'*32 and len(requests)==1
            serial=json.dumps(requests,ensure_ascii=False)
            assert 'PRIVATE_EXPRESSION_MUST_NOT_LEAK' not in serial and '开心' in serial and 'p.qpic.cn' not in serial
            assert not requests[0].get('tools') and manager.get_session_list()==before_sessions and not c.sent
        finally:await b.stop()
    asyncio.run(run())


def test_original_animated_bytes_are_sent_and_revocation_rechecked_after_download(tmp_path):
    async def run():
        b,c=await lab(tmp_path);s=b.favorites;out=io.BytesIO()
        Image.new('RGB',(3,3),'red').save(out,format='GIF',save_all=True,append_images=[Image.new('RGB',(3,3),'blue')])
        raw=out.getvalue()
        async def download(*a):return raw
        s._download=download
        try:
            result=await QQFavorites.read_for_send(s,'a'*32,2)
            assert result==(raw,'gif') and Image.open(io.BytesIO(result[0])).n_frames==2
            async def revoke(*a):s.select(2,[{'id':'a'*32,'selected':False,'meaning':'keep'}]);return raw
            s._download=revoke
            with pytest.raises(FavoriteConflict):await QQFavorites.read_for_send(s,'a'*32,2)
        finally:await b.stop()
    asyncio.run(run())


def test_native_segments_are_validated_and_target_original_group(tmp_path):
    async def run():
        from test_qq_delivery import config
        client=OneBotClient(config());calls=[]
        async def call(action,payload):calls.append((action,payload));return {'message_id':123}
        client.call=call;message=normalize_event(event(group_id=90000),config())[0];path=tmp_path/'a.gif';path.write_bytes(b'gif')
        await client.send_expression(message,'sticker',path=path)
        with pytest.raises(ValueError):await client.send_expression(message,'face','13')
        await client.send_expression(message,'kaomoji','happy')
        assert all(a=='send_group_msg' and p['group_id']==90000 for a,p in calls)
        assert calls[0][1]['message'][0]['data']['file']==path.as_uri()
        assert calls[1][1]['message'][0]['data']['text']==KAOMOJI['happy']
        with pytest.raises(ValueError):await client.send_expression(message,'face','99999')
        await client.close()
    asyncio.run(run())


def test_persisted_modes_require_new_reader_and_owned_expired_assets_are_pruned(tmp_path):
    from saya_app.services.qq_rollback import check_memory_rollback
    import os,sqlite3
    directory=tmp_path/'knowledge_base/qq';directory.mkdir(parents=True)
    with sqlite3.connect(directory/'bridge.sqlite3') as db:
        db.execute('create table qq_expression_policy(bot text,revision integer,mode text)')
        db.execute("insert into qq_expression_policy values('10000',1,'off')")
    assert 'qq_expressions_v1' in check_memory_rollback(tmp_path,{'memory_compatibility':[]})['missing']
    assert check_memory_rollback(tmp_path,{'memory_compatibility':['qq_expressions_v1']})['status']=='compatible_declared'
    async def run():
        b,c=await lab(tmp_path/'bridge');s=b.expressions;s.directory.mkdir(parents=True)
        owned=s.directory/('b'*32+'.gif');owned.write_bytes(b'old');os.utime(owned,(time.time()-400,)*2)
        unrelated=s.directory/'leave.txt';unrelated.write_text('leave')
        fresh=s.directory/('c'*32+'.gif');fresh.write_bytes(b'upload')
        try:s.prune();assert not owned.exists() and unrelated.exists() and fresh.exists()
        finally:await b.stop()
    asyncio.run(run())


def test_switching_off_during_tts_discards_audio_without_falling_back_to_expression(tmp_path):
    import threading
    from saya_app.services.qq_voice import QQVoice
    async def run():
        b,c=await lab(tmp_path,mode='voice');tts=FakeTTS();tts.release=threading.Event();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b);await until(tts.entered.is_set)
            b.expressions.configure('20000',1,'off',confirmed=True);tts.release.set();await b.queue.join()
            assert c.sent and not c.voices and not c.reactions and row(b)['state']=='cancelled'
        finally:tts.release.set();await b.stop()
    asyncio.run(run())


def test_auto_can_choose_voice_instead_of_any_available_expression(tmp_path):
    from saya_app.services.qq_voice import QQVoice
    async def run():
        b,c=await lab(tmp_path,answer='晚安，安心休息吧。',choice={'kind':'voice','id':None,'alternatives':[{'kind':'sticker','id':'a'*32},{'kind':'face','id':'14'}]})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b,'想听你轻声说晚安');await b.queue.join()
            assert len(c.voices)==1 and len(tts.calls)==1 and not c.reactions and row(b)['state']=='sent'
            assert json.loads(row(b)['plan'])['kind']=='voice' and c.sent
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('question,kind,key,expected',[
    ('这次不要语音','voice',None,None),('这次不要表情','face','14',None),
    ('不要颜文字','kaomoji','shy',None),('不要颜文字','face','6',None),
    ('不要收藏表情','sticker','a'*32,None),('别发笑脸','face','14',None),
    ('不要语音，可以发颜文字','kaomoji','smile','kaomoji')])
def test_optouts_filter_choices_without_substituting_another_category(tmp_path,question,kind,key,expected):
    async def run():
        b,c=await lab(tmp_path,choice={'kind':kind,'id':key,'alternatives':[{'kind':'face','id':'13'}]})
        try:
            submit(b,question);await b.queue.join()
            assert [x[1] for x in c.reactions]==([expected] if expected else []) and not c.voices
        finally:await b.stop()
    asyncio.run(run())


def test_random_uses_only_semantically_nominated_valid_candidates_including_none(tmp_path):
    async def run():
        b,c=await lab(tmp_path,mode='random',choice={'kind':'kaomoji','id':'shy','alternatives':[
            {'kind':'none'},{'kind':'sticker','id':'unapproved'},{'kind':'voice'},{'kind':'face','id':'99999'}]})
        def pick(options):
            assert options==[{'kind':'kaomoji','id':'shy'},{'kind':'none'}]
            return options[-1]
        try:
            with patch('saya_app.services.qq_expressions.secrets.choice',side_effect=pick):submit(b);await b.queue.join()
            assert not c.reactions and not c.voices and row(b)['state']=='skipped'
        finally:await b.stop()
    asyncio.run(run())


def test_recent_reaction_context_is_actor_and_conversation_scoped_without_message_text(tmp_path):
    async def run():
        b,c=await lab(tmp_path)
        async def select(message,*args):
            if message.user_id=='30000':return {'kind':'kaomoji','id':'sad','anchor':'太好了','placement':'before'}
            if not message.group_id:return {'kind':'sticker','id':'a'*32}
            return {'kind':'kaomoji','id':'happy','anchor':'太好了','placement':'after'}
        b.expressions._select=select
        try:
            for mid,user,group in [(1,20000,90000),(2,30000,90000),(3,20000,None)]:
                b.submit_signed(json.dumps(event('PRIVATE_TEXT_MUST_NOT_ENTER_RECENT',mid,user,group_id=group)).encode(),'Bearer inbound');await b.queue.join()
            group=normalize_event(event('q',4,group_id=90000),b.settings)[0]
            recent=b.expressions._recent(group)
            assert len(recent)==1 and recent[0]['kind']=='kaomoji' and recent[0]['id']=='happy'
            assert set(recent[0])=={'kind','id','location','state','seconds_ago'} and 'PRIVATE_TEXT' not in str(recent)
        finally:await b.stop()
    asyncio.run(run())


def test_disabled_voice_engine_not_advertised_to_semantic_selector(tmp_path):
    from dataclasses import replace
    from saya_app.services.qq_voice import QQVoice
    async def run():
        b,c=await lab(tmp_path,choice={'kind':'voice'});tts=FakeTTS();tts.value=tts.value.model_copy(update={'enabled':False});b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b,'说声晚安');await b.queue.join()
            assert not tts.calls and not c.reactions and not c.voices and row(b)['state']=='skipped'
        finally:await b.stop()
    asyncio.run(run())
