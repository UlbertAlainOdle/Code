"""Experience changes through real QQ queue/generator; all transports isolated."""
import asyncio,hashlib,json,threading,time
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from fastapi import FastAPI
from saya_app.services.qq_chat import QQChatResponder,channel_identity_prompt
from saya_app.services.qq_dialogue_experience import QQDialogueExperience,turn_guidance
from saya_app.services.qq_expressions import SelectionError,speech_span
from saya_app.services.qq_voice import QQVoice
from saya_app.services.qq_bridge import normalize_event
from saya_app.services import qq_delivery
from saya_app.routers import qq as routes
from test_qq_expressions import lab,row
from test_qq_voice import FakeTTS,submit
from test_qq_delivery import event
from test_qq_task_scheduler import until
from test_qq_chat_runtime import server
from test_qq_group_integration import response


def enable(b):
    return b.dialogue_experience.configure('20000',b.dialogue_experience.state()['revision'],{'enabled':True})


def test_policy_default_off_cas_owner_strict_and_no_settings_overwrite(tmp_path):
    async def run():
        b,c=await lab(tmp_path);s=b.dialogue_experience
        try:
            before=(b.controls(),b.expressions.state(),b.media.state(),b.favorites._load())
            assert s.state()=={'revision':0,'values':{'enabled':False,'concise_replies':True,'short_voice':True}}
            for owner,revision,values,error in [('30000',0,{'enabled':True},PermissionError),
                ('20000',True,{'enabled':True},ValueError),('20000',0,{'enabled':'true'},ValueError),
                ('20000',0,{'enabled':True,'voice_max_chars':999},ValueError)]:
                with pytest.raises(error):s.configure(owner,revision,values)
            enable(b)
            with pytest.raises(ValueError):s.configure('20000',0,{'enabled':False})
            assert before==(b.controls(),b.expressions.state(),b.media.state(),b.favorites._load())
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('enabled,expected',[(False,False),(True,True)])
def test_long_text_short_semantic_voice_uses_confirmed_sentence_only(tmp_path,enabled,expected):
    async def run():
        sentence='辛苦了，先歇一会儿，我在这里陪你。'
        body='这部分是需要保留的详细说明。'*12+'\n'+sentence
        b,c=await lab(tmp_path,answer=body,choice={'kind':'none','voice':True,'speech_anchor':sentence})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            if enabled:enable(b)
            submit(b,'今天忙了一天，想和你聊聊。');await b.queue.join()
            assert bool(c.voices)==expected and bool(tts.calls)==expected
            assert ''.join(x[1] for x in c.sent)==body and not c.reactions
            if expected:
                assert tts.calls[0][0]==sentence
                r=row(b);plan=json.loads(r['plan'])
                assert r['state']=='sent' and plan['speech_chars']==len(sentence)
                assert sentence not in json.dumps(plan,ensure_ascii=False)
                assert b.media.state()['values']['voice_max_chars']==120
                await b.expressions.after_text(c.sent[0][0],r['scope'],r['result_id'])
                assert len(tts.calls)==1
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('anchor',['凭空增加的承诺。','完整的','第一句。最后一句。',None,{'text':'最后一句。'}])
def test_invalid_generated_speech_is_never_synthesized(tmp_path,anchor):
    async def run():
        body='完整的第一句。'+'中间详细说明。'*25+'最后一句。'
        b,c=await lab(tmp_path,answer=body,choice={'kind':'none','voice':True,'speech_anchor':anchor})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            enable(b);submit(b,'陪我聊聊');await b.queue.join()
            assert not tts.calls and not c.voices and c.sent
            assert json.loads(row(b)['plan'])['voice_reason']=='invalid_speech_span'
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('failure,reason',[(TimeoutError(),'selector_timeout'),(RuntimeError('PRIVATE_ERROR'),'selector_error'),
    (SelectionError('selector_json'),'selector_json'),(SelectionError('selector_truncated'),'selector_truncated')])
