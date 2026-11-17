import asyncio,time
import pytest
from saya_app.services.memory_reference import explicit_save_body,save_reference,source_author
from test_qq_memory_reference_selection import lab,approve

REQUEST='请把我刚才说的两条虚构验收规则，分别保存成两条长期记忆。'


@pytest.mark.parametrize('text',[REQUEST,'请将我刚才说的两条规则分别存入长期记忆。',
    '把我上面提到的两条规则各自写入记忆条目。'])
def test_object_before_storage_purpose_is_a_source_set_not_literal_fact(text):
    body=explicit_save_body(text)
    assert body and body.startswith('我') and '长期记忆' not in body
    assert save_reference(text)=='set' and source_author(text)=='用户'


@pytest.mark.parametrize('text',['我正在讨论如何把两条规则分别保存成两条长期记忆。',
    '请不要把我上面两条规则保存成长期记忆。','请把我上面两条规则不要保存成长期记忆。',
    '能否解释“分别保存成两条长期记忆”的含义？'])
def test_description_question_or_negation_does_not_grant_save(text):
    assert explicit_save_body(text) is None and save_reference(text) is None


def test_real_responder_routes_actual_failed_sentence_to_complete_preview_before_write(tmp_path):
    from test_qq_memory_candidate_quotes import responder,send
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            manager.save_message('user','第二条虚构来源：霜桥标本柜固定每周三校对编号。',session_id=sid,
                metadata={'qq_event_dependencies':tag,'memory_use_epoch':k.memory_use.snapshot()})
            lines=b.store.conversation(scope)[0]+['Doctor: 第二条虚构来源：霜桥标本柜固定每周三校对编号。']
            b.store.save_conversation(scope,lines,sid)
            b.context_salience.record(scope,sid,lines,[time.time()-3,time.time()-2,time.time()-1],
                b.context_salience.snapshot(scope),event_dependencies=tag,user_dependencies=tag)
            responder(b,m)
            async def decide(*a,**kw):return {'mode':'reference','ids':['h1','h3'],'ambiguous':False}
            s.decide=decide
            await send(b,REQUEST,720)
            assert not k.memories
            preview=b.client.sent[-1][1]
            assert '批量完整预览' in preview and '共2条' in preview and REQUEST not in preview
    asyncio.run(run())


def test_explicit_count_cannot_shrink_to_one_model_selected_source(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text=REQUEST
            async def decide(*a,**kw):return {'mode':'reference','ids':['h1'],'ambiguous':False}
            s.decide=decide
            reply=await m.references.resolve(q,sid)
            assert '要求2条' in reply and not k.memories
    asyncio.run(run())
