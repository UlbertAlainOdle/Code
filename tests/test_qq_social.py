import asyncio,json,time
from dataclasses import replace
from unittest.mock import patch
import pytest
from test_qq_expressions import lab,row
from test_qq_task_scheduler import until
from test_qq_delivery import event
from test_qq_voice import FakeTTS,submit
from saya_app.services.qq_bridge import normalize_event,OneBotClient
from saya_app.services.qq_voice import QQVoice
from saya_app.services import conversation_mood as mood


def notice(**values):
    return dict(time=int(time.time()),self_id=10000,post_type='notice',notice_type='notify',sub_type='poke',user_id=20000,target_id=10000,event_id='fixture')|values


def poke(b,e=None,auth='Bearer inbound'):return b.submit_signed(json.dumps(e or notice()).encode(),auth)


@pytest.mark.parametrize('action,expected',[('silent',0),('poke',1),('text',0)])
def test_authenticated_notice_uses_social_decision_not_ordinary_message_commands(tmp_path,action,expected):
    async def run():
        b,c=await lab(tmp_path);pokes=[];captured=[]
        async def send_poke(m):pokes.append(m)
        async def decision(prompt,**kw):captured.append(json.loads(prompt.rsplit('\n',1)[1]));return {'action':action,'text':'嗯？[Mood: curious]','mood':'curious'}
        async def ordinary(*a):raise AssertionError('poke must not run ordinary responder')
        c.send_poke=send_poke;b.responder=ordinary;b.expressions.decide=decision
        try:
            assert poke(b)[0]=='queued';await b.queue.join()
            assert len(pokes)==expected and len(c.sent)==(1 if action=='text' else 0)
            if c.sent:assert c.sent[0][1]=='嗯？' and not c.sent[0][2]
            assert captured[0]['recent_pokes_60s']==1 and captured[0]['is_owner']
            with b.store.connect() as db:assert db.execute('select state from qq_poke_events').fetchone()[0]==action
            assert poke(b)[0]=='ignored' and len(pokes)==expected
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('changes',[
    {'target_id':30000},{'user_id':10000},{'user_id':40000},{'group_id':88888},{'group_id':'bad'},
    {'self_id':99999},{'time':0},{'time':float('nan')},{'sub_type':'other'}])
def test_wrong_target_self_echo_unapproved_scope_and_stale_notice_are_ignored(tmp_path,changes):
    async def run():
        b,c=await lab(tmp_path)
        try:assert poke(b,notice(**changes))[0]=='ignored';assert not c.sent and b.queue.empty()
        finally:await b.stop()
    asyncio.run(run())


def test_unsigned_notice_cannot_grant_poke_or_mood_authority(tmp_path):
    async def run():
        b,c=await lab(tmp_path)
        try:
            assert poke(b,auth='Bearer wrong')[0]=='unauthorized'
            with b.store.connect() as db:assert db.execute('select count(*) from qq_poke_events').fetchone()[0]==0
        finally:await b.stop()
    asyncio.run(run())


def test_poke_context_is_scrubbed_and_memory_change_blocks_late_text(tmp_path):
    from types import SimpleNamespace
    async def run():
        b,c=await lab(tmp_path);revision=[1];seen=[]
        b.memory_management=SimpleNamespace(prepare_scope=lambda scope,history:([],{'rev':1}),
            kb=SimpleNamespace(memory_use=SimpleNamespace(snapshot=lambda:{'rev':revision[0]})))
        scope='10000:private:20000:shared';b.store.save_conversation(scope,['Doctor: removed secret','Saya: removed private fact'],'session_qq_fixture.jsonl')
        async def decide(prompt,**kw):seen.append(prompt);revision[0]=2;return {'action':'text','text':'late old answer','mood':'happy'}
        b.expressions.decide=decide
        try:
            poke(b);await b.queue.join()
            assert seen and 'removed secret' not in seen[0] and not c.sent
        finally:await b.stop()
    asyncio.run(run())


def test_burst_is_coalesced_and_counted_without_forced_anger(tmp_path):
    async def run():
        b,c=await lab(tmp_path);captured=[];entered=asyncio.Event();release=asyncio.Event()
        async def decide(prompt,**kw):entered.set();await release.wait();captured.append(json.loads(prompt.rsplit('\n',1)[1]));return {'action':'silent','mood':'happy'}
        b.expressions.decide=decide
        try:
            poke(b);await entered.wait()
            for n in range(6):assert poke(b,notice(event_id=str(n)))[0]=='ignored'
            release.set();await b.queue.join()
            with b.store.connect() as db:assert db.execute('select count(*) from qq_poke_events').fetchone()[0]==7
            base=time.time()
            with patch('saya_app.services.qq_social.time.time',return_value=base+4):
                assert poke(b,notice(time=base+4,event_id='later'))[0]=='queued';await b.queue.join()
            assert captured[-1]['recent_pokes_60s']==8 and not c.sent
            m=normalize_event(event(),b.settings)[0];state=b.social.view(m)
            assert state['happiness']>50 and state['anger']==0
        finally:release.set();await b.stop()
    asyncio.run(run())


