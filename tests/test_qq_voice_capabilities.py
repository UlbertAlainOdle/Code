import asyncio,json
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from saya_app.services.qq_voice import QQVoice,voice_requested,voice_opted_out
from saya_app.services.qq_capabilities import snapshot,prompt
from saya_app.services.qq_chat import QQChatResponder,channel_identity_prompt
from saya_app.services.qq_bridge import normalize_event
from test_qq_expressions import lab,row
from test_qq_voice import FakeTTS,submit
from test_qq_delivery import event
from test_qq_chat_runtime import server
from test_qq_group_integration import response


@pytest.mark.parametrize('text,requested,optout',[
    ('发点语音来听听',True,False),('我想听听你的声音',True,False),
    ('用语音回答我吧',True,False),('你念给我听',True,False),
    ('不要大表情，发语音给我',True,False),('不要语音，发文字',False,True),
    ('我不想听语音',False,True),('纯文字解释语音怎么触发',False,True),
    ('你语音的触发机制是什么',False,False),('为什么语音很少出现',False,False),
    ('给我解释一下发语音的原理',False,False),('说一句晚安',False,False),
    ('今天辛苦啦',False,False)])
def test_direct_request_and_turn_optout(text,requested,optout):
    assert voice_requested(text)==requested
    assert voice_opted_out(text)==optout


@pytest.mark.parametrize('choice',[
    {'kind':'none','voice':False},
    {'kind':'sticker','id':'a'*32,'voice':False},
    {'kind':[],'id':{}},
    {'kind':'kaomoji','id':'shy','voice':False},
    {'kind':'face','id':'6','voice':False}])
def test_explicit_voice_reaches_audio_even_when_optional_selector_declines(tmp_path,choice):
    async def run():
        b,c=await lab(tmp_path,answer='太好了。',choice=choice);tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b,'发点语音来听听');await b.queue.join()
            assert len(c.voices)==len(tts.calls)==1 and len(c.sent)==1
            assert not any(x[1]=='sticker' for x in c.reactions)
            assert row(b)['state']=='sent' and json.loads(row(b)['plan'])['voice_reason']=='explicit_request'
            assert tts.calls[0][0]=='太好了。'
            assert [x[1] for x in c.reactions]==([choice['kind']] if choice['kind']=='kaomoji' else [])
        finally:await b.stop()
    asyncio.run(run())


def test_direct_voice_request_survives_optional_selection_failure_without_extra_text(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='太好了。');tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        async def fail(*a):raise TimeoutError('selection unavailable')
        b.expressions._select=fail
        try:
            submit(b,'我想听听你的声音');await b.queue.join()
            assert len(c.voices)==1 and len(c.sent)==1 and not c.reactions
            message=c.sent[0][0];record=row(b)
            await b.expressions.after_text(message,record['scope'],record['result_id'])
            assert len(c.voices)==len(tts.calls)==1
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('mode,question,disabled,busy,answer,reason',[
    ('off','发点语音来听听',False,False,'太好了。','expression_mode_off'),
    ('sticker','发点语音来听听',False,False,'太好了。','expression_mode_sticker'),
    ('auto','这次不要语音',False,False,'太好了。','turn_text_only'),
    ('auto','发点语音来听听',True,False,'太好了。','tts_disabled'),
    ('auto','发点语音来听听',False,True,'太好了。','tts_busy'),
    ('auto','发点语音来听听',False,False,'很长的技术内容。'*30,'available')])
def test_request_cannot_bypass_switch_busy_or_length_and_snapshot_reports_reason(tmp_path,mode,question,disabled,busy,answer,reason):
    async def run():
        b,c=await lab(tmp_path,answer=answer,mode=mode,choice={'kind':'none'});tts=FakeTTS()
        tts.value=tts.value.model_copy(update={'enabled':not disabled});b.voice=QQVoice(b,tmp_path,tts)
        if busy:b.voice._render_lock.acquire()
        try:
            msg=normalize_event(event(question),b.settings)[0]
            state=snapshot(b,msg);assert state['voice_output']['reason']==reason
            if len(answer)>120:assert b.voice.availability(msg,answer)['reason']=='text_length_limit'
            submit(b,question);await b.queue.join()
            assert not c.voices and not tts.calls
        finally:
            if busy:b.voice._render_lock.release()
            await b.stop()
    asyncio.run(run())


