from concurrent.futures import ThreadPoolExecutor
import copy
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from core_services import KnowledgeBase
from saya_app.routers import memories
from saya_app.services import memory_conflicts as conflicts
from saya_app.services.memory_versions import MemoryVersionConflict, record_version


@pytest.fixture
def kb(tmp_path, monkeypatch):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    monkeypatch.setattr(memories,'kb_system',kb)
    return kb


def version(kb,mid):
    return next(x['content_version'] for x in kb.memory_v4_controls_summary()['memories'] if x['id']==mid)


@pytest.mark.parametrize('operation',['pin','unpin','correct','forget','restore','do_not_mention'])
def test_v4_rejects_stale_web_snapshot_after_shared_edit_without_new_effect(kb,operation):
    m=kb.add_memory('original controls',source='manual');before=version(kb,m['id'])
    kb.update_memory(m['id'],{'content':'changed through QQ shared service'})
    epoch=kb.memory_use.snapshot()
    with pytest.raises(HTTPException) as error:
        memories.apply_memory_v4_control({'operation':operation,'memory_id':m['id'],'expected_version':before,
            'updates':{'canonical_text':'stale'},'revision':1,'enabled':True})
    assert error.value.status_code==409
    assert kb.memory_use.snapshot()==epoch and kb._find_memory(m['id'])['content']=='changed through QQ shared service'


def test_v4_token_ignores_access_counters_but_detects_native_edits(kb):
    m=kb.add_memory('token controls',source='manual');v=version(kb,m['id'])
    kb._find_memory(m['id'])['access_count']=900
    assert version(kb,m['id'])==v
    kb.memory_v4_store.update_memory(m['id'],{'canonical_text':'native edit'})
    with pytest.raises(MemoryVersionConflict):kb.memory_v4_control('pin',memory_id=m['id'],expected_version=v)


def test_v4_unknown_target_does_not_create_block_or_history_barrier(kb):
    epoch=kb.memory_use.snapshot();policies=copy.deepcopy(kb.memory_use.read_state().get('policies',{}))
    result=kb.memory_v4_control('do_not_mention',memory_id='missing',enabled=True)
    assert result['status']=='not_found' and kb.memory_use.snapshot()==epoch
    assert kb.memory_use.read_state().get('policies',{})==policies


def test_v4_disabled_and_managed_controls_preserve_persona(kb):
    m=kb.add_memory('persona fixture',source='manual',metadata={'managed_slot':'assistant_role:fixture'})
    before=copy.deepcopy(kb._find_memory(m['id']));epoch=kb.memory_use.snapshot()
    for op in ['correct','forget','restore','do_not_mention']:
        result=kb.memory_v4_control(op,memory_id=m['id'],updates={'canonical_text':'wrong'},revision=1)
        assert result['status']=='rejected'
    assert kb._find_memory(m['id'])==before and kb.memory_use.snapshot()==epoch
    kb.settings['memory_v4_write_enabled']=False
    assert kb.memory_v4_control('pin',memory_id=m['id'])['status']=='disabled'
    assert kb.memory_use.snapshot()==epoch


def test_v4_commit_with_projection_failure_reports_partial_and_preserves_stop_policy(kb):
    m=kb.add_memory('partial controls',source='manual');v=version(kb,m['id'])
    with patch.object(kb,'save_memories',return_value=False):
        with pytest.raises(HTTPException) as error:
            memories.apply_memory_v4_control({'operation':'do_not_mention','memory_id':m['id'],
                'expected_version':v,'enabled':True})
    assert error.value.status_code==503
    assert kb.memory_v4_store.get_memory(m['id']).metadata['do_not_mention'] is True
    assert not kb.memory_allowed_for_use(kb._find_memory(m['id']))


