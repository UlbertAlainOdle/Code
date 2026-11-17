"""R4 evidence-set contracts, using isolated stores and the real chat adapter."""
import asyncio
from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from core_services import KnowledgeBase, MemoryManager
from scripts.eval_qq_recall_r4 import evaluate, seed
from saya_app.services.memory_recall_contract import explicit_future_window, evidence_contract
from saya_app.services.memory_v4 import MemoryItemV4, PromptCompiler
from saya_app.services.memory_v4.model_adapters import _tool_policy_lines
from test_qq_chat_runtime import server


def test_fixed_information_sets_use_real_retrieval_without_borrowing_forbidden_sources():
    result = evaluate()
    assert result['cases'] == result['passed'] == 16, result['cases_detail']
    assert result['corpus_sha256'] == 'a52b3e24a9b8d2de3181fe94a749e6aeaeea92f419b37c35479a7b92eb13b830'
    assert not result['real_QQ_acceptance'] and not result['real_model_answer_quality']


@pytest.mark.parametrize('text, expected', [
    ('我明年的方案是什么？', 2027), ('我后年的方案是什么？', 2028),
    ('my plan next year', 2027), ('我在2029年的方案是什么？', 2029),
    ('我以前的方案是什么？', None), ('不是明年的方案', None),
    ('解释“明年”这个词', None), ('明年和后年的方案', None),
    ('我在2025年的方案', None), ('别查明年的方案', None),
])
def test_future_year_is_explicit_and_ambiguous_or_quoted_time_is_not_inferred(text, expected):
    value = explicit_future_window(text, datetime(2026, 11, 6))
    assert value == ({} if expected is None else {'time_from': f'{expected}-01-01', 'time_to': f'{expected}-12-31'})


def test_future_read_keeps_explicit_caller_filters_and_does_not_change_default_eligibility(tmp_path):
    kb, ids = seed(tmp_path)
    before = kb.memory_v4_store.export_state()
    assert ids['future'] not in {x['id'] for x in kb.query_memory_v4('云堤计划', bypass_need_planner=True)['items']}
    assert ids['future'] in {x['id'] for x in kb.query_memory_v4('我明年的云堤计划', bypass_need_planner=True)['items']}
    result = kb.query_memory_v4('我明年的云堤计划', bypass_need_planner=True, time_to='2026-12-31')
    assert ids['future'] not in {x['id'] for x in result['items']}
    assert result['query']['time_from'] is None and result['query']['time_to'].startswith('2026-12-31')
    assert kb.memory_v4_store.export_state() == before


def test_host_reason_question_survives_topic_seed_and_model_refinement(tmp_path):
    kb, ids = seed(tmp_path)
    handler = kb._memory_v4_capability_registry(allow_legacy_memory=True, memory_query_options={
        'question_text': '我为什么给棠木计划选木结构？', 'topic_seed': '棠木计划', 'max_items': 3,
    }).get('memory.query').handler
    for query in ['最近', '棠木计划']:
        result = handler({'query': query, 'question_text': '为什么选择联网？'})
        assert ids['no_reason'] in {x['id'] for x in result['items']}
        assert result['evidence_contract']['question']['asks_reason']
        assert result['evidence_contract']['reason_status'] == 'not_found_in_selected_material'
        assert 'never assert Doctor never said it' in result['recall_rules']


def test_another_subject_or_another_object_is_not_a_matching_personal_reason():
    rows = [{'id':'other_person', 'subject':'person:lin', 'canonical_text':'青岚计划，因为他办公室网络稳定。'},
            {'id':'other_object', 'subject':'user', 'canonical_text':'银湾计划，因为办公室网络稳定。'},
            {'id':'choice', 'subject':'user', 'canonical_text':'青岚计划：我选择离线处理。'}]
    value = evidence_contract('我为什么在青岚计划选择离线处理？', rows)
    assert value['reason_status'] == 'not_found_in_selected_material' and not value['reason_passage_ids']


def test_reason_paraphrase_preserves_evidence_without_turning_other_topics_into_a_comparison():
    rows=[{'id':'reason','subject':'user','canonical_text':'青岚计划：因为海上经常断网。'}]
    value=evidence_contract('那次我把青岚计划定成离线，主要考虑了什么？', rows)
    assert value['reason_status']=='explicit_passages' and value['reason_passage_ids']==['reason']


