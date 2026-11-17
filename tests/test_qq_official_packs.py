import asyncio,json
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from fastapi import FastAPI
from saya_app.services.qq_favorites import QQFavorites,FavoriteConflict
from saya_app.services.qq_official_packs import QQOfficialPacks
from saya_app.routers import qq as routes
from test_qq_task_scheduler import make


def official(pack=123):return {'type':'image','data':{'emoji_package_id':pack,'emoji_id':'a'*32,'key':'b'*32,'summary':'惊讶','url':'http://127.0.0.1/DO_NOT_USE'}}


def lab(tmp_path):
    b=make(tmp_path,None);b.store.initialize(7);b.memory_management=SimpleNamespace();b.task=SimpleNamespace(done=lambda:False)
    b.favorites=QQFavorites(b);b.official_packs=QQOfficialPacks(b)
    origin={'bot_id':'10000','user_id':'20000','group_id':'','message_id':'3'}
    with b.store.connect() as db:
        db.execute('insert into qq_deliveries values(?,?,?,?,?,?,?,?)',('r','k','s',json.dumps(origin),'final','reply',1,9999999999))
    raw={'message_id':3,'message_type':'private','user_id':20000,'message':[official()]};calls=[]
    async def call(action,payload):calls.append((action,payload));return raw
    b.client=SimpleNamespace(call=call)
    return b,raw,calls


def test_observed_official_pack_parallel_to_favorites_and_not_full_inventory(tmp_path):
    b,raw,calls=lab(tmp_path);s=b.official_packs
    state=asyncio.run(s.refresh(0,owner_id='20000'))
    assert state['packages'][0]['id']=='123' and len(state['items'])==1
    assert not state['inventory_complete'] and not state['items'][0]['selected']
    assert calls==[('get_msg',{'message_id':'3'})]
    assert b.favorites.state()['catalogue_revision']==0 and not b.favorites.state()['items']
    assert 'url' not in state['items'][0] and 'key' not in state['items'][0]
    _,_,item=s._entry(state['items'][0]['id']);assert item['url'].startswith('https://gxh.vip.qq.com/')
    key=item['id'];s.select(1,[{'id':key,'selected':True,'meaning':'惊讶但不要用于坏消息'}])
    raw['message']=[]
    state=asyncio.run(s.refresh(2,owner_id='20000'))
    assert state['items'][0]['selected'] # samples are not a full deletion inventory
    with pytest.raises(ValueError):b.favorites.select(0,[{'id':key,'selected':True,'meaning':'wrong source'}])


@pytest.mark.parametrize('mutate',[
    lambda raw:raw.update(user_id=30000),lambda raw:raw.update(message_type='group',group_id=90000),
    lambda raw:raw.update(message_id=4),lambda raw:raw['message'][0]['data'].update(emoji_id='../x'),
    lambda raw:raw['message'][0]['data'].update(emoji_package_id='0'),lambda raw:raw['message'][0]['data'].pop('key')])
def test_forged_or_cross_scope_official_elements_not_imported(tmp_path,mutate):
    b,raw,_=lab(tmp_path);mutate(raw)
    assert not asyncio.run(b.official_packs.refresh(0,owner_id='20000'))['items']


def test_official_directory_requires_owner_and_stale_revision_fails_before_read(tmp_path):
    b,_,calls=lab(tmp_path)
    with pytest.raises(PermissionError):asyncio.run(b.official_packs.refresh(0,owner_id='30000'))
    with pytest.raises(FavoriteConflict):asyncio.run(b.official_packs.refresh(10,owner_id='20000'))
    assert not calls


def test_cdn_only_uses_verified_official_id():
    item=QQOfficialPacks.official_element(official())
    assert QQOfficialPacks.image_url(item['url'])==item['url']
    for url in ['https://gxh.vip.qq.com.evil/a','http://gxh.vip.qq.com/a','https://gxh.vip.qq.com/../a','http://127.0.0.1/a']:
        with pytest.raises(ValueError):QQOfficialPacks.image_url(url)


def test_api_source_routing_does_not_overwrite_favorite_list(tmp_path):
    async def run():
        b,_,_=lab(tmp_path);app=FastAPI();app.include_router(routes.router)
        with patch.object(routes,'qq_bridge',b):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as web:
                h={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'};await web.post('/api/qq/ui-session',headers=h)
                body={'owner_id':'20000','catalogue_revision':0,'confirmed':True}
                result=await web.post('/api/qq/favorites/refresh?source=official',headers=h,json=body)
                assert result.status_code==200 and result.json()['packages']
                assert not (await web.get('/api/qq/favorites')).json()['items']
                assert (await web.get('/api/qq/favorites?source=invalid')).status_code==422
    asyncio.run(run())
