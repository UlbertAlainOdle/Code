"""Actual ordinary-web evidence selection and bounded prompt contracts."""
import asyncio
import json

import httpx
import pytest

from scripts.eval_qq_recall_r4 import seed
from saya_app.services.memory_v4 import MemoryItemV4, PromptCompiler
from saya_app.services.memory_v4.prompt_compiler import _estimate_tokens
from test_qq_chat_runtime import server
from r4_web_lab import web_lab
from saya_app.services.web_research.policy import should_recall
from saya_app.services.memory_recall_contract import recall_rules, explicit_self_choice, evidence_contract


@pytest.mark.parametrize('budget', [64, 256, 512, 800, 1200, 2400])
def test_small_budget_reports_actual_omissions_and_clipping_without_growing_the_prompt(budget):
    items=[MemoryItemV4(id='choice', canonical_text='青岚计划：我选择离线处理。'+'补充材料。'*140),
           MemoryItemV4(id='reason', canonical_text='青岚计划选择离线的理由：海上作业经常断网。')]
    result=PromptCompiler().compile('我为什么在青岚计划选择离线？',memory_items=items,token_budget=budget)
    memories=[block for block in result.blocks if block.kind=='memory']
    receipt=next((block for block in result.blocks if block.kind=='recall_selection'),None)
    selection=result.audit['selection']
    assert _estimate_tokens(result.prompt)<=budget
    assert selection['available_count']==2 and selection['included_count']==len(memories)
    assert set(selection['omitted_ids'])=={'memory:choice','memory:reason'}-{block.id for block in memories}
    assert all(identifier in {block.id for block in memories} for identifier in selection['clipped_ids'])
    if memories:
        assert receipt is not None
    if receipt:
        assert f'included={len(memories):04d}' in receipt.content
        assert f'omitted={len(selection["omitted_ids"]):04d}' in receipt.content
        assert f'clipped={len(selection["clipped_ids"]):04d}' in receipt.content
    assert '补充材料' not in json.dumps(result.audit,ensure_ascii=False)


def test_pre_fit_excerpt_cap_also_counts_as_clipping():
    item=MemoryItemV4(id='long', canonical_text='长记录。'*500)
    result=PromptCompiler().compile('我之前的记录',memory_items=[item],token_budget=8000)
    assert result.audit['selection']['clipped_ids']==['memory:long']
    assert next(block.content for block in result.blocks if block.kind=='recall_boundary')==recall_rules()


def test_unfinished_excerpt_is_omitted_instead_of_being_shown_as_a_complete_reason():
    item=MemoryItemV4(id='long',canonical_text='My reason is '+('unfinishedword '*400))
    result=PromptCompiler().compile('What was my reason?',memory_items=[item],token_budget=8000)
    assert result.audit['selection']['available_count']==1
    assert result.audit['selection']['included_count']==0
    assert result.audit['selection']['omitted_ids']==['memory:long']
    assert 'unfinishedword' not in result.prompt


def test_literal_focus_keeps_the_named_object_but_preserves_multi_object_questions(tmp_path):
    kb,ids=seed(tmp_path)
    focused=kb.query_memory_v4('我之前为什么在棠木计划选择木结构？',bypass_need_planner=True)
    assert {row['id'] for row in focused['items']}=={ids['no_reason']}
    both=kb.query_memory_v4('我之前在青岚计划和棠木计划选的方案是什么？',bypass_need_planner=True,max_items=10)
    assert {ids['choice'],ids['no_reason']}<={row['id'] for row in both['items']}
    kb.set_memory_use_policy(ids['no_reason'],True)
    forbidden=kb.query_memory_v4('我之前为什么在棠木计划选择木结构？',bypass_need_planner=True)
    assert ids['no_reason'] not in {row['id'] for row in forbidden['items']}


