import json
from copy import deepcopy

import pytest

from core_services import KnowledgeBase
from saya_app.routers import memories
from saya_app.services.memory_importance import grade,view,projection,policy_view,set_policy,contribution,preview
from saya_app.services.memory_origin import bind_origin,ingress
from saya_app.services.memory_versions import record_version,MemoryVersionConflict
from saya_app.services.memory_v4 import MemoryItemV4,MemoryEvidence,MemoryV4Store,MemoryRetriever,MemoryQuery


@pytest.mark.parametrize('score,expected',[(0,'ordinary'),(.449,'ordinary'),(.45,'attention'),(.699,'attention'),(.70,'important'),(.849,'important'),(.85,'critical'),(1,'critical')])
def test_initial_grades_use_scores_not_source_or_text_length(score,expected):
    assert grade(score)==expected


@pytest.mark.parametrize('score,old,expected',[(.479,'ordinary','ordinary'),(.48,'ordinary','attention'),(.72,'attention','attention'),(.73,'attention','important'),(.82,'critical','critical'),(.819,'critical','important'),(.3,'critical','ordinary'),(.9,'ordinary','critical')])
def test_grade_hysteresis_and_multiple_threshold_crossings(score,old,expected):
    assert grade(score,old)==expected


@pytest.fixture
def kb(tmp_path,monkeypatch):
    value=KnowledgeBase(str(tmp_path/'kb'));monkeypatch.setattr(memories,'kb_system',value)
    return value


def test_manual_score_grade_lock_and_pipeline_preserve_pin(kb):
    item=memories.add_memory({'content':'虚构茶杯喜欢蓝色。','is_important':True})['memory']
    def change(updates):return memories.update_memory({'id':item['id'],'expected_version':record_version(item),'updates':updates})
    change({'importance_score':.72})
    assert item['importance_grade']=='important'  # Creation manual source default may start in a high grade.
    change({'importance_score':.2})
    assert item['importance_grade']=='ordinary'
    change({'importance_score':.74,'importance_locked':True})
    assert item['importance_grade']=='important' and item['importance_locked']
    current=kb.memory_v4_store.get_memory(item['id'])
    result=kb.process_memory_write_v4('更正：虚构茶杯改为喜欢绿色。',action={'operation':'correct','memory_id':item['id'],'predicate':current.predicate,'salience':.99})
    assert result['committed']
    actual=kb._find_memory(item['id'])
    assert actual['importance_score']==.74 and actual['is_important'] and actual['importance_locked']
    assert actual['importance_audit'][-1]['reason']=='locked_preserved'
    again=KnowledgeBase(kb.base_dir)
    assert projection(again._find_memory(item['id']))==projection(actual)
    assert again.memory_v4_store.get_memory(item['id']).salience==.74


def test_old_records_preview_initialize_backup_and_same_score(tmp_path,monkeypatch):
    base=tmp_path/'old';base.mkdir();(base/'knowledge.json').write_text(json.dumps([{'id':'old','content':'虚构旧事实。','title':'旧事实','source':'manual','importance_score':.65}]),encoding='utf-8')
    kb=KnowledgeBase(str(base));monkeypatch.setattr(memories,'kb_system',kb);item=kb._find_memory('old')
    before=deepcopy(item)
    proposed=preview(kb)
    assert item==before and proposed['writes']==0 and proposed['items'][0]['grade']=='attention'
    assert not view(item)['importance_grade_initialized']
    result=memories.initialize_importance({'id':'old','expected_version':record_version(item)})
    assert result['status']=='success' and item['importance_score']==.65 and item['content']==before['content']
    assert view(item)['importance_grade_initialized'] and (base/'backups/importance-v1/knowledge.json').exists()
    assert kb.memory_v4_store.get_memory('old').salience==.65


def test_policy_is_default_off_versioned_and_has_no_web_or_read_amplification(kb):
    initial=policy_view(kb);assert not initial['enabled']
    assert not ( __import__('pathlib').Path(kb.base_dir)/'memory_importance_policy.json').exists()
    enabled=set_policy(kb,True,initial['content_version']);assert enabled['enabled']
    with pytest.raises(MemoryVersionConflict):set_policy(kb,False,initial['content_version'])
    assert enabled['length_adjustment']==0 and enabled['max_qq_score_contribution']==.008


