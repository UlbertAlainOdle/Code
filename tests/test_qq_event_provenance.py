import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import re
from types import SimpleNamespace
import pytest
from fastapi.responses import StreamingResponse
from test_qq_topic_routing import topics,item,create
from test_qq_delivery import event
from test_qq_task_scheduler import until
from test_qq_chat_runtime import server
from saya_app.services.qq_chat import QQChatResponder


async def seed(s,msg,choice,text='晨光归档：我负责紫鹭清单。',*,group=None,dual=False):
    m=msg(text,group=group)
    choice[0]={'items':[item(text,personal=create() if not group or dual else None,group=create() if group else None,mode='commitment')]}
    await s.observe(m,[])
    return m,s.provenance.current_sources(m)


@pytest.mark.parametrize('action',['stop','pause','delete'])
def test_single_forget_invalidates_inherited_replies_but_preserves_other_known_context(tmp_path,action):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            a,ta=await seed(s,msg,choice)
            z,tz=await seed(s,msg,choice,'湖畔读书：我愿意准备提纲。')
            pa=next(c for c in s.people._canonical('20000') if c['id']==ta['refs'][0]['id'])
            p=s.provenance;empty=p.empty(z)
            inherited=p.inherit(z,empty,[{'role':'ai','content':'第一次转述','qq_event_dependencies':ta}])
            again=p.inherit(z,empty,[{'role':'ai','content':'第二次转述','qq_event_dependencies':inherited}])
            receipt=s.command(msg('/events '+action+' '+pa['id']))
            if action=='delete':
                token=re.search(r'/events confirm ([a-f0-9]{32})',str(receipt))[1]
                s.command(msg('/events confirm '+token))
            assert not p.valid(z,ta) and not p.valid(z,again)
            assert p.valid(z,tz) and p.valid(z,empty)
            rows=[{'role':'ai','content':'旧安排','qq_event_dependencies':again},
                {'role':'ai','content':'独立提纲','qq_event_dependencies':tz},
                {'role':'ai','content':'普通问候','qq_event_dependencies':empty}]
            assert [x['content'] for x in p.label_rows(z,rows)]==['独立提纲','普通问候']
            assert len(prompts)==2
    asyncio.run(run())