def test_self_choice_does_not_borrow_another_subject_after_own_sources_are_revoked(tmp_path):
    kb,ids=seed(tmp_path)
    kb.set_memory_use_policy(ids['choice'],True);kb.set_memory_use_policy(ids['reason'],True)
    result=kb.query_memory_v4('我之前在青岚计划选择离线的理由是什么？',bypass_need_planner=True)
    assert result['items']==[]
    other=kb.query_memory_v4('我想知道林舟之前在青岚计划为什么选联网？',bypass_need_planner=True)
    assert ids['other_person'] in {row['id'] for row in other['items']}


def test_first_person_meta_question_does_not_claim_the_other_persons_choice():
    assert explicit_self_choice('我当时在青岚计划选离线的理由是什么？')
    assert explicit_self_choice('Why did I choose offline processing?')
    assert not explicit_self_choice('我想知道林舟为什么选联网？')
    assert not explicit_self_choice('他说“我选离线”是什么意思？')


def test_unresolved_pronoun_object_is_not_reported_as_no_matching_reason():
    value=evidence_contract('回到刚才那个海上作业的计划，我为什么作了那种选择？',
        [{'id':'r','subject':'user','canonical_text':'青岚计划：因为海上作业经常断网。'}])
    assert value['reason_status']=='object_reference_unresolved'


