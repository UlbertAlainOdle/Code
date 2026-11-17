"""Only explicit arrangement changes permit bounded historical generation."""
from copy import deepcopy
from unittest.mock import patch
import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_temporal_history import mark, selected_history


OLD='虚构的槐汀晚餐计划使用蒸锅，因为炉灶要留给汤锅。'
NEW='虚构的槐汀晚餐计划不再用蒸锅，改用烤箱，因为蒸锅坏了。'
QUESTION='槐汀晚餐计划在改用烤箱之前，为什么选蒸锅？'


def changed(tmp_path, text=NEW, *, bound=False):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    kb.update_settings({'embedding_enabled':False,'memory_core_limit':0})
    saved=kb.capture_explicit_memory('记住：'+OLD)
    current=kb.memory_v4_store.get_memory(saved['id'])
    action={'intent':'explicit_correct','memory_id':current.id,'proposal':{
        'canonical_text':text,'predicate':current.predicate,'kind':current.kind,
        'metadata':{'temporal_predecessor':{'version':1,'revision':1}}}} if bound else None
    result=kb.process_memory_write_v4('更正：'+text,message_id='change',action=action)
    assert result['committed']
    return kb,saved['id']


def test_changed_arrangement_reads_original_reason_without_restoring_a_version(tmp_path):
    kb,identifier=changed(tmp_path)
    before=deepcopy(kb.memory_v4_store.export_state())
    result=kb.build_memory_v4_context_with_audit(QUESTION)
    assert OLD in result['context'] and NEW not in result['context']
    assert 'historical arrangement; later changed' in result['context']
    assert result['audit']['historical_sources'][identifier]=={'revision':1,'current_revision':2}
    assert result['audit']['evidence_contract']['reason_passage_ids']==[identifier]
    assert kb._find_memory(identifier)['content']==NEW
    # Query selection usage is allowed; stored versions, records and evidence stay identical.
    after=kb.memory_v4_store.export_state()
    for key in ('memories','versions','evidence','tombstones'):assert after.get(key)==before.get(key)
    again=KnowledgeBase(kb.base_dir)
    assert OLD in again.build_memory_v4_context_with_audit(QUESTION)['context']


@pytest.mark.parametrize('question',[
    '槐汀晚餐计划现在用什么？', '槐汀晚餐计划在改用微波炉之前为什么选蒸锅？',
    '比较槐汀晚餐计划和另一个项目。', '他说“槐汀晚餐计划在改用烤箱之前”是什么意思？',
])
def test_current_unknown_transition_and_quoted_questions_do_not_open_old_text(tmp_path,question):
    kb,_=changed(tmp_path)
    result=kb.build_memory_v4_context_with_audit(question)
    assert '炉灶要留给汤锅' not in result['context']
    assert not result['audit'].get('historical_sources')


@pytest.mark.parametrize('text',[
    '其实虚构的槐汀晚餐计划从未用过蒸锅，应当使用烤箱。',
    '虚构的槐汀晚餐计划原记录记错了，不再用蒸锅，改用烤箱。',
    '虚构的槐汀晚餐计划使用烤箱。',
])
def test_factual_correction_does_not_authorize_old_reason(tmp_path,text):
    kb,_=changed(tmp_path,text,bound=True)
    result=kb.build_memory_v4_context_with_audit(QUESTION)
    assert '炉灶要留给汤锅' not in result['context']
    assert not result['audit'].get('historical_sources')


@pytest.mark.parametrize('action',['block','delete','archive','partial','tamper','failed_read'])
def test_current_source_revocation_or_invalid_proof_closes_history(tmp_path,action):
    kb,identifier=changed(tmp_path)
    if action=='block':kb.set_memory_use_policy(identifier,True)
    elif action=='delete':kb.process_memory_write_v4('忘掉槐汀晚餐计划',message_id='forget')
    elif action=='archive':kb.update_memory(identifier,{'is_archived':True})
    elif action=='partial':kb._mark_v4_projection(identifier,True)
    elif action=='tamper':
        item=kb.memory_v4_store.get_memory(identifier)
        item.metadata['temporal_predecessor']['previous_sha256']='a'*64
        kb.memory_v4_store.upsert_memory(item,operation='edit')
    else:
        with patch.object(kb.memory_v4_store,'list_versions',return_value=[]):
            assert not selected_history(kb,QUESTION,[kb.memory_v4_store.get_memory(identifier).to_dict()])
        return
    assert '炉灶要留给汤锅' not in kb.build_memory_v4_context_with_audit(QUESTION)['context']


