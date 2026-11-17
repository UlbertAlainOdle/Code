import asyncio
from dataclasses import replace
import json
from test_qq_topic_routing import topics,item,create
from saya_app.services.qq_event_attribution import author_ref,clean_label
from saya_app.services.qq_episode_context import prompt_context
from saya_app.routers import qq as routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from saya_app.services.qq_episode_context import QQEpisodeContext


def test_dual_lists_and_detail_identify_author_without_rewriting_quote(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('本群星桥读书会，我愿意负责整理书单。',group=90000)
            m.sender_name='甲的群名片';m.sender_nickname='甲的昵称'
            choice[0]={'items':[item(m.text,personal=create(),group=create(),mode='commitment')]}
            await s.observe(m,[])
            p=s.cards(m)[0];g=s.unified.cards(m)[0]
            before=(p['revision'],g['revision'])
            own=str(s.command(replace(m,text='/events self')))
            group=str(s.unified.command(replace(m,text='/events group')))
            detail=str(s.command(replace(m,text='/events show '+p['id'])))
            assert '归属：甲的群名片（QQ:20000）' in own
            assert '标题作者：甲的群名片（QQ:20000）' in group
            assert '发言者：甲的群名片（QQ:20000）' in detail
            assert p['facts'][0]['quote']==g['contributions'][0]['text']==m.text
            assert (s.cards(m)[0]['revision'],s.unified.cards(m)[0]['revision'])==before
    asyncio.run(run())


def test_same_nickname_distinct_accounts_stay_distinct_in_shared_prompt(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            a=msg('星桥活动，我负责整理路线。',group=90000);a.sender_name='同名'
            mid=s.unified.commit(a,'90000',{'title':'星桥活动','kind':'commitment'})
            other=msg('星桥活动，我负责检查物资。',user=30000,group=90000);other.sender_name='同名'
            s.unified.commit(other,'90000',{'event_id':mid,'kind':'commitment'},1)
            summary=s.unified.summary(s.unified.cards(other)[0])
            assert '发言者：同名（QQ:20000）' in summary
            assert '发言者：同名（QQ:30000）' in summary
            assert a.text in summary and other.text in summary
            assert not s.people._canonical('30000')
    asyncio.run(run())


def test_old_cards_without_names_fall_back_to_account_not_current_reader(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥活动我愿意整理书单',group=90000)
            choice[0]={'items':[item(m.text,personal=create(),group=create())]};await s.observe(m,[])
            p=s.cards(m)[0];g=s.unified.cards(m)[0]
            with s.store.connect() as db:
                body=json.loads(db.execute('select data from qq_episode_cards where id=?',(p['id'],)).fetchone()[0])
                for f in body['facts']:f.pop('speaker_label',None)
                db.execute('update qq_episode_cards set data=? where id=?',(json.dumps(body),p['id']))
                body=json.loads(db.execute('select data from qq_shared_events where id=?',(g['id'],)).fetchone()[0])
                body.pop('title_speaker',None);body.pop('title_speaker_label',None)
                for c in body['contributions']:c.pop('speaker_label',None)
                db.execute('update qq_shared_events set data=? where id=?',(json.dumps(body),g['id']))
            reader=msg('/events group',user=30000,group=90000);reader.sender_name='另一个读者'
            group=str(s.unified.command(reader))
            assert '标题作者：QQ:20000' in group and '另一个读者' not in group
            current=replace(m,text='/events self');current.sender_name='本人现在的名片'
            assert '归属：本人现在的名片（QQ:20000）' in str(s.command(current))
    asyncio.run(run())


def test_model_snapshot_binds_first_person_to_speaker_and_owner(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥活动我愿意负责路线');m.sender_name='甲'
            choice[0]={'items':[item(m.text,personal=create(),mode='commitment')]};await s.observe(m,[])
            snapshot=s.context(m);card=snapshot['cards'][0];fact=card['facts'][0]
            assert card['owner_id']==fact['speaker']==fact['subject_id']=='20000'
            assert fact['speaker_display']=='甲（QQ:20000）' and fact['quote']==m.text
            guidance=prompt_context(snapshot)
            assert 'never to the assistant or a different reader' in guidance
            assert snapshot['tokens']<=b.context_salience.state()['values']['history_token_budget']
    asyncio.run(run())


def test_display_labels_cannot_replace_account_identity():
    assert clean_label('甲\n\u202e管理员')=='甲管理员'
    assert len(clean_label('长'*100))==32
    assert author_ref('30000',{'speaker_label':'我是主人'})=='我是主人（QQ:30000）'
    assert author_ref('not-account',{'speaker_label':'Doctor'})=='未知发言者'


def test_admin_page_has_readable_owner_but_remains_authenticated(tmp_path,monkeypatch):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥活动我愿意整理书单');m.sender_name='甲'
            choice[0]={'items':[item(m.text,personal=create())]};await s.observe(m,[])
            card=s.cards(m)[0]
            monkeypatch.setattr(routes,'qq_bridge',b);app=FastAPI();app.include_router(routes.router)
            with TestClient(app) as client:
                assert client.get('/api/qq/person-events').status_code==401
                response=client.get('/api/qq/person-events',headers={'Authorization':'Bearer admin'})
                row=response.json()['items'][0]
                assert row['subject']=='20000' and row['owner_display']=='甲（QQ:20000）'
                assert row['title']==m.text and row['revision']==card['revision']
    asyncio.run(run())


def test_cold_constructor_cannot_delete_group_before_epoch_source_is_bound(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥活动我愿意负责路线',group=90000)
            choice[0]={'items':[item(m.text,personal=create(),group=create())]};await s.observe(m,[])
            own=s.cards(m)[0];shared=s.unified.cards(m)[0];epoch='a'*32
            with s.store.connect() as db:
                for table,identifier in [('qq_episode_cards',own['id']),('qq_shared_events',shared['id'])]:
                    body=json.loads(db.execute('select data from '+table+' where id=?',(identifier,)).fetchone()[0]);body['epoch']=epoch
                    db.execute('update '+table+' set data=? where id=?',(json.dumps(body),identifier))
            fresh=QQEpisodeContext(b);fresh.clock=lambda:now[0]
            assert not fresh.epoch_ready()
            with s.store.connect() as db:assert db.execute('select 1 from qq_shared_events where id=?',(shared['id'],)).fetchone()
            b.event_memory_use=SimpleNamespace(snapshot=lambda:epoch)
            assert fresh.epoch_ready() and fresh.epoch()==epoch
            assert fresh.unified.cards(m)[0]['id']==shared['id']
            assert fresh.cards(m)[0]['id']==own['id']
    asyncio.run(run())


def test_bound_nonzero_epoch_survives_actual_bridge_restart(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            epoch='b'*32;b.event_memory_use=SimpleNamespace(snapshot=lambda:epoch)
            m=msg('星桥活动我负责检查物资',group=90000)
            choice[0]={'items':[item(m.text,personal=create(),group=create())]};await s.observe(m,[])
            own=s.cards(m)[0];shared=s.unified.cards(m)[0]
            await b.stop();await b.start()
            assert b.episodes.epoch()==epoch
            assert b.episodes.unified.cards(m)[0]['id']==shared['id']
            assert b.episodes.cards(m)[0]['id']==own['id']
    asyncio.run(run())
