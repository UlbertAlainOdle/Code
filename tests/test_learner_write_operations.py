from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase
from saya_app.services.learner import LearnerService
from saya_app.services.learner_writes import authority,read_context,manage,readonly_result
from saya_app.services.chat_memory_writes import journal


@pytest.fixture
def lab(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'knowledge_base'))
    service=LearnerService(tmp_path/'learner.json');service.configure(knowledge_base=kb)
    task=service.create_task({'topic':'虚构青杯知识','manage_memory':True,'enabled':False})
    run_id='run-lab'
    service.data['runs'].append({'id':run_id,'task_id':task['id'],'status':'running','memory_authority':authority(task)})
    read_context(service,task,run_id)
    candidate={'title':'虚构青杯','content':'虚构青杯实验固定使用蓝光。','tags':[],'importance':.6,'important':False}
    service._memory_candidates=lambda *a,**k:[deepcopy(candidate)]
    yield service,kb,task,run_id,candidate
    service.shutdown()


def invoke(lab,material=None):
    service,_,task,run_id,_=lab
    return manage(service,task,run_id,'虚构的来源归纳。',material or {'sources':[]},{})


def test_manual_disabled_task_is_authorized_but_same_run_replay_never_rewrites(lab):
    service,kb,_,run_id,_=lab;first=invoke(lab)
    assert first['status']=='success' and len(first['created_ids'])==1
    with patch.object(service,'_memory_candidates',side_effect=AssertionError('repeat model')),patch.object(kb,'add_memory',side_effect=AssertionError('repeat write')):
        second=invoke(lab)
    assert second['created_ids']==first['created_ids'] and len(kb.memories)==1
    assert service.get_run(run_id)['memory_result']['created_ids']==first['created_ids']


def test_parallel_requests_only_generate_candidates_once(lab):
    service,kb,_,_,candidate=lab;entered=threading.Event();release=threading.Event()
    def model(*args,**kwargs):entered.set();assert release.wait(5);return [candidate]
    with patch.object(service,'_memory_candidates',model),ThreadPoolExecutor(2) as pool:
        first=pool.submit(invoke,lab);assert entered.wait(5)
        try:
            second=pool.submit(invoke,lab).result(5)
            assert second['status']=='pending' and not second['created_ids']
        finally:release.set()
        assert first.result(5)['status']=='success'
    assert len(kb.memories)==1


@pytest.mark.parametrize('change',['task','epoch','run','shutdown','material'])
def test_revoke_or_change_during_model_prevents_commit(lab,change):
    service,kb,task,run_id,candidate=lab;material={'sources':[]}
    def model(*args,**kwargs):
        if change=='task':service.update_task(task['id'],{'manage_memory':False})
        elif change=='epoch':kb.memory_use.advance()
        elif change=='run':service.data['runs'][0]['status']='interrupted'
        elif change=='shutdown':service._shutdown_event.set()
        else:material['sources'].append({'url':'changed'})
        return [candidate]
    with patch.object(service,'_memory_candidates',model):result=invoke(lab,material)
    assert result['status']=='invalid' and not kb.memories


def test_source_memory_change_after_summary_read_prevents_commit(lab):
    service,kb,task,run_id,candidate=lab
    parent=kb.add_memory('虚构青杯旧的来源知识。')
    task['read_memory']=True;service.data['tasks'][0].update(read_memory=True)
    service.data['runs'][0]['memory_authority']=authority(task)
    with patch.object(service,'_read_memories',return_value=(parent['content'],[parent['id']])):read_context(service,task,run_id)
    def model(*a,**k):kb.update_memory(parent['id'],{'content':'用户更正后的来源知识。'});return [candidate]
    with patch.object(service,'_memory_candidates',model):result=invoke(lab)
    assert result['status']=='invalid' and len(kb.memories)==1


def test_target_version_frozen_before_candidate_model(lab):
    service,kb,task,run_id,candidate=lab
    target=kb.add_memory(candidate['content'],source='learner',source_detail={'learner_run_id':run_id})
    def model(*a,**k):kb.update_memory(target['id'],{'content':'外部已更新的版本。'});return [candidate]
    with patch.object(service,'_memory_candidates',model):result=invoke(lab)
    assert result['status']=='invalid' and kb._find_memory(target['id'])['content']=='外部已更新的版本。'


@pytest.mark.parametrize('after',[False,True])
def test_uncertain_second_add_records_partial_and_never_replays(lab,after):
    service,kb,_,run_id,candidate=lab;actual=kb.add_memory;calls=[]
    other={**candidate,'content':'完全不同的虚构银湾观星实验流程。'}
    service._memory_candidates=lambda *a,**k:[candidate,other]
    def fail(*a,**kw):
        calls.append(1)
        if len(calls)==2:
            if after:actual(*a,**kw)
            raise OSError('uncertain second')
        return actual(*a,**kw)
    with patch.object(kb,'add_memory',fail):first=invoke(lab)
    assert first['status']=='partial' and len(first['created_ids'])==1
    assert first['operations'][1]['items'][0]['state']=='unknown'
    ledger=journal(kb);ledger.clock=lambda:10**12
    ledger.run('c'*32,'other','other','capture',[{}],verify=lambda db:True,valid=lambda p:True,write=lambda p:{'state':'not_saved'},retention=1)
    with patch.object(kb,'add_memory',side_effect=AssertionError('duplicate')),patch.object(service,'_memory_candidates',side_effect=AssertionError('repeat')):
        second=invoke(lab)
    assert second['status']=='partial' and second['created_ids']==first['created_ids'] and len(kb.memories)==1+int(after)
    assert readonly_result(kb,run_id)['status']=='partial'