def test_trusted_policy_survives_provider_tool_aliasing_and_excludes_absent_capabilities():
    policy='\n'.join(_tool_policy_lines([{'name':'memory_query_12345678','native_name':'memory.query'}]))
    assert 'Call memory_query_12345678 only when' in policy
    assert 'Do not list other people or topics' in policy
    assert 'does not prove no later updates exist' in policy
    assert 'memory.query' not in policy and 'web.search' not in policy
    assert 'Recall evidence boundary' not in '\n'.join(_tool_policy_lines([]))


def test_missing_database_is_not_reported_as_a_successful_empty_read(tmp_path):
    kb = KnowledgeBase(str(tmp_path/'knowledge_base'))
    kb.settings['memory_v4_read_enabled'] = True
    kb.memory_v4_store = None
    with patch.object(kb, '_open_memory_v4_store_for_read', return_value=None):
        registry = kb._memory_v4_capability_registry()
        for name, args in [('memory.query', {'query':'我之前的方案'}), ('memory.read', {'ids':['missing']})]:
            result = registry.get(name).handler(args)
            assert result['evidence_contract']['retrieval_status'] == 'read_unavailable'


def test_tool_summary_marks_truncation_and_full_read_can_supply_the_missing_reason(tmp_path):
    kb = KnowledgeBase(str(tmp_path/'knowledge_base'))
    kb.update_settings({'memory_v4_read_enabled': True,'memory_v4_write_enabled': True})
    text = '虚构的山林计划：我选择离线。' + '补充材料。'*60 + '因为离线可用。'
    item = kb.add_memory(text, title='山林计划')
    registry = kb._memory_v4_capability_registry(memory_query_options={'question_text':'我为什么选择山林计划？'})
    query = registry.get('memory.query').handler({'query':'山林计划'})
    row = next(row for row in query['items'] if row['id'] == item['id'])
    assert row['text_truncated'] and len(row['canonical_text']) == 240
    assert query['evidence_contract']['reason_status'] == 'not_found_in_selected_material'
    full = registry.get('memory.read').handler({'ids':[item['id']]})
    assert full['evidence_contract']['reason_status'] == 'explicit_passages'
    assert full['items'][0]['canonical_text'] == text


@pytest.mark.parametrize('native', [True, False])
def test_tool_query_and_full_read_preserve_metadata_without_inventing_legacy_authority(tmp_path, native):
    kb, ids = seed(tmp_path)
    if not native:
        kb.update_settings({'memory_v4_read_enabled': False})
        kb.memory_v4_store = None
        with patch.object(kb, '_open_memory_v4_store_for_read', return_value=None):
            registry = kb._memory_v4_capability_registry(allow_legacy_memory=True)
            result = registry.get('memory.query').handler({'query':'青岚计划'})
            full = registry.get('memory.read').handler({'ids':[ids['choice']]})
    else:
        registry = kb._memory_v4_capability_registry(allow_legacy_memory=True)
        result = registry.get('memory.query').handler({'query':'青岚计划'})
        full = registry.get('memory.read').handler({'ids':[ids['other_person']]})
    assert result['items'] and full['items']
    for row in result['items'] + full['items']:
        assert all(key in row for key in ['subject','authority','revision','valid_from','valid_to','lifecycle'])
        if not native:
            assert row['subject'] == 'unstructured_legacy' and row['authority'] == 'legacy_record'
    if native:
        assert full['items'][0]['subject'] == 'person:lin' and full['items'][0]['authority'] == 'observed'


def test_unmirrored_native_supplement_is_bounded_read_only_and_scope_safe(tmp_path):
    kb, ids = seed(tmp_path)
    assert kb.sqlite_store.search_candidate_ids('青岚计划')  # Nonempty old FTS must not hide a native-only row.
    before = kb.memory_v4_store.export_state()
    result = kb.query_memory_v4('青岚计划', bypass_need_planner=True)
    assert ids['other_person'] in {x['id'] for x in result['items']}
    store = kb.memory_v4_store
    assert len(store.search_native_candidate_ids('计划', limit=1)) <= 1
    assert not store.search_native_candidate_ids('%_')
    assert ids['project_alpha'] not in store.search_native_candidate_ids('项目青一')
    assert ids['project_alpha'] in store.search_native_candidate_ids('项目青一', scope_type='project', scope_id='alpha')
    assert ids['project_beta'] not in store.search_native_candidate_ids('项目青一', scope_type='project', scope_id='alpha')
    assert store.export_state() == before


