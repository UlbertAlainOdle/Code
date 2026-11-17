import asyncio
from copy import deepcopy
from dataclasses import replace
import re

from test_qq_person_events import lab


def test_short_forget_commits_global_suppression_and_invalidates_old_sources(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进虚构的松湖公园计划。');await s.observe(first,[])
            card=s.cards(first)[0];snap=s.context(replace(first,text='松湖公园计划是什么？'))
            history=s.people.history_projection(first)
            response=s.people.controls.handle(msg('忘掉这件事'))
            assert '已停止' in str(response)
            assert not s.context(replace(first,text='之前那个松湖公园计划呢？'))
            assert card['id'] not in str(s.command(replace(first,text='/events self')))
            assert 'suppressed' in str(s.command(replace(first,text='/events stopped')))
            assert not s.valid(first,snap) and not s.people.valid_projection(first,history)
            assert s.people._canonical(first.user_id)[0]['count']==1
    asyncio.run(run())


def test_pause_requires_explicit_resume_and_is_visible_with_same_source_ttl(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进虚构的松湖公园计划。');await s.observe(first,[])
            original=deepcopy(s.people._canonical(first.user_id)[0])
            assert '暂停' in str(s.people.controls.handle(msg('先别跟进这件事')))
            assert 'paused' in str(s.command(replace(first,text='/events stopped')))
            assert s.context(msg('之前那个完全无关的问题？')) is None
            response=s.people.controls.handle(msg('重新跟进松湖公园计划'))
            assert '恢复' in str(response)
            after=s.people._canonical(first.user_id)[0]
            assert after['expires']==original['expires'] and after['facts']==original['facts']
            assert s.context(msg('松湖公园计划是什么？'))
    asyncio.run(run())


def test_reported_hypothetical_negative_and_reschedule_are_not_forget_controls(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);before=deepcopy(s.people._canonical(first.user_id))
            for text in ['他说忘掉这件事','如果我说忘掉这件事会怎样？','不要忘记这件事',
                         '不是让你忘掉这件事','取消原来9点，改成10点','“忘掉这个事吧”是什么意思？',
                         '不要删除这件事','不要停止跟进这件事','我忘记带雨伞了','为什么忘掉这件事',
                         "'忘掉这件事'",'忘掉这件事了吗']:
                assert s.people.controls.handle(msg(text)) is None
            assert s.people._canonical(first.user_id)==before
    asyncio.run(run())


def test_named_unmatched_target_never_falls_back_to_unrelated_recent_event(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖公园计划。');await s.observe(first,[])
            before=deepcopy(s.people._canonical(first.user_id))
            result=s.people.controls.handle(msg('忘掉蓝叶杯这件事'))
            assert '明确' in str(result) and s.people._canonical(first.user_id)==before
    asyncio.run(run())


def test_completed_and_cancelled_are_terminal_without_changing_evidence(tmp_path):
    async def run():
        for action,lifecycle in [('complete','completed'),('cancel','cancelled')]:
            async with lab(tmp_path/action) as (b,s,now,msg,choice,prompts):
                first=msg('帮我跟进松湖公园计划。');await s.observe(first,[])
                original=deepcopy(s.people._canonical(first.user_id)[0]);mid=original['id']
                s.command(msg('/events '+action+' '+mid))
                assert mid not in str(s.command(msg('/events self')))
                assert lifecycle in str(s.command(msg('/events stopped')))
                snapshot=s.context(msg('松湖公园计划是什么？'))
                assert snapshot['cards'][0]['lifecycle']==lifecycle
                assert '没有恢复' in str(s.command(msg('/events resume '+mid)))
                current=s.people._canonical(first.user_id)[0]
                assert current['facts']==original['facts'] and current['expires']==original['expires']
    asyncio.run(run())


def test_private_terminal_state_is_not_revealed_in_public_or_revived_after_expiry(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            public=msg('帮我跟进松湖公园计划。',group=90000);await s.observe(public,[])
            mid=s.cards(public)[0]['id']
            s.command(msg('/events complete '+mid))
            assert not s.context(msg('松湖公园计划是什么？',group=90000))
            assert s.context(msg('松湖公园计划是什么？'))['cards'][0]['lifecycle']=='completed'
            # Control validity can end before retained private evidence; never restore the old open plan.
            with s.store.connect() as db:
                row=db.execute('select data from qq_episode_cards where id=?',(mid,)).fetchone()
                import json
                card=json.loads(row[0]);card['lifecycle_expires']=now[0]+1
                db.execute('update qq_episode_cards set data=? where id=?',(json.dumps(card),mid))
            now[0]+=2
            assert not s.context(msg('松湖公园计划是什么？'))
    asyncio.run(run())


def test_manual_importance_keep_and_reads_do_not_extend_ttl_or_increment_count(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖公园计划。');await s.observe(first,[])
            original=deepcopy(s.people._canonical(first.user_id)[0]);mid=original['id']
            for action in ['importance '+mid+' high','keep '+mid,'unkeep '+mid,'importance '+mid+' normal']:
                s.command(msg('/events '+action))
            after=s.people._canonical(first.user_id)[0]
            assert after['importance']=='normal' and after['importance_locked'] and not after['keep']
            assert after['facts']==original['facts'] and after['expires']==original['expires'] and after['count']==original['count']
            s.people.controls.handle(msg('这件很重要，但是先别提'))
            after=s.people._canonical(first.user_id)[0]
            assert after['tracking']=='paused' and after['importance']=='high'
            s.command(msg('/events stop '+mid))
            s.command(msg('/events importance '+mid+' high'))
            assert s.context(msg('松湖公园计划是什么？')) is None
    asyncio.run(run())


def test_web_effective_state_and_importance_match_command_and_cooling_clock(tmp_path,monkeypatch):
    async def run():
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from saya_app.routers import qq as routes
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);mid=s.cards(first)[0]['id']
            s.command(msg('/events importance '+mid+' high'));now[0]+=7201
            monkeypatch.setattr(routes,'qq_bridge',b);app=FastAPI();app.include_router(routes.router)
            with TestClient(app) as client:
                assert client.get('/api/qq/person-events').status_code==401
                item=client.get('/api/qq/person-events',headers={'Authorization':'Bearer admin'}).json()['items'][0]
            assert item['state']=='cooling' and item['importance']=='high' and item['importance_locked']
            assert 'cooling' in str(s.command(msg('/events self')))
    asyncio.run(run())


def test_actual_bridge_natural_forget_sends_committed_control_without_model(tmp_path):
    async def run():
        from test_qq_task_scheduler import submit
        from saya_app.services.qq_bridge import QQMessage
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);mid=s.cards(first)[0]['id']
            async def forbidden(*args,**kwargs):raise AssertionError('model must not handle event controls')
            b.bind_responder(forbidden)
            assert submit(b,'忘掉事件 '+mid,mid=712)[0]=='queued'
            await asyncio.wait_for(b.queue.join(),3)
            assert any('已停止' in part[1] for part in b.client.sent)
            with b.store.connect() as db:
                assert db.execute('select state from inbox where event_key like ?',('%:message:712',)).fetchone()[0]=='sent'
            assert not b.store.conversation(first.scope+':shared')[0]
    asyncio.run(run())


def test_control_CAS_cancellation_schema_and_reader_compatibility(tmp_path):
    async def run():
        import json,sqlite3
        from saya_app.services.qq_rollback import check_memory_rollback
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);old=s.cards(first)[0]
            s.command(msg('/events importance '+old['id']+' high'))
            assert '没有修改' in str(s.people.controls.change(msg('忘掉'),old,'stop'))
            target=tmp_path/'project/knowledge_base/qq';target.mkdir(parents=True)
            with s.store.connect() as src,sqlite3.connect(target/'bridge.sqlite3') as dst:src.backup(dst)
            assert 'qq_event_controls_v1' in check_memory_rollback(tmp_path/'project',{'memory_compatibility':[]})['missing']
            with s.store.connect() as db:db.execute('update qq_event_control_schema set version=9')
            from saya_app.services.qq_event_controls import QQEventControls
            try:QQEventControls(s.people)
            except ValueError:pass
            else:raise AssertionError('future schema must reject')
    asyncio.run(run())


def test_cancelled_control_cannot_commit_and_other_message_kinds_are_not_authority(tmp_path,monkeypatch):
    async def run():
        import pytest
        from saya_app.services import qq_event_controls as module
        from saya_app.services.qq_tasks import TaskCancelled
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);before=deepcopy(s.people._canonical(first.user_id))
            for kind in ['proactive','poke']:
                assert s.people.controls.handle(replace(msg('忘掉这件事'),kind=kind)) is None
            def cancelled():raise TaskCancelled()
            monkeypatch.setattr(module,'checkpoint_current',cancelled)
            with pytest.raises(TaskCancelled):s.people.controls.handle(msg('忘掉这件事'))
            assert s.people._canonical(first.user_id)==before
    asyncio.run(run())


def test_post_forget_real_responder_filters_raw_history_and_new_snapshot_is_valid(tmp_path):
    async def run():
        import json
        from types import SimpleNamespace
        from fastapi.responses import StreamingResponse
        from saya_app.services.qq_chat import QQChatResponder
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖公园计划。');await s.observe(first,[])
            s.people.controls.handle(msg('忘掉这件事'))
            async def generate(req,channel_turn):
                assert first.text not in str(channel_turn['history'])
                assert channel_turn['qq_episode']['control_revision']==s.people.controls.version(first.user_id)
                async def body():yield 'event: answer_complete\ndata: '+json.dumps({'text':'新话题'})+'\n\n'
                return StreamingResponse(body())
            responder=QQChatResponder(b.settings,lambda **kwargs:SimpleNamespace(**kwargs),generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            b.bind_responder(responder);query=msg('聊一个新话题，不用搜索。')
            result=await responder(query,['Doctor: '+first.text,'Saya: old event answer'],'private-event-stop')
            assert str(result)=='新话题' and s.valid(query,query.episode_snapshot)
    asyncio.run(run())


def test_management_metadata_capacity_rejects_without_partial_card_write(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);before=deepcopy(s.people._canonical(first.user_id))
            with s.store.connect() as db:
                db.executemany('insert into qq_event_control_versions values(?,?,?,?)',
                    [(s.people.bot,str(40000+i),1,now[0]) for i in range(4096)])
            response=s.people.controls.handle(msg('忘掉这件事'))
            assert '没有修改' in str(response) and s.people._canonical(first.user_id)==before
    asyncio.run(run())


def test_no_long_term_save_wording_is_per_turn_opt_out_not_event_forgetting(tmp_path):
    async def run():
        import json
        from types import SimpleNamespace
        from fastapi.responses import StreamingResponse
        from saya_app.services.qq_chat import QQChatResponder
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);before=deepcopy(s.people._canonical(first.user_id))
            async def generate(req,channel_turn):
                assert not req.memory_write_enabled and not channel_turn['memory_write_enabled']
                async def body():yield 'event: answer_complete\ndata: '+json.dumps({'text':'本轮不保存'})+'\n\n'
                return StreamingResponse(body())
            responder=QQChatResponder(b.settings,lambda **kwargs:SimpleNamespace(**kwargs),generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            b.bind_responder(responder)
            for text in ['不要保存成长期记忆','不要保存为长期记忆','别记录到长期记忆']:
                query=msg(text);assert s.people.controls.handle(query) is None
                assert str(await responder(query,[],'save-opt-out'))=='本轮不保存'
            assert s.people._canonical(first.user_id)==before
    asyncio.run(run())


def test_expired_latest_source_is_manageable_but_never_listed_as_active(tmp_path):
    async def run():
        import json
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);mid=s.cards(first)[0]['id']
            with s.store.connect() as db:
                row=db.execute('select data from qq_episode_cards where id=?',(mid,)).fetchone()
                card=json.loads(row[0]);card['latest_source']='expired-source'
                db.execute('update qq_episode_cards set data=? where id=?',(json.dumps(card),mid))
            assert mid not in str(s.command(msg('/events self')))
            assert 'source_unavailable' in str(s.command(msg('/events stopped')))
            assert '已停止' in str(s.people.controls.handle(msg('忘掉事件 '+mid)))
    asyncio.run(run())


def test_self_contained_new_followup_excludes_old_questions_and_archive_reload(tmp_path):
    async def run():
        import json
        from types import SimpleNamespace
        from fastapi.responses import StreamingResponse
        from saya_app.services.qq_chat import QQChatResponder
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            history=['Doctor: 旧的集合时间是多少？','Saya: OLD_UNBOUND_ANSWER']
            async def generate(req,channel_turn):
                assert channel_turn['fresh_working_event']
                assert channel_turn['history']==[] and channel_turn['history_times']==[]
                assert channel_turn['context_selector'](history,[{'role':'user','content':'OLD_ARCHIVE'}])==([],[])
                assert 'event_context' not in channel_turn and not channel_turn.get('context_link')
                assert not req.memory_write_enabled
                async def body():yield 'event: answer_complete\ndata: '+json.dumps({'text':'跟进这次新安排'})+'\n\n'
                return StreamingResponse(body())
            responder=QQChatResponder(b.settings,lambda **kwargs:SimpleNamespace(**kwargs),generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            b.bind_responder(responder)
            query=msg('帮我跟进本次虚构安排：明天整理一盒彩色卡片。')
            assert str(await responder(query,history,'fresh-event'))=='跟进这次新安排'
    asyncio.run(run())


def test_multiple_targets_and_other_account_cannot_change_an_event(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖公园计划。');await s.observe(first,[])
            await s.observe(msg('帮我跟进月城旅行计划。'),[])
            before=deepcopy(s.people._canonical(first.user_id))
            # Expire the short same-conversation focus window: don't guess a global focus.
            now[0]+=1801
            assert '明确' in str(s.people.controls.handle(msg('忘掉这件事')))
            identifier=before[0]['id']
            assert '没有' in str(s.people.controls.handle(msg('忘掉事件 '+identifier,user=30000)))
            assert s.people._canonical(first.user_id)==before
    asyncio.run(run())


def test_group_local_stop_preserves_private_use_and_global_stop_spans_scopes(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖公园公开计划。',group=90000);await s.observe(first,[])
            identifier=s.cards(first)[0]['id']
            assert '本群' in str(s.people.controls.handle(msg('别在这个群提松湖公园计划',group=90000)))
            assert s.context(msg('之前松湖公园计划是什么？',group=90000)) is None
            assert s.context(msg('之前松湖公园计划是什么？'))
            assert '各会话' in str(s.people.controls.handle(msg('忘掉事件 '+identifier)))
            assert s.context(msg('之前松湖公园计划是什么？')) is None
    asyncio.run(run())


def test_late_classifier_cannot_recreate_after_forget_or_delete(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);identifier=s.cards(first)[0]['id']
            entered,release=asyncio.Event(),asyncio.Event()
            async def late(*args,**kwargs):
                entered.set();await release.wait();return {'action':'create','confidence':1}
            s.decide=late
            pending=asyncio.create_task(s.observe(msg('我还有一条关于原计划的补充信息。'),[]))
            await entered.wait()
            s.people.controls.handle(msg('忘掉事件 '+identifier))
            preview=str(s.command(msg('/events delete '+identifier)))
            token=re.search(r'confirm ([a-f0-9]+)',preview)[1]
            assert '已删除' in str(s.command(msg('/events confirm '+token)))
            release.set();await pending
            assert not s.people._canonical(first.user_id)
    asyncio.run(run())


def test_replayed_old_evidence_and_management_input_do_not_make_candidates(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[])
            s.people.controls.handle(msg('忘掉这件事'))
            before=len(prompts)
            for text in [first.text,'这只是测试用的事件，忘掉这个事吧。','重新跟进这件事']:
                await s.observe(msg(text),[])
            assert len(prompts)==before
            assert len(s.people._canonical(first.user_id))==1
    asyncio.run(run())


def test_disabled_capture_still_allows_stop_and_restart_keeps_suppression(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[])
            policy=s.policy();s.configure(first.user_id,policy['revision'],policy['values']|{'enabled':False})
            assert '已停止' in str(s.people.controls.handle(msg('忘掉这件事')))
            from saya_app.services.qq_episode_context import QQEpisodeContext
            fresh=QQEpisodeContext(b,clock=lambda:now[0])
            assert fresh.people._canonical(first.user_id)[0]['tracking']=='suppressed'
    asyncio.run(run())
