import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest
from saya_app.services.qq_event_priority import parse_date,extract_timings,automatic_importance,timing_view
from test_qq_person_events import lab


def test_explicit_dates_relative_dates_date_precision_offsets_and_dst():
    at=datetime(2026,11,4,15,tzinfo=timezone.utc).timestamp()
    assert parse_date('明天18:00',at,'Asia/Shanghai')['local']=='2026-11-05T18:00:00+08:00'
    assert parse_date('2026年11月6日',at,'Asia/Shanghai')['precision']=='day'
    assert parse_date('2026-11-06 18:00+09:00',at,'Asia/Shanghai')['local'].endswith('+09:00')
    for value in ['周末09:20','2026-02-30','2026-11-06 25:30','2026-11-01 01:30','2026-03-08 02:30']:
        with pytest.raises(ValueError):parse_date(value,at,'America/New_York')
    assert parse_date('2026-11-01 01:30-04:00',at,'America/New_York')['status']=='known'


def test_ambiguous_or_quoted_dates_never_become_deadlines():
    at=datetime(2026,11,4,tzinfo=timezone.utc).timestamp()
    assert extract_timings('他说“截止明天18:00”。',at,'Asia/Shanghai')=={}
    for text in ['截止周末09:20','截止2026-11-06 25:30','截止明天18:00或后天18:00','截止今天18:00；截止明天19:00']:
        assert extract_timings(text,at,'Asia/Shanghai')['deadline']['status']=='unknown'
    assert extract_timings('截止2026-11-06之前',at,'Asia/Shanghai')['deadline']['status']=='unknown'


def test_direct_value_evidence_only_manual_lock_and_urgency_are_separate():
    args=dict(source='synthetic',visibility='private',at=100)
    card={};automatic_importance(card,'这是一件重要安排。',**args);assert card['importance']=='high'
    automatic_importance(card,'有一些新的进展。',**args);assert card['importance']=='high'
    automatic_importance(card,'这是低优先级的安排。',**args);assert card['importance']=='low'
    locked={'importance':'normal','importance_locked':True};before=deepcopy(locked)
    automatic_importance(locked,'非常重要。',**args);assert locked==before
    for text in ['不急，随时可以处理。','他形容为“非常重要”。','不是关键任务。']:
        card={};automatic_importance(card,text,**args);assert card['importance']=='normal'
    card={};automatic_importance(card,'不急，但非常重要。',**args);assert card['importance']=='high'
    for text in ['如果非常重要应该怎样。','你的回复说这很重要。',"台词是'非常重要'。"]:
        card={};automatic_importance(card,text,**args);assert card.get('importance','normal')=='normal'


