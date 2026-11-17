"""Source-bound technical queries rendered locally, never copied into persona chat."""
from dataclasses import fields,asdict
import json,re,time
from .memory_write_operations import fingerprint
from .qq_memory_access import MemoryActor,MemoryAccessDenied
from .qq_responses import ConsoleNotice

TABLE='qq_memory_console_reads'
LABELS={'list':'记忆清单','search':'查找记忆','show':'完整详情','result':'保存结果','history':'修改历史','why':'使用依据','help':'操作说明',
        'events':'个人事件查询','link-show':'联合视图来源','link-result':'联合视图结果','archive-show':'归档来源','archive-result':'归档结果'}


def readonly_query(text):
    text=str(text).strip()
    if re.fullmatch(r'/memories(?:\s+[1-9]\d{0,3})?',text):return 'candidates'
    if re.fullmatch(r'/memory(?:\s+(?:help|why|list(?:\s+[1-9]\d{0,3})?|search\s+[^\r\n]{1,200}|(?:show|history)\s+[A-Za-z0-9_.:-]{1,128}|result\s+[a-f0-9]{32}))?',text):return 'memory'
    if re.fullmatch(r'/memory\s+(?:(?:link|archive)-show\s+[A-Za-z0-9_.:-]{1,128}|(?:link|archive)-result\s+[a-f0-9]{32})',text):return 'memory'
    if re.fullmatch(r'/events(?:\s+(?:self|stopped)|\s+(?:show|why)\s+[a-f0-9]{16})?',text):return 'events'
    return None


