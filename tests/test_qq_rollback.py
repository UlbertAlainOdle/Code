import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore, QQMessage
from saya_app.services.qq_rollback import prepare_data, build_source_bundle, check_memory_rollback


def config_root(root):
    directory=root/'knowledge_base/qq';directory.mkdir(parents=True)
    bridge=QQBridge(QQSettings(history_turns=12),QQStore(directory/'bridge.sqlite3'))
    asyncio.run(bridge.start())
    values=bridge.controls()['settings']
    bridge.controls_store.save(values,['history_turns','thinking_effort'])
    bridge.shared_config.save({'history_turns':23,'thinking_effort':'none'},False,bridge.shared_config.load()['revision'])
    with sqlite3.connect(directory/'bridge.sqlite3') as db:
        db.execute('create table preservation (id text primary key, value text)')
        db.execute("insert into preservation values ('new-after-upgrade','must-survive')")
    (root/'knowledge_base/api_credentials.json').write_text(json.dumps({'api_key':'fake-model-secret','api_url':'http://fake/v1',
        'api_model_name':'fake-model','brave_search_api_key':'new-shared-search'}))
    (directory/'search_credentials.json').write_text(json.dumps({'api_key':'stale-search'}))
    return directory


def test_downgrade_freezes_effective_values_exports_search_and_preserves_new_data(tmp_path):
    source=tmp_path/'source';directory=config_root(source)
    files={path:path.read_bytes() for path in source.rglob('*') if path.is_file()}
    target=tmp_path/'bundle'
    result=prepare_data(source,target)
    flat=json.loads((target/'knowledge_base/qq/settings.json').read_text())
    assert flat['history_turns']==23 and flat['thinking_effort']=='none'
    assert 'inherit_fields' not in flat and 'schema_version' not in flat
    assert not {'delivery_mode','bubble_strategy','reply_reference','bubble_chars','reply_max_messages'} & flat.keys()
    current=json.loads((target/'knowledge_base/qq/settings-current.json').read_text())
    assert current['delivery_mode']=='chat' and current['bubble_chars']==240
    assert json.loads((target/'knowledge_base/qq/search_credentials.json').read_text())['api_key']=='new-shared-search'
    with sqlite3.connect(target/'knowledge_base/qq/bridge.sqlite3') as db:
        assert db.execute('select value from preservation').fetchone()[0]=='must-survive'
    assert all(path.read_bytes()==value for path,value in files.items())
    assert result['databases'][0]['integrity']=='ok'
    assert 'fake-model-secret' not in json.dumps(result) and 'new-shared-search' not in json.dumps(result)
    assert (target/'rollback-origin/qq/settings.json').exists()


def test_quoted_queued_payload_downgrade_only_changes_the_copy(tmp_path):
    source=tmp_path/'source';directory=config_root(source)
    store=QQStore(directory/'bridge.sqlite3');store.initialize(7)
    message=QQMessage('quoted-key','scope','10000','20000','','3','解释一下',False,reply_to='-12')
    store.admit(message,QQSettings())
    original=store.queued()[0]['payload']
    prepare_data(source,tmp_path/'export')
    with sqlite3.connect(tmp_path/'export/knowledge_base/qq/bridge.sqlite3') as db:
        projected=json.loads(db.execute("select payload from inbox where state='queued'").fetchone()[0])
    assert projected['text']=='解释一下' and 'reply_to' not in projected
    retained=json.loads((tmp_path/'export/knowledge_base/qq/quoted-queue-current.json').read_text('utf-8'))
    assert retained[0]['payload']['reply_to']=='-12'
    assert store.queued()[0]['payload']==original


def test_explicit_search_clear_is_not_revived_by_legacy_downgrade(tmp_path):
    source=tmp_path/'source';config_root(source)
    credentials=source/'knowledge_base/api_credentials.json'
    data=json.loads(credentials.read_text());data['brave_search_api_key']='';credentials.write_text(json.dumps(data))
    target=tmp_path/'bundle';prepare_data(source,target)
    assert json.loads((target/'knowledge_base/qq/search_credentials.json').read_text())=={'api_key':''}


