import asyncio
import json
from unittest.mock import patch
import httpx
import pytest
from core_services import KnowledgeBase,MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.qq_chat import QQChatResponder
from test_qq_chat_runtime import server
from test_qq_topic_routing import topics,item,create


@pytest.mark.parametrize('late_binding',[False,True])
def test_real_generator_archives_response_dependencies_without_user_overwrite(server,tmp_path,late_binding):
    manager=MemoryManager(str(tmp_path/'chats'));kb=KnowledgeBase(str(tmp_path/'kb'))
    session='session_qq_archive_dependencies.jsonl';calls=[]
    def model(request):
        calls.append(json.loads(request.content))
        data={'choices':[{'delta':{'content':'观测范围取决于可到达的光信号。'},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
    async def no_candidates(*args,**kwargs):return []
    async def run():
        async with topics(tmp_path/'bridge') as (b,s,now,msg,choice,prompts):
            s.topics.dialogue.configure('20000',True)
            settings=b.settings.model_copy(update={'model':'api','agent_mode':False,'web_search_mode':'off','recall_mode':'off','memory_write_enabled':False})
            responder=QQChatResponder(settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid/v1','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
                with patch.object(server,'_get_api_httpx_client',lambda:client):
                    first=msg('虚构星雾边界为什么难以观察？')
                    choice[0]={'items':[]} if late_binding else {'items':[{**item(first.text,personal=create(),mode='question',subject='general'),'topic_anchor':'虚构星雾边界'}]}
                    await s.observe(first,[]);await responder(first,[],session)
                    if late_binding:
                        current=msg('刚才那段解释让我想到另一个阅读笔记整理问题。');choice[0]={'items':[]}
                        await s.observe(current,[]);await responder(current,[],session)
                    else:current=first
            archive=manager.load_session_full(session,make_current=False)
            response=current.event_provenance_response;user=current.event_provenance_user
            assert response!=user # The distinction is the contract under test.
            saved_user=next(x for x in reversed(archive) if x['role']=='user')
            saved_reply=next(x for x in reversed(archive) if x['role']=='ai')
            assert saved_user['qq_event_dependencies']==user
            assert saved_reply['qq_event_dependencies']==response
            assert saved_reply['qq_event_dependencies']['version']==2
            assert saved_reply['chat_runtime']['session_id']==session and saved_reply.get('qq_context')
            assert len(calls)==(2 if late_binding else 1)
            before=(tmp_path/'chats'/session).read_bytes()
            if late_binding:
                source=next(x for x in first.event_provenance_user['refs'] if x['kind']=='dialogue')
                follow=msg('虚构星雾边界后续：我想继续理解观测条件。')
                choice[0]={'items':[{**item(follow.text,personal=create(),mode='opinion',subject='general'),'topic_anchor':'虚构星雾边界','context_sources':[source['id']]}]}
                await s.observe(follow,[]);card=s.cards(follow)[0]
                assert len(card['facts'])==2 and s.provenance.valid(current,response)
            else:card=s.cards(first)[0]
            s.command(msg('/events stop '+card['id']))
            assert not s.provenance.valid(current,saved_reply['qq_event_dependencies'])
            if late_binding:assert s.provenance.valid(current,saved_user['qq_event_dependencies'])
            assert (tmp_path/'chats'/session).read_bytes()==before
    with (patch.object(server,'memory_manager',manager),patch.object(server,'kb_system',kb),
          patch.object(server,'PROFILE_PATH',tmp_path/'missing-profile'),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'save_credentials',lambda **kwargs:None)):
        asyncio.run(run())