class QQConsoleReads:
    def __init__(self,management):
        self.m=management;self.b=management.bridge
        with self.b.store.connect() as db:
            db.execute(f'''create table if not exists {TABLE}(id text primary key,bot text,owner text,event text,
                signature text,session text,label text,created real,expires real,request text not null default '{{}}')''')
            if 'request' not in {r[1] for r in db.execute('pragma table_info('+TABLE+')')}:
                db.execute("alter table "+TABLE+" add column request text not null default '{}'")

    def actor(self,owner):
        actor=MemoryActor(self.b.settings.bot_id,owner,True,True);self.m.require_owner(actor)
        if not self.b.settings.enabled or not self.b.settings.shared_memory:raise MemoryAccessDenied('native_read_disabled')
        return actor

    def eligible(self,m):
        s=self.b.settings
        return bool(s.enabled and s.shared_memory and m.bot_id==s.bot_id and m.user_id in s.owner_ids and m.shared and not m.group_id
            and m.kind=='message' and not m.reply_to and not m.forward_present and not m.media_types and not getattr(m,'quote_context',None))

    @staticmethod
    def signature(raw):
        return fingerprint({k:raw.get(k) for k in ('key','scope','bot_id','user_id','group_id','message_id','text','shared','kind','reply_to','forward_present','media_types','context_snapshot')})

    def offer(self,m,session):
        kind=readonly_query(m.text)
        if not kind or not self.eligible(m):return None
        with self.b.store.connect() as db:
            source=db.execute('select scope,payload from inbox where event_key=?',(m.key,)).fetchone()
            raw=json.loads(source['payload']) if source else {}
            if not source or source['scope']!=m.scope or any(raw.get(k)!=getattr(m,k) for k in ('bot_id','user_id','message_id','text','shared')):
                raise ValueError('native_read_original_ingress_required')
            now=time.time();db.execute('delete from '+TABLE+' where expires<=?',(now,))
            identifier=fingerprint([m.bot_id,m.user_id,m.key])[:32]
            if not db.execute('select 1 from '+TABLE+' where id=?',(identifier,)).fetchone():
                if db.execute('select count(*) from '+TABLE).fetchone()[0]>=1024:raise ValueError('native_read_queue_full')
                words=m.text.split();label='旧候选清单' if kind=='candidates' else LABELS['events'] if kind=='events' else LABELS.get(words[1] if len(words)>1 else 'help','记忆查询')
                # Terminal inbox rows intentionally erase their payload. Keep
                # only this bounded technical query and its host identity fence,
                # never the requested memory/event body or a rendered result.
                request={k:raw.get(k) for k in ('key','scope','bot_id','user_id','group_id','message_id','text','shared','kind','reply_to','forward_present','media_types','context_snapshot') if k in raw}
                db.execute('insert into '+TABLE+' values(?,?,?,?,?,?,?,?,?,?)',(identifier,m.bot_id,m.user_id,m.key,self.signature(raw),session,label,now,now+86400,json.dumps(request)))
        return ConsoleNotice('查询已放进后台记忆操作台的“查询与回执”，可以在那里查看完整内容。')

    def listing(self,owner):
        actor=self.actor(owner)
        with self.b.store.connect() as db:
            rows=db.execute('select id,label,created,expires from '+TABLE+' where bot=? and owner=? and expires>? order by created desc limit 150',
                (actor.bot_id,owner,time.time())).fetchall()
        return {'available':True,'entries':[dict(r) for r in rows]}

    def source(self,owner,identifier):
        actor=self.actor(owner)
        with self.b.store.connect() as db:
            row=db.execute('select * from '+TABLE+' where id=? and bot=? and owner=? and expires>?',(identifier,actor.bot_id,owner,time.time())).fetchone()
            source=db.execute('select scope,payload,state from inbox where event_key=?',(row['event'],)).fetchone() if row else None
        if not row or not source:raise LookupError('native_read_source_expired')
        raw=json.loads(row['request']);ingress=json.loads(source['payload'])
        if not raw or source['scope']!=raw.get('scope') or (ingress and self.signature(ingress)!=row['signature']):
            raise MemoryAccessDenied('native_read_source_changed')
        if not ingress and source['state'] not in {'sent','processed','delivery_unknown'}:raise MemoryAccessDenied('native_read_source_incomplete')
        from .qq_bridge import QQMessage
        m=QQMessage(**{f.name:raw[f.name] for f in fields(QQMessage) if f.name in raw})
        if self.signature(raw)!=row['signature'] or not self.eligible(m) or not readonly_query(m.text):raise MemoryAccessDenied('native_read_source_changed')
        return m,row['session']

    def render(self,owner,text,session,message=None):
        actor=self.actor(owner);kind=readonly_query(text)
        if not kind:raise ValueError('native_read_command_only')
        if kind=='candidates':
            words=text.split();page=int(words[1]) if len(words)>1 else 1
            value=json.dumps(self.m.browse(actor,kind='candidates',page=page),ensure_ascii=False,indent=2)
        elif kind=='memory':
            words=text.split(maxsplit=2)
            if len(words)==3 and words[1]=='show':
                # Native details are full originals, not the old QQ 1600-char excerpt.
                detail=self.m.detail(actor,words[2]);value=json.dumps(detail,ensure_ascii=False,indent=2)
            elif len(words)==3 and words[1]=='history':
                from .memory_revisions import revision_service
                value=json.dumps(revision_service(self.m.kb).history(words[2],subject_id=owner),ensure_ascii=False,indent=2)
            elif len(words)==3 and words[1] in {'link-show','link-result','archive-show','archive-result'}:
                engine=self.m.maintenance_links(actor,session) if words[1].startswith('link-') else self.m.maintenance_archive(actor,session)
                result=engine.for_member(words[2]) if words[1].endswith('-show') else engine.result(words[2])
                # Full validated originals; native reads have no QQ chunk limit
                # and never mark a pending preview as delivered or confirmed.
                value=json.dumps(result,default=asdict,ensure_ascii=False,indent=2)
            else:
                result=self.m.command(actor,text,session);value=str(getattr(result,'payload',result))
        else:
            service=getattr(self.b,'episodes',None)
            if not service:raise LookupError('native_event_read_unavailable')
            if message is None:
                from .qq_bridge import QQMessage
                # A local admin's read actor, never admitted/delivered as QQ.
                message=QQMessage('native-console:read',f'{actor.bot_id}:private:{owner}',actor.bot_id,owner,'','',text,True)
            result=service.command(message);value=str(getattr(result,'payload',result))
        return {'text':value,'query':text,'read_only':True}

    def read(self,owner,identifier):
        with self.m.kb._memory_write_lock:
            message,session=self.source(owner,identifier)
            return self.render(owner,message.text,session,message)

    def local(self,owner,text):
        actor=self.actor(owner);session=self.b.store.conversation(f'{actor.bot_id}:private:{owner}:shared')[1]
        with self.m.kb._memory_write_lock:return self.render(owner,text,session)
