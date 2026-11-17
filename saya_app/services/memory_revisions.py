"""Bounded revision previews over the original KB/CAS and V4 version ledger.

There is no second fact store. Pending intents live in the existing V3 SQLite
database; commits always call KnowledgeBase.update_memory. Unknown outcomes
are read back, never replayed as a new fact.
"""
from copy import deepcopy
import hashlib
import json
import re
import secrets
import time

from .memory_origin import current_origin
from .memory_rules import fact_terms
from .memory_versions import check_version, record_version, MemoryVersionConflict
from .memory_v4.models import _contains_secret
from .qq_tasks import checkpoint_current


_REQUEST = re.compile(r'^(?:请)?(?P<verb>补充|追加|更正|纠正|修正|更新)(?:一下)?(?P<selector>[^:：\n]{0,160})[:：]\s*(?P<content>.+)$', re.S)
_UNSAFE = re.compile(r'如果|假设|也许|可能|有人说|他说|她说|他(?:的|喜欢)|她(?:的|喜欢)|(?:我的)?(?:朋友|同事|妈妈|爸爸|母亲|父亲|姐姐|弟弟)|这次不保存|不要保存|不用保存|别保存|不写入|密码|密钥|口令|助记词|私钥|[?？]|```|<system>|<tool', re.I)
_RECENT = {'刚才那条','刚记住的那条','刚保存的那条','这条','那条','刚才的记忆'}


def revision_confirmation(text):
    """Recognize the control namespace even when its token is malformed.

    Only the one closing quote added by a displayed receipt may be ignored.
    Whole quoted instructions remain DATA; no guessing, code extraction from
    arbitrary prose, or TTL/version relaxation happens at this boundary.
    """
    raw=str(text or '').strip()
    if raw.startswith('确认修改'):
        match=re.fullmatch(r'确认修改\s+([A-Za-z0-9_-]{32})[”」』"]?',raw)
        return {'token':match[1] if match else None}
    quoted=re.fullmatch(r'(?:请?(?:发送|输入|回复)\s*)?[“「『"](确认修改.+)[”」』"]',raw,re.S)
    return {'token':None} if quoted else None


def revision_request(text):
    raw = str(text or '').strip()
    match = _REQUEST.fullmatch(raw)
    wrapped = None
    if not match:
        wrapped = re.fullmatch(r'(?:请?(?:发送|输入|回复)\s*)?[“「『"](.+)[”」』"]',raw,re.S)
        if wrapped:
            match = _REQUEST.fullmatch(wrapped[1].strip())
    if not match:
        return None
    selector = match['selector'].strip()
    selector = re.sub(r'^(?:关于|有关|记忆)\s*', '', selector)
    selector = re.sub(r'(?:的)?(?:信息|记忆)$', '', selector).strip(' “”「」\"')
    safe = not _UNSAFE.search(raw) and not _contains_secret(raw)
    return {'operation':'append' if match['verb'] in {'补充','追加'} else 'correct',
        'selector':selector, 'content':match['content'].strip(),
        'safe':safe and wrapped is None, 'requires_direct':wrapped is not None,
        'direct_text':wrapped[1].strip() if wrapped and safe else ''}


