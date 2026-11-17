"""Native console consent reuses the original writers, never QQ delivery proof."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from core_services import KnowledgeBase
from saya_app.routers import qq as routes
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.memory_versions import record_version
from scripts.saya_qq_memory_panel import MemoryClient, ConsoleError, preview_text
from test_qq_task_scheduler import make


@pytest.fixture
def lab(tmp_path):
    bridge = make(tmp_path/'bridge', None, memory_management_enabled=True)
    bridge.store.initialize(7)
    kb = KnowledgeBase(str(tmp_path/'kb'))
    bridge.memory_management = QQMemoryManagement(bridge, kb)
    bridge.settings = bridge.settings.model_copy(update={'admin_token': 'fixture-admin'})
    bridge.task = SimpleNamespace(done=lambda: False)
    scope = '10000:private:20000:shared'
    bridge.store.save_conversation(scope, [], 'session_qq_fixture.jsonl')
    memory = kb.add_memory('这是虚构材料。' + '完整内容' * 600 + '。末尾必须显示。', title='虚构条目', source='manual')
    app = FastAPI()
    app.include_router(routes.router)
    return bridge, kb, memory, app


def run(lab, function):
    import asyncio
    bridge, kb, memory, app = lab
    async def exercise():
        with patch.object(routes, 'qq_bridge', bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 123)),
                    base_url='http://127.0.0.1:8000', headers={'Authorization':'Bearer fixture-admin', 'Origin':'http://127.0.0.1:8000'}) as client:
                await function(client, bridge, kb, memory)
    asyncio.run(exercise())


async def prepare(client, memory, action='delete'):
    response = await client.post('/api/qq/memory-console/operation-preview', json={
        'owner_id':'20000', 'memory_id':memory['id'], 'content_version':record_version(memory), 'action':action})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


async def confirm(client, preview, owner='20000'):
    return await client.post('/api/qq/memory-console/operation-confirm', json={'owner_id':owner, 'token':preview['token']})


def test_full_preview_then_delete_once_and_no_fake_QQ_delivery(lab):
    async def exercise(client, bridge, kb, memory):
        p = await prepare(client, memory)
        assert p['body'] == memory['content'] and p['body'].endswith('末尾必须显示。')
        assert kb._find_memory(memory['id'])
        assert (await confirm(client, p)).json()['operation_state'] == 'done'
        assert not kb._find_memory(memory['id']) and memory['id'] in kb.deleted_memory_ids
        assert (await confirm(client, p)).json()['status'] == 'done'
        with bridge.store.connect() as db:
            assert db.execute('select count(*) from qq_deliveries').fetchone()[0] == 0
    run(lab, exercise)


@pytest.mark.parametrize('change', ['version','topic','permission','restart'])
def test_native_confirmation_cannot_cross_original_barriers(lab, change):
    async def exercise(client, bridge, kb, memory):
        p = await prepare(client, memory)
        if change == 'version':
            kb.update_memory(memory['id'], {'content':'虚构条目已有新版本。'})
        elif change == 'topic':
            bridge.store.save_conversation('10000:private:20000:shared', [], 'session_qq_another.jsonl')
        elif change == 'permission':
            bridge.settings = bridge.settings.model_copy(update={'memory_write_enabled':False})
        else:
            bridge.memory_management.operations.recover()
        response = await confirm(client, p)
        assert response.status_code in {200,403}
        assert response.json().get('operation_state') != 'done'
        assert kb._find_memory(memory['id'])
    run(lab, exercise)


def test_native_stop_restore_reuses_original_memory_policy(lab):
    async def exercise(client, bridge, kb, memory):
        p = await prepare(client, memory, 'block')
        assert (await confirm(client, p)).json()['operation_state'] == 'done'
        assert not kb.memory_allowed_for_use(kb._find_memory(memory['id']))
        p = await prepare(client, kb._find_memory(memory['id']), 'unblock')
        assert (await confirm(client, p)).json()['operation_state'] == 'done'
        assert kb.memory_allowed_for_use(kb._find_memory(memory['id']))
    run(lab, exercise)


def test_native_endpoints_reject_foreign_origin_account_and_missing_auth(lab):
    async def exercise(client, bridge, kb, memory):
        assert (await client.get('/api/qq/memory-console/accounts')).json()['owners'] == ['20000']
        p = await prepare(client, memory)
        assert (await confirm(client, p, '30000')).status_code == 403
        client.headers['Origin'] = 'https://unrelated.example'
        assert (await confirm(client, p)).status_code == 403
        del client.headers['Authorization']
        assert (await client.get('/api/qq/memory-console/accounts')).status_code == 401
        assert kb._find_memory(memory['id'])
    run(lab, exercise)


def test_http_client_bootstraps_local_cookie_preserves_preview_and_surfaces_conflict():
    requests = []
    cookie_revision = [0]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append(('GET', self.path, self.headers.get('Origin')))
            if f'controls=fixture{cookie_revision[0]}' not in self.headers.get('Cookie', ''):
                self.send_response(401); self.end_headers(); return
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"owners":["20000"]}')
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(('POST', self.path, value))
            if self.path.endswith('/ui-session'):
                cookie_revision[0] += 1
                self.send_response(200); self.send_header('Set-Cookie', f'controls=fixture{cookie_revision[0]}; Path=/api/qq'); self.end_headers(); self.wfile.write(b'{}')
            else:
                self.send_response(409); self.end_headers(); self.wfile.write(json.dumps({'detail':'版本已变化，请重新预览'}, ensure_ascii=False).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        client = MemoryClient(server.server_port)
        assert client.accounts() == ['20000']
        # Same visible window/client survives backend restart. The very first
        # refresh must use its newly issued cookie, without a second click.
        cookie_revision[0] += 1
        assert client.accounts() == ['20000']
        preview = {'id':'a'*32, 'preview_version':'b'*64, 'state':'pending', 'waiting_on':[]}
        with pytest.raises(ConsoleError, match='版本已变化'):
            client.decide('20000', preview, 'approve')
        assert requests[-1][2] == {'owner_id':'20000', 'id':'a'*32, 'preview_version':'b'*64, 'action':'approve'}
        count = len(requests)
        with pytest.raises(ConsoleError):
            client.decide('20000', dict(preview, waiting_on=['c'*32]), 'approve')
        assert len(requests) == count
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_native_preview_keeps_full_revision_and_full_sources():
    old, new, source = '旧' * 2100, '新' * 2000, '原话' * 1000
    text = preview_text({'kind':'revision', 'state':'pending', 'subject':'20000', 'day':'2026-11-14',
                         'previous_body':old, 'body':new, 'sources':[{'text':source, 'message_id':'fixture'}]})
    assert old in text and new in text and source in text


@pytest.mark.parametrize('operation',['append','correct'])
def test_native_revision_full_diff_original_id_and_origin_kept(lab,operation):
    async def exercise(client,bridge,kb,memory):
        before_origin=memory['origin']
        value={'owner_id':'20000','memory_id':memory['id'],'content_version':record_version(memory),
               'operation':operation,'content':'这是虚构材料。清漪花房改用绿色纸袋。'}
        response=await client.post('/api/qq/memory-console/revision-preview',json=value)
        assert response.status_code==200,response.text
        preview=response.json();assert preview['status']=='preview' and preview['old']==memory['content']
        assert preview['new'].endswith(value['content']) and kb._find_memory(memory['id'])['content']==memory['content']
        payload={'owner_id':'20000','token':preview['confirmation_token']}
        # A native preview cannot be confirmed as a delivered QQ preview.
        from saya_app.services.memory_revisions import revision_service
        actor=bridge.memory_management.references.actor(SimpleNamespace(bot_id='10000',user_id='20000'))
        scope='10000:private:20000:shared';session=bridge.store.conversation(scope)[1]
        assert revision_service(kb).confirm(preview['confirmation_token'],scope=scope,session_id=session,authorize=lambda:None)['status']=='unavailable'
        result=await client.post('/api/qq/memory-console/revision-confirm',json=payload)
        assert result.json()['status']=='applied'
        current=kb._find_memory(memory['id'])
        assert len(kb.memories)==1 and current['origin']==before_origin
        assert current['last_modified_channel']=='web' and current['last_modified_origin']['verified_subject']=='20000'
        assert (await client.post('/api/qq/memory-console/revision-confirm',json=payload)).json()['status']=='done'
    run(lab,exercise)


def test_native_revision_rejects_stale_version_and_restores_no_old_snapshot(lab):
    async def exercise(client,bridge,kb,memory):
        value={'owner_id':'20000','memory_id':memory['id'],'content_version':record_version(memory),
               'operation':'correct','content':'虚构材料：试验盒用银色标签。'}
        preview=(await client.post('/api/qq/memory-console/revision-preview',json=value)).json()
        kb.update_memory(memory['id'],{'content':'虚构材料已有另一条并发修改。'})
        response=await client.post('/api/qq/memory-console/revision-confirm',json={'owner_id':'20000','token':preview['confirmation_token']})
        assert response.json()['status']=='stale'
        assert kb._find_memory(memory['id'])['content']=='虚构材料已有另一条并发修改。'
    run(lab,exercise)
