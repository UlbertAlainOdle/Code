import asyncio,json,time
from contextlib import asynccontextmanager
from unittest.mock import patch
import pytest
from core_services import KnowledgeBase,MemoryManager
from test_qq_topic_routing import topics
from test_qq_event_provenance import seed
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.qq_action_proposals import QQActionProposals

@asynccontextmanager
async def lab(path,*,tagged=False,long=False):
    async with topics(path) as (b,s,now,msg,choice,prompts):
        kb=KnowledgeBase(str(path/'kb'));m=QQMemoryManagement(b,kb);b.memory_management=m
        manager=MemoryManager(str(path/'history'));m.history_manager=manager
        b.action_proposals=QQActionProposals(b.store)
        request=msg('记住你刚才的解释');scope=request.scope+':shared';sid='session_qq_reference_dialogue.jsonl'
        b.store.save_conversation(scope,[],sid)
        tag=s.provenance.empty(request)
        if tagged:
            _,tag=await seed(s,msg,choice)
        text='阅读可以从感兴趣的小段开始，先不急着规定数量。'
        if long:text='阅读的感受需要耐心。'*170
        lines=[]
        for role,content in [('user','我想聊阅读体验'),('ai',text)]:
            manager.save_message(role,content,session_id=sid,metadata={'qq_event_dependencies':tag,
                'memory_use_epoch':kb.memory_use.snapshot(),'memory_history_floor':kb.memory_use.history_floor()})
            lines.append(('Doctor: ' if role=='user' else 'Saya: ')+content)
        b.store.save_conversation(scope,lines,sid)
        b.context_salience.record(scope,sid,lines,[time.time()-20,time.time()-10],b.context_salience.snapshot(scope),event_dependencies=tag,user_dependencies=tag)
        async def decide(*a,**kw):return {'ids':['h2'],'ambiguous':False}
        s.decide=decide
        yield b,s,kb,m,manager,request,scope,sid,tag

def approve(b,scope,sid,preview,text='记住'):
    b.action_proposals.delivered(scope,sid,str(preview))
    return b.action_proposals.reply(scope,sid,text,b.controls()['revision'],True)

def test_full_source_preview_then_commit_is_exact_and_attributed(tmp_path):
    async def run():
        async with lab(tmp_path,long=True) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=await m.references.resolve(q,sid)
            assert '完整正文' in str(preview) and '助手' in str(preview) and not k.memories
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True) is None
            d=approve(b,scope,sid,preview);assert d['kind']=='save_reference'
            result=m.references.commit(q,d['payload']['plan'])
            assert '已确认并保存' in result and len(k.memories)==1
            assert k.memories[0]['content']==d['payload']['plan']['content']
            assert '助手' in k.memories[0]['content'] and len(k.memories[0]['content'])>160
    asyncio.run(run())

def test_ambiguous_choice_requires_selection_and_second_delivered_preview(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='记住上面那段'
            async def decide(*a,**kw):return {'ids':['h1','h2'],'ambiguous':True}
            s.decide=decide;options=await m.references.resolve(q,sid)
            assert '1.' in str(options) and '2.' in str(options)
            d=approve(b,scope,sid,options,'选2');assert d['kind']=='select_reference'
            preview=m.references.select(q,d['payload']);assert not k.memories
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True) is None
            assert approve(b,scope,sid,preview)['kind']=='save_reference'
    asyncio.run(run())

