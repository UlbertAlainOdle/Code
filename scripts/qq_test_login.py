"""Provision two isolated NapCat test logins using the already bound local loader."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import secrets
import sys
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.qq_test_sender import TestTarget, production_accounts


ROLES = {'sender': (3010, 6191), 'receiver': (3011, 6192)}
INSTANCES = ('r5-personal', 'r5-group', 'r5-archive')


def _directory(root):
    path = root.resolve() / '.runtime' / 'qq-test-sender' / 'login'
    if path.resolve() != path:
        raise ValueError('invalid_test_login_path')
    return path


def _save_new(path, value):
    with path.open('x', encoding='utf-8') as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write('\n')


def _role_directory(directory, role):
    path = directory / role
    if role not in ROLES or path.resolve() != path:
        raise ValueError('invalid_test_login_role')
    return path


def configure(root, sender, receiver, forbidden):
    directory = _directory(root)
    target = TestTarget('r5-' + uuid4().hex[:12], sender, receiver, 'http://127.0.0.1:3010', 'http://127.0.0.1:3011')
    if not forbidden or {sender, receiver} & forbidden:
        raise ValueError('dedicated_test_accounts_required')
    targets = [directory.parent / 'instances' / name / 'target.json' for name in INSTANCES]
    if any(path.resolve() != path or not path.parent.is_dir() or path.exists() for path in targets):
        raise ValueError('test_target_already_exists_or_unprepared')
    directory.mkdir(exist_ok=False)
    tokens = {f'SAYA_QQ_TEST_{name}_TOKEN': secrets.token_urlsafe(32)
              for name in ('SENDER', 'RECEIVER', 'WEBHOOK')}
    _save_new(directory / 'tokens.json', tokens)
    for role, account in (('sender', sender), ('receiver', receiver)):
        api, web = ROLES[role]
        profile = _role_directory(directory, role)
        config = profile / 'config'
        config.mkdir(parents=True, exist_ok=False)
        _save_new(profile / 'instance.json', {'marker': uuid4().hex, 'account': account})
        _save_new(config / 'webui.json', {'host': '127.0.0.1', 'port': web, 'token': secrets.token_urlsafe(32),
                                         'disableWebUI': False, 'autoLoginAccount': ''})
        server = {'name': 'saya-r5-test-api', 'enable': True, 'host': '127.0.0.1', 'port': api,
                  'messagePostFormat': 'array', 'token': tokens[f'SAYA_QQ_TEST_{role.upper()}_TOKEN'],
                  'enableCors': False, 'enableWebsocket': False, 'debug': False}
        callback = {'name': 'saya-r5-test-webhook', 'enable': True,
                    'url': 'http://127.0.0.1:18081/api/qq/webhook', 'messagePostFormat': 'array',
                    'reportSelfMessage': False, 'token': tokens['SAYA_QQ_TEST_WEBHOOK_TOKEN'], 'debug': False}
        network = {'httpServers': [server], 'httpClients': [callback] if role == 'receiver' else [],
                   'websocketServers': [], 'websocketClients': []}
        _save_new(config / f'onebot11_{account}.json', {'network': network, 'musicSignUrl': '',
                                                       'enableLocalFile2Url': False, 'parseMultMsg': False})
    data = {key: getattr(target, key) for key in ('run', 'sender_id', 'receiver_id', 'sender_url', 'receiver_url')}
    for path in targets:
        _save_new(path, data)
    _save_new(directory / 'target.json', data)
    return directory


def load_connection(root=ROOT):
    directory = _directory(root)
    target_file = directory / 'target.json'
    tokens_file = directory / 'tokens.json'
    if target_file.resolve() != target_file or tokens_file.resolve() != tokens_file:
        raise ValueError('invalid_test_login_path')
    if not directory.exists():
        raise ValueError('test_login_not_configured')
    if not target_file.is_file() or not tokens_file.is_file():
        raise ValueError('test_login_configuration_incomplete')
    target = TestTarget(**json.loads(target_file.read_text(encoding='utf-8')))
    tokens = json.loads(tokens_file.read_text(encoding='utf-8'))
    expected = {f'SAYA_QQ_TEST_{name}_TOKEN' for name in ('SENDER', 'RECEIVER', 'WEBHOOK')}
    if (set(tokens) != expected or any(not isinstance(token, str) or len(token) < 24 for token in tokens.values())
            or len(set(tokens.values())) != len(expected)):
        raise ValueError('invalid_test_login_tokens')
    return target, tokens


def _require_dedicated(target):
    if {target.sender_id, target.receiver_id} & production_accounts():
        raise ValueError('dedicated_test_accounts_required')


def launch(role):
    from scripts.manage_qq_service import port_open
    from saya_app.services.qq_lifecycle import InstanceLease
    from saya_app.services.qq_napcat import NapcatSupervisor, load_install

    target, _ = load_connection()
    _require_dedicated(target)
    profile = _role_directory(_directory(ROOT), role)
    state_path = profile / 'instance.json'
    if state_path.resolve() != state_path or (profile / 'config').resolve() != profile / 'config':
        raise ValueError('invalid_test_login_path')
    state = json.loads(state_path.read_text(encoding='utf-8'))
    account = target.sender_id if role == 'sender' else target.receiver_id
    if state['account'] != account or not re.fullmatch(r'[a-f0-9]{32}', state['marker']):
        raise ValueError('test_login_account_changed')
    installed = load_install(ROOT)
    if installed['runtime_kind'] != 'external_shell':
        raise ValueError('test_login_requires_bound_external_loader')
    config = {**installed, 'bot_id': account, 'profile_dir': profile, 'work_dir': profile,
              'qq_instance_id': state['marker']}
    supervisor = NapcatSupervisor(ROOT, profile)
    with InstanceLease(profile / 'login-launch.lock'):
        processes = supervisor.scan(config)
        if processes['uncertain'] or processes['foreign']:
            raise ValueError('test_login_process_unverified')
        if processes['members']:
            return
        if any(port_open(port) for port in ROLES[role]):
            raise ValueError('test_login_port_in_use')
        supervisor.launch(config)


async def readiness():
    from saya_app.services.qq_bridge import OneBotClient, QQSettings

    try:
        target, tokens = load_connection()
    except ValueError as exc:
        if str(exc) == 'test_login_not_configured':
            return dict.fromkeys(ROLES, 'not_configured')
        raise
    _require_dedicated(target)
    result = {}
    for role, other in (('sender', 'receiver'), ('receiver', 'sender')):
        account = getattr(target, role + '_id')
        client = OneBotClient(QQSettings(api_url=getattr(target, role + '_url'),
                                         access_token=tokens[f'SAYA_QQ_TEST_{role.upper()}_TOKEN']))
        try:
            login = await client.call('get_login_info', {})
            if str(login.get('user_id')) != account:
                result[role] = 'wrong_account'
                continue
            friends = await client.call('get_friend_list', {})
            result[role] = ('ready' if isinstance(friends, list) and any(
                isinstance(friend, dict) and str(friend.get('user_id')) == getattr(target, other + '_id')
                for friend in friends) else 'friendship_pending')
        except Exception:
            result[role] = 'onebot_unavailable'
        finally:
            await client.http.aclose()
    return result


def wait_webui(role, timeout=35):
    import httpx

    url = f'http://127.0.0.1:{ROLES[role][1]}/webui/'
    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=1) as client:
        while True:
            try:
                page = client.get(url)
                if page.status_code == 200 and 'text/html' in page.headers.get('Content-Type', ''):
                    return url
            except httpx.RequestError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('test_webui_not_ready')
            time.sleep(min(.5, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('configure')
    start = commands.add_parser('launch')
    start.add_argument('role', choices=ROLES)
    commands.add_parser('status')
    args = parser.parse_args()
    try:
        if args.command == 'configure':
            sender = input('Dedicated sender QQ: ').strip()
            receiver = input('Dedicated receiver QQ: ').strip()
            print(configure(ROOT, sender, receiver, production_accounts()))
        elif args.command == 'launch':
            launch(args.role)
            print(wait_webui(args.role))
        else:
            print(json.dumps(asyncio.run(readiness())))
    except Exception as exc:
        hints = {
            'test_login_not_configured': '尚未配置专用测试 QQ。请先运行 qq_test_login.py configure。',
            'test_login_configuration_incomplete': '专用配置尚未写全，请核对 configure 的输出。',
            'dedicated_test_accounts_required': '请输入两只独立的测试 QQ。当前正式主人或机器人账号不能用于专用测试。',
            'test_login_port_in_use': '专用端口被其他进程占用。请保留窗口输出供排查。',
            'test_login_process_unverified': '专用进程归属无法核实。请保留窗口输出供排查。',
            'test_webui_not_ready': '专用进程已提交启动，但 WebUI 尚未返回页面。请保留窗口输出供排查。',
        }
        raise SystemExit(hints.get(str(exc), f'QQ test login stopped ({type(exc).__name__}).')) from None


if __name__ == '__main__':
    main()