def test_selector_failure_reason_and_explicit_voice_without_text_regeneration(tmp_path,failure,reason):
    async def run():
        body='先把手边的事情放一放。'+'这是补充解释。'*22
        b,c=await lab(tmp_path,answer=body)
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts);enable(b)
        async def fail(*args):raise failure
        b.expressions._select=fail
        try:
            submit(b,'发点语音来听听');await b.queue.join()
            assert len(c.voices)==1 and tts.calls[0][0]=='先把手边的事情放一放。'
            p=json.loads(row(b)['plan']);assert p['decision_reason']==reason and p['voice_reason']=='explicit_request'
            with b.store.connect() as db:raw=db.execute('select metrics from qq_experience_metrics').fetchone()[0]
            assert 'PRIVATE_ERROR' not in raw and body not in raw and '发点' not in raw
            assert json.loads(raw)['expression_outcome']=='sent'
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('mode,choice,kind,voice',[
    ('auto',{'kind':'none','voice':False},'none',False),
    ('auto',{'kind':'sticker','id':'a'*32,'voice':True},'sticker',False),
    ('auto',{'kind':'face','id':'6','voice':True,'anchor':'我在','placement':'before'},'none',False),
    ('auto',{'kind':'kaomoji','id':'shy','voice':True,'anchor':'我在','placement':'after'},'kaomoji',True),
    ('off',{'kind':'none','voice':True},'none',False)])
def test_voice_visual_semantics_and_mutual_exclusion_remain(tmp_path,mode,choice,kind,voice):
    async def run():
        b,c=await lab(tmp_path,answer='我在，安心休息吧。',mode=mode,choice=choice)
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            enable(b);submit(b,'晚安。');await b.queue.join()
            assert bool(c.voices)==voice and json.loads(row(b)['plan'])['kind']==kind
            if tts.calls:assert tts.calls[0][0]=='我在，安心休息吧。'
        finally:await b.stop()
    asyncio.run(run())


def test_policy_off_during_synthesis_discards_late_audio(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='我在呢。',choice={'kind':'none','voice':True})
        tts=FakeTTS();tts.release=threading.Event();b.voice=QQVoice(b,tmp_path,tts)
        try:
            enable(b);submit(b);await until(tts.entered.is_set)
            b.dialogue_experience.configure('20000',1,{'enabled':False});tts.release.set();await b.queue.join()
            assert c.sent and not c.voices and row(b)['state']=='cancelled'
        finally:tts.release.set();await b.stop()
    asyncio.run(run())


def test_speech_span_cannot_be_reused_for_another_canonical_result(tmp_path):
    async def run():
        b,c=await lab(tmp_path,mode='off');tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b);await b.queue.join();r=row(b);message=c.sent[0][0]
            span=speech_span('绝不会发送这句。','绝不会发送这句。',120)
            await b.voice.after_text(message,r['scope'],r['result_id'],speech_span=span)
            assert not tts.calls
        finally:await b.stop()
    asyncio.run(run())


