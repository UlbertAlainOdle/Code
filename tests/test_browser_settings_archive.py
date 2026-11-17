import json
from unittest.mock import patch
import pytest
from saya_app.services.browser_settings_archive import BrowserSettingsArchive
from saya_app.services.credentials_storage import CredentialsConflict
from saya_app.services.state_storage import StateSaveError


def test_archive_preserves_exact_raw_settings_and_keeps_missing_values_and_history(tmp_path):
    store=BrowserSettingsArchive(tmp_path/'browser_settings.json')
    camera=' {"_view":{"camPos":[1,2,3]}, "ambient":0.9} '
    first=store.save({'saya_mmd_3d_settings':camera,'saya_weather_city':'Shanghai'},store.status()['revision'],source_label='correct browser')
    assert store.load()['values']['saya_mmd_3d_settings']==camera
    assert 'camPos' not in json.dumps(first)
    preview=store.preview({'saya_weather_city':'Tokyo'},first['revision'])
    assert preview['changes'][0]['action']=='conflict' and preview['retained_count']==1
    with pytest.raises(CredentialsConflict):store.save({'saya_weather_city':'Tokyo'},first['revision'])
    second=store.save({'saya_weather_city':'Tokyo'},first['revision'],True)
    assert store.load()['values']['saya_mmd_3d_settings']==camera
    assert store.load()['history'][0]['values']['saya_weather_city']=='Shanghai'
    assert not second['runtime_applied']
    with pytest.raises(CredentialsConflict):store.save({'saya_weather_city':'old'},first['revision'],True)


@pytest.mark.parametrize('values',[
    {'saya_api_settings_v1':'{"api_key":"fake"}'}, {'saya_current_role_mode':5},
    {'saya_mmd_3d_settings':'[] trailing'}, {'saya_mmd_light_defaults':'null'},
    {'unknown':'{}'}, {'saya_mmd_3d_settings':'{"value":NaN}'},
])
def test_archive_rejects_unknown_credentials_and_invalid_shapes(tmp_path,values):
    store=BrowserSettingsArchive(tmp_path/'settings.json')
    with pytest.raises(ValueError):store.save(values,store.status()['revision'])
    assert not store.path.exists()


def test_archive_disk_failure_corruption_and_bounded_history(tmp_path):
    store=BrowserSettingsArchive(tmp_path/'settings.json')
    for i in range(6):store.save({'saya_weather_city':str(i)},store.status()['revision'],True)
    assert len(store.load()['history'])==3
    before=store.path.read_bytes()
    with patch('saya_app.services.browser_settings_archive.atomic_save_json',side_effect=StateSaveError('disk full')):
        with pytest.raises(StateSaveError):store.save({'saya_weather_city':'new'},store.status()['revision'],True)
    assert store.path.read_bytes()==before
    store.path.write_text('[]','utf-8')
    with pytest.raises(ValueError):store.status()


def test_archive_api_is_authenticated_versioned_and_does_not_return_raw_values(tmp_path,monkeypatch):
    import asyncio,httpx
    from fastapi import FastAPI
    from saya_app.routers import qq as routes
    monkeypatch.setattr(routes,'BROWSER_ARCHIVE_PATH',tmp_path/'archive.json')
    async def run():
        app=FastAPI();app.include_router(routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
            assert (await client.get('/api/qq/browser-settings')).status_code==401
            client.headers.update({'Origin':'http://127.0.0.1:8000','X-Saya-QQ-UI':'1'})
            assert (await client.post('/api/qq/ui-session')).status_code==200
            status=(await client.get('/api/qq/browser-settings')).json()
            body={'values':{'saya_mmd_3d_settings':'{"camera":"PRIVATE_CAMERA_FIXTURE"}'},'revision':status['revision']}
            preview=await client.post('/api/qq/browser-settings/preview',json=body)
            assert preview.status_code==200 and 'PRIVATE_CAMERA_FIXTURE' not in preview.text
            assert not (tmp_path/'archive.json').exists()
            saved=await client.put('/api/qq/browser-settings',json=body)
            assert saved.status_code==200 and 'PRIVATE_CAMERA_FIXTURE' not in saved.text
            assert (await client.put('/api/qq/browser-settings',json=body)).status_code==409
            assert 'PRIVATE_CAMERA_FIXTURE' not in (await client.get('/api/qq/browser-settings')).text
    asyncio.run(run())
