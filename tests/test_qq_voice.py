import asyncio,io,json,threading,time,wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import httpx,pytest
from saya_app.services.qq_bridge import OneBotClient,normalize_event
from saya_app.services.qq_media import QQMedia,MediaPolicy
from saya_app.services.qq_voice import QQVoice
from saya_app.services.local_tts import VoiceSettings,LocalTTS
from test_qq_delivery import Client,event,config
from test_qq_task_scheduler import make,until


def wav(seconds=.25,channels=1):
    data=io.BytesIO()
    with wave.open(data,'wb') as out:
        out.setparams((channels,2,24000,0,'NONE','not compressed'))
        out.writeframes(b'\x00\x10'*(int(seconds*24000)*channels))
    return data.getvalue()


class FakeTTS:
    def __init__(self):
        self.value=VoiceSettings(engine='transformers');self.calls=[];self.entered=threading.Event();self.release=None;self.fail=False;self.audio=wav()
    def settings(self):return self.value
    def synthesize_ephemeral(self,text,settings,*,session_id=None):
        self.calls.append((text,settings,session_id));self.entered.set()
        if self.release:self.release.wait(20)
        if self.fail:raise RuntimeError('private tts details')
        return self.audio


class VoiceClient(Client):
    def __init__(self):super().__init__();self.voices=[];self.fail_voice=False
    async def send_voice(self,message,path):
        self.voices.append((message,Path(path),Path(path).read_bytes()))
        if self.fail_voice:raise TimeoutError('transport details')
        return '700'


async def lab(tmp_path,answer='你好，我在。',enabled=True,**settings):
    calls=[]
    async def model(message,history,session):calls.append(message);return answer
    client=VoiceClient();bridge=make(tmp_path/'bridge',model,client=client,**settings)
    await bridge.start();bridge.media=QQMedia(bridge,tmp_path)
    bridge.media.configure('20000',0,MediaPolicy(voice_reply_enabled=enabled).model_dump(),confirmed=True)
    tts=FakeTTS();bridge.voice=QQVoice(bridge,tmp_path,tts)
    return bridge,tts,client,calls


def submit(bridge,text='你好',mid=1):return bridge.submit_signed(json.dumps(event(text,mid)).encode(),'Bearer inbound')


def row(bridge):
    with bridge.store.connect() as db:
        r=db.execute('select * from qq_voice_deliveries order by created desc limit 1').fetchone()
    return dict(r) if r else None


def test_default_off_keeps_original_text_and_has_no_voice_side_effect(tmp_path):
    async def run():
        b,tts,c,_=await lab(tmp_path,enabled=False)
        try:
            submit(b);await b.queue.join()
            assert c.sent and not tts.calls and not c.voices and row(b) is None
        finally:await b.stop()
    asyncio.run(run())


def test_text_confirmed_before_voice_one_receipt_no_replay_or_cache_read_cross_scope(tmp_path):
    async def run():
        b,tts,c,calls=await lab(tmp_path);tts.release=threading.Event()
        try:
            submit(b);await until(tts.entered.is_set)
            assert len(c.sent)==1 and not c.voices and b.status()['counts']['voice_rendering']==1
            tts.release.set();await b.queue.join()
            r=row(b);assert r['state']=='sent' and r['receipt']=='700' and len(c.voices)==1
            scope='10000:private:20000:shared'
            message=c.sent[0][0]
            await b.voice.after_text(message,scope,r['result_id']);assert len(tts.calls)==1
            submit(b,'/continue '+r['result_id'],2);await b.queue.join()
            assert len(calls)==1 and len(c.voices)==1
            text=json.dumps(r);assert '你好' not in text and 'data:' not in text
        finally:tts.release.set();await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('answer,question,changes',[
    ('长回答。'*100,'你好',{}),('```python\nprint(1)\n```','你好',{}),('https://example.invalid','你好',{}),
    ('短回答。','这次不要语音',{}),('短回答。','你好',{'delivery_mode':'legacy'})])
def test_long_code_links_optout_and_legacy_keep_text_only(tmp_path,answer,question,changes):
    async def run():
        b,tts,c,_=await lab(tmp_path,answer,**changes)
        try:submit(b,question);await b.queue.join();assert c.sent and not tts.calls and not c.voices
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('stop',['cancel','voice_revoke','settings_change'])
def test_cancel_or_revocation_during_tts_discards_late_audio_and_keeps_shared_service(tmp_path,stop):
    async def run():
        b,tts,c,_=await lab(tmp_path);tts.release=threading.Event()
        try:
            submit(b);await until(tts.entered.is_set)
            if stop=='cancel':
                submit(b,'/cancel',2);await asyncio.wait_for(b.task_controls.join(),1)
            elif stop=='voice_revoke':b.media.configure('20000',1,{'voice_reply_enabled':False},confirmed=True)
            else:tts.value=tts.value.model_copy(update={'temperature':.7})
            await asyncio.wait_for(b.queue.join(),2)
            assert c.sent and not c.voices and b.voice.busy # shared synthesis is allowed to settle
            tts.release.set();await until(lambda:not b.voice.busy)
            assert not c.voices and not list(b.voice.directory.glob('*.wav'))
            assert row(b)['state']=='cancelled'
        finally:tts.release.set();await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('failure',['model','bad_wav','duration','stereo','transport'])
