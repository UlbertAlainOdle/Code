"""Run the protected-memory archive case against a dedicated QQ fixture."""
import argparse
import asyncio
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.qq_test_personal_events import BridgeEvidence, Step, _accept, _execute, bridge_path, preview as validate
from scripts.qq_test_sender import TestTarget, _configured_production_accounts, fingerprint, production_accounts


KEEP, DUPLICATE = 'memory:r5_archive_keep', 'memory:r5_archive_duplicate'
FACT = '这是虚构的归档验收样本。云苇灯塔演练使用蓝色标记。'
SOURCE = 'session_qq_archive_fixture:user:duplicate-source'
MEMBERS = ('保留编号：' + KEEP, '归档编号：' + DUPLICATE, FACT)
SAVED = ('已归档重复记忆', '操作编号：{operation}')
UNDONE = ('已撤销归档，原编号已恢复使用', '操作编号：{operation}')
STEPS = (
    Step('archive_preview', f'/memory archive {KEEP} {DUPLICATE}', ('重复记忆归档预览', *MEMBERS), 'operation'),
    Step('archive_confirm', '/memory archive-confirm {operation}', SAVED),
    Step('archive_repeat', '/memory archive-confirm {operation}', SAVED),
    Step('archive_result', '/memory archive-result {operation}', SAVED),
    Step('archive_show_keep', f'/memory archive-show {KEEP}', (*MEMBERS, '操作编号：{operation}')),
    Step('archive_show_duplicate', f'/memory archive-show {DUPLICATE}', (*MEMBERS, '操作编号：{operation}')),
    Step('archive_undo', '/memory archive-undo {operation}', UNDONE),
    Step('archive_result_undone', '/memory archive-result {operation}', UNDONE),
    Step('archive_retry_undone', '/memory archive-confirm {operation}', UNDONE),
)


def preview(target, instance):
    validate(target, instance)
    plan = {'target': asdict(target), 'instance': instance, 'case': 'R5-A-protected-archive-v1',
            'steps': [asdict(step) for step in STEPS], 'fixture_source_only': True}
    return {**plan, 'plan_id': fingerprint(plan)}


def seed(kb, target):
    """Install two projections of one synthetic source in a dedicated empty KB."""
    from saya_app.services.memory_origin import ingress

    if {row['id'] for row in kb.memories} == {KEEP, DUPLICATE}:
        if len(kb.memories) != 2 or any(row.get('content') != FACT for row in kb.memories):
            raise ValueError('archive_fixture_memories_changed')
        return
    if kb.memories or (kb.memory_v4_store and kb.memory_v4_store.list_memories(limit=1)):
        raise ValueError('archive_fixture_requires_empty_dedicated_kb')
    kb.update_settings({'memory_v4_read_enabled': True, 'memory_v4_write_enabled': True, 'embedding_enabled': False})
    now = datetime.now(timezone.utc)
    origin = ingress('qq', subject=target.sender_id, bot_id=target.receiver_id,
                     session_id='session_qq_archive_fixture', message_id='fixture-duplicate-source')['origin']
    for key in (KEEP, DUPLICATE):
        row = {'id': key, 'title': '虚构的归档验收样本', 'content': FACT, 'source': 'write_pipeline',
               'user_confirmed': True, 'creation_time': now.strftime('%Y-%m-%d %H:%M:%S'),
               'origin': origin, 'source_detail': {'type': 'memory_v4_write_pipeline', 'memory_id': key},
               'evidence': [{'type': 'user_message', 'role': 'assertion', 'source_id': SOURCE,
                             'quote': FACT, 'observed_at': now.isoformat()}]}
        kb.normalize_memory(row)
        kb.memories.append(row)
    if not kb.save_memories() or not all(kb.memory_v4_store.get_memory(key) for key in (KEEP, DUPLICATE)):
        raise RuntimeError('archive_fixture_projection_failed')


def bind(bridge, kb):
    from saya_app.services.qq_chat import QQChatResponder
    from saya_app.services.qq_memory_management import QQMemoryManagement

    async def no_model(*args, **kwargs):
        raise RuntimeError('archive_fixture_has_no_model')
    responder = QQChatResponder(bridge.settings, lambda **kwargs: SimpleNamespace(**kwargs),
                                no_model, lambda: 'api', lambda: {})
    bridge.memory_management = QQMemoryManagement(bridge, kb)
    responder.memory_management = bridge.memory_management
    bridge.bind_responder(responder)