def test_explicit_topic_switch_uses_current_evidence_then_return_can_read_stored_history(server,tmp_path):
    kb,ids=seed(tmp_path)
    captured=[]
    answers=['青岚计划的理由是海上作业经常断网。','棠木计划只有选择，缺少理由。','青岚计划的理由是海上作业经常断网。']
    def model(request):
        captured.append(json.loads(request.content))
        value={'choices':[{'delta':{'content':answers[len(captured)-1]},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(value,ensure_ascii=False)+'\n\ndata: [DONE]\n\n')
    async def run():
        with web_lab(server,tmp_path/'lab',kb) as lab:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
                await lab.turn(client,'我之前在青岚计划为什么选离线？')
                second=await lab.turn(client,'换一个话题：棠木计划，我当初选木结构有什么理由？')
                assert second['audit']['history_selection']=='explicit_grounded_topic_shift'
                assert '青岚计划' not in json.dumps(captured[1],ensure_ascii=False)
                third=await lab.turn(client,'回到刚才那个海上作业的计划，我为什么作了那种选择？')
                assert '海上作业经常断网' in json.dumps(captured[2],ensure_ascii=False)
                assert third['audit']['history_selection']=='resolved_return_reference'
                assert third['audit']['resolved_reference']['focus']=='青岚计划'
                assert len(lab.manager.load_session_full('session_web_r4.jsonl',make_current=False))==6
    asyncio.run(run())


def test_split_policy_uses_the_same_budget_and_clipped_reason_is_not_complete(tmp_path):
    from core_services import KnowledgeBase
    kb=KnowledgeBase(str(tmp_path/'knowledge_base'))
    item=kb.add_memory('短预算样例选择离线的理由：'+'尚需查看的详细材料。'*200, title='短预算理由')
    result=kb.build_memory_v4_context_with_audit('我之前选择离线的理由是什么？',token_budget=800,split_answer_policy=True)
    assert result['answer_policy']==recall_rules()
    assert 'Recall evidence boundary:' not in result['context']
    assert _estimate_tokens(result['context']+result['answer_policy'])<=800
    contract=result['audit']['evidence_contract']
    assert contract['reason_status']=='partially_visible_passages'
    assert contract['incomplete_reason_passage_ids']==[item['id']]


@pytest.mark.parametrize('question', [
    '我为什么在青岚计划选择离线处理？', 'Why did I choose offline processing?',
    '松涧计划我现在使用什么配色？', '我明年的云堤计划选定了什么接口？',
    '我当时在松涧计划用什么颜色？', '我那阵子在松涧计划用什么颜色？',
])
def test_auto_recall_includes_existing_personal_choice_and_dated_state(question):
    assert should_recall('auto',question)
    assert not should_recall('off',question)


@pytest.mark.parametrize('question', [
    '槐汀晚餐计划为什么选蒸锅？', '槐汀晚餐计划现在用什么，为什么？',
    '岑湾目录计划现在先做什么，原因是什么？',
    '砂汀纸面试验已经确认应该用粗纸了吗？',
    '珀汀温室计划夜间仍用什么标记？', '青岚计划使用哪种处理方式？',
])
def test_auto_recall_includes_named_record_questions_without_a_personal_pronoun(question):
    assert should_recall('auto',question)
    assert not should_recall('off',question)


@pytest.mark.parametrize('question', [
    '槐汀晚餐计划现在应该用什么？', '槐汀晚餐计划为什么要选蒸锅？',
    '槐汀晚餐计划建议怎么安排？', '槐汀晚餐计划现在用蒸锅。',
    '砂汀纸面试验已经确认应该用粗纸。',
    '他说“槐汀晚餐计划现在用什么”是什么意思？',
])
def test_named_record_gate_keeps_advice_statements_and_quotes_out(question):
    assert not should_recall('auto',question)


@pytest.mark.parametrize('question', [
    '一般离线处理为什么更适合弱网？', '我为什么会困？',
    '我为什么要选择离线处理？', 'Why should I choose offline processing?',
    '他说“我为什么选择离线处理”是什么意思？', '我现在为青岚计划应该选哪个方案？',
    '我为青岚计划选择离线处理。',
])
def test_new_personal_gate_does_not_turn_general_advice_quotes_or_statements_into_recall(question):
    assert not should_recall('auto',question)


def test_ordinary_adapter_distinguishes_matched_but_omitted_from_no_match(tmp_path):
    kb, ids=seed(tmp_path)
    result=kb.build_memory_v4_context_with_audit('我为什么在青岚计划选择离线？',token_budget=256)
    contract=result['audit']['evidence_contract']
    assert contract['selection']['available_count']>0
    assert contract['selection_count']==0
    assert contract['retrieval_status']=='matched_material_omitted_from_prompt'
    assert contract['selection_status']=='omitted_or_clipped'
    empty=kb.build_memory_v4_context_with_audit('我说过紫钙星穹协议的细节吗？',token_budget=1200)
    assert empty['audit']['evidence_contract']['retrieval_status']=='no_match_in_current_read'


@pytest.mark.parametrize('question', [
    '我为什么在青岚计划选择离线处理？', '青岚计划为什么选择离线处理？',
    '青岚计划使用哪种处理方式？',
])
def test_real_ordinary_web_generator_receives_receipt_and_keeps_recall_off(server,tmp_path,question):
    kb,ids=seed(tmp_path)
    captured=[]
    def model(request):
        captured.append(json.loads(request.content))
        value={'choices':[{'delta':{'content':'所选材料不足以说明理由。'},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(value,ensure_ascii=False)+'\n\ndata: [DONE]\n\n')
    async def run():
        with web_lab(server,tmp_path/'lab',kb) as lab:
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
                result=await lab.turn(client,question,budget=800)
                assert result['answer']=='所选材料不足以说明理由。'
                assert result['audit']['version']=='v4'
                text=json.dumps(captured[-1],ensure_ascii=False)
                assert 'Read selection:' in text and 'Never complete clipped words or reasons' in text
                assert 'No speculative reasons unless asked' in text
                assert 'Missing here does not mean never saved' in text
                policies=[message for message in captured[-1]['messages']
                          if message['role']=='system' and str(message.get('content','')).startswith('Recall evidence boundary:')]
                assert len(policies)==1
                assert text.count('Recall evidence boundary:')==1
                assert result['audit']['evidence_contract']['selection']['available_count']>0
                off=await lab.turn(client,question,session='session_web_off.jsonl',recall='off')
                assert off['audit']['selected_count']==0
                assert 'Read selection:' not in json.dumps(captured[-1],ensure_ascii=False)
    asyncio.run(run())
