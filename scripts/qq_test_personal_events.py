"""Run the fixed R5-A personal-event case on a dedicated private QQ instance."""
import argparse
import asyncio
from contextlib import closing
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.qq_test_sender import TestTarget, _history, _message, _verify_identity, fingerprint, production_accounts


FIRST = '我的云苇展台登记演练已经完成表格核对，下一步准备登记样本'
SECOND = '我的云苇展台归还演练已经完成编号清点，下一步准备归还样本'


@dataclass(frozen=True)
class Step:
    key: str
    command: str
    contains: tuple[str, ...] = ()
    evidence: str = ''
    control: bool = True


STEPS = (
    Step('baseline', '/events self', ('本人总预算', '/events self'), 'baseline'),
    Step('topic_first', '/context new', ('开始独立QQ话题',)),
    Step('sample_first', f'这是虚构的 R5 验收材料。{FIRST}。', control=False),
    Step('list_first', '/events self', ('本人总预算', '/events self'), 'first'),
    Step('show_first', '/events show {first}', ('{first} · v', '原话：', FIRST)),
    Step('topic_second', '/context new', ('开始独立QQ话题',)),
    Step('sample_second', f'这是另一个虚构的 R5 验收事件。{SECOND}。', control=False),
    Step('list_second', '/events self', ('本人总预算', '/events self'), 'second'),
    Step('show_second', '/events show {second}', ('{second} · v', '原话：', SECOND)),
    Step('preview', '/events link {first} {second}',
         ('联合视图预览（待确认）', '原编号：{first}、{second}', FIRST, SECOND), 'operation'),
    Step('confirm', '/events link-confirm {operation}', ('联合视图已确认', '操作编号：{operation}')),
    Step('repeat_confirm', '/events link-confirm {operation}', ('联合视图已确认', '操作编号：{operation}')),
    Step('read_first', '/events link-show {first}', ('原编号：{first}、{second}', FIRST, SECOND, '操作编号：{operation}')),
    Step('read_second', '/events link-show {second}', ('原编号：{first}、{second}', FIRST, SECOND, '操作编号：{operation}')),
    Step('undo', '/events link-undo {operation}', ('已撤销联合视图', '操作编号：{operation}')),
    Step('result', '/events link-result {operation}', ('已撤销联合视图', '操作编号：{operation}')),
    Step('retry_after_undo', '/events link-confirm {operation}', ('已撤销联合视图', '操作编号：{operation}')),
    Step('original_first', '/events show {first}', ('{first} · v', '原话：', FIRST)),
    Step('original_second', '/events show {second}', ('{second} · v', '原话：', SECOND)),
)


def preview(target, instance):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', instance):
        raise ValueError('invalid_test_instance_name')
    plan = {'target': asdict(target), 'instance': instance, 'case': 'R5-A-personal-events-v1',
            'steps': [asdict(step) for step in STEPS], 'preserve_test_cards': True}
    return {**plan, 'plan_id': fingerprint(plan)}


def bridge_path(instance):
    # A test instance and its DB must both resolve within the dedicated area.
    base = ROOT / '.runtime' / 'qq-test-sender' / 'instances'
    if base.resolve() != base or not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', instance):
        raise ValueError('invalid_test_instance_path')
    path = base / instance / 'knowledge_base' / 'qq' / 'bridge.sqlite3'
    if path.resolve() != path or not path.is_file():
        raise ValueError('dedicated_test_bridge_missing')
    return path


def _accept(step, text, state):
    if step.control and not (text.startswith('【操作回执】“') and text.endswith('”')):
        raise ValueError('complete_control_receipt_required')
    if not text.strip() or any(marker.format_map(state) not in text for marker in step.contains):
        raise ValueError('unexpected_test_receipt')
    if step.evidence == 'operation':
        operations = re.findall(r'(?m)^核对完整来源后，十分钟内直接发送：/events link-confirm ([a-f0-9]{32})”$', text)
        if len(operations) != 1:
            raise ValueError('confirmation_not_unique')
        return operations[0]
    if not step.evidence:
        return None
    cards = dict(re.findall(r'(?m)^([a-f0-9]{16}) · 归属：[^\n]+? · 原话标题：(.+?) · ', text))
    if step.evidence == 'baseline':
        if any('云苇展台登记演练' in title or '云苇展台归还演练' in title for title in cards.values()):
            raise ValueError('test_card_already_exists')
        return sorted(cards)
    previous = set(state['baseline']) | ({state['first']} if step.evidence == 'second' else set())
    new = cards.keys() - previous
    label = '云苇展台登记演练' if step.evidence == 'first' else '云苇展台归还演练'
    if not previous <= cards.keys() or len(new) != 1 or label not in cards[next(iter(new))]:
        raise ValueError('new_test_card_not_unique')
    return next(iter(new))


