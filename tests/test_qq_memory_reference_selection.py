import asyncio,json,time
from unittest.mock import patch
import pytest
from test_qq_memory_reference_dialogue import lab,approve
from saya_app.services.memory_reference import save_reference
from saya_app.services.memory_v4.write_pipeline import WriteIntentGate
from saya_app.services.qq_action_proposals import QQActionProposals
from saya_app.services.qq_memory_reference_selection import selection_request


@pytest.mark.parametrize('text',['把上面两条都记住','记住上面两条','只记住你刚才的解释中的“需要耐心”','记住第5条'])
def test_contextual_set_or_fragment_never_uses_literal_fallback(text):
    assert save_reference(text)
    assert WriteIntentGate().detect(text,{'intent':'explicit_save','content':'不能替换指代'}).intent=='none'


@pytest.mark.parametrize('text,indices',[('选1、2',[0,1]),('排除第2条',[0,2]),('除了第2条，其他都保存',[0,2]),('只保存第2条',[1]),('全选',[0,1,2])])
def test_selection_refers_only_to_displayed_numbers(text,indices):
    assert selection_request(text,3)['indices']==indices
    assert selection_request('选1、1',3)=={'invalid':True}
    assert selection_request('排除1、2、3',3)=={'invalid':True}


@pytest.mark.parametrize('v4',[False,True])
def test_delivered_set_preview_saves_each_body_once_and_records_results(tmp_path,v4):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            k.settings['memory_v4_write_enabled']=v4
            options=m.references.options(q,sid);listing=m.references.sets.list(q,sid,options)
            assert b.action_proposals.reply(scope,sid,'都保存',b.controls()['revision'],True) is None
            decision=approve(b,scope,sid,listing,'选1、2');preview=m.references.select(q,decision['payload'])
            assert not k.memories and '分别保存' in str(preview)
            decision=approve(b,scope,sid,preview)
            result=m.references.sets.commit(q,decision['id'],decision['payload'])
            assert '已确认并保存全部2条' in result and len(k.memories)==2
            assert {x['content'] for x in k.memories}=={p['content'] for p in decision['payload']['plans']}
            QQActionProposals(b.store)
            assert m.references.sets.commit(q,decision['id'],decision['payload'])==result and len(k.memories)==2
    asyncio.run(run())


def test_excluding_from_batch_requires_new_delivered_preview_and_cancel_writes_nothing(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=m.references.sets.preview(q,sid,m.references.options(q,sid))
            d=approve(b,scope,sid,preview,'排除第2条');assert d['kind']=='select_reference'
            revised=m.references.select(q,d['payload'])
            assert '我想聊阅读体验' in str(revised) and '阅读可以从感兴趣' not in str(revised)
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True) is None
            assert approve(b,scope,sid,revised,'取消')['status']=='rejected' and not k.memories
    asyncio.run(run())