def test_source_append_and_attention_keep_original_source_but_refresh_derived_view(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,source=await seed(s,msg,choice);c=s.people._canonical('20000')[0];p=s.provenance
            view=p.empty(m);view['refs']=p.refs(m,'personal',c['id'],view=True)
            s.command(msg('/events importance '+c['id']+' high'))
            assert p.valid(m,source) and p.valid(m,view)
            text='我还会准备一份备份。';u=msg('晨光归档：'+text)
            choice[0]={'items':[item(text,personal={'action':'update','event_id':c['id'],'supersedes':[]},mode='commitment')]};await s.observe(u,[])
            assert p.valid(u,source) and not p.valid(u,view)
            now[0]+=7201
            assert p.valid(u,source)
    asyncio.run(run())


def test_correction_and_expiry_invalidate_exact_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,old=await seed(s,msg,choice);c=s.people._canonical('20000')[0]
            text='我更正自己的安排，只整理白鹭清单。';u=msg('晨光归档：'+text)
            choice[0]={'items':[item(text,personal={'action':'update','event_id':c['id'],'supersedes':[0]},mode='commitment',revision='correction')]}
            await s.observe(u,[]);new=s.provenance.current_sources(u)
            assert not s.provenance.valid(u,old) and s.provenance.valid(u,new)
            now[0]=new['refs'][0]['expires']+1
            assert not s.provenance.valid(u,new)
    asyncio.run(run())


def test_legacy_and_already_contaminated_history_quarantined_without_deleting_archive(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,tag=await seed(s,msg,choice);c=s.people._canonical('20000')[0];scope=m.scope+':shared';session='session_qq_provenance.jsonl'
            legacy=s.provenance.legacy(m)
            rows=[{'role':'ai','content':'主动旧提醒，紫鹭清单明天18点截止','proactive':{'id':'old'}},
                {'role':'ai','content':'停止后被错误转述的旧提醒','qq_episode':{'refs':[]}},
                {'role':'ai','content':'仍有效的独立聊天','qq_event_dependencies':s.provenance.empty(m)}]
            for row in rows:row['time']=now[0]
            lines=['Saya: '+r['content'] for r in rows]
            b.store.save_conversation(scope,lines,session)
            s.command(msg('/events stop '+c['id']))
            kept,meta=b.context_salience.project(scope,session,lines,rows,query='聊聊近况')
            assert kept==['Saya: 仍有效的独立聊天']
            assert not s.provenance.valid(m,legacy)
            assert b.store.conversation(scope)[0]==lines
            s.command(msg('/events resume '+c['id']))
            assert s.provenance.valid(m,tag) and not s.provenance.valid(m,legacy)
            assert b.context_salience.project(scope,session,lines,rows)[0]==kept
    asyncio.run(run())


def test_multiple_source_reply_fails_whole_but_original_surviving_source_is_retained(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            a,ta=await seed(s,msg,choice);z,tz=await seed(s,msg,choice,'湖畔读书：我负责准备提纲。')
            combined=s.provenance.merge(a,ta,tz)
            c=next(c for c in s.people._canonical('20000') if c['id']==ta['refs'][0]['id'])
            s.command(msg('/events stop '+c['id']))
            assert not s.provenance.valid(z,combined) and s.provenance.valid(z,tz)
    asyncio.run(run())


def test_group_public_dependencies_reauthorize_without_private_owner_leak(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,tag=await seed(s,msg,choice,group=90000,dual=True)
            other=msg('紫鹭清单呢',user=30000,group=90000)
            assert tag['refs'] and all(x['kind']=='group' for x in tag['refs'])
            assert s.provenance.valid(other,tag)
            personal=s.people._canonical('20000')[0]
            s.command(msg('/events stop '+personal['id']))
            assert not s.provenance.valid(m,tag) and s.provenance.valid(other,tag)
            s.unified.mark_member('90000','30000','revoked')
            assert not s.provenance.valid(other,tag)
    asyncio.run(run())


def test_selected_history_dependencies_propagate_without_another_model_call(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            original,tag=await seed(s,msg,choice);m=msg('我现在想聊聊自己的感受')
            channel={'context_selector':lambda lines,rows:(lines,rows)}
            s.provenance.bind_turn(m,channel)
            rows=[{'role':'ai','content':'来源明确的旧回复','qq_event_dependencies':tag}]
            channel['context_selector'](['Saya: 来源明确的旧回复'],rows)
            assert m.event_provenance_response['refs']==tag['refs']
            assert len(prompts)==1
    asyncio.run(run())


def test_actual_responder_filters_archive_reload_and_stores_inherited_label(server,tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            original,tag=await seed(s,msg,choice);c=s.people._canonical('20000')[0]
            s.command(msg('/events stop '+c['id']));calls=[]
            async def generate(req,channel_turn):
                lines,rows=channel_turn['context_selector'](['Saya: 旧主动提醒紫鹭清单明天18点截止'],
                    [{'role':'ai','content':'旧主动提醒紫鹭清单明天18点截止','proactive':{'id':'old'}}])
                assert lines==[] and rows==[]
                assert channel_turn['qq_event_dependencies']['refs']==[]
                calls.append(channel_turn)
                async def stream():yield 'event: answer_complete\ndata: {"text":"我们先聊你现在的感受。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            m=msg('其实我觉得目标很多，想先理清自己的感受')
            answer=await responder(m,[],'session_qq_actual_provenance.jsonl')
            assert '现在的感受' in str(answer) and len(calls)==1
            assert s.provenance.valid(m,m.event_provenance_response)
    asyncio.run(run())


def test_known_new_source_can_repeat_forgotten_words_without_resurrecting_old_id(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            old,oldtag=await seed(s,msg,choice);old_id=oldtag['refs'][0]['id']
            s.command(msg('/events stop '+old_id))
            fresh,tag=await seed(s,msg,choice,'晨光归档新一轮：我负责紫鹭清单。')
            assert tag['refs'][0]['id']!=old_id and s.provenance.valid(fresh,tag)
            row={'role':'user','content':fresh.text,'time':now[0],'qq_event_dependencies':tag}
            scope=fresh.scope+':shared'
            lines,rows=b.context_salience.project(scope,'session_qq_repeat.jsonl',['Doctor: '+fresh.text],[row])
            projection=s.people.history_projection(fresh)
            import hashlib
            projection['blocked'].append((fresh.user_id,hashlib.sha256(('Doctor: '+fresh.text).encode()).hexdigest()))
            lines,rows=s.people.filter_history(fresh,lines,rows,projection)
            assert lines==['Doctor: '+fresh.text] and rows[0]['_qq_event_validated']
            assert s.people._canonical('20000')[0]['id'] in {old_id,tag['refs'][0]['id']}
    asyncio.run(run())


async def proactive_lab(path):
    from test_proactive_interaction import lab
    from saya_app.services.qq_bridge import normalize_event
    rt,b,kb,manager,session,now,calls=await lab(path)
    s=b.episodes;s.clock=lambda:now[0];b.context_salience.clock=lambda:now[0]
    b.context_salience.configure('20000',0,{'enabled':True});s.configure('20000',0,{'enabled':True})
    s.people.migrate('20000',s.people.migration_preview()['version'])
    s.unified.configure('20000',0,{'enabled':True,'topic_routing_enabled':True},s.unified.preview()['version'])
    text='晨光归档：我负责紫鹭清单。'
    m,_=normalize_event(event(text,70001,20000),b.settings);m.sent_at=now[0]
    m.context_snapshot=b.context_salience.snapshot(m.scope+':isolated')
    async def decide(*a,**kw):return {'items':[item(text,personal=create(),mode='commitment')]}
    s.decide=decide;await s.observe(m,[]);tag=s.provenance.current_sources(m)
    scope=m.scope+':isolated';sid='session_qq_fixture.jsonl'
    history=['Doctor: '+text,'Saya: 我记下这次紫鹭清单安排。']
    b.store.save_conversation(scope,history,sid)
    b.context_salience.record(scope,sid,history,[now[0],now[0]],b.context_salience.snapshot(scope),event_dependencies=tag,user_dependencies=tag)
    return rt,b,kb,manager,now,calls,m,tag


def test_actual_proactive_send_persists_dependencies_and_then_fades_from_history(tmp_path):
    async def run():
        rt,b,kb,manager,now,calls,m,tag=await proactive_lab(tmp_path)
        try:
            await rt.tick();await b.queue.join()
            assert len(calls)==1 and len(b.client.sent)==1
            scope=m.scope+':isolated';history,sid=b.store.conversation(scope)
            with b.store.connect() as db:
                labels=[json.loads(r[0]) for r in db.execute('select tag from qq_event_result_dependencies')]
            assert labels and labels[-1]['refs']==tag['refs']
            stop=replace(m,key=m.key+'-stop',text='/events stop '+tag['refs'][0]['id'])
            s=b.episodes;s.command(stop)
            assert not s.provenance.valid(m,labels[-1])
            assert b.context_salience.project(scope,sid,history)[0]==[]
            assert b.store.conversation(scope)[0]==history
        finally:await b.stop()
    asyncio.run(run())


def test_pending_proactive_cancelled_when_source_stopped_without_any_send(tmp_path):
    async def run():
        from saya_app.services.proactive import ProactivePolicy
        rt,b,kb,manager,now,calls,m,tag=await proactive_lab(tmp_path)
        try:
            job=await rt.prepare('qq',ProactivePolicy(**rt.state()['values']))
            assert job and rt.valid(job)
            rt.store.transition(job['id'],'ready','queued')
            b.episodes.command(replace(m,key=m.key+'-stop',text='/events stop '+tag['refs'][0]['id']))
            assert not rt.valid(rt.store.get(job['id']))
            queued=replace(m,key=m.bot_id+':proactive:'+job['id'],kind='proactive')
            await rt.deliver_qq(queued)
            assert not b.client.sent and rt.store.get(job['id'])['state']=='cancelled'
        finally:await b.stop()
    asyncio.run(run())


def test_shared_proactive_archive_carries_identical_label(tmp_path):
    async def run():
        from saya_app.services.qq_bridge import QQMessage
        rt,b,kb,manager,now,calls,m,tag=await proactive_lab(tmp_path)
        try:
            b.settings=b.settings.model_copy(update={'shared_memory':True})
            scope=m.scope+':shared';sid='session_qq_fixture.jsonl'
            lines=['Doctor: '+m.text,'Saya: 我记下了。']
            b.store.save_conversation(scope,lines,sid)
            b.context_salience.record(scope,sid,lines,[now[0],now[0]],b.context_salience.snapshot(scope),event_dependencies=tag,user_dependencies=tag)
            manager.save_message('user',m.text,session_id=sid,metadata={'qq_event_dependencies':tag})
            manager.save_message('ai','我记下了。',session_id=sid,metadata={'qq_event_dependencies':tag})
            await rt.tick();await b.queue.join()
            archive=manager.load_session_full(sid,make_current=False)
            assert archive[-1]['proactive'] and archive[-1]['qq_event_dependencies']['refs']==tag['refs']
            assert len(calls)==1
        finally:await b.stop()
    asyncio.run(run())


def test_user_and_delivered_bot_quotes_inherit_dependency_then_reject_stopped_source(tmp_path):
    async def run():
        from saya_app.services import qq_delivery
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            original,tag=await seed(s,msg,choice);channel={}
            s.provenance.bind_turn(original,channel)
            q=msg('解释引用');q.reply_to=original.message_id
            q.quote_context={'status':'ready','sender_id':original.user_id,'text':original.text}
            assert s.provenance.protect_quote(q)['refs']==tag['refs']
            result=qq_delivery.create(b.store,original,original.scope+':shared','派生说明',100)
            s.provenance.bind_result(original,result,tag)
            with b.store.connect() as db:
                db.execute("update qq_delivery_parts set state='sent',receipt='77777' where result_id=?",(result,))
            botq=msg('继续解释');botq.reply_to='77777'
            botq.quote_context={'status':'ready','sender_id':original.bot_id,'text':'派生说明'}
            assert s.provenance.protect_quote(botq)['refs']==tag['refs']
            s.command(msg('/events stop '+tag['refs'][0]['id']))
            assert s.provenance.protect_quote(q) is None and q.quote_context['status']=='unavailable'
            assert s.provenance.protect_quote(botq) is None and botq.quote_context['status']=='unavailable'
            assert not s.provenance.valid_result(original,result)
    asyncio.run(run())


def test_legacy_cached_final_cannot_bypass_forget_via_continuation(tmp_path):
    async def run():
        from saya_app.services import qq_delivery
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,tag=await seed(s,msg,choice)
            legacy=qq_delivery.create(b.store,m,m.scope+':shared','未标记的旧安排',100)
            receipt=qq_delivery.create(b.store,replace(m,key=m.key+'-control'),m.scope+':shared','主机操作回执',100,kind='control')
            assert s.provenance.valid_result(m,legacy)
            s.command(msg('/events stop '+tag['refs'][0]['id']))
            assert not s.provenance.valid_result(m,legacy) and s.provenance.valid_result(m,receipt)
    asyncio.run(run())


def test_generation_guard_rechecks_after_async_wait_without_model_review(tmp_path):
    async def run():
        from saya_app.services.qq_tasks import QQTaskGuard,TaskCancelled
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            original,tag=await seed(s,msg,choice);ready=asyncio.Event();release=asyncio.Event();sent=[]
            guard=QQTaskGuard(SimpleNamespace(checkpoint=lambda *a:None),'fixture',original.scope)
            async def generating():
                with guard.bind():
                    m=msg('想继续聊聊');channel={'context_selector':lambda lines,rows:(lines,rows)}
                    s.provenance.bind_turn(m,channel)
                    channel['context_selector'](['Saya: 来源内容'],[{'role':'ai','content':'来源内容','qq_event_dependencies':tag}])
                    guard.checkpoint();ready.set();await release.wait()
                    guard.checkpoint();sent.append('不得发送的旧安排')
            task=asyncio.create_task(generating());await ready.wait()
            s.command(msg('/events stop '+tag['refs'][0]['id']));release.set()
            with pytest.raises(TaskCancelled):await task
            assert not sent and len(prompts)==1
    asyncio.run(run())


def test_restart_backfill_is_idempotent_and_quarantines_legacy_transitive_replies(tmp_path):
    async def run():
        from saya_app.services.qq_event_provenance import QQEventProvenance
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,tag=await seed(s,msg,choice);s.command(msg('/events stop '+tag['refs'][0]['id']))
            with b.store.connect() as db:
                db.execute('delete from qq_event_provenance_barriers');db.execute('delete from qq_event_provenance_schema')
            p=QQEventProvenance(s);before=p.barriers(m)
            assert before['person:'+m.user_id]>0
            assert p.history_tag(m,{'role':'ai','content':'旧转述','qq_episode':{'refs':[]}}) is None
            again=QQEventProvenance(s)
            assert again.barriers(m)==before and again.valid(m,again.empty(m))
    asyncio.run(run())


def test_group_history_reauthorizes_public_labels_and_respects_requester_stop(tmp_path):
    async def run():
        from core_services import KnowledgeBase
        from saya_app.services.qq_group_context import QQGroupContext
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            kb=KnowledgeBase(str(tmp_path/'group-kb'));g=QQGroupContext(b,kb,clock=lambda:now[0]);b.group_context=g
            g.configure('20000','90000',{'context_enabled':True},0,confirmed=True)
            m,tag=await seed(s,msg,choice,group=90000,dual=True)
            snap=g.snapshot(m);s.provenance.bind_turn(m,{})
            g.record_turn(m,snap,'紫鹭清单由发言者负责')
            other=msg('继续讨论',user=30000,group=90000)
            assert len(g.snapshot(other)['history'])==2
            s.command(msg('/events stop '+s.people._canonical('20000')[0]['id']))
            assert g.snapshot(m)['history']==[]
            assert len(g.snapshot(other)['history'])==2
    asyncio.run(run())


def test_proactive_source_changes_at_binding_cancel_instead_of_sticking_in_sending(tmp_path):
    async def run():
        from saya_app.services.proactive import ProactivePolicy
        rt,b,kb,manager,now,calls,m,tag=await proactive_lab(tmp_path)
        try:
            job=await rt.prepare('qq',ProactivePolicy(**rt.state()['values']))
            assert job and rt.valid(job)
            rt.store.transition(job['id'],'ready','queued')
            original=b.episodes.provenance.bind_result
            def changed(*args):
                b.episodes.command(replace(m,key=m.key+'-stop',text='/events stop '+tag['refs'][0]['id']))
                return original(*args)
            b.episodes.provenance.bind_result=changed
            queued=replace(m,key=m.bot_id+':proactive:'+job['id'],kind='proactive')
            with pytest.raises(ValueError,match='event_result_dependencies_changed'):await rt.deliver_qq(queued)
            assert not b.client.sent and rt.store.get(job['id'])['state']=='cancelled'
        finally:await b.stop()
    asyncio.run(run())


def test_downgrade_without_provenance_reader_is_blocked(tmp_path):
    import sqlite3
    from saya_app.services.qq_rollback import check_memory_rollback
    root=tmp_path/'rollback';path=root/'knowledge_base/qq';path.mkdir(parents=True)
    with sqlite3.connect(path/'bridge.sqlite3') as db:
        db.execute('create table qq_event_provenance_schema(bot text primary key,version integer)')
        db.execute("insert into qq_event_provenance_schema values('10000',1)")
    gate=check_memory_rollback(root,{})
    assert 'qq_event_provenance_v1' in gate['missing'] and gate['status']=='blocked'
    assert check_memory_rollback(root,{'memory_compatibility':gate['required']})['status']=='compatible_declared'


def test_prompt_projection_excludes_host_labels_without_erasing_them_from_channel():
    from saya_app.services.qq_chat import context_turn_reference
    from saya_app.services.qq_context_links import history_data
    tag={'refs':[{'hash':'HOST_METADATA_CANARY'}]}
    group={'topic':'fixture','history':[{'speaker_id':'20000','role':'user','text':'我负责清单','at':1,
        'qq_event_dependencies':tag,'_qq_event_validated':True}],'facts':[]}
    link={'session_id':'session_fixture.jsonl','source_version':'v','linked':1,'message_range':[0,0],
        'source_time_range':[1,1],'turn_count':1,'turns':[{'role':'user','content':'阅读体验','time':1,'source_index':0,
        'qq_event_dependencies':tag,'_qq_event_validated':True}]}
    prompt=context_turn_reference({'group_context':group,'context_link':link})
    assert '我负责清单' in prompt and '20000' in prompt and '阅读体验' in history_data(link)
    assert all(x not in prompt for x in ['qq_event_dependencies','_qq_event_validated','HOST_METADATA_CANARY'])
    assert group['history'][0]['qq_event_dependencies']==tag and link['turns'][0]['qq_event_dependencies']==tag
