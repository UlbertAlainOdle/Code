"""Optional short local voice after confirmed text, with no uncertain retries."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4
import wave
from .qq_tasks import checkpoint_current,effect_current,current_guard,TaskCancelled


MAX_AUDIO_BYTES=2*1024*1024
MAX_CACHE_BYTES=32*1024*1024
MAX_CACHE_FILES=32

# QQ roleplay can put a delivery cue inside an otherwise valid spoken sentence.
# Only complete, short, known cues are omitted; ordinary parenthesized prose and
# the shared speech cleaner (including formulas/citations) remain authoritative.
_SPEECH_CUE=re.compile(r'(?:(?:用)?(?:很轻|轻声|小声|低声|柔声|轻柔|温柔|轻轻|平静|认真|坚定|缓缓|慢慢|轻快|带笑|含笑)(?:地|的语气)?(?:说|说道|读|念)?|(?:声音|语气)(?:很轻|轻柔|温柔|平静)|压低声音|放轻声音|放低声音|轻笑|轻轻叹气|叹气|停顿|稍作停顿|短暂停顿)')


def _without_speech_cues(text):
    def replace(match):
        parts=re.split(r'[，,、；;]',match.group(0)[1:-1])
        return ' ' if all(_SPEECH_CUE.fullmatch(part.strip()) for part in parts) else match.group(0)
    return re.sub(r'\([^()\n]{1,40}\)|（[^（）\n]{1,40}）',replace,text)


def voice_opted_out(text):
    return bool(re.search(r'(?:不要|不用|无需|别|不想)[^，。！？；\n]{0,8}(?:语音|朗读|声音)|只(?:要|用|发)文字|纯文字',text))


def voice_requested(text):
    """Recognize a direct delivery request, not a question about voice features."""
    if voice_opted_out(text):return False
    if re.search(r'(?:解释|介绍|说明|研究)[^，。！？；\n]*(?:语音|声音)|语音(?:识别|功能|触发机制|开关|设置|编码|接口)',text):return False
    return bool(re.search(
        r'(?:发|来|用|听|想听|想要|要听)[^，。！？；\n]{0,8}语音'
        r'|语音\s*(?:吧|来听|给我|说|回复|回答|读|念)'
        r'|(?:想听|听听|听一下|让我听)[^，。！？；\n]{0,5}(?:你(?:的)?声音|你说|你念)'
        r'|(?:念|读)(?:一句|一声|一下|给我听)|说给我听|用声音(?:说|回答|回复)',text))


def spoken_body(text):
    from .speech_text import clean_speech
    from .qq_presentation import clean_control_output
    from .qq_expressions import TRAILING
    text=clean_control_output(text)
    for decoration in TRAILING:text=text.replace(decoration,' ')
    return clean_speech(_without_speech_cues(text))


class QQVoice:
    def __init__(self,bridge,root,tts):
        self.bridge,self.store,self.tts=bridge,bridge.store,tts
        self.directory=Path(root).resolve()/'.runtime/qq-media/audio'
        self._render_lock=threading.Lock();self._pending=set();self._sweeper=None
        with self.store.connect() as db:
            db.execute('''create table if not exists qq_voice_deliveries(result_id text primary key,id text unique,
                scope text,state text,revision integer,tts_revision text,created real,expires real,receipt text,error text)''')
            db.execute("update qq_voice_deliveries set state='interrupted' where state in ('rendering','ready')")
            db.execute("update qq_voice_deliveries set state='unknown' where state='sending'")
        self.prune()
        try:self.start()
        except RuntimeError:pass # Construction outside a running loop is read-only.

    def start(self):
        if self._sweeper is None or self._sweeper.done():
            loop=asyncio.get_running_loop();self._sweeper=loop.create_task(self._sweep())

    async def stop(self):
        if self._sweeper:
            self._sweeper.cancel()
            await asyncio.gather(self._sweeper,return_exceptions=True);self._sweeper=None

    async def _sweep(self):
        while True:
            await asyncio.sleep(30)
            try:self.prune()
            except (OSError,ValueError,sqlite3.Error):pass

    @property
    def busy(self):return self._render_lock.locked()

    def availability(self,message,body=None):
        """Current ability to attempt delivery, not a promise of transport success."""
        media=self.bridge.media
        result={'supported':True,'available':False,'reason':'permission_unavailable'}
        if not media or not media.allowed(message):return result
        values=media.state()['values']
        result.update(max_chars=values['voice_max_chars'],max_seconds=values['voice_max_seconds'])
        reason='available'
        if not values['voice_reply_enabled']:reason='qq_voice_disabled'
        elif voice_opted_out(message.text):reason='turn_text_only'
        elif self.bridge.settings.delivery_mode!='chat':reason='legacy_delivery'
        else:
            try:
                settings=self.tts.settings()
                if not settings.enabled:reason='tts_disabled'
                elif settings.provider!='qwen_local':reason='local_tts_unavailable'
                elif self.busy:reason='tts_busy'
            except (OSError,ValueError):reason='local_tts_unavailable'
        if reason=='available' and body is not None:
            if not 1<=len(body)<=values['voice_max_chars']:reason='text_length_limit'
            elif re.search(r'```|https?://|\[CQ:|\n\s*\||已保存长期记忆',body):reason='not_speech_text'
            elif not re.search(r'[^\W_]',spoken_body(body),re.UNICODE):reason='no_speakable_text'
        result.update(available=reason=='available',reason=reason)
        return result

    def _path(self,identifier):
        if not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('invalid_voice_id')
        directory=self.directory.resolve()
        if directory!=self.directory or not directory.is_relative_to(self.directory.parents[1]):raise ValueError('voice_cache_changed')
        path=(directory/(identifier+'.wav')).resolve()
        if path.parent!=directory:raise ValueError('voice_cache_changed')
        return path

    def prune(self):
        with self.store.connect() as db:
            rows=db.execute('select result_id,id from qq_voice_deliveries where expires<=?',(time.time(),)).fetchall()
            for row in rows:
                self._path(row['id']).unlink(missing_ok=True)
                db.execute("update qq_voice_deliveries set state='expired' where result_id=?",(row['result_id'],))
            # Unexpired files remain available for an already-started OneBot
            # upload, including an unknown transport outcome.
            db.execute('delete from qq_voice_deliveries where expires<=? and result_id not in (select id from qq_deliveries)',(time.time(),))

    def _tts_revision(self,settings):
        value={'settings':settings.model_dump()}
        if settings.voice_id:
            path=self.tts.audio_path(settings.voice_id,'voices')
            value['reference']=[(p.stat().st_size,p.stat().st_mtime_ns) for p in [path,path.with_suffix('.json')]]
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

    def _cache_capacity(self,extra):
        with self.store.connect() as db:ids=[r[0] for r in db.execute('select id from qq_voice_deliveries')]
        sizes=[p.stat().st_size for p in (self._path(i) for i in ids) if p.is_file()]
        return len(sizes)<MAX_CACHE_FILES and sum(sizes)+extra<=MAX_CACHE_BYTES

    def _finish(self,result_id,state,error=''):
        with self.store.connect() as db:
            db.execute("update qq_voice_deliveries set state=?,error=? where result_id=? and state in ('rendering','ready')",
                (state,error,result_id))

    def _valid(self,message,scope,result_id,revision,tts_revision,memory_check):
        b=self.bridge;media=b.media
        if not media.allowed(message):return False
        state=media.state()
        if state['media_revision']!=revision or not state['values']['voice_reply_enabled']:return False
        try:
            settings=self.tts.settings()
            if not settings.enabled or settings.provider!='qwen_local' or self._tts_revision(settings)!=tts_revision:return False
        except (OSError,ValueError):return False
        if memory_check and not memory_check():return False
        with self.store.connect() as db:
            row=db.execute("select origin,expires from qq_deliveries where id=? and scope=? and kind='final'",(result_id,scope)).fetchone()
            stopped=db.execute('select 1 from qq_delivery_stops where result_id=?',(result_id,)).fetchone()
            voice=db.execute('select expires from qq_voice_deliveries where result_id=? and scope=?',(result_id,scope)).fetchone()
        if not row or row['expires']<=time.time() or stopped or not voice or voice['expires']<=time.time():return False
        origin=json.loads(row['origin'])
        return all(origin[k]==getattr(message,k) for k in ['bot_id','user_id','group_id'])

    @staticmethod
    def _audio_ok(raw,max_seconds):
        if not isinstance(raw,bytes) or not raw or len(raw)>MAX_AUDIO_BYTES:raise ValueError('audio_byte_limit')
        with wave.open(io.BytesIO(raw)) as wav:
            if wav.getcomptype()!='NONE' or wav.getnchannels()!=1 or wav.getsampwidth()!=2 or not 8000<=wav.getframerate()<=48000:
                raise ValueError('audio_format')
            frames=wav.getnframes()
            if not 0<frames/wav.getframerate()<=max_seconds:raise ValueError('audio_duration')
            if len(wav.readframes(frames))!=frames*2:raise ValueError('audio_truncated')

    async def after_text(self,message,scope,result_id,*,session_id=None,memory_check=None,speech_span=None):
        """Only called on a new model answer, never controls or continuation."""
        b=self.bridge
        if not b.media or not b.media.allowed(message):return
        state=b.media.state();v=state['values']
        if not v['voice_reply_enabled']:return
        if voice_opted_out(message.text):return
        self.prune()
        with self.store.connect() as db:
            record=db.execute("select * from qq_deliveries where id=? and scope=? and kind='final'",(result_id,scope)).fetchone()
            parts=db.execute('select state from qq_delivery_parts where result_id=?',(result_id,)).fetchall()
        if not record or not parts or any(p['state']!='sent' for p in parts):return
        if scope!=message.scope+(':shared' if message.shared else ':isolated'):return
        origin=json.loads(record['origin'])
        if any(origin[k]!=getattr(message,k) for k in ['bot_id','user_id','group_id']):return
        text=record['canonical'].strip()
        if speech_span is not None:
            # Re-derive from the confirmed scoped canonical body. The selector
            # cannot supply new speech, a path, or a different result's text.
            if not isinstance(speech_span,dict):return
            start,end=speech_span.get('start'),speech_span.get('end')
            if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text):return
            from .qq_expressions import speech_span as validate_span
            if validate_span(text,text[start:end],v['voice_max_chars'])!=speech_span:return
            text=text[start:end]
        if (not 1<=len(text)<=v['voice_max_chars'] or re.search(r'```|https?://|\[CQ:|\n\s*\||已保存长期记忆',text)):return
        text=spoken_body(text)
        if not re.search(r'[^\W_]',text,re.UNICODE):return
        if not self._cache_capacity(MAX_AUDIO_BYTES):return
        try:
            settings=self.tts.settings()
            if not settings.enabled or settings.provider!='qwen_local':return
            tts_revision=self._tts_revision(settings)
        except (OSError,ValueError):return
        identifier=uuid4().hex;now=time.time()
        with self.store.connect() as db:
            if not db.execute('insert or ignore into qq_voice_deliveries values(?,?,?,?,?,?,?,?,?,?)',
                (result_id,identifier,scope,'rendering',state['media_revision'],tts_revision,now,now+v['voice_retention_seconds'],'','')).rowcount:return
        if not self._render_lock.acquire(blocking=False):
            self._finish(result_id,'skipped','tts_busy');return
        guard=current_guard();original=guard.permission_check if guard else None
        def permitted():return self._valid(message,scope,result_id,state['media_revision'],tts_revision,memory_check)
        if guard:guard.permission_check=lambda:(not original or original()) and permitted()
        def render():
            try:
                checkpoint_current()
                if not permitted():raise ValueError('voice_permission_changed')
                return self.tts.synthesize_ephemeral(text,settings,session_id=session_id)
            finally:self._render_lock.release()
        rendering=asyncio.create_task(asyncio.to_thread(render));self._pending.add(rendering)
        def settle(task):
            self._pending.discard(task)
            if not task.cancelled():task.exception()
        rendering.add_done_callback(settle)
        def valid():
            checkpoint_current()
            return self._valid(message,scope,result_id,state['media_revision'],tts_revision,memory_check)
        path=None
        try:
            deadline=time.monotonic()+v['voice_timeout_seconds']
            while not rendering.done():
                if not valid():self._finish(result_id,'cancelled','permission_changed');return
                if time.monotonic()>=deadline:self._finish(result_id,'timed_out','tts_timeout');return
                await asyncio.wait({rendering},timeout=.05)
            audio=rendering.result()
            if not valid():self._finish(result_id,'cancelled','permission_changed');return
            self._audio_ok(audio,v['voice_max_seconds'])
            if not self._cache_capacity(len(audio)):self._finish(result_id,'skipped','cache_full');return
            path=self._path(identifier);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as file:file.write(audio)
            with self.store.connect() as db:db.execute("update qq_voice_deliveries set state='ready' where result_id=? and state='rendering'",(result_id,))
            if not valid():self._finish(result_id,'cancelled','permission_changed');path.unlink(missing_ok=True);return
            with effect_current('send',scope=scope) as effect:
                with self.store.connect() as db:
                    claimed=db.execute("update qq_voice_deliveries set state='sending' where result_id=? and state='ready'",(result_id,)).rowcount
                if claimed!=1:effect.failed();return
                try:
                    receipt=await b.client.send_voice(message,path)
                    if not re.fullmatch(r'-?[0-9]{1,20}',str(receipt)):raise RuntimeError('missing_voice_receipt')
                    effect.committed()
                    with self.store.connect() as db:db.execute("update qq_voice_deliveries set state='sent',receipt=? where result_id=?",(str(receipt),result_id))
                except BaseException:
                    with self.store.connect() as db:db.execute("update qq_voice_deliveries set state='unknown',error='transport_unknown' where result_id=?",(result_id,))
                    raise
        except (TaskCancelled,asyncio.CancelledError):
            self._finish(result_id,'cancelled','cancelled')
            if path:
                with self.store.connect() as db:row=db.execute('select state from qq_voice_deliveries where result_id=?',(result_id,)).fetchone()
                if row and row['state']!='unknown':path.unlink(missing_ok=True)
            raise
        except Exception:
            self._finish(result_id,'failed','tts_or_audio_failed')
            if path:
                with self.store.connect() as db:row=db.execute('select state from qq_voice_deliveries where result_id=?',(result_id,)).fetchone()
                if row and row['state'] not in {'sent','unknown'}:path.unlink(missing_ok=True)
            # Text is already confirmed. Do not send another model response,
            # retry uncertain audio or turn this into a text-delivery failure.
        finally:
            if guard:guard.permission_check=original
