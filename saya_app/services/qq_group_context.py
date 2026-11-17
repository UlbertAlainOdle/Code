"""Explicit per-group context and facts, separate from owner chat/history.

Default off. All references are data, with provenance checked before prompt,
stream, delivery and continuation. User identity comes only from QQ ingress.
"""
from dataclasses import asdict
import asyncio
import hashlib
import json
import re
import time
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .memory_versions import record_version
from .qq_memory_access import MemoryActor, MemoryReadGrant, valid_id
from .qq_memory_management import policy_content_revision
from .qq_tasks import checkpoint_current, effect_current


class GroupPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    context_enabled:bool=False
    memory_enabled:bool=False
    write_enabled:bool=False
    admin_writes:bool=False
    reply_wakeup:bool=False
    writer_ids:list[str]=Field(default_factory=list,max_length=128)
    retention_seconds:int=Field(default=3600,ge=60,le=86400)
    topic_idle_seconds:int=Field(default=1800,ge=60,le=7200)
    memory_retention_days:int=Field(default=7,ge=1,le=90)

    @field_validator('writer_ids')
    @classmethod
    def validate_writers(cls,value):
        if any(not valid_id(x) for x in value):raise ValueError('invalid_group_writer')
        return sorted(set(value))


class GroupConflict(ValueError):pass
class GroupDenied(ValueError):pass
class GroupReceipt(str):pass


