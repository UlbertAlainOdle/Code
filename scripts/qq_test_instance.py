"""Prepare and run an isolated QQ transport fixture for the R5-A private case."""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request
from saya_app.services.qq_bridge import OneBotClient, QQBridge, QQSettings, QQStore, normalize_event
from saya_app.services.qq_context_links import QQContextLinks
from scripts.qq_test_personal_events import FIRST, SECOND, STEPS
from scripts.qq_test_group_events import FIRST as GROUP_FIRST, SECOND as GROUP_SECOND, STEPS as GROUP_STEPS, Group
from scripts.qq_test_archive import STEPS as ARCHIVE_STEPS, bind as bind_archive, seed as seed_archive
from scripts.qq_test_sender import TestTarget, _verify_identity, production_accounts


_FACTS = {step.command: fact for step, fact in zip((s for s in STEPS if not s.control), (FIRST, SECOND))}
_GROUP_FACTS = {step.command: fact for step, fact in zip((s for s in GROUP_STEPS if not s.control),
                                                       (GROUP_FIRST, GROUP_SECOND))}
_MAX_BODY = 16 * 1024


def instance_path(name, root=ROOT):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', name):
        raise ValueError('invalid_test_instance_name')
    base = root.resolve() / '.runtime' / 'qq-test-sender' / 'instances'
    path = base / name
    if path.resolve() != path:
        raise ValueError('invalid_test_instance_path')
    return path


def prepare(name, root=ROOT, *, group=False):
    path = instance_path(name, root)
    path.mkdir(parents=True, exist_ok=False)
    with (path / 'target.template.json').open('x', encoding='utf-8') as file:
        json.dump({'run': 'r5-personal-01', 'sender_id': '', 'receiver_id': '',
                   'sender_url': 'http://127.0.0.1:3010', 'receiver_url': 'http://127.0.0.1:3011'},
                  file, indent=2)
        file.write('\n')
    if group:
        with (path / 'group.template.json').open('x', encoding='utf-8') as file:
            file.write('{"group_id": ""}\n')
    return path


def _production_boundary(target, forbidden, webhook_port):
    if not forbidden:
        raise ValueError('production_identity_unavailable')
    if {target.sender_id, target.receiver_id} & forbidden:
        raise ValueError('dedicated_test_accounts_required')
    api_ports = {urlsplit(target.sender_url).port, urlsplit(target.receiver_url).port}
    if not 1024 <= webhook_port <= 65535 or webhook_port in api_ports | {3000, 8000}:
        raise ValueError('test_webhook_port_required')


async def _decide(prompt, *, actor, purpose, **kwargs):
    fact = (_GROUP_FACTS if actor.group_id else _FACTS).get(actor.text)
    if purpose != 'qq_topic_route' or not fact:
        return {'items': []}
    kind = 'group' if actor.group_id else 'personal'
    return {'items': [{'quote': fact, 'title': fact[:20], 'mode': 'progress', 'subject': 'self',
                       'revision_kind': 'append', 'confidence': 1.0,
                       kind: {'action': 'create', 'supersedes': []}}]}


async def _respond(*args):
    return '收到这条虚构演练信息。'


class GroupFixtureClient:
    def __init__(self, client, group):
        self.client, self.group = client, group

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def send_part(self, message, text, **options):
        if message.group_id != self.group.id:
            raise ValueError('test_group_scope_changed')
        await self.group.verify_member_list(self.client)
        return await self.client.send_part(message, text, **options)

    async def send(self, message, text):
        return await self.send_part(message, text)


