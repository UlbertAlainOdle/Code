"""Fixed-target R5-A private QQ transport probe. Preview is the default."""
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
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services.memory_write_operations import fingerprint


PROBE = '/events self'


def _configured_production_accounts(root):
    identity = {}
    wanted = {'SAYA_QQ_BOT_ID', 'SAYA_QQ_OWNER_IDS'}
    for line in (root / '.env.qq').read_text(encoding='utf-8-sig').splitlines():
        name, separator, value = line.strip().partition('=')
        name = name.strip()
        if not separator or name not in wanted:
            continue
        if name in identity:
            raise ValueError('ambiguous_production_identity')
        value = value.strip()
        if len(value) >= 2 and value[0] in {'"', "'"} and value[0] == value[-1]:
            value = value[1:-1]
        identity[name] = value
    bot = identity.get('SAYA_QQ_BOT_ID', '')
    owners = set(filter(None, re.split(r'[,，\s]+', identity.get('SAYA_QQ_OWNER_IDS', ''))))
    if not owners or any(not re.fullmatch(r'[1-9][0-9]{0,19}', account) for account in {bot, *owners}):
        raise ValueError('production_identity_unavailable')
    return {bot, *owners}


def production_accounts(root=ROOT):
    """Keep the production account denylist available during a manual stop."""
    from scripts import manage_qq_service as service

    accounts = _configured_production_accounts(root)
    if service.port_open(8000):
        live = service.local_request(8000, 'settings')['identity']
        if not live['bot_id'] or not live['owner_ids']:
            raise ValueError('production_identity_unavailable')
        accounts.update({str(live['bot_id']), *(str(owner) for owner in live['owner_ids'])})
    return accounts


@dataclass(frozen=True)
class TestTarget:
    __test__ = False
    run: str
    sender_id: str
    receiver_id: str
    sender_url: str
    receiver_url: str

    def __post_init__(self):
        if not isinstance(self.run, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,48}', self.run):
            raise ValueError('invalid_test_run')
        for account in (self.sender_id, self.receiver_id):
            if not isinstance(account, str) or not re.fullmatch(r'[1-9][0-9]{4,19}', account):
                raise ValueError('invalid_test_account')
        if self.sender_id == self.receiver_id:
            raise ValueError('test_accounts_must_differ')
        ports = set()
        for endpoint in (self.sender_url, self.receiver_url):
            url = urlsplit(endpoint)
            if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'} or not url.port
                    or url.path or url.query or url.fragment or url.username or url.password):
                raise ValueError('test_onebot_requires_loopback_port')
            ports.add(url.port)
        if len(ports) != 2:
            raise ValueError('test_endpoints_must_differ')

    def preview(self):
        body = {**asdict(self), 'kind': 'private', 'message': PROBE}
        return {**body, 'plan_id': fingerprint(body)}


async def _verify_identity(client, expected):
    login = await client.call('get_login_info', {})
    if str(login.get('user_id')) != expected:
        raise ValueError('unexpected_onebot_login')


async def _history(receiver, sender_id):
    result = await receiver.call('get_friend_msg_history', {'user_id': int(sender_id), 'count': 40})
    messages = result.get('messages') if isinstance(result, dict) else result
    if not isinstance(messages, list) or any(not isinstance(row, dict) or not row.get('message_id') for row in messages):
        raise ValueError('test_friend_history_unavailable')
    return {str(row['message_id']): row for row in messages}


def _message(row):
    sender = str((row.get('sender') or {}).get('user_id') or row.get('user_id') or '')
    parts = row.get('message')
    if isinstance(parts, list):
        text = ''.join(str(part['data'].get('text', '')) for part in parts if part['type'] == 'text')
        refs = {str(part['data'].get('id')) for part in parts if part['type'] == 'reply'}
        return sender, text, refs
    text = str(row.get('raw_message') or parts or '')
    return sender, re.sub(r'^\[CQ:reply,id=-?\d+\]', '', text), set(re.findall(r'\[CQ:reply,id=(-?\d+)\]', text))


