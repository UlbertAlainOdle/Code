"""Exercise QQ through the real chat generator with a simulated model endpoint."""

import asyncio
import importlib.util
import io
import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.qq_bridge import QQBridge, QQMessage, QQSettings, QQStore, OneBotClient
from saya_app.services.qq_chat import QQChatResponder


@pytest.fixture(scope="module")
def server():
    spec = importlib.util.spec_from_file_location("saya_qq_test_server", Path(__file__).resolve().parents[1] / "server5-22B----.py")
    module = importlib.util.module_from_spec(spec)
    with patch("sys.stdin", io.StringIO("5\n")), patch.dict("os.environ", {"SAYA_STARTUP_MODEL": "api"}):
        spec.loader.exec_module(module)
    return module


def test_web_history_excludes_channel_files_without_removing_shared_qq_history(tmp_path):
    manager = MemoryManager(str(tmp_path / 'chats'))
    manager.legacy_sessions_file = str(tmp_path / 'no_legacy_sessions.json')
    web = 'session_web_fixture.jsonl'
    qq = 'session_qq_fixture.jsonl'
    names = [web, qq, 'session_mc_fixture.jsonl', 'session_saya_unified.jsonl']
    for name in names:
        rows = [{'time': '2026-10-28 12:00:00', 'role': role, 'content': 'boundary_canary'}
                for role in ['user', 'ai']]
        (Path(manager.base_dir) / name).write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
    manager.save_pinned_set(set(names))
    original = {name: (Path(manager.base_dir) / name).read_bytes() for name in names}
    assert [item['id'] for item in manager.get_session_list()] == [web]
    assert [item['id'] for item in manager.search_sessions('boundary_canary')] == [web]
    stats = manager.get_chat_activity_stats(year=2026, month=10)
    assert (stats['total_sessions'], stats['total_messages']) == (1, 2)
    daily = manager.get_daily_conversation_stats('2026-10-28')
    assert daily['session_count'] == daily['conversation_count'] == 1
    assert manager.load_session(qq, make_current=False)  # Owner QQ can still resume.
    assert manager.get_pinned_set() == set(names)
    assert all((Path(manager.base_dir) / name).read_bytes() == data for name, data in original.items())


@pytest.mark.parametrize('endpoint', ['sessions/load', 'sessions/load_full', 'sessions/pin', 'sessions/unpin',
    'sessions/delete-preview', 'sessions/delete', 'sessions/archive-preview', 'sessions/archive',
    'sessions/branch', 'history/reset_to'])
def test_web_session_http_routes_reject_qq_before_any_read_or_mutation(server, tmp_path, endpoint):
    manager = MemoryManager(str(tmp_path / 'chats'))
    qq = 'session_qq_fixture.jsonl'
    manager.save_message('user', 'QQ_PRIVATE_CANARY', session_id=qq)
    original = (Path(manager.base_dir) / qq).read_bytes()
    original_current = manager.current_session_file
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
            response = await client.post('/api/' + endpoint, json={'session_id': qq, 'index': 0, 'up_to_index': 0})
            assert response.status_code == 404, response.text
            assert 'QQ_PRIVATE_CANARY' not in response.text
    with patch.object(server, 'memory_manager', manager), patch.object(server, 'history', ['WEB_ONLY']):
        asyncio.run(run())
        assert server.history == ['WEB_ONLY']
    assert (Path(manager.base_dir) / qq).read_bytes() == original
    assert manager.current_session_file == original_current
    assert not manager.get_pinned_set()


@pytest.mark.parametrize('via', ['session_id', 'context', 'multi_agent'])
def test_web_cannot_continue_a_cached_qq_session(server, tmp_path, via):
    from fastapi import HTTPException
    manager = MemoryManager(str(tmp_path / 'chats'))
    qq = 'session_qq_fixture.jsonl'
    fields = {'context': {'session_id': qq}} if via == 'context' else {'session_id': qq}
    if via == 'multi_agent':
        fields['agent_ids'] = ['saya']
    req = server.ChatWithMotion(user_input='hello', model='api', **fields)
    async def run():
        with pytest.raises(HTTPException) as error:
            await (server.chat_stream(req) if via == 'multi_agent' else server._chat_stream_single(req))
        assert error.value.status_code == 404
    with patch.object(server, 'memory_manager', manager):
        asyncio.run(run())
    assert not list(Path(manager.base_dir).glob('session_*.jsonl'))


def test_ordinary_web_load_pin_branch_and_reset_still_work(server, tmp_path):
    manager = MemoryManager(str(tmp_path / 'chats'))
    web = manager.create_session()
    manager.save_message('user', 'WEB_CANARY', session_id=web)
    manager.save_message('ai', 'WEB_ANSWER', session_id=web)
    with patch.object(server, 'memory_manager', manager), patch.object(server, 'history', []):
        assert server.load_session({'session_id': web})['history']
        assert server.pin_session({'session_id': web})['success']
        assert server.unpin_session({'session_id': web})['success']
        assert server.load_session_full({'session_id': web})['session_id'] == web
        branched = server.branch_session({'session_id': web, 'up_to_index': 1})
        assert branched['copied_count'] == 2
        assert manager._is_web_session_file(branched['session_id'])
        result = server.reset_history_to({'session_id': branched['session_id'], 'index': 1})
        assert result['status'] == 'success' and result['remaining_count'] == 1


