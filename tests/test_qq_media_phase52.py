import asyncio,base64,io
from dataclasses import replace
from types import SimpleNamespace
from PIL import Image
import pytest
from saya_app.services.qq_chat import QQChatResponder,context_turn_reference
from saya_app.services.qq_delivery import natural_parts
from test_qq_media import lab,configure,CREDS
from test_qq_group_integration import response


@pytest.mark.parametrize('format',['GIF','WEBP','PNG'])
def test_animated_image_only_exposes_first_frame_as_png(lab,format):
    _,media,msg,client,path=lab;configure(media)
    Image.new('RGB',(12,8),'red').save(path,format=format,save_all=True,
        append_images=[Image.new('RGB',(12,8),'blue')],duration=100,loop=0)
    result=asyncio.run(media.prepare(msg,CREDS,'api'))
    image=Image.open(io.BytesIO(base64.b64decode(result['attachments'][0]['data_url'].split(',')[1])))
    assert image.format=='PNG' and image.n_frames==1 and image.convert('RGB').getpixel((0,0))[0]>240
    assert '首帧' in result['status']


def test_complete_paragraphs_do_not_merge_to_fit_batch():
    paragraphs=[f'第{i}段有完整含义，应当单独显示并保持原有内容。' for i in range(7)]
    assert natural_parts('\n\n'.join(paragraphs),240,3)==paragraphs
    assert natural_parts('\n\n'.join(paragraphs),240,5)==paragraphs


def test_media_failure_uses_character_generator_without_fixed_status_receipt(lab):
    bridge,media,msg,client,path=lab;configure(media);path.write_bytes(b'bad image')
    captured=[]
    async def single(req,channel_turn):
        captured.append((req,channel_turn))
        return response('这张没读出来呢，重新发一遍试试。')
    responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',lambda:CREDS)
    responder.media=media
    answer=asyncio.run(responder(replace(msg,media_only=True),[],'session_qq_fixture.jsonl'))
    assert str(answer)=='这张没读出来呢，重新发一遍试试。'
    req,turn=captured[0]
    assert turn['media_context']['read_failed'] and not req.agent_mode and not req.memory_write_enabled
    assert not req.attachments and 'Do not guess' in context_turn_reference(turn)
    assert not getattr(answer,'notes',())
