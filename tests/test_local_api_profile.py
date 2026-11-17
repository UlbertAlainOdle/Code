import asyncio
import json
from unittest.mock import patch

from fastapi import FastAPI
import httpx
import pytest

from saya_app.services.local_api_profile import LocalApiProfile
from saya_app.services.credentials_storage import CredentialsConflict, read_credentials, update_credentials
from saya_app.services.state_storage import StateSaveError
from saya_app.services import conversation
from saya_app.routers import qq as routes


BUNDLE = {'api_key':'fake-model-secret','api_url':'http://model.invalid/v1','api_model_name':'model-a'}


def test_revisioned_merge_does_not_clear_or_leak_and_rejects_stale_writer(tmp_path):
    store=LocalApiProfile(tmp_path/'credentials.json')
    empty=store.status()
    first=store.save(BUNDLE | {'brave_search_api_key':'fake-search-secret'},empty['revision'])
    assert 'fake-model-secret' not in json.dumps(first) and 'fake-search-secret' not in json.dumps(first)
    assert first['configured']['brave_search_api_key']
    with pytest.raises(CredentialsConflict):
        store.save({'brave_search_api_key':'stale'},empty['revision'],True)
    with pytest.raises(CredentialsConflict):
        store.save({'brave_search_api_key':'different'},first['revision'])
    second=store.save({'brave_search_api_key':'','multimodal_model_name':'vision'},first['revision'])
    assert store.read()['brave_search_api_key']=='fake-search-secret'
    assert store.read()['api_key']=='fake-model-secret' and second['revision'] != first['revision']


def test_explicit_clear_and_model_bundle_guard(tmp_path):
    store=LocalApiProfile(tmp_path/'credentials.json')
    for partial in ({'api_key':'fake'}, {'api_url':'http://example.invalid'}):
        with pytest.raises(ValueError):store.save(partial,store.status()['revision'])
    first=store.save(BUNDLE | {'brave_search_api_key':'fake'},store.status()['revision'])
    store.save({},first['revision'],clear_fields=['api_url','brave_search_api_key'])
    assert all(store.read()[key]=='' for key in ('api_key','api_url','api_model_name','brave_search_api_key'))


def test_disk_failure_and_legacy_write_cannot_replace_managed_profile(tmp_path,monkeypatch):
    path=tmp_path/'credentials.json';store=LocalApiProfile(path)
    first=store.save(BUNDLE,store.status()['revision'])
    before=path.read_bytes();monkeypatch.setattr(conversation,'CREDENTIALS_FILE',path)
    assert conversation.ConversationService().save_credentials(api_key='stale-cache')['saved'] is False
    assert path.read_bytes()==before
    with patch('saya_app.services.credentials_storage.atomic_save_json',side_effect=StateSaveError('disk full')):
        with pytest.raises(StateSaveError):store.save({'brave_search_api_key':'new'},first['revision'])
    assert path.read_bytes()==before


@pytest.mark.parametrize('values',[
    {'owner_ids':'fake'}, {'api_key':5}, {'brave_search_api_key':'a'*513}, {'tts_api_url':'javascript:fake'},
    {'translation_secret_key':'fake\nheader'}, {'memory_strong_model_name':['fake']},
])
def test_invalid_values_preserve_disk(tmp_path,values):
    store=LocalApiProfile(tmp_path/'credentials.json')
    with pytest.raises(ValueError):store.save(values,store.status()['revision'])
    assert not store.path.exists()


def test_environment_bundle_and_search_clear_never_revive_legacy(tmp_path,monkeypatch):
    path=tmp_path/'credentials.json';store=LocalApiProfile(path)
    status=store.save(BUNDLE | {'brave_search_api_key':'fake-search'},store.status()['revision'])
    store.save({},status['revision'],clear_fields=['brave_search_api_key'])
    monkeypatch.setattr(conversation,'CREDENTIALS_FILE',path)
    monkeypatch.setenv('API_KEY','fake-env');monkeypatch.setenv('API_URL','http://env.invalid');monkeypatch.setenv('API_MODEL','env-model')
    monkeypatch.delenv('BRAVE_SEARCH_API_KEY',raising=False)
    result=conversation.ConversationService().credentials()
    assert (result['api_key'],result['api_url'],result['model'])==('fake-env','http://env.invalid','env-model')
    assert result['brave_search_api_key']==''


def test_routes_require_local_same_origin_and_secret_read_is_no_store(tmp_path,monkeypatch):
    path=tmp_path/'credentials.json';monkeypatch.setattr(conversation,'CREDENTIALS_FILE',path)
    from saya_app.services.qq_controls import QQSearchCredentialsStore
    monkeypatch.setattr(routes.qq_bridge,'search_credentials',QQSearchCredentialsStore(tmp_path/'legacy.json',path))
    async def run():
        app=FastAPI();app.include_router(routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as web:
            assert (await web.get('/api/qq/local-profile')).status_code==401
            web.headers.update({'Origin':'http://127.0.0.1:8000','X-Saya-QQ-UI':'1'})
            assert (await web.post('/api/qq/ui-session')).status_code==200
            status=(await web.get('/api/qq/local-profile')).json()
            del web.headers['Origin']
            assert (await web.get('/api/qq/local-profile')).status_code==200  # Browsers omit Origin on same-origin GET.
            assert (await web.post('/api/qq/local-profile/read',json={'revision':status['revision'],'purpose':'runtime'})).status_code in {401,403}
            web.headers['Origin']='http://127.0.0.1:8000'
            result=await web.put('/api/qq/local-profile',json={'revision':status['revision'],'values':BUNDLE})
            assert result.status_code==200 and 'fake-model-secret' not in result.text
            status=result.json()
            assert (await web.put('/api/qq/local-profile',json={'revision':'stale','values':{}})).status_code==409
            read=await web.post('/api/qq/local-profile/read',json={'revision':status['revision'],'purpose':'browser_sync'})
            assert read.status_code==200 and read.json()['settings']['api_key']=='fake-model-secret'
            assert read.headers['cache-control']=='no-store'
            assert (await web.post('/api/qq/credentials',json=BUNDLE)).status_code==409
            assert (await web.post('/api/qq/search-credentials',json={'api_key':'stale'})).status_code==409
            web.headers['Origin']='http://evil.invalid'
            assert (await web.post('/api/qq/local-profile/read',json={'revision':status['revision'],'purpose':'runtime'})).status_code in {401,403}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('192.0.2.1',1234)),base_url='http://127.0.0.1:8000',headers={'Origin':'http://127.0.0.1:8000','X-Saya-QQ-UI':'1'}) as remote:
            assert (await remote.post('/api/qq/ui-session')).status_code==403
    asyncio.run(run())


def test_out_of_band_write_invalidates_revision_and_corrupt_file_not_overwritten(tmp_path):
    path=tmp_path/'credentials.json';store=LocalApiProfile(path)
    first=store.status();update_credentials(path,{'brave_search_api_key':'other-writer'})
    with pytest.raises(CredentialsConflict):store.save({},first['revision'])
    path.write_text('[]',encoding='utf-8')
    with pytest.raises(ValueError):store.status()
    assert path.read_text('utf-8')=='[]'


def test_passive_legacy_request_cannot_replace_existing_unmanaged_model(tmp_path,monkeypatch):
    path=tmp_path/'credentials.json';update_credentials(path,BUNDLE)
    monkeypatch.setattr(conversation,'CREDENTIALS_FILE',path)
    before=path.read_bytes()
    assert conversation.ConversationService().save_credentials(api_key='stale',from_request=True)['saved'] is False
    assert path.read_bytes()==before
