import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_episode_context import QQEpisodeContext
from saya_app.services.qq_person_events import CAPACITY
from saya_app.services.qq_rollback import check_memory_rollback
from saya_app.routers import qq as routes
from test_qq_task_scheduler import make
from test_qq_delivery import event


@asynccontextmanager
async def lab(tmp_path,*,migrated=True,enabled=True):
    bridge=make(tmp_path,None,admin_token='admin');await bridge.start();s=bridge.episodes
    now=[time.time()];s.clock=lambda:now[0];bridge.context_salience.clock=lambda:now[0]
    bridge.context_salience.configure('20000',0,{'enabled':True})
    if enabled:s.configure('20000',0,{'enabled':True})
    prompts=[];choice=[{'action':'create','title':'跟进','confidence':.95}];sequence=[0]
    async def decide(prompt,**kwargs):prompts.append(prompt);return deepcopy(choice[0])
    s.decide=decide
    def message(text='帮我跟进这个虚构计划。',user=20000,group=None):
        sequence[0]+=1;now[0]+=61
        msg,_=normalize_event(event(text,sequence[0],user,group),bridge.settings)
        msg.sent_at=now[0];msg.context_snapshot=bridge.context_salience.snapshot(msg.scope+(':shared' if msg.shared else ':isolated'))
        return msg
    if migrated:s.people.migrate('20000',s.people.migration_preview()['version'])
    try:yield bridge,s,now,message,choice,prompts
    finally:await bridge.stop()


def test_empty_migration_preserves_disabled_and_is_idempotent_with_backup(tmp_path):
    async def run():
        async with lab(tmp_path,migrated=False,enabled=False) as (b,s,now,msg,choice,prompts):
            preview=s.people.migration_preview();assert preview['writes']==0 and not preview['enabled']
            with pytest.raises(PermissionError):s.people.migrate('30000',preview['version'])
            with pytest.raises(ValueError):s.people.migrate('20000','stale')
            assert not s.people.active()
            result=s.people.migrate('20000',preview['version'])
            assert result['status']=='success' and not s.policy()['values']['enabled'] and s.people.active()
            assert list((tmp_path/'backups/person-events-v2').glob('*.sqlite3'))
            assert s.people.migrate('20000',s.people.migration_preview()['version'])['status']=='unchanged'
            await s.observe(msg(),[]);assert not prompts
    asyncio.run(run())


