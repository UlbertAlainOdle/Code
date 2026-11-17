"""Source-bound dream steps and file-only diary recovery."""
from datetime import datetime,timedelta
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .chat_memory_writes import identity,journal,public_result
from .memory_origin import bind_origin
from .memory_use import MemoryUseState
from .memory_versions import record_version
from .memory_write_operations import fingerprint
from .state_storage import atomic_save_json


def date_key(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('invalid_dream_date')
    datetime.strptime(value,'%Y-%m-%d')
    return value


def source_root(base):
    from saya_app.config import KNOWLEDGE_BASE_DIR,CHAT_MEMORY_DIR
    return Path(CHAT_MEMORY_DIR if Path(base).resolve()==Path(KNOWLEDGE_BASE_DIR).resolve() else base).resolve()


def collect(base,date):
    date_key(date);base=Path(base);root=source_root(base);use=MemoryUseState(base)
    state=base/'listener_state.json';registered=json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
    if not isinstance(registered,dict):raise ValueError('listener_state_unavailable')
    entries=[];versions={}
    for path in sorted(root.glob('session_*.jsonl')):
        if path.name.startswith(('session_qq','session_mc_')) or path.name in registered:continue
        if not path.resolve().is_relative_to(root):continue
        rows=[]
        for line in path.read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if isinstance(row,dict):rows.append(row)
        if any(row.get('listener') or row.get('source')=='qq' or
               (row.get('origin') if isinstance(row.get('origin'),dict) else {}).get('channel')=='qq' for row in rows):continue
        selected=[row for row in rows if str(row.get('time') or '')[:10]==date]
        if not selected:continue
        versions[path.name]=fingerprint(selected)
        for row in use.project_entries(selected):
            if row.get('role') not in {'user','ai'} or row.get('context_eligible') is False:continue
            if row.get('content_kind') in {'control_receipt','action_proposal'}:continue
            content=str(row.get('content') or '').strip()
            if content:entries.append({**row,'content':content[:800]})
    entries.sort(key=lambda row:str(row.get('time') or ''))
    return entries,{'version':1,'date':date,'epoch':use.snapshot(),
                    'root':fingerprint(str(root)),'sources':versions}


def source_valid(base,guard):
    try:
        return isinstance(guard,dict) and guard.get('version')==1 and collect(base,guard['date'])[1]==guard
    except (OSError,ValueError,TypeError,KeyError):return False


def step_identity(base,date,kind):
    with bind_origin(None):return identity(kind,date,fingerprint([str(Path(base).resolve()),date,kind]))


def readonly_result(base,date):
    from .memory_write_operations import SaveResult
    date_key(date);path=Path(base)/'memory3.sqlite3';steps={}
    if not path.exists():return None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("select 1 from sqlite_master where name='memory_write_operations'").fetchone():return None
        for kind in ['dream_theme','dream_diary']:
            oid,scope=step_identity(base,date,kind)
            row=db.execute('select result from memory_write_operations where id=? and scope=? and session=? and kind=?',(oid,scope,date,kind)).fetchone()
            if row:steps[kind]=public_result(SaveResult(oid,json.loads(row[0])))
    return {'date':date,'operations':steps} if steps else None


def diary_allowed(base,payload):
    try:
        guard=payload['source_guard'];operation,scope=step_identity(base,date_key(payload['date']),'dream_diary')
        if not source_valid(base,guard):return False
        path=Path(base)/'memory3.sqlite3'
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("select result from memory_write_operations where id=? and scope=? and session=? and kind='dream_diary'",
                           (operation,scope,payload['date'])).fetchone()
        item=json.loads(row[0])[0] if row else {}
        return item.get('state')=='committed' and item.get('payload_version')==fingerprint(payload)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):return False


def recover_diary(base,kb,date,authorized=None):
    from . import dream
    ledger=journal(kb);operation,scope=step_identity(base,date,'dream_diary')
    def repair(items):
        item=dict(items[0])
        if item.get('state')!='committed' or not item.get('file_pending'):return items
        if authorized is not None and not authorized():
            item['recovery_blocked']=True;return [item]
        if item.get('authority','0')!=kb.settings.get('_dream_authority_version','0'):
            item['recovery_blocked']=True;return [item]
        try:
            staged=Path(base)/'.dream-staged'/f'{operation}.json'
            payload=json.loads(staged.read_text(encoding='utf-8'))
            target=Path(dream._diary_path(base,date))
            existing=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
            if not source_valid(base,payload['source_guard']) or fingerprint(payload)!=item['payload_version']:
                item['recovery_blocked']=True
            elif existing is not None and fingerprint(existing)==item['payload_version']:
                item['file_pending']=False;item.pop('recovery_blocked',None)
            elif (hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None)!=item['previous_file']:
                item['recovery_blocked']=True
            else:
                dream.save_diary(base,date,payload)
                item['file_pending']=False;item.pop('recovery_blocked',None)
        except (OSError,ValueError,TypeError,KeyError):pass
        return [item]
    with kb._memory_write_lock,dream._LOCK:
        return ledger.reconcile(scope,date,operation,kind='dream_diary',recover=repair)


