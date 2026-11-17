"""Trusted per-turn clock facts; no persona or stored history rewrites."""
from datetime import datetime,timezone,timedelta
import json
import hashlib
import time

SHANGHAI=timezone(timedelta(hours=8),'Asia/Shanghai')


def parse_time(value):
    try:
        if isinstance(value,(int,float)) and not isinstance(value,bool):return datetime.fromtimestamp(value,SHANGHAI) if value>0 else None
        if isinstance(value,datetime):parsed=value
        elif isinstance(value,str) and value.strip():parsed=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
        else:return None
        return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)
    except (ValueError,TypeError,OverflowError,OSError):return None


def clock_context(*,message_at=None,previous_at=None,history=None,now=None):
    current=parse_time(now) or datetime.now(SHANGHAI)
    sent=parse_time(message_at)
    previous=parse_time(previous_at)
    elapsed=(sent or current)-previous if previous else None
    gap=round(elapsed.total_seconds()) if elapsed and elapsed.total_seconds()>=0 else (0 if elapsed is not None and elapsed.total_seconds()==0 else None)
    data={'timezone':'Asia/Shanghai','current_time':current.isoformat(timespec='seconds'),
          'message_sent_at':sent.isoformat(timespec='seconds') if sent else None,
          'previous_conversation_at':previous.isoformat(timespec='seconds') if previous else None,
          'elapsed_seconds':gap}
    timeline=[]
    for index,row in enumerate((history or [])[-12:]):
        stamp=parse_time(row.get('time'))
        age=(current-stamp).total_seconds() if stamp else None
        timeline.append({'history_tail_index':index,'role':row.get('role','unknown'),
            'message_hint':str(row.get('content') or '')[:100],
            'sent_at':stamp.isoformat(timespec='seconds') if stamp else None,
            'seconds_ago':round(age) if age is not None and age>=0 else None})
    data['history_messages']=timeline
    return ('[Server conversation clock]\n'+json.dumps(data,ensure_ascii=False)+'\n'
            'Use these clock facts for temporal reasoning. Old history is not happening now. '
            'A gap of hours/days is not an immediate follow-up; do not call it just now. '
            'Unknown timestamps stay unknown; do not invent dates or elapsed time. '
            'History timestamp prefixes label archived turns only; never copy them into your new reply. '
            'Do not routinely announce time or explain this metadata; answer naturally.\n')


def previous_web_time(manager,session_id,agent_id='saya'):
    if not session_id:return None
    try:
        rows=web_history_times(manager,session_id,agent_id=agent_id)
        return next((row.get('time') for row in reversed(rows) if isinstance(row,dict) and row.get('time')),None)
    except (OSError,ValueError,TypeError):return None


def web_history_times(manager,session_id,agent_id='saya'):
    if not session_id:return []
    try:
        from .chat_agents import entries_for_agent
        return entries_for_agent(manager.load_session_full(session_id,make_current=False),agent_id)[-200:]
    except (OSError,ValueError,TypeError):return []


class QQTurnTimes:
    """Only timestamp/hash metadata. Unknown old rows are never backdated."""
    def __init__(self,store,retention_days=7):
        self.store,self.retention_days=store,retention_days
        with store.connect() as db:
            db.execute('''create table if not exists qq_turn_times(
                id integer primary key,scope text not null,session_id text not null,
                content_hash text not null,sent_at real,created real not null)''')
            db.execute('create index if not exists qq_turn_times_scope on qq_turn_times(scope,session_id,id)')

    @staticmethod
    def digest(line):return hashlib.sha256(line.encode('utf8')).hexdigest()

    def record(self,scope,session_id,lines,timestamps):
        now=time.time()
        with self.store.connect() as db:
            for line,stamp in zip(lines,timestamps):
                parsed=parse_time(stamp)
                db.execute('insert into qq_turn_times(scope,session_id,content_hash,sent_at,created) values(?,?,?,?,?)',
                    (scope,session_id,self.digest(line),parsed.timestamp() if parsed else None,now))
            db.execute('delete from qq_turn_times where created<?',(now-self.retention_days*86400,))
            db.execute('delete from qq_turn_times where id in (select id from qq_turn_times order by id desc limit -1 offset 10000)')

    def history(self,scope,session_id,lines):
        with self.store.connect() as db:
            rows=db.execute('select content_hash,sent_at from qq_turn_times where scope=? and session_id=? order by id desc limit 1000',(scope,session_id)).fetchall()
        stamps={}
        for digest,stamp in rows:stamps.setdefault(digest,[]).append(stamp)
        result=[]
        for line in reversed(lines[-200:]):
            options=stamps.get(self.digest(line),[])
            role,_,content=line.partition(':')
            result.append({'role':'user' if role=='Doctor' else 'ai' if role=='Saya' else 'unknown',
                           'content':content.strip(),'time':options.pop(0) if options else None})
        return list(reversed(result))


def timestamp_history(lines,primary=(),fallback=()):
    """Prompt-only projection. Exact content/role matching, duplicates in order."""
    def buckets(rows):
        result={}
        for row in reversed(rows):
            role='Doctor' if row.get('role')=='user' else 'Saya' if row.get('role') in {'ai','assistant'} else ''
            if role:result.setdefault((role,str(row.get('content') or '').strip()),[]).append(row.get('time'))
        return result
    first,second=buckets(primary),buckets(fallback)
    output=[]
    for line in reversed(lines):
        role,sep,body=line.partition(':')
        if not sep or role not in {'Doctor','Saya'}:output.append(line);continue
        key=(role,body.strip());a=first.get(key,[]);b=second.get(key,[])
        primary_time=a.pop(0) if a else None
        fallback_time=b.pop(0) if b else None
        stamp=parse_time(primary_time) or parse_time(fallback_time)
        label=stamp.isoformat(timespec='seconds') if stamp else 'unknown'
        output.append(f'{role}: [History sent_at={label}; timezone=Asia/Shanghai] {body.strip()}')
    return list(reversed(output))