def test_poke_unknown_transport_is_not_repeated_or_replaced_with_text(tmp_path):
    async def run():
        b,c=await lab(tmp_path);calls=[]
        async def decide(*a,**kw):return {'action':'poke','mood':'curious'}
        async def fail(m):calls.append(m);raise TimeoutError()
        b.expressions.decide=decide;c.send_poke=fail
        try:
            poke(b);await b.queue.join();assert len(calls)==1 and not c.sent
            with b.store.connect() as db:assert db.execute('select state from qq_poke_events').fetchone()[0]=='unknown'
            with b.store.connect() as db:assert db.execute("select state from inbox where event_key like '%:poke:%'").fetchone()[0]=='delivery_unknown'
            assert poke(b)[0]=='ignored' and len(calls)==1
        finally:await b.stop()
    asyncio.run(run())


def test_revoked_group_or_cancelled_poke_cannot_send_late_action(tmp_path):
    async def run():
        b,c=await lab(tmp_path);entered=asyncio.Event();release=asyncio.Event();pokes=[]
        async def decide(*a,**kw):entered.set();await release.wait();return {'action':'poke','mood':'angry'}
        async def send(m):pokes.append(m)
        b.expressions.decide=decide;c.send_poke=send
        try:
            poke(b,notice(group_id=90000));await entered.wait()
            b.settings=b.settings.model_copy(update={'allowed_groups':set()});release.set();await b.queue.join()
            assert not pokes and not c.sent
        finally:release.set();await b.stop()
    asyncio.run(run())


def test_affect_is_actor_scoped_decays_with_real_time_and_respects_lock(tmp_path):
    async def run():
        b,c=await lab(tmp_path)
        try:
            private=normalize_event(event(),b.settings)[0];group=normalize_event(event(group_id=90000),b.settings)[0]
            state=mood.advance(mood.neutral(),'angry');b.social.commit(private,state)
            assert b.social.view(group)['anger']==0
            now=time.time()
            with patch('saya_app.services.qq_social.time.time',return_value=now+7200):assert b.social.view(private)['anger']<state['anger']
            with patch('saya_app.services.qq_social.time.time',return_value=now+24*3600):assert b.social.view(private)['speech_mood']=='neutral'
            private.key+='locked';b.social.commit(private,state|{'is_locked':True})
            with patch('saya_app.services.qq_social.time.time',return_value=now+7200):assert b.social.view(private)['anger']==state['anger']
        finally:await b.stop()
    asyncio.run(run())


def test_private_poke_mood_influences_next_reply_but_newer_web_mood_and_lock_win(tmp_path):
    from types import SimpleNamespace
    async def run():
        b,c=await lab(tmp_path)
        b.responder=SimpleNamespace(mood_snapshot=lambda sid:mood.neutral(),mood_time=lambda sid:0)
        try:
            m=normalize_event(event(),b.settings)[0]
            assert b.social.prompt_state(m,True,'fixture') is None
            m.key=m.scope+':poke:test';b.social.commit(m,mood.advance(mood.neutral(),'angry'))
            assert b.social.prompt_state(m,True,'fixture')['anger']>0
            b.responder.mood_time=lambda sid:time.time()+1
            assert b.social.prompt_state(m,True,'fixture') is None
            b.responder.mood_snapshot=lambda sid:mood.neutral()|{'is_locked':True}
            assert b.social.prompt_state(m,True,'fixture')['is_locked']
        finally:await b.stop()
    asyncio.run(run())


def test_known_visual_catalogue_and_unicode_faces_never_enter_tts_text(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='加油(๑•̀ㅂ•́)و✧，我在这里。😊[微笑]',mode='voice');tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b);await b.queue.join()
            assert len(tts.calls)==1 and tts.calls[0][0]=='加油，我在这里。'
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('kind,key',[('kaomoji','happy'),('kaomoji','shy')])
def test_inline_and_voice_coexist_without_extra_visual_bubble_or_spoken_decorations(tmp_path,kind,key):
    async def run():
        b,c=await lab(tmp_path,answer='晚安。安心休息。',choice={'kind':kind,'id':key,'voice':True,'anchor':'晚安。','placement':'after'})
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit(b,'晚安');await b.queue.join()
            assert len(c.sent)==1 and len(c.reactions)==1 and len(c.voices)==1
            assert tts.calls[0][0]=='晚安。安心休息。' and json.loads(row(b)['plan'])['inline_delivered']
            assert row(b)['state']=='sent'
        finally:await b.stop()
    asyncio.run(run())