@pytest.mark.parametrize('channel',['web','qq'])
def test_selected_search_provider_reaches_real_chat_generator_without_stale_brave_key(server,tmp_path,channel):
    from saya_app.services.web_research.models import ResearchBundle,WebSource
    manager=MemoryManager(str(tmp_path/'chats'));kb=KnowledgeBase(str(tmp_path/'kb'));calls=[]
    credentials={'configured':True,'api_key':'MODEL_FIXTURE','api_url':'https://fixture.invalid/v1','model':'test-model',
        'api_source':'server_file','local_preferred':True,'local_fields':['search_provider','deepseek_search_api_key'],
        'model_routes':{},'search_provider':'deepseek','deepseek_search_api_key':'DEEP_FIXTURE','brave_search_api_key':'BRAVE_STALE'}
    class Research:
        def research(self,*args,**kwargs):
            assert args[2]=='DEEP_FIXTURE'
            return ResearchBundle(query='fixture',performed=True,context_text='PUBLIC_SEARCH_EVIDENCE',
                sources=[WebSource(1,'Fixture','https://example.com/','example.com',status='read',text='Public fixture text')])
    def factory(provider,key,legacy):calls.append((provider,key));return Research()
    def model(request):
        assert ('[System: QQ delivery style]' in request.content.decode()) is (channel == 'qq')
        chunks=[{'choices':[{'delta':{'content':'Public answer [1].'},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}]}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(x)+'\n\n' for x in chunks)+'data: [DONE]\n\n')
    async def no_candidates(*args,**kwargs):return []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server,'_get_api_httpx_client',lambda:client):
                if channel=='qq':
                    bot=QQChatResponder(QQSettings(model='api',owner_ids={'20000'},shared_memory=False),server.ChatWithMotion,
                        server._chat_stream_single,lambda:'api',lambda:dict(credentials))
                    await bot(QQMessage('event','scope','10000','20000','','1','/search fixture',False),[],'session')
                else:
                    req=server.ChatWithMotion(user_input='Search fixture',model='api',api_key='STALE_MODEL',api_url='https://old.invalid',
                        temporary_chat=True,agent_mode=False,recall_mode='off',web_search_mode='force',web_search_api_key='BRAVE_STALE_BODY')
                    response=await server._chat_stream_single(req)
                    async for _ in response.body_iterator:pass
    with (patch.object(server,'memory_manager',manager),patch.object(server,'kb_system',kb),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'credentials',lambda:dict(credentials)),
          patch.object(conversation_service,'save_credentials',lambda **kwargs:None),
          patch('saya_app.services.web_research.providers.research_for_provider',side_effect=factory)):
        asyncio.run(run())
    assert calls==[('deepseek','DEEP_FIXTURE')]


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize('limited', [False, True])
def test_qq_auto_news_runs_search_and_fetch_before_answer_even_with_agent_enabled(server, tmp_path, shared, limited, group):
    from saya_app.services.web_research.orchestrator import WebResearchOrchestrator
    from saya_app.services.web_research.models import SearchResult, PageDocument
    manager = MemoryManager(str(tmp_path / 'chats'))
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    steps, prompts = [], []
    url = 'https://www.reuters.com/world/fixture-news'
    class Search:
        provider = 'deepseek'
        supports_pagination = False
        def search(self, query, key, **kwargs):
            assert key == 'SEARCH_FIXTURE' and '新闻' in query
            steps.append('search')
            if limited:
                return [], 'usage_limit_exceeded'
            return [SearchResult('今日新闻 Fixture', url, 'Public fixture headline', rank=1)], None
    class Reader:
        def fetch(self, value, **kwargs):
            assert value == url
            steps.append('fetch')
            return PageDocument(value, final_url=value, title='Fixture news', text='AUTO_NEWS_WEB_EVIDENCE', status='ok')
    research = WebResearchOrchestrator(search_service=Search(), reader=Reader())
    def factory(provider, key, legacy):
        assert provider == 'deepseek' and key == 'SEARCH_FIXTURE'
        return research
    def model(request):
        if not json.loads(request.content).get('stream',True):
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"mood":"neutral"}'},'finish_reason':'stop'}]})
        steps.append('answer')
        prompts.append(json.loads(request.content))
        text = '搜索失败，请稍后重试。' if limited else '查询到的新闻如下[1]。'
        data = {'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n')
    async def no_candidates(*args, **kwargs):
        return []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server, '_get_api_httpx_client', lambda: client):
                reply = QQChatResponder(QQSettings(model='api', owner_ids={'20000'}, shared_memory=True,
                    agent_mode=True, web_search_mode='auto', recall_mode='off', memory_write_enabled=False),
                    server.ChatWithMotion, server._chat_stream_single, lambda: 'api', lambda: {
                        'configured': True, 'api_key': 'MODEL_FIXTURE', 'api_url': 'https://fixture.invalid/v1',
                        'model': 'test-model', 'search_provider': 'deepseek', 'deepseek_search_api_key': 'SEARCH_FIXTURE'})
                return await reply(QQMessage('auto-news', 'scope', '10000', '20000', '90000' if group else '', '1', '今天有什么新闻吗？', shared),
                    [], 'session_qq_fixture.jsonl')
    with (patch.object(server, 'memory_manager', manager), patch.object(server, 'kb_system', kb),
          patch.object(server, 'refresh_daily_activity_memory', lambda: None),
          patch.object(server, 'maybe_create_autonomous_memory_candidates', no_candidates),
          patch.object(conversation_service, 'save_credentials', lambda **kwargs: None),
          patch('saya_app.services.web_research.providers.research_for_provider', side_effect=factory),
          patch.object(kb, 'run_memory_v4_agent_once', side_effect=AssertionError('Duplicate planner must not run'))):
        answer = asyncio.run(run())
    assert steps == (['search', 'answer'] if limited else ['search', 'fetch', 'answer'])
    if limited:
        assert url not in answer and '联网查询没成功' in answer
        assert 'AUTO_NEWS_WEB_EVIDENCE' not in json.dumps(prompts)
    else:
        assert url not in answer and 'AUTO_NEWS_WEB_EVIDENCE' in json.dumps(prompts)
        assert '[1]' not in answer
    if shared and not group:
        entries = manager.load_session_full('session_qq_fixture.jsonl', make_current=False)
        stored = next(item for item in reversed(entries) if item['role'] == 'ai')
        assert stored['web_research']['performed']
        if not limited:
            assert stored['web_research']['sources'][0]['url'] == url
            assert '[1]' in stored['content']  # Presentation never erases canonical evidence.
            assert 'search_safe_search_unavailable' in stored['web_research']['diagnostics']['warnings']
    assert not any(item['id'].startswith('session_qq_') for item in manager.get_session_list())


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize('control',['MOD_SAYA','MODAL_SAYA'])
def test_real_runtime_memory_boundary_context_and_game_commands(server, tmp_path, shared,control):
    manager = MemoryManager(str(tmp_path / "chats"))
    kb = KnowledgeBase(str(tmp_path / "kb"))
    session = 'session_qq_fixture.jsonl'
    captured = []

    def model(request):
        if not json.loads(request.content).get('stream',True):
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"mood":"happy"}'},'finish_reason':'stop'}]})
        captured.append(json.loads(request.content))
        chunks = [
            {"choices": [{"delta": {"content": '['+control[:3]}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": control[3:]+": affectionate][MOOD_SAYA: happy]这是回答。[MC:attack nearest]"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        text = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text)

    async def no_candidates(*args, **kwargs):
        return []

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server, "_get_api_httpx_client", lambda: client):
                responder = QQChatResponder(QQSettings(model="api", owner_ids=frozenset({"20000"}), shared_memory=shared), server.ChatWithMotion,
                    server._chat_stream_single, lambda: "api",
                    lambda: {"configured": True, "api_key": "fake-key", "api_url": "http://example.invalid/v1", "model": "test-model"})
                message = QQMessage("event", "scope", "10000", "20000", "", "1", "请记住，我喜欢乌龙茶。", shared)
                answer = await responder(message, ["Doctor: CHANNEL_HISTORY_MARKER", 'Saya: ['+control+': affectionate]上一条回答'], session)
                assert answer.startswith('这是回答。') and not any(tag in answer for tag in ['MC:', 'MOD_SAYA', 'MODAL_SAYA', 'MOOD_SAYA'])
                if shared:
                    # Second owner request reloads only its QQ session history.
                    await responder(QQMessage("event2", "scope", "10000", "20000", "", "2", "你好", True), [], session)

    with (patch.object(server, "memory_manager", manager), patch.object(server, "kb_system", kb),
          patch.object(server, "PROFILE_PATH", tmp_path / "missing-profile"),
          patch.object(server, "refresh_daily_activity_memory", lambda: None),
          patch.object(server, "maybe_create_autonomous_memory_candidates", no_candidates),
          patch.object(conversation_service, "save_credentials", lambda **kwargs: None),
          patch.object(server, "journal_service_prompt_context", return_value="JOURNAL_PRIVATE_MARKER") as journal,
          patch.object(server, "bridge_service_prompt_context", return_value="GAME_PRIVATE_MARKER") as game,
          patch.object(kb, "capture_explicit_memory_result", wraps=kb.capture_explicit_memory_result) as capture,
          patch.object(server, "queue_saya_bridge_commands") as commands,
          patch.object(server.motion_ai, 'get_motion_suggestion') as animation,
          patch.object(server.echo_service, "mark_chat_activity") as activity):
        asyncio.run(run())
        commands.assert_not_called()
        animation.assert_not_called()
        game.assert_not_called()
        for payload in captured:
            system = payload['messages'][0]['content']
            assert 'MUST choose exactly one chat MOTION' not in system
            assert 'Use the required MOTION tag' not in system
            assert 'GAME_PRIVATE_MARKER' not in system
            assert all(control not in item['content'] for item in payload['messages'] if item['role']=='assistant')
        activity.assert_not_called()
        if shared:
            assert capture.call_count >= 1
            assert len(manager.load_session_full(session, make_current=False)) == 4
            assert "请记住" in json.dumps(captured[-1], ensure_ascii=False)
        else:
            capture.assert_not_called()
            journal.assert_not_called()
            game.assert_not_called()
            assert manager.load_session_full(session, make_current=False) == []
            payload = json.dumps(captured[0], ensure_ascii=False)
            assert "CHANNEL_HISTORY_MARKER" in payload
            assert "JOURNAL_PRIVATE_MARKER" not in payload and "GAME_PRIVATE_MARKER" not in payload
            assert "conversation is isolated" in payload
            assert "sender IS your configured owner" in payload


@pytest.mark.parametrize('model', ['api', 'mistral_fixture', 'qwen3_fixture', 'saya_r1_q5'])
def test_qq_prompt_removes_only_avatar_instructions_preserving_web_persona(server,tmp_path,model):
    with (patch.object(server,'current_loaded_model_name',model),
          patch.object(server,'current_emotion','angry'),
          patch.object(server,'PROFILE_PATH',tmp_path/'missing'),
          patch.object(server,'bridge_service_prompt_context',return_value='GAME_CONTEXT') as game):
        options=dict(history_context=[],skip_memory=True,memory_context_override='',include_time=False)
        web=server.build_prompt('你好','',**options)
        game.assert_called_once()
        game.reset_mock()
        qq=server.build_prompt('你好','',qq_channel=True,**options)
        game.assert_not_called()
        assert 'GAME_CONTEXT' in web and 'GAME_CONTEXT' not in qq
        assert 'MUST choose exactly one chat MOTION' not in qq
        assert '必须将当前的动作/情感归类' not in qq
        assert 'Use the required MOTION tag' not in qq
        assert 'Use the required MOTION tag' in web
        assert 'Saya Identity Reminder' in qq and 'Attachment intensity:' in qq
        assert 'Memory imagination:' in qq and 'Sociality:' in qq
        assert '[DoctorEmotion]' not in qq
        if model in ['mistral_fixture','qwen3_fixture']:
            assert '[DoctorEmotion]: Doctor feels angry.' in web
        # Mood continuity is internal state, not the avatar transport protocol.
        assert server.build_saya_style_prompt({'attachment_level':'high'},motion_tags=False).replace(
            'No body-animation labels; this channel has no avatar playback.',
            'Use the required MOTION tag normally and avoid unnecessary extra labels.') == server.build_saya_style_prompt({'attachment_level':'high'})


def test_local_prompt_keeps_only_supplied_channel_history(server, tmp_path):
    with (patch.object(server, "current_loaded_model_name", "qwen3_30b_q5"),
          patch.object(server, "PROFILE_PATH", tmp_path / "missing"),
          patch.object(server, "journal_service_prompt_context") as journal,
          patch.object(server, "bridge_service_prompt_context") as game,
          patch.object(server, "history", ["WEB_PRIVATE_HISTORY_MARKER"])):
        isolated = server.build_prompt("问题", "", temporary_chat=True,
                                      channel_history=["Doctor: QQ_HISTORY_MARKER"])
        browser = server.build_prompt("问题", "", temporary_chat=True,
                                     history_context=["BROWSER_PRIVATE_HISTORY_MARKER"])
        assert "QQ_HISTORY_MARKER" in isolated and "WEB_PRIVATE_HISTORY_MARKER" not in isolated
        assert "BROWSER_PRIVATE_HISTORY_MARKER" not in browser
        journal.assert_not_called()
        game.assert_not_called()


@pytest.mark.parametrize('delivery_mode', ['legacy','chat'])
@pytest.mark.parametrize('quoted_text', [None,'QUOTE_ORIGINAL_CANARY：周五下午开会。'])
@pytest.mark.parametrize('group', [False,True])
def test_signed_http_event_through_real_chat_to_onebot_reply(server, tmp_path, delivery_mode, quoted_text, group):
    from fastapi import FastAPI
    from saya_app.routers import qq as routes

    config = QQSettings(enabled=True, bot_id="10000", access_token="outbound", webhook_secret="inbound",
                        allowed_users=frozenset({"20000"}), allowed_groups={'90000'}, model="api", delivery_mode=delivery_mode,
                        bubble_strategy='natural')
    sent = []
    fetched = []
    captured_prompts = []

    def model(request):
        if not json.loads(request.content).get('stream',True):
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"mood":"curious"}'},'finish_reason':'stop'}]})
        captured_prompts.append(json.loads(request.content))
        chunks=[{'choices':[{'delta':{'reasoning_content':'PRIVATE_REASONING','content':text},
                            'finish_reason':'stop' if index==1 else None}]}
                for index,text in enumerate(['[MOD_', 'SAYA: affectionate]QQ 闭环回答'])]
        return httpx.Response(200, text=''.join('data: '+json.dumps(data)+'\n\n' for data in chunks)+'data: [DONE]\n\n')

    def onebot(request):
        if request.url.path=='/get_msg':
            fetched.append(json.loads(request.content))
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':{
                'self_id':10000,'message_type':'group' if group else 'private','message_id':77,
                'user_id':30000 if group else 20000,
                **({'group_id':90000} if group else {}),
                'message':[{'type':'text','data':{'text':quoted_text}}]}})
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 88}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_client:
            wire = OneBotClient(config)
            await wire.http.aclose()
            wire.http = httpx.AsyncClient(transport=httpx.MockTransport(onebot))
            responder = QQChatResponder(config, server.ChatWithMotion, server._chat_stream_single, lambda: "api",
                lambda: {"configured": True, "api_key": "fake", "api_url": "http://mock/v1", "model": "test-model"})
            bridge = QQBridge(config, QQStore(tmp_path / "qq.sqlite3"), responder, wire)
            app = FastAPI()
            app.include_router(routes.router)
            await bridge.start()
            scope='10000:group:90000:user:20000:isolated' if group else '10000:private:20000:isolated'
            if group and quoted_text:
                bridge.store.save_conversation(scope,['Doctor: 先前的问题','Saya: 我看不到引用，请贴原文。'],'session_qq_stale_quote.jsonl')
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    import time
                    segments=[{"type": "text", "data": {"text": "你好"}}]
                    if quoted_text:segments.insert(0,{'type':'reply','data':{'id':'77'}})
                    if group:segments.insert(0,{'type':'at','data':{'qq':'10000'}})
                    raw = json.dumps(dict(time=int(time.time()), self_id=10000, user_id=20000,
                        post_type="message", message_type="group" if group else "private", sub_type="friend", message_id=10,
                        **({'group_id':90000} if group else {}),
                        message=segments)).encode()
                    signature = "sha1=" + hmac.new(b"inbound", raw, hashlib.sha1).hexdigest()
                    with patch.object(routes, "qq_bridge", bridge), patch.object(server, "_get_api_httpx_client", lambda: model_client):
                        ack = await client.post("/api/qq/events", content=raw, headers={"X-Signature": signature})
                        assert ack.json()["status"] == "queued"
                        await asyncio.wait_for(bridge.queue.join(), 10)
                        assert (await client.get("/api/qq/status")).json()["counts"] == {"sent": 1}
                        saved,_=bridge.store.conversation(scope)
                        assert saved[-2]=='Doctor: 你好' and 'QUOTE_ORIGINAL_CANARY' not in saved[-2]
                        # Inline MOD_SAYA says affectionate; only the separate
                        # validated curious metadata may drive the mood state.
                        with bridge.store.connect() as db:
                            affect=json.loads(db.execute('select state from qq_affect').fetchone()[0])
                            observation=tuple(db.execute('select status,label from qq_mood_observations').fetchone())
                        assert affect['curiosity']>40 and affect['affection']==50
                        assert observation==('ok','curious')
            finally:
                await bridge.stop()

    with (patch.object(server, "memory_manager", MemoryManager(str(tmp_path / "chats"))),
          patch.object(server, "kb_system", KnowledgeBase(str(tmp_path / "kb"))),
          patch.object(server, "PROFILE_PATH", tmp_path / "profile")):
        asyncio.run(run())
    assert len(captured_prompts) == 1
    assert 'Include one accurate [MOOD_SAYA:' not in json.dumps(captured_prompts,ensure_ascii=False)
    assert ('ordinary blank lines' in captured_prompts[0]['messages'][0]['content']) is (delivery_mode=='chat')
    assert fetched==([{'message_id':'77'}] if quoted_text else [])
    if quoted_text:
        payload=json.dumps(captured_prompts[0],ensure_ascii=False)
        assert quoted_text in payload and 'untrusted reference content' in payload
        current=captured_prompts[0]['messages'][-1]
        assert current['role']=='user' and quoted_text in current['content']
        assert current['content'].endswith('你好')
        assert quoted_text not in captured_prompts[0]['messages'][0]['content']
        if group:
            assert any('请贴原文' in value['content'] for value in captured_prompts[0]['messages'][:-1] if value['role']=='assistant')
    if group:
        segments=([{'type':'reply','data':{'id':'10'}}] if delivery_mode=='chat' else [])+[
            {'type':'at','data':{'qq':'20000'}},{'type':'text','data':{'text':' '}},
            {'type':'text','data':{'text':'QQ 闭环回答'}}]
        assert sent==[('/send_group_msg',{'group_id':90000,'message':segments})]
    elif delivery_mode == 'legacy':
        assert sent == [("/send_private_msg", {"user_id": 20000, "message": [{"type": "text", "data": {"text": "QQ 闭环回答"}}]})]
    else:
        assert sent == [("/send_private_msg", {"user_id": 20000, "message": [
            {"type":"reply", "data":{"id":"10"}}, {"type":"text", "data":{"text":"QQ 闭环回答"}}]})]


