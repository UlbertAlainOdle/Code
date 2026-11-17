"""Run the group plan through an isolated QQ webhook and actual bridge storage."""
import asyncio
from contextlib import closing
import json
import sqlite3

import httpx
import pytest

from scripts.qq_test_group_events import Group, GroupEvidence, execute, preview, require_personal_success
from scripts.qq_test_instance import create_app, prepare
from scripts.qq_test_personal_events import STEPS as PERSONAL_STEPS
from scripts.qq_test_sender import TestTarget
from saya_app.services.qq_bridge import QQStore
from test_qq_delivery import Client, event
from test_qq_r5_test_instance import post_signed
from test_qq_task_scheduler import until


@pytest.fixture
def group():
    return Group('91000', TestTarget('fixture-transport', '22000', '33000',
                                    'http://127.0.0.1:3010', 'http://127.0.0.1:3011'))


class Transport(Client):
    def __init__(self, identity, group):
        super().__init__()
        self.identity, self.group = identity, group
        self.members = [22000, 33000]
        self.history = []

    async def call(self, action, body):
        if action == 'get_login_info':
            return {'user_id': self.identity}
        assert body['group_id'] == int(self.group.id)
        if action == 'get_group_member_list':
            return [{'user_id': member, 'group_id': body['group_id']} for member in self.members]
        if action == 'get_group_member_info':
            return {'user_id': body['user_id'], 'group_id': body['group_id'], 'role': 'member'}
        assert action == 'get_group_msg_history'
        return {'messages': self.history[-40:]}


def inbound(group, mid, parts):
    result = event('', mid, user_id=int(group.target.sender_id), group_id=int(group.id))
    return {**result, 'self_id': int(group.target.receiver_id), 'message': parts}


def test_group_requires_completed_private_run_with_same_target(tmp_path, group):
    path = tmp_path / 'personal.sqlite3'
    plan = preview(group, 'group-test', 'personal-test')
    with pytest.raises(ValueError):
        preview(group, 'group-test', 'group-test')
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table personal_runs(run text,plan text)')
        db.execute('create table personal_steps(run text,step text,status text)')
        db.execute('insert into personal_runs values(?,?)', (group.target.run, plan['personal_plan']))
        with pytest.raises(ValueError, match='verified_personal_case_required'):
            require_personal_success(path, group.target, plan['personal_plan'])
        db.executemany("insert into personal_steps values(?,?,'verified')",
                       [(group.target.run, step.key) for step in PERSONAL_STEPS])
    require_personal_success(path, group.target, plan['personal_plan'])


def test_extra_member_prevents_send_before_claim(tmp_path, group):
    class Sender(Transport):
        async def call(self, action, body):
            assert action != 'send_group_msg'
            return await super().call(action, body)
    sender = Sender(22000, group)
    receiver = Transport(33000, group)
    receiver.members.append(44000)
    ledger = tmp_path / 'runs.sqlite3'
    evidence = GroupEvidence(tmp_path / 'unused.sqlite3', group.target, group.id)
    with pytest.raises(ValueError, match='dedicated_two_account_group_required'):
        asyncio.run(execute(group, 'group-test', 'personal-test', preview(group, 'group-test', 'personal-test')['plan_id'],
                            sender, receiver, ledger, evidence, forbidden={'10000', '20000'},
                            wait_seconds=0, pause_seconds=0))
    with closing(sqlite3.connect(ledger)) as db:
        assert db.execute('select count(*) from personal_steps').fetchone() == (0,)


def test_full_group_workflow_with_real_bridge_and_roster_recheck(tmp_path, group, monkeypatch):
    original_admit = QQStore.admit
    monkeypatch.setattr(QQStore, 'admit', lambda self, message, settings:
                        original_admit(self, message, settings.model_copy(update={'cooldown_seconds': 0})))

    async def run():
        path = prepare('group-fixture', tmp_path, group=True)
        assert json.loads((path / 'group.template.json').read_text()) == {'group_id': ''}
        receiver = Transport(33000, group)
        app = create_app(group.target, path, 'test-outbound', 'test-inbound', client=receiver, group=group)
        evidence = GroupEvidence(path / 'knowledge_base' / 'qq' / 'bridge.sqlite3', group.target, group.id)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
            endpoint = '/api/qq/webhook'
            receiver.members.append(44000)
            probe_parts = [{'type': 'at', 'data': {'qq': '33000'}}, {'type': 'text', 'data': {'text': '/events group'}}]
            assert (await post_signed(http, endpoint, inbound(group, 55, probe_parts))).json() == {'status': 'ignored'}
            assert not receiver.sent
            receiver.members.remove(44000)
            assert (await post_signed(http, endpoint, inbound(group, 56, probe_parts[1:]))).json() == {'status': 'ignored'}

            class Sender(Transport):
                count = 0

                async def call(self, action, body):
                    if action != 'send_group_msg':
                        return await super().call(action, body)
                    assert body['group_id'] == int(group.id)
                    assert body['message'][0] == {'type': 'at', 'data': {'qq': '33000'}}
                    self.count += 1
                    mid = self.count + 1000
                    receiver.history.append({'message_id': mid, 'user_id': 22000,
                                             'raw_message': '[CQ:at,qq=33000] ' + body['message'][1]['data']['text']})
                    offset = len(receiver.sent)
                    response = await post_signed(http, endpoint, inbound(group, mid, body['message']))
                    assert response.json()['status'] == 'queued'
                    await until(lambda: evidence.admission(str(mid)) == 'sent')
                    for index, (message, content, reference, _) in enumerate(receiver.sent[offset:], offset + 501):
                        assert message.message_id == str(mid) and message.group_id == group.id
                        prefix = f'[CQ:reply,id={mid}]' if reference else ''
                        receiver.history.append({'message_id': index, 'user_id': 33000,
                                                 'raw_message': prefix + '[CQ:at,qq=22000] ' + content})
                    return {'message_id': 7000 + self.count}

            sender = Sender(22000, group)
            approval = preview(group, 'group-fixture', 'personal-fixture')['plan_id']
            result = await execute(group, 'group-fixture', 'personal-fixture', approval, sender, receiver,
                                   tmp_path / 'runner.sqlite3', evidence, forbidden={'10000', '20000'},
                                   wait_seconds=0, pause_seconds=0)
            assert result['status'] == 'verified', result
            assert sender.count == 19 and result['first'] != result['second']
            evidence.check_link(result['operation'], (result['first'], result['second']), 'undone')
            evidence.check_coordinator(result['first'])
            evidence.check_coordinator(result['second'])

    asyncio.run(run())