def test_unknown_outer_outcome_is_readable_after_restart_without_new_model(lab):
    service,kb,task,run_id,_=lab;ledger=journal(kb);actual=ledger._record
    def fail(oid,items):
        if items[0].get('result'):raise OSError('after item commits')
        return actual(oid,items)
    with patch.object(ledger,'_record',fail),pytest.raises(OSError):invoke(lab)
    assert len(kb.memories)==1
    restarted=LearnerService(service.path);restarted.configure(knowledge_base=kb)
    try:
        result=restarted.get_run(run_id)['memory_result']
        assert result['status']=='unknown' and len(result['created_ids'])==1
        with patch.object(restarted,'_memory_candidates',side_effect=AssertionError('repeat')):
            assert manage(restarted,task,run_id,'same',{'sources':[]},{})['status']=='unknown'
    finally:restarted.shutdown()
    assert len(kb.memories)==1


def test_merge_primary_commits_before_archive_and_each_outcome_is_counted(lab):
    service,kb,task,run_id,candidate=lab
    detail={'learner_task_id':task['id'],'learner_run_id':run_id}
    primary=kb.add_memory(candidate['content'],source='learner',is_important=True,source_detail=detail)
    duplicate=kb.add_memory(candidate['content'],source='learner',source_detail=detail)
    locked=kb.add_memory(candidate['content'],source='learner',source_detail=detail)
    kb.update_memory(locked['id'],{'is_locked':True})
    # Read authorization belongs to the current source era.
    read_context(service,task,run_id)
    result=invoke(lab)
    assert result['status']=='success' and result['merged_ids']==[primary['id']],result
    assert result['archived_duplicate_ids']==[duplicate['id']] and locked['id'] in result['preserved_important_ids']
    assert [x['items'][0]['action'] for x in result['operations']]==['merged','archived']


def test_primary_update_failure_does_not_archive_any_duplicate(lab):
    service,kb,task,run_id,candidate=lab;detail={'learner_run_id':run_id}
    for _ in range(2):kb.add_memory(candidate['content'],source='learner',source_detail=detail)
    with patch.object(kb,'update_memory',return_value=False):result=invoke(lab)
    assert result['status']=='partial' and not result['archived_duplicate_ids'] and not any(m.get('is_archived') for m in kb.memories)


def test_derived_learner_memory_is_unusable_when_actual_parent_changes(lab):
    service,kb,task,run_id,_=lab;parent=kb.add_memory('实际读取的虚构来源。')
    task['read_memory']=True;service.data['tasks'][0].update(read_memory=True);service.data['runs'][0]['memory_authority']=authority(task)
    with patch.object(service,'_read_memories',return_value=(parent['content'],[parent['id']])):read_context(service,task,run_id)
    result=invoke(lab);derived=kb._find_memory(result['created_ids'][0])
    assert kb.memory_allowed_for_use(derived)
    kb.delete_memory(parent['id'])
    assert not kb.memory_allowed_for_use(derived)


def test_missing_run_authorization_cannot_write_by_calling_private_upsert(lab):
    service,kb,task,_,candidate=lab
    with pytest.raises(ValueError,match='authorization_missing'):
        service._upsert_learning_memory(task,'unregistered-run',candidate,{'sources':[]})
    assert not kb.memories


def test_deleted_committed_target_is_not_recreated_by_same_run(lab):
    _,kb,_,_,_=lab;result=invoke(lab);kb.delete_memory(result['created_ids'][0])
    with patch.object(kb,'add_memory',side_effect=AssertionError('revive')):again=invoke(lab)
    assert again['created_ids']==result['created_ids'] and not kb.memories


def test_new_scheduled_run_does_not_reuse_revoked_task_snapshot(lab):
    service,kb,task,run_id,_=lab
    service.update_task(task['id'],{'topic':'另一任务'})
    with pytest.raises(ValueError,match='changed_before_read'):read_context(service,task,run_id)
    assert not kb.memories


def test_revoked_then_restored_task_permission_does_not_revive_old_plan(lab):
    service,kb,task,_,candidate=lab
    def model(*a,**k):
        service.update_task(task['id'],{'manage_memory':False})
        service.update_task(task['id'],{'manage_memory':True})
        return [candidate]
    with patch.object(service,'_memory_candidates',model):result=invoke(lab)
    assert result['status']=='invalid' and not kb.memories
