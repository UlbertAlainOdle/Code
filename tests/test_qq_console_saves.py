"""Native save consent keeps the original source guards and write journal."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from core_services import KnowledgeBase
from saya_app.routers import qq as routes
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_action_proposals import QQActionProposals
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.qq_memory_console import QQMemoryConsole
from saya_app.services.qq_memory_access import MemoryAccessDenied
from saya_app.services.qq_responses import ConsoleNotice, ControlReceipt, dialogue_body
from saya_app.services.memory_versions import record_version
from test_qq_task_scheduler import make
from test_qq_delivery import event
from test_qq_memory_reference_dialogue import lab as reference_lab


@pytest.fixture
def lab(tmp_path):
    b=make(tmp_path/'bridge',None,memory_management_enabled=True);b.store.initialize(7)
    k=KnowledgeBase(str(tmp_path/'kb'));m=QQMemoryManagement(b,k);b.memory_management=m
    b.action_proposals=QQActionProposals(b.store);m.console=QQMemoryConsole(m)
    sid='session_qq_native_saves.jsonl';b.store.save_conversation('10000:private:20000:shared',[],sid)
    return b,k,m,m.console,sid


def incoming(lab,text='请记住：这是虚构材料，薄暮茶室使用青色纸杯。',mid=1):
    b,_,_,_,_=lab
    q=normalize_event(event(text,mid,20000),b.settings)[0]
    assert b.store.admit(q,b.settings)[0]=='queued'
    return q


def literal(lab,mid=1):
    b,k,m,c,sid=lab;q=incoming(lab,mid=mid)
    result=asyncio.run(m.references.resolve(q,sid))
    assert isinstance(result,ConsoleNotice) and '【操作回执】' not in result
    assert not k.memories
    entry=c.listing('20000')['entries'][0]
    return q,c.preview('20000',entry['id'])


def test_QQ_ignore_is_native_ignore_only_with_full_preview_and_no_long_term_write(lab):
    from scripts.saya_qq_save_panel import save_text
    b,k,m,c,sid=lab;candidate=k.add_memory_candidate('这是虚构旧候选，北浦资料使用蓝色封套。',title='独立旧候选')
    q=incoming(lab,'/memory-ignore '+candidate['id']);before=record_version(candidate)
    for _ in range(2):assert isinstance(c.offer_ignore(q,sid),ConsoleNotice)
    entries=c.listing('20000')['entries'];assert len(entries)==1
    p=c.preview('20000',entries[0]['id']);assert p['requested_action']=='ignore' and candidate['content'] in save_text(p)
    assert '拟忽略的原候选' in save_text(p) and '拟保存第' not in save_text(p)
    assert record_version(k._find_memory_candidate(candidate['id']))==before and not k.memories
    with pytest.raises(ValueError,match='ignore_only'):c.decide('20000',p['id'],p['preview_version'],'approve')
    assert '不会将它改为保存' in c.hint(replace(q,text='记住'),sid)
    with patch.object(c,'covered_events',return_value=[['possible_daily_source']]):
        assert not c.pending_covering('20000',{'possible_daily_source'})
        b.store.set_state(1,'processed')
        result=c.decide('20000',p['id'],p['preview_version'],'ignore')
    assert result['state']=='ignored' and not k.memories and k._find_memory_candidate(candidate['id'])['status']=='ignored'
    assert c.decide('20000',p['id'],p['preview_version'],'ignore')['state']=='ignored'
    assert '已经按原请求忽略' in c.hint(replace(q,text='记住'),sid)


@pytest.mark.parametrize('change',['candidate','permission','topic','source','quote','other_user','group'])
def test_ignore_does_not_bypass_current_candidate_scope_permission_or_original_ingress(lab,change):
    b,k,m,c,sid=lab;candidate=k.add_memory_candidate('这是虚构旧候选，松桥清单使用绿色纸张。')
    q=incoming(lab,'/memory-ignore '+candidate['id'])
    if change in {'quote','other_user','group'}:
        q=replace(q,**({'reply_to':'old'} if change=='quote' else {'user_id':'30000'} if change=='other_user' else {'group_id':'90000'}))
        assert c.offer_ignore(q,sid) is None;assert not c.listing('20000')['entries'];return
    c.offer_ignore(q,sid);p=c.preview('20000',c.listing('20000')['entries'][0]['id'])
    if change=='candidate':k.update_memory_candidate(candidate['id'],{'content':'这是新的虚构候选，原文已变化。'})
    elif change=='permission':b.settings=b.settings.model_copy(update={'memory_write_enabled':False})
    elif change=='topic':b.store.save_conversation(q.scope+':shared',[],'another-native-topic')
    else:
        with b.store.connect() as db:db.execute('delete from inbox')
    with pytest.raises((ValueError,MemoryAccessDenied)):c.decide('20000',p['id'],p['preview_version'],'ignore')
    assert k._find_memory_candidate(candidate['id'])['status']=='pending' and not k.memories


def test_native_ignore_failure_remains_unknown_and_does_not_retry(lab):
    b,k,m,c,sid=lab;candidate=k.add_memory_candidate('这是虚构旧候选，南堤资料只用白纸。');q=incoming(lab,'/memory-ignore '+candidate['id'])
    c.offer_ignore(q,sid);p=c.preview('20000',c.listing('20000')['entries'][0]['id'])
    with patch.object(k,'ignore_memory_candidate',side_effect=OSError('isolated uncertain storage')) as write:
        result=c.decide('20000',p['id'],p['preview_version'],'ignore')
        again=c.decide('20000',p['id'],p['preview_version'],'ignore')
        assert result['state']==again['state']=='unknown' and write.call_count==1
    assert not k.memories and k._find_memory_candidate(candidate['id'])['status']=='pending'


def test_authenticated_QQ_ignore_never_uses_old_direct_candidate_writer(lab):
    from test_qq_r5_maintenance_commands import bind_memory_responder,run_command
    async def run():
        b,k,m,c,sid=lab;candidate=k.add_memory_candidate('这是虚构旧候选，北岸箱子放在书架。');bind_memory_responder(b,m)
        await b.start()
        try:
            sent=await run_command(b,'/memory-ignore '+candidate['id'],9901)
            assert len(sent)==1 and '现在还没有忽略' in sent[0][1] and '【操作回执】' not in sent[0][1]
            assert k._find_memory_candidate(candidate['id'])['status']=='pending' and not k.memories
            assert c.preview('20000',c.listing('20000')['entries'][0]['id'])['requested_action']=='ignore'
        finally:await b.stop()
    asyncio.run(run())


def test_pending_ignore_requires_new_rollback_reader_capability(lab,tmp_path):
    import sqlite3
    from saya_app.services.qq_rollback import memory_rollback_requirements
    b,k,m,c,sid=lab;candidate=k.add_memory_candidate('这是虚构旧候选，原稿使用橙色目录。');q=incoming(lab,'/memory-ignore '+candidate['id']);c.offer_ignore(q,sid)
    target=tmp_path/'rollback/knowledge_base/qq/bridge.sqlite3';target.parent.mkdir(parents=True)
    with b.store.connect() as src,sqlite3.connect(target) as dst:src.backup(dst)
    assert 'qq_console_candidate_ignore_v1' in memory_rollback_requirements(target.parent.parent.parent)


def test_explicit_literal_waits_for_native_preview_and_saves_once_with_actual_QQ_origin(lab):
    b,k,m,c,sid=lab;q,p=literal(lab)
    assert p['plans'][0]['content']=='这是虚构材料，薄暮茶室使用青色纸杯。'
    assert p['plans'][0]['sources'][0]['text']==q.text
    b.store.set_state(1,'processed')
    with b.store.connect() as db:assert db.execute('select count(*) from qq_action_proposals').fetchone()[0]==0
    result=c.decide('20000',p['id'],p['preview_version'],'approve')
    assert result['state']=='done' and len(k.memories)==1
    origin=k.memories[0]['origin'];assert origin['verified_subject']=='20000' and origin['message_id']==q.message_id
    assert c.decide('20000',p['id'],p['preview_version'],'approve')['state']=='done' and len(k.memories)==1
    assert c.preview('20000',p['id'])['plans']==[]
    with b.store.connect() as db:
        assert db.execute('select count(*) from qq_chat_save_operations').fetchone()[0]==1
        assert db.execute('select count(*) from qq_memory_recent').fetchone()[0]==1
        assert db.execute('select count(*) from qq_deliveries').fetchone()[0]==0


def test_native_queue_does_not_allow_QQ_yes_to_commit(lab):
    b,k,m,c,sid=lab;q,p=literal(lab)
    answer=asyncio.run(m.references.resolve(replace(q,text='记住'),sid))
    assert isinstance(answer,ConsoleNotice) and not k.memories
    assert b.action_proposals.reply(q.scope+':shared',sid,'记住',b.controls()['revision'],True) is None


def test_missing_admitted_source_cannot_mint_native_save(lab):
    b,k,m,c,sid=lab
    q=normalize_event(event('请记住：这是虚构材料，纸杯是青色。',88,20000),b.settings)[0]
    with pytest.raises(MemoryAccessDenied):c.literal(q,sid,'这是虚构材料，纸杯是青色。',k.memory_use.snapshot(),b.controls()['revision'],contextual=False)
    assert not c.listing('20000')['entries'] and not k.memories


@pytest.mark.parametrize('change',['permission','topic','epoch','source','expiry','revision'])
def test_pending_save_revocation_clears_source_and_never_writes(lab,change):
    b,k,m,c,sid=lab;q,p=literal(lab)
    if change=='permission':b.settings=b.settings.model_copy(update={'memory_write_enabled':False})
    elif change=='topic':b.store.save_conversation(q.scope+':shared',[],'session_qq_new_topic.jsonl')
    elif change=='epoch':k.memory_use.advance()
    elif change=='source':
        with b.store.connect() as db:db.execute('delete from inbox')
    elif change=='expiry':
        with b.store.connect() as db:db.execute('update qq_memory_console_saves set expires=?',(time.time()-1,))
    else:
        b.settings=b.settings.model_copy(update={'bubble_chars':b.settings.bubble_chars+1})
    with pytest.raises(ValueError):c.decide('20000',p['id'],p['preview_version'],'approve')
    assert not k.memories
    view=c.preview('20000',p['id']);assert not view['plans'] and view['state'] in {'invalid','expired'}


def test_recovery_requires_new_preview_but_preserves_pending_source(lab):
    b,k,m,c,sid=lab;q,p=literal(lab)
    replacement=QQMemoryConsole(m);replacement.recover()
    new=replacement.preview('20000',p['id'])
    assert new['plans']==p['plans'] and new['preview_version']!=p['preview_version']
    with pytest.raises(ValueError):replacement.decide('20000',p['id'],p['preview_version'],'approve')
    assert replacement.decide('20000',new['id'],new['preview_version'],'approve')['state']=='done'


def test_partial_failure_is_not_replayed_or_recreated(lab):
    b,k,m,c,sid=lab;q,p=literal(lab)
    with patch.object(m.references,'capture',side_effect=OSError('isolated disk failure')) as write:
        result=c.decide('20000',p['id'],p['preview_version'],'approve')
        assert result['state']=='unknown' and write.call_count==1
        c.decide('20000',p['id'],p['preview_version'],'approve')
        assert write.call_count==1
    # Redelivery of the exact authenticated request cannot create another op.
    asyncio.run(m.references.resolve(q,sid))
    assert len(c.listing('20000')['entries'])==1 and not k.memories


def test_native_approval_concurrency_has_one_commit(lab):
    b,k,m,c,sid=lab;q,p=literal(lab)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:c.decide('20000',p['id'],p['preview_version'],'approve'),range(2)))
    assert all(r['state']=='done' for r in results) and len(k.memories)==1


def test_selection_then_full_preview_saves_batch_sources_and_attributes(lab,tmp_path):
    async def exercise():
        async with reference_lab(tmp_path/'refs',long=True) as (b,s,k,m,history,q,scope,sid,tag):
            controls=b.controls();await b.update_controls(controls['settings']|{'memory_management_enabled':True},controls['revision'])
            c=QQMemoryConsole(m);m.console=c
            assert b.store.admit(q,b.settings)[0]=='queued'
            options=m.references.options(q,sid)
            answer=m.references.sets.list(q,sid,options)
            assert isinstance(answer,ConsoleNotice)
            p=c.preview('20000',c.listing('20000')['entries'][0]['id'])
            assert p['state']=='select' and len(p['options'])==2
            with pytest.raises(ValueError):c.decide('20000',p['id'],p['preview_version'],'approve')
            full=c.select('20000',p['id'],p['preview_version'],[0,1])
            assert full['state']=='pending' and len(full['plans'])==2 and len(full['plans'][1]['content'])>1600
            with pytest.raises(ValueError):c.decide('20000',p['id'],p['preview_version'],'approve')
            result=c.decide('20000',full['id'],full['preview_version'],'approve')
            assert result['state']=='done' and len(k.memories)==2
            assert any('助手' in x['content'] for x in k.memories)
            assert len(c.preview('20000',p['id'])['result']['items'])==2
    asyncio.run(exercise())


def test_reject_cancels_only_this_intent_and_cannot_be_confirmed(lab):
    b,k,m,c,sid=lab;q,p=literal(lab)
    assert c.decide('20000',p['id'],p['preview_version'],'reject')['state']=='rejected'
    with pytest.raises(ValueError):c.decide('20000',p['id'],p['preview_version'],'approve')
    assert not c.preview('20000',p['id'])['plans'] and not k.memories


def test_marker_free_notice_remains_excluded_from_context_and_wire_history(lab):
    from saya_app.services import qq_delivery as d
    b,k,m,c,sid=lab;q,p=literal(lab)
    notice=ConsoleNotice('已放进记忆操作台，等你核对。')
    assert ControlReceipt(notice) is notice and dialogue_body(notice)==''
    result=d.create(b.store,q,q.scope+':shared',notice,1500,kind='control')
    with b.store.connect() as db:
        row=db.execute('select text from qq_delivery_parts where result_id=?',(result,)).fetchone()
        assert row and '【操作回执】' not in row['text']
        assert db.execute('select content_kind from qq_delivery_part_kinds where result_id=?',(result,)).fetchone()[0]=='control_receipt'


def test_native_save_API_rejects_forged_actor_and_foreign_origin(lab):
    b,k,m,c,sid=lab;q,p=literal(lab)
    b.task=SimpleNamespace(done=lambda:False);b.settings=b.settings.model_copy(update={'admin_token':'fixture-admin'})
    # Recreate the proposal after the settings revision changed.
    q,p=literal(lab,mid=2)
    app=FastAPI();app.include_router(routes.router)
    async def exercise():
        with patch.object(routes,'qq_bridge',b):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',123)),base_url='http://127.0.0.1:8000',
                    headers={'Authorization':'Bearer fixture-admin','Origin':'http://127.0.0.1:8000'}) as client:
                value={'owner_id':'20000','id':p['id'],'preview_version':p['preview_version'],'action':'approve'}
                assert (await client.post('/api/qq/memory-console/save-decision',json=value|{'source_user':'30000'})).status_code==422
                assert (await client.post('/api/qq/memory-console/save-decision',json=value|{'owner_id':'30000'})).status_code==403
                assert (await client.post('/api/qq/memory-console/save-decision',json=value,headers={'Origin':'https://foreign.example'})).status_code==403
                result=await client.post('/api/qq/memory-console/save-decision',json=value)
                assert result.status_code==200 and result.json()['state']=='done'
    asyncio.run(exercise())


@pytest.mark.parametrize('change',['commit','source','version'])
def test_native_QQ_candidate_retains_actual_origin_and_source_barrier(tmp_path,change):
    from saya_app.services.memory_origin import bind_origin,ingress
    async def exercise():
        async with reference_lab(tmp_path/'refs') as (b,s,k,m,history,q,scope,sid,tag):
            controls=b.controls();await b.update_controls(controls['settings']|{'memory_management_enabled':True},controls['revision'])
            m.console=c=QQMemoryConsole(m)
            s.provenance.bind_turn(q,{})
            with bind_origin(ingress('qq',subject=q.user_id,bot_id=q.bot_id,message_id=q.message_id,session_id=sid)):
                candidate=k.add_memory_candidate('这是虚构材料，阅读从短段落开始。',title='虚构阅读候选')
            p=c.candidate('20000',candidate['id'],record_version(candidate))
            assert p['state']=='pending' and p['channel']=='local_candidate'
            assert c.candidate('20000',candidate['id'],record_version(candidate))['id']==p['id']
            if change=='source':
                with b.store.connect() as db:db.execute('delete from qq_event_message_dependencies')
            if change=='version':k.update_memory_candidate(candidate['id'],{'content':'这是虚构材料，候选内容已经发生变化。'})
            if change=='commit':
                assert c.decide('20000',p['id'],p['preview_version'],'approve')['state']=='done'
                assert len(k.memories)==1 and k.memories[0]['origin']['message_id']==q.message_id
                assert k.memories[0]['origin']['verified_subject']=='20000'
            else:
                with pytest.raises(ValueError):c.decide('20000',p['id'],p['preview_version'],'approve')
                assert not k.memories
    asyncio.run(exercise())


def test_native_ignore_uses_original_candidate_CAS_and_never_creates_memory(lab):
    b,k,m,c,sid=lab
    candidate=k.add_memory_candidate('这是虚构旧候选，秋岬音乐会使用木笛。',title='虚构音乐候选')
    p=c.candidate('20000',candidate['id'],record_version(candidate))
    result=c.decide('20000',p['id'],p['preview_version'],'ignore')
    assert result['state']=='ignored' and not k.memories
    assert k._find_memory_candidate(candidate['id'])['status']=='ignored'
    assert c.decide('20000',p['id'],p['preview_version'],'approve')['state']=='ignored' and not k.memories
    # The original result reader must remain safe for a non-save operation.
    assert '没有新增长期记忆' in m.references.result(m.references.actor(SimpleNamespace(bot_id='10000',user_id='20000')),sid,p['id'],across_topics=True)


@pytest.mark.parametrize('action',['approve','reject','unknown'])
def test_explicit_full_user_source_and_daily_draft_cannot_both_save(tmp_path,action):
    from saya_app.services.qq_daily_memory import QQDailyMemory
    async def exercise():
        async with reference_lab(tmp_path/'refs') as (b,s,k,m,history,q,scope,sid,tag):
            controls=b.controls();await b.update_controls(controls['settings']|{'memory_management_enabled':True},controls['revision'])
            m.console=c=QQMemoryConsole(m)
            daily=b.daily_memory=QQDailyMemory(b,k);daily.configure('20000',0,True)
            source=normalize_event(event('我想聊阅读体验',103,20000),b.settings)[0]
            assert b.store.admit(source,b.settings)[0]=='queued';s.provenance.bind_turn(source,{})
            daily.accept(source,sid,{'value':'daily','pending_id':None,'memory_id':None,'content':source.text},[],[],daily.policy()['revision'],k.memory_use.history_floor())
            row=daily.listing('20000')['entries'][0]
            with b.store.connect() as db:db.execute("update qq_daily_entries set state='pending',closes=? where id=?",(time.time()-1,row['id']))
            q=replace(q,text='请保存我刚才说的阅读体验。')
            assert b.store.admit(q,b.settings)[0]=='queued'
            option=next(o for o in m.references.options(q,sid) if o['author']=='用户')
            m.references.preview(q,sid,option)
            p=c.preview('20000',c.listing('20000')['entries'][0]['id'])
            day=daily.preview('20000',row['id']);assert p['id'] in day['waiting_on']
            with pytest.raises(ValueError):daily.decide('20000',row['id'],day['preview_version'],'approve')
            if action=='unknown':
                with patch.object(m.references,'capture',side_effect=OSError('isolated disk failure')):
                    assert c.decide('20000',p['id'],p['preview_version'],'approve')['state']=='unknown'
                assert p['id'] in daily.preview('20000',row['id'])['waiting_on']
                assert not k.memories
            else:
                c.decide('20000',p['id'],p['preview_version'],action)
                current=daily.preview('20000',row['id'])
                if action=='approve':
                    assert current['state']=='superseded' and not current['sources'] and len(k.memories)==1
                else:
                    assert not current['waiting_on']
                    assert daily.decide('20000',row['id'],current['preview_version'],'approve')['state']=='saved' and len(k.memories)==1
    asyncio.run(exercise())


def test_background_sweep_clears_expired_native_draft_without_console_reads(lab):
    from saya_app.services.qq_daily_memory import QQDailyMemory
    b,k,m,c,sid=lab;q,p=literal(lab)
    daily=b.daily_memory=QQDailyMemory(b,k)
    with b.store.connect() as db:db.execute('update qq_memory_console_saves set expires=?',(time.time()-1,))
    async def exercise():
        with patch('saya_app.services.qq_daily_memory.asyncio.sleep',side_effect=asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):await daily._run()
    asyncio.run(exercise())
    with b.store.connect() as db:
        row=db.execute('select state,message,payload from qq_memory_console_saves').fetchone()
    assert row['state']=='expired' and row['message']=='{}' and row['payload']=='{}' and not k.memories