@pytest.mark.parametrize('local_model',['mistral_fixture','saya_r1_q5'])
def test_local_quote_context_is_after_stale_history_before_current_question(server,tmp_path,local_model):
    from saya_app.services.qq_quotes import current_turn_context
    reference=current_turn_context({'status':'ready','text':'LOCAL_QUOTE_CANARY','sender_id':'30000'})
    with patch.object(server,'current_loaded_model_name',local_model),patch.object(server,'PROFILE_PATH',tmp_path/'profile'):
        prompt=server.build_prompt('CURRENT_QUESTION','',temporary_chat=True,include_time=False,
            channel_history=['Doctor: old','Saya: STALE_CANNOT_READ'],turn_reference=reference)
    assert prompt.index('STALE_CANNOT_READ')<prompt.index('LOCAL_QUOTE_CANARY')<prompt.index('Doctor: CURRENT_QUESTION')


@pytest.mark.parametrize('limited',[False,True])
@pytest.mark.parametrize('request_sources',[False,True])
def test_qq_forced_search_preserves_real_web_evidence_in_model_prompt(server, tmp_path,limited,request_sources):
    from saya_app.services.web_research.models import ResearchBundle, WebSource
    captured = []
    def model(request):
        captured.append(json.loads(request.content))
        data = {"choices": [{"delta": {"content": "查询结果[1]。"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")
    bundle = ResearchBundle(query="新闻", performed=True, context_text="WEB_EVIDENCE_MARKER: 来源[1]提供的事实。",
        sources=[WebSource(1, "测试来源", "https://example.org/evidence", "example.org", status="read")])
    if limited:
        bundle=ResearchBundle(query='新闻',performed=True,context_text='No usable web evidence. Do not invent sources.',
            warnings=['search_failed','search_usage_limit_exceeded'])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server, "_get_api_httpx_client", lambda: client):
                responder = QQChatResponder(QQSettings(model="api", owner_ids={"20000"}), server.ChatWithMotion,
                    server._chat_stream_single, lambda: "api", lambda: {"configured": True, "api_key": "fake",
                        "api_url": "http://mock/v1", "model": "test-model", "brave_search_api_key": "fake-search"})
                question = '/search 新闻，附上来源链接' if request_sources else '/search 新闻'
                answer = await responder(QQMessage("k", "scope", "10000", "20000", "", "1", question, False), [], "unused")
                if limited:
                    assert '联网查询没成功' in answer and 'HTTP 402' not in answer
                    assert 'https://example.org/evidence' not in answer
                else:
                    assert ("https://example.org/evidence" in answer) is request_sources
    with (patch.object(server, "memory_manager", MemoryManager(str(tmp_path / "chats"))),
          patch.object(server, "kb_system", KnowledgeBase(str(tmp_path / "kb"))),
          patch.object(server.web_research_service, "research", return_value=bundle) as search,
          patch.object(server, "queue_saya_bridge_commands") as game):
        asyncio.run(run())
        search.assert_called_once()
        game.assert_not_called()
    payload = json.dumps(captured, ensure_ascii=False)
    if not limited:assert "WEB_EVIDENCE_MARKER" in payload
    assert "sender IS your configured owner" in payload


@pytest.mark.parametrize("write_enabled", [True, False])
def test_qq_explicit_memory_save_uses_web_write_pipeline_and_returns_receipt(server, tmp_path, write_enabled):
    kb = KnowledgeBase(str(tmp_path / "kb"))
    manager = MemoryManager(str(tmp_path / "chats"))
    session = manager.create_session()
    def model(request):
        data = {"choices": [{"delta": {"content": "好的。"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server, "_get_api_httpx_client", lambda: client):
                responder = QQChatResponder(QQSettings(enabled=True, bot_id="10000", webhook_token="fixture-inbound", access_token="fixture-outbound", model="api", owner_ids={"20000"}, shared_memory=True, memory_write_enabled=write_enabled),
                    server.ChatWithMotion, server._chat_stream_single, lambda: "api", lambda: {"configured": True,
                        "api_key": "fake", "api_url": "http://mock/v1", "model": "test-model"})
                answer = await responder(QQMessage("k", "scope", "10000", "20000", "", "1", "请记住，我最喜欢乌龙茶。", True), [], session)
                assert ("已保存长期记忆" in answer) is write_enabled
    with (patch.object(server, "kb_system", kb), patch.object(server, "memory_manager", manager),
          patch.object(server, "refresh_daily_activity_memory", lambda: None),
          patch.object(conversation_service, "save_credentials", lambda **kwargs: None),
          patch.object(server.companion_router, "check_conflict_for_memory", return_value=None)):
        asyncio.run(run())
    assert any("乌龙茶" in str(item.get("content")) for item in kb.memories) is write_enabled
    assert ("乌龙茶" in kb.build_memory_v4_context_with_audit("乌龙茶")["context"]) is write_enabled


@pytest.mark.parametrize('with_quote',[False,True])
def test_qq_agent_really_queries_shared_memory_with_only_approved_tools(server, tmp_path, with_quote):
    from types import SimpleNamespace
    from saya_app.services.memory_v4 import OpenAICompatibleAgentStep
    kb = KnowledgeBase(str(tmp_path / "kb"))
    kb.update_settings({"memory_v4_agent_allow_private": True})
    kb.capture_explicit_memory("记住：我最喜欢乌龙茶。")
    manager = MemoryManager(str(tmp_path / "chats"))
    session = manager.create_session()
    posted = []
    trace = []
    def post(url, **kwargs):
        body = kwargs["json"]
        posted.append(body)
        names = {tool["function"]["name"] for tool in body["tools"]}
        query_name = next(name for name in names if name.startswith("memory_query_"))
        assert not any("connector" in name or "game" in name for name in names)
        assert not any(name.startswith("web_") for name in names)
        if len(posted) == 1:
            output = {"content": "", "reasoning_content": "HIDDEN_AGENT_REASONING", "tool_calls": [
                {"id": "query-call", "type": "function", "function": {"name": query_name, "arguments": '{"query":"乌龙茶"}'}}]}
        else:
            assert "乌龙茶" in json.dumps(body["messages"], ensure_ascii=False)
            assert any("乌龙茶" in str(msg.get("content")) for msg in body["messages"] if msg["role"] == "tool")
            output = {"content": "[MOD_SAYA: affectionate]我记得你喜欢乌龙茶。[MC:attack nearest]", "reasoning_content": "PRIVATE_FINAL"}
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": output}]})
    def factory(url, key, settings):
        assert settings.thinking_effort == "high" and settings.context_chars == 64000
        return OpenAICompatibleAgentStep(url, key, settings, request_post=post)
    async def no_candidates(*args, **kwargs):
        return []
    async def run():
        async def single(req, channel_turn):
            response = await server._chat_stream_single(req, channel_turn=channel_turn)
            original = response.body_iterator
            async def spy():
                async for chunk in original:
                    if any(word in str(chunk).lower() for word in ("failed", "error", "answer_complete")):
                        trace.append(str(chunk)[:1500])
                    yield chunk
            response.body_iterator = spy()
            return response
        responder = QQChatResponder(QQSettings(model="api", owner_ids={"20000"}, shared_memory=True,
            agent_mode=True, web_search_mode="off", thinking_effort="high", memory_token_budget=16000),
            server.ChatWithMotion, single, lambda: "api", lambda: {"configured": True,
                "api_key": "fake", "api_url": "https://api.deepseek.com", "model": "deepseek-flash"})
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(500, text="unexpected fallback"))) as fallback:
                with patch.object(server, "_get_api_httpx_client", lambda: fallback):
                    incoming=QQMessage("k", "scope", "10000", "20000", "", "1", "还记得我喜欢喝什么？", True)
                    if with_quote:
                        incoming.quote_context={'status':'ready','sender_id':'20000','text':'AGENT_QUOTE_CANARY：乌龙茶','truncated':False}
                    answer = await responder(incoming, [], session)
        except RuntimeError:
            raise AssertionError(trace) from None
        assert "乌龙茶" in answer and "HIDDEN_AGENT" not in answer and "MC:" not in answer and 'MOD_SAYA' not in answer
    with (patch.object(server, "kb_system", kb), patch.object(server, "memory_manager", manager),
          patch("saya_app.services.memory_v4.OpenAICompatibleAgentStep", side_effect=factory),
          patch.object(server, "refresh_daily_activity_memory", lambda: None),
          patch.object(server, "maybe_create_autonomous_memory_candidates", no_candidates),
          patch.object(server, "log_to_file", side_effect=trace.append),
          patch.object(conversation_service, "save_credentials", lambda **kwargs: None),
          patch.object(server, "queue_saya_bridge_commands") as game):
        asyncio.run(run())
        game.assert_not_called()
    assert len(posted) == 2
    if with_quote:
        user_messages=[item for item in posted[0]['messages'] if item['role']=='user']
        assert 'AGENT_QUOTE_CANARY' in user_messages[-1]['content']
        assert user_messages[-1]['content'].endswith('还记得我喜欢喝什么？')
        assert 'AGENT_QUOTE_CANARY' not in posted[0]['messages'][0]['content']


def test_web_global_defaults_are_opt_in_and_snapshot_does_not_change_mid_turn(server, tmp_path):
    from saya_app.services.shared_chat_config import SharedChatConfigStore
    store = SharedChatConfigStore(tmp_path / "shared.json")
    store.save({"thinking_effort":"none", "max_output_tokens":1024, "recall_mode":"off", "memory_write_enabled":False},
        True, store.load()["revision"])
    captured = []
    manager = MemoryManager(str(tmp_path / 'chats'))
    before_history = {path.name:path.read_bytes() for path in (tmp_path/'chats').glob('*.jsonl')}
    def model(request):
        captured.append((str(request.url), json.loads(request.content)))
        data = {"choices":[{"delta":{"content":"回答"},"finish_reason":"stop"}]}
        return httpx.Response(200,text="data: "+json.dumps(data)+"\n\ndata: [DONE]\n\n")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server,"_get_api_httpx_client",lambda:client):
                request = server.ChatWithMotion(user_input="你好",model="api",api_key="browser-fake",
                    api_url="http://browser/v1",api_model_name="browser-model",temporary_chat=True)
                response = await server._chat_stream_single(request)
                old_revision = store.load()["revision"]
                store.save({"thinking_effort":"auto", "max_output_tokens":2048},True,old_revision)
                events = "".join([chunk async for chunk in response.body_iterator])
                assert old_revision in events and '"inherited": true' in events
    with (patch("saya_app.services.shared_chat_config.shared_chat_config",store),
          patch.object(conversation_service,"credentials",lambda:{"configured":True,"api_key":"server-fake",
            "api_url":"https://api.deepseek.com","model":"deepseek-flash","brave_search_api_key":""}),
          patch.object(server,"memory_manager",manager),
          patch.object(server,"kb_system",KnowledgeBase(str(tmp_path/'kb')))):
        asyncio.run(run())
    assert captured[0][0].startswith("https://api.deepseek.com/")
    assert captured[0][1]["thinking"] == {"type":"disabled"}
    assert captured[0][1]["max_tokens"] == 1024
    assert {path.name:path.read_bytes() for path in (tmp_path/'chats').glob('*.jsonl')} == before_history


def test_local_profile_precedes_browser_without_mutating_disk_and_freezes_request(server,tmp_path,monkeypatch):
    from saya_app.services import conversation
    from saya_app.services.local_api_profile import LocalApiProfile
    from saya_app.services.shared_chat_config import SharedChatConfigStore
    for name in ('API_KEY','API_URL','API_MODEL','BRAVE_SEARCH_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    path=tmp_path/'credentials.json';monkeypatch.setattr(conversation,'CREDENTIALS_FILE',path)
    profile=LocalApiProfile(path)
    first=profile.save({'api_key':'local-fake','api_url':'http://local.invalid/v1','api_model_name':'local-model',
        'multimodal_model_name':'local-vision','brave_search_api_key':''},profile.status()['revision'])
    captured=[]
    def model(request):
        captured.append((str(request.url),request.headers['authorization'],json.loads(request.content)))
        data={'choices':[{'delta':{'content':'回答'},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
            with patch.object(server,'_get_api_httpx_client',lambda:client):
                req=server.ChatWithMotion(user_input='你好',model='api',api_key='browser-fake',api_url='http://browser.invalid',
                    api_model_name='browser-model',temporary_chat=True,recall_mode='off',web_search_mode='off')
                response=await server._chat_stream_single(req)
                profile.save({'api_key':'next-fake','api_url':'http://next.invalid','api_model_name':'next-model'},
                    first['revision'],overwrite=True)
                saved=path.read_bytes()
                async for _ in response.body_iterator:pass
                assert path.read_bytes()==saved
                # Turning off the shared preference explicitly restores the browser bundle.
                profile.save({},profile.status()['revision'],prefer_local=False)
                response=await server._chat_stream_single(req)
                async for _ in response.body_iterator:pass
                profile.save({},profile.status()['revision'],prefer_local=True,clear_fields=['api_key'])
                with pytest.raises(server.HTTPException) as unavailable:
                    await server._chat_stream_single(req)
                assert unavailable.value.status_code==400  # Explicit local clear cannot revive an old browser key.
    with (patch('saya_app.services.shared_chat_config.shared_chat_config',SharedChatConfigStore(tmp_path/'shared.json')),
          patch.object(server,'memory_manager',MemoryManager(str(tmp_path/'chats'))),
          patch.object(server,'kb_system',KnowledgeBase(str(tmp_path/'kb')))):
        asyncio.run(run())
    assert captured[0][0].startswith('http://local.invalid/')
    assert captured[0][1]=='Bearer local-fake' and captured[0][2]['model']=='local-model'
    assert captured[1][0].startswith('http://browser.invalid/')
    assert captured[1][1]=='Bearer browser-fake' and captured[1][2]['model']=='browser-model'


def test_web_http_chat_without_window_owner_is_rejected_before_generation(server):
    from types import SimpleNamespace
    from saya_app.services.web_window import WebWindowManager
    manager=WebWindowManager();owner=manager.claim()
    async def run():
        request=server.ChatWithMotion(user_input='fixture',model='api')
        for token in ['', 'old-window-token']:
            incoming=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(web_window=manager)),headers={'X-Saya-Web-Window':token})
            with pytest.raises(server.HTTPException) as error:
                await server.chat_stream(request,http_request=incoming)
            assert error.value.status_code==409
    with patch.object(server,'_chat_stream_single') as generate:
        asyncio.run(run());generate.assert_not_called()
    manager.release(owner)