def test_sticker_and_voice_request_remain_mutually_exclusive(tmp_path):
    async def run():
        b,c=await lab(tmp_path,choice={'kind':'sticker','id':'a'*32,'voice':True});tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:submit(b);await b.queue.join();assert len(c.reactions)==1 and not c.voices and not tts.calls
        finally:await b.stop()
    asyncio.run(run())


def test_bad_inline_anchor_does_not_turn_into_a_trailing_face(tmp_path):
    async def run():
        b,c=await lab(tmp_path,choice={'kind':'kaomoji','id':'smile','anchor':'not in reply','placement':'after'})
        try:submit(b);await b.queue.join();assert c.sent and not c.reactions
        finally:await b.stop()
    asyncio.run(run())


def test_intentional_kaomoji_only_reply_is_not_erased_or_spoken(tmp_path):
    async def run():
        b,c=await lab(tmp_path,answer='(^_^)',mode='voice');tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        try:submit(b,'只想看一个颜文字');await b.queue.join();assert c.sent[0][1]=='(^_^)' and not tts.calls and not c.reactions
        finally:await b.stop()
    asyncio.run(run())


def test_native_inline_segment_sits_between_text_and_poke_targets_original_actor(tmp_path):
    from test_qq_delivery import config
    async def run():
        c=OneBotClient(config());calls=[]
        async def native(action,payload):calls.append((action,payload));return {'message_id':800}
        c.call=native;m=normalize_event(event(group_id=90000),config())[0]
        try:
            await c.send_inline(m,'晚安。安心休息。',kind='kaomoji',key='shy',offset=3,reference=False,mention=False)
            assert calls[0][1]['message']==[{'type':'text','data':{'text':'晚安。'}},{'type':'text','data':{'text':'(〃▽〃)'}},{'type':'text','data':{'text':'安心休息。'}}]
            await c.send_poke(m);assert calls[-1]==('send_poke',{'user_id':20000,'group_id':90000})
        finally:await c.close()
    asyncio.run(run())


def test_poke_return_loop_is_bounded_without_forcing_an_angry_reply(tmp_path):
    async def run():
        b,c=await lab(tmp_path);pokes=[]
        async def decide(*a,**kw):return {'action':'poke','mood':'happy'}
        async def send(m):pokes.append(m)
        b.expressions.decide=decide;c.send_poke=send;now=time.time()
        try:
            for n in range(3):
                with patch('saya_app.services.qq_social.time.time',return_value=now+n*4):
                    poke(b,notice(time=now+n*4,event_id=str(n)));await b.queue.join()
            assert len(pokes)==2 and not c.sent
        finally:await b.stop()
    asyncio.run(run())


def test_disabling_mood_preserves_original_setting_and_does_not_write_new_affect(tmp_path):
    from types import SimpleNamespace
    async def run():
        b,c=await lab(tmp_path);b.responder=SimpleNamespace(mood_enabled=lambda:False)
        try:
            m=normalize_event(event(),b.settings)[0];b.social.commit(m,mood.advance(mood.neutral(),'angry'))
            assert b.social.view(m)==mood.neutral()
            with b.store.connect() as db:assert db.execute('select count(*) from qq_affect').fetchone()[0]==0
        finally:await b.stop()
    asyncio.run(run())


def test_structured_reply_mood_reaches_actor_state_without_visible_control_tag(tmp_path):
    from types import SimpleNamespace
    from starlette.responses import StreamingResponse
    from saya_app.services.qq_chat import QQChatResponder
    async def run():
        b,c=await lab(tmp_path);b.expressions.configure('20000',1,'off',confirmed=True);captured=[]
        state=mood.advance(mood.neutral(),'curious')
        async def single(req,channel_turn):
            captured.append(channel_turn)
            async def body():yield 'event: answer_complete\ndata: '+json.dumps({'text':'我有点好奇。[Mood: curious]','mood_state':state})+'\n\n'
            return StreamingResponse(body())
        b.responder=QQChatResponder(b.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
        b.responder.social=b.social
        try:
            b.submit_signed(json.dumps(event('你好',group_id=90000)).encode(),'Bearer inbound');await b.queue.join()
            assert captured[0]['qq_mood_state']==mood.neutral() and c.sent[0][1]=='我有点好奇。'
            m=normalize_event(event(group_id=90000),b.settings)[0];assert b.social.view(m)['curiosity']>60
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('choice',[{'action':[],'mood':{}},{'action':'text','text':'[MOOD_SAYA: angry]'},{'action':'text','text':'[CQ:at,qq=all]'},{'action':'text','text':{}}])
def test_malformed_poke_decision_is_silent_without_protocol_or_filter_leak(tmp_path,choice):
    async def run():
        b,c=await lab(tmp_path)
        async def decide(*a,**kw):return choice
        b.expressions.decide=decide
        try:poke(b);await b.queue.join();assert not c.sent
        finally:await b.stop()
    asyncio.run(run())