def test_telemetry_bounded_enumerated_and_contains_no_conversation_identity(tmp_path):
    async def run():
        b,c=await lab(tmp_path,choice={'kind':'none'});s=b.dialogue_experience
        try:
            s.MAX_ROWS=2;submit(b,'PRIVATE_QUESTION');await b.queue.join()
            r=row(b);msg=c.sent[0][0]
            for index in range(4):
                s.record(str(index),msg,'SECRET_BODY',{'kind':'PRIVATE_KIND','decision_reason':'SECRET_REASON','anchor':'SECRET_ANCHOR','voice_reason':'tts_busy'})
            with b.store.connect() as db:rows=db.execute('select * from qq_experience_metrics').fetchall()
            assert len(rows)==2
            raw=json.dumps([tuple(x) for x in rows])
            assert all(x not in raw for x in ['PRIVATE_QUESTION','SECRET_BODY','PRIVATE_KIND','SECRET_REASON','SECRET_ANCHOR','20000','10000'])
            v=s.summary();assert v['count']==2 and v['voice_reasons']=={'tts_busy':2}
            s.clock=lambda:time.time()+8*86400;assert s.summary()['count']==0
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('effort',['none','auto','high'])
def test_real_generator_length_guidance_preserves_thinking_with_separate_mood_metadata(tmp_path,server,enabled,effort):
    from core_services import KnowledgeBase,MemoryManager
    from saya_app.services.conversation import conversation_service
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'));payloads=[]
    def model(request):
        if not json.loads(request.content).get('stream',True):
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"mood":"neutral"}'},'finish_reason':'stop'}]})
        payloads.append(json.loads(request.content))
        return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'在呢。'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n')
    async def run():
        b,c=await lab(tmp_path/'bridge');b.settings=b.settings.model_copy(update={'shared_memory':False,'agent_mode':False,'web_search_mode':'off','thinking_effort':effort})
        if enabled:enable(b)
        responder=QQChatResponder(b.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',
            lambda:{'configured':True,'api_key':'fixture','api_url':'https://api.deepseek.com/v1','model':'deepseek-v4-flash'})
        responder.dialogue_experience=b.dialogue_experience
        message=normalize_event(event('在吗？'),b.settings)[0]
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
                with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'_get_api_httpx_client',lambda:api):
                    answer=await responder(message,['Doctor: 请记住本轮方案','Saya: OLD_PLAN_CANARY：三天整理桌面、衣物和书架。'],'isolated')
            assert str(answer)=='在呢。' and len(payloads)==1
            request=payloads[0];system='\n'.join(str(x['content']) for x in request['messages'])
            assert any(x['role']=='assistant' and 'OLD_PLAN_CANARY' in x['content'] for x in request['messages'])
            assert ('QQ turn pacing' in system)==enabled
            final_system=[x['content'] for x in request['messages'] if x['role']=='system'][-1]
            assert ('Current turn pacing' in final_system)==enabled
            assert request['thinking']['type']==('disabled' if effort=='none' else 'enabled')
            if effort=='high':assert request['reasoning_effort']=='high'
            assert message.experience_timing['generation_ms']>=0
        finally:await b.stop()
    asyncio.run(run())


def test_http_policy_same_origin_owner_and_cas(tmp_path):
    async def run():
        b,c=await lab(tmp_path);app=FastAPI();app.include_router(routes.router)
        headers={'Origin':'http://127.0.0.1:8000','X-Saya-QQ-UI':'1'}
        try:
            with patch.object(routes,'qq_bridge',b):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
                    path='/api/qq/dialogue-experience'
                    assert (await client.get(path)).status_code==401
                    assert (await client.post('/api/qq/ui-session',headers=headers)).status_code==200
                    assert (await client.get(path)).json()['owners']==['20000']
                    body={'owner_id':'20000','revision':0,'confirmed':True,'values':{'enabled':True}}
                    assert (await client.put(path,json=body,headers={'Origin':'https://foreign.invalid'})).status_code==401
                    assert (await client.put(path,json=body|{'owner_id':'30000'},headers=headers)).status_code==403
                    assert (await client.put(path,json=body|{'confirmed':False},headers=headers)).status_code==403
                    assert (await client.put(path,json=body,headers=headers)).status_code==200
                    assert (await client.put(path,json=body,headers=headers)).status_code==409
        finally:await b.stop()
    asyncio.run(run())


def test_command_receipt_does_not_enter_history(tmp_path):
    async def run():
        b,c=await lab(tmp_path)
        try:
            submit(b,'/experience on');await b.queue.join()
            assert b.dialogue_experience.state()['values']['enabled']
            assert '【操作回执】' in c.sent[0][1]
            assert b.store.conversation('10000:private:20000:shared')[0]==[]
            assert not row(b) and not c.voices
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('finish,content,expected',[('stop','{"kind":"none","voice":false}','semantic_none'),
    ('length','{"kind":','selector_truncated'),('stop','not json','selector_json')])
