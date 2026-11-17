import asyncio,time
import pytest
from test_qq_memory_reference_dialogue import lab,approve


def fresh_reply(b,k,manager,q,scope,sid,tag,*,body=None,invalid=False):
    body=body or '新话题：晨鹭测试飞船需要备用燃料，以便在意外变轨时保留退路。'
    metadata={'qq_event_dependencies':({'version':-1} if invalid else tag),'memory_use_epoch':k.memory_use.snapshot(),'memory_history_floor':k.memory_use.history_floor()}
    lines=[]
    for role,text in [('user','新话题：晨鹭测试飞船为什么需要备用燃料？'),('ai',body)]:
        manager.save_message(role,text,session_id=sid,metadata=metadata)
        lines.append(('Doctor: ' if role=='user' else 'Saya: ')+text)
    history,_=b.store.conversation(scope);b.store.save_conversation(scope,history+lines,sid)
    b.context_salience.record(scope,sid,lines,[time.time()-2,time.time()-1],b.context_salience.snapshot(scope),event_dependencies=metadata['qq_event_dependencies'],user_dependencies=tag)
    return body


@pytest.mark.parametrize('save_text',['帮我把你刚刚阐述的那一层想法存一下','记住刚刚你阐述的那一层想法','记住你刚才阐述的那一层想法'])
def test_just_now_binds_latest_actual_reply_even_if_model_would_prefer_old_theme(tmp_path,save_text):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            latest=fresh_reply(b,k,manager,q,scope,sid,tag)
            async def wrong(*a,**kw):raise AssertionError('an unqualified just-now source must not be reranked by a model')
            s.decide=wrong;q.text=save_text
            preview=await m.references.resolve(q,sid)
            assert latest in str(preview) and '阅读可以从感兴趣' not in str(preview)
            d=approve(b,scope,sid,preview);assert '已确认并保存' in m.references.commit(q,d['payload']['plan'])
            assert latest in k.memories[0]['content']
    asyncio.run(run())


@pytest.mark.parametrize('case',['invalid','too_long'])
def test_unusable_latest_source_never_falls_back_to_an_older_reply(tmp_path,case):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            fresh_reply(b,k,manager,q,scope,sid,tag,invalid=case=='invalid',body='飞船需要备用燃料。'*300 if case=='too_long' else None)
            q.text='帮我把你刚刚阐述的那一层想法存一下';answer=await m.references.resolve(q,sid)
            assert '不会改用较早内容' in str(answer) and not k.memories
    asyncio.run(run())


def test_named_older_theme_requires_an_explicit_choice_instead_of_silent_substitution(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            latest=fresh_reply(b,k,manager,q,scope,sid,tag)
            async def older(*a,**kw):return {'mode':'reference','ids':['h2'],'ambiguous':False}
            s.decide=older;q.text='记住你刚才关于阅读的那个解释'
            listing=await m.references.resolve(q,sid)
            assert '最近一条' in str(listing) and '较早来源' in str(listing) and latest in str(listing)
            d=approve(b,scope,sid,listing,'选2');preview=m.references.select(q,d['payload'])
            assert '阅读可以从感兴趣' in str(preview) and latest not in str(preview) and not k.memories
    asyncio.run(run())


def test_real_bridge_after_topic_change_previews_latest_and_cancel_does_not_write(tmp_path):
    async def run():
        from test_qq_memory_candidate_quotes import responder,send
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            latest=fresh_reply(b,k,manager,q,scope,sid,tag);responder(b,m)
            await send(b,'帮我把你刚刚阐述的那一层想法存一下',501)
            assert any(latest in x[1] and '完整正文' in x[1] for x in b.client.sent)
            await send(b,'取消',502)
            assert not k.memories and '这次不执行' in b.client.sent[-1][1]
    asyncio.run(run())
