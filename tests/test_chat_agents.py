import asyncio
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saya_app.routers import chat_agents as routes
from saya_app.services.chat_agents import AgentStore, history_for_agent, persona_prompt
from saya_app.services import multi_agent_chat, conversation_mood


def test_agent_settings_crud_and_validation(tmp_path):
    store = AgentStore(tmp_path / "agents.json")
    app = FastAPI()
    app.include_router(routes.router)
    with patch.object(routes, "agent_store", store), TestClient(app) as client:
        assert client.get('/api/chat-agents').json()['agents'][0]['id'] == 'saya'
        created = client.post('/api/chat-agents', json={'name': '编辑', 'prompt': '你是一位编辑。'}).json()
        agent_id = created['id']
        assert AgentStore(store.path).resolve([agent_id, agent_id])[0]['prompt'] == '你是一位编辑。'
        assert client.post('/api/chat-agents', json={'name': 'sAyA', 'prompt': 'test'}).status_code == 400
        assert client.post('/api/chat-agents', json={'name': '编辑', 'prompt': 'test'}).status_code == 400
        for name in ['has space', '@agent', '<script>']:
            assert client.post('/api/chat-agents', json={'name': name, 'prompt': 'test'}).status_code == 400
        assert client.put('/api/chat-agents/' + agent_id, json={'name': '评审', 'prompt': '给出批评'}).status_code == 200
        assert store.resolve([agent_id])[0]['name'] == '评审'
        assert client.delete('/api/chat-agents/saya').status_code == 400
        assert client.delete('/api/chat-agents/' + agent_id).status_code == 200
        assert client.delete('/api/chat-agents/' + agent_id).status_code == 404
        with pytest.raises(ValueError):
            store.resolve([agent_id])


def test_history_and_custom_prompt_are_isolated():
    items = [dict(role='user', content='你好'), dict(role='ai', content='Saya 的回答'),
             dict(role='ai', content='编辑的回答', agent_id='editor'),
             dict(role='ai', content='导师的回答', agent_id='teacher')]
    assert history_for_agent(items, 'editor') == ['Doctor: 你好', 'Saya: 编辑的回答']
    assert history_for_agent(items, 'saya') == ['Doctor: 你好', 'Saya: Saya 的回答']
    prompt = persona_prompt(dict(name='编辑', prompt='只给修改建议'), '问题', [], local=False)
    assert '只给修改建议' in prompt
    assert 'Saya' not in prompt
    local = persona_prompt(dict(name='编辑', prompt='只给修改建议'), '问题', ['Saya: 先前回答'], local=True)
    assert '编辑: 先前回答' in local and local.endswith('编辑:')
    assert 'Saya:' not in local
    happy = conversation_mood.advance(conversation_mood.neutral(), 'happy')
    projected = conversation_mood.items_with_snapshots([
        dict(role='ai', mood_state=happy), dict(role='ai', agent_id='editor', mood='sad'), dict(role='ai')])
    assert projected[-1]['mood_state']['happiness'] == conversation_mood.advance(happy, None)['happiness']
    assert 'mood_state' not in projected[1]


@pytest.fixture(scope='module')
def server():
    spec = importlib.util.spec_from_file_location('saya_agents_test_server', Path(__file__).resolve().parents[1] / 'server5-22B----.py')
    module = importlib.util.module_from_spec(spec)
    with patch('sys.stdin', io.StringIO('5\n')), patch.dict('os.environ', {'SAYA_STARTUP_MODEL': 'api'}):
        spec.loader.exec_module(module)
    return module


def parse_events(stream):
    events = []
    for block in stream.split('\n\n'):
        lines = block.splitlines()
        name = next((line[6:].strip() for line in lines if line.startswith('event:')), '')
        data = next((line[5:].strip() for line in lines if line.startswith('data:')), '')
        if data:
            events.append((name, json.loads(data)))
    return events


