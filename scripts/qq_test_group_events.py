"""Run the R5-A event case in a dedicated QQ group of exactly two test accounts."""
import argparse
import asyncio
from contextlib import closing
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.qq_test_personal_events import BridgeEvidence, Step, STEPS as PERSONAL_STEPS
from scripts.qq_test_personal_events import _accept, _execute, bridge_path, preview as personal_preview
from scripts.qq_test_sender import TestTarget, _message, fingerprint, production_accounts


FIRST = '群共同事件「栖禾展架登记演练」已完成登记表核对，下一步准备登记样本'
SECOND = '「栖禾展架归还演练」已完成归还清单清点，下一步准备归还样本'
P = '/events group'
STEPS = (
    Step('baseline', P, (P + ' show',), 'baseline'),
    Step('sample_first', f'这是虚构的 R5 验收材料。{FIRST}。', control=False),
    Step('list_first', P, (P + ' show',), 'first'),
    Step('show_first', P + ' show {first}', ('群事件：', '原话：', FIRST)),
    Step('sample_second', f'这是另一个虚构的 R5 验收群共同事件。{SECOND}。', control=False),
    Step('list_second', P, (P + ' show',), 'second'),
    Step('show_second', P + ' show {second}', ('群事件：', '原话：', SECOND)),
    Step('coordinator_first', P + ' coordinator {first} {actor}', ('已更新群事件协调人。',)),
    Step('coordinator_second', P + ' coordinator {second} {actor}', ('已更新群事件协调人。',)),
    Step('preview', P + ' link {first} {second}',
         ('联合视图预览（待确认）', '原编号：{first}、{second}', FIRST, SECOND), 'operation'),
    Step('confirm', P + ' link-confirm {operation}', ('联合视图已确认', '操作编号：{operation}')),
    Step('repeat_confirm', P + ' link-confirm {operation}', ('联合视图已确认', '操作编号：{operation}')),
    Step('read_first', P + ' link-show {first}', ('原编号：{first}、{second}', FIRST, SECOND, '操作编号：{operation}')),
    Step('read_second', P + ' link-show {second}', ('原编号：{first}、{second}', FIRST, SECOND, '操作编号：{operation}')),
    Step('undo', P + ' link-undo {operation}', ('已撤销联合视图', '操作编号：{operation}')),
    Step('result', P + ' link-result {operation}', ('已撤销联合视图', '操作编号：{operation}')),
    Step('retry_after_undo', P + ' link-confirm {operation}', ('已撤销联合视图', '操作编号：{operation}')),
    Step('original_first', P + ' show {first}', ('群事件：', '原话：', FIRST)),
    Step('original_second', P + ' show {second}', ('群事件：', '原话：', SECOND)),
)


@dataclass(frozen=True)
class Group:
    id: str
    target: TestTarget

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r'[1-9][0-9]{4,19}', self.id):
            raise ValueError('invalid_test_group')

    async def verify_member_list(self, client):
        rows = await client.call('get_group_member_list', {'group_id': int(self.id)})
        expected = {self.target.sender_id, self.target.receiver_id}
        if (not isinstance(rows, list) or len(rows) != 2
                or any(not isinstance(row, dict) or str(row.get('group_id', self.id)) != self.id for row in rows)
                or {str(row.get('user_id')) for row in rows} != expected):
            raise ValueError('dedicated_two_account_group_required')

    async def verify(self, sender, receiver):
        await self.verify_member_list(sender)
        await self.verify_member_list(receiver)

    async def history(self, receiver):
        result = await receiver.call('get_group_msg_history', {'group_id': int(self.id), 'count': 40})
        rows = result.get('messages') if isinstance(result, dict) else result
        if (not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get('message_id')
                or str(row.get('group_id', self.id)) != self.id for row in rows)):
            raise ValueError('test_group_history_unavailable')
        result = {str(row['message_id']): row for row in rows}
        if len(result) != len(rows):
            raise ValueError('ambiguous_test_group_history')
        return result

    def message(self, row):
        sender, text, refs = _message(row)
        mention = r'^\[CQ:at,qq=(?:' + self.target.receiver_id + '|' + self.target.sender_id + r')\]\s*'
        return sender, re.sub(mention, '', text), refs


def preview(group, instance, personal_instance):
    if instance == personal_instance:
        raise ValueError('test_group_instance_must_differ')
    personal = personal_preview(group.target, personal_instance)
    personal_preview(group.target, instance)  # Validate the separate directory name.
    plan = {'target': asdict(group.target), 'group': group.id, 'instance': instance,
            'personal_plan': personal['plan_id'], 'case': 'R5-A-group-events-v1',
            'steps': [asdict(step) for step in STEPS], 'preserve_test_cards': True}
    return {**plan, 'plan_id': fingerprint(plan)}