class MemoryRevisions:
    def __init__(self, kb, *, clock=time.time):
        self.kb, self.clock = kb, clock
        self.store = kb.sqlite_store
        if not self.store or not self.store.available:
            raise ValueError('revision_store_unavailable')
        with self.store._connection() as db:
            db.execute('''create table if not exists memory_revision_previews(
                token_hash text primary key, scope text not null, session_id text not null,
                memory_id text not null, version text not null, operation text not null,
                payload text not null, expires real not null, state text not null)''')

    def record(self, memory_id, *, project_id='', subject_id=None):
        record = self.kb._find_memory(memory_id)
        if (not record or record.get('managed_slot') or record.get('is_locked')
                or record.get('source') == 'assistant_role' or record.get('is_archived')
                or not self.kb.memory_allowed_for_use(record)
                or str(record.get('project_id') or '') != str(project_id or '')):
            raise ValueError('revision_target_unavailable')
        origin = record.get('origin') or {}
        if subject_id and origin.get('channel') == 'qq' and origin.get('verified_subject') not in {None,subject_id}:
            raise ValueError('revision_subject_mismatch')
        if _UNSAFE.search(str(record.get('content') or '')):
            raise ValueError('revision_target_requires_manual_edit')
        store = self.kb.memory_v4_store
        item = store.get_memory(memory_id) if store else None
        if item and (item.subject != 'user' or item.scope_type not in {'global','project'}
                or (item.scope_id or '') != str(project_id or '')):
            raise ValueError('revision_subject_scope_mismatch')
        return record

    def projection_matches(self, record):
        with self.store._connection() as db:
            row=db.execute('select payload from memories where id=? and is_deleted=0',(record['id'],)).fetchone()
        legacy=json.loads(row[0]) if row else {}
        if legacy.get('content')!=record.get('content') or legacy.get('origin')!=record.get('origin'):
            return False
        if not self.kb.settings.get('memory_v4_write_enabled'):
            return True
        store = self.kb.memory_v4_store
        item = store.get_memory(record['id']) if store else None
        return bool(item and item.canonical_text == record.get('content')
            and item.metadata.get('origin') == record.get('origin'))

    @staticmethod
    def new_content(record, operation, content):
        old = str(record.get('content') or '').strip()
        text = str(content or '').strip()
        if operation not in {'append','correct','restore'} or not 2 <= len(text) <= 12000:
            raise ValueError('invalid_revision_content')
        if _UNSAFE.search(text) or _contains_secret(text) or text[:1] in {'「','『','“','"',"'"}:
            raise ValueError('revision_requires_direct_safe_statement')
        normalize = lambda s: re.sub(r'[\s，。,.!！；;]+','',s).lower()
        if normalize(text) == normalize(old) or (operation == 'append' and normalize(text) in normalize(old)):
            return old
        if operation == 'append':
            text = old.rstrip('。；;') + '；' + text
        elif operation == 'correct' and re.search(r'→|=>', text):
            fragments = re.split(r'→|=>', text, maxsplit=1)
            before, after = [part.strip() for part in fragments]
            if not before or not after:
                raise ValueError('correction_fragment_invalid')
            if old.count(before) == 0:
                if after in old:
                    return old
                raise ValueError('correction_fragment_missing')
            if old.count(before) != 1:
                raise ValueError('correction_fragment_not_unique')
            text = old.replace(before, after, 1)
        if len(text) > 12000:
            raise ValueError('revision_too_long')
        return text

    def targets(self, selector, content, *, recent_id=None, project_id='', subject_id=None):
        result = []
        for item in self.kb.memories:
            try:
                record = self.record(item['id'], project_id=project_id, subject_id=subject_id)
            except ValueError:
                continue
            if selector in _RECENT:
                match = record['id'] == recent_id
            elif selector:
                match = selector == record['id'] or selector in str(record.get('content') or '') or selector == record.get('title')
            else:
                shared = fact_terms(content) & fact_terms(record.get('content'))
                match = len(shared) >= 2
            if match:
                result.append(record)
        return result

    def preview(self, memory_id, operation, content, *, expected_version, scope, session_id,
                authorize, project_id='', subject_id=None):
        checkpoint_current()
        authorize()
        with self.kb._memory_write_lock:
            authorize()
            record = self.record(memory_id, project_id=project_id, subject_id=subject_id)
            if not isinstance(expected_version, str):
                raise ValueError('revision_version_required')
            check_version(record, expected_version)
            if not self.projection_matches(record):
                return {'status':'partial', 'memory_id':memory_id}
            new = self.new_content(record, operation, content)
            if new == str(record.get('content') or '').strip():
                return {'status':'noop', 'memory_id':memory_id, 'content_version':record_version(record)}
            token = secrets.token_urlsafe(24)
            digest = hashlib.sha256(token.encode()).hexdigest()
            payload = {'old':record['content'], 'new':new, 'project_id':project_id or '',
                'subject_id':subject_id, 'origin_context':current_origin(),
                'subject':'user', 'scope_type':'project' if project_id else 'global'}
            with self.store._connection() as db:
                db.execute('begin immediate')
                db.execute('delete from memory_revision_previews where expires<?', (self.clock()-86400,))
                if db.execute('select count(*) from memory_revision_previews').fetchone()[0] >= 1024:
                    raise ValueError('revision_preview_limit')
                db.execute("update memory_revision_previews set state='superseded' where scope=? and session_id=? and state='pending'",(scope,session_id))
                db.execute('insert into memory_revision_previews values(?,?,?,?,?,?,?,?,?)',
                    (digest,scope,session_id,memory_id,expected_version,operation,json.dumps(payload,ensure_ascii=False),self.clock()+600,'pending'))
            return {'status':'preview','memory_id':memory_id,'operation':operation,'expected_version':expected_version,
                'old':record['content'],'new':new,'confirmation_token':token,'expires_in':600}

    def pending(self,token,*,scope,session_id,authorize):
        """Revalidate a complete stored diff for a local owner's preview."""
        authorize()
        if not isinstance(token,str) or not 16<=len(token)<=128:return {'status':'unavailable'}
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self.kb._memory_write_lock,self.store._connection() as db:
            authorize()
            row=db.execute('select * from memory_revision_previews where token_hash=? and scope=? and session_id=?',(digest,scope,session_id)).fetchone()
            if not row:return {'status':'unavailable'}
            if row['state']!='pending':return {'status':'unknown' if row['state']=='executing' else row['state'],'memory_id':row['memory_id']}
            if row['expires']<=self.clock():return {'status':'expired'}
            payload=json.loads(row['payload'])
            try:
                record=self.record(row['memory_id'],project_id=payload['project_id'],subject_id=payload['subject_id'])
                check_version(record,row['version'])
            except ValueError:return {'status':'stale'}
            if not self.projection_matches(record):return {'status':'partial'}
            return {'status':'pending','memory_id':row['memory_id'],'old':payload['old'],'new':payload['new'],'operation':row['operation']}

    def confirm(self, token, *, scope, session_id, authorize):
        checkpoint_current()
        authorize()
        if not isinstance(token,str) or not 16 <= len(token) <= 128:
            return {'status':'unavailable'}
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.kb._memory_write_lock:
            authorize()
            with self.store._connection() as db:
                db.execute('begin immediate')
                row = db.execute('select * from memory_revision_previews where token_hash=? and scope=? and session_id=?',
                    (digest,scope,session_id)).fetchone()
                if not row:
                    return {'status':'unavailable'}
                if row['state'] != 'pending':
                    return {'status':'unknown' if row['state']=='executing' else row['state']}
                state = 'expired' if row['expires'] <= self.clock() else None
                payload = json.loads(row['payload'])
                try:
                    record = self.record(row['memory_id'],project_id=payload['project_id'],subject_id=payload['subject_id'])
                    check_version(record,row['version'])
                except ValueError:
                    state = state or 'stale'
                if state:
                    db.execute('update memory_revision_previews set state=? where token_hash=?',(state,digest))
                    return {'status':state}
                checkpoint_current()
                db.execute("update memory_revision_previews set state='executing' where token_hash=?",(digest,))
            try:
                from .memory_temporal_history import confirmed_revision
                with confirmed_revision(row['memory_id'],payload['old'],payload['new'],enabled=row['operation']=='correct'):
                    saved = self.kb.update_memory(row['memory_id'],
                        {'content':payload['new'],'tags':deepcopy(record.get('tags') or []),'category':record.get('category')},
                        expected_version=row['version'],write_check=authorize,
                        revision_context=payload['origin_context'],revision_operation=row['operation'])
                current = self.kb._find_memory(row['memory_id'])
                state = 'done' if saved and current and self.projection_matches(current) else 'partial' if saved else 'failed'
            except BaseException:
                with self.store._connection() as db:
                    db.execute("update memory_revision_previews set state='unknown' where token_hash=?",(digest,))
                raise
            with self.store._connection() as db:
                db.execute('update memory_revision_previews set state=? where token_hash=?',(state,digest))
            return {'status':'applied' if state=='done' else state,'memory_id':row['memory_id'],
                'operation':row['operation'],'content_version':record_version(current) if current else None,
                'content':current.get('content') if current and saved else None}

    def history(self, memory_id, *, project_id='', subject_id=None):
        with self.kb._memory_write_lock:
            record = self.record(memory_id,project_id=project_id,subject_id=subject_id)
            store = self.kb.memory_v4_store
            versions = store.list_versions(memory_id,limit=20) if store else []
            return {'memory_id':memory_id,'content_version':record_version(record),
                'items':[{'revision':v.revision,'operation':v.operation,'content':v.snapshot.get('canonical_text'),
                    'created_at':v.created_at,'origin':v.snapshot.get('metadata',{}).get('last_modified_origin')} for v in versions]}


def revision_service(kb):
    return MemoryRevisions(kb)