@pytest.mark.parametrize('temporary', [False, True])
def test_real_stream_dispatch_prompts_persistence_and_default_saya(server, tmp_path, temporary):
    from core_services import MemoryManager, KnowledgeBase
    from saya_app.services.conversation import conversation_service

    store = AgentStore(tmp_path / 'agents.json')
    editor = store.save('编辑', 'EDITOR_ONLY_MARKER: 只做文字编辑。')
    teacher = store.save('导师', 'TEACHER_ONLY_MARKER: 分步骤讲解。')
    manager = MemoryManager(str(tmp_path / 'chats'))
    knowledge = KnowledgeBase(str(tmp_path / 'kb'))
    session_id = manager.create_session()
    captured = []

    def provider(request):
        payload = json.loads(request.content)
        captured.append(payload)
        text = f"回复{len(captured)}"
        chunk = {'choices': [{'delta': {'content': text}, 'finish_reason': None}]}
        end = {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text=f'data: {json.dumps(chunk)}\n\ndata: {json.dumps(end)}\n\ndata: [DONE]\n\n')

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with patch.object(server, '_get_api_httpx_client', lambda: client):
                req = server.ChatWithMotion(
                    user_input='讨论一下', display_input='@编辑 @导师 @Saya 讨论一下',
                    agent_ids=[editor['id'], teacher['id'], 'saya', editor['id']],
                    session_id=session_id, model='api', api_key='test-key',
                    api_url='https://example.invalid/v1', api_model_name='test-model',
                    recall_mode='off', thinking_effort='none', temporary_chat=temporary,
                )
                response = await server.chat_stream(req)
                chunks = [chunk async for chunk in response.body_iterator]
                if not temporary:
                    # Ordinary Saya turns must not see custom replies as Saya's own.
                    followup = req.model_copy(update={'agent_ids': [], 'user_input': '再聊聊', 'display_input': '再聊聊'})
                    response = await server.chat_stream(followup)
                    _ = [chunk async for chunk in response.body_iterator]
                return parse_events(''.join(chunks))

    async def no_candidates(*args, **kwargs):
        return []

    with (patch.object(multi_agent_chat, 'agent_store', store),
          patch.object(server, 'memory_manager', manager), patch.object(server, 'kb_system', knowledge),
          patch.object(server, 'refresh_daily_activity_memory', lambda: None),
          patch.object(server, 'maybe_create_autonomous_memory_candidates', no_candidates),
          patch.object(conversation_service, 'save_credentials', lambda **kwargs: None),
          patch.object(server.assistant_role_manager, 'add_session') as assistant_register,
          patch.object(server, 'queue_saya_bridge_commands')):
        events = asyncio.run(run())
        assistant_register.assert_not_called()
    assert [d['name'] for e, d in events if e == 'persona_start'] == ['编辑', '导师', 'Saya']
    assert len([e for e, _ in events if e == 'answer_complete']) == 3
    assert len(captured) == (3 if temporary else 4)
    assert 'EDITOR_ONLY_MARKER' in captured[0]['messages'][0]['content']
    assert 'TEACHER_ONLY_MARKER' not in captured[0]['messages'][0]['content']
    assert 'TEACHER_ONLY_MARKER' in captured[1]['messages'][0]['content']
    assert 'EDITOR_ONLY_MARKER' not in captured[1]['messages'][0]['content']
    assert all(len(p['messages']) >= 2 for p in captured)
    saved = manager.load_session_full(session_id, make_current=False)
    if temporary:
        assert saved == []
    else:
        assert [item['role'] for item in saved] == ['user', 'ai', 'ai', 'ai', 'user', 'ai']
        assert [item['agent_id'] for item in saved[1:4]] == [editor['id'], teacher['id'], 'saya']
        assert '回复1' not in json.dumps(captured[-1], ensure_ascii=False)
        assert '回复2' not in json.dumps(captured[-1], ensure_ascii=False)
        assert '回复3' in json.dumps(captured[-1], ensure_ascii=False)
        assert next(d for e, d in events if e == 'personas_complete')['history_count'] == 4
        assert server.load_session_full({'session_id': session_id})['role_mode'] == ''


