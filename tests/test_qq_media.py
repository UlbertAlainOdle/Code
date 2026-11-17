import asyncio,base64,json,time
from dataclasses import asdict,replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from PIL import Image
from fastapi import FastAPI
from saya_app.services.qq_bridge import normalize_event,OneBotClient
from saya_app.services.qq_media import QQMedia,MediaPolicy,MediaConflict,MediaUnavailable,_image_tokens
from saya_app.services.qq_chat import QQChatResponder,context_turn_reference
from saya_app.services import qq_delivery
from saya_app.routers import qq as routes
from test_qq_delivery import config,event,Client
from test_qq_task_scheduler import make,until
from test_qq_group_integration import response

CREDS={'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'text-fixture',
    'model_routes':{'multimodal_model_name':'vision-fixture'}}
def image_event(text='这张图里有什么？',mid=1,user=20000,group=None,token='UNTRUSTED_EVENT_TOKEN'):
    e=event(text,mid,user,group_id=group)
    e['message'].append({'type':'image','data':{'file':token,'url':'http://127.0.0.1/secret'}})
    return e

class MediaClient(Client):
    def __init__(self,raw,path):
        super().__init__();self.raw,self.path,self.calls=raw,path,[];self.release=None;self.entered=None
    async def call(self,action,payload):
        self.calls.append((action,payload))
        if action=='get_msg':return self.raw
        if self.entered:self.entered.set();await self.release.wait()
        return {'file':str(self.path),'url':'http://127.0.0.1/never-fetch','file_size':self.path.stat().st_size}

def configure(media,**kw):
    media.configure('20000',media.state()['media_revision'],MediaPolicy(intake_enabled=True,images_enabled=True,**kw).model_dump(),confirmed=True)

@pytest.fixture
def lab(tmp_path):
    cache=tmp_path/'dedicated';cache.mkdir();path=cache/'picture.bin'
    Image.new('RGB',(16,12),(255,0,0)).save(path,format='PNG')
    raw=image_event();raw['message_id']=str(raw['message_id']);raw['message'][-1]['data']['file']='AUTHENTIC_PROTOCOL_TOKEN'
    client=MediaClient(raw,path);bridge=make(tmp_path/'bridge',None,client=client)
    bridge.store.initialize(7);media=QQMedia(bridge,tmp_path,trusted_roots=[cache]);bridge.media=media
    msg,_=normalize_event(image_event(),bridge.settings)
    return bridge,media,msg,client,path

def test_durable_metadata_does_not_keep_urls_paths_or_image_bytes():
    raw=image_event(text='');m,reason=normalize_event(raw,config())
    assert reason=='accepted' and m.media_types==('image',) and m.media_only
    payload=json.dumps(asdict(m));assert 'UNTRUSTED_EVENT_TOKEN' not in payload and '127.0.0.1' not in payload
    assert normalize_event(image_event(user=40000),config())[1]=='user_not_allowed'
    fake=event('[CQ:image,file=fake]');assert normalize_event(fake,config())[0].media_types==()
    literal=event();literal['message']='&#91;CQ:image,file=literal&#93;';assert normalize_event(literal,config())[0].media_types==()

def test_default_off_and_visual_route_missing_do_not_call_protocol(lab):
    _,media,msg,client,_=lab
    r=asyncio.run(media.prepare(msg,CREDS,'api'));assert not r['attachments'] and '未开启' in r['status'];assert not client.calls
    configure(media)
    r=asyncio.run(media.prepare(msg,CREDS|{'model_routes':{}},'api'));assert '视觉模型' in r['status'] and not client.calls
    with pytest.raises(PermissionError):media.configure('30000',1,{},confirmed=True)
    with pytest.raises(PermissionError):media.configure('20000',1,{},confirmed=False)
    with pytest.raises(MediaConflict):media.configure('20000',0,{},confirmed=True)

def test_same_message_re_read_supplies_only_verified_image_and_no_binary_cache(lab):
    bridge,media,msg,client,path=lab;configure(media)
    r=asyncio.run(media.prepare(msg,CREDS,'api'))
    assert len(r['attachments'])==1 and r['model']=='vision-fixture'
    assert client.calls==[('get_msg',{'message_id':'1'}),('get_image',{'file':'AUTHENTIC_PROTOCOL_TOKEN'})]
    raw=base64.b64decode(r['attachments'][0]['data_url'].split(',')[1]);assert Image.open(BytesIO(raw)).size==(16,12)
    with bridge.store.connect() as db:
        value=db.execute('select snapshot from qq_media_events').fetchone()[0]
    assert 'base64' not in value and str(path) not in value and 'AUTHENTIC' not in value

@pytest.mark.parametrize('mutate',[
    lambda r:r.update(group_id=90001,message_type='group'),
    lambda r:r.update(user_id=30000),lambda r:r.update(message_id='2'),
    lambda r:r.pop('message_id'),
    lambda r:r['message'][-1]['data'].update(file='http://127.0.0.1/private'),
    lambda r:r['message'][-1]['data'].update(file='../../secret.png'),
])
def test_mismatch_or_forged_reference_never_reaches_get_image(lab,mutate):
    _,media,msg,client,_=lab;configure(media);mutate(client.raw)
    result=asyncio.run(media.prepare(msg,CREDS,'api'));assert not result['attachments']
    assert len(client.calls)==1

