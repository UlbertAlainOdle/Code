import asyncio,json,time
import pytest
from test_qq_memory_reference_dialogue import lab,approve
from saya_app.services.memory_origin import bind_origin,ingress
from saya_app.services import qq_delivery
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_responses import ActionProposal
from test_qq_delivery import event
from test_qq_task_scheduler import until


def candidate(s,k,m,q,*,long=False):
    s.provenance.bind_turn(q,{})
    with bind_origin(ingress('qq',subject=q.user_id,bot_id=q.bot_id,message_id=q.message_id)):
        return k.add_memory_candidate('用户的虚构验收偏好是逐段阅读纸质书。'*(30 if long else 1),title='阅读观点')


def publish(b,q,scope,text,*,kind='control',complete=True,target=1500):
    result=qq_delivery.create(b.store,q,scope,text,target,kind=kind)
    with b.store.connect() as db:
        rows=db.execute('select seq,text from qq_delivery_parts where result_id=? order by seq',(result,)).fetchall()
        for seq,_ in rows:
            if complete or seq==0:db.execute("update qq_delivery_parts set state='sent',receipt=? where result_id=? and seq=?",(str(700+seq),result,seq))
    return result,''.join(x[1] for x in rows if complete or x[0]==0)


def responder(b,m):
    async def unexpected(*a,**kw):raise AssertionError('must not generate another answer')
    r=QQChatResponder(b.settings,None,unexpected,lambda:'api',lambda:{'configured':False})
    r.memory_management=m;b.responder=r
    return r


async def send(b,text,mid):
    assert b.submit(event(text,mid))[0]=='queued'
    await asyncio.wait_for(b.queue.join(),5);await until(lambda:not b._task_workers)


