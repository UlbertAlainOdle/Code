import asyncio
from unittest.mock import patch
import pytest
from test_qq_memory_reference_dialogue import lab,approve
from saya_app.services.memory_reference import save_reference,unresolved_reference,contextual_save
from saya_app.services.memory_v4.write_pipeline import WriteIntentGate


@pytest.mark.parametrize('text',['记住你刚才阐述的那一层想法','帮我把上文谈到的那个结论存一下','麻烦你保存前面分析出来的那种思路','please remember that idea you mentioned earlier'])
@pytest.mark.parametrize('v4',[True,False])
def test_novel_contextual_forms_do_not_fall_through_to_literal_save(tmp_path,text,v4):
    from core_services import KnowledgeBase
    k=KnowledgeBase(str(tmp_path));k.settings['memory_v4_write_enabled']=v4
    assert contextual_save(text) and save_reference(text)
    assert WriteIntentGate().detect(text,{'intent':'explicit_save','content':'不能替换原请求'}).intent=='none'
    assert k.capture_explicit_memory(text) is None and not k.memories


def test_novel_reference_gets_real_source_body_not_a_model_rewrite(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='记住你刚才阐述的那一层想法'
            async def decide(*a,**kw):return {'mode':'reference','ids':['h2'],'ambiguous':False,'content':'模型提供的替代正文'}
            s.decide=decide;preview=await m.references.resolve(q,sid)
            assert '阅读可以从感兴趣' in str(preview) and '替代正文' not in str(preview) and not k.memories
            d=approve(b,scope,sid,preview);assert '已确认并保存' in m.references.commit(q,d['payload']['plan'])
            assert '那一层想法' not in k.memories[0]['content']
    asyncio.run(run())


def test_model_cannot_classify_a_bare_reference_into_a_literal_write(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='记住你刚才阐述的那一层想法'
            async def decide(*a,**kw):return {'mode':'self_contained','content':'假的完整事实'}
            s.decide=decide;answer=await m.references.resolve(q,sid)
            assert '尚未保存' in str(answer) and not k.memories
    asyncio.run(run())


@pytest.mark.parametrize('v4',[True,False])
def test_complete_assertion_with_context_words_saves_exact_request_body(tmp_path,v4):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            k.settings['memory_v4_write_enabled']=v4;q.text='记住你刚才推荐的书是《海风》。'
            async def decide(*a,**kw):raise AssertionError('complete literal body must not require model approval')
            s.decide=decide;answer=await m.references.resolve(q,sid)
            assert '已确认并保存' in str(answer) and len(k.memories)==1
            assert '你刚才推荐的书是《海风》' in k.memories[0]['content'] and '另一书名' not in k.memories[0]['content']
    asyncio.run(run())


def test_postcommit_receipt_failure_does_not_claim_no_save(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='记住你刚才推荐的书是《海风》。'
            async def decide(*a,**kw):return {'mode':'self_contained'}
            s.decide=decide
            with patch.object(m,'saved_receipt',side_effect=OSError('private error')):
                answer=await m.references.resolve(q,sid)
            assert len(k.memories)==1 and '已保存' in str(answer) and '尚未保存' not in str(answer)
    asyncio.run(run())


def test_independent_literal_title_and_known_full_fact_remain_valid():
    for text in ['小说标题叫你上面说的记忆','你刚才推荐的书是《海风》','我喝咖啡不加糖']:
        assert not unresolved_reference(text)
        assert WriteIntentGate().detect('记住：'+text).intent=='explicit_save'


def test_new_verb_still_binds_requested_source_author(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='帮我把我刚刚阐述的那一层想法存一下'
            async def decide(*a,**kw):return {'mode':'reference','ids':['h2'],'ambiguous':False}
            s.decide=decide
            assert '尚未保存' in str(await m.references.resolve(q,sid)) and not k.memories
    asyncio.run(run())