class ArchiveEvidence(BridgeEvidence):
    def __init__(self, database, target):
        super().__init__(database, target)
        self.base = database.parent.parent

    def check_operation(self, operation, state):
        namespace = json.dumps(('memory', self.target.receiver_id, self.target.sender_id), separators=(',', ':'))
        with self._connect() as db:
            topic = db.execute('select session_id from conversations where scope=?',
                               (self.scope + ':shared',)).fetchone()
            row = db.execute('select keep_id,target_id,state,delivered,topic from qq_maintenance_archives '
                             'where id=? and namespace=?', (operation, namespace)).fetchone()
        if not topic or row != (KEEP, DUPLICATE, state, 1, topic[0]):
            raise ValueError('archive_test_operation_not_verified')
        archived = state == 'archived'
        expected = {KEEP: False, DUPLICATE: archived}
        rows = json.loads((self.base / 'knowledge.json').read_text(encoding='utf-8'))
        if len(rows) != 2 or {row['id']: bool(row.get('is_archived')) for row in rows if row['content'] == FACT} != expected:
            raise ValueError('archive_test_json_not_verified')
        with closing(sqlite3.connect((self.base / 'memory3.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            compat = db.execute('select id,is_archived,payload from memories where is_deleted=0 and content=?',
                                (FACT,)).fetchall()
        if ({key: bool(value) for key, value, _ in compat} != expected
                or {key: json.loads(payload) for key, _, payload in compat} != {row['id']: row for row in rows}):
            raise ValueError('archive_test_compatibility_not_verified')
        with closing(sqlite3.connect((self.base / 'memory4.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            native = db.execute("select id,lifecycle,authority,json_extract(metadata_json,'$.origin.verified_subject'),"
                                "json_extract(metadata_json,'$.origin.bot_id') from memory_items where canonical_text=?",
                                (FACT,)).fetchall()
            sources = db.execute('select memory_id,source_id,role,quote from memory_evidence order by memory_id').fetchall()
            mutations = db.execute("select memory_id,operation,reason from memory_versions "
                                   "where operation in ('archive_duplicate','undo_archive_duplicate') order by revision").fetchall()
        expected_native = [(key, 'archived' if value else 'active', 'user_confirmed',
                            self.target.sender_id, self.target.receiver_id) for key, value in expected.items()]
        if sorted(native) != sorted(expected_native) or sources != [(key, SOURCE, 'assertion', FACT) for key in sorted(expected)]:
            raise ValueError('archive_test_native_evidence_not_verified')
        expected_writes = []
        if state != 'pending':
            expected_writes.append((DUPLICATE, 'archive_duplicate', f'qq_archive:{operation}:archived'))
        if state == 'undone':
            expected_writes.append((DUPLICATE, 'undo_archive_duplicate', f'qq_archive:{operation}:active'))
        if mutations != expected_writes:
            raise ValueError('archive_test_mutation_count_not_verified')


def _accept_archive(step, text, state, evidence):
    from dataclasses import replace
    _accept(replace(step, evidence=''), text, state)
    if step.evidence == 'operation':
        operations = re.findall(r'(?m)^核对完整预览后，十分钟内直接发送：/memory archive-confirm ([a-f0-9]{32})”$', text)
        if len(operations) != 1:
            raise ValueError('archive_confirmation_not_unique')
        evidence.check_operation(operations[0], 'pending')
        return operations[0]
    expected = 'undone' if step.key in {'archive_undo', 'archive_result_undone', 'archive_retry_undone'} else 'archived'
    evidence.check_operation(state['operation'], expected)
    return None


async def execute(target, instance, approval, sender, receiver, ledger, evidence, *,
                  forbidden, wait_seconds=180, pause_seconds=4):
    return await _execute(target, approval, preview(target, instance)['plan_id'], STEPS,
                          lambda step, text, state: _accept_archive(step, text, state, evidence),
                          sender, receiver, ledger, evidence, forbidden=forbidden,
                          wait_seconds=wait_seconds, pause_seconds=pause_seconds)


def isolated_kb(path):
    """Import the shared KB only after explicitly redirecting its module defaults."""
    if 'core_services' in sys.modules or 'saya_app.config' in sys.modules:
        raise ValueError('archive_fixture_requires_fresh_process')
    base = path / 'knowledge_base'
    isolated_paths = [base, path / 'chats', path / 'notes', path / 'mmd']
    if any(item.resolve() != item for item in isolated_paths):
        raise ValueError('invalid_archive_fixture_path')
    if any(item.resolve() != item for folder in isolated_paths if folder.is_dir() for item in folder.rglob('*')):
        raise ValueError('invalid_archive_fixture_path')
    base.mkdir(exist_ok=True)
    settings = base / 'memory_settings.json'
    if settings.resolve() != settings:
        raise ValueError('invalid_archive_fixture_path')
    if not settings.exists():
        with settings.open('x', encoding='utf-8') as file:
            json.dump({'embedding_enabled': False, 'memory_v4_read_enabled': True,
                       'memory_v4_write_enabled': True}, file)
    os.chdir(path)
    os.environ.update(PROJECT_ROOT=str(path), DEAR_ROOT=str(path), SAYA_CHAT_MEMORY_DIR=str(path / 'chats'),
                      SAYA_MMD_DIRECTORY=str(path / 'mmd'), OBSIDIAN_VAULT_DIR=str(path / 'notes'))
    from saya_app.config import KNOWLEDGE_BASE_DIR
    if KNOWLEDGE_BASE_DIR != base:
        raise ValueError('archive_fixture_storage_not_isolated')
    from core_services import kb_system
    return kb_system


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--serve', action='store_true')
    mode.add_argument('--execute-approved-plan', metavar='PLAN_ID')
    parser.add_argument('--port', type=int, default=18083)
    args = parser.parse_args()
    try:
        path = ROOT / '.runtime' / 'qq-test-sender' / 'instances' / args.instance
        config = path / 'target.json'
        if (not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', args.instance)
                or path.resolve() != path or config.resolve() != config):
            raise ValueError('invalid_test_instance_path')
        target = TestTarget(**json.loads(config.read_text(encoding='utf-8-sig')))
        plan = preview(target, args.instance)
        if not args.serve and not args.execute_approved_plan:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        if args.serve:
            if {target.sender_id, target.receiver_id} & _configured_production_accounts(ROOT):
                raise ValueError('dedicated_test_accounts_required')
            receiver_token = os.environ['SAYA_QQ_TEST_RECEIVER_TOKEN']
            webhook_token = os.environ['SAYA_QQ_TEST_WEBHOOK_TOKEN']
            if not receiver_token or not webhook_token or receiver_token == webhook_token:
                raise ValueError('distinct_test_receiver_and_webhook_tokens_required')
            kb = isolated_kb(path)
            from scripts.qq_test_instance import _production_boundary, create_app
            _production_boundary(target, production_accounts(), args.port)
            app = create_app(target, path, receiver_token, webhook_token, archive=kb)
            import uvicorn
            uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1)
            return
        if args.execute_approved_plan != plan['plan_id']:
            raise ValueError('test_plan_or_identity_not_authorized')
        forbidden = production_accounts()
        if {target.sender_id, target.receiver_id} & forbidden:
            raise ValueError('dedicated_test_accounts_required')
        work = path.parent.parent
        probe, ledger = work / 'probe.sqlite3', work / 'archive.sqlite3'
        if probe.resolve() != probe or ledger.resolve() != ledger:
            raise ValueError('invalid_test_ledger_path')
        with closing(sqlite3.connect(probe.as_uri() + '?mode=ro', uri=True)) as db:
            smoke = db.execute('select state from qq_test_probes where run=? and plan=?',
                               (target.run, target.preview()['plan_id'])).fetchone()
        if smoke != ('reply_verified',):
            raise ValueError('verified_test_probe_required')
        evidence = ArchiveEvidence(bridge_path(args.instance), target)
        from saya_app.services.qq_bridge import OneBotClient, QQSettings

        async def run():
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
        raise SystemExit(f'QQ archive test stopped ({type(exc).__name__}). Inspect the original run before retrying.') from None


if __name__ == '__main__':
    main()
