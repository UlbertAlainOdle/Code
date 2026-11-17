"""Isolated, bounded text extraction. Invoked with python -I, not imported by UI.

Only bytes on stdin are parsed. No file paths, credentials, external resources,
macros or document commands are interpreted. Limits are established before read.
"""
import io
import json
import os
import socket
import sys
import zipfile
import xml.etree.ElementTree as ET

MAX_INPUT=5*1024*1024
MAX_XML=4*1024*1024
MAX_ZIP=20*1024*1024
MEMORY_LIMIT=256*1024*1024
_job=None


def resource_limits():
    global _job
    if os.name=='nt':
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_=[('process_time',c.c_longlong),('job_time',c.c_longlong),('flags',w.DWORD),
                ('min_ws',c.c_size_t),('max_ws',c.c_size_t),('active',w.DWORD),('affinity',c.c_size_t),
                ('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(c.Structure):
            _fields_=[(name,c.c_ulonglong) for name in ['read_op','write_op','other_op','read_bytes','write_bytes','other_bytes']]
        class Extended(c.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),
                ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
        k=c.WinDLL('kernel32',use_last_error=True)
        k.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
        k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD];k.SetInformationJobObject.restype=w.BOOL
        k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.AssignProcessToJobObject.restype=w.BOOL
        k.GetCurrentProcess.restype=w.HANDLE
        _job=k.CreateJobObjectW(None,None)
        if not _job:raise RuntimeError('resource_limit_unavailable')
        info=Extended();info.basic.flags=0x2|0x100|0x2000|0x8
        info.basic.process_time=5*10_000_000;info.basic.active=1;info.process_memory=MEMORY_LIMIT
        if not k.SetInformationJobObject(_job,9,c.byref(info),c.sizeof(info)) or not k.AssignProcessToJobObject(_job,k.GetCurrentProcess()):
            raise RuntimeError('resource_limit_unavailable')
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(MEMORY_LIMIT,MEMORY_LIMIT))
        resource.setrlimit(resource.RLIMIT_CPU,(5,5))


def deny_network(*a,**kw):raise OSError('document_network_disabled')


def clean(text):
    if any(ord(ch)<32 and ch not in '\r\n\t' for ch in text):raise ValueError('binary_text')
    return text.replace('\r\n','\n').replace('\r','\n').strip()


def xml_root(raw):
    text=raw.decode('utf-8-sig')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('xml_entities_rejected')
    return ET.fromstring(text)


def docx_text(raw,max_chars):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist();names=[x.filename for x in infos]
        if not 1<=len(names)<=256 or len(names)!=len(set(names)):raise ValueError('zip_entry_limit')
        if sum(x.file_size for x in infos)>MAX_ZIP:raise ValueError('zip_expansion_limit')
        for entry in infos:
            name=entry.filename.replace('\\','/');lower=name.lower()
            if name.startswith('/') or ':' in name or '..' in name.split('/') or entry.flag_bits&1:
                raise ValueError('unsafe_zip_entry')
            if 'vbaproject' in lower or '/activex/' in lower or '/embeddings/' in lower:
                raise ValueError('active_document_rejected')
        def read(name,limit):
            if archive.getinfo(name).file_size>limit:raise ValueError('xml_size_limit')
            with archive.open(name) as stream:content=stream.read(limit+1)
            if len(content)>limit:raise ValueError('xml_size_limit')
            return content
        types=xml_root(read('[Content_Types].xml',65536))
        expected='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
        if not any(n.get('PartName')=='/word/document.xml' and n.get('ContentType')==expected for n in types):
            raise ValueError('not_a_docx_document')
        document=xml_root(read('word/document.xml',MAX_XML))
        ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        parts=[];total=0;truncated=False
        for paragraph in document.iter(ns+'p'):
            values=[]
            for n in paragraph.iter():
                if n.tag==ns+'t':values.append(n.text or '')
                elif n.tag==ns+'tab':values.append('\t')
                elif n.tag in {ns+'br',ns+'cr'}:values.append('\n')
            text=''.join(values)
            if text:
                remaining=max_chars-total
                if len(text)+1>remaining:truncated=True
                parts.append(text[:max(0,remaining)]);total+=len(text)+1
                if total>=max_chars:truncated=True;break
        return '\n'.join(parts)[:max_chars],truncated,None


def extract(raw,kind,max_pages,max_chars):
    if not raw or len(raw)>MAX_INPUT:raise ValueError('document_size_limit')
    if kind=='txt':
        if raw.startswith((b'%PDF-',b'PK\x03\x04',b'MZ',b'\x89PNG')):raise ValueError('type_mismatch')
        encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        try:text=raw.decode(encoding)
        except UnicodeDecodeError:
            if encoding=='utf-16':raise
            text=raw.decode('gb18030')
        text=clean(text);return text[:max_chars],len(text)>max_chars,None
    if kind=='docx':
        if not raw.startswith(b'PK\x03\x04'):raise ValueError('type_mismatch')
        return docx_text(raw,max_chars)
    if kind!='pdf' or not raw.startswith(b'%PDF-'):raise ValueError('type_mismatch')
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw),strict=True)
    if reader.is_encrypted:raise ValueError('encrypted_document')
    pages=len(reader.pages)
    if pages>max_pages:raise ValueError('page_limit')
    parts=[];total=0;truncated=False
    for index,page in enumerate(reader.pages):
        text=clean(page.extract_text() or '')
        if not text:continue
        separator=2 if parts else 0;remaining=max_chars-total-separator
        if remaining<=0:truncated=True;break
        if len(text)>remaining:truncated=True
        parts.append(text[:remaining]);total+=min(len(text),remaining)+separator
        if total>=max_chars:
            truncated=truncated or index<pages-1
            break
    return '\n\n'.join(parts)[:max_chars],truncated,pages


def main():
    result={'status':'unavailable'}
    try:
        resource_limits()
        socket.socket.connect=deny_network;socket.socket.connect_ex=deny_network
        socket.create_connection=deny_network;socket.getaddrinfo=deny_network
        kind=sys.argv[1];max_pages=int(sys.argv[2]);max_chars=int(sys.argv[3])
        if not 1<=max_pages<=50 or not 100<=max_chars<=24000:raise ValueError('invalid_limits')
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        text,truncated,pages=extract(raw,kind,max_pages,max_chars)
        text=clean(text)
        if not text:raise ValueError('no_extractable_text')
        result={'status':'ready','text':text,'truncated':truncated,'pages':pages,'resource_limits':True}
    except Exception:pass
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'));sys.stdout.buffer.flush()


if __name__=='__main__':main()
