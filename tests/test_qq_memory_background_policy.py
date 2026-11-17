import asyncio
from unittest.mock import patch,AsyncMock

import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_use import MemoryUseChanged
from saya_app.services.memory_v4 import CapabilityPolicy
from saya_app.services.qq_action_proposals import QQActionProposals
from saya_app.services.qq_memory_management import QQMemoryManagement
from test_qq_chat_runtime import server
from test_qq_task_scheduler import make


def test_obsolete_deferred_job_never_invokes_model_or_candidate_creation(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));m=kb.add_memory('停用的信息',source='manual')
    old=kb.memory_use.snapshot();kb.set_memory_use_policy(m['id'],True)
    candidate_function=AsyncMock(return_value=[])
    async def run():
        result=await server._execute_deferred_triage_job(None,{'memory_use_epoch':old,'user_text':'OLD_JOB_CANARY'})
        assert result['status']=='obsolete'
        candidate_function.assert_not_awaited()
    with patch.object(server,'kb_system',kb),patch.object(server,'maybe_create_autonomous_memory_candidates',candidate_function):
        asyncio.run(run())


def test_queued_job_receives_authoritative_epoch_and_floor(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    async def run():
        queue=[]
        with (patch.object(server,'_deferred_triage_queue',queue),patch.object(server,'_deferred_triage_jobs_save'),
              patch.object(server,'_ensure_deferred_triage_drainer'),patch.object(server,'_deferred_triage_wake')):
            server._enqueue_deferred_triage({'memory_use_epoch':'forged','memory_history_floor':'forged','request_id':'fixture'})
            assert queue[0]['job']['memory_use_epoch']==kb.memory_use.snapshot()
            assert queue[0]['job']['memory_history_floor']==kb.memory_use.history_floor()
    with patch.object(server,'kb_system',kb):asyncio.run(run())


def test_old_candidate_is_not_automatically_adopted_after_do_not_use(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));c=kb.add_memory_candidate('停用前的推断候选',title='候选')
    m=kb.add_memory('旧来源信息',source='manual');kb.set_memory_use_policy(m['id'],True)
    assert kb.confirm_memory_candidate(c['id'],automatic=True) is None
    assert kb._find_memory_candidate(c['id'])['status']=='pending'
    c['creation_time']='2020-01-01 00:00:00';c['confidence']=0.99
    assert kb.auto_adopt_pending_candidates()['adopted']==0
    assert kb._find_memory_candidate(c['id'])['status']=='pending'


def test_old_natural_suggestion_is_superseded_when_context_changes(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));bridge=make(tmp_path/'bridge',None);bridge.store.initialize(7)
    management=QQMemoryManagement(bridge,kb);proposals=QQActionProposals(bridge.store)
    scope='10000:private:20000:shared';session='session_qq_fixture.jsonl'
    proposals.ask(scope,session,'search_on',{},'要开启搜索吗？','r1');proposals.delivered(scope,session,'要开启搜索吗？')
    m=kb.add_memory('需要停用的事实',source='manual');kb.set_memory_use_policy(m['id'],True)
    management.prepare_scope(scope,[])
    assert proposals.reply(scope,session,'同意','r1',True) is None
    with bridge.store.connect() as db:
        assert db.execute('select state from qq_action_proposals').fetchone()[0]=='superseded'


def test_standalone_agent_cannot_use_late_context_after_policy_change(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));kb.settings['agent_loop_enabled']=True
    m=kb.add_memory('旧依据',source='manual');calls=[]
    def step(context,capabilities):
        calls.append(True)
        kb.memory_use.advance()
        return {'final_answer':'LATE_AGENT_CANARY'}
    result=kb.run_memory_v4_agent_loop('fixture',step,policy=CapabilityPolicy())
    assert result['answer']!='LATE_AGENT_CANARY' and len(calls)==1
