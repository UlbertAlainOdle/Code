"""Exercise the test sender against two isolated OneBot identities."""
import asyncio
from contextlib import closing
from dataclasses import replace
import sqlite3

import pytest

from scripts.qq_test_sender import PROBE, TestTarget, execute, production_accounts


class OneBot:
    def __init__(self, identity, receiver=None, *, fail_after_send=False):
        self.identity, self.receiver = identity, receiver
        self.fail_after_send = fail_after_send
        self.calls, self.history = [], []

    async def call(self, action, body):
        self.calls.append((action, body))
        if action == 'get_login_info':
            return {'user_id': self.identity}
        if action == 'get_friend_msg_history':
            return {'messages': self.history.copy()}
        assert action == 'send_private_msg' and body == {
            'user_id': self.receiver.identity, 'message': [{'type': 'text', 'data': {'text': PROBE}}]}
        self.receiver.history.extend([
            {'message_id': 501, 'user_id': self.identity, 'message': body['message']},
            {'message_id': 502, 'user_id': self.receiver.identity, 'raw_message': '[CQ:reply,id=501]【操作回执】“当前没有事件。”'},
        ])
        if self.fail_after_send:
            raise TimeoutError('isolated send result lost')
        return {'message_id': 401}


@pytest.fixture
def probe(tmp_path):
    target = TestTarget('fixture-run', '22000', '33000', 'http://127.0.0.1:3010', 'http://127.0.0.1:3011')
    receiver = OneBot(33000)
    sender = OneBot(22000, receiver)
    return target, sender, receiver, tmp_path / 'test.sqlite3'


def run(target, sender, receiver, ledger, *, approval=None, forbidden=None):
    return asyncio.run(execute(target, target.preview()['plan_id'] if approval is None else approval,
                               sender, receiver, ledger, forbidden=forbidden or {'10000', '20000'}, wait_seconds=0))


def test_preview_binds_target_and_message_then_sends_only_once(probe):
    target, sender, receiver, ledger = probe
    assert target.preview()['message'] == PROBE and not ledger.exists()
    assert not sender.calls and not receiver.calls
    assert run(*probe) == run(*probe) == 'reply_verified'
    assert [action for action, _ in sender.calls].count('send_private_msg') == 1
    with closing(sqlite3.connect(ledger)) as db:
        row = db.execute('select state,receipt,inbound,reply from qq_test_probes').fetchone()
    assert row == ('reply_verified', '401', '501', '502')
    assert replace(target, receiver_id='44000').preview()['plan_id'] != target.preview()['plan_id']


def test_no_send_for_wrong_plan_production_or_wrong_account(probe):
    _, sender, receiver, ledger = probe
    with pytest.raises(ValueError, match='test_plan_or_identity_not_authorized'):
        run(*probe, approval='wrong')
    with pytest.raises(ValueError, match='test_plan_or_identity_not_authorized'):
        run(*probe, forbidden={'33000'})
    receiver.identity = 44000
    with pytest.raises(ValueError, match='unexpected_onebot_login'):
        run(*probe)
    assert not ledger.exists() and all(action != 'send_private_msg' for action, _ in sender.calls)


def test_manual_stop_uses_local_production_identity_without_request(tmp_path, monkeypatch):
    from scripts import manage_qq_service

    config = tmp_path / '.env.qq'
    config.write_text("SAYA_QQ_BOT_ID = '10000'\nSAYA_QQ_OWNER_IDS = \"20000，25000\"\nSAYA_QQ_ACCESS_TOKEN=fixture",
                      encoding='utf-8')
    monkeypatch.setattr(manage_qq_service, 'port_open', lambda port: False)
    def forbidden(*args):
        pytest.fail('offline production backend should not be contacted')
    monkeypatch.setattr(manage_qq_service, 'local_request', forbidden)
    assert production_accounts(tmp_path) == {'10000', '20000', '25000'}
    config.write_text('SAYA_QQ_BOT_ID=10000\nSAYA_QQ_OWNER_IDS=invalid\n', encoding='utf-8')
    with pytest.raises(ValueError, match='production_identity_unavailable'):
        production_accounts(tmp_path)


def test_no_replay_when_send_receipt_is_missing(probe):
    _, sender, receiver, _ = probe
    sender.fail_after_send = True
    with pytest.raises(TimeoutError):
        run(*probe)
    assert receiver.history
    assert run(*probe) == 'unknown'
    assert [action for action, _ in sender.calls].count('send_private_msg') == 1


def test_existing_input_and_reply_do_not_count_as_a_new_delivery(probe):
    _, sender, receiver, _ = probe
    asyncio.run(sender.call('send_private_msg', {'user_id': 33000, 'message': [{'type': 'text', 'data': {'text': PROBE}}]}))
    assert run(*probe) == 'submitted'


@pytest.mark.parametrize('endpoint', ['http://example.com:1234', 'http://127.0.0.1:3011',
                                     'http://127.0.0.1:3010/path', 'http://user:token@127.0.0.1:3010'])
def test_only_distinct_local_onebot_ports(probe, endpoint):
    with pytest.raises(ValueError):
        replace(probe[0], sender_url=endpoint)
