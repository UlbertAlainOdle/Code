"""Verify the dedicated test webhook and storage without connecting to QQ."""
import asyncio
import hashlib
import hmac
import json

import httpx
import pytest

from scripts.qq_test_instance import _production_boundary, create_app, prepare
from scripts.qq_test_personal_events import BridgeEvidence, execute, preview
from scripts.qq_test_sender import TestTarget
from saya_app.services.qq_bridge import QQStore
from test_qq_delivery import Client, event
from test_qq_task_scheduler import until


@pytest.fixture
def target():
    return TestTarget('fixture-transport', '22000', '33000', 'http://127.0.0.1:3010', 'http://127.0.0.1:3011')


class TestOneBot(Client):
    __test__ = False

    async def call(self, action, payload):
        assert action == 'get_login_info' and payload == {}
        return {'user_id': 33000}


def request(text, mid, *, user=22000, group=None):
    return event(text, mid, user_id=user, group_id=group) | {'self_id': 33000}


async def post_signed(client, endpoint, payload):
    content = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    signature = hmac.new(b'test-inbound', content, hashlib.sha1).hexdigest()
    return await client.post(endpoint, content=content, headers={'X-Signature': 'sha1=' + signature})


def test_private_template_does_not_store_or_assume_accounts(tmp_path):
    path = prepare('r5-fixture', tmp_path)
    document = json.loads((path / 'target.template.json').read_text())
    assert document['sender_id'] == document['receiver_id'] == ''
    assert not (path / 'knowledge_base').exists()
    with pytest.raises(FileExistsError):
        prepare('r5-fixture', tmp_path)
    with pytest.raises(ValueError):
        prepare('../other', tmp_path)


def test_production_login_or_reserved_port_blocks_serving(target):
    _production_boundary(target, {'10000', '20000'}, 18081)
    with pytest.raises(ValueError, match='dedicated_test_accounts_required'):
        _production_boundary(target, {'10000', '22000'}, 18081)
    with pytest.raises(ValueError, match='dedicated_test_accounts_required'):
        _production_boundary(target, {'33000', '20000'}, 18081)
    for port in (3000, 8000, 3011):
        with pytest.raises(ValueError, match='test_webhook_port_required'):
            _production_boundary(target, {'10000', '20000'}, port)


def test_webhook_allows_only_authenticated_fixture_private_commands(tmp_path, target):
    async def run():
        path = prepare('isolated', tmp_path)
        outbound = TestOneBot()
        app = create_app(target, path, 'test-outbound', 'test-inbound', client=outbound)
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url='http://test') as http:
            assert (await http.get('/health')).json()['ready']
            endpoint = '/api/qq/webhook'
            assert (await http.post(endpoint, json=request('/events self', 1))).status_code == 401
            assert (await http.post(endpoint, json=request('/events self', 1),
                                    headers={'Authorization': 'Bearer test-inbound'})).status_code == 401
            for index, row in enumerate(('/events delete aaaaaaaaaaaaaaaa', 'ordinary chat'), 2):
                assert (await post_signed(http, endpoint, request(row, index))).json()['status'] == 'ignored'
            assert (await post_signed(http, endpoint, request('/events self', 4, user=99999))).json()['status'] == 'ignored'
            assert (await post_signed(http, endpoint, request('/events self', 5, group=90000))).json()['status'] == 'ignored'
            assert (await http.post(endpoint, content=b'x' * 17000)).status_code == 413
            assert (await post_signed(http, endpoint, request('/events self', 6))).json()['status'] == 'queued'
            await until(lambda: len(outbound.sent) == 1)
            assert '本人总预算' in outbound.sent[0][1]
            assert (await post_signed(http, endpoint, request('/events self', 7))).json()['status'] == 'rate_limited'
        assert (path / 'knowledge_base' / 'qq' / 'bridge.sqlite3').is_file()
    asyncio.run(run())


def test_instance_binding_blocks_account_reuse(tmp_path, target):
    path = prepare('isolated', tmp_path)
    create_app(target, path, 'test-outbound', 'test-inbound', client=TestOneBot())
    replacement = TestTarget('another-run', '44000', '33000', target.sender_url, target.receiver_url)
    with pytest.raises(ValueError, match='test_instance_accounts_changed'):
        create_app(replacement, path, 'test-outbound', 'test-inbound', client=TestOneBot())


def test_personal_runner_passes_the_fixture_webhook_and_real_bridge(tmp_path, target, monkeypatch):
    original_admit = QQStore.admit
    monkeypatch.setattr(QQStore, 'admit', lambda self, message, settings:
                        original_admit(self, message, settings.model_copy(update={'cooldown_seconds': 0})))

    async def run():
        path = prepare('isolated', tmp_path)
        outbound = TestOneBot()
        app = create_app(target, path, 'test-outbound', 'test-inbound', client=outbound)
        evidence = BridgeEvidence(path / 'knowledge_base' / 'qq' / 'bridge.sqlite3', target)
        history = []
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
            class Receiver:
                async def call(self, action, payload):
                    if action == 'get_login_info':
                        return {'user_id': 33000}
                    assert action == 'get_friend_msg_history'
                    return {'messages': history[-40:]}

            class Sender:
                sent = 0

                async def call(self, action, payload):
                    if action == 'get_login_info':
                        return {'user_id': 22000}
                    assert action == 'send_private_msg' and payload['user_id'] == 33000
                    self.sent += 1
                    mid = self.sent + 1000
                    text = payload['message'][0]['data']['text']
                    history.append({'message_id': mid, 'user_id': 22000, 'message': payload['message']})
                    offset = len(outbound.sent)
                    response = await post_signed(http, '/api/qq/webhook', request(text, mid))
                    assert response.json()['status'] == 'queued'
                    await until(lambda: evidence.admission(str(mid)) == 'sent')
                    for index, (message, content, reference, _) in enumerate(outbound.sent[offset:], offset + 501):
                        assert message.message_id == str(mid)
                        prefix = f'[CQ:reply,id={mid}]' if reference else ''
                        history.append({'message_id': index, 'user_id': 33000, 'raw_message': prefix + content})
                    return {'message_id': 7000 + self.sent}

            sender = Sender()
            result = await execute(target, 'isolated', preview(target, 'isolated')['plan_id'], sender,
                                   Receiver(), tmp_path / 'runner.sqlite3', evidence, forbidden={'20000', '10000'},
                                   wait_seconds=0, pause_seconds=0)
            assert result['status'] == 'verified', result
            assert sender.sent == 19
    asyncio.run(run())
