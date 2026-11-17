"""A pending candidate is not a commit; enabled QQ saving is a real service."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.qq_action_proposals import QQActionProposals
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_chat import QQChatResponder, channel_identity_prompt
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.qq_responses import ActionProposal
from test_qq_chat_runtime import server
from test_qq_delivery import config, event
from test_qq_task_scheduler import make


def test_enabled_save_contract_distinguishes_permission_candidate_and_commit():
    prompt = channel_identity_prompt({'shared':True,'is_owner':True,'memory_write_enabled':True})
    assert 'This chat CAN save long-term memory' in prompt
    assert 'a candidate or a question asking for consent is NOT a committed long-term memory' in prompt
    assert 'Do not say 已保存、记住了、已经记入长期记忆' in prompt
    assert 'Do not tell the owner to manually copy the fact into the web page' in prompt


@pytest.mark.parametrize('turn', [
    {'shared':True,'is_owner':True,'memory_write_enabled':False},
    {'shared':False,'is_owner':True},
    {'shared':True,'is_owner':False},
    {'shared':True,'is_owner':True,'group_id':'90000'},
])
def test_save_contract_never_grants_other_scopes_permission(turn):
    assert 'This chat CAN save long-term memory' not in channel_identity_prompt(turn)


@pytest.mark.parametrize('explicit', [False,True])
def test_actual_agent_responder_and_write_receipt_use_the_same_state(server,tmp_path,monkeypatch,explicit):
    from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
    kb=KnowledgeBase(str(tmp_path/'kb'))
    kb.settings['memory_v4_agent_allow_private']=True
    manager=MemoryManager(str(tmp_path/'chats'))
    manager.legacy_sessions_file=str(tmp_path/'no-legacy.json')
    bridge=make(tmp_path/'bridge',None)
    bridge.settings=config(shared_memory=True,agent_mode=True,web_search_mode='off')
    bridge.store.initialize(7)
    management=QQMemoryManagement(bridge,kb)
    captured=[]
    def post(url,**kwargs):
        captured.append(kwargs['json'])
        return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':{'content':'蓝叶杯，这个名字听清了。'}}]})
    def factory(url,key,settings):
        return OpenAICompatibleAgentStep(url,key,settings,request_post=post)
    async def candidate(*args,**kwargs):
        return [kb.add_memory_candidate('虚构测试茶杯叫蓝叶杯。', title='测试杯名')]
    monkeypatch.setattr(server,'kb_system',kb)
    monkeypatch.setattr(server,'memory_manager',manager)
    monkeypatch.setattr(server,'refresh_daily_activity_memory',lambda:None)
    monkeypatch.setattr(server.companion_router,'check_conflict_for_memory',lambda *a:None)
    monkeypatch.setattr(server,'maybe_create_autonomous_memory_candidates',candidate)
    monkeypatch.setattr(conversation_service,'save_credentials',lambda **kw:None)
    monkeypatch.setattr('saya_app.services.memory_v4.OpenAICompatibleAgentStep',factory)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500,text='unexpected fallback'))) as api:
            monkeypatch.setattr(server,'_get_api_httpx_client',lambda:api)
            responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',
                lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
            responder.memory_management=management
            responder.action_proposals=QQActionProposals(bridge.store)
            bridge.action_proposals=responder.action_proposals
            bridge.store.save_conversation('10000:private:20000:shared',[],'session_qq_save_contract.jsonl')
            responder.configuration_provider=lambda:{'revision':'fixture-revision'}
            text=('记住：' if explicit else '')+'这是M1虚构验收，我的测试茶杯叫蓝叶杯。'
            message,_=normalize_event(event(text,message_id=123),bridge.settings)
            result=await responder(message,[],'session_qq_save_contract.jsonl')
            if explicit:
                assert len(kb.memories)==1 and not kb.memory_candidates
                assert result.committed and '已确认并保存长期记忆' in result and '操作编号' in result
            else:
                assert not kb.memories and len(kb.memory_candidates)==1
                assert kb.memory_candidates[0]['status']=='pending'
                assert kb.memory_candidates[0]['origin']['channel']=='qq'
                proposal=next(n for n in result.notes if isinstance(n,ActionProposal))
                assert '待确认候选，尚未写入长期记忆' in proposal
                assert '要查看完整正文' in proposal
                assert len(proposal)<=240
                with bridge.store.connect() as db:
                    row=db.execute('select state,payload from qq_action_proposals').fetchone()
                assert row[0]=='prepared'
                assert json.loads(row[1])['candidate_id']==kb.memory_candidates[0]['id']
    asyncio.run(run())
    assert len(captured)==(0 if explicit else 1)
    if not explicit:
        system='\n'.join(str(x.get('content','')) for x in captured[0]['messages'] if x['role']=='system')
        assert 'This chat CAN save long-term memory' in system
        assert 'not a commit receipt' in system
