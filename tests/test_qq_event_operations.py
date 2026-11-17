import asyncio
from dataclasses import replace
from copy import deepcopy
import json
import pytest
from test_qq_topic_routing import topics,item,create
from test_qq_delivery import event
from test_qq_task_scheduler import until
from saya_app.services import qq_delivery


async def seed(s,msg,choice,count=2,dual=False):
    for i in range(count):
        text='我负责整理清单'+str(i)+'。'
        m=msg('晨光资料'+str(i)+'：'+text,group=90000 if dual else None)
        choice[0]={'items':[item(text,personal=create(),group=create() if dual else None,mode='commitment')]}
        await s.observe(m,[])
    return s.people._canonical('20000')


async def deliver(b,s,m,answer):
    scope=s.operations.scope(m)
    identifier=qq_delivery.create(b.store,m,scope,answer,12000,kind='control')
    await qq_delivery.send_batch(b.store,b.client,m,scope,identifier,30,False)
    s.operations.delivered(m,identifier)
    return identifier


def test_bulk_stop_has_delivery_bound_consent_and_atomic_result(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice)
            request=msg('忘掉当前所有事件');answer=await s.operations.handle(request)
            assert '共2条' in answer and '待确认' in answer
            assert all(c.get('tracking')!='suppressed' for c in s.people._canonical('20000'))
            assert '尚未确认送达' in str(await s.operations.handle(msg('对的')))
            await deliver(b,s,request,answer)
            assert '共2条' in str(await s.operations.handle(msg('对的')))
            assert all(c['tracking']=='suppressed' for c in s.people._canonical('20000'))
            with s.store.connect() as db:
                result=json.loads(db.execute('select data from qq_event_operation_results').fetchone()[0])
                assert len(result['changes'])==2 and all('before' in x and 'after' in x for x in result['changes'])
            assert len(prompts)==2 # only the two mocked topic captures; bulk uses no model
    asyncio.run(run())


@pytest.mark.parametrize('change',['new_card','expiry','permission','body','intervening','wrong_actor','wrong_scope','failed_delivery'])
def test_bulk_consent_rejects_changed_or_unbound_context_without_partial_write(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice)
            request=msg('忘掉当前所有事件');answer=await s.operations.handle(request)
            if change!='failed_delivery':await deliver(b,s,request,answer)
            if change=='new_card':
                m=msg('另一份新资料：我愿意收集参考。');choice[0]={'items':[item('我愿意收集参考。',personal=create())]};await s.observe(m,[])
            elif change=='expiry':now[0]+=601
            elif change=='permission':
                p=s.unified.policy();s.unified.configure('20000',p['revision'],{**p['values'],'group_total':21})
            elif change=='body':
                with s.store.connect() as db:
                    row=db.execute('select id,data from qq_episode_cards limit 1').fetchone();c=json.loads(row['data'])
                    c['facts'][0]['quote']='一个真正变化的新事实'
                    db.execute('update qq_episode_cards set data=?,revision=revision+1 where id=?',(json.dumps(c),row['id']))
            elif change=='intervening':assert await s.operations.handle(msg('我想聊聊今天的阅读体验')) is None
            confirm=msg('对的',user=30000 if change=='wrong_actor' else 20000,group=90000 if change=='wrong_scope' else None)
            await s.operations.handle(confirm)
            assert not any(c.get('tracking')=='suppressed' for c in s.people._canonical('20000'))
            with s.store.connect() as db:assert db.execute('select count(*) from qq_event_operation_results').fetchone()[0]==0
    asyncio.run(run())


def test_bulk_database_failure_rolls_back_every_card(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);request=msg('忘掉当前所有事件');a=await s.operations.handle(request);await deliver(b,s,request,a)
            with s.store.connect() as db:
                db.execute("create trigger fail_second before update on qq_episode_cards when old.id='"+cards[-1]['id']+"' begin select raise(ABORT,'fixture'); end")
            assert '回滚' in str(await s.operations.handle(msg('对的')))
            assert s.people._canonical('20000')==cards
    asyncio.run(run())


def test_semantic_delete_freezes_original_request_and_executes_only_bound_yes(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=1);identifier=cards[0]['id']
            request=msg('把晨光资料的事件删掉，卡片正文也不要保留')
            choice[0]={'action':'delete','scope':'personal','quantifier':'one','target_ids':[identifier],
                'intent_quote':'卡片正文也不要保留','mode':'request','clarify':False}
            answer=await s.operations.handle(request)
            assert '删除事件卡正文' in answer
            await deliver(b,s,request,answer)
            assert '已删除事件卡正文' in str(await s.operations.handle(msg('对的')))
            assert not s.people._canonical('20000')
            assert request.text in prompts[-1] and identifier in prompts[-1]
    asyncio.run(run())


