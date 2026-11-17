"""The test login setup never uses formal accounts, profiles, or listener ports."""
import asyncio
import json

import httpx

import pytest

from scripts import qq_test_login as login


@pytest.fixture
def root(tmp_path):
    base = tmp_path / '.runtime' / 'qq-test-sender' / 'instances'
    for name in login.INSTANCES:
        (base / name).mkdir(parents=True)
    return tmp_path


def test_configure_writes_two_private_profiles_and_exact_signed_callback(root):
    directory = login.configure(root, '22000', '33000', {'10000', '20000'})
    target, tokens = login.load_connection(root)
    assert (target.sender_id, target.receiver_id) == ('22000', '33000')
    assert len(set(tokens.values())) == 3
    for role, account in [('sender', '22000'), ('receiver', '33000')]:
        config = directory / role / 'config'
        web = json.loads((config / 'webui.json').read_text())
        network = json.loads((config / f'onebot11_{account}.json').read_text())['network']
        api_port, web_port = login.ROLES[role]
        assert web['host'] == '127.0.0.1' and web['port'] == web_port and not web['autoLoginAccount']
        assert network['httpServers'][0]['port'] == api_port
        assert network['httpServers'][0]['token'] == tokens[f'SAYA_QQ_TEST_{role.upper()}_TOKEN']
        assert network['httpClients'] == [] if role == 'sender' else len(network['httpClients']) == 1
        if role == 'receiver':
            assert network['httpClients'][0]['token'] == tokens['SAYA_QQ_TEST_WEBHOOK_TOKEN']
            assert network['httpClients'][0]['url'] == 'http://127.0.0.1:18081/api/qq/webhook'
            assert network['httpClients'][0]['reportSelfMessage'] is False
    expected = json.loads((directory / 'target.json').read_text())
    for name in login.INSTANCES:
        path = directory.parent / 'instances' / name / 'target.json'
        assert json.loads(path.read_text()) == expected
    with pytest.raises(ValueError):
        login.configure(root, '22000', '33000', {'10000', '20000'})


def test_production_identity_is_rejected_before_writes(root):
    with pytest.raises(ValueError, match='dedicated_test_accounts_required'):
        login.configure(root, '20000', '33000', {'10000', '20000'})
    assert not (root / '.runtime' / 'qq-test-sender' / 'login').exists()


def test_unconfigured_status_is_reported_without_a_network_request(root, monkeypatch):
    reader = login.load_connection
    monkeypatch.setattr(login, 'load_connection', lambda: reader(root))
    assert asyncio.run(login.readiness()) == {'sender': 'not_configured', 'receiver': 'not_configured'}


def test_webui_url_is_returned_only_after_http_page(monkeypatch):
    code = [503]

    def handle(request):
        assert str(request.url) == 'http://127.0.0.1:6191/webui/'
        return httpx.Response(code[0], headers={'Content-Type': 'text/html'})

    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ValueError, match='test_webui_not_ready'):
        login.wait_webui('sender', timeout=0)
    code[0] = 200
    assert login.wait_webui('sender', timeout=0) == 'http://127.0.0.1:6191/webui/'


def test_launch_uses_exact_test_profile_and_does_not_restart_existing_process(root, monkeypatch):
    from scripts import manage_qq_service
    from saya_app.services import qq_napcat

    directory = login.configure(root, '22000', '33000', {'10000', '20000'})
    reader = login.load_connection
    monkeypatch.setattr(login, 'ROOT', root)
    monkeypatch.setattr(login, 'load_connection', lambda: reader(root))
    monkeypatch.setattr(login, 'production_accounts', lambda: {'10000', '20000'})
    monkeypatch.setattr(manage_qq_service, 'port_open', lambda _: False)
    monkeypatch.setattr(qq_napcat, 'load_install', lambda _: {'runtime_kind': 'external_shell'})
    launched = []
    monkeypatch.setattr(qq_napcat.NapcatSupervisor, 'scan',
                        lambda self, config: {'uncertain': False, 'foreign': False, 'members': launched})
    monkeypatch.setattr(qq_napcat.NapcatSupervisor, 'launch', lambda self, config: launched.append(config))
    login.launch('receiver')
    login.launch('receiver')
    assert len(launched) == 1
    assert launched[0]['bot_id'] == '33000'
    assert launched[0]['profile_dir'] == launched[0]['work_dir'] == directory / 'receiver'
