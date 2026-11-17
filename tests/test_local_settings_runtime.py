import asyncio,json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx,pytest
from saya_app.services.browser_settings_archive import BrowserSettingsArchive
from saya_app.services.credentials_storage import CredentialsConflict
from saya_app.services.state_storage import StateSaveError


def seeded(tmp_path):
    store=BrowserSettingsArchive(tmp_path/'settings.json')
    store.save({'saya_weather_city':'Correct city','saya_mmd_3d_settings':' {"ambient":0.9} '},store.status()['revision'])
    return store


def test_explicit_activation_preserves_archive_values_and_raw_browser_import_format(tmp_path):
    store=seeded(tmp_path);before=store.load()['values']
    assert store.runtime()['values']=={} and not store.status()['runtime_applied']
    data=store.update_runtime(store.status()['revision'],enabled=True)
    assert data['runtime_applied'] and data['values']==before
    assert store.load()['values']==before
    store.save({'saya_thinking_effort':'auto'},data['revision'])
    assert store.runtime()['runtime_applied']


def test_patch_clear_restart_and_disable_preserve_user_preferences(tmp_path):
    store=seeded(tmp_path);enabled=store.update_runtime(store.status()['revision'],enabled=True)
    changed=store.update_runtime(enabled['revision'],values={'saya_thinking_effort':'high'},clear_fields=['saya_weather_city'])
    assert 'saya_weather_city' not in changed['values'] and changed['values']['saya_thinking_effort']=='high'
    reloaded=BrowserSettingsArchive(store.path)
    assert reloaded.runtime()==changed
    assert reloaded.update_runtime(changed['revision'],enabled=False)['values']=={}
    assert reloaded.load()['values']['saya_thinking_effort']=='high'
    assert 'saya_weather_city' not in reloaded.load()['values']


def test_atomic_write_failure_and_cas_do_not_change_active_policy_or_file(tmp_path):
    store=seeded(tmp_path);old_revision=store.status()['revision'];before=store.path.read_bytes()
    with patch('saya_app.services.browser_settings_archive.atomic_save_json',side_effect=StateSaveError('fixture disk full')):
        with pytest.raises(StateSaveError):store.update_runtime(old_revision,enabled=True)
    assert store.path.read_bytes()==before and not store.status()['runtime_applied']
    store.update_runtime(old_revision,enabled=True)
    with pytest.raises(CredentialsConflict):store.update_runtime(old_revision,values={'saya_weather_city':'stale'})
    assert store.load()['values']['saya_weather_city']=='Correct city'


def test_concurrent_edits_have_one_winner_and_no_silent_last_writer_override(tmp_path):
    store=seeded(tmp_path);revision=store.update_runtime(store.status()['revision'],enabled=True)['revision']
    def edit(value):
        try:store.update_runtime(revision,values={'saya_weather_city':value});return 'saved'
        except CredentialsConflict:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(edit,['A','B']))
    assert sorted(results)==['conflict','saved']


@pytest.mark.parametrize('kwargs',[
    {'values':{'saya_api_settings_v1':'{}'}},
    {'values':{'saya_user_profile_v1':'{}'}},
    {'clear_fields':['saya_style_settings_v1']},
    {'values':{'saya_weather_city':'x'},'clear_fields':['saya_weather_city']},
    {'clear_fields':[{}]}, {'enabled':'true'}, {'enabled':False,'values':{'saya_weather_city':'x'}},
])
def test_runtime_rejects_credentials_server_projections_ambiguous_and_invalid_changes(tmp_path,kwargs):
    store=seeded(tmp_path);store.update_runtime(store.status()['revision'],enabled=True);before=store.path.read_bytes()
    with pytest.raises(ValueError):store.update_runtime(store.status()['revision'],**kwargs)
    assert store.path.read_bytes()==before


def test_uninitialized_runtime_does_not_activate_from_defaults_or_accept_background_writes(tmp_path):
    store=BrowserSettingsArchive(tmp_path/'empty.json')
    with pytest.raises(ValueError):store.update_runtime(store.status()['revision'],enabled=True)
    with pytest.raises(CredentialsConflict):store.update_runtime(store.status()['revision'],values={'saya_weather_city':'old cache'})
    assert not store.path.exists()


def test_authenticated_runtime_projection_origin_boundaries_revision_and_safe_status(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from saya_app.routers import qq as routes
    store=seeded(tmp_path);monkeypatch.setattr(routes,'BROWSER_ARCHIVE_PATH',store.path)
    async def run():
        app=FastAPI();app.include_router(routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
            assert (await client.get('/api/qq/browser-settings/runtime')).status_code==401
            client.headers.update({'Origin':'http://127.0.0.1:8000','X-Saya-QQ-UI':'1'})
            assert (await client.post('/api/qq/ui-session')).status_code==200
            initial=(await client.get('/api/qq/browser-settings/runtime')).json()
            enabled=await client.put('/api/qq/browser-settings/runtime',json={'revision':initial['revision'],'enabled':True})
            assert enabled.status_code==200 and enabled.json()['values']['saya_weather_city']=='Correct city'
            assert enabled.headers['cache-control']=='no-store'
            assert 'Correct city' not in (await client.get('/api/qq/browser-settings')).text
            assert (await client.put('/api/qq/browser-settings/runtime',json={'revision':initial['revision'],'enabled':False})).status_code==409
            assert (await client.put('/api/qq/browser-settings/runtime',json={'revision':enabled.json()['revision'],'enabled':None})).status_code==400
            client.headers['Origin']='https://untrusted.example'
            assert (await client.get('/api/qq/browser-settings/runtime')).status_code==401
            assert (await client.put('/api/qq/browser-settings/runtime',json={})).status_code==401
    asyncio.run(run())


def test_preferences_backup_restores_settings_and_policy_without_copying_credentials(tmp_path):
    from saya_app.services.backup_manager import BackupManager
    kb=tmp_path/'kb';kb.mkdir()
    store=BrowserSettingsArchive(kb/'browser_settings.json')
    store.save({'saya_weather_city':'original'},store.status()['revision'])
    store.update_runtime(store.status()['revision'],enabled=True)
    (kb/'api_credentials.json').write_text('{"api_key":"PRIVATE_FIXTURE"}','utf-8')
    backup=BackupManager(kb,tmp_path/'chats')
    created=backup.create_backup(['preferences'])
    assert [f['path'] for f in created['files']]==['knowledge/browser_settings.json']
    store.update_runtime(store.status()['revision'],values={'saya_weather_city':'edited'})
    stale=store.status()['revision']
    preview=backup.preview_restore(created['backup_id'],['preferences'])
    backup.restore(created['backup_id'],['preferences'],preview['confirmation_token'])
    assert store.runtime()['runtime_applied'] and store.load()['values']['saya_weather_city']=='original'
    with pytest.raises(CredentialsConflict):store.update_runtime(stale,values={'saya_weather_city':'stale'})
    assert json.loads((kb/'api_credentials.json').read_text('utf-8'))['api_key']=='PRIVATE_FIXTURE'


def test_preferences_backup_rejects_invalid_setting_schema(tmp_path):
    from saya_app.services.backup_manager import BackupManager
    kb=tmp_path/'kb';kb.mkdir();(kb/'browser_settings.json').write_text('{"values":{"saya_api_settings_v1":"secret"}}','utf-8')
    backup=BackupManager(kb,tmp_path/'chats')
    with pytest.raises(ValueError):backup.create_backup(['preferences'])
