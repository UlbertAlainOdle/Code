"""Native owner consent over the original maintenance/revision engines.

Only references, tokens and content-free results are persisted here. Full
previews are reloaded under current source permissions, never copied to chat.
"""
from dataclasses import asdict, is_dataclass
from uuid import uuid4
import json
import time

from .memory_versions import check_version
from .memory_write_operations import fingerprint
from .qq_memory_access import MemoryActor, MemoryAccessDenied
from .qq_responses import ConsoleNotice


TABLE='qq_memory_console_actions'


def serialized(value):
    if is_dataclass(value):return serialized(asdict(value))
    if isinstance(value,dict):return {str(k):serialized(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [serialized(v) for v in value]
    if value is None or type(value) in {str,int,float,bool}:return value
    raise ValueError('unsupported_native_preview')


class QQConsoleActions:
    def __init__(self,management):
        self.m=management;self.b=management.bridge;self.kb=management.kb;self.generation=uuid4().hex
        with self.b.store.connect() as db:
            db.execute(f'''create table if not exists {TABLE}(
                id text primary key,bot text not null,owner text not null,topic text not null,
                kind text not null,refs text not null,settings_revision text not null,
                epoch text not null,expires real not null,phase text not null,revision integer not null,
                result text not null)''')

    def actor(self,owner):
        actor=MemoryActor(self.b.settings.bot_id,owner,True,True);self.m.require_owner(actor);return actor

    def context(self,owner):
        actor=self.actor(owner);scope=f'{actor.bot_id}:private:{owner}:shared'
        topic=self.b.store.conversation(scope)[1]
        if not isinstance(topic,str) or not topic:raise ValueError('native_topic_unavailable')
        self.m.authorize_operation(actor,scope,topic)
        return actor,scope,topic

    def command_preview(self,owner,action,members):
        """A local command creates the same full preview, never a commit."""
        from .memory_versions import record_version
        with self.kb._memory_write_lock:
            actor,scope,topic=self.context(owner)
            if action in {'link','archive'} and len(members)==2:
                versions=[record_version(self.m._record(i)) for i in members]
                return self.prepare(owner,action,*members,*versions)
            if action not in {'delete','block','unblock'} or len(members)!=1:raise ValueError('native_command_not_supported')
            record=self.m._record(members[0])
            preview=self.m.operations.preview(actor,scope,topic,members[0],record_version(record),action=action)
            if preview.get('status')=='blocked':raise MemoryAccessDenied('native_memory_protected')
            identifier=self.register(actor,topic,action,{'token':preview['confirmation_token'],'members':members})
            return self.preview(owner,identifier)

    def prepare_duplicate(self,owner,keep,duplicate,keep_version,duplicate_version,review):
        with self.kb._memory_write_lock:
            actor,scope,topic=self.context(owner)
            a,b=self.m._record(keep),self.m._record(duplicate)
            check_version(a,keep_version);check_version(b,duplicate_version)
            library=self.m.library
            from .qq_memory_library import duplicate_guard
            if not all(library.eligible(r) for r in (a,b)) or duplicate_guard(a,b) or b.get('is_important') or b.get('importance_locked'):
                raise ValueError('library_duplicate_protected_or_changed')
            result=self.m.operations.preview(actor,scope,topic,duplicate,duplicate_version,action='block')
            if result.get('status')=='blocked':raise MemoryAccessDenied('native_memory_protected')
            identifier=self.register(actor,topic,'block',{'token':result['confirmation_token'],'members':[keep,duplicate],
                'keep':keep,'keep_version':keep_version,'library_review':review})
            return self.preview(owner,identifier)

    def recover(self):
        with self.b.store.connect() as db:
            db.execute(f"update {TABLE} set phase='unknown' where phase='executing'")
            db.execute(f"update {TABLE} set revision=revision+1 where phase='prepared'")
        self.sweep()

    def sweep(self):
        with self.b.store.connect() as db:
            db.execute(f"update {TABLE} set phase='expired',revision=revision+1 where phase='prepared' and expires<=?",(time.time(),))

    def row(self,owner,identifier):
        actor=self.actor(owner)
        with self.b.store.connect() as db:
            row=db.execute(f'select * from {TABLE} where id=? and bot=? and owner=?',(identifier,actor.bot_id,owner)).fetchone()
        if not row:raise LookupError('native_action_unavailable')
        return dict(row)

    def register(self,actor,topic,kind,refs):
        self.m.authorize_operation(actor,f'{actor.bot_id}:private:{actor.user_id}:shared',topic)
        if kind not in {'link','archive','revision','delete','block','unblock'}:raise ValueError('native_action_kind')
        identifier=uuid4().hex
        with self.b.store.connect() as db:
            db.execute('begin immediate')
            if db.execute(f'select count(*) from {TABLE}').fetchone()[0]>=4096:raise ValueError('native_action_capacity')
            db.execute(f'insert into {TABLE} values(?,?,?,?,?,?,?,?,?,\'prepared\',1,\'{{}}\')',
                (identifier,actor.bot_id,actor.user_id,topic,kind,json.dumps(refs),self.b.controls()['revision'],self.kb.memory_use.snapshot(),time.time()+600))
        return identifier

    def prepare(self,owner,kind,first,second,first_version,second_version):
        if kind not in {'link','archive'}:raise ValueError('native_pair_kind')
        with self.kb._memory_write_lock:
            actor,scope,topic=self.context(owner)
            check_version(self.m._record(first),first_version);check_version(self.m._record(second),second_version)
            links=self.m.maintenance_links(actor,topic)
            operation=uuid4().hex
            if kind=='link':
                a,b=links.load(first),links.load(second)
                if not a or not b:raise ValueError('native_pair_unavailable')
                result=links.prepare(operation,first,second,anchor_version=a.version,candidate_version=b.version,
                    comparison=True,check_preview=serialized,preview_channel='native')
            else:
                from .maintenance_archive import MaintenanceArchive
                result=MaintenanceArchive(self.kb,links,self.m.access).prepare(operation,first,second,check_preview=serialized,preview_channel='native')
            if result['status']!='pending':return serialized(result)
            identifier=self.register(actor,topic,kind,{'operation':operation,'members':[first,second]})
            return self.preview(owner,identifier)

    def register_revision(self,actor,topic,preview):
        identifier=self.register(actor,topic,'revision',{'token':preview['confirmation_token'],'members':[preview['memory_id']]})
        return ConsoleNotice('这次修改已放进记忆操作台，请在那里核对完整前后内容；现在还没有修改。')

    def register_operation(self,actor,topic,kind,identifier,preview):
        self.register(actor,topic,kind,{'token':preview['confirmation_token'],'members':[identifier]})
        return ConsoleNotice('这次操作已放进记忆操作台，请在那里核对影响范围后决定；现在还没有执行。')

    def offer_pair(self,owner,topic,kind,members):
        if len(members)!=2:raise ValueError('native_pair_members')
        actor,_,current=self.context(owner)
        if current!=topic:raise MemoryAccessDenied('native_topic_changed')
        a,b=[self.m._record(x) for x in members]
        from .memory_versions import record_version
        result=self.prepare(owner,kind,*members,record_version(a),record_version(b))
        if result.get('status')!='pending':return ConsoleNotice('这两个来源当前不满足整理条件，没有执行操作。请在记忆操作台分别核对。')
        return ConsoleNotice('这次整理已放进记忆操作台，请在那里核对完整来源后确认；现在还没有执行。')

    def _engine(self,actor,topic,row,*,consent=False):
        refs=json.loads(row['refs'])
        def native(operation):
            if not consent or operation!=refs.get('operation'):return False
            with self.b.store.connect() as db:
                current=db.execute(f'select phase from {TABLE} where id=? and bot=? and owner=?',(row['id'],actor.bot_id,actor.user_id)).fetchone()
            return bool(current and current['phase']=='executing')
        links=self.m.maintenance_links(actor,topic,native_consent=native)
        if row['kind']=='archive':
            from .maintenance_archive import MaintenanceArchive
            return MaintenanceArchive(self.kb,links,self.m.access)
        return links

    def _view(self,row):
        actor=self.actor(row['owner']);refs=json.loads(row['refs']);kind=row['kind']
        if row['phase'] in {'cancelled','expired'}:return {'status':row['phase']}
        if kind in {'revision','delete','block','unblock'} and row['phase']=='finished':return json.loads(row['result'])
        if not self.b.settings.shared_memory:raise MemoryAccessDenied('native_action_readonly')
        current=self.b.store.conversation(f'{actor.bot_id}:private:{actor.user_id}:shared')[1]
        if row['phase']=='prepared' and (current!=row['topic'] or row['settings_revision']!=self.b.controls()['revision'] or row['epoch']!=self.kb.memory_use.snapshot()):
            return {'status':'stale'}
        if kind in {'link','archive'}:
            engine=self._engine(actor,current,row)
            result=engine.result(refs['operation'])
            if kind=='archive' and result['status']=='pending':result=engine.pending(refs['operation'])
            elif kind=='archive' and result['status']=='archived':
                visible=engine.for_member(refs['members'][0])
                if visible['status']=='archived':
                    result=result|visible|{'sources':[[{'record_id':visible['keep'],'source':source}] for source in visible['sources']]}
                else:result={'status':'invalidated'}
        else:
            scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
            authorize=lambda:self.m.authorize_operation(actor,scope,current)
            if kind=='revision':
                from .memory_revisions import revision_service
                result=revision_service(self.kb).pending(refs['token'],scope='qq-console:'+scope,session_id=row['topic'],authorize=authorize)
                if refs.get('overlap_plan') and result.get('status')=='pending':
                    try:
                        body,anchors=self.m.overlap.check_plan(self.m._record(result['memory_id']),refs['overlap_plan'])
                        if body!=result['new']:return {'status':'stale'}
                    except (ValueError,LookupError):return {'status':'stale'}
                    result=result|{'overlap_anchors':anchors}
            else:
                result=self.m.operations.pending(actor,scope,row['topic'],refs['token'])
                if refs.get('keep') and result.get('status')=='pending':
                    keep=self.m._record(refs['keep']);duplicate=self.m._record(refs['members'][1])
                    from .qq_memory_library import duplicate_guard
                    if not self.m.library.eligible(keep) or duplicate_guard(keep,duplicate):return {'status':'stale'}
                    try:
                        check_version(keep,refs['keep_version'])
                        self.m.library.check_keep_dependency(keep['id'],duplicate['id'])
                    except ValueError:return {'status':'stale'}
                    result=result|{'keep':keep['id'],'keep_body':str(keep.get('content') or ''),
                        'deduplication':'only_stop_duplicate_keep_original_bodies_and_sources'}
        if row['phase'] in {'executing','unknown'} and result['status']=='pending':return {'status':'unknown'}
        return serialized(result)

    def preview(self,owner,identifier):
        with self.kb._memory_write_lock:
            self.sweep();row=self.row(owner,identifier);view=self._view(row)
            return {'id':identifier,'kind':row['kind'],'members':json.loads(row['refs'])['members'],'status':view['status'],
                    'expires':row['expires'],'view':view,'preview_version':fingerprint([row,view,self.generation])}

    def listing(self,owner):
        actor=self.actor(owner);self.sweep()
        with self.b.store.connect() as db:
            rows=db.execute(f'select id,kind,refs,phase,expires from {TABLE} where bot=? and owner=? order by rowid desc limit 150',(actor.bot_id,owner)).fetchall()
        return {'entries':[{'id':r['id'],'kind':r['kind'],'members':json.loads(r['refs'])['members'],'phase':r['phase'],'expires':r['expires']} for r in rows]}

    def decide(self,owner,identifier,version,action):
        if action not in {'confirm','undo','cancel'}:raise ValueError('native_action_decision')
        with self.kb._memory_write_lock:
            actor,scope,topic=self.context(owner);row=self.row(owner,identifier)
            current=self.preview(owner,identifier);kind=row['kind'];refs=json.loads(row['refs'])
            if row['phase']=='finished' and action=='confirm':return current
            if current['preview_version']!=version:raise ValueError('native_action_preview_changed')
            if action=='cancel':
                if row['phase']!='prepared':return current
                with self.b.store.connect() as db:db.execute(f"update {TABLE} set phase='cancelled',revision=revision+1 where id=?",(identifier,))
                return self.preview(owner,identifier)
            if action=='confirm' and (row['phase']!='prepared' or current['status']!='pending' or topic!=row['topic']):
                raise ValueError('native_action_not_pending')
            if action=='undo' and not ((kind=='link' and current['status'] in {'active','invalidated'}) or (kind=='archive' and current['status']=='archived')):
                raise ValueError('native_action_not_reversible')
            with self.b.store.connect() as db:db.execute(f"update {TABLE} set phase='executing' where id=?",(identifier,))
            try:
                if kind in {'link','archive'}:
                    engine=self._engine(actor,topic,row,consent=True)
                    result=(engine.confirm if action=='confirm' else engine.undo)(refs['operation'])
                elif kind=='revision':
                    from .memory_revisions import revision_service
                    from .memory_origin import bind_origin,ingress
                    with bind_origin(ingress('web',subject=owner)):
                        result=revision_service(self.kb).confirm(refs['token'],scope='qq-console:'+scope,session_id=topic,
                            authorize=lambda:self.m.authorize_operation(actor,scope,topic))
                else:result=self.m.operations.confirm(actor,scope,topic,refs['token'])
                safe={k:result[k] for k in ('status','memory_id','operation','content_version','operation_state','deleted_count') if k in result}
                if kind=='revision' and result.get('status')=='applied':
                    # A secondary recent-index failure must not conceal or
                    # repeat a committed original-ID revision.
                    try:self.m.saved_receipt(actor,result['memory_id'],topic,confirmed=False)
                    except Exception:safe['recent_index']='unavailable'
                phase='unknown' if result.get('status') in {'unknown','partial'} else 'finished'
                with self.b.store.connect() as db:
                    db.execute(f'update {TABLE} set phase=?,result=?,revision=revision+1 where id=?',(phase,json.dumps(safe),identifier))
            except BaseException:
                with self.b.store.connect() as db:db.execute(f"update {TABLE} set phase='unknown' where id=?",(identifier,))
                raise
            return self.preview(owner,identifier)