def test_voice_faults_never_relabel_confirmed_text_or_retry_audio(tmp_path,failure):
    async def run():
        b,tts,c,_=await lab(tmp_path)
        if failure=='model':tts.fail=True
        if failure=='bad_wav':tts.audio=b'not wav'
        if failure=='duration':tts.audio=wav(31)
        if failure=='stereo':tts.audio=wav(channels=2)
        if failure=='transport':c.fail_voice=True
        try:
            submit(b);await b.queue.join();r=row(b)
            assert c.sent and r['state']==('unknown' if failure=='transport' else 'failed')
            with b.store.connect() as db:assert db.execute("select state from inbox where event_key=?",(c.sent[0][0].key,)).fetchone()[0]=='sent'
            await b.voice.after_text(c.sent[0][0],'10000:private:20000:shared',r['result_id'])
            assert len(tts.calls)==1 and len(c.voices)==int(failure=='transport')
        finally:await b.stop()
    asyncio.run(run())


def test_voice_timeout_releases_qq_task_but_drains_background_before_upgrade(tmp_path):
    async def run():
        b,tts,c,_=await lab(tmp_path);tts.release=threading.Event()
        b.media.configure('20000',1,{'voice_timeout_seconds':5},confirmed=True)
        try:
            submit(b);await until(tts.entered.is_set);await asyncio.wait_for(b.queue.join(),7)
            assert row(b)['state']=='timed_out' and not c.voices
            assert b.status()['counts']['voice_rendering']==1
            tts.release.set();await until(lambda:not b.voice.busy)
            assert b.status()['counts']['voice_rendering']==0
        finally:tts.release.set();await b.stop()
    asyncio.run(run())


def test_expired_audio_is_removed_and_restart_unknown_is_not_retried(tmp_path):
    async def run():
        b,tts,c,_=await lab(tmp_path)
        try:
            submit(b);await b.queue.join();r=row(b);path=c.voices[0][1];assert path.exists()
            with b.store.connect() as db:db.execute("update qq_voice_deliveries set state='sending'")
            voice=QQVoice(b,tmp_path,tts);assert row(b)['state']=='unknown'
            await voice.after_text(c.sent[0][0],'10000:private:20000:shared',r['result_id']);assert len(tts.calls)==1
            with b.store.connect() as db:db.execute('update qq_voice_deliveries set expires=?',(time.time()-1,))
            voice.prune();assert not path.exists() and row(b)['state']=='expired'
        finally:await b.stop()
    asyncio.run(run())


def test_other_scope_cannot_claim_voice_result_and_self_record_cannot_trigger_loop(tmp_path):
    async def run():
        b,tts,c,_=await lab(tmp_path,enabled=False)
        try:
            submit(b);await b.queue.join()
            with b.store.connect() as db:r=db.execute("select id from qq_deliveries where kind='final'").fetchone()[0]
            b.media.configure('20000',1,{'voice_reply_enabled':True},confirmed=True)
            wrong=replace(c.sent[0][0],user_id='30000')
            await b.voice.after_text(wrong,'10000:private:20000:shared',r)
            assert not tts.calls and row(b) is None
            self_event=event('',2,user_id=10000);self_event['message']=[{'type':'record','data':{'file':'opaque'}}]
            assert b.submit_signed(json.dumps(self_event).encode(),'Bearer inbound')[0]=='ignored'
        finally:await b.stop()
    asyncio.run(run())


def test_onebot_voice_uses_only_generated_file_and_correct_recipient_without_retry(tmp_path):
    async def run():
        c=OneBotClient(config());await c.http.aclose();calls=[]
        def handler(request):calls.append(json.loads(request.content));return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'message_id':888}})
        c.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        path=tmp_path/'private voice.wav';path.write_bytes(wav())
        message,_=normalize_event(event(group_id=90000),config())
        try:
            assert await c.send_voice(message,path)=='888';assert len(calls)==1
            body=calls[0];assert body['group_id']==90000 and body['message'][0]['type']=='record'
            assert body['message'][0]['data']['file']==path.resolve().as_uri()
        finally:await c.close()
    asyncio.run(run())