def test_new_and_updated_events_get_structured_deadline_and_bounded_auto_grade(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进重要安排：松湖资料截止明天18:00。');await s.observe(first,[])
            card=s.cards(first)[0];mid=card['id']
            assert card['importance']=='high' and card['timing']['deadline']['status']=='known'
            original=deepcopy(s.people._canonical(first.user_id)[0])
            for _ in range(3):s.context(msg('松湖资料现在什么状态？'))
            assert s.people._canonical(first.user_id)[0]==original
            choice[0]={'action':'update','event_id':mid,'confidence':.99}
            await s.observe(msg('松湖资料这次只供练习，低优先级。'),[])
            assert s.cards(first)[0]['importance']=='low'
            assert len(s.cards(first)[0]['importance_audit'])==2
    asyncio.run(run())


def test_deadline_control_preserves_TTL_and_count_invalidates_old_snapshot(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖资料：截止2026-11-08 18:00。');await s.observe(first,[])
            old=s.cards(first)[0];snapshot=s.context(msg('松湖资料何时截止？'))
            response=s.command(msg('/events deadline '+old['id']+' 2026-11-09 19:00'))
            assert '已更新' in str(response)
            card=s.people._canonical(first.user_id)[0]
            assert card['expires']==old['expires'] and card['count']==old['count']
            assert not s.valid(first,snapshot)
            context=s.context(msg('松湖资料何时截止？'))
            assert '2026-11-08' not in json.dumps(context,ensure_ascii=False)
            assert '2026-11-09' in json.dumps(context,ensure_ascii=False)
            assert '已更新' in str(s.command(msg('/events deadline '+old['id']+' clear')))
            assert s.cards(first)[0]['timing']['deadline']['status']=='removed'
    asyncio.run(run())


def test_deadline_does_not_resume_forgotten_event_or_complete_overdue_plan(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖资料：截止2026-10-01 18:00。');await s.observe(first,[])
            card=s.cards(first)[0];mid=card['id']
            assert timing_view(card,'private',now[0])['deadline']['status']=='overdue'
            assert card['lifecycle']=='open'
            s.people.controls.handle(msg('忘掉这件事'))
            s.command(msg('/events deadline '+mid+' 2026-11-06 18:00'))
            assert not s.context(msg('之前松湖资料何时截止？'))
    asyncio.run(run())


def test_private_deadline_edit_and_expired_timing_never_reveal_old_public_date(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖资料：截止2026-11-08 18:00。',group=90000);await s.observe(first,[])
            mid=s.cards(first)[0]['id']
            s.command(msg('/events deadline '+mid+' 2026-11-09 19:00'))
            assert not s.context(msg('松湖资料何时截止？',group=90000))
            assert s.context(msg('松湖资料何时截止？'))
            with s.store.connect() as db:
                row=db.execute('select data from qq_episode_cards where id=?',(mid,)).fetchone();card=json.loads(row[0])
                card['timing']['deadline']['source_expires']=now[0]+1
                db.execute('update qq_episode_cards set data=? where id=?',(json.dumps(card),mid))
            now[0]+=2
            assert not s.context(msg('松湖资料何时截止？'))
    asyncio.run(run())


def test_natural_deadline_target_is_not_selected_using_date_digits(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            await s.observe(msg('帮我跟进松湖资料。'),[])
            await s.observe(msg('帮我跟进云城资料，截止2026-11-08 18:00。'),[])
            result=s.people.controls.handle(msg('松湖资料截止时间设为2026-11-09 19:00'))
            assert '已更新' in str(result)
            lake=next(c for c in s.people._canonical('20000') if '松湖' in c['facts'][0]['quote'])
            city=next(c for c in s.people._canonical('20000') if '云城' in c['facts'][0]['quote'])
            assert '2026-11-09' in lake['timing']['deadline']['local'] and '2026-11-08' in city['timing']['deadline']['local']
    asyncio.run(run())


def test_date_only_display_audit_bound_and_manual_auto_unlock_on_next_evidence(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖资料，截止2026-11-06。');await s.observe(first,[])
            mid=s.cards(first)[0]['id'];view=timing_view(s.cards(first)[0],'private',now[0])
            assert view['deadline']['local']=='2026-11-06'
            s.command(msg('/events importance '+mid+' low'))
            choice[0]={'action':'update','event_id':mid,'confidence':1}
            await s.observe(msg('松湖资料是关键任务，优先处理。'),[])
            assert s.cards(first)[0]['importance']=='low'
            s.command(msg('/events importance '+mid+' auto'))
            assert s.cards(first)[0]['importance']=='low'
            for i in range(15):
                await s.observe(msg('松湖资料更新'+str(i)+('非常重要。' if i%2==0 else '低优先级。')),[])
            card=s.cards(first)[0]
            assert len(card['importance_audit'])==12 and not card['importance_locked']
    asyncio.run(run())


def test_due_boundary_invalidates_pending_answer_without_writing_or_extending(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            from datetime import timedelta
            due=datetime.fromtimestamp(now[0]+150,timezone.utc).isoformat(timespec='seconds')
            first=msg('帮我跟进松湖资料，截止'+due+'。');await s.observe(first,[])
            query=msg('松湖资料何时截止？');snapshot=s.context(query);assert snapshot
            original=deepcopy(s.people._canonical(first.user_id))
            now[0]+=200
            assert not s.valid(query,snapshot)
            assert s.people._canonical(first.user_id)==original
            assert s.cards(query)[0]['lifecycle']=='open'
    asyncio.run(run())


def test_future_priority_version_and_rollback_reader_are_rejected(tmp_path):
    async def run():
        import sqlite3
        from saya_app.services.qq_rollback import check_memory_rollback
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);mid=s.cards(first)[0]['id']
            target=tmp_path/'project/knowledge_base/qq';target.mkdir(parents=True)
            with s.store.connect() as src,sqlite3.connect(target/'bridge.sqlite3') as dst:src.backup(dst)
            assert 'qq_event_priority_v1' in check_memory_rollback(tmp_path/'project',{'memory_compatibility':[]})['missing']
            with s.store.connect() as db:
                row=db.execute('select data from qq_episode_cards where id=?',(mid,)).fetchone();data=json.loads(row[0])
                data['importance_rule_version']=9;db.execute('update qq_episode_cards set data=? where id=?',(json.dumps(data),mid))
            with pytest.raises(ValueError):s.cards(first)
    asyncio.run(run())


def test_invalid_date_other_actor_and_stale_delete_confirmation_cannot_mutate(tmp_path):
    async def run():
        import re
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进松湖资料。');await s.observe(first,[]);mid=s.cards(first)[0]['id']
            before=deepcopy(s.people._canonical(first.user_id))
            assert '没有修改' in str(s.command(msg('/events deadline '+mid+' 2026-02-30')))
            assert '没有' in str(s.command(msg('/events deadline '+mid+' 2026-11-06 18:00',user=30000)))
            assert s.people._canonical(first.user_id)==before
            preview=str(s.command(msg('/events delete '+mid)));token=re.search('confirm ([a-f0-9]+)',preview)[1]
            s.command(msg('/events deadline '+mid+' 2026-11-06 18:00'))
            assert '未删除' in str(s.command(msg('/events confirm '+token)))
    asyncio.run(run())


def test_restart_and_authenticated_web_share_timing_without_old_grade_migration(tmp_path,monkeypatch):
    async def run():
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from saya_app.routers import qq as routes
        from saya_app.services.qq_episode_context import QQEpisodeContext
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进关键任务：松湖资料，截止明天18:00。');await s.observe(first,[])
            before=deepcopy(s.people._canonical(first.user_id))
            fresh=QQEpisodeContext(b,clock=lambda:now[0]);b.episodes=fresh
            assert fresh.people._canonical(first.user_id)==before
            monkeypatch.setattr(routes,'qq_bridge',b);app=FastAPI();app.include_router(routes.router)
            with TestClient(app) as client:
                assert client.get('/api/qq/person-events').status_code==401
                page=client.get('/api/qq/person-events',headers={'Authorization':'Bearer admin'}).json()
            entry=page['items'][0]
            assert entry['importance']=='high' and entry['importance_rule_version']==1
            assert entry['timing']==timing_view(fresh.cards(first)[0],'private',now[0])
    asyncio.run(run())