def test_v4_qq_bias_before_truncation_relevance_gate_and_nonpersistent_scores(tmp_path):
    store=MemoryV4Store(tmp_path/'v4.sqlite3')
    origins={'a-qq':ingress('qq',subject='20000',bot_id='10000',message_id='1')['origin'],
        'z-web':ingress('web')['origin'],'unrelated':ingress('qq',subject='20000',bot_id='10000',message_id='2')['origin']}
    for mid,origin in origins.items():
        text='synthetic matched topic' if mid!='unrelated' else 'totally different'
        item=MemoryItemV4(id=mid,canonical_text=text,salience=.65,metadata={'origin':origin})
        store.upsert_memory(item,[MemoryEvidence(id='e-'+mid,memory_id=mid,source_type='user_message',source_id=mid,quote=text)])
    query=MemoryQuery(query='matched topic',max_candidates=1,max_items=1)
    baseline=MemoryRetriever(store).search(query)
    assert baseline.items[0].id=='z-web'
    snapshot=[x.to_dict() for x in store.list_memories()]
    for _ in range(3):
        result=MemoryRetriever(store,qq_origin_preference=True).search(query)
        assert result.items[0].id=='a-qq'
        assert result.candidates[0].origin_contribution==.0052
        assert result.candidates[0].score==pytest.approx(result.candidates[0].base_score+.0052)
        assert any(x['id']=='unrelated' and x['reason']=='no_query_match' for x in result.omitted)
    assert [x.to_dict() for x in store.list_memories()]==snapshot
    strong=MemoryRetriever(store,semantic_scores={'z-web':1.0,'a-qq':.2},qq_origin_preference=True).search(MemoryQuery(query='new precise wording',max_items=1))
    assert strong.items[0].id=='z-web'


def test_trusted_qq_preference_legacy_fallback_and_web_unchanged(kb):
    with bind_origin(ingress('qq',subject='20000',bot_id='10000',message_id='1')):
        qq=kb.add_memory('synthetic cup preference',importance_score=.65)
    with bind_origin(ingress('web')):
        web=kb.add_memory('synthetic cup preference',importance_score=.65)
    set_policy(kb,True,policy_view(kb)['content_version'])
    original_scores=[kb.score_memory(x,'synthetic cup') for x in [qq,web]]
    snapshot=deepcopy(kb.memories)
    with bind_origin(ingress('qq',subject='20000',bot_id='10000',message_id='query')):
        assert kb.score_memory(qq,'synthetic cup')>kb.score_memory(web,'synthetic cup')
        kb.settings.update(memory_v4_read_enabled=False,memory_v4_shadow_read_enabled=False)
        payload=kb._memory_v4_capability_registry(allow_legacy_memory=True).get('memory.query').handler({'query':'synthetic cup','max_items':1})
        assert payload['items'][0]['id']==qq['id']
        assert payload['items'][0]['origin_contribution']<=.008
    assert [kb.score_memory(x,'synthetic cup') for x in [qq,web]]==original_scores
    assert all(x['importance_score']==y['importance_score'] for x,y in zip(kb.memories,snapshot))


def test_fake_labels_and_model_origin_do_not_get_bias_or_grades(kb):
    result=kb.process_memory_write_v4('记住：虚构重要性测试。',action={'metadata':{'importance_grade':'critical','importance_locked':True,'origin':ingress('qq',subject='20000',bot_id='10000')['origin']}})
    item=result['committed'][0]['memory']
    assert item['metadata']['importance_grade']=='attention' and not item['metadata']['importance_locked']
    assert contribution(item['metadata'].get('origin'),.65)==0
    assert contribution({'channel':'qq','verification':'verified'},.65)==0


