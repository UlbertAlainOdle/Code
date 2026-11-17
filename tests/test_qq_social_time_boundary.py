import asyncio,json,time
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from fastapi.responses import StreamingResponse
from test_qq_topic_routing import topics,item,create
from test_qq_social import lab
from test_qq_delivery import event
from test_qq_chat_runtime import server
from test_qq_task_scheduler import make
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.chat_time_context import previous_web_time,parse_time
from saya_app.services import conversation_mood as mood


@pytest.mark.parametrize('format',['naive','offset','utc','numeric','datetime','missing','invalid'])
@pytest.mark.parametrize('newer',[False,True])
def test_real_archive_time_adapter_compares_safely_with_poke_time(tmp_path,format,newer):
    async def run():
        b,c=await lab(tmp_path)
        try:
            m=normalize_event(event(),b.settings)[0];m.key=m.scope+':poke:fixture'
            b.social.commit(m,mood.advance(mood.neutral(),'angry'))
            with b.store.connect() as db:at=db.execute('select at from qq_affect where scope=?',(m.scope,)).fetchone()[0]
            stamp=datetime.fromtimestamp(at+(60 if newer else -60),timezone(timedelta(hours=8)))
            value={'naive':stamp.replace(tzinfo=None).isoformat(),'offset':stamp.isoformat(),
                'utc':stamp.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),'numeric':stamp.timestamp(),
                'datetime':stamp,'missing':None,'invalid':'unavailable'}[format]
            manager=SimpleNamespace(load_session_full=lambda *a,**kw:[{'role':'ai','agent_id':'saya','content':'earlier','time':value}])
            b.responder=SimpleNamespace(mood_snapshot=lambda sid:mood.neutral(),mood_time=lambda sid:previous_web_time(manager,sid))
            result=b.social.prompt_state(m,True,'session_fixture.jsonl')
            if newer and parse_time(value):assert result is None
            else:assert result['anger']>0
            b.responder.mood_snapshot=lambda sid:mood.neutral()|{'is_locked':True}
            assert b.social.prompt_state(m,True,'session_fixture.jsonl')['is_locked']
        finally:await b.stop()
    asyncio.run(run())


def test_actual_bridge_poke_then_topic_write_and_later_stopped_event_question_keep_replying(server,tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            calls=[]
            manager=SimpleNamespace(load_session_full=lambda *a,**kw:[{'role':'ai','content':'earlier reading reply',
                'time':datetime.fromtimestamp(time.time()-120,timezone.utc).isoformat()}])
            async def generate(req,channel_turn):
                calls.append(req.user_input)
                assert channel_turn.get('qq_mood_state') is not None
                channel_turn['context_selector'](channel_turn['history'],channel_turn['history_times'])
                async def stream():yield 'event: answer_complete\ndata: {"text":"可以慢慢聊聊你现在的感受。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            b.responder=responder;b.bind_responder(responder)
            responder.social=b.social
            responder.mood_snapshot=lambda sid:mood.neutral()
            responder.mood_time=lambda sid:previous_web_time(manager,sid)
            m=msg('其实现在的我感觉对读书有点浮躁读不下去了');m.key=m.scope+':poke:fixture'
            b.social.commit(m,mood.advance(mood.neutral(),'curious'))
            text='其实现在的我感觉对读书有点浮躁读不下去了'
            choice[0]={'items':[item(text,personal=create())]}
            raw=event(text,80001,20000)
            assert b.submit_signed(json.dumps(raw).encode(),'Bearer inbound')[0]=='queued'
            await asyncio.wait_for(b.queue.join(),5)
            cards=s.people._canonical('20000');assert len(cards)==1
            identifier=cards[0]['id'];facts=cards[0]['facts']
            assert len(calls)==1 and b.client.sent[-1][1]=='可以慢慢聊聊你现在的感受。'
            s.command(msg('/events stop '+identifier));choice[0]={'items':[]}
            for n,text in enumerate(['还记得之前的安排吗','现在我们随便聊聊'],80002):
                assert b.submit_signed(json.dumps(event(text,n,20000)).encode(),'Bearer inbound')[0]=='queued'
                await asyncio.wait_for(b.queue.join(),5)
            assert len(calls)==3 and all('暂时无法生成' not in x[1] and '回答生成失败' not in x[1] for x in b.client.sent)
            current=s.people._canonical('20000')[0]
            assert current['id']==identifier and current['tracking']=='suppressed' and current['facts']==facts
            with b.store.connect() as db:
                assert db.execute("select count(*) from inbox where state='failed'").fetchone()[0]==0
                assert db.execute('select count(*) from qq_event_result_dependencies').fetchone()[0]==3
    asyncio.run(run())


def test_failure_log_has_type_and_site_without_exception_body_or_credentials(tmp_path,caplog):
    async def run():
        async def fail(*args):raise TypeError('PRIVATE_MESSAGE secret-api-key fixture-secret')
        b=make(tmp_path,fail);await b.start()
        try:
            assert b.submit_signed(json.dumps(event('ordinary fixture',90001,20000)).encode(),'Bearer inbound')[0]=='queued'
            await b.queue.join()
            logs=[r.getMessage() for r in caplog.records if 'qq_response_failed' in r.getMessage()]
            assert len(logs)==1 and 'type=TypeError' in logs[0] and 'site=test_qq_social_time_boundary.py:' in logs[0]
            assert not any(x in logs[0] for x in ['PRIVATE_MESSAGE','secret-api-key','fixture-secret','ordinary fixture'])
        finally:await b.stop()
    asyncio.run(run())
