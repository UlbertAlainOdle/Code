"""Shared durable intents for web/QQ initiative; drafts are not deliveries."""
import hashlib,json,sqlite3,time
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
from pydantic import BaseModel,ConfigDict,Field,StrictBool,field_validator


class ProactivePolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:StrictBool=False
    owner_id:str=''
    source_context:StrictBool=True
    source_projects:StrictBool=False
    source_dates:StrictBool=False
    source_dream:StrictBool=False
    daily_limit:int=Field(1,ge=0,le=10)
    min_gap_minutes:int=Field(60,ge=0,le=1440)
    min_idle_minutes:int=Field(30,ge=1,le=1440)
    quiet_start:int=Field(23,ge=0,le=23)
    quiet_end:int=Field(8,ge=0,le=23)
    timezone:str='America/New_York'

    @field_validator('timezone')
    @classmethod
    def zone(cls,value):
        try:ZoneInfo(value)
        except Exception:raise ValueError('invalid timezone') from None
        return value

    @field_validator('owner_id')
    @classmethod
    def owner(cls,value):
        import re
        if value and not re.fullmatch('[1-9][0-9]{0,19}',value):raise ValueError('invalid owner')
        return value


def fingerprint(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def quiet(now,policy):
    hour=datetime.fromtimestamp(now,ZoneInfo(policy.timezone)).hour
    a,b=policy.quiet_start,policy.quiet_end
    return False if a==b else a<=hour<b if a<b else hour>=a or hour<b


class ProactiveStore:
    def __init__(self,path,clock=time.time):
        self.path,self.clock=Path(path),clock;self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''create table if not exists initiative_policy(scope text primary key,revision integer,policy text);
                create table if not exists initiative_activity(principal text primary key,at real);
                create table if not exists initiative_jobs(id text primary key,principal text,channel text,session text,topic text,
                    day text,created real,expires real,state text,body text,guard text,token text,delivered real default 0);
                create index if not exists initiative_principal on initiative_jobs(principal,created);
                create table if not exists initiative_meta(key text primary key,value text);''')
            # An interrupted transport may have reached its recipient. Never replay it.
            db.execute("update initiative_jobs set state='unknown',body='' where state in ('queued','sending','committed')")
            db.execute("update initiative_jobs set state='interrupted',body='' where state in ('generating','ready')")
        self.prune()

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def policy(self,scope):
        with self.connect() as db:row=db.execute('select * from initiative_policy where scope=?',(scope,)).fetchone()
        return {'revision':row['revision'] if row else 0,'values':ProactivePolicy.model_validate(json.loads(row['policy']) if row else {}).model_dump()}

    def configure(self,scope,revision,values):
        p=ProactivePolicy.model_validate(values)
        with self.connect() as db:
            db.execute('begin immediate');row=db.execute('select revision from initiative_policy where scope=?',(scope,)).fetchone()
            if (row[0] if row else 0)!=revision:raise ValueError('policy_changed')
            db.execute('insert or replace into initiative_policy values(?,?,?)',(scope,revision+1,p.model_dump_json()))
        return self.policy(scope)

    def activity(self,principal,at=None):
        at=self.clock() if at is None else at
        with self.connect() as db:
            db.execute('insert into initiative_activity values(?,?) on conflict(principal) do update set at=max(at,excluded.at)',(principal,at))
            db.execute("update initiative_jobs set state='cancelled',body='' where principal=? and state in ('generating','ready','queued')",(principal,))

    def last_activity(self,principal):
        with self.connect() as db:row=db.execute('select at from initiative_activity where principal=?',(principal,)).fetchone()
        return row[0] if row else 0

    def eligible(self,principal,policy,last_incoming=0):
        now=self.clock();day=datetime.fromtimestamp(now,ZoneInfo(policy.timezone)).date().isoformat()
        if not policy.enabled:return 'disabled'
        if quiet(now,policy):return 'quiet_hours'
        incoming=max(last_incoming,self.last_activity(principal))
        if not incoming:return 'no_interaction'
        if now-incoming<policy.min_idle_minutes*60:return 'recent_interaction'
        with self.connect() as db:
            rows=db.execute('select state,created,delivered,day from initiative_jobs where principal=? order by created desc',(principal,)).fetchall()
            legacy=db.execute("select value from initiative_meta where key='legacy_echo'").fetchone()
        if any(x['state'] in {'generating','ready','queued','sending','committed'} for x in rows):return 'pending'
        if any(x['state'] in {'sent','unknown'} and max(x['delivered'],x['created'])>incoming for x in rows):return 'awaiting_reply'
        legacy=json.loads(legacy[0]) if legacy else {}
        carried=int(legacy.get('sent_today',0)) if legacy.get('day')==day else 0
        uncertain=sum(x['state']=='unknown' and x['day']==day for x in rows)
        if sum(x['state']=='sent' and x['day']==day for x in rows)+uncertain+carried>=policy.daily_limit:return 'delivery_uncertain' if uncertain else 'daily_limit'
        if rows and now-rows[0]['created']<policy.min_gap_minutes*60:return 'cooldown'
        return 'eligible'

    def reserve(self,principal,channel,session,topic,policy,guard):
        now=self.clock();day=datetime.fromtimestamp(now,ZoneInfo(policy.timezone)).date().isoformat()
        with self.connect() as db:
            db.execute('begin immediate')
            if db.execute('select 1 from initiative_jobs where principal=? and topic=? and day=?',(principal,topic,day)).fetchone():return None
            if db.execute("select 1 from initiative_jobs where principal=? and state in ('generating','ready','queued','sending','committed')",(principal,)).fetchone():return None
            identifier=uuid4().hex;token=uuid4().hex
            db.execute('insert into initiative_jobs values(?,?,?,?,?,?,?,?,?,?,?,?,0)',(identifier,principal,channel,session,topic,day,now,now+300,'generating','',json.dumps(guard),token))
        return self.get(identifier)

    def get(self,identifier):
        with self.connect() as db:row=db.execute('select * from initiative_jobs where id=?',(identifier,)).fetchone()
        return dict(row) if row else None

    def transition(self,identifier,expected,state,body=None):
        with self.connect() as db:
            query='update initiative_jobs set state=?'+(',body=?' if body is not None else '')+" where id=? and state=?"
            return bool(db.execute(query,([state,body,identifier,expected] if body is not None else [state,identifier,expected])).rowcount)

    def ready(self,identifier,topic,body):
        with self.connect() as db:
            db.execute('begin immediate');job=db.execute("select * from initiative_jobs where id=? and state='generating'",(identifier,)).fetchone()
            if not job:return False
            duplicate=db.execute("select 1 from initiative_jobs where principal=? and topic=? and day=? and id!=? and state in ('ready','queued','sending','committed','sent','unknown')",(job['principal'],topic,job['day'],identifier)).fetchone()
            if duplicate:db.execute("update initiative_jobs set state='silent',body='' where id=?",(identifier,));return False
            db.execute("update initiative_jobs set topic=?,body=?,state='ready' where id=?",(topic,body,identifier));return True

    def sent(self,identifier):
        with self.connect() as db:
            return bool(db.execute("update initiative_jobs set state='sent',body='',delivered=? where id=? and state in ('sending','committed')",(self.clock(),identifier)).rowcount)

    def prune(self):
        now=self.clock()
        with self.connect() as db:
            db.execute("update initiative_jobs set state=case when state='committed' then 'unknown' else 'expired' end,body='' where expires<=? and state in ('generating','ready','queued','committed')",(now,))
            db.execute('delete from initiative_jobs where created<?',(now-14*86400,))
            db.execute('delete from initiative_activity where at<?',(now-30*86400,))

    def summary(self):
        with self.connect() as db:rows=db.execute('select channel,state,count(*) from initiative_jobs group by channel,state').fetchall()
        return [{'channel':x[0],'state':x[1],'count':x[2]} for x in rows]