class GroupEvidence(BridgeEvidence):
    def check_coordinator(self, card):
        with self._connect() as db:
            row = db.execute("select json_extract(data,'$.coordinator') from qq_shared_events "
                             'where id=? and bot=? and grp=?',
                             (card, self.target.receiver_id, self.group_id)).fetchone()
        if row != (self.target.sender_id,):
            raise ValueError('test_group_coordinator_not_verified')


def _accept_group(step, text, state, evidence):
    _accept(replace(step, evidence=''), text, state)
    if step.key in {'coordinator_first', 'coordinator_second'}:
        evidence.check_coordinator(state[step.key.split('_')[1]])
    if step.evidence == 'operation':
        tokens = re.findall(r'(?m)^核对完整来源后，十分钟内直接发送：/events group link-confirm ([a-f0-9]{32})”$', text)
        if len(tokens) != 1:
            raise ValueError('confirmation_not_unique')
        return tokens[0]
    if not step.evidence:
        return None
    cards = dict(re.findall(r'(?m)^([a-f0-9]{16}) · 群公共 · (?:标题原话|协调人指定名称)：(.+?) · 标题作者：',
                            text.removeprefix('【操作回执】“')))
    labels = {'first': '栖禾展架登记演练', 'second': '栖禾展架归还演练'}
    if step.evidence == 'baseline':
        if any(label in title for title in cards.values() for label in labels.values()):
            raise ValueError('test_card_already_exists')
        return sorted(cards)
    old = set(state['baseline']) | ({state['first']} if step.evidence == 'second' else set())
    new = cards.keys() - old
    if not old <= cards.keys() or len(new) != 1 or labels[step.evidence] not in cards[next(iter(new))]:
        raise ValueError('new_test_card_not_unique')
    return next(iter(new))


async def execute(group, instance, personal_instance, approval, sender, receiver, ledger, evidence, *,
                  forbidden, wait_seconds=180, pause_seconds=4):
    target = group.target
    steps = tuple(replace(step, command=step.command.replace('{actor}', target.sender_id)) for step in STEPS)
    return await _execute(target, approval, preview(group, instance, personal_instance)['plan_id'], steps,
                          lambda step, text, state: _accept_group(step, text, state, evidence),
                          sender, receiver, ledger, evidence, forbidden=forbidden,
                          wait_seconds=wait_seconds, pause_seconds=pause_seconds, group=group)


def require_personal_success(path, target, personal_plan):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        run = db.execute('select plan from personal_runs where run=?', (target.run,)).fetchone()
        steps = db.execute("select step from personal_steps where run=? and status='verified'",
                           (target.run,)).fetchall()
    if run != (personal_plan,) or {key for key, in steps} != {step.key for step in PERSONAL_STEPS}:
        raise ValueError('verified_personal_case_required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance', help='name of the dedicated group fixture instance')
    parser.add_argument('personal_instance', help='dedicated private instance already verified')
    parser.add_argument('--execute-approved-plan', metavar='PLAN_ID')
    args = parser.parse_args()
    try:
        from scripts.qq_test_instance import instance_path
        instance = instance_path(args.instance)
        config, group_config = instance / 'target.json', instance / 'group.json'
        if config.resolve() != config or group_config.resolve() != group_config:
            raise ValueError('invalid_test_target_path')
        target = TestTarget(**json.loads(config.read_text(encoding='utf-8-sig')))
        group = Group(json.loads(group_config.read_text(encoding='utf-8-sig'))['group_id'], target)
        plan = preview(group, args.instance, args.personal_instance)
        if not args.execute_approved_plan:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        if args.execute_approved_plan != plan['plan_id']:
            raise ValueError('test_plan_or_identity_not_authorized')
        work = instance.parent.parent
        personal, ledger = work / 'personal-events.sqlite3', work / 'group-events.sqlite3'
        if personal.resolve() != personal or ledger.resolve() != ledger:
            raise ValueError('invalid_test_ledger_path')
        require_personal_success(personal, target, plan['personal_plan'])
        evidence = GroupEvidence(bridge_path(args.instance), target, group.id)
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
                result = await execute(group, args.instance, args.personal_instance, args.execute_approved_plan,
                                       sender, receiver, ledger, evidence, forbidden=forbidden)
                print(json.dumps(result, ensure_ascii=False))
            finally:
                await asyncio.gather(sender.http.aclose(), receiver.http.aclose())

        asyncio.run(run())
    except Exception as exc:
        raise SystemExit(f'QQ group test stopped ({type(exc).__name__}). Inspect the original run before retrying.') from None


if __name__ == '__main__':
    main()