def test_preparation_never_overwrites_existing_target(tmp_path):
    source=tmp_path/'source';config_root(source)
    target=tmp_path/'existing';target.mkdir();(target/'keep').write_text('unchanged')
    with pytest.raises(FileExistsError):prepare_data(source,target)
    assert (target/'keep').read_text()=='unchanged'


def test_archive_write_or_unknown_state_requires_compatible_rollback(tmp_path):
    directory = tmp_path / 'knowledge_base' / 'qq'
    directory.mkdir(parents=True)
    with sqlite3.connect(directory / 'bridge.sqlite3') as db:
        db.execute('create table qq_maintenance_archives(state text)')
        db.execute("insert into qq_maintenance_archives values ('pending')")
    capability = 'qq_maintenance_archive_v1'
    assert capability not in check_memory_rollback(tmp_path, {})['required']
    with sqlite3.connect(directory / 'bridge.sqlite3') as db:
        db.execute("update qq_maintenance_archives set state='archiving'")
    assert capability in check_memory_rollback(tmp_path, {})['missing']
    assert check_memory_rollback(tmp_path, {'memory_compatibility': [capability]})['status'] == 'compatible_declared'


def test_source_bundle_validates_snapshot_before_use_and_never_imports_live_data(tmp_path):
    root=tmp_path/'root';(root/'saya_app').mkdir(parents=True)
    (root/'saya_app/example.py').write_text('VERSION="new"\n')
    (root/'knowledge_base').mkdir();(root/'knowledge_base/secret.json').write_text('private')
    (root/'static/js').mkdir(parents=True);(root/'static/js/kept.js').write_text('window.COPIED=true;')
    snapshot=root/'snapshot';(snapshot/'saya_app').mkdir(parents=True)
    old=b'VERSION="old"\n';(snapshot/'saya_app/example.py').write_bytes(old)
    manifest={'files':[{'path':'saya_app/example.py','sha256':hashlib.sha256(old).hexdigest()}]}
    target=root/'.runtime/qq-upgrade/rollback-rehearsals/test'
    report=build_source_bundle(root,snapshot,manifest,target)
    assert (target/'saya_app/example.py').read_bytes()==old and report['overlaid_files']==1
    assert not (target/'knowledge_base/secret.json').exists()
    assert (target/'static/js/kept.js').read_text()=='window.COPIED=true;' and report['copied_static_sources']==1
    assert (target/'rehearsal_guard/sitecustomize.py').exists()
    with pytest.raises(ValueError):build_source_bundle(root,snapshot,manifest,tmp_path/'outside')
    (snapshot/'saya_app/example.py').write_text('corrupt')
    with pytest.raises(ValueError):build_source_bundle(root,snapshot,manifest,root/'.runtime/qq-upgrade/rollback-rehearsals/corrupt')


def test_rehearsal_guard_blocks_dns_and_async_numeric_ip_before_any_network(tmp_path):
    import os,subprocess,sys
    from saya_app.services.qq_rollback import NETWORK_GUARD
    (tmp_path/'sitecustomize.py').write_text(NETWORK_GUARD,encoding='utf-8')
    script="""import socket,asyncio
for call in (lambda:socket.getaddrinfo('example.invalid',443),lambda:socket.gethostbyname('example.invalid')):
    try:call()
    except OSError:pass
    else:raise AssertionError('DNS was not blocked')
async def check():
    try:await asyncio.open_connection('203.0.113.1',443)
    except OSError as error:assert 'external async' in str(error)
    else:raise AssertionError('numeric-IP connection was not blocked')
asyncio.run(check())
print('blocked before network')
"""
    result=subprocess.run([sys.executable,'-c',script],cwd=tmp_path,env=os.environ | {'PYTHONPATH':str(tmp_path)},
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and 'blocked before network' in result.stdout
