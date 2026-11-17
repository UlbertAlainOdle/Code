import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.session_archive_writes import archive, preview
from saya_app.services.memory_versions import MemoryVersionConflict
from test_qq_chat_runtime import server


@pytest.fixture
def lab(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'knowledge_base'))
    manager=MemoryManager(str(tmp_path/'history'))
    state={'key':'session_archive_test.jsonl','entries':[{'role':'user','content':'虚构青杯问题'},
        {'role':'ai','content':'虚构青杯解释'}]}
    load=lambda:(state['key'],state['entries'])
    source=preview(kb,manager,load)
    request={'operation_id':'a'*32,'expected_snapshot':source['snapshot'],'selected_exchange_ids':['qa-0-1']}
    return kb,manager,state,load,request


async def run_archive(lab,generate,request=None):
    kb,manager,state,load,original=lab
    return await archive(kb,manager,request or original,load,generate,
        lambda text:('虚构归档',text),lambda text:(text,False,len(text)))


def test_exact_archive_replay_and_reopened_preview_share_actual_commit(lab):
    kb,_,_,_,request=lab
    calls=[]
    async def model(*args):calls.append(1);return '虚构青杯归档摘要。'
    async def run():
        first=await run_archive(lab,model)
        assert first['status']=='success' and first['summary']=='虚构青杯归档摘要。'
        again=await run_archive(lab,model,{**request,'operation_id':'b'*32})
        assert again['operation']['operation_id']==first['operation']['operation_id']
        assert again['operation']['replayed'] and len(kb.memories)==1 and len(calls)==1
    asyncio.run(run())


@pytest.mark.parametrize('change',['text','epoch','session','remove'])
def test_source_or_permission_change_during_model_wait_rejects_commit(lab,change):
    kb,_,state,_,_=lab
    async def model(*args):
        if change=='text':state['entries'][1]['content']='另一份解释'
        elif change=='epoch':kb.memory_use.advance()
        elif change=='session':state['key']='session_other.jsonl'
        else:state['entries']=[]
        return '旧来源生成的摘要。'
    result=asyncio.run(run_archive(lab,model))
    assert result['status']=='invalid' and not kb.memories


def test_source_changed_since_preview_is_rejected_before_model(lab):
    _,_,state,_,_=lab
    state['entries'][0]['content']='已编辑的新问题'
    async def model(*args):pytest.fail('stale preview must not call model')
    with pytest.raises(MemoryVersionConflict):asyncio.run(run_archive(lab,model))


def test_concurrent_duplicate_returns_pending_without_second_model_or_commit(lab):
    kb,_,_,_,request=lab
    async def run():
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def model(*args):calls.append(1);entered.set();await release.wait();return '虚构摘要。'
        task=asyncio.create_task(run_archive(lab,model));await entered.wait()
        pending=await run_archive(lab,model,{**request,'operation_id':'b'*32})
        assert pending['status']=='pending' and not kb.memories
        release.set();saved=await task
        assert saved['status']=='success' and len(kb.memories)==len(calls)==1
    asyncio.run(run())


def test_unknown_postcommit_result_never_regenerates_or_writes_again(lab):
    kb,_,_,_,_=lab
    actual=kb.add_memory;calls=[]
    async def model(*args):calls.append(1);return '虚构未知结果摘要。'
    def fail(*args,**kwargs):actual(*args,**kwargs);raise OSError('private error')
    async def run():
        with patch.object(kb,'add_memory',fail):first=await run_archive(lab,model)
        assert first['status']=='unknown' and len(kb.memories)==1
        second=await run_archive(lab,model)
        assert second['status']=='unknown' and len(calls)==len(kb.memories)==1
    asyncio.run(run())


def test_cancel_during_model_preparation_has_no_commit_or_replay(lab):
    kb,_,_,_,_=lab
    async def run():
        entered=asyncio.Event()
        async def model(*args):entered.set();await asyncio.Event().wait()
        task=asyncio.create_task(run_archive(lab,model));await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        async def unexpected(*args):pytest.fail('cancelled preparation must not replay')
        result=await run_archive(lab,unexpected)
        assert result['status']=='invalid' and not kb.memories
    asyncio.run(run())