@pytest.mark.parametrize('operation',['correct','forget','restore','do_not_mention'])
def test_partial_v4_projection_recovers_native_truth_after_full_kb_restart(kb,operation):
    m=kb.add_memory('before pending native control',source='manual');mid=m['id']
    if operation=='restore':
        assert kb.memory_v4_control('forget',memory_id=mid)['status']=='success'
    with patch.object(kb,'save_memories',return_value=False):
        result=kb.memory_v4_control(operation,memory_id=mid,updates={'canonical_text':'native latest fact'},revision=1,enabled=True)
    assert result['status']=='partial' and mid in kb._pending_v4_projections()
    before=kb.memory_v4_store.get_memory(mid).to_dict()
    # Ordinary compatibility saves cannot roll native state back while pending.
    kb._sync_memory_v4(kb.memories)
    assert kb.memory_v4_store.get_memory(mid).to_dict()==before
    assert kb.memory_v4_control('pin',memory_id=mid)['status']=='partial'
    restored=KnowledgeBase(kb.base_dir)
    assert mid not in restored._pending_v4_projections()
    if operation=='forget':
        assert restored._find_memory(mid) is None and mid in restored.deleted_memory_ids
        assert restored.memory_v4_store.is_tombstoned(mid)
    else:
        assert restored._find_memory(mid) and mid not in restored.deleted_memory_ids
        assert not restored.memory_v4_store.is_tombstoned(mid)
        expected='native latest fact' if operation=='correct' else 'before pending native control'
        assert restored._find_memory(mid)['content']==expected
        assert restored.memory_allowed_for_use(restored._find_memory(mid))==(operation!='do_not_mention')


def test_v4_projection_intent_failure_prevents_native_mutation(kb):
    m=kb.add_memory('journal failure',source='manual');before=kb.memory_v4_store.get_memory(m['id']).to_dict()
    with patch.object(kb,'_mark_v4_projection',side_effect=OSError('journal failure')):
        assert kb.memory_v4_control('pin',memory_id=m['id'])['status']=='partial'
    assert kb.memory_v4_store.get_memory(m['id']).to_dict()==before


def test_concurrent_v4_actions_bind_one_snapshot(kb):
    m=kb.add_memory('concurrent controls',source='manual');v=version(kb,m['id'])
    def act(op):
        try:return kb.memory_v4_control(op,memory_id=m['id'],expected_version=v)['status']
        except MemoryVersionConflict:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(act,['pin','correct']))
    # Supply valid corrections in separate tests; either a rejected validation
    # or a serialized conflict is acceptable, while pin must commit exactly once.
    assert results[0]=='success' and results[1] in {'conflict','rejected'}


def pair(kb):
    a=kb.add_memory('Doctor 喜欢喝拿铁，尤其下午工作的时候。',category='preference',source='manual')
    b=kb.add_memory('Doctor 现在喜欢喝美式咖啡了。',category='preference',source='manual')
    return a,b


def card(kb,origin='explicit',candidate=False):
    a,b=pair(kb)
    if candidate:b=kb.add_memory_candidate('Doctor 改喝美式咖啡了。',category='preference')
    c=conflicts.verified_create(kb.base_dir,kb,lambda _: '{"is_contradiction":true}',b,origin)
    assert c
    return a,b,c


def test_graph_snapshot_covers_memories_and_manual_edges(kb):
    a,b=pair(kb);v=kb.get_memory_graph_view()['graph_version']
    edge=memories.add_memory_graph_edge({'source_memory_id':a['id'],'target_memory_id':b['id'],'expected_graph_version':v})['edge']
    with pytest.raises(HTTPException) as error:
        memories.remove_memory_graph_edge({'edge_id':edge['id'],'expected_graph_version':v})
    assert error.value.status_code==409
    fresh=kb.get_memory_graph_view()['graph_version']
    kb.update_memory(a['id'],{'title':'QQ corrected title'})
    with pytest.raises(HTTPException) as error:
        memories.remove_memory_graph_edge({'edge_id':edge['id'],'expected_graph_version':fresh})
    assert error.value.status_code==409
    assert memories.remove_memory_graph_edge({'edge_id':edge['id'],
        'expected_graph_version':kb.get_memory_graph_view()['graph_version']})['status']=='success'


def test_graph_disk_failure_rolls_back_memory_and_lookup_indexes(kb):
    a,b=pair(kb);kb.ensure_memory_graph();before=copy.deepcopy(kb.memory_graph.graph)
    with patch.object(kb.memory_graph,'_save',side_effect=OSError('fixture failed')):
        with pytest.raises(OSError):kb.add_memory_graph_edge(a['id'],b['id'])
    assert kb.memory_graph.graph==before
    edge=kb.add_memory_graph_edge(a['id'],b['id']);before=copy.deepcopy(kb.memory_graph.graph)
    with patch.object(kb.memory_graph,'_save',side_effect=OSError('fixture failed')):
        with pytest.raises(OSError):kb.remove_memory_graph_edge(edge['id'])
    assert kb.memory_graph.graph==before
    assert any(x['id']==edge['id'] for x in kb.memory_graph.neighbors(a['id'])['edges'])


