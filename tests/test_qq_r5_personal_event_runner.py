"""Exercise the R5-A sequence and its evidence gates with isolated OneBot data."""
import asyncio
from contextlib import closing
import json
import sqlite3

import pytest

from scripts.qq_test_personal_events import BridgeEvidence, FIRST, SECOND, execute, preview
from scripts.qq_test_sender import TestTarget
from saya_app.services.qq_context_links import QQContextLinks
from test_qq_task_scheduler import submit, until
from test_qq_topic_routing import create, item, topics


FIRST_ID, SECOND_ID, OP = 'a' * 16, 'b' * 16, 'c' * 32


def control(body):
    return f'【操作回执】“{body}”'


def listing(*rows):
    cards = '\n'.join(f'{key} · 归属：本人 · 原话标题：{title} · active' for key, title in rows)
    return control('本人总预算：3活跃/6总量。\n' + (cards or '当前场合没有可见事件。') + '\n/events self')


def replies():
    first = (FIRST_ID, '云苇展台登记演练')
    second = (SECOND_ID, '云苇展台归还演练')
    detail_first = (f'/events show {FIRST_ID}', control(f'{FIRST_ID} · v1\n发言者：本人 · 原话：{FIRST}。'), None)
    detail_second = (f'/events show {SECOND_ID}', control(f'{SECOND_ID} · v1\n发言者：本人 · 原话：{SECOND}。'), None)
    sources = f'原编号：{FIRST_ID}、{SECOND_ID}\n{FIRST}。\n{SECOND}。'
    confirmation = (f'/events link-confirm {OP}', control(f'联合视图已确认。 操作编号：{OP}。'), 'active')
    undone = control(f'已撤销联合视图。 操作编号：{OP}。')
    new = ('/context new', control('开始独立QQ话题。'), None)
    return [
        ('/events self', listing(), None), new,
        (f'这是虚构的 R5 验收材料。{FIRST}。', '收到这条虚构演练信息。', None),
        ('/events self', listing(first), None), detail_first, new,
        (f'这是另一个虚构的 R5 验收事件。{SECOND}。', '收到第二条虚构演练信息。', None),
        ('/events self', listing(first, second), None), detail_second,
        (f'/events link {FIRST_ID} {SECOND_ID}',
         control(f'联合视图预览（待确认）\n{sources}\n核对完整来源后，十分钟内直接发送：/events link-confirm {OP}'), 'pending'),
        confirmation, confirmation,
        (f'/events link-show {FIRST_ID}', control(f'联合视图\n{sources}\n操作编号：{OP}'), None),
        (f'/events link-show {SECOND_ID}', control(f'联合视图\n{sources}\n操作编号：{OP}'), None),
        (f'/events link-undo {OP}', undone, 'undone'), (f'/events link-result {OP}', undone, 'undone'),
        (f'/events link-confirm {OP}', undone, 'undone'), detail_first, detail_second,
    ]


class Receiver:
    async def call(self, action, body):
        if action == 'get_login_info':
            return {'user_id': 33000}
        assert action == 'get_friend_msg_history' and body['user_id'] == 22000
        return {'messages': self.history[-40:]}

    def __init__(self):
        self.history = []


class Sender:
    def __init__(self, receiver, evidence):
        self.receiver, self.evidence = receiver, evidence
        self.expected, self.sent = replies(), []
        self.timeout_at = self.bad_admission_at = self.missing_link_at = None

    async def call(self, action, body):
        if action == 'get_login_info':
            return {'user_id': 22000}
        assert action == 'send_private_msg' and body['user_id'] == 33000
        index = len(self.sent)
        command, response, status = self.expected[index]
        assert body['message'] == [{'type': 'text', 'data': {'text': command}}]
        self.sent.append(command)
        inbound, reply = index * 2 + 501, index * 2 + 502
        scope = self.evidence.scope
        self.receiver.history.extend([
            {'message_id': inbound, 'user_id': 22000, 'message': body['message']},
            {'message_id': reply, 'user_id': 33000, 'raw_message': f'[CQ:reply,id={inbound}]{response}'},
        ])
        with closing(sqlite3.connect(self.evidence.database)) as db, db:
            db.execute('insert into inbox values(?,?,?)',
                       (f'{scope}:message:{inbound}', scope, 'sending' if index == self.bad_admission_at else 'sent'))
            if status and index != self.missing_link_at:
                db.execute('insert or replace into qq_maintenance_links values(?,?,?,?,?,?,?,?)',
                           (OP, '["personal","33000","22000"]', '22000', scope, FIRST_ID, SECOND_ID, status, 'delivered'))
        if index == self.timeout_at:
            raise TimeoutError('simulated send accepted but response lost')
        return {'message_id': index + 401}


@pytest.fixture
def case(tmp_path):
    target = TestTarget('fixture-personal', '22000', '33000', 'http://127.0.0.1:3010', 'http://127.0.0.1:3011')
    bridge = tmp_path / 'bridge.sqlite3'
    with closing(sqlite3.connect(bridge)) as db, db:
        db.execute('create table inbox(event_key text,scope text,state text)')
        db.execute('''create table qq_maintenance_links(id text primary key,namespace text,actor text,topic text,
                                                      first text,second text,state text,preview_state text)''')
    evidence = BridgeEvidence(bridge, target)
    receiver = Receiver()
    return target, Sender(receiver, evidence), receiver, tmp_path / 'runs.sqlite3', evidence