def test_fragment_is_exact_host_source_and_preserves_author(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            async def unexpected(*a,**kw):raise AssertionError('exact literal fragment needs no model')
            s.decide=unexpected;q.text='只记住你刚才的解释中的“先不急着规定数量”'
            preview=await m.references.resolve(q,sid)
            assert '原文片段' in str(preview) and '助手' in str(preview) and '阅读可以从感兴趣' not in str(preview)
            d=approve(b,scope,sid,preview);assert d['payload']['plan']['fragment'] is True
            assert '已确认并保存' in m.references.commit(q,d['payload']['plan'])
            assert '先不急着规定数量' in k.memories[0]['content']
            q.text='只记住你刚才的解释中的“不存在的原文”'
            assert '尚未保存' in str(await m.references.resolve(q,sid)) and len(k.memories)==1
    asyncio.run(run())


def test_fragment_from_numbered_list_never_chooses_another_source(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            listing=m.references.sets.list(q,sid,m.references.options(q,sid))
            d=approve(b,scope,sid,listing,'只保存第2条中的“先不急着规定数量”')
            preview=m.references.select(q,d['payload']);assert '原文片段' in str(preview) and not k.memories
            d=approve(b,scope,sid,preview);assert d['kind']=='save_reference'
            q.text='只保存第2条中的“先不急着规定数量”'
            b.action_proposals.supersede(scope,sid)
            assert '没有已送达' in str(await m.references.resolve(q,sid)) and not k.memories
    asyncio.run(run())


def test_changed_source_preflight_stops_whole_batch_without_writes(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=m.references.sets.preview(q,sid,m.references.options(q,sid));d=approve(b,scope,sid,preview)
            d['payload']['plans'][1]['sources'][0]['expires']=time.time()-1
            # Keep the host proposal identical while simulating an original source expiring.
            with b.store.connect() as db:db.execute('update qq_action_proposals set payload=? where id=?',(json.dumps(d['payload']),d['id']))
            result=m.references.sets.commit(q,d['id'],d['payload'])
            assert '本次没有保存' in result and not k.memories
    asyncio.run(run())


def test_partial_commit_is_reported_and_never_replayed(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=m.references.sets.preview(q,sid,m.references.options(q,sid));d=approve(b,scope,sid,preview)
            actual=m.references._write_reference;calls=[]
            def fail_second(*args):
                calls.append(1)
                if len(calls)==2:raise OSError('private error content must not be displayed')
                return actual(*args)
            with patch.object(m.references,'_write_reference',fail_second):
                result=m.references.sets.commit(q,d['id'],d['payload'])
                assert '已确认保存1/2条' in result and '结果待核对' in result and 'private error' not in result
                assert m.references.sets.commit(q,d['id'],d['payload'])==result and len(calls)==2 and len(k.memories)==1
    asyncio.run(run())


def test_candidate_fragment_does_not_confirm_or_save_the_whole_candidate(tmp_path):
    async def run():
        from test_qq_memory_candidate_quotes import candidate
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            c=candidate(s,k,m,q)
            option=m.references.candidate_option(q,sid,c['id']);option=m.references.sets.fragment(option,'逐段阅读纸质书')
            preview=m.references.preview(q,sid,option);d=approve(b,scope,sid,preview)
            assert '已确认并保存' in m.references.commit(q,d['payload']['plan'])
            assert k._find_memory_candidate(c['id'])['status']=='pending'
            assert len(k.memories)==1 and k.memories[0]['content']!=c['content'] and '原文片段' in k.memories[0]['content']
    asyncio.run(run())


def test_explicit_two_targets_cannot_silently_shrink_to_one(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='把上面两条都记住'
            async def decide(*a,**kw):return {'ids':['h2'],'ambiguous':False}
            s.decide=decide
            assert '未定位到完整' in str(await m.references.resolve(q,sid)) and not k.memories
    asyncio.run(run())


def test_real_bridge_full_set_exclusion_and_confirmation_use_exact_remaining_scope(tmp_path):
    async def run():
        from test_qq_memory_candidate_quotes import responder,send
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            listing=m.references.sets.list(q,sid,m.references.options(q,sid))
            b.action_proposals.delivered(scope,sid,str(listing));responder(b,m)
            await send(b,'都保存',301)
            assert any('批量完整预览' in x[1] for x in b.client.sent) and not k.memories
            await send(b,'排除第2条',302)
            assert not k.memories
            await send(b,'记住',303)
            assert len(k.memories)==1 and '我想聊阅读体验' in k.memories[0]['content']
    asyncio.run(run())


def test_new_selection_metadata_requires_compatible_reader(tmp_path):
    import sqlite3
    from saya_app.services.qq_rollback import check_memory_rollback
    path=tmp_path/'knowledge_base/qq/bridge.sqlite3';path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('create table qq_action_proposals(kind text,state text,payload text)')
        db.execute('insert into qq_action_proposals values(?,?,?)',('save_reference','prepared',json.dumps({'plan':{'fragment':True}})))
    assert 'qq_memory_reference_selection_v1' in check_memory_rollback(tmp_path,{})['missing']


def test_partial_set_delivery_and_expired_selection_cannot_authorize(tmp_path):
    async def run():
        async with lab(tmp_path,long=True) as (b,s,k,m,manager,q,scope,sid,tag):
            preview=m.references.sets.preview(q,sid,m.references.options(q,sid))
            b.action_proposals.delivered(scope,sid,str(preview)[:300])
            assert b.action_proposals.reply(scope,sid,'记住',b.controls()['revision'],True) is None and not k.memories
            listing=m.references.sets.list(q,sid,m.references.options(q,sid));b.action_proposals.delivered(scope,sid,str(listing))
            with b.store.connect() as db:db.execute("update qq_action_proposals set expires=? where state='pending'",(time.time()-1,))
            assert b.action_proposals.reply(scope,sid,'选1、2',b.controls()['revision'],True)['status']=='expired'
            assert not k.memories
    asyncio.run(run())
