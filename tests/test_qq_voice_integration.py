import asyncio,json,threading,time
from pathlib import Path
from unittest.mock import patch
import httpx
from core_services import KnowledgeBase,MemoryManager
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_media import QQMedia,MediaPolicy
from saya_app.services.qq_voice import QQVoice,MAX_CACHE_FILES
from saya_app.services.qq_memory_access import MemoryActor
from saya_app.services.qq_memory_management import policy_content_revision
from saya_app.services.conversation import conversation_service
from test_qq_voice import FakeTTS,VoiceClient,lab,submit
from test_qq_task_scheduler import make,until
from test_qq_group_integration import lab as group_lab,configure,response,submit as submit_group
from test_qq_chat_runtime import server
from test_qq_media import CREDS


def test_actual_generator_confirmed_text_then_voice_no_second_model_or_memory_write(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'));captured=[]
    async def run():
        client=VoiceClient();b=make(tmp_path/'bridge',None,client=client);await b.start()
        b.media=QQMedia(b,tmp_path);b.media.configure('20000',0,MediaPolicy(voice_reply_enabled=True).model_dump(),confirmed=True)
        tts=FakeTTS();b.voice=QQVoice(b,tmp_path,tts)
        def model(request):
            captured.append(json.loads(request.content));delta={'choices':[{'delta':{'content':'你好，我一直都在。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(delta)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                b.responder=QQChatResponder(b.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:CREDS)
                b.responder.media=b.media
                try:
                    submit(b);await asyncio.wait_for(b.queue.join(),8)
                    assert len(client.sent)==len(client.voices)==len(tts.calls)==1
                    assert '你好，我一直都在' in tts.calls[0][0]
                    assert not kb.memories
                finally:await b.stop()
    with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'refresh_daily_activity_memory',lambda:None):
        asyncio.run(run())
    assert len(captured)==1


def test_group_private_record_revoke_after_confirmed_text_prevents_late_voice(tmp_path):
    async def run():
        async def single(*a,**kw):return response('主人喜欢茉莉茶。')
        kb,b,group=await group_lab(tmp_path,single);configure(group,context_enabled=True,memory_enabled=True)
        memory=kb.add_memory('茉莉茶',source='manual');owner=MemoryActor('10000','20000',True,True)
        group.access.set(owner,memory['id'],policy_content_revision(memory),'20000',visibility='groups',groups=('90000',))
        client=VoiceClient();b.client=client;b.media=QQMedia(b,tmp_path)
        b.media.configure('20000',0,MediaPolicy(voice_reply_enabled=True).model_dump(),confirmed=True)
        b.responder.media=b.media;tts=FakeTTS();tts.release=threading.Event();b.voice=QQVoice(b,tmp_path,tts)
        try:
            submit_group(b,'你记得什么');await until(tts.entered.is_set);assert client.sent
            policy=group.access.get('10000',memory['id'])
            group.access.set(owner,memory['id'],policy_content_revision(memory),'20000',expected_revision=policy.revision,visibility='private')
            await asyncio.wait_for(b.queue.join(),2);assert not client.voices
            tts.release.set();await until(lambda:not b.voice.busy)
            assert not client.voices and not list(b.voice.directory.glob('*.wav'))
        finally:tts.release.set();await b.stop()
    asyncio.run(run())


def test_voice_cache_file_quota_falls_back_to_text_without_deleting_other_pending_uploads(tmp_path):
    async def run():
        b,tts,c,_=await lab(tmp_path)
        try:
            for index in range(MAX_CACHE_FILES):
                identifier=f'{index:032x}';path=b.voice._path(identifier);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'pending')
                with b.store.connect() as db:db.execute('insert into qq_voice_deliveries values(?,?,?,?,?,?,?,?,?,?)',
                    ('pending-'+str(index),identifier,'other-scope','unknown',1,'hash',time.time(),time.time()+300,'','transport_unknown'))
            submit(b);await b.queue.join()
            assert c.sent and not c.voices and not tts.calls
            assert len(list(b.voice.directory.glob('*.wav')))==MAX_CACHE_FILES
        finally:await b.stop()
    asyncio.run(run())