def test_same_account_cross_scope_same_id_and_hidden_correction_does_not_show_old_state(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            private=msg('帮我跟进PRIVATE_CANARY秘密星湾计划。');await s.observe(private,[])
            mid=s.cards(private)[0]['id'];original=s.cards(private)[0]['revision']
            group=msg('群里有什么计划？',group=90000)
            assert not s.cards(group) and 'PRIVATE_CANARY' not in str(s.command(replace(group,text='/events self')))
            choice[0]={'action':'update','event_id':mid,'title':'公开补充','confidence':.95}
            public=msg('继续事件 '+mid+'，公开补充：周末准备去公园。',group=90000)
            await s.observe(public,[])
            assert 'PRIVATE_CANARY' not in prompts[-1]
            assert len(s.cards(public))==1 and s.cards(public)[0]['id']==mid and s.cards(public)[0]['revision']>original
            assert 'PRIVATE_CANARY' not in json.dumps(s.cards(public),ensure_ascii=False)
            assert 'PRIVATE_CANARY' in json.dumps(s.cards(private),ensure_ascii=False)
            snapshot=s.context(replace(public,text='继续之前的计划。'));assert snapshot
            private_update=msg('更正一下，现在决定私人计划取消原安排。')
            choice[0]={'action':'update','event_id':mid,'title':'私人计划','confidence':.95,'supersedes':[0,1]}
            await s.observe(private_update,[])
            assert not s.cards(group) and not s.valid(public,snapshot)
            assert not s.cards(replace(group,group_id='91000'))
            assert len(s.people._canonical('20000'))==1
    asyncio.run(run())


def test_same_nickname_other_account_and_forged_target_never_merge(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            owner=msg();await s.observe(owner,[]);mid=s.cards(owner)[0]['id']
            other=msg('帮我跟进这个虚构计划。',user=30000);await s.observe(other,[])
            assert s.cards(other)[0]['id']!=mid
            choice[0]={'action':'update','event_id':mid,'confidence':1}
            await s.observe(msg('我要更正本人事件 '+mid+' 的最新计划。',user=30000),[])
            assert len(s.people._canonical('20000'))==1 and s.cards(owner)[0]['revision']==1
            assert mid not in prompts[-1].split('available_events')[-1]
    asyncio.run(run())


def test_legacy_unique_migrates_same_id_mixed_public_preserved_and_old_previews_invalid(tmp_path):
    async def run():
        async with lab(tmp_path,migrated=False) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);card=s.cards(first)[0];mid=card['id']
            old_snapshot=s.context(first);assert old_snapshot
            old_policy=s.policy()['revision']
            with s.store.connect() as db:
                mixed=deepcopy(card);mixed['facts'].append({**mixed['facts'][0],'speaker':'30000','source':'other'})
                for k in ['id','revision','paused_for_actor']:mixed.pop(k,None)
                db.execute('insert into qq_episode_cards values(?,?,?,?)',('mixed','10000:group:90000',1,json.dumps(mixed)))
                db.execute('insert into qq_episode_deletions values(?,?,?,?,?,?)',('old-token',s.scope(first),'20000',mid,1,now[0]+600))
            preview=s.people.migration_preview()
            assert {e['action'] for e in preview['entries']}=={'migrate','keep_legacy'}
            result=s.people.migrate('20000',preview['version'])
            assert result['migrated']==1 and result['legacy_preserved']==1 and s.cards(first)[0]['id']==mid
            assert s.policy()['revision']>old_policy and not s.valid(first,old_snapshot)
            with s.store.connect() as db:
                assert db.execute('select scope from qq_episode_cards where id=?',('mixed',)).fetchone()[0]=='10000:group:90000'
                assert db.execute('select count(*) from qq_episode_deletions').fetchone()[0]==0
                assert db.execute('select count(*) from qq_person_event_migrations').fetchone()[0]==1
            again=QQEpisodeContext(b,clock=lambda:now[0]);assert again.people.active() and again.cards(first)[0]['id']==mid
    asyncio.run(run())


@pytest.mark.parametrize('level,pair',list(CAPACITY.items()))
def test_relationship_capacity_uses_global_manual_level_not_group_affinity(tmp_path,level,pair):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            if level=='owner':subject='20000'
            else:
                subject='30000';b.relationships.configure_policy('20000',0,{'enabled':True})
                b.relationships.configure('20000',subject,'global',0,{'level':level})
                b.relationships.configure('20000',subject,'group:90000',0,{'level':'distant' if level!='distant' else 'important'})
            cap=s.people.capacity(subject)
            assert (cap['max_active'],cap['max_total'])==pair and not cap['relation_grants_permissions']
    asyncio.run(run())


def test_relation_off_stable_default_capacity_and_cooling_24_hour_grace(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            b.relationships.configure('20000','30000','global',0,{'level':'important'})
            assert s.people.capacity('30000')['max_total']==6  # Relationship learning remains off.
            b.relationships.configure_policy('20000',0,{'enabled':True})
            for i in range(8):await s.observe(msg('帮我跟进虚构编号计划'+str(i),user=30000),[])
            before=s.people._canonical('30000');assert len(before)==8
            b.relationships.configure('20000','30000','global',1,{'level':'distant'})
            query=msg('之前计划有哪些？',user=30000);s.cards(query)
            cards=s.people._canonical('30000');assert len(cards)==8
            assert sum(c['state']=='active' for c in cards)<=1 and any(c.get('capacity_grace_until') for c in cards)
            now[0]+=86401;s.cards(query)
            assert len(s.people._canonical('30000'))==3
    asyncio.run(run())


def test_delete_is_bound_to_actor_visibility_revision_and_expiry(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            private=msg();await s.observe(private,[]);mid=s.cards(private)[0]['id']
            receipt=str(s.command(replace(private,text='/events delete '+mid)))
            token=re.search(r'confirm ([a-f0-9]+)',receipt)[1]
            group=msg('/events confirm '+token,group=90000)
            assert '未删除' in s.command(group) and s.cards(private)
            assert '未删除' in s.command(replace(private,user_id='30000',text='/events confirm '+token))
            assert '已删除' in s.command(replace(private,text='/events confirm '+token)) and not s.cards(private)
            assert '未删除' in s.command(replace(private,text='/events confirm '+token))
    asyncio.run(run())


def test_group_total_and_other_user_commands_never_expose_private_titles(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            choice[0]['title']='PRIVATE_CANARY'
            private=msg('帮我跟进PRIVATE_CANARY私密计划。');await s.observe(private,[])
            for command in ['/events all','/events user 20000','/events self','/events show '+s.cards(private)[0]['id']]:
                result=str(s.command(msg(command,group=90000)))
                assert 'PRIVATE_CANARY' not in result and '本人总预算' not in result
            assert 'PRIVATE_CANARY' in s.command(replace(private,text='/events all'))
    asyncio.run(run())


@pytest.mark.parametrize('dimension',['subjects','cards'])
def test_hard_global_subject_limit_does_not_evict_owner(tmp_path,dimension):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            own=msg();await s.observe(own,[]);owner=s.cards(own)[0]
            with s.store.connect() as db:
                original=json.loads(db.execute('select data from qq_episode_cards where id=?',(owner['id'],)).fetchone()[0])
                for i in range(254 if dimension=='subjects' else 1999):
                    subject=str(40000+i) if dimension=='subjects' else '40000'
                    data={**original,'subject':subject}
                    db.execute('insert into qq_episode_cards values(?,?,?,?)',(f'other-{i}',s.people.scope(subject),1,json.dumps(data)))
            await s.observe(msg(user=30000),[])
            assert not s.people._canonical('30000') and s.cards(own)[0]['id']==owner['id']
    asyncio.run(run())


def test_migration_api_authorization_CAS_and_owner_private_page(tmp_path,monkeypatch):
    async def run():
        async with lab(tmp_path,migrated=False,enabled=False) as (b,s,now,msg,choice,prompts):
            monkeypatch.setattr(routes,'qq_bridge',b);app=FastAPI();app.include_router(routes.router)
            with TestClient(app) as client:
                assert client.get('/api/qq/person-events/migration').status_code==401
                headers={'Authorization':'Bearer admin'}
                preview=client.get('/api/qq/person-events/migration',headers=headers).json()
                assert client.post('/api/qq/person-events/migration',headers=headers,json={'confirmed':True,'owner_id':'30000','expected_version':preview['version']}).status_code==403
                assert client.post('/api/qq/person-events/migration',headers=headers,json={'confirmed':True,'owner_id':'20000','expected_version':'stale'}).status_code==409
                result=client.post('/api/qq/person-events/migration',headers=headers,json={'confirmed':True,'owner_id':'20000','expected_version':preview['version']})
                assert result.status_code==200 and not result.json()['enabled']
                assert client.get('/api/qq/person-events',headers=headers).json()['items']==[]
    asyncio.run(run())


def test_roll_back_reader_rejects_personal_schema_even_events_off(tmp_path):
    async def run():
        async with lab(tmp_path,enabled=False) as (b,s,now,msg,choice,prompts):
            root=tmp_path/'project';target=root/'knowledge_base/qq';target.mkdir(parents=True)
            import sqlite3
            with s.store.connect() as src,sqlite3.connect(target/'bridge.sqlite3') as dst:src.backup(dst)
            gate=check_memory_rollback(root,{'memory_compatibility':[]})
            assert 'qq_person_events_v2' in gate['missing']
    asyncio.run(run())


def test_quoted_forwarded_media_and_paused_source_never_rewrite_person_event(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            own=msg();await s.observe(own,[]);mid=s.cards(own)[0]['id']
            choice[0]={'action':'update','event_id':mid,'confidence':1,'supersedes':[0]}
            before=len(prompts)
            for text,fields in [('他说“更正一下我的计划。”',{}),('更正一下我的计划，改为其他。',{'forward_present':True}),
                    ('更正一下我的计划，改为其他。',{'media_types':('image',)}),('更正一下我的计划，改为其他。',{'reply_to':'old'})]:
                await s.observe(replace(msg(text),**fields),[])
            assert len(prompts)==before and s.cards(own)[0]['revision']==1
            snapshot=s.context(own);assert snapshot
            group=msg('继续之前的计划。',group=90000)
            assert not s.valid(group,snapshot)
    asyncio.run(run())


def test_expired_hidden_latest_state_does_not_revive_previous_public_state(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            public=msg('帮我跟进PUBLIC_OLD公开计划。',group=90000);await s.observe(public,[])
            mid=s.cards(public)[0]['id']
            choice[0]={'action':'update','event_id':mid,'confidence':.95}
            await s.observe(msg('私人补充：最新状态暂不公开。'),[])
            with s.store.connect() as db:
                card=json.loads(db.execute('select data from qq_episode_cards where id=?',(mid,)).fetchone()[0])
                card['facts'][-1]['expires']=now[0]+1
                db.execute('update qq_episode_cards set data=? where id=?',(json.dumps(card),mid))
            now[0]+=40;s.sweep(force=True)
            assert not s.cards(public)
    asyncio.run(run())


def test_delete_old_revision_and_expired_confirm_never_apply(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            own=msg();await s.observe(own,[]);mid=s.cards(own)[0]['id']
            def preview():return re.search(r'confirm ([a-f0-9]+)',str(s.command(replace(own,text='/events delete '+mid))))[1]
            token=preview();choice[0]={'action':'update','event_id':mid,'confidence':.95}
            await s.observe(msg('继续之前的虚构计划，新补充一条。'),[])
            assert '未删除' in s.command(replace(own,text='/events confirm '+token))
            token=preview();now[0]+=601
            assert '未删除' in s.command(replace(own,text='/events confirm '+token)) and s.cards(own)
    asyncio.run(run())


def test_pre_migration_backup_restores_copy_without_overwriting_evidence(tmp_path):
    async def run():
        async with lab(tmp_path,migrated=False) as (b,s,now,msg,choice,prompts):
            own=msg();await s.observe(own,[]);mid=s.cards(own)[0]['id']
            s.people.migrate('20000',s.people.migration_preview()['version'])
            backup=next((tmp_path/'backups/person-events-v2').glob('*.sqlite3'));before=backup.read_bytes()
            import shutil
            from saya_app.services.qq_bridge import QQStore
            restore=tmp_path/'restored.sqlite3';shutil.copy2(backup,restore)
            other=make(tmp_path/'other',None);other.store=QQStore(restore)
            from types import SimpleNamespace
            other.expressions=SimpleNamespace(decide=s.decide)
            restored=QQEpisodeContext(other,clock=lambda:now[0])
            assert not restored.people.active() and restored.cards(own)[0]['id']==mid
            assert backup.read_bytes()==before and s.people.active()
    asyncio.run(run())


def test_repeated_same_evidence_is_noop_without_a_second_model_call(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg();await s.observe(first,[]);before=deepcopy(s.people._canonical('20000'))
            await s.observe(msg(),[])
            assert len(prompts)==1 and s.people._canonical('20000')==before
    asyncio.run(run())
