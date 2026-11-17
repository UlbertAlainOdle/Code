import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from saya_app.services import qq_quotes
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_presentation import clean_control_output,clean_assistant_history
from test_qq_media import lab,configure,CREDS
from test_qq_group_integration import response


@pytest.mark.parametrize('bot_author',[True,False])
def test_plain_group_quote_does_not_create_media_warning_or_disable_text_tools(lab,bot_author):
    bridge,media,msg,client,_=lab;configure(media,quote_images_enabled=True,documents_enabled=True,quote_documents_enabled=True)
    msg=replace(msg,group_id='90000',scope='10000:group:90000:user:20000',reply_to='5',media_types=())
    client.raw.update(message_id='5',message_type='group',group_id='90000',user_id=10000 if bot_author else 30000,
        message=[{'type':'text','data':{'text':'原来的普通文字'}}])
    async def run():
        await qq_quotes.resolve(client,msg,bridge.store)
        assert msg.quote_context['status']=='ready'
        assert await media.prepare(msg,CREDS,'api') is None
        assert all(action=='get_msg' for action,_ in client.calls)
        captured=[]
        async def single(req,channel_turn):
            captured.append(channel_turn)
            return response('这是对引用文字的回答。[Mood: curious]')
        responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',lambda:CREDS)
        responder.media=media
        answer=await responder(msg,[],'session_qq_fixture.jsonl')
        assert str(answer)=='这是对引用文字的回答。'
        assert captured[0]['quoted_message']['text']=='原来的普通文字'
        assert not captured[0].get('media_context')
        assert not getattr(msg,'media_snapshot',None)
    asyncio.run(run())


def test_known_private_bot_text_quote_skips_attachment_provenance_gate(lab):
    bridge,media,msg,client,_=lab;configure(media,quote_images_enabled=True)
    msg=replace(msg,reply_to='5',media_types=())
    client.raw.update(message_id='5',user_id=10000,message=[{'type':'text','data':{'text':'普通机器人回复'}}])
    with patch.object(qq_quotes,'_known_private_outbound',return_value=True):
        assert asyncio.run(media.prepare(msg,CREDS,'api')) is None
    # Actual bot images still fail the unchanged attachment provenance gate.
    client.raw['message']=[{'type':'image','data':{'file':'IMAGE_TOKEN'}}];client.calls.clear()
    with patch.object(qq_quotes,'_known_private_outbound',return_value=True):
        result=asyncio.run(media.prepare(msg,CREDS,'api'))
        assert not result['attachments'] and '附件未能读取' in result['status']
        assert client.calls==[('get_msg',{'message_id':'5'})]


@pytest.mark.parametrize('disabled',['intake_enabled','images_enabled'])
def test_disabled_media_does_not_add_attachment_warning_to_plain_quotes(lab,disabled):
    _,media,msg,client,_=lab;configure(media,quote_images_enabled=True)
    media.configure('20000',1,{disabled:False},confirmed=True)
    assert asyncio.run(media.prepare(replace(msg,media_types=(),reply_to='5'),CREDS,'api')) is None
    assert not client.calls


@pytest.mark.parametrize('tag',['[Mood: curious]','［mOoD：curious］','[Mood: curious'])
def test_plain_mood_alias_is_hidden_in_prose_and_old_assistant_history(tag):
    assert clean_control_output('回答正文。'+tag)=='回答正文。'
    assert clean_assistant_history(['Saya: 旧回答。'+tag,'Doctor: '+tag])==['Saya: 旧回答。','Doctor: '+tag]
    assert clean_control_output(clean_control_output('回答正文。'+tag))=='回答正文。'


def test_literal_mood_examples_and_normal_brackets_are_preserved():
    text='示例 `[Mood: curious]`。\n```text\n[Mood: curious]\n```\n[Mood board] [99] $x[1]$'
    assert clean_control_output(text)==text


def test_old_cached_mood_alias_is_filtered_without_repeating_sent_parts(tmp_path):
    from saya_app.services import qq_delivery as delivery
    from saya_app.services.qq_bridge import QQStore,QQMessage
    from test_qq_delivery import Client
    store=QQStore(tmp_path/'qq.sqlite3');delivery.initialize(store)
    message=QQMessage('k','s','10000','20000','','12','q',False)
    result=delivery.create(store,message,'s','旧正文。',120)
    with store.connect() as db:
        db.execute("UPDATE qq_delivery_parts SET text='已发正文',state='sent',receipt='confirmed' WHERE result_id=?",(result,))
        db.execute("INSERT INTO qq_delivery_parts VALUES(?,1,?,'pending','')",(result,'剩余正文。[Mood: curious]'))
    client=Client();asyncio.run(delivery.send_batch(store,client,message,'s',result,3,True))
    assert len(client.sent)==1 and client.sent[0][1]=='[2/2] 剩余正文。'
    with store.connect() as db:
        assert db.execute('select receipt from qq_delivery_parts where result_id=? and seq=0',(result,)).fetchone()[0]=='confirmed'


def test_direct_onebot_transport_filters_plain_mood_alias():
    import json,httpx
    from saya_app.services.qq_bridge import OneBotClient,QQMessage
    from test_qq_delivery import config
    async def run():
        sent=[]
        def post(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'message_id':5}})
        client=OneBotClient(config());await client.http.aclose()
        client.http=httpx.AsyncClient(transport=httpx.MockTransport(post))
        try:
            await client.send(QQMessage('k','s','10000','20000','','12','q',False),'正文。[Mood: curious]')
            assert sent[0]['message']==[{'type':'text','data':{'text':'正文。'}}]
        finally:await client.close()
    asyncio.run(run())