def test_stale_text_policy_is_not_relaxed_and_untrusted_proof_is_removed(tmp_path):
    kb,identifier=changed(tmp_path)
    old=kb.memory_v4_store.list_versions(identifier)[-1].snapshot
    assert not kb.memory_allowed_for_use(old)
    metadata={'temporal_predecessor':{'version':1,'revision':1}}
    mark(metadata,kb.memory_v4_store.get_memory(identifier),'其实之前说错了。')
    assert 'temporal_predecessor' not in metadata


def test_later_factual_correction_removes_a_previously_valid_history_link(tmp_path):
    kb,identifier=changed(tmp_path)
    item=kb.memory_v4_store.get_memory(identifier)
    text='其实虚构的槐汀晚餐计划从未使用蒸锅，原记录不属实。'
    result=kb.process_memory_write_v4('更正：'+text,message_id='factual',action={
        'intent':'explicit_correct','memory_id':identifier,
        'proposal':{'canonical_text':text,'predicate':item.predicate,'kind':item.kind}})
    assert result['committed']
    assert 'temporal_predecessor' not in kb.memory_v4_store.get_memory(identifier).metadata
    assert not kb.build_memory_v4_context_with_audit(QUESTION)['audit'].get('historical_sources')


def test_source_blocked_during_snapshot_read_is_not_projected(tmp_path):
    kb,identifier=changed(tmp_path)
    original=kb.memory_v4_store.list_versions
    def revoke(*args,**kwargs):
        rows=original(*args,**kwargs)
        kb.memory_use.set_block(identifier,True)
        return rows
    with patch.object(kb.memory_v4_store,'list_versions',side_effect=revoke):
        assert not selected_history(kb,QUESTION,[kb.memory_v4_store.get_memory(identifier).to_dict()])


def test_reenabling_current_memory_does_not_reopen_history_before_nonuse_barrier(tmp_path):
    kb,identifier=changed(tmp_path)
    kb.set_memory_use_policy(identifier,True)
    kb.set_memory_use_policy(identifier,False)
    assert kb.memory_allowed_for_use(kb._find_memory(identifier))
    assert not kb.build_memory_v4_context_with_audit(QUESTION)['audit'].get('historical_sources')


def test_cas_revision_service_retains_only_an_explicit_changed_arrangement(tmp_path):
    from saya_app.services.memory_revisions import revision_service
    from saya_app.services.memory_versions import record_version
    kb=KnowledgeBase(str(tmp_path/'kb'))
    kb.update_settings({'embedding_enabled':False,'memory_core_limit':0})
    saved=kb.capture_explicit_memory('记住：'+OLD)
    service=revision_service(kb)
    preview=service.preview(saved['id'],'correct',NEW,expected_version=record_version(saved),
        scope='fixture',session_id='fixture',authorize=lambda:None)
    result=service.confirm(preview['confirmation_token'],scope='fixture',session_id='fixture',authorize=lambda:None)
    assert result['status']=='applied'
    assert OLD in kb.build_memory_v4_context_with_audit(QUESTION)['context']
    current=kb._find_memory(saved['id'])
    assert current['content']==NEW
    assert service.confirm(preview['confirmation_token'],scope='fixture',session_id='fixture',authorize=lambda:None)['status']=='done'


@pytest.mark.parametrize('budget',[256,512,800,2400])
def test_history_uses_original_budget_and_reports_actual_inclusion(tmp_path,budget):
    kb,identifier=changed(tmp_path)
    result=kb.build_memory_v4_context_with_audit(QUESTION,token_budget=budget)
    assert result['audit']['compiler']['estimated_tokens']<=budget
    included=identifier in {r['id'] for r in result['audit']['selected']}
    assert bool(result['audit'].get('historical_sources'))==included
    if included:assert 'historical arrangement; later changed' in result['context']