def test_scope_correction_replans_using_delivered_question_and_original_intent(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=1,dual=True)
            request=msg('忘掉所有事件',group=90000);a=await s.operations.handle(request)
            assert '需要先明确' in a;await deliver(b,s,request,a)
            choice[0]={'action':'stop','scope':'personal','quantifier':'all','target_ids':[],
                'intent_quote':'忘掉所有事件','mode':'request','clarify':False}
            correction=msg('只处理所有个人事件',group=90000)
            a=await s.operations.handle(correction)
            assert 'registered_delivered_question' in prompts[-1] and '忘掉所有事件' in prompts[-1]
            assert '待确认' in a
            await deliver(b,s,correction,a)
            assert '共1条' in str(await s.operations.handle(msg('对的',group=90000)))
            # Shared public card still exists for other members.
            assert s.unified.cards(msg('/events group',user=30000,group=90000))
    asyncio.run(run())


def test_group_following_stop_does_not_modify_public_card(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=1,dual=True)
            m=msg('本群所有事件我都不跟进',group=90000);before=s.unified.cards(m)[0]
            a=await s.operations.handle(m);await deliver(b,s,m,a)
            assert '共1条' in str(await s.operations.handle(msg('对的',group=90000)))
            assert not s.unified.cards(m)
            assert before['id'] not in str(s.unified.command(msg('/events group',group=90000)))
            assert before['id'] in str(s.unified.command(msg('/events group stopped',group=90000)))
            other=s.unified.cards(msg('/events group',user=30000,group=90000))[0]
            assert before['revision']==other['revision'] and before['contributions']==other['contributions']
    asyncio.run(run())


def test_group_common_bulk_requires_every_coordinator(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=2,dual=True)
            m=msg('删除全群所有共同事件',group=90000)
            assert '须逐条具备协调人' in str(await s.operations.handle(m))
            assert len(s.unified.cards(m))==2
    asyncio.run(run())


def test_real_bridge_delivers_and_accepts_exact_operation_question(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=1)
            b.bind_responder(lambda *_:'不应调用普通模型')
            assert b.submit(event('忘掉当前所有事件',98001,20000))[0]=='queued'
            await until(lambda:bool(b.client.sent));await until(lambda:not b.task_registry.list_active())
            with s.store.connect() as db:assert db.execute('select state from qq_event_operation_sessions').fetchone()[0]=='waiting'
            b.submit(event('对的',98002,20000));await until(lambda:len(b.client.sent)>=2);await until(lambda:not b.task_registry.list_active())
            assert s.people._canonical('20000')[0]['tracking']=='suppressed'
    asyncio.run(run())


@pytest.mark.parametrize('quoted',[False,True])
def test_intervening_bot_question_requires_explicit_trusted_quote(tmp_path,quoted):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=1)
            m=msg('忘掉当前所有事件');a=await s.operations.handle(m);await deliver(b,s,m,a)
            with s.store.connect() as db:
                row=db.execute("select data from qq_event_operation_sessions where state='waiting'").fetchone()
                receipt=json.loads(row[0])['receipts'][-1]
            unrelated=msg('今天想聊点什么');await deliver(b,s,unrelated,'你想先聊哪本书？')
            yes=msg('对的');yes.reply_to=receipt if quoted else None
            result=await s.operations.handle(yes)
            assert (s.people._canonical('20000')[0].get('tracking')=='suppressed')==quoted
    asyncio.run(run())


def test_pending_negative_qualification_never_executes_original_request(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=1)
            m=msg('忘掉当前所有事件');a=await s.operations.handle(m);await deliver(b,s,m,a)
            assert '没有修改' in str(await s.operations.handle(msg('是那件，但不要忘掉')))
            assert s.people._canonical('20000')==cards
    asyncio.run(run())


def test_semantic_failure_keeps_dialogue_and_cannot_fall_back_to_lexical_write(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=1)
            async def failed(*args,**kwargs):raise TimeoutError('fixture')
            s.decide=failed;m=msg('我想忘掉晨光资料的那份记录，卡片也删除')
            answer=await s.operations.handle(m)
            assert '没有修改' in answer and m.event_control_dialogue and m.event_operation_checked
            assert s.people._canonical('20000')==cards
    asyncio.run(run())


def test_terminal_and_paused_retained_cards_are_included_in_all(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=2)
            s.command(msg('/events complete '+cards[0]['id']));s.command(msg('/events pause '+cards[1]['id']))
            m=msg('忘掉当前所有事件');a=await s.operations.handle(m)
            assert '共2条' in a;await deliver(b,s,m,a)
            assert '共2条' in str(await s.operations.handle(msg('对的')))
            assert all(c['tracking']=='suppressed' for c in s.people._canonical('20000'))
    asyncio.run(run())


