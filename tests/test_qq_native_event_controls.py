"""Synthetic native consent commits through the original personal settings writer."""
import asyncio,json,sqlite3
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
import pytest
from saya_app.services.qq_event_console import QQEventConsole
from saya_app.services.qq_responses import ConsoleNotice
from saya_app.services.qq_rollback import memory_rollback_requirements
from scripts.saya_qq_event_panel import event_text
from test_qq_topic_routing import topics
from test_qq_event_operations import seed
from test_qq_r5_maintenance_commands import run_command


def offer(b,s,msg,card,command):
    console=s.operations.console=QQEventConsole(s.operations)
    request=msg(command.replace('TARGET',card['id']));assert b.store.admit(request,b.settings)[0]=='queued'
    answer=console.command(request);assert isinstance(answer,ConsoleNotice) and '【操作回执】' not in answer
    p=console.preview('20000',console.listing('20000')['entries'][0]['id'])
    assert p['state']=='native_prepared' and len(p['targets'])==1
    return console,request,p


@pytest.mark.parametrize('command,action',[
    ('/events keep TARGET','keep'),('/events unkeep TARGET','unkeep'),
    ('/events importance TARGET high','importance'),('/events importance TARGET auto','importance'),
    ('/events deadline TARGET 明天 18:00','timing'),('/events schedule TARGET 2026-12-03 09:00+08:00','timing'),
    ('/events deadline TARGET 明天','timing'),
    ('/events deadline TARGET clear','timing')])
def test_full_preview_then_original_writer_one_transaction_and_idempotent_result(tmp_path,command,action):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);before=deepcopy(s.people._canonical('20000'))
            c,request,p=offer(b,s,msg,cards[0],command)
            assert p['proposed']['action']==action and p['targets'][0]['sources']
            rendered=event_text(p);assert p['targets'][0]['sources'][0]['text'] in rendered and '当前重要度' in rendered
            assert s.people._canonical('20000')==before
            assert '后台' in str(await s.operations.handle(msg('对的')))
            row,data=c.row('20000',p['id'])
            # Even an original delivered-QQ executor cannot consume this variant.
            s.operations.execute(request,row,data)
            assert s.people._canonical('20000')==before
            if action=='timing' and p['proposed']['timing']['value']!='clear':
                target_time=p['proposed']['timing']['value'];assert target_time in rendered
            now[0]+=60
            result=c.decide('20000',p['id'],p['preview_version'],'confirm');assert result['state']=='done'
            assert c.decide('20000',p['id'],p['preview_version'],'confirm')['state']=='done'
            current=s.people._canonical('20000');target=next(x for x in current if x['id']==cards[0]['id'])
            previous=next(x for x in before if x['id']==target['id'])
            for key in ['facts','expires','created','last_user','count']:
                if key in previous:assert target[key]==previous[key]
            assert next(x for x in current if x['id']==cards[1]['id'])==next(x for x in before if x['id']==cards[1]['id'])
            if action=='keep':assert target['keep'] is True
            elif action=='unkeep':assert target['keep'] is False
            elif action=='importance':
                assert target['importance_locked'] is ('auto' not in command)
                if 'high' in command:assert target['importance']=='high'
            else:
                timing=target['timing'][p['proposed']['timing']['kind']]
                if 'clear' in command:assert timing['status']=='removed'
                else:
                    assert timing['local'].startswith(target_time)
                    if command.endswith('明天'):assert timing['precision']=='day'
            with b.store.connect() as db:
                audit=db.execute('select event from qq_event_control_audit').fetchall();assert len(audit)==1 and audit[0][0]=='native-console:event:'+p['id']
                result=db.execute('select data from qq_event_operation_results').fetchall();assert len(result)==1 and json.loads(result[0][0])['native_control'] is True
            assert 'proposed' not in c.preview('20000',p['id'])
    asyncio.run(run())