class BridgeEvidence:
    def __init__(self, database, target, group_id=''):
        self.database, self.target, self.group_id = database, target, group_id
        self.scope = (f'{target.receiver_id}:group:{group_id}:user:{target.sender_id}' if group_id
                      else f'{target.receiver_id}:private:{target.sender_id}')

    def _connect(self):
        return closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=2))

    def admission(self, message_id):
        with self._connect() as db:
            row = db.execute('select state from inbox where scope=? and event_key=?',
                             (self.scope, f'{self.scope}:message:{message_id}')).fetchone()
        return row[0] if row else None

    def check_link(self, operation, members, state):
        kind = 'group' if self.group_id else 'personal'
        namespace = json.dumps((kind, self.target.receiver_id, self.group_id or self.target.sender_id),
                               separators=(',', ':'))
        with self._connect() as db:
            row = db.execute('select first,second,state,preview_state from qq_maintenance_links '
                             'where id=? and namespace=? and actor=? and topic=?',
                             (operation, namespace, self.target.sender_id, self.scope)).fetchone()
        if row != (*members, state, 'delivered'):
            raise ValueError('test_link_ledger_not_verified')


async def _exchange(sender, receiver, target, command, claim, sent, evidence, *, wait_seconds, group=None):
    async def history():
        return await group.history(receiver) if group else await _history(receiver, target.sender_id)

    if group:
        await _verify_identity(sender, target.sender_id)
        await _verify_identity(receiver, target.receiver_id)
        await group.verify(sender, receiver)
    before = set(await history())
    await _verify_identity(sender, target.sender_id)
    await _verify_identity(receiver, target.receiver_id)
    if group:
        await group.verify(sender, receiver)
    claim()
    parts = [{'type': 'text', 'data': {'text': command}}]
    if group:
        parts.insert(0, {'type': 'at', 'data': {'qq': target.receiver_id}})
        result = await sender.call('send_group_msg', {'group_id': int(group.id), 'message': parts})
    else:
        result = await sender.call('send_private_msg', {'user_id': int(target.receiver_id), 'message': parts})
    receipt = str(result.get('message_id') or '')
    if not re.fullmatch(r'-?\d{1,20}', receipt):
        return None
    sent(receipt)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while True:
        await _verify_identity(receiver, target.receiver_id)
        if group:
            await group.verify_member_list(receiver)
        latest = await history()
        parse = group.message if group else _message
        fresh = {key: parse(row) for key, row in latest.items() if key not in before}
        inputs = [key for key, (who, text, _) in fresh.items() if who == target.sender_id and text.strip() == command]
        if len(inputs) > 1:
            raise ValueError('ambiguous_test_input')
        if inputs and evidence.admission(inputs[0]) == 'sent':
            replies = [(key, text) for key, (who, text, refs) in fresh.items()
                       if who == target.receiver_id and inputs[0] in refs]
            if len(replies) > 1:
                raise ValueError('ambiguous_test_reply')
            if replies:
                reply_id, body = replies[0]
                return inputs[0], reply_id, body.strip()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(1, remaining))


async def execute(target, instance, approved_plan, sender, receiver, ledger, evidence, *, forbidden,
                  wait_seconds=180, pause_seconds=4):
    return await _execute(target, approved_plan, preview(target, instance)['plan_id'], STEPS, _accept,
                          sender, receiver, ledger, evidence, forbidden=forbidden,
                          wait_seconds=wait_seconds, pause_seconds=pause_seconds)


