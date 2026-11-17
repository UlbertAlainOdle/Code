import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi.responses import StreamingResponse

from test_qq_person_events import lab
from test_qq_chat_runtime import server
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_bridge import QQSettings,QQMessage
from saya_app.services.api_failures import diagnostic,project_legacy_errors
from saya_app.services.qq_rollback import check_memory_rollback


@asynccontextmanager
async def unified(tmp_path):
    async with lab(tmp_path) as state:
        b,s,now,msg,choice,prompts=state
        u=s.unified
        u.configure('20000',0,{'enabled':True},u.preview()['version'])
        yield state


def test_personal_same_id_dates_and_own_only_writes_across_group(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            private=msg('帮我跟进重要安排：星桥资料截止2026-11-06 18:00+08:00。')
            choice[0]['title']='星桥资料';await s.observe(private,[])
            before=s.cards(private)[0]
            group=msg('/events self',group=90000)
            current=s.cards(group)[0]
            assert current['id']==before['id'] and current['revision']==before['revision']
            assert '2026-11-06' in str(s.command(group))
            outsider=msg('/events show '+before['id'],user=30000,group=90000)
            assert not s.cards(outsider) and '星桥' not in str(s.command(outsider))
            changed=s.people.controls.handle(msg('星桥资料截止时间改为2026-11-07 18:00+08:00',group=90000))
            assert '已更新' in str(changed)
            assert '2026-11-07' in str(s.command(replace(private,text='/events self')))
            assert len(s.people._canonical('20000'))==1
    asyncio.run(run())


def test_personal_other_user_cannot_write_even_if_important(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            p=msg('帮我跟进星桥资料整理计划。');choice[0]['title']='星桥资料';await s.observe(p,[])
            card=s.cards(p)[0]
            b.relationships.configure('20000','30000','global',0,{'level':'important'})
            receipt=s.people.controls.handle(msg('忘掉事件 '+card['id'],user=30000,group=90000))
            assert '星桥' not in str(receipt)
            assert s.cards(p)[0]['revision']==card['revision']
    asyncio.run(run())


def test_group_pool_multiple_authors_preserves_conflict_and_coordinator(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified
            a=msg('本群活动星桥出游，我建议09:00集合。',group=90000)
            mid=u.commit(a,'90000',{'title':'星桥出游','kind':'proposal'})
            other=msg('本群活动星桥出游，我只能10:00到。',user=30000,group=90000)
            u.commit(other,'90000',{'event_id':mid,'kind':'commitment'},1)
            card=u.cards(a)[0]
            assert '09:00' in u.summary(card) and '10:00' in u.summary(card) and '尚未定案' in u.summary(card)
            with pytest.raises(PermissionError):
                u.commit(msg('我要更正别人的话',user=30000,group=90000),'90000',
                    {'event_id':mid,'kind':'progress','supersedes':[card['contributions'][0]['id']]},2)
            assert len(u.cards(a)[0]['contributions'])==2
            no=msg('本群活动星桥出游，最终定为10:30。',user=30000,group=90000)
            u.commit(no,'90000',{'event_id':mid,'kind':'decision'},2)
            assert '尚未定案' in u.summary(u.cards(a)[0])
            receipt=u.command(replace(a,text='/events group coordinator '+mid+' 30000'))
            assert '协调人' in str(receipt)
            yes=msg('本群活动星桥出游，最终确定10:30。',user=30000,group=90000)
            u.commit(yes,'90000',{'event_id':mid,'kind':'decision'},4)
            assert '已确认安排：本群活动星桥出游，最终确定10:30。' in u.summary(u.cards(a)[0])
    asyncio.run(run())


def test_group_private_update_requires_current_membership_and_same_id(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥出游，建议周末讨论。',group=90000)
            mid=u.commit(a,'90000',{'title':'星桥出游'})
            p=msg('补充群事件 '+mid+'：我愿意整理路线。')
            assert not u.member(p,'90000')
            async def call(action,params):return {'user_id':20000,'group_id':90000}
            with patch.object(b.client,'call',call,create=True):await u.prepare(p)
            u.commit(p,'90000',{'event_id':mid,'kind':'commitment'},1)
            assert u.cards(a)[0]['revision']==2 and len(u.cards(a)[0]['contributions'])==2
            snap=u.context(p);assert snap and u.valid(p,snap)
            u.mark_member('90000','20000','revoked');assert not u.valid(p,snap)
            with pytest.raises(PermissionError):u.commit(p,'90000',{'event_id':mid},2)
    asyncio.run(run())


def test_group_personal_stop_does_not_cancel_shared_event(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥出游，我建议周末。',group=90000)
            mid=u.commit(a,'90000',{'title':'星桥出游'})
            other=msg('/events group stop '+mid,user=30000,group=90000)
            assert '其他成员' in str(u.command(other))
            assert not u.cards(other) and len(u.cards(a))==1
            assert '只有明确' in str(u.command(replace(other,text='/events group cancel '+mid)))
            assert u.cards(a)[0]['state']=='active'
    asyncio.run(run())


def test_group_source_limit_rejects_without_partial_or_lost_disagreements(tmp_path):
    async def run():
        async with unified(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;a=msg('本群活动星桥出游，'+('详细安排。'*80),group=90000)
            mid=u.commit(a,'90000',{'title':'星桥出游'})
            assert len(u.cards(a)[0]['contributions'][0]['text'])>200
            before=u.cards(a)[0]
            with pytest.raises(ValueError):u.commit(msg('本群活动星桥出游，'+('太长'*1200),group=90000),'90000',{'event_id':mid},1)
            assert u.cards(a)[0]['revision']==before['revision']
            with pytest.raises(ValueError):u.commit(msg('本群活动星桥出游，更新安排。',group=90000),'90000',{'event_id':mid},0)
            assert len(u.cards(a)[0]['contributions'])==1
    asyncio.run(run())


def test_policy_backup_cas_and_legacy_stops_preserved(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            p=msg('帮我跟进星桥资料整理计划。');choice[0]['title']='星桥资料';await s.observe(p,[])
            card=s.cards(p)[0];s.people.controls.handle(msg('忘掉星桥资料'))
            preview=s.unified.preview()
            with pytest.raises(ValueError):s.unified.configure('20000',0,{'enabled':True},'stale')
            s.unified.configure('20000',0,{'enabled':True},preview['version'])
            assert list((tmp_path/'backups/unified-events').glob('*.sqlite3'))
            assert not s.context(msg('之前星桥资料如何？',group=90000))
            assert s.people._canonical('20000')[0]['id']==card['id']
            assert s.unified.policy()['values']['minimum_level']=='important'
            assert not b.relationships.policy()['values']['enabled']
    asyncio.run(run())


@pytest.mark.parametrize('partial',[False,True])
def test_QQ_stream_error_never_becomes_successful_answer(server,tmp_path,partial):
    async def response(req,channel_turn=None):
        async def chunks():
            if partial:yield 'event: text\ndata: {"text":"some text"}\n\n'
            yield 'event: stream_error\ndata: {"message":"failure"}\n\n'
            yield 'event: answer_complete\ndata: {"text":"API stream interrupted: ConnectError: All connection attempts failed"}\n\n'
        return StreamingResponse(chunks())
    responder=QQChatResponder(QQSettings(model='api',owner_ids={'20000'},recall_mode='off'),server.ChatWithMotion,response,
        lambda:'api',lambda:{'configured':True,'api_key':'FIXTURE','api_url':'https://fixture.invalid','model':'fixture'})
    async def run():
        with pytest.raises(RuntimeError,match='qq_generation_failed'):
            await responder(QQMessage('fixture','10000:private:20000','10000','20000','','1','hello',False),[],'')
    asyncio.run(run())


def test_transport_diagnostic_never_exposes_provider_details():
    cause=ConnectionRefusedError(10061,'https://secret.invalid/key?token=SECRET')
    error=httpx.ConnectError('API_KEY_SECRET');error.__cause__=cause
    result=diagnostic(error,request_id='fixture',attempt=3)
    assert result['causes'][-1]['errno']==10061
    assert 'SECRET' not in json.dumps(result) and 'https' not in json.dumps(result)