def test_paraphrased_operation_uses_router_detection_then_semantic_review_without_capture(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=1)
            text='不必再把晨光资料作为持续事项了';calls=[]
            async def classify(prompt,**kw):
                calls.append(kw['purpose'])
                if kw['purpose']=='qq_topic_route':return {'items':[],'event_operation':True}
                return {'action':'stop','scope':'personal','quantifier':'one','target_ids':[cards[0]['id']],
                    'intent_quote':text,'mode':'request','clarify':False}
            s.decide=classify;m=msg(text)
            assert await s.operations.handle(m) is None
            answer=await s.unified.capture(m)
            assert '待确认' in answer and calls==['qq_topic_route','qq_event_operation']
            assert s.people._canonical('20000')==cards
            assert not getattr(m,'topic_results',None)
    asyncio.run(run())


def test_forgot_all_identifiers_is_not_local_bulk_authorization(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=1)
            choice[0]={'action':'none'}
            m=msg('我不小心忘记所有资料事件的编号了')
            assert s.operations.bulk_plan(m) is None
            assert await s.operations.handle(m) is None
            assert s.people._canonical('20000')==cards
    asyncio.run(run())


def test_partial_preview_becomes_confirmable_only_after_same_result_continuation(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=2)
            m=msg('忘掉当前所有事件');a=await s.operations.handle(m);scope=s.operations.scope(m)
            identifier=qq_delivery.create(b.store,m,scope,a,100,kind='control')
            await qq_delivery.send_batch(b.store,b.client,m,scope,identifier,1,False)
            s.operations.delivered(m,identifier)
            with s.store.connect() as db:assert db.execute('select state from qq_event_operation_sessions').fetchone()[0]=='prepared'
            cont=msg('/continue '+identifier)
            assert await s.operations.handle(cont) is None
            await qq_delivery.send_batch(b.store,b.client,cont,scope,identifier,30,False)
            s.operations.delivered(cont,identifier)
            assert '共2条' in str(await s.operations.handle(msg('对的')))
            assert all(c['tracking']=='suppressed' for c in s.people._canonical('20000'))
    asyncio.run(run())


def test_event_question_supersedes_previous_search_or_save_consent(tmp_path):
    async def run():
        from saya_app.services.qq_action_proposals import QQActionProposals
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=1)
            m=msg('忘掉当前所有事件');scope=s.operations.scope(m);session='session_qq_interop.jsonl'
            b.store.save_conversation(scope,[],session)
            b.action_proposals=QQActionProposals(b.store,clock=s.clock)
            b.action_proposals.ask(scope,session,'search_on',{},'要开启搜索吗？','fixture-revision')
            b.action_proposals.delivered(scope,session,'要开启搜索吗？')
            b.bind_responder(lambda *_:'不应调用普通模型')
            b.submit(event(m.text,99001,20000));await until(lambda:bool(b.client.sent));await until(lambda:not b.task_registry.list_active())
            with s.store.connect() as db:
                assert db.execute('select state from qq_action_proposals').fetchone()[0]=='superseded'
                assert db.execute('select state from qq_event_operation_sessions').fetchone()[0]=='waiting'
    asyncio.run(run())


@pytest.mark.parametrize('change',['membership','content'])
def test_semantic_plan_rechecks_after_model_wait_before_showing_preview(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice,count=1,dual=True)
            m=msg('请把晨光资料那条事件卡删掉',group=90000)
            async def late(*args,**kwargs):
                if change=='membership':s.unified.mark_member('90000','20000','revoked')
                else:
                    with s.store.connect() as db:
                        row=db.execute('select data from qq_episode_cards where id=?',(cards[0]['id'],)).fetchone()
                        c=json.loads(row[0]);c['facts'][0]['quote']='并发更新后的正文'
                        db.execute('update qq_episode_cards set data=?,revision=revision+1 where id=?',(json.dumps(c),cards[0]['id']))
                return {'action':'delete','scope':'personal','quantifier':'one','target_ids':[cards[0]['id']],
                    'intent_quote':m.text,'mode':'request','clarify':False}
            s.decide=late
            answer=await s.operations.handle(m)
            assert '没有修改' in answer and not getattr(m,'event_operation_proposal',None)
    asyncio.run(run())


def test_proposal_delivery_checks_current_group_membership(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=1,dual=True)
            m=msg('本群所有事件我都不跟进',group=90000)
            assert '待确认' in str(await s.operations.handle(m)) and s.operations.can_deliver(m)
            s.unified.mark_member('90000','20000','revoked')
            assert not s.operations.can_deliver(m)
    asyncio.run(run())


def test_all_completion_uses_semantic_plan_instead_of_single_target_fallback(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=2)
            m=msg('把所有个人事件标记为完成')
            choice[0]={'action':'complete','scope':'personal','quantifier':'all','target_ids':[],
                'intent_quote':m.text,'mode':'request','clarify':False}
            a=await s.operations.handle(m);assert '共2条' in a and '待确认' in a
            await deliver(b,s,m,a)
            assert '共2条' in str(await s.operations.handle(msg('对的')))
            assert all(c['lifecycle']=='completed' for c in s.people._canonical('20000'))
    asyncio.run(run())