async def execute(target, approved_plan, sender, receiver, ledger, *, forbidden, wait_seconds=25):
    plan = target.preview()['plan_id']
    if approved_plan != plan or not forbidden or {target.sender_id, target.receiver_id} & forbidden:
        raise ValueError('test_plan_or_identity_not_authorized')
    await _verify_identity(sender, target.sender_id)
    await _verify_identity(receiver, target.receiver_id)
    with closing(sqlite3.connect(ledger)) as db:
        with db:
            db.execute('''create table if not exists qq_test_probes(
                run text primary key,plan text not null,state text not null,baseline text not null,
                receipt text not null default '', inbound text not null default '', reply text not null default '')''')
        existing = db.execute('select plan,state,baseline from qq_test_probes where run=?', (target.run,)).fetchone()
        if existing and existing[0] != plan:
            raise ValueError('test_run_plan_changed')
        if existing and existing[1] in {'reply_verified', 'unknown', 'ambiguous'}:
            return existing[1]
        if existing:
            before = set(json.loads(existing[2]))
        else:
            before = set(await _history(receiver, target.sender_id))
            await _verify_identity(sender, target.sender_id)
            await _verify_identity(receiver, target.receiver_id)
            # A competing executor cannot claim the same run or send again.
            with db:
                db.execute('insert into qq_test_probes(run,plan,state,baseline) values(?,?,?,?)',
                           (target.run, plan, 'unknown', json.dumps(sorted(before))))
            sent = await sender.call('send_private_msg', {'user_id': int(target.receiver_id),
                                                         'message': [{'type': 'text', 'data': {'text': PROBE}}]})
            receipt = str(sent.get('message_id') or '')
            if not receipt:
                return 'unknown'
            with db:
                db.execute("update qq_test_probes set state='submitted',receipt=? where run=?", (receipt, target.run))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        state = existing[1] if existing else 'submitted'
        while True:
            await _verify_identity(receiver, target.receiver_id)
            current = await _history(receiver, target.sender_id)
            fresh = {key: _message(row) for key, row in current.items() if key not in before}
            inputs = [key for key, (who, text, _) in fresh.items() if who == target.sender_id and text == PROBE]
            if len(inputs) > 1:
                with db:
                    db.execute("update qq_test_probes set state='ambiguous' where run=?", (target.run,))
                return 'ambiguous'
            if inputs:
                inbound = inputs[0]
                replies = [key for key, (who, text, refs) in fresh.items()
                           if who == target.receiver_id and inbound in refs and '【操作回执】' in text]
                if len(replies) > 1:
                    with db:
                        db.execute("update qq_test_probes set state='ambiguous' where run=?", (target.run,))
                    return 'ambiguous'
                state = 'reply_verified' if len(replies) == 1 else 'inbound_verified'
                with db:
                    db.execute('update qq_test_probes set state=?,inbound=?,reply=? where run=?',
                               (state, inbound, replies[0] if len(replies) == 1 else '', target.run))
                if state == 'reply_verified':
                    return state
            if loop.time() >= deadline:
                return state
            await asyncio.sleep(min(1, deadline - loop.time()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path, help='private JSON with fixed dedicated sender and receiver')
    parser.add_argument('--execute-approved-plan', metavar='PLAN_ID')
    args = parser.parse_args()
    target = TestTarget(**json.loads(args.target.read_text(encoding='utf-8-sig')))
    if not args.execute_approved_plan:
        print(json.dumps(target.preview(), ensure_ascii=False, indent=2))
        return
    from saya_app.services.qq_bridge import OneBotClient, QQSettings

    async def run():
        forbidden = production_accounts()
        work = ROOT / '.runtime' / 'qq-test-sender'
        work.mkdir(parents=True, exist_ok=True)
        sender_token = os.environ['SAYA_QQ_TEST_SENDER_TOKEN']
        receiver_token = os.environ['SAYA_QQ_TEST_RECEIVER_TOKEN']
        if not sender_token or not receiver_token or sender_token == receiver_token:
            raise ValueError('distinct_test_onebot_tokens_required')
        sender = OneBotClient(QQSettings(api_url=target.sender_url, access_token=sender_token))
        receiver = OneBotClient(QQSettings(api_url=target.receiver_url, access_token=receiver_token))
        try:
            print(await execute(target, args.execute_approved_plan, sender, receiver,
                                work / 'probe.sqlite3', forbidden=forbidden))
        finally:
            await asyncio.gather(sender.http.aclose(), receiver.http.aclose())

    try:
        asyncio.run(run())
    except Exception as exc:
        raise SystemExit(f'QQ test probe stopped ({type(exc).__name__}). Inspect the original run before retrying.') from None


if __name__ == '__main__':
    main()