@pytest.mark.parametrize('change',['body','shared','owner','topic','timezone','epoch','expiry','restart'])
def test_stale_setting_proposals_cannot_commit(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);c,request,p=offer(b,s,msg,cards[0],'/events importance TARGET high')
            if change=='body':
                with b.store.connect() as db:
                    row=db.execute('select data from qq_episode_cards where id=?',(cards[0]['id'],)).fetchone();data=json.loads(row[0]);data['title']='新的标题'
                    db.execute('update qq_episode_cards set data=?,revision=revision+1 where id=?',(json.dumps(data),cards[0]['id']))
            elif change=='shared':b.settings=b.settings.model_copy(update={'shared_memory':False})
            elif change=='owner':b.settings=b.settings.model_copy(update={'owner_ids':frozenset({'30000'})})
            elif change=='topic':b.store.save_conversation('10000:private:20000:shared',[],'new-native-topic')
            elif change=='timezone':
                state=b.context_salience.state();b.context_salience.configure('20000',state['revision'],{'timezone':'America/New_York'})
            elif change=='epoch':b.event_memory_use=type('Epoch',(),{'snapshot':lambda self:'different'})()
            elif change=='expiry':now[0]+=601
            else:c=s.operations.console=QQEventConsole(s.operations)
            with pytest.raises((ValueError,PermissionError)):c.decide('20000',p['id'],p['preview_version'],'confirm')
            with b.store.connect() as db:
                assert db.execute('select count(*) from qq_event_control_audit').fetchone()[0]==0
                assert db.execute('select count(*) from qq_event_operation_results').fetchone()[0]==0
    asyncio.run(run())


def test_control_and_native_audits_roll_back_together_after_last_write_failure(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);before=deepcopy(s.people._canonical('20000'));c,request,p=offer(b,s,msg,cards[0],'/events keep TARGET')
            original=s.people.controls._commit
            def fail(*args,native_transaction=None,**kwargs):
                def callback(db,phase,result):
                    native_transaction(db,phase,result)
                    if phase=='committed':raise OSError('isolated simulated final audit failure')
                return original(*args,native_transaction=callback,**kwargs)
            with patch.object(s.people.controls,'_commit',fail):result=c.decide('20000',p['id'],p['preview_version'],'confirm')
            assert result['state']=='failed' and s.people._canonical('20000')==before
            with b.store.connect() as db:
                assert db.execute('select count(*) from qq_event_control_audit').fetchone()[0]==0
                assert db.execute('select count(*) from qq_event_operation_results').fetchone()[0]==0
    asyncio.run(run())


def test_concurrent_native_confirmation_and_reject_keep_original_writer_boundaries(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);c,request,p=offer(b,s,msg,cards[0],'/events keep TARGET')
            with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:c.decide('20000',p['id'],p['preview_version'],'confirm'),range(2)))
            assert all(x['state']=='done' for x in results)
            with b.store.connect() as db:assert db.execute('select count(*) from qq_event_control_audit').fetchone()[0]==1
            c,request,p=offer(b,s,msg,cards[0],'/events unkeep TARGET')
            assert c.decide('20000',p['id'],p['preview_version'],'reject')['state']=='refused'
            assert next(x for x in s.people._canonical('20000') if x['id']==cards[0]['id'])['keep']
    asyncio.run(run())


@pytest.mark.parametrize('command',['/events importance TARGET high\n/events stop TARGET','/events importance TARGET impossible','/events deadline TARGET 2026-02-30','/events schedule TARGET 2026-11-01 01:30'])
def test_invalid_parameters_never_fall_through_to_a_write(tmp_path,command):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);console=s.operations.console=QQEventConsole(s.operations)
            if '01:30' in command:
                state=b.context_salience.state();b.context_salience.configure('20000',state['revision'],{'timezone':'America/New_York'})
            before=deepcopy(s.people._canonical('20000'));request=msg(command.replace('TARGET',cards[0]['id']));b.store.admit(request,b.settings)
            assert isinstance(console.command(request),ConsoleNotice)
            assert not console.listing('20000')['entries'] and s.people._canonical('20000')==before
    asyncio.run(run())


def test_authenticated_bridge_migrates_metadata_without_model_or_immediate_write(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);console=s.operations.console=QQEventConsole(s.operations);before=deepcopy(s.people._canonical('20000'))
            result=await run_command(b,'/events importance '+cards[0]['id']+' high',9001)
            assert len(result)==1 and '后台' in result[0][1] and '【操作回执】' not in result[0][1]
            assert s.people._canonical('20000')==before
            assert console.listing('20000')['entries'][0]['action']=='importance'
    asyncio.run(run())


def test_native_metadata_capability_survives_session_cleanup(tmp_path):
    async def run():
        async with topics(tmp_path/'lab') as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);c,request,p=offer(b,s,msg,cards[0],'/events keep TARGET')
            c.decide('20000',p['id'],p['preview_version'],'confirm')
            with b.store.connect() as db:db.execute('delete from qq_event_operation_sessions')
            target=tmp_path/'rollback/knowledge_base/qq/bridge.sqlite3';target.parent.mkdir(parents=True)
            with b.store.connect() as src,sqlite3.connect(target) as dst:src.backup(dst)
            assert 'qq_native_event_controls_v1' in memory_rollback_requirements(target.parent.parent.parent)
    asyncio.run(run())