@pytest.mark.parametrize('kind',['outside','corrupt','extension','pixels','declared_size'])
def test_path_type_pixels_frames_and_size_are_bounded(lab,tmp_path,kind):
    _,media,msg,client,path=lab;configure(media,max_pixels=10000)
    if kind=='outside':
        other=tmp_path/'outside.png';Image.new('RGB',(2,2)).save(other);client.path=other
    elif kind=='corrupt':path.write_bytes(b'not an image')
    elif kind=='extension':Image.new('RGB',(2,2)).save(path,format='BMP')
    elif kind=='pixels':Image.new('RGB',(101,101)).save(path,format='PNG')
    else:client.raw['message'][-1]['data']['file_size']=99999999
    result=asyncio.run(media.prepare(msg,CREDS,'api'));assert not result['attachments']
    if kind=='declared_size':assert len(client.calls)==1

def test_quoted_image_rechecks_group_and_never_reads_bot_derived_images(lab):
    bridge,media,msg,client,_=lab;configure(media,quote_images_enabled=True)
    msg=replace(msg,group_id='90000',scope='10000:group:90000:user:20000',reply_to='5',media_types=())
    client.raw.update(message_id='5',message_type='group',group_id='90000',user_id=30000)
    assert asyncio.run(media.prepare(msg,CREDS,'api'))['attachments']
    client.raw['user_id']=10000
    assert not asyncio.run(media.prepare(msg,CREDS,'api'))['attachments']
    client.raw.update(user_id=30000,group_id='90001')
    assert not asyncio.run(media.prepare(msg,CREDS,'api'))['attachments']

def test_revocation_after_read_starts_discards_late_bytes(lab):
    async def run():
        _,media,msg,client,_=lab;configure(media)
        client.entered=asyncio.Event();client.release=asyncio.Event()
        task=asyncio.create_task(media.prepare(msg,CREDS,'api'));await asyncio.wait_for(client.entered.wait(),2)
        media.configure('20000',1,MediaPolicy().model_dump(),confirmed=True);client.release.set()
        with pytest.raises(MediaConflict):await task
    asyncio.run(run())

def test_media_cache_cannot_continue_after_policy_change(lab):
    bridge,media,msg,_,_=lab;configure(media)
    asyncio.run(media.prepare(msg,CREDS,'api'))
    scope=msg.scope+':shared';result=qq_delivery.create(bridge.store,msg,scope,'answer。'*300,300)
    assert media.valid_delivery(msg,result)
    media.configure('20000',1,MediaPolicy().model_dump(),confirmed=True)
    assert not media.valid_delivery(msg,result);media.prepare_delivery_scope(msg,scope)
    with bridge.store.connect() as db:assert db.execute('select reason from qq_delivery_stops where result_id=?',(result,)).fetchone()[0]=='cancelled'


def test_request_budget_counts_normalized_image_bytes_and_cleanup_keeps_live_provenance(lab):
    bridge,media,msg,client,_=lab;configure(media,max_total_bytes=4096)
    client.raw['message'].append({'type':'image','data':{'file':'SECOND_PROTOCOL_TOKEN'}})
    with patch.object(media,'_read_image',lambda *a:(b'n'*3000,100)):
        assert not asyncio.run(media.prepare(msg,CREDS,'api'))['attachments']
    result=qq_delivery.create(bridge.store,msg,msg.scope+':shared','cached answer',300)
    media.prune()
    with bridge.store.connect() as db:assert db.execute('select count(*) from qq_media_events').fetchone()[0]==1
    with bridge.store.connect() as db:db.execute('delete from qq_deliveries where id=?',(result,))
    media.prune()
    with bridge.store.connect() as db:assert db.execute('select count(*) from qq_media_events').fetchone()[0]==0

def test_bounded_protocol_metadata_rejects_oversize_without_following_url():
    async def run():
        client=OneBotClient(config());await client.http.aclose();calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,content=b'x'*(512*1024+1))
        client.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(RuntimeError,match='metadata_too_large'):await client.call_bounded('get_image',{'file':'opaque'})
            with pytest.raises(ValueError):await client.call_bounded('send_msg',{})
            assert len(calls)==1
        finally:await client.close()
    asyncio.run(run())

def test_actual_responder_gets_current_image_and_disables_automatic_writes(lab):
    async def run():
        bridge,media,msg,_,_=lab;configure(media);captured=[]
        async def single(req,channel_turn):captured.append((req,channel_turn));return response('图中有红色。')
        responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',lambda:CREDS)
        responder.media=media
        answer=await responder(msg,[],'session_qq_fixture.jsonl')
        req,turn=captured[0];assert req.attachments[0]['data_url'].startswith('data:image/png;base64,')
        assert req.multimodal_model_name=='vision-fixture' and not req.memory_write_enabled and not req.agent_mode
        assert req.user_input==msg.text and turn['media_context']['image_count']==1
        assert 'untrusted reference DATA' in context_turn_reference(turn)
        assert '红色' in str(answer)
    asyncio.run(run())

def test_media_policy_http_requires_owner_explicit_confirmation_and_cas(lab):
    async def run():
        bridge,media,_,_,_=lab;bridge.memory_management=SimpleNamespace();await bridge.start()
        app=FastAPI();app.include_router(routes.router)
        try:
            with patch.object(routes,'qq_bridge',bridge):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as web:
                    assert (await web.get('/api/qq/media-policy')).status_code==401
                    h={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'};await web.post('/api/qq/ui-session',headers=h)
                    state=(await web.get('/api/qq/media-policy')).json()
                    request={'owner_id':'20000','revision':state['revision'],'media_revision':0,'confirmed':False,'values':MediaPolicy().model_dump()}
                    assert (await web.put('/api/qq/media-policy',headers=h,json=request)).status_code==403
                    request['confirmed']='true';assert (await web.put('/api/qq/media-policy',headers=h,json=request)).status_code==422
                    request['confirmed']=True;assert (await web.put('/api/qq/media-policy',headers=h,json=request)).status_code==200
                    assert (await web.put('/api/qq/media-policy',headers=h,json=request)).status_code==409
        finally:await bridge.stop()
    asyncio.run(run())
