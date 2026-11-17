"""Opt-in prompt-only chat-day/topic selection; original archives stay intact."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import time
from types import SimpleNamespace
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .qq_responses import ControlReceipt


class ContextPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled:bool=False
    timezone:str='Asia/Shanghai'
    history_token_budget:int=Field(default=6000,ge=100,le=32000)
    old_fraction:float=Field(default=0.2,ge=0,le=0.4)

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls,value):
        try:ZoneInfo(value)
        except Exception:raise ValueError('invalid_timezone') from None
        return value


def chat_day(at,zone):
    local=datetime.fromtimestamp(at,ZoneInfo(zone))
    return (local.date()-timedelta(days=1) if local.hour<4 else local.date()).isoformat()


def topic_request(text):
    text=text.strip()
    command=re.fullmatch(r'/(?:topic\s+new|换话题)(?:\s+(.*))?',text,re.S)
    if command:return (command.group(1) or '').strip()
    match=re.fullmatch(r'(?:我们|咱们)?(?:换个话题(?:吧)?|聊点别的(?:吧)?|前面不谈了)(?:[。！!]|[，,；;]\s*(.*))?',text,re.S)
    return (match.group(1) or '').strip() if match else None


def _timestamp(value):
    from .chat_time_context import parse_time
    parsed=parse_time(value)
    return parsed.timestamp() if parsed else None


class QQContextSalience:
    def __init__(self,bridge,*,clock=time.time):
        self.bridge,self.store,self.clock=bridge,bridge.store,clock
        with self.store.connect() as db:
            db.executescript('''create table if not exists qq_context_policy(bot text primary key,revision integer,policy text);
                create table if not exists qq_context_topics(scope text primary key,generation integer,switched real);
                create table if not exists qq_context_switches(event text primary key,scope text,generation integer,at real);
                create table if not exists qq_context_turns(id integer primary key,scope text,session text,digest text,
                    generation integer,at real,created real);
                create index if not exists qq_context_turn_scope on qq_context_turns(scope,session,id);''')
            db.execute('create table if not exists qq_context_turn_sources(id integer primary key,snapshot text)')
        self.prune()

    def prune(self):
        with self.store.connect() as db:
            for table in ['qq_context_switches','qq_context_turns']:
                field='at' if table.endswith('switches') else 'created'
                extra=' and id not in (select id from qq_context_turn_sources)' if table.endswith('turns') else ''
                db.execute('delete from '+table+' where '+field+'<?'+extra,(self.clock()-7*86400,))
            for row in db.execute('select t.id,t.scope,t.session,t.digest from qq_context_turns t join qq_context_turn_sources s on s.id=t.id').fetchall():
                history,session=self.store.conversation(row['scope'])
                if session!=row['session'] or not any(hashlib.sha256(x.encode()).hexdigest()==row['digest'] for x in history):
                    db.execute('delete from qq_context_turn_sources where id=?',(row['id'],))

    def state(self):
        with self.store.connect() as db:row=db.execute('select revision,policy from qq_context_policy where bot=?',(self.bridge.settings.bot_id,)).fetchone()
        return {'revision':row[0] if row else 0,'values':ContextPolicy.model_validate(json.loads(row[1]) if row else {}).model_dump(),
            'owners':sorted(self.bridge.settings.owner_ids)}

    def configure(self,owner,revision,values):
        if owner not in self.bridge.settings.owner_ids:raise PermissionError('owner_required')
        policy=ContextPolicy.model_validate(values).model_dump()
        with self.store.connect() as db:
            db.execute('begin immediate')
            old=db.execute('select revision from qq_context_policy where bot=?',(self.bridge.settings.bot_id,)).fetchone()
            if type(revision) is not int or (old[0] if old else 0)!=revision:raise ValueError('context_policy_changed')
            db.execute('insert or replace into qq_context_policy values(?,?,?)',(self.bridge.settings.bot_id,revision+1,json.dumps(policy)))
        return self.state()

    def snapshot(self,scope):
        state=self.state()
        with self.store.connect() as db:row=db.execute('select generation,switched from qq_context_topics where scope=?',(scope,)).fetchone()
        return {'scope':scope,'policy_revision':state['revision'],'enabled':state['values']['enabled'],
            'timezone':state['values']['timezone'],'day':chat_day(self.clock(),state['values']['timezone']),
            'generation':row[0] if row else 0,'switched':row[1] if row else 0}

    def valid(self,snapshot):
        return snapshot==self.snapshot(snapshot['scope'])

    def switch(self,scope,event):
        if not re.fullmatch(r'[1-9]\d{0,19}:(?:private:[1-9]\d{0,19}|group:[1-9]\d{0,19}:user:[1-9]\d{0,19}):(?:shared|isolated)',scope):raise ValueError('invalid_scope')
        with self.store.connect() as db:
            db.execute('begin immediate')
            duplicate=db.execute('select scope from qq_context_switches where event=?',(event,)).fetchone()
            if duplicate:
                if duplicate[0]!=scope:raise ValueError('event_scope_mismatch')
            else:
                row=db.execute('select generation from qq_context_topics where scope=?',(scope,)).fetchone();generation=(row[0] if row else 0)+1
                db.execute('insert or replace into qq_context_topics values(?,?,?)',(scope,generation,self.clock()))
                db.execute('insert into qq_context_switches values(?,?,?,?)',(event,scope,generation,self.clock()))
                if db.execute("select 1 from sqlite_master where name='qq_action_proposals'").fetchone():
                    db.execute("update qq_action_proposals set state='superseded' where scope=? and state in ('prepared','pending')",(scope,))
                db.execute("insert or ignore into qq_delivery_stops select id,'topic_changed' from qq_deliveries where scope=? and kind='final'",(scope,))
        return self.snapshot(scope)

    @staticmethod
    def tokens(text):
        text=str(text or '');cjk=len(re.findall(r'[\u3400-\u9fff]',text));other=re.sub(r'[\u3400-\u9fff]','',text)
        return max(1,cjk+int(len(other)/4)+int(len(re.findall(r'[A-Za-z0-9_]+|[^\sA-Za-z0-9_]',other))*.25))

    def select_rows(self,scope,snapshot,rows,query='',*,budget=None,old_budget=None):
        policy=self.state()['values']
        if not snapshot['enabled']:return list(rows)
        budget=min(budget or policy['history_token_budget'],policy['history_token_budget'])
        old_cap=int(budget*policy['old_fraction']);current=[];old=[]
        if old_budget is not None:old_cap=min(old_cap,max(0,old_budget))
        query_words=set(re.findall(r'[a-zA-Z]{3,}|[\u3400-\u9fff]{2}',query))
        explicit=bool(re.search(r'昨天|之前|刚才|那件|先前|上次|前面|记得',query))
        for index,row in enumerate(rows):
            if row.get('context_eligible') is False or row.get('content_kind') in {'control_receipt','action_proposal'}:continue
            text=str(row.get('content',row.get('text','')) or '');at=_timestamp(row.get('time',row.get('at')))
            meta=row.get('qq_context') or {}
            day=chat_day(at,snapshot['timezone']) if at else None
            generation=meta.get('generation')
            is_old=(day!=snapshot['day'] or generation is not None and generation!=snapshot['generation']
                or snapshot['switched'] and generation is None and (at is None or at<snapshot['switched']))
            cost=self.tokens(text)
            if not is_old:current.append((index,row,cost))
            elif explicit or any(w in text for w in query_words):
                age=max(0,(datetime.fromisoformat(snapshot['day'])-datetime.fromisoformat(day)).days) if day else 3
                score=.3**min(age,3)*(.2 if generation is not None and generation!=snapshot['generation'] else 1)
                old.append((score,index,row,cost))
        chosen=[];used=0
        for index,row,cost in reversed(current):
            if used+cost<=budget:chosen.append((index,row));used+=cost
        old_used=0
        for _,index,row,cost in sorted(old,key=lambda x:(x[0],x[1]),reverse=True):
            if old_used+cost<=old_cap and used+cost<=budget:chosen.append((index,row));used+=cost;old_used+=cost
        return [row for _,row in sorted(chosen,key=lambda x:x[0])]

    def project(self,scope,session,history,rows=(),query='',snapshot=None,budget=None,old_budget=None):
        snapshot=snapshot or self.snapshot(scope)
        provenance=getattr(getattr(self.bridge,'episodes',None),'provenance',None)
        provenance=provenance if provenance and provenance.active() else None
        parts=scope.split(':')
        actor=SimpleNamespace(bot_id=parts[0],user_id=parts[2] if parts[1]=='private' else parts[4],
            group_id=parts[2] if parts[1]=='group' else '',scope=scope.rsplit(':',1)[0],shared=scope.endswith(':shared'))
        supplied={}
        for row in reversed(list(rows)):
            role='Doctor' if row.get('role')=='user' else 'Saya' if row.get('role') in {'ai','assistant'} else ''
            if role:supplied.setdefault(role+': '+str(row.get('content') or ''),[]).append(row)
        clocks=self.bridge.turn_times.history(scope,session,history) if self.bridge.turn_times else []
        with self.store.connect() as db:meta=db.execute('''select t.digest,t.generation,t.at,s.snapshot from qq_context_turns t
            left join qq_context_turn_sources s on s.id=t.id where t.scope=? and t.session=? order by t.id desc limit 1000''',(scope,session)).fetchall()
        dependency_buckets={}
        if provenance:
            with self.store.connect() as db:
                for r in db.execute('select t.digest,p.tag from qq_context_turns t left join qq_event_turn_dependencies p on p.id=t.id '
                    'where t.scope=? and t.session=? order by t.id desc limit 1000',(scope,session)):
                    dependency_buckets.setdefault(r['digest'],[]).append(r['tag'])
        buckets={}
        for row in meta:buckets.setdefault(row['digest'],[]).append(row)
        projected=[];dependency_cache={}
        for reverse_index,line in enumerate(reversed(history)):
            role,_,text=line.partition(': ');options=supplied.get(line,[]);row=dict(options.pop(0)) if options else {'role':'user' if role=='Doctor' else 'ai','content':text}
            available=buckets.get(hashlib.sha256(line.encode()).hexdigest(),[])
            source=row.get('qq_episode');from_archive='qq_context' in row or 'chat_runtime' in row
            if available:
                item=available.pop(0);row['qq_context']={'generation':item['generation']};row['time']=item['at']
                if item['snapshot'] and not from_archive:source=json.loads(item['snapshot'])
            elif not row.get('time') and reverse_index<len(clocks):row['time']=clocks[-1-reverse_index].get('time')
            if provenance:
                labels=dependency_buckets.get(hashlib.sha256(line.encode()).hexdigest(),[])
                side=labels.pop(0) if labels else None
                known=bool(side) or row.get('qq_event_dependencies') is not None and row.get('_qq_event_validated',True)
                tag=provenance.history_tag(actor,row,json.loads(side) if side else None,dependency_cache)
                if tag is None:continue
                row['qq_event_dependencies']=tag;row['_qq_event_validated']=known
            elif source:
                if not self.bridge.episodes or not self.bridge.episodes.valid(actor,source,history=True):continue
            row['_line']=line;projected.append(row)
        selected=self.select_rows(scope,snapshot,list(reversed(projected)),query,budget=budget,old_budget=old_budget)
        return [row['_line'] for row in selected],[{k:v for k,v in row.items() if k!='_line'} for row in selected]

    def record(self,scope,session,lines,times,snapshot,episode_source=None,event_dependencies=None,user_dependencies=None):
        if not snapshot['enabled'] and event_dependencies is None and user_dependencies is None:
            with self.store.connect() as db:
                if not db.execute('select 1 from qq_context_turn_sources s join qq_context_turns t on t.id=s.id where t.scope=? limit 1',(scope,)).fetchone():return
        with self.store.connect() as db:
            for line,at in zip(lines,times):
                row=db.execute('insert into qq_context_turns(scope,session,digest,generation,at,created) values(?,?,?,?,?,?)',
                    (scope,session,hashlib.sha256(line.encode()).hexdigest(),snapshot['generation'],_timestamp(at),self.clock()))
                if episode_source and line.startswith('Saya: '):
                    db.execute('insert into qq_context_turn_sources values(?,?)',(row.lastrowid,json.dumps(episode_source)))
                tag=event_dependencies if line.startswith('Saya: ') else user_dependencies
                if tag is not None:db.execute('insert into qq_event_turn_dependencies values(?,?)',(row.lastrowid,json.dumps(tag,ensure_ascii=False)))
        self.prune()

    def command(self,message):
        args=message.text.split();scope=message.scope+(':shared' if message.shared else ':isolated')
        if len(args)==1 or args[1]=='status':
            state=self.state();snap=self.snapshot(scope)
            return ControlReceipt(f"话题降权：{'开启' if state['values']['enabled'] else '关闭'}；时区{snap['timezone']}，04:00分日；当前聊天日{snap['day']}，话题代次{snap['generation']}。/topic on/off；/topic timezone 时区；/topic new [新话题]。")
        if args[1] in {'on','off','timezone'}:
            if message.group_id or message.user_id not in self.bridge.settings.owner_ids:return ControlReceipt('仅主人好友私聊可修改话题策略。')
            state=self.state();values=state['values']
            if args[1]=='timezone':
                if len(args)!=3:return ControlReceipt('用法：/topic timezone Asia/Shanghai')
                values['timezone']=args[2]
            elif len(args)==2:values['enabled']=args[1]=='on'
            else:return ControlReceipt('用法：/topic on 或 /topic off')
            try:self.configure(message.user_id,state['revision'],values)
            except (ValueError,KeyError):return ControlReceipt('时区或参数无效，本次未修改。请使用有效IANA时区。')
            return ControlReceipt('话题策略已更新；原历史、长期记忆和人物设定保留。')
        return ControlReceipt('用法：/topic status、/topic new [新话题]、/topic on/off、/topic timezone 时区。')