def test_same_operation_cannot_change_selection_and_query_hides_summary(lab):
    kb,_,state,_,request=lab
    async def model(*args):return '不可在结果查询暴露的虚构摘要。'
    first=asyncio.run(run_archive(lab,model))
    with pytest.raises(ValueError,match='mismatch'):
        asyncio.run(run_archive(lab,model,{**request,'selected_exchange_ids':['qa-3-4']}))
    from saya_app.services.chat_memory_writes import journal,identity,public_result
    from saya_app.services.memory_origin import bind_origin,ingress
    with bind_origin(ingress('web',session_id=state['key'])):_,scope=identity('capture',state['key'])
    stored=journal(kb).lookup(scope,state['key'],first['operation']['operation_id'])
    assert stored.committed and '虚构摘要' not in str(public_result(stored))


def test_old_derived_history_excluded_without_changing_source_indices(lab):
    kb,manager,state,load,_=lab
    kb.memory_use.advance()
    state['entries'] += [{'role':'user','content':'新问题'},
        {'role':'ai','content':'新解释','memory_use_epoch':kb.memory_use.snapshot()}]
    source=preview(kb,manager,load)
    assert [x['id'] for x in source['exchanges']]==['qa-2-3']


@pytest.mark.parametrize('current',[False,True])
def test_both_http_archive_routes_use_preview_source_and_stable_results(server,lab,monkeypatch,current):
    kb,manager,state,_,_=lab
    monkeypatch.setattr(server,'kb_system',kb);monkeypatch.setattr(server,'memory_manager',manager)
    manager.save_message('user','虚构问题',session_id=state['key'])
    manager.save_message('ai','虚构解释',session_id=state['key'])
    manager.current_session_file=str(Path(manager.base_dir)/state['key'])
    calls=[]
    async def model(*args,**kwargs):calls.append(1);return 'TITLE: 归档\nSUMMARY: 虚构路由摘要。'
    monkeypatch.setattr(server,'_generate_archive_completion',model)
    source=server.archive_history_preview() if current else server.archive_session_preview({'session_id':state['key']})
    request={'operation_id':'c'*32,'expected_snapshot':source['snapshot'],'selected_exchange_ids':['qa-0-1']}
    if not current:request['session_id']=state['key']
    route=server.archive_history if current else server.archive_session
    first=asyncio.run(route(request));second=asyncio.run(route(request))
    assert first['status']==second['status']=='success' and len(calls)==len(kb.memories)==1


def test_archive_ledger_requires_compatible_reader(lab):
    kb,_,_,_,_=lab
    async def model(*args):return '虚构归档兼容测试。'
    asyncio.run(run_archive(lab,model))
    from saya_app.services.qq_rollback import memory_rollback_requirements
    assert 'archive_write_operations_v1' in memory_rollback_requirements(Path(kb.base_dir).parent)


def test_edited_committed_archive_replay_keeps_original_id_without_old_summary(lab):
    kb,_,_,_,_=lab
    async def model(*args):return '虚构原摘要。'
    first=asyncio.run(run_archive(lab,model))
    kb.update_memory(first['memory_id'],{'content':'用户后来修改的摘要。'})
    async def unexpected(*args):pytest.fail('do not regenerate an already committed archive')
    replay=asyncio.run(run_archive(lab,unexpected))
    assert replay['memory_id']==first['memory_id'] and replay['content_changed'] and 'summary' not in replay


def test_reopened_cancelled_plan_deduplicates_against_later_success(lab):
    kb,_,_,_,request=lab
    async def run():
        entered=asyncio.Event()
        async def cancelled(*args):entered.set();await asyncio.Event().wait()
        task=asyncio.create_task(run_archive(lab,cancelled));await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        calls=[]
        async def model(*args):calls.append(1);return '取消后明确重开的虚构摘要。'
        first=await run_archive(lab,model,{**request,'operation_id':'b'*32})
        replay=await run_archive(lab,model,{**request,'operation_id':'c'*32})
        assert first['status']==replay['status']=='success'
        assert first['operation']['operation_id']==replay['operation']['operation_id']
        assert len(kb.memories)==len(calls)==1
    asyncio.run(run())
