"""Parent-side, one-at-a-time document parser with owned process cancellation."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from .qq_tasks import checkpoint_current


class DocumentUnavailable(ValueError):pass


def document_refs(raw,*,group=False):
    from .qq_media import MediaUnavailable
    import re
    result=[]
    if not isinstance(raw,list) or len(raw)>256:return result
    for item in raw:
        if not isinstance(item,dict) or item.get('type')!='file' or not isinstance(item.get('data'),dict):continue
        data=item['data'];token=data.get('file_id');name=data.get('file') or data.get('name')
        # Filename aliases are not IDs: equal filenames across groups must
        # never select the last cached alias from another group.
        # Native group file UUIDs are '/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'.
        # Preserve this exact protocol ID for get_file, only after a group
        # get_msg scope check. It is never opened as a local filesystem path.
        opaque=isinstance(token,str) and re.fullmatch(r'[A-Za-z0-9_+={}.\-]{1,512}',token)
        group_uuid=(group is True and isinstance(token,str) and
            re.fullmatch(r'/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',token))
        if not (opaque or group_uuid) or '..' in token:
            raise MediaUnavailable('invalid_document_reference')
        if not isinstance(name,str) or not 1<=len(name)<=160 or re.search(r'[\\/:\x00-\x1f\x7f]',name):
            raise MediaUnavailable('invalid_document_name')
        suffix=Path(name).suffix.lower()
        if suffix not in {'.txt','.pdf','.docx'}:raise MediaUnavailable('unsupported_document_type')
        result.append({'id':token,'name':name,'kind':suffix[1:],'size':data.get('file_size')})
    return result


class QQDocumentParser:
    def __init__(self):self.lock=asyncio.Lock();self.active=set()

    async def parse(self,raw,kind,values):
        if not isinstance(raw,bytes) or not raw or len(raw)>values['max_document_bytes']:
            raise DocumentUnavailable('document_size_limit')
        async with self.lock:
            checkpoint_current()
            worker=Path(__file__).with_name('qq_document_worker.py').resolve()
            env={k:v for k,v in os.environ.items() if k in {'SystemRoot','WINDIR','SYSTEMROOT','TEMP','TMP'}}
            process=await asyncio.create_subprocess_exec(sys.executable,'-I',str(worker),kind,
                str(values['max_document_pages']),str(values['max_document_chars']),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                env=env,cwd=str(worker.parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.active.add(process.pid)
            async def exchange():
                process.stdin.write(raw);await process.stdin.drain();process.stdin.close()
                result=bytearray()
                while True:
                    part=await process.stdout.read(8192)
                    if not part:break
                    result.extend(part)
                    if len(result)>128*1024:raise DocumentUnavailable('parser_output_limit')
                await process.wait()
                return result
            exchanging=asyncio.create_task(exchange())
            try:
                async with asyncio.timeout(values['document_timeout_seconds']):
                    while not exchanging.done():
                        await asyncio.wait({exchanging},timeout=.05)
                        checkpoint_current()
                    output=await exchanging
                checkpoint_current()
                if process.returncode!=0:raise DocumentUnavailable('parser_failed')
                result=json.loads(output)
                if (not isinstance(result,dict) or result.get('status')!='ready' or result.get('resource_limits') is not True
                    or not isinstance(result.get('text'),str) or len(result['text'])>values['max_document_chars']):
                    raise DocumentUnavailable('no_readable_document')
                return result
            finally:
                if not exchanging.done():exchanging.cancel()
                if process.returncode is None:
                    try:process.kill()
                    except ProcessLookupError:pass
                    await process.wait()
                await asyncio.gather(exchanging,return_exceptions=True)
                self.active.discard(process.pid)
