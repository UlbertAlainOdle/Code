"""Existing game archive policy with durable results and file-only recovery."""
import hashlib
import json
from pathlib import Path

from .chat_memory_writes import identity,journal,public_result
from .memory_origin import bind_origin
from .memory_versions import record_version
from .memory_write_operations import fingerprint


def scope_for(session):
    with bind_origin(None):return identity('game_archive',session)[1]


def path_for(service,session,archive_name=None):
    if not isinstance(session,str) or not service.GAME_SESSION_RE.fullmatch(session):raise ValueError('游戏会话编号无效')
    base=Path(service._memory().base_dir).resolve()
    path=(base/'archived'/archive_name if archive_name else base/session).resolve()
    if not path.is_relative_to(base):raise ValueError('归档路径无效')
    return path


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def latest(service,session):
    ledger=journal(service._knowledge_base())
    with ledger.store.connect() as db:
        row=db.execute("select id,result from memory_write_operations where scope=? and session=? and kind='game_archive' "
                       "and json_extract(result,'$[0].state')!='invalid' order by rowid desc limit 1",
                       (scope_for(session),session)).fetchone()
    return (row['id'],json.loads(row['result'])[0]) if row else None


def recover(service,session,operation):
    kb=service._knowledge_base();ledger=journal(kb)
    with kb._memory_write_lock,service._lock:
        def repair(items):
            item=dict(items[0])
            if item['state']!='committed' or not item.get('file_pending'):return items
            memory=kb._find_memory(item['memory_id'])
            source=path_for(service,session);target=path_for(service,session,item['archive_name'])
            if not memory or record_version(memory)!=item['version']:
                item['recovery_blocked']=True;return [item]
            if not source.exists():
                if target.is_file() and digest(target)==item['source_version']:
                    item['file_pending']=False;item.pop('recovery_blocked',None)
                else:item['recovery_blocked']=True
                return [item]
            if digest(source)!=item['source_version'] or target.exists():
                item['recovery_blocked']=True;return [item]
            try:
                target.parent.mkdir(parents=True,exist_ok=True)
                # Both resolved paths are confined to this history directory.
                # A pre-existing destination is never replaced.
                source.rename(target)
                item['file_pending']=False;item.pop('recovery_blocked',None)
            except OSError:pass
            return [item]
        result=ledger.reconcile(scope_for(session),session,operation,kind='game_archive',recover=repair)
        if result is None:raise ValueError('没有该游戏归档操作')
        item=result.items[0]
        return {'archived':result.committed,'status':'success' if result.committed else item['state'],
            'session_id':session,'memory_id':item.get('memory_id'),'turns':item.get('turns',0),
            'file_moved_to_archived':bool(result.committed and not item.get('file_pending')),
            'recovery_blocked':item.get('recovery_blocked',False),'operation':public_result(result),
            'reason':('来源或使用权限已变化，本次未保存' if item['state']=='invalid' else
                     '原操作处理中或结果待核对，请勿重复新建归档') if not result.committed else
                     '记忆已保存，原文件移动待核对；原文件保持' if item.get('file_pending') else ''}


def archive(service,session=None):
    kb=service._knowledge_base()
    if not session:
        sessions=service.list_game_sessions()
        if not sessions:return {'archived':False,'reason':'没有可归档的游戏会话'}
        session=sessions[0]['id']
    source=path_for(service,session)
    with kb._memory_write_lock,service._lock:
        old=latest(service,session)
        if old and (not source.exists() or old[1]['state'] in {'pending','unknown'}
                    or old[1].get('file_pending')):
            return recover(service,session,old[0])
        if not source.is_file():return {'archived':False,'reason':'该游戏会话没有原文件'}
        version=digest(source);epoch=kb.memory_use.snapshot()
        rows=service._memory().load_session_full(session,make_current=False) or []
        items=kb.memory_use.project_entries(rows)[-200:]
        items=[item for item in items if item.get('role') in {'user','ai','assistant'}
               and item.get('content') and item.get('context_eligible') is not False
               and item.get('content_kind') not in {'control_receipt','action_proposal'}]
        if not items:return {'archived':False,'reason':'没有当前可用的游戏对话来源'}
        plan={'session':session,'source_version':version,'epoch':epoch,'selected':fingerprint(items)}
        with bind_origin(None):operation,scope=identity('game_archive',session,fingerprint(plan))
        ledger=journal(kb)
        def valid():return source.is_file() and digest(source)==version and kb.memory_use.snapshot()==epoch
        result=ledger.run(operation,scope,session,'game_archive',[plan],verify=lambda db:True,
            valid=lambda plan:valid(),write=lambda plan:{'state':'pending'},retention=30*86400)
    if result.fresh and result.items==[{'state':'pending'}]:
        user=[row for row in items if row['role']=='user'];ai=[row for row in items if row['role'] in {'ai','assistant'}]
        time_span=f"{items[0].get('time','')} 至 {items[-1].get('time','')}"
        lines=[f"- Doctor：{left['content'][:60]} / Saya：{right['content'][:60]}" for left,right in list(zip(user,ai))[:4]]
        fallback=(f"与Doctor在Minecraft中的游戏对话归档（会话 {session}，{time_span}，共 {len(user)} 轮）。对话摘要：\n"+'\n'.join(lines))[:1600]
        try:summary=service._ai_archive_summary(items)
        except BaseException:
            ledger.finish_prepared(scope,session,operation,kind='game_archive',valid=lambda:False,write=lambda:None)
            raise
        content=f"与Doctor在Minecraft中的游戏对话归档（{time_span}，共 {len(user)} 轮）。要点：\n{summary}" if summary else fallback
        def write():
            target_name=session
            if path_for(service,session,target_name).exists():target_name=f'{Path(session).stem}.{operation}.jsonl'
            with bind_origin(None):
                memory=kb.add_memory(content,title=f"游戏对话归档 {session.replace('session_mc_','').replace('.jsonl','')}",
                    source='session_archive',category='conversation_summary',tags=['minecraft','game-chat'],
                    confidence=.9 if summary else .6,is_important=True,source_detail={
                        'type':'game_session_archive','session_id':session,'turns':len(user),'ai_summary':bool(summary),
                        'archive_operation_id':operation,'archive_source_version':version})
            return {'state':'committed','memory_id':memory['id'],'version':record_version(memory),
                    'source_version':version,'archive_name':target_name,'file_pending':True,'turns':len(user)}
        with kb._memory_write_lock,service._lock:
            result=ledger.finish_prepared(scope,session,operation,kind='game_archive',valid=valid,write=write)
    return recover(service,session,result.id)
