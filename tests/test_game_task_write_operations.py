from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase
from saya_app.services.game_memory import record_task_event
from saya_app.services.game_task_writes import record,result_for
from saya_app.services.chat_memory_writes import journal
from saya_app.services.memory_write_operations import MemoryWriteOperations


@pytest.fixture
def lab(tmp_path,monkeypatch):
    monkeypatch.setenv('SAYA_GAME_MEMORY','1')
    from saya_app.services import game_memory
    class Clock:
        @staticmethod
        def now():return datetime(2026,11,6,12,0)
    monkeypatch.setattr(game_memory,'datetime',Clock)
    kb=KnowledgeBase(str(tmp_path/'knowledge_base'))
    plan={'plan_id':'plan-lab','status':'completed','goal_description':'完成虚构红石灯线路','steps':[{'id':1}]}
    return kb,plan


def test_concurrent_completion_callbacks_commit_once(lab):
    kb,plan=lab
    with patch.object(kb,'add_memory',wraps=kb.add_memory) as add,ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:record_task_event(plan,kb),range(2)))
    assert add.call_count==1 and len(kb.memories)==1 and sum(bool(x) for x in results)==1
    assert result_for(kb,plan['plan_id']).committed


def test_restart_replay_after_memory_delete_never_recreates_task(lab):
    kb,plan=lab;memory_id=record_task_event(plan,kb)
    kb.delete_memory(memory_id)
    restarted=KnowledgeBase(kb.base_dir)
    with patch.object(restarted,'add_memory',side_effect=AssertionError('revival')):
        assert record_task_event(plan,restarted) is None
    assert not restarted.memories and result_for(restarted,plan['plan_id']).items[0]['memory_id']==memory_id


@pytest.mark.parametrize('after',[False,True])
def test_unknown_storage_failure_cannot_be_reclaimed_even_after_expiry(lab,after):
    kb,plan=lab;actual=kb.add_memory
    def fail(*args,**kwargs):
        if after:actual(*args,**kwargs)
        raise OSError('uncertain storage return')
    with patch.object(kb,'add_memory',fail):assert record_task_event(plan,kb) is None
    assert result_for(kb,plan['plan_id']).items==[{'state':'unknown'}]
    ledger=journal(kb);ledger.clock=lambda:10**12
    ledger.run('f'*32,'other','other','capture',[{}],verify=lambda db:True,valid=lambda p:True,write=lambda p:{'state':'not_saved'},retention=1)
    with patch.object(kb,'add_memory',side_effect=AssertionError('duplicate')):
        assert record_task_event(plan,kb) is None
    assert len(kb.memories)==int(after) and result_for(kb,plan['plan_id']).items[0]['state']=='unknown'


def test_same_window_goal_dedup_is_durable_for_distinct_plan_ids(lab):
    kb,plan=lab
    assert record_task_event(plan,kb)
    other={**plan,'plan_id':'plan-other'}
    outcome=record(kb,other)
    assert outcome.items[0]['state']=='not_saved' and outcome.items[0]['reason']=='duplicate_event'
    assert len(kb.memories)==1


def test_uncertain_same_window_event_suppresses_another_plan(lab):
    kb,plan=lab
    with patch.object(kb,'add_memory',side_effect=OSError('unknown')):record_task_event(plan,kb)
    with patch.object(kb,'add_memory',side_effect=AssertionError('possible duplicate')):
        result=record(kb,{**plan,'plan_id':'plan-other'})
    assert result.items[0]['state']=='not_saved' and not kb.memories


def test_distinct_long_goals_do_not_collide_at_old_prefix(lab):
    kb,plan=lab;common='同一段虚构任务前缀'*30
    assert record_task_event({**plan,'goal_description':common+'甲'},kb)
    assert record_task_event({**plan,'plan_id':'plan-other','goal_description':common+'乙'},kb)
    assert len(kb.memories)==2


def test_same_plan_id_cannot_change_accepted_plan(lab):
    kb,plan=lab;first=record(kb,plan)
    with pytest.raises(ValueError,match='mismatch'):record(kb,{**plan,'goal_description':'另一项虚构任务'})
    assert len(kb.memories)==1 and result_for(kb,plan['plan_id']).id==first.id


@pytest.mark.parametrize('change',['disabled','status','goal','epoch'])
def test_change_before_commit_invalidates_frozen_plan(lab,monkeypatch,change):
    kb,plan=lab;actual=MemoryWriteOperations.run
    def mutate(ledger,*args,**kwargs):
        if change=='disabled':monkeypatch.setenv('SAYA_GAME_MEMORY','0')
        elif change=='status':plan['status']='cancelled'
        elif change=='goal':plan['goal_description']='别的任务'
        else:kb.memory_use.advance()
        return actual(ledger,*args,**kwargs)
    with patch.object(MemoryWriteOperations,'run',mutate):result=record(kb,plan)
    assert result.items==[{'state':'invalid'}] and not kb.memories


def test_disabled_incomplete_and_unidentified_plans_never_store(lab,monkeypatch):
    kb,plan=lab
    assert record_task_event({**plan,'status':'failed'},kb) is None
    assert record_task_event({**plan,'plan_id':''},kb) is None
    monkeypatch.setenv('SAYA_GAME_MEMORY','0')
    assert record_task_event(plan,kb) is None and not kb.memories


def test_result_read_does_not_call_storage_or_return_fact_body(lab):
    kb,plan=lab;first=record(kb,plan)
    with patch.object(kb,'add_memory',side_effect=AssertionError('query wrote')):
        result=result_for(kb,plan['plan_id'])
        assert result_for(kb,'plan-missing') is None
    assert result.id==first.id and '红石' not in str(result.items)


def test_game_task_requires_compatible_rollback(lab):
    from saya_app.services.qq_rollback import memory_rollback_requirements
    kb,plan=lab;record(kb,plan)
    assert 'game_task_write_operations_v1' in memory_rollback_requirements(Path(kb.base_dir).parent)