def test_one_failed_persona_does_not_cancel_remaining(server, tmp_path):
    from core_services import MemoryManager
    store = AgentStore(tmp_path / 'agents.json')
    a = store.save('甲', '提示词')
    b = store.save('乙', '提示词')
    manager = MemoryManager(str(tmp_path / 'chats'))
    session_id = manager.create_session()
    invoked = []

    async def fail(req, turn):
        invoked.append(turn['agent']['id'])
        raise RuntimeError('provider unavailable')

    async def run():
        req = server.ChatWithMotion(user_input='hello', session_id=session_id, agent_ids=[a['id'], b['id']])
        response = await multi_agent_chat.multi_agent_response(req, manager, fail, server.normalize_chat_attachments)
        return ''.join([chunk async for chunk in response.body_iterator])

    with patch.object(multi_agent_chat, 'agent_store', store):
        events = parse_events(asyncio.run(run()))
    assert invoked == [a['id'], b['id']]
    assert len([e for e, _ in events if e == 'stream_error']) == 2
    assert [i['role'] for i in manager.load_session_full(session_id, make_current=False)] == ['user', 'ai', 'ai']


def test_cancel_after_partial_text_saves_only_started_persona(server, tmp_path):
    from core_services import MemoryManager
    from fastapi.responses import StreamingResponse
    store = AgentStore(tmp_path / 'agents.json')
    a = store.save('甲', '提示词')
    b = store.save('乙', '提示词')
    manager = MemoryManager(str(tmp_path / 'chats'))
    session_id = manager.create_session()
    invoked = []

    async def partial(req, turn):
        agent = turn['agent']
        invoked.append(agent['id'])
        async def gen():
            try:
                yield multi_agent_chat.event('text', {'text': 'partial'})
                await asyncio.sleep(10)
            finally:
                manager.save_message('ai', 'partial', metadata={'agent_id': agent['id'], 'agent_name': agent['name']}, session_id=session_id)
        return StreamingResponse(gen())

    async def run():
        req = server.ChatWithMotion(user_input='hello', session_id=session_id, agent_ids=[a['id'], b['id']])
        response = await multi_agent_chat.multi_agent_response(req, manager, partial, server.normalize_chat_attachments)
        async for chunk in response.body_iterator:
            if 'event: text' in chunk:
                break
        await response.body_iterator.aclose()
        lock = await multi_agent_chat.chat_runtime_manager.session_lock(session_id)
        assert not lock.locked()

    with patch.object(multi_agent_chat, 'agent_store', store):
        asyncio.run(run())
    assert invoked == [a['id']]
    assert [i['content'] for i in manager.load_session_full(session_id, make_current=False)] == ['hello', 'partial']


def test_local_persona_uses_own_prompt_without_saya_actions(server, tmp_path):
    from core_services import MemoryManager, KnowledgeBase
    store = AgentStore(tmp_path / 'agents.json')
    editor = store.save('编辑', '只给修改建议 LOCAL_EDITOR_MARKER')
    manager = MemoryManager(str(tmp_path / 'chats'))
    knowledge = KnowledgeBase(str(tmp_path / 'kb'))
    session_id = manager.create_session()
    prompts = []

    def local_llama(prompt, **kwargs):
        prompts.append(prompt)
        yield {'choices': [{'text': '建议修改[MOTION:happy][MC:follow Doctor]', 'finish_reason': 'stop'}]}

    async def run():
        req = server.ChatWithMotion(user_input='这句话通顺吗', session_id=session_id,
                                   model='mistral_22b_q6', agent_ids=[editor['id']])
        response = await server.chat_stream(req)
        return parse_events(''.join([chunk async for chunk in response.body_iterator]))

    with (patch.object(multi_agent_chat, 'agent_store', store),
          patch.object(server, 'memory_manager', manager), patch.object(server, 'kb_system', knowledge),
          patch.object(server, 'get_model', lambda name: local_llama),
          patch.object(server, 'refresh_daily_activity_memory', lambda: None),
          patch.object(server, 'queue_saya_bridge_commands') as commands,
          patch.object(server.assistant_role_manager, 'add_session') as register):
        events = asyncio.run(run())
        commands.assert_not_called()
        register.assert_not_called()
    assert len(prompts) == 1 and 'LOCAL_EDITOR_MARKER' in prompts[0]
    assert prompts[0].endswith('编辑:')
    assert not any(e in ('motion_start', 'motion_end') for e, _ in events)
    saved = manager.load_session_full(session_id, make_current=False)
    assert saved[1]['content'] == '建议修改'
    assert saved[1]['agent_id'] == editor['id']
