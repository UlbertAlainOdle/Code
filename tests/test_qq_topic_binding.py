import asyncio
from dataclasses import replace
import hashlib
import json
import pytest
from test_qq_topic_routing import topics,item,create
from saya_app.services.qq_topic_identity import source_anchor,repair_split
from test_qq_chat_runtime import server
from saya_app.services.qq_chat import QQChatResponder,context_turn_reference
from fastapi.responses import StreamingResponse


def test_other_speaker_partial_clause_binds_same_named_group_topic(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('本群星桥虚构读书会，我愿意负责整理书单。',group=90000)
            choice[0]={'items':[item(first.text,personal=create(),group=create(),mode='commitment')]};await s.observe(first,[])
            target=s.unified.cards(first)[0]['id']
            clause='我只在周日下午有空，愿意准备讨论提纲。'
            other=msg('星桥虚构读书会：'+clause,user=30000,group=90000)
            # Reproduce the original classifier mistake, not an idealized update.
            choice[0]={'items':[item(clause,group=create(),mode='commitment')]};await s.observe(other,[])
            cards=s.unified.cards(other)
            assert len(cards)==1 and cards[0]['id']==target and len(cards[0]['contributions'])==2
            assert {c['actor'] for c in cards[0]['contributions']}=={'20000','30000'}
            assert cards[0]['contributions'][-1]['text']==clause
            view=s.unified.context(msg('星桥虚构读书会谁负责什么？',group=90000))
            assert '讨论提纲' in view['text'] and '整理书单' in view['text'] and '尚未定案' in view['text']
    asyncio.run(run())


def test_first_partial_clause_keeps_topic_from_parent_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            clause='我愿意负责准备讨论提纲。';m=msg('星桥读书会：'+clause,group=90000)
            choice[0]={'items':[item(clause,personal=create(),group=create(),mode='commitment')]};await s.observe(m,[])
            assert s.cards(m)[0]['title']==s.unified.cards(m)[0]['title']=='星桥读书会'
            assert s.cards(m)[0]['facts'][0]['quote']==s.unified.cards(m)[0]['contributions'][0]['text']==clause
    asyncio.run(run())


def test_distinct_book_clubs_and_explicit_next_occurrence_stay_separate(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            for name in ['星桥读书会','云帆读书会']:
                clause='我愿意整理本次书单。';m=msg(name+'：'+clause,group=90000)
                choice[0]={'items':[item(clause,group=create())]};await s.observe(m,[])
            assert len(s.unified.cards(m))==2
            next_round=msg('星桥读书会：下周再办一次，我愿意准备新书单。',group=90000)
            value=item('下周再办一次，我愿意准备新书单。',group=create());value.update(group_relation='new_occurrence',occurrence_quote='下周再办一次')
            choice[0]={'items':[value]};await s.observe(next_round,[])
            assert len(s.unified.cards(m))==3
    asyncio.run(run())


def test_ambiguous_same_subject_does_not_force_merge(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            for value in ['星桥读书会：本周讨论。','星桥读书会：下周讨论。']:
                direct=msg(value,group=90000);s.unified.commit(direct,'90000',{'title':'星桥读书会'})
            m=msg('星桥读书会：我愿意整理书单。',group=90000)
            choice[0]={'items':[item('我愿意整理书单。',group=create())]};await s.observe(m,[])
            assert not getattr(m,'topic_results',None)
            assert len(s.unified.cards(m))==2
    asyncio.run(run())


def test_prefix_does_not_leak_across_unlabelled_second_topic():
    assert source_anchor('星桥读书会：我负责书单。',6,'我负责书单。')=='星桥读书会'
    assert source_anchor('星桥读书会：我负责书单；电影结尾很好。',12,'电影结尾很好。')==''
    assert source_anchor('帮我跟进本群活动：我负责书单。',10,'我负责书单。')==''


def test_named_status_read_does_not_create_an_extra_contribution(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥虚构读书会，目前分别是谁负责什么？时间定了吗？',group=90000)
            await s.observe(m,[])
            assert not prompts and not getattr(m,'topic_results',None)
    asyncio.run(run())


def test_repair_proven_split_preserves_claim_and_old_id_alias(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified;anchor='星桥虚构读书会'
            first=msg(anchor+'：我负责书单。',group=90000)
            target=u.commit(first,'90000',{'title':anchor,'kind':'commitment'})
            clause='我只在周日下午有空，愿意准备讨论提纲。'
            original=msg(anchor+'：'+clause,user=30000,group=90000)
            evidence=hashlib.sha256(original.key.encode()).hexdigest()[:32]
            part=replace(original,key=original.key+':topic:0:group',text=clause);part.topic_source=original.key
            span=[original.text.index(clause),len(original.text)]
            part.topic_metadata={'evidence_id':evidence,'mode':'commitment','subject':'self','source_span':span}
            orphan=u.commit(part,'90000',{'title':clause[:20],'kind':'commitment'})
            original_claim=u.cards(original)[0]['contributions'][0] if u.cards(original)[0]['id']==orphan else next(c for c in u.cards(original) if c['id']==orphan)['contributions'][0]
            with s.store.connect() as db:
                db.execute('insert into qq_topic_routes values(?,?,?,?,?,?,?)',(original.key,u.bot,original.user_id,now[0],now[0]+3600,'committed',json.dumps({'results':[{'target':'group','id':orphan,'status':'committed'}]})))
                db.execute('insert into qq_topic_evidence values(?,?,?,?,?,?,?,?)',(evidence,u.bot,original.user_id,'90000',original.key,now[0],now[0]+3600,json.dumps({'quote':clause,'source_span':span,'links':[{'kind':'group','id':orphan}]})))
            with s.store.connect() as db:
                db.execute('begin immediate')
                with pytest.raises(ValueError):
                    repair_split(db,bot=u.bot,group='90000',source=orphan,target=target,source_revision=1,target_revision=0,
                        anchor=anchor,source_text=original.text,source_event=original.key,now=now[0],epoch=s.epoch(),limit=32)
                db.execute('insert into qq_shared_event_stops values(?,?,?,?)',(u.bot,'30000',orphan,now[0]))
                with pytest.raises(ValueError):
                    repair_split(db,bot=u.bot,group='90000',source=orphan,target=target,source_revision=1,target_revision=1,
                        anchor=anchor,source_text=original.text,source_event=original.key,now=now[0],epoch=s.epoch(),limit=32)
                db.execute('delete from qq_shared_event_stops where card=?',(orphan,))
                result=repair_split(db,bot=u.bot,group='90000',source=orphan,target=target,source_revision=1,target_revision=1,
                    anchor=anchor,source_text=original.text,source_event=original.key,now=now[0],epoch=s.epoch(),limit=32)
            card=u.cards(original)[0]
            assert len(u.cards(original))==1 and card['id']==target and card['revision']==2
            assert card['contributions'][-1]==original_claim and result['old_ID_alias']
            assert s.topics.resolve_alias(orphan,'90000')==target
            assert s.topics.resolve_alias(orphan,'90001')==orphan
            assert '讨论提纲' in str(u.command(replace(original,text='/events group show '+orphan)))
            assert u.context(replace(original,text=orphan))['refs'][0]['id']==target
            with s.store.connect() as db:
                db.execute('begin immediate')
                with pytest.raises(ValueError):
                    repair_split(db,bot=u.bot,group='90000',source=orphan,target=target,source_revision=1,target_revision=1,
                        anchor=anchor,source_text=original.text,source_event=original.key,now=now[0],epoch=s.epoch(),limit=32)
    asyncio.run(run())


def test_named_status_lookup_uses_current_topic_without_old_unanswered_history(server,tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            doc=msg('星桥资料：我负责提交截止资料。')
            choice[0]={'items':[item('我负责提交截止资料。',personal=create(),mode='commitment')]};await s.observe(doc,[])
            book=msg('星桥虚构读书会：我愿意整理书单。',group=90000)
            choice[0]={'items':[item('我愿意整理书单。',personal=create(),group=create(),mode='commitment')]};await s.observe(book,[])
            before={c['id']:c['revision'] for c in s.people._canonical('20000')}
            history=['Doctor: 星桥资料什么时候截止？','Saya: OLD_DOCUMENT_CANARY此前没有可查日期。']
            channels=[]
            async def generate(req,channel_turn):
                channels.append(channel_turn)
                if len(channels)==1:
                    assert channel_turn['named_topic_read']=='星桥虚构读书会'
                    assert channel_turn['history']==[]
                    assert channel_turn['context_selector'](history,[])==([],[])
                    assert 'OLD_DOCUMENT_CANARY' not in context_turn_reference(channel_turn)
                    assert '星桥资料' not in context_turn_reference(channel_turn)
                    assert all('资料' not in c['title'] for c in channel_turn.get('event_context',{}).get('cards',[]))
                else:
                    assert not channel_turn.get('named_topic_read')
                    assert channel_turn['history']
                async def stream():yield 'event: answer_complete\ndata: {"text":"书单由你整理，时间尚未定案。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            await responder(msg('星桥虚构读书会，目前谁负责什么？时间定了吗？',group=90000),history,'named-status')
            await responder(msg('聊聊刚才的选书思路吧。',group=90000),history,'ordinary-chat')
            assert len(channels)==2
            assert before=={c['id']:c['revision'] for c in s.people._canonical('20000')}
    asyncio.run(run())
