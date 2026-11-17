"""Isolated ordinary-web generator harness; no browser or QQ transport."""
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
from unittest.mock import patch

from core_services import MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.shared_chat_config import shared_chat_config


class WebLab:
    def __init__(self, server, root, knowledge, credentials):
        self.server, self.knowledge, self.credentials = server, knowledge, credentials
        self.manager = MemoryManager(str(Path(root)/'chats'))
        self.manager.legacy_sessions_file = str(Path(root)/'no-legacy.json')
        self.usage = []

    async def record_usage(self, model_name, usage, completed, **kwargs):
        self.usage.append({'model': model_name, 'usage': usage, 'completed': completed, **kwargs})

    async def turn(self, client, question, *, session='session_web_r4.jsonl', budget=2400, recall='auto', agent=False):
        credentials = self.credentials
        request = self.server.ChatWithMotion(
            user_input=question, session_id=session, model='api', agent_mode=agent,
            api_key=credentials['api_key'], api_url=credentials['api_url'],
            api_model_name=credentials['model'], thinking_effort='auto',
            memory_write_enabled=False, recall_mode=recall, graph_recall_mode='off',
            web_search_mode='off', memory_token_budget=budget, recall_memory_count=5,
            history_turns=12, max_output_tokens=600,
        )
        with patch.object(self.server, '_get_api_httpx_client', lambda: client):
            response = await self.server.chat_stream(request)
            chunks = [chunk.decode() if isinstance(chunk, bytes) else chunk
                      async for chunk in response.body_iterator]
        answer = ''
        for block in ''.join(chunks).split('\n\n'):
            if block.startswith('event: answer_complete\n'):
                answer = json.loads(block.split('data: ', 1)[1])['text']
        entries = self.manager.load_session_full(session, make_current=False)
        reply = next((entry for entry in reversed(entries) if entry['role'] == 'ai'), {})
        return {'answer': answer, 'audit': reply.get('memory_recall', {}), 'entries': entries}


@contextmanager
def web_lab(server, root, knowledge, credentials=None):
    root = Path(root);root.mkdir(parents=True, exist_ok=True)
    credentials = credentials or {'configured':True, 'api_key':'fixture', 'api_url':'http://mock/v1', 'model':'fixture'}
    credentials = {'configured':True, 'local_preferred':False, 'local_fields':[], 'model_routes':{}, **credentials}
    profile = root/'fixture-profile.txt'
    profile.write_text('You are Saya. Answer the current request using the supplied evidence.', encoding='utf-8')
    lab = WebLab(server, root, knowledge, credentials)
    knowledge.update_settings({'mood_continuity_enabled': False})
    with ExitStack() as stack:
        for name, value in {
            'kb_system':knowledge, 'memory_manager':lab.manager,
            'PROFILE_PATH':profile, 'KNOWLEDGE_BASE_DIR':Path(knowledge.base_dir),
            'refresh_daily_activity_memory':lambda:None, '_record_api_turn_usage':lab.record_usage,
            'log_to_file':lambda *a, **kw:None,
        }.items():
            stack.enter_context(patch.object(server, name, value))
        stack.enter_context(patch.object(server.companion_router, 'proactive', None))
        stack.enter_context(patch.object(conversation_service, 'save_credentials', lambda **kw:None))
        stack.enter_context(patch.object(conversation_service, 'credentials', lambda:credentials))
        stack.enter_context(patch.object(shared_chat_config, 'web_snapshot', lambda overrides=None:
            ({}, {'revision':0, 'inherited':False, 'overridden_fields':[]})))
        yield lab