def test_conflict_stale_targets_do_not_consume_card(kb):
    a,b,c=card(kb);kb.update_memory(a['id'],{'content':'changed after conflict card'})
    with pytest.raises(MemoryVersionConflict):
        conflicts.resolve(kb.base_dir,kb,c['id'],'supersede_old',expected_version=c['content_version'])
    assert conflicts.get(kb.base_dir,c['id'])['status']=='pending'
    assert a['id'] not in kb._find_memory(b['id']).get('supersedes',[])


@pytest.mark.parametrize('action',['supersede_old','keep_both'])
def test_conflict_uses_one_reciprocal_memory_commit(kb,action):
    a,b,c=card(kb)
    with patch.object(kb,'save_memories',wraps=kb.save_memories) as save:
        result=conflicts.resolve(kb.base_dir,kb,c['id'],action,expected_version=c['content_version'])
    assert result['status']=='success' and save.call_count==1
    kb._validate_relationship_graph()
    assert (a['id'] in kb._find_memory(b['id']).get('supersedes',[]))==(action=='supersede_old')
    assert conflicts.resolve(kb.base_dir,kb,c['id'],action)['status']=='error'


def test_conflict_confirmed_candidate_discard_archives_new_but_keeps_original(kb):
    a,b,c=card(kb,origin='candidate')
    assert conflicts.resolve(kb.base_dir,kb,c['id'],'discard_new')['status']=='success'
    assert kb._find_memory(b['id'])['is_archived'] and not kb._find_memory(a['id'])['is_archived']


def test_conflict_save_failure_remains_unknown_and_never_retries(kb):
    _,_,c=card(kb)
    with patch.object(kb,'save_memories',return_value=False) as save:
        result=conflicts.resolve(kb.base_dir,kb,c['id'],'supersede_old')
        again=conflicts.resolve(kb.base_dir,kb,c['id'],'supersede_old')
    assert result['status']==again['status']=='unknown' and save.call_count==1
    assert conflicts.get(kb.base_dir,c['id'])['status']=='applying'
    assert conflicts.pending(kb.base_dir)


def test_conflict_committed_but_state_save_failure_has_no_false_success(kb):
    a,b,c=card(kb);real=conflicts._save;counter=0
    def fail_second(*args):
        nonlocal counter
        counter+=1
        if counter==2:raise OSError('state commit failed')
        return real(*args)
    with patch.object(conflicts,'_save',side_effect=fail_second):
        result=conflicts.resolve(kb.base_dir,kb,c['id'],'supersede_old')
    assert result['status']=='partial' and result['committed'] is True
    assert a['id'] in kb._find_memory(b['id'])['supersedes']
    assert conflicts.resolve(kb.base_dir,kb,c['id'],'supersede_old')['status']=='unknown'


def test_blocked_or_changed_during_model_verification_creates_no_card(kb):
    a,b=pair(kb)
    kb.set_memory_use_policy(a['id'],True)
    with patch('builtins.print'):
        model=lambda _:pytest.fail('blocked source must not reach model')
        assert conflicts.verified_create(kb.base_dir,kb,model,b,'explicit') is None
    kb.set_memory_use_policy(a['id'],False)
    def changed(_):
        kb.update_memory(a['id'],{'title':'changed while model ran'})
        return '{"is_contradiction":true}'
    assert conflicts.verified_create(kb.base_dir,kb,changed,b,'explicit') is None


def test_conflict_old_unversioned_pending_card_is_not_rebound_to_new_targets(kb):
    _,_,c=card(kb);state=conflicts._load(kb.base_dir);state['conflicts'][0].pop('target_versions');conflicts._save(kb.base_dir,state)
    with pytest.raises(MemoryVersionConflict):conflicts.resolve(kb.base_dir,kb,c['id'],'keep_both')


def test_pending_candidate_discard_uses_shared_once_only_candidate_path(kb):
    _,b,c=card(kb,origin='candidate',candidate=True)
    assert conflicts.resolve(kb.base_dir,kb,c['id'],'discard_new')['status']=='success'
    assert kb._find_memory_candidate(b['id'])['status']=='ignored'