def test_legacy_summary_requires_full_preview_and_revision_requires_another_preview(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            c=candidate(s,k,m,q,long=True);payload,summary=m.candidate_proposal(m.references.actor(q),c['id'])
            question='候选摘要：'+summary+'。要查看全文吗？'
            b.action_proposals.ask(scope,sid,'save_candidate',payload,question,b.controls()['revision'])
            b.action_proposals.delivered(scope,sid,question);responder(b,m)
            await send(b,'记住',301)
            assert not k.memories and c['content'] in ''.join(x[1] for x in b.client.sent)
            replacement='用户在虚构验收中喜欢先整理书签，再逐段阅读纸质书。'*12
            await send(b,'请改成：'+replacement,302)
            assert not k.memories and k._find_memory_candidate(c['id'])['content']==replacement
            assert replacement in ''.join(x[1] for x in b.client.sent)
            await send(b,'记住',303)
            assert len(k.memories)==1 and k.memories[0]['content']==replacement
    asyncio.run(run())


def test_memory_save_command_only_previews_complete_candidate(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            c=candidate(s,k,m,q,long=True);q.text='/memory-save '+c['id']
            preview=await responder(b,m)(q,[],sid)
            assert isinstance(preview,ActionProposal) and c['content'] in str(preview) and not k.memories
            d=approve(b,scope,sid,preview)
            assert '已确认并保存' in m.references.commit(q,d['payload']['plan'])
    asyncio.run(run())


def test_explicit_quote_selects_superseded_candidate_without_renewing_expiry(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            c=candidate(s,k,m,q);payload,_=m.candidate_proposal(m.references.actor(q),c['id'])
            question='这条是原候选摘要，要查看正文吗？'
            b.action_proposals.ask(scope,sid,'save_candidate',payload,question,b.controls()['revision'])
            _,sent=publish(b,q,scope,question);b.action_proposals.delivered(scope,sid,sent)
            with b.store.connect() as db:original=db.execute('select expires from qq_action_proposals order by rowid desc limit 1').fetchone()[0]
            b.action_proposals.supersede(scope,sid)
            q.text='记住';q.reply_to='700'
            preview=await m.references.resolve(q,sid)
            assert c['content'] in str(preview) and not k.memories
            d=approve(b,scope,sid,preview)
            assert d['payload']['plan']['expires']<=original
    asyncio.run(run())


@pytest.mark.parametrize('change',['partial','changed','expired','other_scope'])
def test_quote_of_invalid_candidate_does_not_choose_latest_candidate(tmp_path,change):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            c=candidate(s,k,m,q);payload,_=m.candidate_proposal(m.references.actor(q),c['id'])
            question='阅读观点作为候选，尚未保存。'*12
            b.action_proposals.ask(scope,sid,'save_candidate',payload,question,b.controls()['revision'])
            publish(b,q,scope,question,complete=change!='partial',target=100)
            if change=='changed':k.update_memory_candidate(c['id'],{'content':'另一条新内容'})
            elif change=='expired':
                with b.store.connect() as db:db.execute('update qq_action_proposals set expires=?',(time.time()-1,))
            elif change=='other_scope':
                with b.store.connect() as db:db.execute("update qq_deliveries set scope='10000:private:30000:shared'")
            q.text='记住';q.reply_to='700'
            answer=await m.references.resolve(q,sid)
            assert '尚未保存' in str(answer) and not k.memories and '完整正文' not in str(answer)
    asyncio.run(run())


def test_quoted_actual_bot_message_uses_host_receipt_and_archive_not_model(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            text=m.references.options(q,sid)[1]['text']
            result,_=publish(b,q,scope,text,kind='final');s.provenance.bind_result(q,result,tag)
            async def unexpected(*a,**kw):raise AssertionError('a verified quote needs no semantic selector')
            s.decide=unexpected;q.reply_to='700';q.text='记住'
            preview=await m.references.resolve(q,sid)
            assert text in str(preview) and '来源作者：助手' in str(preview) and not k.memories
    asyncio.run(run())


def test_stopped_quoted_bot_source_never_reenters_save(tmp_path):
    async def run():
        async with lab(tmp_path,tagged=True) as (b,s,k,m,manager,q,scope,sid,tag):
            text=m.references.options(q,sid)[1]['text']
            result,_=publish(b,q,scope,text,kind='final');s.provenance.bind_result(q,result,tag)
            q.text='/events stop '+tag['refs'][0]['id'];s.command(q)
            q.reply_to='700';q.text='记住';answer=await m.references.resolve(q,sid)
            assert '失效' in str(answer) and not k.memories
    asyncio.run(run())


def test_verified_own_quote_requires_original_label_and_exact_archive_text(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            s.provenance.bind_turn(q,{})
            q.reply_to=q.message_id;q.text='记住'
            q.quote_context={'status':'ready','sender_id':q.user_id,'text':'我想聊阅读体验','truncated':False}
            preview=await m.references.resolve(q,sid)
            assert '来源作者：用户' in str(preview) and not k.memories
            q.quote_context['text']='查不到的虚构原文'
            assert '尚未保存' in str(await m.references.resolve(q,sid)) and not k.memories
    asyncio.run(run())


def test_real_bridge_reselects_quoted_candidate_after_topic_change(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            c=candidate(s,k,m,q);payload,_=m.candidate_proposal(m.references.actor(q),c['id'])
            question='这是一个候选摘要，尚未保存。'
            b.action_proposals.ask(scope,sid,'save_candidate',payload,question,b.controls()['revision'])
            _,text=publish(b,q,scope,question);b.action_proposals.delivered(scope,sid,text)
            b.action_proposals.supersede(scope,sid);responder(b,m)
            raw=event('记住',400);raw['message'].insert(0,{'type':'reply','data':{'id':'700'}})
            assert b.submit(raw)[0]=='queued'
            await asyncio.wait_for(b.queue.join(),5);await until(lambda:not b._task_workers)
            assert not k.memories and any('完整正文' in x[1] and c['content'] in x[1] for x in b.client.sent)
            await send(b,'记住',401)
            assert len(k.memories)==1 and k.memories[0]['content']==c['content']
    asyncio.run(run())


def test_unresolved_new_quote_clears_previous_generic_confirmation_focus(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=await m.references.resolve(q,sid);b.action_proposals.delivered(scope,sid,str(preview))
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True,reply_to='unknown') is None
            q.reply_to='unknown';q.text='记住';answer=await m.references.resolve(q,sid)
            assert '尚未保存' in str(answer)
            assert b.action_proposals.reply(scope,sid,'好',b.controls()['revision'],True) is None and not k.memories
    asyncio.run(run())


@pytest.mark.parametrize('case',['long_url','legacy_truncation'])
def test_full_preview_transport_failure_does_not_raise_or_allow_confirmation(tmp_path,case):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            c=candidate(s,k,m,q,long=True)
            if case=='long_url':
                k.update_memory_candidate(c['id'],{'content':'虚构资料链接 https://example.invalid/'+'a'*3500})
            else:b.settings=b.settings.model_copy(update={'delivery_mode':'legacy','max_reply_chars':100})
            preview=m.references.offer_candidate(q,sid,c['id'])
            assert '超过当前完整预览' in str(preview) and not k.memories
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True) is None
    asyncio.run(run())


@pytest.mark.parametrize('terminal',['done','unknown'])
def test_new_explicit_quoted_target_is_not_blocked_by_an_old_terminal_proposal(tmp_path,terminal):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=await m.references.resolve(q,sid);d=approve(b,scope,sid,preview)
            b.action_proposals.finish(d['id'],terminal=='done')
            text=m.references.options(q,sid)[1]['text']
            result,_=publish(b,q,scope,text,kind='final');s.provenance.bind_result(q,result,tag)
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True,reply_to='700') is None
            q.reply_to='700';q.text='记住';new_preview=await m.references.resolve(q,sid)
            assert isinstance(new_preview,ActionProposal) and text in str(new_preview) and not k.memories
    asyncio.run(run())