def test_generic_yes_does_not_choose_and_unrelated_quote_does_not_execute(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='记住上面那段'
            async def decide(*a,**kw):return {'ids':['h1','h2'],'ambiguous':True}
            s.decide=decide;preview=await m.references.resolve(q,sid)
            assert approve(b,scope,sid,preview,'好')['status']=='selection_required'
            assert b.action_proposals.reply(scope,sid,'选1',b.controls()['revision'],True,reply_to='unknown')['status']=='unrelated_reply'
            assert not k.memories
    asyncio.run(run())

def test_explicit_candidate_uses_complete_versioned_text_and_existing_confirmation(tmp_path):
    async def run():
        from saya_app.services.memory_origin import bind_origin,ingress
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            s.provenance.bind_turn(q,{})
            with bind_origin(ingress('qq',subject=q.user_id,bot_id=q.bot_id,message_id=q.message_id,session_id=sid)):
                candidate=k.add_memory_candidate('用户认为阅读需要耐心。'*25,title='阅读观点')
            payload,_=m.candidate_proposal(m.references.actor(q),candidate['id'])
            b.action_proposals.ask(scope,sid,'save_candidate',payload,'保存候选吗？',b.controls()['revision'])
            b.action_proposals.delivered(scope,sid,'保存候选吗？')
            q.text='记住刚才那条候选';preview=await m.references.resolve(q,sid)
            assert candidate['content'] in str(preview) and '（摘要）' not in str(preview)
            d=approve(b,scope,sid,preview)
            result=m.references.commit(q,d['payload']['plan'])
            assert '已确认并保存' in result and k._find_memory_candidate(candidate['id'])['status']=='confirmed'
    asyncio.run(run())

@pytest.mark.parametrize('change',['source','session','expired','epoch'])
def test_source_and_conversation_change_refuse_without_write(tmp_path,change):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=await m.references.resolve(q,sid);d=approve(b,scope,sid,preview);plan=d['payload']['plan']
            if change=='source':
                path=manager._safe_session_path(sid)
                from pathlib import Path
                Path(path).write_text('',encoding='utf-8')
            elif change=='session':b.store.save_conversation(scope,[],'session_qq_other.jsonl')
            elif change=='expired':plan['expires']=time.time()-1
            else:plan['epoch']='changed'
            assert '没有保存' in m.references.commit(q,plan) and not k.memories
    asyncio.run(run())

def test_forgotten_source_never_reenters_reference_save(tmp_path):
    async def run():
        async with lab(tmp_path,tagged=True) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=await m.references.resolve(q,sid);d=approve(b,scope,sid,preview)
            q.text='/events stop '+tag['refs'][0]['id'];s.command(q)
            assert '没有保存' in m.references.commit(q,d['payload']['plan']) and not k.memories
    asyncio.run(run())

def test_invalid_model_output_cannot_supply_its_own_content(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            async def decide(*a,**kw):return {'ids':['invented'],'ambiguous':False,'content':'伪造正文'}
            s.decide=decide;answer=await m.references.resolve(q,sid)
            assert '尚未保存' in str(answer) and '伪造' not in str(answer) and not k.memories
    asyncio.run(run())

def test_partial_delivery_and_cancel_do_not_commit(tmp_path):
    async def run():
        async with lab(tmp_path,long=True) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=await m.references.resolve(q,sid)
            b.action_proposals.delivered(scope,sid,str(preview)[:150])
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True) is None
            assert approve(b,scope,sid,preview,'取消')['status']=='rejected' and not k.memories
    asyncio.run(run())


def test_multipart_preview_must_fully_arrive_and_quote_can_bind_any_sent_part(tmp_path):
    async def run():
        from saya_app.services import qq_delivery
        async with lab(tmp_path,long=True) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=await m.references.resolve(q,sid)
            result=qq_delivery.create(b.store,q,scope,preview,1500,kind='control')
            with b.store.connect() as db:
                parts=db.execute('select seq,text from qq_delivery_parts where result_id=? order by seq',(result,)).fetchall()
                assert len(parts)>1
                db.execute("update qq_delivery_parts set state='sent',receipt='part0' where result_id=? and seq=0",(result,))
            b.action_proposals.delivered(scope,sid,parts[0][1])
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True,reply_to='part0') is None
            with b.store.connect() as db:
                db.execute("update qq_delivery_parts set state='sent',receipt='part1' where result_id=? and seq>0",(result,))
            b.action_proposals.delivered(scope,sid,''.join(p[1] for p in parts))
            decision=b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True,reply_to='part0')
            assert decision['kind']=='save_reference' and decision['status']=='approved' and not k.memories
    asyncio.run(run())


def test_selector_cannot_change_author_and_readonly_mode_does_not_fail_chat(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            async def decide(*a,**kw):return {'ids':['h1'],'ambiguous':False}
            s.decide=decide
            assert '尚未保存' in str(await m.references.resolve(q,sid)) and not k.memories
            b.settings=b.settings.model_copy(update={'memory_write_enabled':False})
            assert '权限' in str(await m.references.resolve(q,sid)) and not k.memories
    asyncio.run(run())


def test_real_bridge_delivers_preview_then_commits_without_generating_another_answer(tmp_path):
    async def run():
        from saya_app.services.qq_chat import QQChatResponder
        from test_qq_delivery import event
        from test_qq_task_scheduler import until
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            async def unexpected(*a,**kw):raise AssertionError('reference action must bypass ordinary answer generation')
            responder=QQChatResponder(b.settings,None,unexpected,lambda:'api',lambda:{'configured':False})
            responder.memory_management=m;b.responder=responder
            assert b.submit(event('记住你刚才的解释',301))[0]=='queued'
            await asyncio.wait_for(b.queue.join(),5);await until(lambda:not b._task_workers)
            assert any('完整正文' in sent[1] for sent in b.client.sent) and not k.memories
            assert b.submit(event('记住',302))[0]=='queued'
            await asyncio.wait_for(b.queue.join(),5);await until(lambda:not b._task_workers)
            assert len(k.memories)==1 and '助手' in k.memories[0]['content']
            assert any('已确认并保存' in sent[1] for sent in b.client.sent)
    asyncio.run(run())


def test_candidate_origin_is_required_and_cannot_be_removed_after_preview(tmp_path):
    async def run():
        from saya_app.services.memory_origin import bind_origin,ingress
        from saya_app.services.qq_memory_access import MemoryAccessDenied
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            s.provenance.bind_turn(q,{})
            with bind_origin(ingress('qq',subject=q.user_id,bot_id=q.bot_id,message_id=q.message_id,session_id=sid)):
                candidate=k.add_memory_candidate('用户喜欢逐段阅读纸质书。',title='阅读观点')
            payload,_=m.candidate_proposal(m.references.actor(q),candidate['id'])
            b.action_proposals.ask(scope,sid,'save_candidate',payload,'保存候选吗？',b.controls()['revision'])
            b.action_proposals.delivered(scope,sid,'保存候选吗？')
            q.text='记住刚才那条候选';preview=await m.references.resolve(q,sid)
            decision=approve(b,scope,sid,preview)
            with b.store.connect() as db:db.execute('delete from qq_event_message_dependencies')
            assert '没有保存' in m.references.commit(q,decision['payload']['plan'])
            with pytest.raises(MemoryAccessDenied):m.confirm_candidate(m.references.actor(q),candidate['id'],payload['candidate_version'],sid)
            assert not k.memories and k._find_memory_candidate(candidate['id'])['status']=='pending'
    asyncio.run(run())
