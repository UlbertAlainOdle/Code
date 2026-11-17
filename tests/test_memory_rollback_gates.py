import hashlib,json
from pathlib import Path
import pytest
from test_qq_rollback import config_root
from saya_app.services.qq_rollback import prepare_data,check_memory_rollback,build_source_bundle


def test_daily_admission_and_mood_metadata_require_compatible_rollback(tmp_path):
    import sqlite3
    root=tmp_path/'source';config_root(root);base=root/'knowledge_base';(base/'qq').mkdir(exist_ok=True)
    with sqlite3.connect(base/'qq/bridge.sqlite3') as db:
        db.execute('create table qq_daily_policy(enabled integer)');db.execute('insert into qq_daily_policy values(1)')
        db.execute('create table qq_mood_observations(status text)');db.execute("insert into qq_mood_observations values('unavailable')")
        db.execute('create table qq_memory_scope_epochs(scope text,epoch text)');db.execute("insert into qq_memory_scope_epochs values('10000:private:30000:isolated','epoch')")
    gate=check_memory_rollback(root,{'memory_compatibility':[]})
    assert gate['status']=='blocked' and {'qq_daily_admission_v1','qq_mood_metadata_v1','qq_person_memory_v1'}<=set(gate['missing'])
    assert check_memory_rollback(root,{'memory_compatibility':gate['required']})['status']=='compatible_declared'


def test_native_save_queue_requires_source_and_consent_compatible_reader(tmp_path):
    import sqlite3
    root=tmp_path/'source';config_root(root);base=root/'knowledge_base';(base/'qq').mkdir(exist_ok=True)
    with sqlite3.connect(base/'qq/bridge.sqlite3') as db:
        db.execute('create table qq_memory_console_saves(state text)')
        db.execute("insert into qq_memory_console_saves values('unknown')")
    gate=check_memory_rollback(root,{'memory_compatibility':[]})
    assert gate['status']=='blocked' and 'qq_console_saves_v1' in gate['missing']
    assert check_memory_rollback(root,{'memory_compatibility':gate['required']})['status']=='compatible_declared'


def data(root):
    config_root(root);base=root/'knowledge_base'
    records={
        'memory_use_state.json':{'version':2,'epoch':'a'*32,'dependencies':{'derived':{'sources':{'m':'v'}}},
            'policies':{'m':True},'history_floor':'b'*32},
        'memory_deletions.json':{'schema_version':1,'deleted':{'removed':{'reason':'fixture'}}},
        'memory_v4_projection_pending.json':{'schema_version':1,'ids':['m']},
        'memory_conflicts.json':{'conflicts':[{'status':'applying','target_versions':{},'content':'PRIVATE_FIXTURE_BODY'}]},
        'memory_candidates.json':[{'id':'candidate','content':'PRIVATE_FIXTURE_BODY'}]}
    for name,value in records.items():(base/name).write_text(json.dumps(value),encoding='utf-8')
    return records


def test_private_export_preserves_all_new_journals_and_never_implies_execution(tmp_path):
    root=tmp_path/'source';records=data(root)
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    target=tmp_path/'private-export';report=prepare_data(root,target)
    for name in records:assert (target/'knowledge_base'/name).read_bytes()==before[root/'knowledge_base'/name]
    assert report['execution_authorized'] is False
    assert 'PRIVATE_FIXTURE_BODY' not in json.dumps(report)
    assert all(p.read_bytes()==body for p,body in before.items())
    assert {'memory_use_v2','source_dependencies','per_memory_use_policy','history_floor',
        'deletion_ledger','v4_projection_reconciliation','conflict_cas_intent'}<=set(report['memory_compatibility_required'])


def test_unsupported_target_rejected_before_building_or_running_old_sources(tmp_path):
    root=tmp_path/'source';data(root)
    snapshot=tmp_path/'snapshot';snapshot.mkdir();(snapshot/'fixture.py').write_text('old=True')
    manifest={'files':[{'path':'fixture.py','sha256':hashlib.sha256((snapshot/'fixture.py').read_bytes()).hexdigest()}]}
    gate=check_memory_rollback(root,manifest)
    assert gate['status']=='blocked' and 'v4_projection_reconciliation' in gate['missing']
    target=root/'.runtime/qq-upgrade/rollback-rehearsals/blocked'
    with pytest.raises(ValueError,match='protections missing'):
        build_source_bundle(root,snapshot,manifest,target,data_root=root)
    assert not target.exists()
    manifest['memory_compatibility']=gate['required']
    allowed=check_memory_rollback(root,manifest)
    assert allowed['status']=='compatible_declared' and allowed['execution_authorized'] is False


def test_corrupt_privacy_ledger_does_not_become_empty_requirements(tmp_path):
    root=tmp_path/'source';data(root)
    (root/'knowledge_base/memory_use_state.json').write_text('broken')
    with pytest.raises(ValueError,match='rollback denied'):check_memory_rollback(root,{})


def test_gate_does_not_need_live_backend_or_expose_memory_content(tmp_path):
    root=tmp_path/'source';data(root)
    gate=check_memory_rollback(root,{})
    assert 'PRIVATE_FIXTURE_BODY' not in json.dumps(gate) and gate['execution_authorized'] is False
