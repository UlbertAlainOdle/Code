"""Identity/topic/version-bound QQ deletion confirmation contract.

Uses the existing KB deletion service and its relationship signature, never a
second CRUD implementation. Mutating QQ ingress remains separately gated.
"""
import hashlib
import secrets
import time

from .memory_versions import check_version


class QQMemoryOperations:
    def __init__(self, store, kb, authorize, *, clock=time.time):
        self.store, self.kb, self.authorize, self.clock = store, kb, authorize, clock
        with store.connect() as db:
            db.execute('''create table if not exists qq_memory_operations(
                token_hash text primary key,scope text not null,session_id text not null,
                action text not null,memory_id text not null,content_version text not null,
                backend_token text not null,expires real not null,state text not null)''')

    def recover(self):
        # Call only after acquiring the existing consumer's exclusive lease.
        with self.store.connect() as db:
            db.execute("update qq_memory_operations set state='unknown' where state='executing'")
            # Backend deletion previews are process-local. A restart revokes
            # the pending intent even when its displayed TTL has not elapsed.
            db.execute("update qq_memory_operations set state='restart_invalidated' where state='pending'")

    def preview_delete(self, actor, scope, session_id, memory_id, version):
        return self.preview(actor,scope,session_id,memory_id,version,action='delete')

    def preview(self, actor, scope, session_id, memory_id, version, *, action='delete'):
        if action not in {'delete','block','unblock'}:raise ValueError('unsupported_memory_operation')
        self.authorize(actor, scope, session_id)
        with self.kb._memory_write_lock:
            self.authorize(actor, scope, session_id)
            if action=='delete':
                preview = self.kb.preview_memory_deletion([memory_id], reason='user_delete',expected_versions={memory_id: version})
            else:
                memory=self.kb._find_memory(memory_id);check_version(memory,version)
                if memory is None or memory.get('managed_slot') or memory.get('source')=='assistant_role':
                    return {'status':'blocked','blocked':[{'reason':'managed_memory'}]}
                preview={'delete_count':1,'blocked':[],'confirmation_token':'','titles':[str(memory.get('title') or '')[:160]]}
            if preview['delete_count'] != 1 or preview['blocked']:
                return {'status':'blocked', 'blocked':preview['blocked']}
            token = secrets.token_urlsafe(24)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            with self.store.connect() as db:
                db.execute('begin immediate')
                db.execute('delete from qq_memory_operations where expires<?', (self.clock()-86400,))
                if db.execute('select count(*) from qq_memory_operations').fetchone()[0] >= 1024:
                    raise ValueError('confirmation_limit')
                db.execute("update qq_memory_operations set state='superseded' where scope=? and session_id=? and state='pending'", (scope,session_id))
                db.execute("insert into qq_memory_operations values(?,?,?,?,?,?,?,?, 'pending')",
                    (token_hash,scope,session_id,action,memory_id,version,preview['confirmation_token'],self.clock()+600))
            return {**preview, 'confirmation_token':token,
                'message':{'delete':'仅删除这条长期记忆；原始聊天记录保留。旧回答不再续读。',
                    'block':'停止使用这条记忆及其已知派生内容，并从新话题继续；原聊天文件保留。',
                    'unblock':'恢复这条记忆的使用；先前排除的历史不会自动重新加入。'}[action]}

    def confirm_delete(self, actor, scope, session_id, token):
        return self.confirm(actor,scope,session_id,token,required_action='delete')

    def pending(self,actor,scope,session_id,token):
        self.authorize(actor,scope,session_id)
        if not isinstance(token,str) or not 16<=len(token)<=128:return {'status':'unavailable'}
        with self.kb._memory_write_lock,self.store.connect() as db:
            self.authorize(actor,scope,session_id)
            row=db.execute('select * from qq_memory_operations where token_hash=? and scope=? and session_id=?',
                           (hashlib.sha256(token.encode()).hexdigest(),scope,session_id)).fetchone()
            if not row:return {'status':'unavailable'}
            result={'status':row['state'],'memory_id':row['memory_id'],'action':row['action']}
            if row['state']!='pending':return result
            if row['expires']<=self.clock():return result|{'status':'expired'}
            record=self.kb._find_memory(row['memory_id'])
            try:check_version(record,row['content_version'])
            except ValueError:return result|{'status':'stale'}
            return result|{'body':str(record.get('content') or ''),'title':str(record.get('title') or '')}

    def confirm(self, actor, scope, session_id, token, *, required_action=None):
        self.authorize(actor, scope, session_id)
        if not isinstance(token,str) or not 16 <= len(token) <= 128:
            return {'status':'unavailable'}
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.kb._memory_write_lock:
            self.authorize(actor, scope, session_id)
            with self.store.connect() as db:
                db.execute('begin immediate')
                row = db.execute('select * from qq_memory_operations where token_hash=? and scope=? and session_id=?',
                    (digest,scope,session_id)).fetchone()
                if row is None:return {'status':'unavailable'}
                if required_action is not None and row['action']!=required_action:return {'status':'unavailable'}
                identity={'action':row['action'],'memory_id':row['memory_id']}
                if row['state'] != 'pending':return {'status':row['state'],**identity}
                if row['expires'] <= self.clock():
                    db.execute("update qq_memory_operations set state='expired' where token_hash=?",(digest,))
                    return {'status':'expired',**identity}
                try:check_version(self.kb._find_memory(row['memory_id']),row['content_version'])
                except ValueError:
                    db.execute("update qq_memory_operations set state='stale' where token_hash=?",(digest,))
                    return {'status':'stale',**identity}
                db.execute("update qq_memory_operations set state='executing' where token_hash=?",(digest,))
            try:
                if row['action']=='delete':
                    result = self.kb.delete_memories([row['memory_id']],reason='user_delete',
                        confirmation_token=row['backend_token'],expected_versions={row['memory_id']:row['content_version']},
                        write_check=lambda:self.authorize(actor,scope,session_id))
                    applied=result.get('status')=='success' and result.get('deleted_count')==1
                elif row['action'] in {'block','unblock'}:
                    result=self.kb.set_memory_use_policy(row['memory_id'],row['action']=='block',expected_version=row['content_version'],
                        write_check=lambda:self.authorize(actor,scope,session_id))
                    applied=result.get('policy_applied') is True
                else:raise ValueError('unsupported_memory_operation')
                state = 'done' if applied else 'stale'
                with self.store.connect() as db:
                    db.execute('update qq_memory_operations set state=? where token_hash=?',(state,digest))
                return {**result,'operation_state':state,'action':row['action']}
            except BaseException:
                with self.store.connect() as db:
                    db.execute("update qq_memory_operations set state='unknown' where token_hash=?",(digest,))
                raise