def create_app(target, path, receiver_token, webhook_token, *, client=None, group=None, archive=None):
    if not receiver_token or not webhook_token or receiver_token == webhook_token:
        raise ValueError('distinct_test_receiver_and_webhook_tokens_required')
    database = path / 'knowledge_base' / 'qq' / 'bridge.sqlite3'
    binding = path / 'accounts.json'
    if database.resolve() != database or binding.resolve() != binding or not path.is_dir():
        raise ValueError('invalid_test_bridge_path')
    if archive is not None and (group or Path(archive.base_dir).resolve() != database.parent.parent):
        raise ValueError('archive_fixture_storage_not_isolated')
    mode = 'archive' if archive is not None else 'group' if group else 'personal'
    identities = {'sender_id': target.sender_id, 'receiver_id': target.receiver_id,
                  'sender_url': target.sender_url, 'receiver_url': target.receiver_url,
                  'group': group.id if group else '', 'mode': mode}
    if group and group.target != target:
        raise ValueError('test_group_scope_changed')
    if not binding.exists():
        if database.exists():
            raise ValueError('unbound_test_bridge')
        with binding.open('x', encoding='utf-8') as file:
            json.dump(identities, file)
    saved = json.loads(binding.read_text(encoding='utf-8'))
    if {'group': '', 'mode': 'group' if saved.get('group') else 'personal', **saved} != identities:
        raise ValueError('test_instance_accounts_changed')
    steps = {'personal': STEPS, 'group': GROUP_STEPS, 'archive': ARCHIVE_STEPS}[mode]
    commands = tuple(re.compile(re.escape(step.command.replace('{actor}', target.sender_id))
                                .replace(r'\{first\}', '[a-f0-9]{16}').replace(r'\{second\}', '[a-f0-9]{16}')
                                .replace(r'\{operation\}', '[a-f0-9]{32}'))
                     for step in steps)
    settings = QQSettings(enabled=True, bot_id=target.receiver_id, api_url=target.receiver_url,
                          access_token=receiver_token, webhook_secret=webhook_token,
                          owner_ids=frozenset({target.sender_id}), shared_memory=True,
                          allowed_groups=frozenset({group.id}) if group else frozenset(),
                          cooldown_seconds=3, reply_reference=True, task_control_enabled=True,
                          memory_write_enabled=archive is not None, memory_management_enabled=archive is not None,
                          web_search_mode='off')
    client = client or OneBotClient(settings)
    bridge = QQBridge(settings, QQStore(database), responder=_respond,
                      client=GroupFixtureClient(client, group) if group else client)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await _verify_identity(bridge.client, target.receiver_id)
            if group:
                await group.verify_member_list(bridge.client)
            await bridge.start()
            if bridge.config_error or not bridge.task:
                raise RuntimeError('test_bridge_start_failed')
            if archive is not None:
                seed_archive(archive, target)
                scope = f'{target.receiver_id}:private:{target.sender_id}:shared'
                with bridge.store.connect() as db:
                    if not db.execute('select 1 from conversations where scope=?', (scope,)).fetchone():
                        db.execute('insert into conversations values(?,?,?,?)',
                                   (scope, '[]', 'session_qq_archive_fixture', 0))
                bind_archive(bridge, archive)
                yield
                return
            events = bridge.episodes
            owner = target.sender_id
            context = bridge.context_salience.state()
            if not context['values']['enabled']:
                bridge.context_salience.configure(owner, context['revision'],
                                                  {**context['values'], 'enabled': True})
            policy = events.policy()
            if not policy['values']['enabled']:
                events.configure(owner, policy['revision'], {**policy['values'], 'enabled': True})
            if not events.people.active():
                events.people.migrate(owner, events.people.migration_preview()['version'])
            unified = events.unified.policy()
            if not unified['values']['enabled'] or not unified['values']['topic_routing_enabled']:
                events.unified.configure(owner, unified['revision'],
                                         {**unified['values'], 'enabled': True, 'topic_routing_enabled': True},
                                         events.unified.preview()['version'])
            events.decide = _decide
            bridge.context_links = QQContextLinks(bridge.store, lambda *args: None)
            yield
        finally:
            await bridge.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/health')
    async def health():
        return {'ready': bool(bridge.task and not bridge.task.done()),
                'mode': f'r5-a-{mode}-fixture'}

    @app.post('/api/qq/webhook')
    async def webhook(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > _MAX_BODY:
                raise HTTPException(413, 'test_payload_too_large')
            body.extend(chunk)
        signature = request.headers.get('X-Signature', '')
        if not bridge.authenticate(body, '', signature):
            raise HTTPException(401, 'unauthorized')
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            return {'status': 'ignored'}
        message, _ = normalize_event(value, bridge.settings)
        if (not message or message.group_id != (group.id if group else '') or message.user_id != target.sender_id
                or message.reply_to or message.forward_present or message.media_types
                or not any(pattern.fullmatch(message.text) for pattern in commands)):
            return {'status': 'ignored'}
        if group:
            try:
                await group.verify_member_list(bridge.client)
            except Exception:
                return {'status': 'ignored'}
        result, _ = bridge.submit_signed(bytes(body), signature=signature)
        return {'status': result}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    init = actions.add_parser('prepare')
    init.add_argument('instance')
    init.add_argument('--group', action='store_true')
    serve = actions.add_parser('serve')
    serve.add_argument('instance')
    serve.add_argument('--port', type=int, default=18081)
    serve.add_argument('--group', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            print(prepare(args.instance, group=args.group))
            return
        path = instance_path(args.instance)
        config = path / 'target.json'
        if config.resolve() != config:
            raise ValueError('invalid_test_target_path')
        target = TestTarget(**json.loads(config.read_text(encoding='utf-8-sig')))
        group_config = path / 'group.json'
        if group_config.resolve() != group_config:
            raise ValueError('invalid_test_target_path')
        group = Group(json.loads(group_config.read_text(encoding='utf-8-sig'))['group_id'], target) if args.group else None
        _production_boundary(target, production_accounts(), args.port)
        app = create_app(target, path, os.environ['SAYA_QQ_TEST_RECEIVER_TOKEN'],
                         os.environ['SAYA_QQ_TEST_WEBHOOK_TOKEN'], group=group)
        import uvicorn
        uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1)
    except Exception as exc:
        raise SystemExit(f'QQ test fixture stopped ({type(exc).__name__}).') from None


if __name__ == '__main__':
    main()