@pytest.mark.parametrize('budget', [64, 200, 512, 1200])
def test_compiled_prompt_has_bounded_rules_and_never_includes_pending_as_fact(budget):
    compiler = PromptCompiler()
    pending = MemoryItemV4(id='pending', canonical_text='PENDING_CANARY', lifecycle='candidate')
    active = MemoryItemV4(id='active', canonical_text='USER_REASON_CANARY 因为离线可用。',
                          subject='user', valid_from='2025-01-01', revision=4)
    result = compiler.compile('我为什么选择离线？', memory_items=[pending, active], token_budget=budget)
    assert 'PENDING_CANARY' not in result.prompt
    assert result.audit['estimated_tokens'] <= budget
    assert 'USER_REASON_CANARY' not in json.dumps(result.audit)
    if budget == 1200:
        assert 'subject=user; revision=4' in result.prompt and 'valid: 2025-01-01' in result.prompt
        assert 'another person' in result.prompt and 'never assert Doctor never said it' in result.prompt


@pytest.mark.parametrize('native', [True, False])
def test_ordinary_prompt_reports_missing_evidence_without_claiming_complete_history(tmp_path, native):
    kb, ids = seed(tmp_path)
    if native:
        result = kb.build_memory_v4_context_with_audit('我说过紫钙星穹协议的细节吗？', token_budget=1200)
        context, audit = result['context'], result['audit']
    else:
        context, audit = kb.build_prompt_context_with_audit('我说过紫钙星穹协议的细节吗？', token_budget=1200)
    assert 'never assert Doctor never said it' in context
    assert audit['evidence_contract']['coverage'] == 'bounded_read_not_complete_history'
    if native:
        assert audit['selected_count'] == 0


def test_actual_qq_adapter_delivers_subject_time_version_and_original_why_to_model(server, tmp_path):
    from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
    from saya_app.services.qq_chat import QQChatResponder
    from saya_app.services.conversation import conversation_service
    from test_qq_capabilities import config, message
    kb, ids = seed(tmp_path)
    kb.update_settings({'memory_v4_agent_allow_private': True})
    manager = MemoryManager(str(tmp_path/'chats'))
    manager.legacy_sessions_file = str(tmp_path/'no-legacy.json')
    captured = []
    def post(url, **kwargs):
        payload = kwargs['json']; captured.append(payload)
        if len(captured) == 1:
            name = next(t['function']['name'] for t in payload['tools'] if t['function']['name'].startswith('memory_query_'))
            output = {'content':'', 'tool_calls':[{'id':'query', 'type':'function', 'function':{
                'name':name, 'arguments':json.dumps({'query':'青岚计划'})}}]}
        else:
            result = json.loads(next(m['content'] for m in payload['messages'] if m['role']=='tool'))
            # Tool envelope is intentionally supplied by the real runner.
            text = json.dumps(result, ensure_ascii=False)
            assert 'reason_passage_ids' in text and 'explicit_passages' in text
            assert 'person:lin' in text and 'revision' in text and 'valid_from' in text
            assert 'never assert Doctor never said it' in text
            output = {'content':'依据所选记录：海上作业经常断网，所以选择离线处理。'}
        return SimpleNamespace(status_code=200, json=lambda:{'choices':[{'message':output}]})
    def factory(url, key, settings):
        return OpenAICompatibleAgentStep(url, key, settings, request_post=post)
    async def run():
        responder = QQChatResponder(config(shared_memory=True, web_search_mode='off', memory_write_enabled=False, thinking_effort='auto'),
            server.ChatWithMotion, server._chat_stream_single, lambda:'api',
            lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(500, text='unexpected fallback'))) as api:
            with patch.object(server, '_get_api_httpx_client', lambda:api):
                answer = await responder(message('我为什么在青岚计划选择离线处理？', shared=True), [], 'session_qq_r4.jsonl')
                assert '断网' in str(answer)
    with patch.object(server,'kb_system',kb), patch.object(server,'memory_manager',manager), \
         patch.object(server,'refresh_daily_activity_memory',lambda:None), \
         patch.object(conversation_service,'save_credentials',lambda **kw:None), \
         patch('saya_app.services.memory_v4.OpenAICompatibleAgentStep',side_effect=factory):
        asyncio.run(run())
    assert len(captured) == 2