def run(base,kb,invoke,target_date=None,now=None,*,authorized=None):
    from . import dream
    now=now or datetime.now();date=date_key(target_date or (now-timedelta(days=1)).strftime('%Y-%m-%d'))
    report={'date':date,'ran':False,'calls':0,'transcript_entries':0,'theme_memory_id':None,
        'theme_evidence_kept':0,'theme_evidence_dropped':0,'diary_saved':False,
        'diary_sentences_raw':0,'diary_sentences_kept':0,'diary_sentences_dropped':0,'reason':''}
    if not callable(invoke):return {**report,'reason':'no_invoke'}
    authorized=authorized or (lambda:True);ledger=journal(kb);steps={};fresh=False
    with kb._memory_write_lock,dream._LOCK:
        for kind in ['dream_theme','dream_diary']:
            operation,scope=step_identity(base,date,kind)
            steps[kind]=ledger.lookup(scope,date,operation,kind=kind)
        if not any(steps.values()):
            state=dream._load_state(base)
            if state.get('running'):return {**report,'reason':'already_running'}
            entries,guard=collect(base,date);report['transcript_entries']=len(entries)
            if not entries:return {**report,'reason':'empty_transcript'}
            if not authorized():return {**report,'reason':'authorization_changed'}
            target=Path(dream._diary_path(base,date))
            previous_file=hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
            authority_version=kb.settings.get('_dream_authority_version','0')
            plan={'source':guard,'purpose':'nightly_dream','previous_diary':previous_file,'authority':authority_version}
            for kind in steps:
                operation,scope=step_identity(base,date,kind)
                steps[kind]=ledger.run(operation,scope,date,kind,[plan],verify=lambda db:True,
                    valid=lambda p:source_valid(base,guard) and authorized(),write=lambda p:{'state':'pending'},retention=30*86400)
            fresh=True
    if fresh:
        digest=dream.build_digest(entries);normalized=dream._normalize(digest)
        user_digest=dream._normalize(dream.build_digest([row for row in entries if row['role']=='user']))
        strong=sum(row.get('mood') in dream.STRONG_MOODS for row in entries)
        def valid():return source_valid(base,guard) and authorized() and kb.settings.get('_dream_authority_version','0')==authority_version
        for kind in steps:
            operation,scope=step_identity(base,date,kind)
            with kb._memory_write_lock:
                permitted=valid()
            if not permitted:
                steps[kind]=ledger.finish_prepared(scope,date,operation,kind=kind,valid=lambda:False,write=lambda:None)
                continue
            try:
                answer=invoke(dream._theme_prompt(digest) if kind=='dream_theme' else dream._diary_prompt(digest));report['calls']+=1
            except BaseException:
                for pending in steps:
                    oid,sc=step_identity(base,date,pending)
                    ledger.finish_prepared(sc,date,oid,kind=pending,valid=lambda:False,write=lambda:None)
                raise
            if kind=='dream_theme':
                parsed=dream._parse_theme(answer);theme,evidence=parsed if parsed else ('',[])
                kept=[q for q in evidence if dream._normalize(q) and dream._normalize(q) in user_digest and not dream._SECRET_RE.search(q)]
                report.update(theme_evidence_kept=len(kept),theme_evidence_dropped=len(evidence)-len(kept))
                def write():
                    if not kept or dream._SECRET_RE.search(theme):return {'state':'not_saved','reason':'no_valid_evidence'}
                    with bind_origin(None):
                        memory=kb.add_memory(theme,title=f'梦的整理 · {date}',source='nightly_dream',category='conversation_summary',
                            tags=['dream','summary'],importance_score=min(.95,.5+min(.2,.04*strong)),
                            source_detail={'type':'nightly_dream','date':date,'evidence':kept[:3],
                                           'strong_mood_turns':strong,'dream_source':guard})
                    return {'state':'committed','memory_id':memory['id'],'version':record_version(memory)}
            else:
                kept,quotes=dream.filter_diary(str(answer or ''),normalized)
                raw=[s.strip() for s in dream.SENTENCE_SPLIT.split(str(answer or '')) if len(s.strip())>=4]
                report.update(diary_sentences_raw=len(raw),diary_sentences_kept=len(kept),diary_sentences_dropped=len(raw)-len(kept))
                payload={'date':date,'generated_at':now.strftime('%Y-%m-%d %H:%M:%S'),'entry':''.join(kept),
                    'quotes_used':quotes[:20],'source_guard':guard,'theme_memory_id':steps['dream_theme'].items[0].get('memory_id'),
                    'stats':{'raw':len(raw),'kept':len(kept),'dropped':len(raw)-len(kept),'strong_mood_turns':strong}}
                def write():
                    if not kept:return {'state':'not_saved','reason':'no_valid_evidence'}
                    staged=Path(base)/'.dream-staged'/f'{operation}.json';staged.parent.mkdir(exist_ok=True)
                    atomic_save_json(staged,payload)
                    return {'state':'committed','payload_version':fingerprint(payload),'previous_file':previous_file,
                            'authority':authority_version,'file_pending':True}
            with kb._memory_write_lock:
                steps[kind]=ledger.finish_prepared(scope,date,operation,kind=kind,valid=valid,write=write)
    repaired=recover_diary(base,kb,date,authorized)
    if repaired:steps['dream_diary']=repaired
    report['operations']={kind:public_result(result) for kind,result in steps.items() if result}
    theme=steps['dream_theme'];diary=steps['dream_diary']
    if theme and theme.committed:report['theme_memory_id']=theme.items[0]['memory_id']
    report['diary_saved']=bool(diary and diary.committed and not diary.items[0].get('file_pending'))
    report['diary_available']=bool(dream.read_diary(base,date))
    report['ran']=all(result and result.items[0]['state'] in {'committed','not_saved'} for result in steps.values())
    if not report['ran']:report['reason']='source_changed_or_operation_pending'
    with kb._memory_write_lock,dream._LOCK:
        try:
            state=dream._load_state(base);state['running']=False
            if report['ran']:
                state.setdefault('done',{})[date]={k:report[k] for k in ['theme_memory_id','diary_saved','calls']}
                state['done']=dict(sorted(state['done'].items())[-90:])
            dream._save_state(base,state)
        except (OSError,ValueError):report['state_file_pending']=True
    return report
