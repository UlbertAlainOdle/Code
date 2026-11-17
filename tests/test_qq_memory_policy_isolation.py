import asyncio
import json
from unittest.mock import patch

import httpx

from core_services import KnowledgeBase,MemoryManager
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.conversation import conversation_service
from test_qq_chat_runtime import server
from test_qq_task_scheduler import make
from test_qq_delivery import event,config


def test_private_policy_change_does_not_cancel_group_without_private_memory(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    memory=kb.add_memory('PRIVATE_POLICY_CANARY',source='manual')
    bridge=make(tmp_path/'bridge',None);bridge.store.initialize(7)
    management=QQMemoryManagement(bridge,kb);captured=[]
    async def model(req):
        captured.append(json.loads(req.content))
        kb.set_memory_use_policy(memory['id'],True)
        data={'choices':[{'delta':{'content':'GROUP_STAYS_VALID'},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',
                    lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test'})
                responder.memory_management=management
                message,_=normalize_event(event('hello',group_id=90000),config(shared_memory=True))
                assert await responder(message,[],'session_qq_group_fixture.jsonl')=='GROUP_STAYS_VALID'
                assert 'PRIVATE_POLICY_CANARY' not in json.dumps(captured)
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(conversation_service,'save_credentials',lambda **kw:None)):
        asyncio.run(run())