def test_real_selection_stream_records_finish_reason_and_separate_token_budget(tmp_path,server,finish,content,expected):
    async def run():
        b,c=await lab(tmp_path,answer='晚安，我在。');enable(b);captured=[]
        async def single(req,channel_turn):
            captured.append(req);channel_turn['provider_finish_reason']=finish
            return response(content)
        b.responder=SimpleNamespace(credentials=lambda:{'configured':True,'api_key':'fixture','api_url':'http://mock/v1','model':'fixture'},request_type=server.ChatWithMotion,single_response=single)
        from saya_app.services.qq_expressions import QQExpressions
        b.expressions._select=QQExpressions._select.__get__(b.expressions,QQExpressions)
        try:
            msg=normalize_event(event('晚安'),b.settings)[0]
            body,plan=await b.expressions.plan(msg,'晚安，我在。')
            assert plan['decision_reason']==expected and plan['selector_finish']==finish
            assert captured[0].max_output_tokens==768 and captured[0].thinking_effort=='none'
            assert not captured[0].agent_mode and not captured[0].memory_write_enabled
        finally:await b.stop()
    asyncio.run(run())


def test_diagnostic_failure_cannot_relabel_confirmed_text(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='我在。',choice={'kind':'none'})
        def fail(*args):raise OSError('DISK_PRIVATE')
        b.dialogue_experience.record=fail;b.dialogue_experience.outcome=fail
        try:
            submit(b);await b.queue.join()
            assert c.sent[0][1]=='我在。' and row(b)['state']=='skipped'
            assert b.last_experience_error=='diagnostics_unavailable' and not b.last_error
        finally:await b.stop()
    asyncio.run(run())


def test_combined_optout_blocks_both_even_if_selector_wants_them(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='我在。',choice={'kind':'kaomoji','id':'smile','voice':True,'anchor':'我在','placement':'before'})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            enable(b);submit(b,'这轮别发声音或表情，只告诉我一加一等于几。');await b.queue.join()
            assert c.sent and not tts.calls and not c.reactions
        finally:await b.stop()
    asyncio.run(run())


def test_rollback_requirement_distinguishes_disabled_policy_from_pending_short_speech(tmp_path):
    from saya_app.services.qq_rollback import memory_rollback_requirements
    async def run():
        b,c=await lab(tmp_path/'knowledge_base/qq')
        # lab's store lives directly in the requested directory.
        actual=b.store.path
        try:
            root=actual.parent.parent.parent
            assert 'qq_dialogue_experience_v1' not in memory_rollback_requirements(root)
            enable(b)
            assert 'qq_dialogue_experience_v1' in memory_rollback_requirements(root)
            b.dialogue_experience.configure('20000',1,{'enabled':False})
            assert 'qq_dialogue_experience_v1' not in memory_rollback_requirements(root)
            assert 'qq_dialogue_experience_components_v2' in memory_rollback_requirements(root)
            msg=normalize_event(event('在吗'),b.settings)[0]
            scope=msg.scope+':shared'
            identifier=qq_delivery.create(b.store,msg,scope,'我在。',240)
            b.expressions.bind(identifier,scope,{'kind':'voice','speech_span':speech_span('我在。','我在。',120)})
            assert 'qq_dialogue_experience_v1' in memory_rollback_requirements(root)
            with b.store.connect() as db:db.execute('update qq_expression_deliveries set expires=0 where result_id=?',(identifier,))
            assert 'qq_dialogue_experience_v1' not in memory_rollback_requirements(root)
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('enabled',[False,True])
def test_random_dedup_preserves_independent_speech_candidate_with_same_visual_kind(tmp_path,enabled):
    async def run():
        b,c=await lab(tmp_path,answer='我在，安心休息吧。',mode='random',choice={'kind':'none','voice':False,
            'alternatives':[{'kind':'none','voice':True}]})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            if enabled:enable(b)
            def pick(options):
                assert len(options)==2 and options[0]=={'kind':'none'} and options[1]=={'kind':'none','voice':True}
                return options[1]
            with patch('saya_app.services.qq_expressions.secrets.choice',side_effect=pick):submit(b,'陪我待一会儿');await b.queue.join()
            assert len(c.voices)==1 and json.loads(row(b)['plan'])['decision_reason']=='random_choice'
            assert b.expressions.state()['mode']=='random'
        finally:await b.stop()
    asyncio.run(run())


