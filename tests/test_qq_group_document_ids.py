"""Real NapCat group UUID shape must survive verified group media retrieval."""
import asyncio
from dataclasses import replace

import pytest

from saya_app.services.qq_documents import document_refs
from saya_app.services.qq_media import MediaUnavailable
from test_qq_documents import document_event,document_lab
from test_qq_media import CREDS


GROUP_FILE_ID='/01234567-89ab-cdef-0123-456789abcdef'


def test_verified_group_quote_fetches_native_uuid_without_filename_fallback(tmp_path):
    async def run():
        raw=document_event(group=90000,name='report.txt')
        raw['message'][-1]['data']['file_id']=GROUP_FILE_ID
        bridge,media,msg,client=await document_lab(tmp_path,raw,b'GROUP_FILE_CONTENT')
        try:
            media.configure('20000',1,{'quote_documents_enabled':True},confirmed=True)
            msg=replace(msg,media_types=(),reply_to='2');client.raw.update(message_id='2',user_id=30000)
            result=await media.prepare(msg,CREDS,'api')
            assert result['documents'][0]['text']=='GROUP_FILE_CONTENT'
            assert result['documents'][0]['source_speaker_id']=='30000'
            assert client.calls==[('get_msg',{'message_id':'2'}),('get_file',{'file_id':GROUP_FILE_ID})]
            client.calls.clear();client.raw['group_id']=90001
            assert not (await media.prepare(msg,CREDS,'api'))['documents']
            assert client.calls==[('get_msg',{'message_id':'2'})]
        finally:await bridge.stop()
    asyncio.run(run())


def test_direct_group_file_uses_same_native_uuid(tmp_path):
    async def run():
        raw=document_event(group=90000,name='report.txt')
        raw['message'][-1]['data']['file_id']=GROUP_FILE_ID
        bridge,media,msg,client=await document_lab(tmp_path,raw,b'DIRECT_GROUP_FILE')
        try:
            assert (await media.prepare(msg,CREDS,'api'))['documents'][0]['text']=='DIRECT_GROUP_FILE'
            assert client.calls[-1]==('get_file',{'file_id':GROUP_FILE_ID})
        finally:await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('token',['/etc/passwd','/report.txt','C:\\secret.txt','file:///tmp/secret',
    '/01234567-89ab-cdef-0123-456789abcdef/other','/01234567-89ab-cdef-0123-456789abcdeg',
    '../01234567-89ab-cdef-0123-456789abcdef','//01234567-89ab-cdef-0123-456789abcdef'])
def test_group_uuid_exception_does_not_admit_arbitrary_paths(token):
    with pytest.raises(MediaUnavailable):
        document_refs([{'type':'file','data':{'file':'report.txt','file_id':token}}],group=True)


def test_private_channel_does_not_admit_group_uuid_exception():
    with pytest.raises(MediaUnavailable):
        document_refs([{'type':'file','data':{'file':'report.txt','file_id':GROUP_FILE_ID}}])