def test_capability_snapshot_has_no_secrets_and_changes_with_runtime_settings(tmp_path):
    async def run():
        b,c=await lab(tmp_path);tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            msg=normalize_event(event('发语音'),b.settings)[0];v=snapshot(b,msg)
            assert v['voice_output']['available'] and v['voice_output']['requested_this_turn']
            assert not v['voice_input_recognition'] and not v['ordinary_file_output']
            assert v['voice_output']['compatible_with']==['kaomoji']
            serial=json.dumps(v);assert all(x not in serial for x in ['api_key','voice_id','20000','10000','qpic.cn'])
            tts.value=tts.value.model_copy(update={'enabled':False})
            assert snapshot(b,msg)['voice_output']['reason']=='tts_disabled'
            text=channel_identity_prompt({'capabilities':v,'expression_managed':True})
            assert 'Current QQ capabilities' in text and 'Plain text body is not a text-only channel limitation' in text
        finally:await b.stop()
    asyncio.run(run())


def test_unactivated_expression_policy_preserves_original_optional_voice_capability(tmp_path):
    async def run():
        b,c=await lab(tmp_path,mode=None);tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            message=normalize_event(event('发语音'),b.settings)[0]
            state=snapshot(b,message)
            assert state['expression_mode']=='legacy_optional_voice' and state['voice_output']['available']
            assert not any(state['expressions'][k] for k in ['favorite_stickers','kaomoji'])
            assert 'qq_faces' not in state['expressions']
        finally:await b.stop()
    asyncio.run(run())


def test_selector_separates_no_visual_from_no_voice_without_forcing_quota(tmp_path,server):
    from saya_app.services.qq_expressions import QQExpressions
    async def run():
        b,c=await lab(tmp_path);captured=[]
        async def single(req,channel_turn):captured.append(req.user_input);return response(json.dumps({'kind':'none','voice':True}))
        b.responder=SimpleNamespace(credentials=lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'},request_type=server.ChatWithMotion,single_response=single)
        try:
            message=normalize_event(event('晚安，陪我待一会儿'),b.settings)[0]
            value=await QQExpressions._select(b.expressions,message,'晚安，我在呢。',[],True)
            assert value['kind']=='none' and value['voice'] is True
            assert '分别判断视觉表达和声音表达' in captured[0] and '不表示voice必须false' in captured[0]
            assert '不要为了轮换、凑比例' in captured[0]
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('group',[False,True])
def test_real_generator_gets_live_capabilities_as_system_context_with_correct_scope(tmp_path,server,group):
    from core_services import KnowledgeBase,MemoryManager
    from saya_app.services.conversation import conversation_service
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'));payloads=[]
    def model(request):
        if not json.loads(request.content).get('stream',True):
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"mood":"neutral"}'},'finish_reason':'stop'}]})
        payloads.append(json.loads(request.content))
        delta={'choices':[{'delta':{'content':'你好，我在。'},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(delta)+'\n\ndata: [DONE]\n\n')
    async def run():
        b,c=await lab(tmp_path/'bridge');b.settings=b.settings.model_copy(update={'shared_memory':False,'agent_mode':False,'web_search_mode':'off'})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        responder=QQChatResponder(b.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',
            lambda:{'configured':True,'api_key':'fixture','api_url':'http://mock/v1','model':'fixture'})
        responder.channel_capabilities=lambda message:snapshot(b,message);responder.expression_managed=True
        raw=event('发点语音来听听',group_id=90000 if group else None)
        msg=normalize_event(raw,b.settings)[0]
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
                with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'_get_api_httpx_client',lambda:api):
                    answer=await responder(msg,[],'session_fixture')
            assert '你好' in str(answer) and len(payloads)==1
            system='\n'.join(str(x['content']) for x in payloads[0]['messages'] if x['role']=='system')
            assert 'Current QQ capabilities' in system and '"requested_this_turn": true' in system
            assert '"available": true' in system and '"voice_input_recognition": false' in system
            assert '"personal_memory": {"read": false, "write": false' in system
            assert not c.sent and not c.voices and not tts.calls
        finally:await b.stop()
    asyncio.run(run())