async def _execute(target, approved_plan, plan, steps, accept, sender, receiver, ledger, evidence, *, forbidden,
                   wait_seconds, pause_seconds, group=None):
    if plan != approved_plan or not forbidden or {target.sender_id, target.receiver_id} & forbidden:
        raise ValueError('test_plan_or_identity_not_authorized')
    if group and (group.target != target or evidence.group_id != group.id):
        raise ValueError('test_group_scope_changed')
    await _verify_identity(sender, target.sender_id)
    await _verify_identity(receiver, target.receiver_id)
    state = {}
    with closing(sqlite3.connect(ledger)) as db:
        with db:
            db.execute('create table if not exists personal_runs(run text primary key,plan text not null)')
            db.execute('''create table if not exists personal_steps(
                run text not null,step text not null,status text not null,receipt text,inbound text,reply text,
                answer_hash text, data text,primary key(run,step))''')
            db.execute('insert or ignore into personal_runs values(?,?)', (target.run, plan))
        if db.execute('select plan from personal_runs where run=?', (target.run,)).fetchone()[0] != plan:
            raise ValueError('test_run_plan_changed')
        for step in steps:
            key = (target.run, step.key)
            old = db.execute('select status,data from personal_steps where run=? and step=?', key).fetchone()
            if old:
                if old[0] != 'verified':
                    return {'status': old[0], 'step': step.key, **state}
                if step.evidence:
                    state[step.evidence] = json.loads(old[1])
                continue
            if pause_seconds:
                await asyncio.sleep(pause_seconds)

            def claim():
                with db:
                    db.execute("insert into personal_steps(run,step,status) values(?,?,'unknown')", key)

            def sent(receipt):
                with db:
                    db.execute("update personal_steps set status='submitted',receipt=? where run=? and step=?", (receipt, *key))

            received = await _exchange(sender, receiver, target, step.command.format_map(state), claim, sent,
                                       evidence, wait_seconds=wait_seconds, group=group)
            if not received:
                status = db.execute('select status from personal_steps where run=? and step=?', key).fetchone()[0]
                return {'status': status, 'step': step.key, **state}
            inbound, reply, text = received
            try:
                data = accept(step, text, state)
                if step.key in {'preview', 'confirm', 'repeat_confirm', 'undo', 'result', 'retry_after_undo'}:
                    link_state = {'preview': 'pending', 'confirm': 'active', 'repeat_confirm': 'active'}.get(step.key, 'undone')
                    evidence.check_link(data if step.key == 'preview' else state['operation'],
                                        (state['first'], state['second']), link_state)
            except ValueError:
                with db:
                    db.execute("update personal_steps set status='unexpected',inbound=?,reply=?,answer_hash=? "
                               'where run=? and step=?', (inbound, reply, fingerprint(text), *key))
                return {'status': 'unexpected', 'step': step.key, **state}
            with db:
                db.execute("update personal_steps set status='verified',inbound=?,reply=?,answer_hash=?,data=? "
                           'where run=? and step=?', (inbound, reply, fingerprint(text), json.dumps(data), *key))
            if step.evidence:
                state[step.evidence] = data
    return {'status': 'verified', **state}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path, help='dedicated private target file')
    parser.add_argument('instance', help='name of an isolated local test instance')
    parser.add_argument('--execute-approved-plan', metavar='PLAN_ID')
    args = parser.parse_args()
    try:
        target = TestTarget(**json.loads(args.target.read_text(encoding='utf-8-sig')))
        plan = preview(target, args.instance)
        if not args.execute_approved_plan:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        if args.execute_approved_plan != plan['plan_id']:
            raise ValueError('test_plan_or_identity_not_authorized')
        evidence = BridgeEvidence(bridge_path(args.instance), target)
        work = ROOT / '.runtime' / 'qq-test-sender'
        probe = work / 'probe.sqlite3'
        ledger = work / 'personal-events.sqlite3'
        if probe.resolve() != probe or ledger.resolve() != ledger:
            raise ValueError('invalid_test_ledger_path')
        with closing(sqlite3.connect(probe.as_uri() + '?mode=ro', uri=True)) as db:
            smoke = db.execute('select state from qq_test_probes where run=? and plan=?',
                               (target.run, target.preview()['plan_id'])).fetchone()
        if smoke != ('reply_verified',):
            raise ValueError('verified_test_probe_required')
        from saya_app.services.qq_bridge import OneBotClient, QQSettings

        async def run():
            forbidden = production_accounts()
            sender_token = os.environ['SAYA_QQ_TEST_SENDER_TOKEN']
            receiver_token = os.environ['SAYA_QQ_TEST_RECEIVER_TOKEN']
            if not sender_token or not receiver_token or sender_token == receiver_token:
                raise ValueError('distinct_test_onebot_tokens_required')
            sender = OneBotClient(QQSettings(api_url=target.sender_url, access_token=sender_token))
            receiver = OneBotClient(QQSettings(api_url=target.receiver_url, access_token=receiver_token))
            try:
                result = await execute(target, args.instance, args.execute_approved_plan, sender, receiver,
                                       ledger, evidence, forbidden=forbidden)
                print(json.dumps(result, ensure_ascii=False))
            finally:
                await asyncio.gather(sender.http.aclose(), receiver.http.aclose())

        asyncio.run(run())
    except Exception as exc:
        raise SystemExit(f'QQ event test stopped ({type(exc).__name__}). Inspect the original run before retrying.') from None


if __name__ == '__main__':
    main()
