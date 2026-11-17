import asyncio,io,json,os,subprocess,sys,zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from saya_app.services.qq_documents import QQDocumentParser,DocumentUnavailable,document_refs
from saya_app.services.qq_media import QQMedia,MediaPolicy,MediaConflict
from saya_app.services.qq_bridge import normalize_event
from test_qq_delivery import event,Client
from test_qq_task_scheduler import make
from test_qq_media import CREDS


def docx_bytes(text='项目约定：周五发布。'):
    doc=Document();doc.add_paragraph(text);doc.add_table(rows=1,cols=2).rows[0].cells[0].text='表格内容'
    data=io.BytesIO();doc.save(data);return data.getvalue()


def pdf_bytes(text='PROJECT_ORION_DEADLINE_FRIDAY',pages=1,encrypted=False):
    writer=PdfWriter();font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    for _ in range(pages):
        page=writer.add_blank_page(300,200)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 10 100 Td ('+text+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    if encrypted:writer.encrypt('test-password')
    data=io.BytesIO();writer.write(data);return data.getvalue()


def values(**kw):return MediaPolicy(**kw).model_dump()


@pytest.mark.parametrize('kind,raw,expected',[
    ('txt','中文项目约定：周五发布'.encode(),'中文项目约定'),
    ('txt','中文UTF16'.encode('utf-16'),'中文UTF16'),('txt','国标文本'.encode('gb18030'),'国标文本'),
    ('pdf',pdf_bytes(),'PROJECT_ORION_DEADLINE_FRIDAY'),('docx',docx_bytes(),'项目约定：周五发布。')],
    ids=['utf8','utf16','gb18030','pdf','docx'])
def test_real_resource_limited_worker_extracts_each_format(kind,raw,expected):
    parser=QQDocumentParser();r=asyncio.run(parser.parse(raw,kind,values()))
    assert expected in r['text'] and r['resource_limits'] is True and not parser.active


@pytest.mark.parametrize('kind,raw',[
    ('txt',b'MZbinary executable'),('txt',b'\x00\x01binary'),('pdf',b'not pdf'),
    ('pdf',pdf_bytes(encrypted=True)),('pdf',pdf_bytes(pages=2)),('docx',b'not zip')],
    ids=['exe','binary','bad-pdf','encrypted','pages','bad-zip'])
def test_type_encryption_page_and_binary_fail_closed(kind,raw):
    with pytest.raises(DocumentUnavailable):asyncio.run(QQDocumentParser().parse(raw,kind,values(max_document_pages=1)))


@pytest.mark.parametrize('attack',['traversal','macro','embedding','entity','large','not_docx'])
def test_docx_zip_and_xml_limits_do_not_execute_or_expand(attack):
    original=zipfile.ZipFile(io.BytesIO(docx_bytes()));data=io.BytesIO()
    with zipfile.ZipFile(data,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name in original.namelist():
            value=original.read(name)
            if name=='word/document.xml' and attack=='entity':value=b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///secret">]><a>&x;</a>'
            if name=='[Content_Types].xml' and attack=='not_docx':value=b'<Types/>'
            z.writestr(name,value)
        if attack=='traversal':z.writestr('../outside.txt','never write')
        if attack=='macro':z.writestr('word/vbaProject.bin',b'macro')
        if attack=='embedding':z.writestr('word/embeddings/object.bin',b'embedded')
        if attack=='large':z.writestr('word/huge.xml',b'a'*(21*1024*1024))
    with pytest.raises(DocumentUnavailable):asyncio.run(QQDocumentParser().parse(data.getvalue(),'docx',values()))


def test_long_text_is_explicitly_an_excerpt_and_parser_leaves_no_child():
    parser=QQDocumentParser();r=asyncio.run(parser.parse(('文本'*1000).encode(),'txt',values(max_document_chars=100)))
    assert len(r['text'])==100 and r['truncated'] is True and not parser.active


def test_windows_memory_limit_is_effective_in_a_separate_owned_process():
    worker=Path(__file__).resolve().parents[1]/'saya_app/services/qq_document_worker.py'
    script='''import importlib.util,sys
s=importlib.util.spec_from_file_location('limits',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.resource_limits()
try:bytearray(300*1024*1024)
except MemoryError:print('allocation_blocked',flush=True)
else:raise AssertionError('memory limit not applied')
'''
    r=subprocess.run([sys.executable,'-I','-c',script,str(worker)],capture_output=True,timeout=10)
    assert r.returncode==0 and b'allocation_blocked' in r.stdout


def test_coroutine_cancel_terminates_only_its_document_worker():
    async def run():
        parser=QQDocumentParser()
        async def held_read(*a,**kw):await asyncio.sleep(30)
        # Actual worker process starts and waits on stdin; held exchange makes
        # the cancellation point deterministic without a huge malicious PDF.
        with patch.object(asyncio.StreamWriter,'drain',held_read):
            task=asyncio.create_task(parser.parse(b'hello','txt',values()))
            for _ in range(100):
                if parser.active:break
                await asyncio.sleep(.01)
            assert parser.active
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        assert not parser.active
    asyncio.run(run())


def document_event(text='请概述文档',mid=1,user=20000,group=None,name='项目约定.docx'):
    raw=event(text,mid,user,group_id=group)
    raw['message'].append({'type':'file','data':{'file':name,'file_id':'SCOPED_FILE_UUID','file_size':1024,'url':'http://127.0.0.1/no-download'}})
    return raw


class DocumentClient(Client):
    def __init__(self,raw,path):super().__init__();self.raw,self.path,self.calls=raw,path,[]
    async def call(self,action,payload):
        self.calls.append((action,payload))
        if action=='get_msg':return self.raw
        return {'file':str(self.path),'file_name':self.raw['message'][-1]['data']['file']}


async def document_lab(tmp_path,raw=None,binary=None):
    raw=raw or document_event();cache=tmp_path/'cache';cache.mkdir()
    path=cache/'protocol-download.bin';path.write_bytes(binary if binary is not None else docx_bytes())
    actual=dict(raw);actual['message_id']=str(raw['message_id']);client=DocumentClient(actual,path)
    bridge=make(tmp_path/'bridge',None,client=client);await bridge.start()
    media=QQMedia(bridge,tmp_path,trusted_roots=[cache]);bridge.media=media
    media.configure('20000',0,values(intake_enabled=True,documents_enabled=True),confirmed=True)
    message,_=normalize_event(raw,bridge.settings)
    return bridge,media,message,client


def test_scoped_document_uuid_not_filename_url_or_vision_model_is_used(tmp_path):
    async def run():
        bridge,media,msg,client=await document_lab(tmp_path)
        try:
            r=await media.prepare(msg,CREDS|{'model_routes':{}},'api')
            assert '周五发布' in r['documents'][0]['text'] and not r['attachments'] and 'model' not in r
            assert r['documents'][0]['source_speaker_id']=='20000' and r['documents'][0]['source_sent_at'] is not None
            assert client.calls==[('get_msg',{'message_id':'1'}),('get_file',{'file_id':'SCOPED_FILE_UUID'})]
            assert msg.media_types==('file',)
            with bridge.store.connect() as db:
                saved=db.execute('select snapshot from qq_media_events').fetchone()[0]
            assert '周五' not in saved and 'SCOPED_FILE_UUID' not in saved
            media.configure('20000',1,{'images_enabled':True},confirmed=True)
            assert media.state()['values']['documents_enabled'] is True # old partial client cannot reset unrelated values
            assert not media.valid(msg,msg.media_snapshot)
        finally:await bridge.stop()
    asyncio.run(run())


def test_quotes_keep_group_scope_and_ignore_plain_text_quotes(tmp_path):
    async def run():
        raw=document_event(group=90000);bridge,media,msg,client=await document_lab(tmp_path,raw)
        media.configure('20000',1,{'quote_documents_enabled':True},confirmed=True)
        msg=replace(msg,media_types=(),reply_to='2');client.raw.update(message_id='2',user_id=30000)
        try:
            assert (await media.prepare(msg,CREDS,'api'))['documents']
            client.raw['group_id']=90001
            assert not (await media.prepare(msg,CREDS,'api'))['documents']
            client.raw.update(group_id=90000,message=[{'type':'text','data':{'text':'普通引用'}}])
            assert await media.prepare(msg,CREDS,'api') is None
        finally:await bridge.stop()
    asyncio.run(run())


def test_filename_aliases_paths_and_unsupported_extensions_never_become_file_calls():
    for data in [{'file':'report.txt'},{'file':'report.txt','file_id':'../other'},
        {'file':'../report.txt','file_id':'UUID'},{'file':'macro.docm','file_id':'UUID'}]:
        with pytest.raises(ValueError):document_refs([{'type':'file','data':data}])