class QQGroupContext:
    def __init__(self,bridge,kb,*,clock=time.time):
        self.bridge,self.kb,self.store,self.clock=bridge,kb,bridge.store,clock
        from .qq_memory_access import QQMemoryAccessStore
        self.access=QQMemoryAccessStore(self.store)
        with self.store.connect() as db:
            db.executescript('''
                create table if not exists qq_group_policies(bot text,grp text,revision integer,policy text,
                    topic text,activity real,primary key(bot,grp));
                create table if not exists qq_group_turns(id integer primary key autoincrement,bot text,grp text,
                    topic text,speaker text,label text,role text,text text,at real,requirements text);
                create index if not exists qq_group_turn_scope on qq_group_turns(bot,grp,topic,id);
                create table if not exists qq_group_facts(id text primary key,bot text,grp text,subject text,
                    content text,source_event text,created real,expires real,active integer);
                create table if not exists qq_group_results(result_id text primary key,snapshot text);
                create table if not exists qq_group_events(event_key text primary key,snapshot text);
                create table if not exists qq_group_privacy_guard(bot text,grp text,since real,primary key(bot,grp));
                create table if not exists qq_group_delete_tokens(token text primary key,bot text,grp text,
                    actor text,target text,version text,policy_revision integer,expires real,state text);
            ''')

    def actor(self,message):
        b=self.bridge
        accepted=bool(b.settings and b.settings.enabled and message.bot_id==b.settings.bot_id
            and message.group_id in b.effective_groups())
        return MemoryActor(message.bot_id,message.user_id,accepted,
            bool(b.settings and message.user_id in b.settings.owner_ids),message.group_id,accepted,
            bool(b.settings and b.settings.memory_write_enabled))

    def policy(self,group_id):
        bot=self.bridge.settings.bot_id
        with self.store.connect() as db:
            row=db.execute('select revision,policy,topic,activity from qq_group_policies where bot=? and grp=?',(bot,group_id)).fetchone()
        return {'revision':row[0] if row else 0,'values':GroupPolicy.model_validate(json.loads(row[1]) if row else {}).model_dump(),
            'topic':row[2] if row else '', 'activity':row[3] if row else 0}

    def configure(self,owner_id,group_id,values,expected_revision,*,confirmed):
        b=self.bridge
        if (not b.settings or owner_id not in b.settings.owner_ids or group_id not in b.effective_groups()
                or not valid_id(group_id) or confirmed is not True):raise GroupDenied('owner_explicit_confirmation_required')
        if type(expected_revision) is not int or expected_revision<0:raise ValueError('invalid_group_revision')
        values=GroupPolicy.model_validate(values).model_dump()
        if (values['context_enabled'] or values['memory_enabled'] or values['reply_wakeup']) and not b.settings.task_control_enabled:
            raise GroupDenied('task_control_required')
        with self.store.connect() as db:
            db.execute('begin immediate')
            row=db.execute('select revision from qq_group_policies where bot=? and grp=?',(b.settings.bot_id,group_id)).fetchone()
            if (row[0] if row else 0)!=expected_revision:raise GroupConflict('group_policy_changed')
            db.execute('insert or replace into qq_group_policies values(?,?,?,?,?,?)',
                (b.settings.bot_id,group_id,expected_revision+1,json.dumps(values),uuid4().hex,self.clock()))
            if values['memory_enabled']:
                db.execute('insert or ignore into qq_group_privacy_guard values(?,?,?)',(b.settings.bot_id,group_id,self.clock()))
        return self.policy(group_id)

    def prune(self):
        now=self.clock()
        with self.store.connect() as db:
            db.execute('delete from qq_group_turns where at<?',(now-86400,))
            db.execute('delete from qq_group_facts where expires<?',(now,))
            db.execute('delete from qq_group_delete_tokens where expires<?',(now,))
            db.execute('delete from qq_group_results where result_id not in (select id from qq_deliveries)')
            db.execute("delete from qq_group_events where event_key not in (select event_key from qq_deliveries) and event_key not in (select event_key from inbox where state in ('queued','generating','sending'))")

    def _known_receipt(self,group_id,receipt):
        if not re.fullmatch(r'-?[0-9]{1,20}',str(receipt)):return False
        with self.store.connect() as db:
            rows=db.execute("select d.origin from qq_delivery_parts p join qq_deliveries d on d.id=p.result_id where p.receipt=? and p.state='sent' and d.expires>? limit 4",(str(receipt),self.clock())).fetchall()
        return any((lambda origin:origin.get('bot_id')==self.bridge.settings.bot_id and origin.get('group_id')==group_id)(json.loads(row[0])) for row in rows)

    def wake_proof(self,event):
        if not isinstance(event,dict) or event.get('message_type')!='group':return None
        group=str(event.get('group_id') or '')
        if str(event.get('self_id'))!=self.bridge.settings.bot_id or group not in self.bridge.effective_groups() or not self.bridge.settings.task_control_enabled:return None
        state=self.policy(group)
        if not state['values']['reply_wakeup']:return None
        segments=event.get('message')
        if not isinstance(segments,list):return None
        replies=[str(x.get('data',{}).get('id') or '') for x in segments if isinstance(x,dict) and x.get('type')=='reply' and isinstance(x.get('data'),dict)]
        if len(replies)!=1 or not self._known_receipt(group,replies[0]):return None
        return {'status':'wake_pending','revision':state['revision'],'expires':self.clock()+self.bridge.settings.max_event_age,'receipt':replies[0]}

    def wake_allowed(self,message):
        revision=getattr(message,'reply_wake_revision',0)
        if not revision:return True
        state=self.policy(message.group_id)
        return (self.actor(message).accepted() and self.bridge.settings.task_control_enabled
            and state['values']['reply_wakeup'] and state['revision']==revision
            and getattr(message,'reply_wake_expires',0)>self.clock() and self._known_receipt(message.group_id,message.reply_to))

    def lane(self,message):
        if message.group_id and self.bridge.settings.task_control_enabled and self.actor(message).accepted() and self.policy(message.group_id)['values']['context_enabled']:
            return f'group-context:{message.bot_id}:{message.group_id}'
        return message.scope+(':shared' if message.shared else ':isolated')

    def _topic(self,actor,state):
        now=self.clock();values=state['values']
        if not state['topic'] or now-state['activity']>min(values['retention_seconds'],values['topic_idle_seconds']):
            with self.store.connect() as db:
                db.execute('begin immediate')
                row=db.execute('select revision,topic,activity from qq_group_policies where bot=? and grp=?',(actor.bot_id,actor.group_id)).fetchone()
                if not row or row[0]!=state['revision']:raise GroupConflict('group_policy_changed')
                topic=uuid4().hex
                db.execute('update qq_group_policies set topic=?,activity=? where bot=? and grp=?',
                    (topic,now,actor.bot_id,actor.group_id))
            state={**state,'topic':topic,'activity':now}
        return state

    @staticmethod
    def _matches(query,text):
        terms=re.findall(r'[a-z0-9_]{2,}|[\u3400-\u9fff]{2}',query.lower())
        return any(x in text.lower() for x in terms) or any(x in query for x in ['记得','记忆','约定','remember'])

    def _valid_requirements(self,actor,requirements):
        if not actor.accepted() or not isinstance(requirements,list) or len(requirements)>64:return False
        with self.kb._memory_write_lock:
            for required in requirements:
                if not isinstance(required,dict):return False
                if required.get('kind')=='personal':
                    m=self.kb._find_memory(required.get('id'))
                    if not m or record_version(m)!=required.get('version') or not self.kb.memory_allowed_for_use(m):return False
                    try:grant=MemoryReadGrant(**required['grant'])
                    except (KeyError,TypeError):return False
                    if not self.access.valid_disclosure(actor,grant,policy_content_revision(m)):return False
                elif required.get('kind')=='group':
                    with self.store.connect() as db:
                        row=db.execute('select * from qq_group_facts where id=? and bot=? and grp=? and active=1 and expires>?',
                            (required.get('id'),actor.bot_id,actor.group_id,self.clock())).fetchone()
                    if not row or record_version(dict(row))!=required.get('version'):return False
                elif required.get('kind')=='shared_event':
                    from types import SimpleNamespace
                    message=SimpleNamespace(bot_id=actor.bot_id,user_id=actor.user_id,group_id=actor.group_id)
                    service=getattr(self.bridge,'episodes',None)
                    if not service or not service.unified.valid(message,required.get('snapshot')):return False
                elif required.get('kind')=='episode':
                    if not isinstance(required.get('snapshot'),dict):return False
                    from types import SimpleNamespace
                    message=SimpleNamespace(bot_id=actor.bot_id,user_id=actor.user_id,group_id=actor.group_id,
                        scope=f'{actor.bot_id}:group:{actor.group_id}:user:{actor.user_id}',shared=False)
                    episodes=getattr(self.bridge,'episodes',None)
                    if not episodes or not episodes.valid(message,required.get('snapshot'),history=True):return False
                elif required.get('kind')=='event_dependencies':
                    from types import SimpleNamespace
                    service=getattr(getattr(self.bridge,'episodes',None),'provenance',None)
                    message=SimpleNamespace(bot_id=actor.bot_id,user_id=actor.user_id,group_id=actor.group_id,
                        scope=f'{actor.bot_id}:group:{actor.group_id}:user:{actor.user_id}',shared=False)
                    if not service or not service.valid(message,required.get('tag')):return False
                else:return False
        return True

    @staticmethod
    def _unique(requirements):
        return list({json.dumps(r,sort_keys=True):r for r in requirements}.values())

    def _for_requester(self,actor,requirements):
        copied=[]
        with self.kb._memory_write_lock:
            for ref in requirements:
                ref=dict(ref)
                if ref.get('kind')=='personal':
                    m=self.kb._find_memory(ref.get('id'))
                    if not m:return None
                    grant=self.access.authorize_disclosure(actor,m['id'],policy_content_revision(m),sensitivity=ref['grant']['sensitivity'])
                    if grant is None or grant.policy_revision!=ref['grant']['policy_revision']:return None
                    ref['grant']=asdict(grant)
                elif ref.get('kind')=='shared_event':
                    from types import SimpleNamespace
                    message=SimpleNamespace(bot_id=actor.bot_id,user_id=actor.user_id,group_id=actor.group_id)
                    snapshot=self.bridge.episodes.unified.for_requester(message,ref.get('snapshot',{}))
                    if snapshot is None:return None
                    ref['snapshot']=snapshot
                copied.append(ref)
        return copied if self._valid_requirements(actor,copied) else None

    def snapshot(self,message):
        actor=self.actor(message)
        if not actor.accepted():raise GroupDenied('group_not_authorized')
        self.prune()
        state=self.policy(actor.group_id);v=state['values']
        if not self.bridge.settings.task_control_enabled:return None
        if not v['context_enabled'] and not v['memory_enabled']:return None
        state=self._topic(actor,state)
        requirements=[];history=[];facts=[];budget=0
        if v['context_enabled']:
            with self.store.connect() as db:
                rows=db.execute('select * from qq_group_turns where bot=? and grp=? and topic=? and at>? order by id desc limit 24',
                    (actor.bot_id,actor.group_id,state['topic'],self.clock()-v['retention_seconds'])).fetchall()
            for row in reversed(rows):
                if row['role']=='assistant':
                    from .api_failures import operational_reply
                    if operational_reply(row['text']):
                        with self.store.connect() as db:
                            delivered=db.execute("select 1 from qq_deliveries where scope like ? and canonical=? and kind='final' limit 1",
                                (actor.bot_id+':group:'+actor.group_id+':%',row['text'])).fetchone()
                        if delivered:continue
                refs=self._for_requester(actor,json.loads(row['requirements']))
                if refs is None or len(self._unique(requirements+refs))>56:continue
                text=row['text'][:3000]
                if budget+len(text)>10000:continue
                entry={'speaker_id':row['speaker'],'display_name':row['label'],'role':row['role'],'text':text,'at':row['at']}
                provenance=getattr(getattr(self.bridge,'episodes',None),'provenance',None)
                if provenance and provenance.active():
                    tags=[x['tag'] for x in refs if x.get('kind')=='event_dependencies']
                    tag=provenance.merge(message,*tags) if tags else provenance.history_tag(message,entry)
                    if tag is None:continue
                    entry.update(qq_event_dependencies=tag,_qq_event_validated=bool(tags))
                history.append(entry)
                requirements=self._unique(requirements+refs);budget+=len(text)
        if v['memory_enabled']:
            with self.store.connect() as db:
                rows=db.execute('select * from qq_group_facts where bot=? and grp=? and active=1 and expires>? order by created desc limit 200',
                    (actor.bot_id,actor.group_id,self.clock())).fetchall()
                ids=[r[0] for r in db.execute('select memory_id from qq_memory_access where bot_id=? order by memory_id limit 200',(actor.bot_id,))]
            for row in rows:
                if not self._matches(message.text,row['content']):continue
                if budget+len(row['content'])>16000:break
                facts.append({'id':row['id'],'scope':'current_group','source_speaker_id':row['subject'],'subject_scope':'group','text':row['content']})
                requirements.append({'kind':'group','id':row['id'],'version':record_version(dict(row))});budget+=len(row['content'])
                if len(facts)>=8:break
            with self.kb._memory_write_lock:
                for mid in ids:
                    if len(facts)>=8:break
                    m=self.kb._find_memory(mid)
                    if not m or not self.kb.memory_allowed_for_use(m):continue
                    sensitivity=m.get('sensitivity') or ('secret' if m.get('source')=='credentials' else 'normal')
                    grant=self.access.authorize_disclosure(actor,mid,policy_content_revision(m),sensitivity=sensitivity)
                    if grant is None or not self._matches(message.text,str(m.get('title',''))+' '+str(m.get('content',''))):continue
                    text=str(m.get('content') or '')[:4000]
                    if budget+len(text)>16000:continue
                    policy=self.access.get(actor.bot_id,mid)
                    facts.append({'id':mid,'scope':'individually_authorized','subject_id':policy.subject_id,'text':text})
                    requirements.append({'kind':'personal','id':mid,'version':record_version(m),'grant':asdict(grant)});budget+=len(text)
        requirements=self._unique(requirements)
        selection=getattr(self.bridge,'context_salience',None)
        if selection and getattr(message,'context_snapshot',{}).get('enabled'):
            history=selection.select_rows(message.context_snapshot['scope'],message.context_snapshot,history,message.text)
        result={'bot':actor.bot_id,'group':actor.group_id,'user':actor.user_id,'revision':state['revision'],
            'topic':state['topic'],'expires':self.clock()+min(v['retention_seconds'],v['topic_idle_seconds']),
            'requirements':requirements,'history':history,'facts':facts,'context_enabled':v['context_enabled']}
        if not self.valid(message,result):raise GroupConflict('group_context_changed')
        return result

    def valid(self,message,snapshot):
        actor=self.actor(message)
        if not actor.accepted() or not snapshot or not self.bridge.settings.task_control_enabled:return False
        if (snapshot.get('bot'),snapshot.get('group'),snapshot.get('user'))!=(actor.bot_id,actor.group_id,actor.user_id):return False
        state=self.policy(actor.group_id)
        return (snapshot.get('revision')==state['revision'] and snapshot.get('topic')==state['topic']
            and snapshot.get('expires',0)>self.clock()
            and self._valid_requirements(actor,snapshot.get('requirements',[])))

    def bind_result(self,message,result_id,snapshot):
        if snapshot is None:return
        if not self.valid(message,snapshot):raise GroupConflict('group_context_changed')
        compact={k:v for k,v in snapshot.items() if k not in {'history','facts'}}
        with self.store.connect() as db:db.execute('insert or replace into qq_group_results values(?,?)',(result_id,json.dumps(compact)))

    def bind_event(self,message,snapshot):
        if snapshot is None:return
        if not self.valid(message,snapshot):raise GroupConflict('group_context_changed')
        compact={k:v for k,v in snapshot.items() if k not in {'history','facts'}}
        with self.store.connect() as db:db.execute('insert or replace into qq_group_events values(?,?)',(message.key,json.dumps(compact)))

    def valid_delivery(self,message,result_id):
        if not message.group_id:return True
        with self.store.connect() as db:
            row=db.execute('select snapshot from qq_group_results where result_id=?',(result_id,)).fetchone()
            delivery=db.execute('select kind from qq_deliveries where id=?',(result_id,)).fetchone()
            if row is None:
                row=db.execute('select e.snapshot from qq_group_events e join qq_deliveries d on d.event_key=e.event_key where d.id=?',(result_id,)).fetchone()
        if delivery and delivery[0]!='final':return self.actor(message).accepted()
        if row:return self.valid(message,json.loads(row[0]))
        v=self.policy(message.group_id)['values']
        return self.actor(message).accepted() and not (v['context_enabled'] or v['memory_enabled'])

    def prepare_delivery_scope(self,message,scope):
        if not message.group_id:return
        from . import qq_delivery
        with self.store.connect() as db:
            ids=[row[0] for row in db.execute("select id from qq_deliveries where scope=? and kind='final' order by created desc limit 16",(scope,))]
        for result_id in ids:
            if not self.valid_delivery(message,result_id):qq_delivery.freeze(self.store,scope,result_id)

    def protect_quote(self,message):
        quote=getattr(message,'quote_context',None)
        if not message.group_id or not quote or not message.reply_to:return
        with self.store.connect() as db:
            rows=db.execute('select r.snapshot from qq_group_results r join qq_delivery_parts p on p.result_id=r.result_id where p.receipt=? limit 4',(message.reply_to,)).fetchall()
            strict=db.execute('select since from qq_group_privacy_guard where bot=? and grp=?',(message.bot_id,message.group_id)).fetchone()
        if not rows and quote.get('sender_id')==message.bot_id and strict and not (0<quote.get('sent_at',0)<strict[0]):
            message.quote_context={'status':'unavailable','reason':'reference_provenance_expired'}
            return
        for row in rows:
            source=json.loads(row[0]);source['user']=message.user_id
            refs=self._for_requester(self.actor(message),source.get('requirements',[]))
            source['requirements']=refs
            if refs is None or not self.valid(message,source):
                message.quote_context={'status':'unavailable','reason':'reference_permission_changed'}
                return

    def record_turn(self,message,snapshot,answer):
        if snapshot is None or not snapshot['context_enabled']:return
        if not self.valid(message,snapshot):raise GroupConflict('group_context_changed')
        now=self.clock()
        with self.store.connect() as db:
            db.execute('begin immediate')
            for role,speaker,label,text,requirements in [
                ('user',message.user_id,getattr(message,'sender_name',''),message.text,[]),
                ('assistant',message.bot_id,'',answer,snapshot['requirements'])]:
                tag=getattr(message,'event_provenance_user' if role=='user' else 'event_provenance_response',None)
                if tag is not None:
                    requirements=[x for x in requirements if x.get('kind') not in {'episode','shared_event','event_dependencies'}]
                    requirements.append({'kind':'event_dependencies','tag':tag})
                db.execute('insert into qq_group_turns(bot,grp,topic,speaker,label,role,text,at,requirements) values(?,?,?,?,?,?,?,?,?)',
                    (message.bot_id,message.group_id,snapshot['topic'],speaker,label[:80],role,str(text)[:6000],now,json.dumps(requirements)))
            db.execute('update qq_group_policies set activity=? where bot=? and grp=? and topic=?',
                (now,message.bot_id,message.group_id,snapshot['topic']))
            db.execute('delete from qq_group_turns where bot=? and grp=? and (at<? or id not in (select id from qq_group_turns where bot=? and grp=? order by id desc limit 48))',
                (message.bot_id,message.group_id,now-self.policy(message.group_id)['values']['retention_seconds'],message.bot_id,message.group_id))

    async def _writer(self,message,policy):
        actor=self.actor(message)
        if not actor.accepted() or not actor.write_enabled or not policy['values']['write_enabled']:return False
        if actor.is_owner:return 'owner'
        if actor.user_id in policy['values']['writer_ids']:return 'writer'
        if not policy['values']['admin_writes']:return False
        try:
            member=await asyncio.wait_for(self.bridge.client.call('get_group_member_info',
                {'group_id':int(actor.group_id),'user_id':int(actor.user_id),'no_cache':True}),3)
        except Exception:return False
        checkpoint_current()
        accepted=(isinstance(member,dict) and str(member.get('group_id'))==actor.group_id
            and str(member.get('user_id'))==actor.user_id and member.get('role') in {'admin','owner'}
            and self.actor(message).accepted() and self.policy(actor.group_id)['revision']==policy['revision'])
        return 'admin' if accepted else False

    async def command(self,message):
        name,_,argument=message.text.strip().partition(' ')
        if name not in {'/gcontext','/gnew','/gmemories','/gremember','/gforget','/gconfirm'}:return None
        actor=self.actor(message)
        if not actor.accepted():return GroupReceipt('当前群未授权，未执行。')
        self.prune()
        state=self.policy(actor.group_id);v=state['values']
        if name=='/gcontext':
            return GroupReceipt(f"群公共语境：{'开启' if v['context_enabled'] else '关闭'}；群记忆：{'开启' if v['memory_enabled'] else '关闭'}；群写入：{'开启' if v['write_enabled'] else '只读'}。私聊/网页完整历史不会导入群。")
        if not self.bridge.settings.task_control_enabled:return GroupReceipt('任务控制已关闭，本群公共语境与群记忆功能暂停；原文字聊天保持。')
        if not v['memory_enabled'] and name!='/gnew':return GroupReceipt('本群记忆未开启，请由主人在本机QQ群设置明确开启。')
        if name=='/gmemories':
            with self.store.connect() as db:
                rows=db.execute('select id,subject,content from qq_group_facts where bot=? and grp=? and active=1 and expires>? order by created desc limit 10',(actor.bot_id,actor.group_id,self.clock())).fetchall()
            return GroupReceipt('本群已保存记忆：\n'+('\n'.join(f"{r[0]} · QQ {r[1]} · {r[2][:100]}" for r in rows) or '暂无。'))
        writer=await self._writer(message,state)
        if not writer:return GroupReceipt('当前无群记忆写入权限或保存已关闭，未执行。')
        checkpoint_current()
        if name=='/gnew':
            if writer=='writer':return GroupReceipt('开始全群新话题仅供主人或经核验管理员操作，未执行。')
            if not v['context_enabled']:return GroupReceipt('公共语境未开启，原按发言人隔离模式保持。')
            with effect_current('memory') as effect,self.store.connect() as db:
                db.execute('begin immediate')
                current=db.execute('select revision from qq_group_policies where bot=? and grp=?',(actor.bot_id,actor.group_id)).fetchone()
                if not current or current[0]!=state['revision']:raise GroupConflict('group_policy_changed')
                db.execute('update qq_group_policies set topic=?,activity=? where bot=? and grp=?',
                    (uuid4().hex,self.clock(),actor.bot_id,actor.group_id))
                effect.committed()
            return GroupReceipt('已开始新的群话题，旧话题不会继续引用。')
        if name=='/gremember':
            content=argument.strip()
            if not content or len(content)>4000:return GroupReceipt('请在 /gremember 后填写1～4000字的本群内容。')
            from .memory_v4.models import _contains_secret
            if _contains_secret(content):return GroupReceipt('这段内容可能含凭据或秘密，未保存为群记忆。')
            with effect_current('memory') as effect,self.store.connect() as db:
                db.execute('begin immediate')
                if self.policy(actor.group_id)['revision']!=state['revision'] or not self.actor(message).accepted() or not self.actor(message).write_enabled:raise GroupConflict('group_policy_changed')
                if db.execute('select count(*) from qq_group_facts where bot=? and grp=? and active=1 and expires>?',(actor.bot_id,actor.group_id,self.clock())).fetchone()[0]>=200:
                    return GroupReceipt('本群有效记忆已达200条，请先明确清理。')
                existing=db.execute('select id from qq_group_facts where bot=? and grp=? and source_event=?',(actor.bot_id,actor.group_id,message.key)).fetchone()
                mid=existing[0] if existing else 'gm-'+uuid4().hex
                if not existing:db.execute('insert into qq_group_facts values(?,?,?,?,?,?,?,?,1)',
                    (mid,actor.bot_id,actor.group_id,actor.user_id,content,message.key,self.clock(),self.clock()+v['memory_retention_days']*86400))
                effect.committed()
            return GroupReceipt('已保存本群记忆：'+mid+'；仅在本群使用，未写入私人长期记忆。')
        if name=='/gforget':
            with self.store.connect() as db:
                row=db.execute('select * from qq_group_facts where id=? and bot=? and grp=? and active=1',(argument.strip(),actor.bot_id,actor.group_id)).fetchone()
                if not row:return GroupReceipt('未找到本群可删除条目。')
                if writer=='writer' and row['subject']!=actor.user_id:return GroupReceipt('只能管理自己在本群提交的条目，未执行。')
                token=uuid4().hex;digest=hashlib.sha256(token.encode()).hexdigest()
                db.execute('insert into qq_group_delete_tokens values(?,?,?,?,?,?,?,?,?)',
                    (digest,actor.bot_id,actor.group_id,actor.user_id,row['id'],record_version(dict(row)),state['revision'],self.clock()+600,'pending'))
            return GroupReceipt('请核对删除本群条目：'+row['content'][:120]+'\n十分钟内确认：/gconfirm '+token)
        digest=hashlib.sha256(argument.strip().encode()).hexdigest()
        with effect_current('memory') as effect,self.store.connect() as db:
            db.execute('begin immediate')
            token=db.execute('select * from qq_group_delete_tokens where token=?',(digest,)).fetchone()
            if not token or (token['bot'],token['grp'],token['actor'])!=(actor.bot_id,actor.group_id,actor.user_id) or token['expires']<=self.clock() or token['state']!='pending' or token['policy_revision']!=state['revision']:
                return GroupReceipt('确认已失效、已处理或不属于当前账号/群，未执行。')
            row=db.execute('select * from qq_group_facts where id=? and bot=? and grp=? and active=1',(token['target'],actor.bot_id,actor.group_id)).fetchone()
            if not row or record_version(dict(row))!=token['version']:return GroupReceipt('条目已变化，请重新核对删除。')
            if writer=='writer' and row['subject']!=actor.user_id:return GroupReceipt('只能管理自己在本群提交的条目，未执行。')
            if self.policy(actor.group_id)['revision']!=state['revision'] or not self.actor(message).accepted() or not self.actor(message).write_enabled:raise GroupConflict('group_policy_changed')
            db.execute("update qq_group_facts set active=0,content='' where id=?",(token['target'],))
            db.execute("update qq_group_delete_tokens set state='committed' where token=?",(digest,));effect.committed()
        return GroupReceipt('已删除本群记忆；未删除私人记忆或原始聊天。')