def run(case, approval=None):
    target, sender, receiver, ledger, evidence = case
    return asyncio.run(execute(target, 'isolated-fixture', approval or preview(target, 'isolated-fixture')['plan_id'],
                               sender, receiver, ledger, evidence, forbidden={'10000', '20000'},
                               wait_seconds=0, pause_seconds=0))


def test_full_personal_case_and_replay_bind_ids_without_storing_bodies(case):
    target, sender, _, ledger, _ = case
    assert not ledger.exists()
    assert preview(target, 'isolated-fixture')['plan_id'] != preview(target, 'another-fixture')['plan_id']
    result = run(case)
    assert result == {'status': 'verified', 'baseline': [], 'first': FIRST_ID, 'second': SECOND_ID, 'operation': OP}
    assert run(case) == result and len(sender.sent) == 19
    assert all('delete' not in command for command in sender.sent)
    with closing(sqlite3.connect(ledger)) as db:
        rows = db.execute('select status,receipt,inbound,reply,answer_hash,data from personal_steps').fetchall()
    assert len(rows) == 19 and all(row[0] == 'verified' and all(row[1:5]) for row in rows)
    assert FIRST not in json.dumps(rows, ensure_ascii=False)


def test_incomplete_or_wrong_card_preview_never_confirms(case):
    _, sender, *_ = case
    command, body, status = sender.expected[9]
    sender.expected[9] = (command, body.replace(SECOND, '其他测试内容'), status)
    result = run(case)
    assert result['step'] == 'preview' and result['status'] == 'unexpected'
    assert len(sender.sent) == 10 and all('link-confirm' not in command for command in sender.sent)
    assert run(case) == result and len(sender.sent) == 10


def test_valid_text_without_durable_preview_cannot_confirm(case):
    _, sender, *_ = case
    sender.missing_link_at = 9
    assert run(case)['status'] == 'unexpected'
    assert len(sender.sent) == 10


def test_onebot_receipt_alone_does_not_prove_bridge_completion(case):
    _, sender, *_ = case
    sender.bad_admission_at = 2
    result = run(case)
    assert result['step'] == 'sample_first' and result['status'] == 'submitted'
    assert run(case) == result and len(sender.sent) == 3


def test_unknown_send_is_never_replayed_or_advanced(case):
    _, sender, *_ = case
    sender.timeout_at = 2
    with pytest.raises(TimeoutError):
        run(case)
    result = run(case)
    assert result['status'] == 'unknown' and result['step'] == 'sample_first' and len(sender.sent) == 3


def test_plan_mismatch_prevents_sending(case):
    _, sender, *_ = case
    with pytest.raises(ValueError, match='test_plan_or_identity_not_authorized'):
        run(case, 'unapproved')
    assert not sender.sent


def test_full_runner_against_actual_isolated_bridge_commands_and_storage(tmp_path):
    async def exercise():
        async with topics(tmp_path) as (bridge, service, _, _, choice, _):
            bridge.context_links = QQContextLinks(bridge.store, lambda *args: None)
            bridge.bind_responder(model)
            bridge.settings = bridge.settings.model_copy(update={'reply_reference': True})
            history = []
            sends = 0
            target = TestTarget('actual-bridge-fixture', '20000', '10000',
                                'http://127.0.0.1:3010', 'http://127.0.0.1:3011')

            class ReceiverClient:
                async def call(self, action, body):
                    if action == 'get_login_info':
                        return {'user_id': 10000}
                    assert action == 'get_friend_msg_history' and body['user_id'] == 20000
                    return {'messages': history[-40:]}

            class SenderClient:
                async def call(self, action, body):
                    nonlocal sends
                    if action == 'get_login_info':
                        return {'user_id': 20000}
                    assert action == 'send_private_msg' and body['user_id'] == 10000
                    sends += 1
                    inbound = 1000 + sends
                    text = body['message'][0]['data']['text']
                    fact = next((fact for fact in (FIRST, SECOND) if fact in text), None)
                    if fact:
                        choice[0] = {'items': [item(fact, personal=create(), mode='progress')]}
                    history.append({'message_id': inbound, 'user_id': 20000, 'message': body['message']})
                    offset = len(bridge.client.sent)
                    assert submit(bridge, text, inbound)[0] == 'queued'
                    await asyncio.wait_for(bridge.queue.join(), 5)
                    await until(lambda: not bridge._task_workers)
                    for index, (message, content, reference, _) in enumerate(bridge.client.sent[offset:], offset + 501):
                        assert message.message_id == str(inbound)
                        prefix = f'[CQ:reply,id={inbound}]' if reference else ''
                        history.append({'message_id': index, 'user_id': 10000, 'raw_message': prefix + content})
                    return {'message_id': 7000 + sends}

            result = await execute(target, 'isolated-fixture', preview(target, 'isolated-fixture')['plan_id'],
                                   SenderClient(), ReceiverClient(), tmp_path / 'runner.sqlite3',
                                   BridgeEvidence(bridge.store.path, target), forbidden={'22000', '33000'},
                                   wait_seconds=0, pause_seconds=0)
            assert result['status'] == 'verified', result
            assert sends == 19 and result['first'] != result['second']
            assert {row['id'] for row in service.people._canonical('20000')} == {result['first'], result['second']}

    async def model(*args):
        return '收到这条虚构演练信息。'

    asyncio.run(exercise())