def test_short_and_long_equivalent_facts_do_not_score_by_length_and_audit_is_bounded(kb):
    short=kb.add_memory('姓名是测试甲。',importance_score=.7)
    long=kb.add_memory('姓名是测试甲。'*100,importance_score=.7)
    assert short['importance_grade']==long['importance_grade'] and short['importance_score']==long['importance_score']
    for n in range(24):
        memories.update_memory({'id':short['id'],'expected_version':record_version(short),'updates':{'content':f'姓名是测试甲，附加虚构条目{n}。'}})
    assert short['importance_score']==.7 and len(short['importance_audit'])==20


def test_passive_model_grade_fields_are_sanitized(kb):
    result=kb.process_memory_write_v4('我喜欢虚构的蓝色杯子。',intent='passive_candidate',action={
        'operation':'candidate','metadata':{'importance_grade':'critical','importance_policy_version':1,'importance_locked':True}})
    candidate=result['candidates'][0]['proposal']
    assert candidate['metadata']['importance_grade']=='ordinary' and not candidate['metadata']['importance_locked']


def test_restoring_score_creates_new_grade_revision_and_preserves_current_locks_and_pin(kb):
    item=memories.add_memory({'content':'虚构分级恢复条目。','metadata':{'importance_score':.3}})['memory']
    revision=kb.memory_v4_store.get_memory(item['id']).revision
    memories.update_memory({'id':item['id'],'expected_version':record_version(item),'updates':{'importance_score':.9,'is_important':True,'importance_locked':True}})
    result=memories.apply_memory_v4_control({'operation':'restore','memory_id':item['id'],'revision':revision})
    assert result['status']=='success'
    current=kb._find_memory(item['id'])
    assert current['importance_score']==.3 and current['importance_grade']=='ordinary'
    assert current['importance_locked'] and current['is_important']


def test_new_grade_and_enabled_policy_require_compatible_rollback_reader(tmp_path):
    from saya_app.services.qq_rollback import memory_rollback_requirements
    root=tmp_path/'root';base=root/'knowledge_base';base.mkdir(parents=True)
    kb=KnowledgeBase(str(base));kb.add_memory('虚构兼容性检查。')
    set_policy(kb,True,policy_view(kb)['content_version'])
    assert 'memory_importance_v1' in memory_rollback_requirements(root)


def test_web_query_cannot_supply_qq_preference_filter(kb):
    with bind_origin(ingress('qq',subject='20000',bot_id='10000',message_id='origin')):
        kb.add_memory('synthetic host-only origin')
    set_policy(kb,True,policy_view(kb)['content_version'])
    result=kb.query_memory_v4('synthetic host-only',bypass_need_planner=True,qq_origin_preference=True)
    assert result['candidates'] and all(x['origin_contribution']==0 for x in result['candidates'])


def test_migration_partial_projection_does_not_become_unchanged_success(tmp_path,monkeypatch):
    from fastapi import HTTPException
    base=tmp_path/'partial';base.mkdir();(base/'knowledge.json').write_text(json.dumps([{'id':'old','content':'虚构旧事实。','source':'manual','importance_score':.65}]),encoding='utf-8')
    kb=KnowledgeBase(str(base));monkeypatch.setattr(memories,'kb_system',kb);item=kb._find_memory('old')
    monkeypatch.setattr(kb,'_sync_memory_v4',lambda *a,**kw:{'status':'error'})
    for _ in range(2):
        with pytest.raises(HTTPException) as error:memories.initialize_importance({'id':'old','expected_version':record_version(item)})
        assert error.value.status_code==503
    assert len(kb.memories)==1 and item['importance_score']==.65


def test_native_only_grade_requires_reader_before_legacy_projection(tmp_path):
    from saya_app.services.qq_rollback import memory_rollback_requirements
    root=tmp_path/'native';base=root/'knowledge_base';base.mkdir(parents=True)
    store=MemoryV4Store(base/'memory4.sqlite3')
    item=MemoryItemV4(id='native',canonical_text='synthetic native grade',metadata={'importance_policy_version':1,'importance_grade':'attention'})
    store.upsert_memory(item,[MemoryEvidence(id='e',memory_id='native',source_type='user_message',source_id='m',quote='synthetic native grade')])
    assert 'memory_importance_v1' in memory_rollback_requirements(root)