def test_actual_tts_failure_has_distinct_diagnostic_without_repeating_text(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='我在。',choice={'kind':'none','voice':True})
        tts=FakeTTS();tts.fail=True;b.voice=QQVoice(b,tmp_path,tts)
        try:
            enable(b);submit(b,'陪我聊聊');await b.queue.join()
            assert len(c.sent)==1 and not c.voices and row(b)['state']=='failed'
            summary=b.dialogue_experience.summary()
            assert summary['voice_outcome_reasons']=={'tts_or_audio_failed':1}
            assert summary['outcomes']=={'failed':1}
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('kind,key',[('kaomoji','happy'),('kaomoji','shy')])
def test_valid_inline_visual_cannot_bypass_rejected_speech_span(tmp_path,kind,key):
    async def run():
        b,c=await lab(tmp_path,answer='我在，安心休息吧。',choice={'kind':kind,'id':key,'voice':True,
            'anchor':'我在','placement':'before','speech_anchor':'未在正文出现的话。'})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            enable(b);submit(b,'陪我待一会儿');await b.queue.join()
            assert len(c.reactions)==1 and c.sent
            assert not c.voices and not tts.calls
            assert json.loads(row(b)['plan'])['voice_reason']=='invalid_speech_span'
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('pacing,short_voice,expect_audio',[(False,True,True),(True,False,False),(False,False,False)])
def test_components_can_enable_short_voice_without_changing_reply_pacing(tmp_path,pacing,short_voice,expect_audio):
    from saya_app.services.qq_dialogue_experience import component_active
    async def run():
        sentence='我在，安心歇一会儿。';body='这是需要保留的说明。'*20+'\n'+sentence
        b,c=await lab(tmp_path,answer=body,choice={'kind':'none','voice':True,'speech_anchor':sentence})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            b.dialogue_experience.configure('20000',0,{'enabled':True,'concise_replies':pacing,'short_voice':short_voice})
            assert component_active(b.dialogue_experience,'concise_replies')==pacing
            assert component_active(b.dialogue_experience,'short_voice')==short_voice
            submit(b,'陪我说说话');await b.queue.join();assert bool(c.voices)==expect_audio
            if expect_audio:assert tts.calls[0][0]==sentence
            # A legacy master-only save must not reset independent preferences.
            b.dialogue_experience.configure('20000',1,{'enabled':False})
            state=b.dialogue_experience.state()['values'];assert state['concise_replies']==pacing and state['short_voice']==short_voice
        finally:await b.stop()
    asyncio.run(run())


def test_component_command_changes_only_requested_preference(tmp_path):
    async def run():
        b,c=await lab(tmp_path)
        try:
            submit(b,'/experience concise off',1);await b.queue.join()
            assert b.dialogue_experience.state()['values']=={'enabled':False,'concise_replies':False,'short_voice':True}
            submit(b,'/experience on',2);await b.queue.join()
            assert b.dialogue_experience.state()['values']=={'enabled':True,'concise_replies':False,'short_voice':True}
            assert all('【操作回执】' in x[1] for x in c.sent) and not row(b)
        finally:await b.stop()
    asyncio.run(run())
