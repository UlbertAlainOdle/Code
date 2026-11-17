import asyncio,json
from unittest.mock import patch
import httpx
import pytest
from fastapi import FastAPI
from core_services import KnowledgeBase,MemoryManager
from saya_app.routers import memories as routes
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_action_proposals import QQActionProposals
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.memory_versions import record_version
from saya_app.services.conversation import conversation_service
from test_qq_chat_runtime import server
from test_qq_task_scheduler import make,submit


@pytest.mark.parametrize('save_enabled',[True,False])
def test_c06_11_disabled_adapter_preserves_legal_edits_old_commands_web_and_restart(server,tmp_path,save_enabled):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    initial=kb.add_memory('ORIGINAL_EDIT_CANARY',source='manual')
    async def run():
        bridge=make(tmp_path/'bridge',None,memory_management_enabled=True)
        management=QQMemoryManagement(bridge,kb);bridge.memory_management=management
        responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',
            lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test-model'},server._qq_memory_action)
        responder.memory_management=management;bridge.responder=responder
        def model(_):
            item={'choices':[{'delta':{'content':'好的。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(item)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                await bridge.start()
                bridge.action_proposals=QQActionProposals(bridge.store)
                responder.action_proposals=bridge.action_proposals
                try:
                    submit(bridge,f"/memory edit {initial['id']} {record_version(initial)} LEGAL_QQ_EDIT",1)
                    await bridge.queue.join()
                    assert kb._find_memory(initial['id'])['content']=='LEGAL_QQ_EDIT'
                    current=bridge.controls()
                    await bridge.update_controls(current['settings']|{'memory_management_enabled':False,
                        'memory_write_enabled':save_enabled},current['revision'])
                    a=kb.add_memory_candidate('For project Orion I use blue index cards to organize notes.',title='Orion',category='project')
                    b=kb.add_memory_candidate('My swimming lessons are held on Wednesday evenings.',title='Swimming',category='schedule')
                    assert a['id']!=b['id']
                    before=len(kb.memories)
                    for mid,text in enumerate(['/memories',f"/memory-save {a['id']}",'记住',f"/memory-ignore {b['id']}",
                        '/remember 我最喜欢清淡的乌龙茶。'],2):
                        submit(bridge,text,mid);await bridge.queue.join()
                    assert any('待确认记忆' in item[1] for item in bridge.client.sent)
                    if save_enabled:
                        assert kb._find_memory_candidate(a['id'])['status']=='confirmed'
                        assert kb._find_memory_candidate(b['id'])['status']=='ignored'
                        assert any('乌龙茶' in m['content'] for m in kb.memories)
                        assert any('已确认并保存长期记忆' in item[1] for item in bridge.client.sent)
                    else:
                        assert len(kb.memories)==before
                        assert kb._find_memory_candidate(a['id'])['status']=='pending'
                        assert kb._find_memory_candidate(b['id'])['status']=='pending'
                        assert any('保存已关闭' in item[1] for item in bridge.client.sent)
                    # Closing the QQ adapter cannot disable the user's web editor.
                    app=FastAPI();app.include_router(routes.router)
                    with patch.object(routes,'kb_system',kb):
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as web:
                            rows=(await web.get('/api/kb/memories')).json()['memories']
                            v=next(m['content_version'] for m in rows if m['id']==initial['id'])
                            result=await web.post('/api/kb/update',json={'id':initial['id'],'expected_version':v,
                                'updates':{'content':'LEGAL_WEB_EDIT_AFTER_ADAPTER_OFF'}})
                            assert result.status_code==200
                    submit(bridge,f"/memory edit {initial['id']} {record_version(kb._find_memory(initial['id']))} FORBIDDEN_OVERWRITE",8)
                    await bridge.queue.join()
                    assert kb._find_memory(initial['id'])['content']=='LEGAL_WEB_EDIT_AFTER_ADAPTER_OFF'
                finally:await bridge.stop()
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
        patch.object(server,'refresh_daily_activity_memory',lambda:None),
        patch.object(server.companion_router,'check_conflict_for_memory',return_value=None),
        patch.object(conversation_service,'save_credentials',lambda **kwargs:None)):
        asyncio.run(run())
    restored=KnowledgeBase(kb.base_dir)
    assert restored._find_memory(initial['id'])['content']=='LEGAL_WEB_EDIT_AFTER_ADAPTER_OFF'
