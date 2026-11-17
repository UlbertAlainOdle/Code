import asyncio
import pytest
from core_services import KnowledgeBase
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.qq_memory_access import MemoryActor,MemoryAccessDenied
from saya_app.services.memory_versions import record_version
from test_qq_task_scheduler import make,submit
from test_qq_chat_runtime import server


@pytest.fixture
def memory_lab(tmp_path):
    bridge=make(tmp_path/'bridge',None);bridge.store.initialize(7)
    kb=KnowledgeBase(str(tmp_path/'kb'));service=QQMemoryManagement(bridge,kb);bridge.memory_management=service
    return bridge,kb,service,MemoryActor('10000','20000',True,True)


def test_candidate_preview_is_actual_content_and_saved_receipt_is_readback(memory_lab):
    b,k,s,actor=memory_lab
    c=k.add_memory_candidate('周六参加了虚构的天文观测。',title='含糊标题')
    payload,preview=s.candidate_proposal(actor,c['id'])
    assert c['content'] in preview
    result=s.confirm_candidate(actor,c['id'],payload['candidate_version'],session_id='session_qq_test.jsonl')
    assert c['content'] in result
    reply=s.natural_response(actor,'刚才记住的那条具体是什么？','session_qq_test.jsonl')
    assert c['content'] in reply


def test_natural_saved_catalog_excludes_candidates_and_blocked_records(memory_lab):
    b,k,s,actor=memory_lab
    k.add_memory('唯一虚构偏好是蓝色风筝。',title='风筝')
    blocked=k.add_memory('BLOCKED_CANARY',title='停用条目')
    k.set_memory_use_policy(blocked['id'],True,expected_version=record_version(blocked))
    k.add_memory_candidate('CANDIDATE_CANARY',title='待确认')
    result=s.natural_response(actor,'你现在对我有哪些长期记忆？','session_qq_test.jsonl')
    assert '风筝' in result and 'BLOCKED_CANARY' not in result and 'CANDIDATE_CANARY' not in result


def test_candidate_revision_requires_new_version_and_persists(memory_lab,tmp_path):
    b,k,s,actor=memory_lab
    c=k.add_memory_candidate('想去山上。',title='出游');version=record_version(c)
    payload,preview=s.revise_candidate(actor,c['id'],version,'改为想去海边。')
    assert '海边' in preview and payload['candidate_version']!=version
    with pytest.raises(ValueError):s.confirm_candidate(actor,c['id'],version)
    reloaded=KnowledgeBase(str(tmp_path/'kb'))
    assert reloaded._find_memory_candidate(c['id'])['content']=='改为想去海边。'


def test_natural_queries_do_not_match_quoted_requests_or_other_users(memory_lab):
    b,k,s,actor=memory_lab
    assert s.natural_response(actor,'他说“你记得我哪些事”是什么意思？','s') is None
    stranger=MemoryActor('10000','30000',True,False)
    with pytest.raises(MemoryAccessDenied):s.natural_response(stranger,'你记得我哪些事','s')


def test_actual_empty_context_retrieval_contains_saved_fact_and_audit(server,tmp_path):
    import json,httpx
    from unittest.mock import patch
    from core_services import MemoryManager
    from saya_app.services.conversation import conversation_service
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    memory=kb.add_memory('用户最喜欢的虚构测试代号是星帆ZXQ718。',title='虚构测试代号',source='manual')
    seen=[];sid='session_qq_recall_fresh.jsonl'
    async def run():
        async def model(request):
            seen.append(json.loads(request.content));data={'choices':[{'delta':{'content':'依据提供的记忆作答。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                req=server.ChatWithMotion(user_input='我最喜欢的虚构测试代号是什么？',session_id=sid,model='api',api_key='fake',
                    api_url='http://mock/v1',agent_mode=False,recall_mode='on',memory_write_enabled=False)
                response=await server._chat_stream_single(req,channel_turn={'history':[],'shared':True,'is_owner':True,'sender_id':'20000'})
                async for _ in response.body_iterator:pass
        assert seen and 'ZXQ718' in json.dumps(seen[-1],ensure_ascii=False)
        rows=manager.load_session_full(sid,make_current=False)
        audit=next(r['memory_recall'] for r in rows if r['role']=='ai')
        assert memory['id'] in json.dumps(audit)
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(conversation_service,'save_credentials',lambda **kw:None)):
        asyncio.run(run())
