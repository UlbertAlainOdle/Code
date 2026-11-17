import asyncio
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import httpx
import pytest

from test_qq_unified_events import unified,server
from test_qq_person_events import lab
from test_qq_delivery import event
from test_qq_task_scheduler import until
from saya_app.services.qq_bridge import QQMessage,QQSettings
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_rollback import check_memory_rollback
from saya_app.services import qq_delivery
from saya_app.services.api_failures import project_legacy_errors
from saya_app.routers import qq as routes


def test_new_friend_threshold_and_minimal_private_admission(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            from saya_app.services.qq_contacts import QQContactReview
            b.contact_review=QQContactReview(b)
            b.relationships.configure('20000','40000','global',0,{'level':'familiar'})
            req={'post_type':'request','request_type':'friend','self_id':10000,'user_id':40000,'time':__import__('time').time(),'flag':'fixture'}
            assert b.contact_review.submit(req)[0]=='pending'
            rid=b.contact_review.state()['requests'][0]['id']
            calls=[]
            async def call(name,payload):calls.append(name);return {}
            with patch.object(b.client,'call',call,create=True):
                with pytest.raises(ValueError,match='below_threshold'):await b.contact_review.decide('20000',rid,True,confirmed=True)
                assert not calls
                b.relationships.configure('20000','40000','global',1,{'level':'important'})
                await b.contact_review.decide('20000',rid,True,confirmed=True)
            assert s.unified.admitted('40000')
            captured=[]
            async def responder(message,history,session):captured.append(message.shared);return 'fixture answer'
            b.bind_responder(responder)
            assert b.submit_signed(json.dumps(event('你好',999,40000)).encode(),'Bearer inbound')[0]=='queued'
            await asyncio.wait_for(b.queue.join(),3);await until(lambda:not b._task_workers)
            assert captured==[False] and '40000' not in b.settings.owner_ids|b.settings.allowed_users
            assert not b.relationships.policy()['values']['enabled']
    asyncio.run(run())


def test_group_capacity_finished_slot_and_expiry_pruning(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;ids=[]
            for i in range(20):ids.append(u.commit(msg('本群活动项目'+str(i)+'，需要讨论安排。',group=90000),'90000',{'title':'项目'+str(i)}))
            assert sum(c['state']=='active' for c in u.cards(msg('/events group',group=90000)))==5
            with pytest.raises(ValueError,match='capacity'):u.commit(msg('本群活动项目六，需要讨论安排。',group=90000),'90000',{'title':'项目六'})
            assert len(u.cards(msg('/events group',group=90000)))==20
            now[0]+=8*86400;u.prune()
            with s.store.connect() as db:assert db.execute('select count(*) from qq_shared_events').fetchone()[0]==0
    asyncio.run(run())


def test_current_decision_never_falls_back_to_expired_previous_value(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，我建议周末。',group=90000)
            mid=u.commit(a,'90000',{'title':'星桥讨论'})
            u.command(replace(a,text='/events group coordinator '+mid+' 20000'))
            u.commit(msg('本群活动星桥讨论，最终确定09:00。',group=90000),'90000',{'event_id':mid,'kind':'decision'},2)
            u.commit(msg('本群活动星桥讨论，最终确定10:00。',group=90000),'90000',{'event_id':mid,'kind':'decision'},3)
            with s.store.connect() as db:
                row=db.execute('select data from qq_shared_events where id=?',(mid,)).fetchone();c=json.loads(row[0]);c['contributions'][-1]['expires']=now[0]-1
                db.execute('update qq_shared_events set data=? where id=?',(json.dumps(c),mid))
            summary=u.summary(u.cards(a)[0]);assert '已确认安排：尚未定案' in summary
            assert '09:00' in summary # history remains attributed, not a current decision
    asyncio.run(run())


def test_model_cannot_turn_coordinator_suggestion_into_decision(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，我建议周末。',group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论'})
            u.command(replace(a,text='/events group coordinator '+mid+' 20000'))
            u.commit(msg('本群活动星桥讨论，我建议最终确定10:00，可以吗？',group=90000),'90000',{'event_id':mid,'kind':'decision'},2)
            assert u.cards(a)[0]['contributions'][-1]['kind']=='proposal'
    asyncio.run(run())


def test_group_membership_rejoin_does_not_revive_old_snapshot(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，周末一起讨论。',group=90000);await u.prepare(a)
            u.commit(a,'90000',{'title':'星桥讨论'});snapshot=u.context(a);assert u.valid(a,snapshot)
            u.notice({'self_id':10000,'notice_type':'group_decrease','group_id':90000,'user_id':20000});assert not u.valid(a,snapshot)
            u.mark_member('90000','20000');assert not u.valid(a,snapshot)
            assert u.valid(a,u.context(a))
    asyncio.run(run())


def test_personal_grade_cycle_invalidates_old_snapshot_without_deleting_card(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            b.relationships.configure('20000','30000','global',0,{'level':'important'})
            a=msg('帮我跟进星桥资料整理计划。',user=30000);choice[0]['title']='星桥资料';await s.observe(a,[])
            query=msg('之前星桥资料如何？',user=30000,group=90000);snapshot=s.context(query);assert s.valid(query,snapshot)
            b.relationships.configure('20000','30000','global',1,{'level':'familiar'});assert not s.valid(query,snapshot)
            b.relationships.configure('20000','30000','global',2,{'level':'important'});assert not s.valid(query,snapshot)
            assert len(s.cards(query))==1
    asyncio.run(run())


def test_disabled_collection_keeps_controls_and_does_not_collect_shared_events(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，周末一起讨论。',group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论'})
            s.configure('20000',s.policy()['revision'],{'enabled':False})
            assert not u.context(a)
            with pytest.raises(PermissionError):u.commit(msg('本群活动星桥讨论，更新安排。',group=90000),'90000',{'event_id':mid},1)
            assert '停止' in str(u.command(replace(a,text='/events group stop '+mid)))
    asyncio.run(run())


def test_group_evidence_limit_never_discards_first_contributor(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;policy=u.policy();u.configure('20000',policy['revision'],{**policy['values'],'max_contributions':8})
            a=msg('本群活动星桥讨论，首条未决分歧。',group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论','kind':'objection'})
            for i in range(7):u.commit(msg('本群活动星桥讨论，新进展'+str(i)+'。',user=30000,group=90000),'90000',{'event_id':mid,'kind':'progress'},i+1)
            with pytest.raises(ValueError,match='no_silent_drop'):u.commit(msg('本群活动星桥讨论，又一条新进展。',group=90000),'90000',{'event_id':mid},8)
            assert '首条未决分歧' in u.summary(u.cards(a)[0])
    asyncio.run(run())


def test_host_delivery_error_projection_keeps_audit_and_user_quotes(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            a=msg('问题');scope=a.scope+':shared';error='API stream interrupted: ConnectError: All connection attempts failed'
            qq_delivery.create(s.store,a,scope,error,1200)
            history=['Doctor: '+error,'Saya: '+error,'Saya: 解释 '+error]
            assert project_legacy_errors(s.store,scope,history)==[history[0],history[2]]
            with s.store.connect() as db:assert db.execute('select canonical from qq_deliveries where scope=?',(scope,)).fetchone()[0]==error
    asyncio.run(run())


def test_policy_API_auth_preview_and_group_summary(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            app=FastAPI();app.include_router(routes.router)
            with patch.object(routes,'qq_bridge',b):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as c:
                    assert (await c.get('/api/qq/unified-events')).status_code==401
                    c.headers['Authorization']='Bearer admin';v=(await c.get('/api/qq/unified-events')).json()
                    body={'owner_id':'20000','revision':v['policy']['revision'],'values':{'enabled':True},'confirmed':True,'expected_version':v['preview']['version']}
                    assert (await c.put('/api/qq/unified-events',json={**body,'expected_version':'stale'})).status_code==409
                    assert (await c.put('/api/qq/unified-events',json=body)).status_code==200
                    assert (await c.put('/api/qq/unified-events',json=body)).status_code==409
                    state=(await c.get('/api/qq/unified-events')).json();assert state['policy']['values']['minimum_level']=='important'
    asyncio.run(run())


def test_real_bridge_records_generation_failure_not_successful_error_answer(server,tmp_path):
    async def response(req,channel_turn=None):
        async def chunks():
            yield 'event: stream_error\ndata: {"message":"fixture"}\n\n'
            yield 'event: answer_complete\ndata: {"text":"API stream interrupted: ConnectError: All connection attempts failed"}\n\n'
        return StreamingResponse(chunks())
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            responder=QQChatResponder(b.settings,server.ChatWithMotion,response,lambda:'api',lambda:{'configured':True,'api_key':'FIXTURE','api_url':'https://fixture.invalid','model':'fixture'})
            b.bind_responder(responder)
            assert b.submit_signed(json.dumps(event('普通问题',777)).encode(),'Bearer inbound')[0]=='queued'
            await asyncio.wait_for(b.queue.join(),4);await until(lambda:not b._task_workers)
            tasks=b.task_registry.list_scope('10000:private:20000:shared');assert tasks[0]['state']=='failed' and tasks[0]['error']=='generation_failed'
            assert b.client.sent and not any('API stream interrupted' in x[1] for x in b.client.sent)
            assert not b.store.conversation('10000:private:20000:shared')[0]
            assert not s.people._canonical('20000')
    asyncio.run(run())


def test_group_delete_requires_same_actor_preview_version_and_keeps_personal_card(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，周末安排。',group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论'})
            u.command(replace(a,text='/events group coordinator '+mid+' 20000'))
            receipt=u.command(replace(a,text='/events group delete '+mid));token=receipt.payload.split()[-1]
            other=msg('/events group confirm '+token,user=30000,group=90000)
            assert '没有' in str(u.command(other)) or '变化' in str(u.command(other))
            u.commit(msg('本群活动星桥讨论，又有进展。',group=90000),'90000',{'event_id':mid},2)
            assert '没有删除' in str(u.command(replace(a,text='/events group confirm '+token)))
            fresh=u.command(replace(a,text='/events group delete '+mid)).payload.split()[-1]
            assert '已按确认删除' in str(u.command(replace(a,text='/events group confirm '+fresh)))
            assert not u.cards(a)
    asyncio.run(run())


def test_late_classifier_cannot_recreate_deleted_or_stopped_group_event(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，周末安排。',group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论'})
            update=msg('本群活动星桥讨论，补充路线。',group=90000)
            async def decide(*args,**kwargs):
                u.command(replace(update,text='/events group stop '+mid))
                return {'action':'create','title':'星桥讨论','confidence':.95}
            s.decide=decide
            assert '没有提交' in str(await u.capture(update))
            assert len(u.cards(a,include_stopped=True))==1 and not u.cards(a)
    asyncio.run(run())


def test_important_member_natural_followup_routes_to_existing_group_pool(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，建议09:00。',user=30000,group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论'})
            follow=msg('我只能10:00之后到达。',group=90000)
            assert u.is_group_request(follow)
            choice[0]={'action':'update','event_id':mid,'kind':'commitment','confidence':.95}
            await u.capture(follow);assert follow.shared_event_receipt['status']=='committed'
            assert len(u.cards(a)[0]['contributions'])==2 and not s.people._canonical('20000')
            assert await u.observe(follow) # no duplicate classification/write
            assert len(u.cards(a)[0]['contributions'])==2
    asyncio.run(run())


def test_below_threshold_retained_owner_can_manage_but_not_create(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            b.relationships.configure('20000','30000','global',0,{'level':'important'})
            p=msg('帮我跟进星桥资料整理计划。',user=30000);choice[0]['title']='星桥资料';await s.observe(p,[]);mid=s.cards(p)[0]['id']
            b.relationships.configure('20000','30000','global',1,{'level':'familiar'})
            assert not s.context(msg('之前星桥资料如何？',user=30000))
            assert mid in str(s.command(replace(p,text='/events self')))
            assert '已停止' in str(s.command(replace(p,text='/events stop '+mid)))
            await s.observe(msg('帮我跟进另一个全新的计划。',user=30000),[])
            assert len(s.people._canonical('30000'))==1
    asyncio.run(run())


def test_group_context_responder_snapshot_and_budget_no_recursion(server,tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            a=msg('本群活动星桥讨论，建议09:00。',group=90000);mid=s.unified.commit(a,'90000',{'title':'星桥讨论'})
            captured=[]
            async def respond(req,channel_turn=None):
                captured.append(channel_turn)
                channel_turn['context_selector'](channel_turn['history'],channel_turn['history_times'])
                async def chunks():yield 'event: answer_complete\ndata: {"text":"目前只是提议，尚未定案。"}\n\n'
                return StreamingResponse(chunks())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,respond,lambda:'api',lambda:{'configured':True,'api_key':'FIXTURE','api_url':'https://fixture.invalid','model':'fixture'})
            b.bind_responder(responder)
            query=msg('本群活动星桥讨论目前如何？',group=90000)
            answer=await responder(query,['Doctor: 无关旧话','Saya: 无关旧回复'],'session_qq_fixture.jsonl')
            assert '尚未定案' in str(answer)
            assert not captured[0]['memory_write_enabled'] and 'text' not in query.episode_snapshot['shared_group']
            rid=qq_delivery.create(s.store,query,query.scope+':isolated',str(answer),1200)
            s.bind_result(query,rid);assert s.valid_delivery(query,rid)
            s.unified.command(replace(query,text='/events group stop '+mid));assert not s.valid_delivery(query,rid)
    asyncio.run(run())


def test_real_generator_failed_QQ_stream_does_not_persist_AI_error(server,tmp_path):
    from core_services import KnowledgeBase,MemoryManager
    manager=MemoryManager(str(tmp_path/'chats'));kb=KnowledgeBase(str(tmp_path/'kb'))
    async def noop(*args,**kwargs):pass
    def model(request):raise httpx.ConnectError('All connection attempts failed',request=request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server,'_get_api_httpx_client',lambda:client),patch.object(server,'_api_retry_backoff_seconds',lambda *args:0):
                req=server.ChatWithMotion(user_input='fixture question',model='api',session_id='session_qq_failure_fixture.jsonl',
                    temporary_chat=False,api_key='FIXTURE',api_url='https://fixture.invalid',recall_mode='off',memory_write_enabled=False)
                response=await server._chat_stream_single(req,channel_turn={'shared':True,'is_owner':True,'history':[]})
                raw=''.join([str(x) async for x in response.body_iterator]);assert 'stream_error' in raw and 'answer_complete' not in raw
        rows=[json.loads(x) for p in (tmp_path/'chats').glob('*.jsonl') for x in p.read_text('utf-8').splitlines() if x]
        assert not any(x.get('role') in {'ai','assistant'} for x in rows)
    from saya_app.services.conversation import conversation_service
    with patch.object(server,'memory_manager',manager),patch.object(server,'kb_system',kb),patch.object(server,'build_prompt',lambda *a,**k:'fixture'),\
        patch.object(server,'_record_api_turn_usage',noop),patch.object(conversation_service,'save_credentials',lambda **kwargs:None):
        asyncio.run(run())


def test_real_bridge_group_capture_commits_once_before_answer(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            choices=[{'action':'create','title':'星桥讨论','kind':'proposal','confidence':.95}]
            async def decide(*args,**kwargs):return choices[0]
            s.decide=decide;captured=[]
            async def respond(message,history,session):
                captured.append(getattr(message,'shared_event_receipt',None));return '只是提议，仍待讨论。'
            b.bind_responder(respond)
            assert b.submit_signed(json.dumps(event('本群活动星桥讨论，我建议09:00集合。',888,30000,90000)).encode(),'Bearer inbound')[0]=='queued'
            await asyncio.wait_for(b.queue.join(),4);await until(lambda:not b._task_workers)
            assert captured and captured[0]['status']=='committed' and captured[0]['kind']=='proposal'
            with s.store.connect() as db:
                rows=db.execute('select data from qq_shared_events').fetchall();assert len(rows)==1
                assert len(json.loads(rows[0][0])['contributions'])==1
            assert not s.people._canonical('30000')
    asyncio.run(run())


def test_unified_data_rejects_old_rollback_reader(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            import sqlite3
            root=tmp_path/'rollback-root';folder=root/'knowledge_base/qq';folder.mkdir(parents=True)
            with s.store.connect() as source,sqlite3.connect(folder/'bridge.sqlite3') as copy:source.backup(copy)
            report=check_memory_rollback(root,{'memory_compatibility':[]})
            assert 'qq_unified_events_v1' in report['missing']
    asyncio.run(run())


def test_short_personal_withdrawal_keeps_group_event_and_other_positions(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，周末一起出发。',group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论'})
            withdrawal=msg('我不去了',user=30000,group=90000)
            await u.capture(withdrawal)
            card=u.cards(a)[0]
            assert card['state']=='active' and len(card['contributions'])==2
            assert card['contributions'][-1]['actor']=='30000' and card['contributions'][-1]['kind']=='withdrawal'
            assert withdrawal.shared_event_receipt['status']=='committed'
    asyncio.run(run())


def test_public_group_history_can_reauthorize_another_member_but_not_another_group(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，周末一起出发。',group=90000);u.commit(a,'90000',{'title':'星桥讨论'})
            snap=u.source_snapshot(u.context(a));other=msg('本群活动星桥讨论怎样？',user=30000,group=90000)
            assert not u.valid(other,snap)
            rebound=u.for_requester(other,snap);assert rebound and u.valid(other,rebound)
            assert snap['actor']=='20000' and rebound['actor']=='30000'
            assert u.for_requester(replace(other,group_id='90001'),snap) is None
            u.mark_member('90000','20000')
            assert not u.member(replace(a,group_id='90001'),'90000')
    asyncio.run(run())


def test_shared_dates_only_from_current_coordinator_decision_and_expiry_invalidates(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，建议截止2026-11-06 18:00+08:00。',group=90000)
            mid=u.commit(a,'90000',{'title':'星桥讨论','kind':'proposal'});assert not u.timing(u.cards(a)[0])
            u.command(replace(a,text='/events group coordinator '+mid+' 20000'))
            decision=msg('本群活动星桥讨论，最终确定截止2026-11-07 18:00+08:00。',group=90000)
            u.commit(decision,'90000',{'event_id':mid,'kind':'decision'},2)
            card=u.cards(a)[0];assert u.timing(card)['deadline']['local']=='2026-11-07T18:00:00+08:00'
            snapshot=u.context(a);assert u.valid(a,snapshot)
            now[0]+=3700
            assert not u.valid(a,snapshot) and not u.cards(a)
    asyncio.run(run())


def test_admin_summary_cannot_treat_expired_decision_as_current(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥讨论，周末安排。',group=90000);mid=u.commit(a,'90000',{'title':'星桥讨论'})
            u.command(replace(a,text='/events group coordinator '+mid+' 20000'))
            u.commit(msg('本群活动星桥讨论，最终确定09:00。',group=90000),'90000',{'event_id':mid,'kind':'decision'},2)
            with s.store.connect() as db:
                card=json.loads(db.execute('select data from qq_shared_events where id=?',(mid,)).fetchone()[0])
            card['contributions'][-1]['expires']=now[0]-1
            summary=u.summary(card)
            assert '已确认安排：尚未定案' in summary and '09:00' not in summary
    asyncio.run(run())


def test_committed_group_progress_and_failed_answer_have_separate_receipt(server,tmp_path):
    async def response(req,channel_turn=None):
        async def chunks():yield 'event: stream_error\ndata: {"message":"fixture"}\n\n'
        return StreamingResponse(chunks())
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            choice[0]={'action':'create','title':'星桥讨论','kind':'proposal','confidence':.95}
            responder=QQChatResponder(b.settings,server.ChatWithMotion,response,lambda:'api',lambda:{'configured':True,'api_key':'FIXTURE','api_url':'https://fixture.invalid','model':'fixture'})
            b.bind_responder(responder)
            assert b.submit_signed(json.dumps(event('本群活动星桥讨论，我建议09:00。',889,30000,90000)).encode(),'Bearer inbound')[0]=='queued'
            await asyncio.wait_for(b.queue.join(),4);await until(lambda:not b._task_workers)
            assert any('补充已提交' in item[1] for item in b.client.sent)
            task=b.task_registry.list_scope('10000:group:90000:user:30000:isolated')[0]
            assert task['state']=='failed' and any(x['kind']=='memory' and x['outcome']=='committed' for x in task['effects'].values())
            with s.store.connect() as db:assert db.execute('select count(*) from qq_shared_events').fetchone()[0]==1
    asyncio.run(run())
