"""Run-authorized Learner derivation over the original KB storage APIs."""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict,is_dataclass
import json
from pathlib import Path
import sqlite3

from .chat_memory_writes import identity,journal,public_result
from .memory_origin import bind_origin
from .memory_versions import record_version,dependency_version
from .memory_write_operations import fingerprint

RUNTIME_FIELDS={'updated_at','last_run_at','last_status','processed_source_ids','next_run_at','defer_session_id'}


def authority(task):return fingerprint({k:v for k,v in task.items() if k not in RUNTIME_FIELDS})


def material_fingerprint(material):
    def encode(value):
        if is_dataclass(value):return asdict(value)
        raise TypeError('unsupported_learner_source')
    return fingerprint(json.loads(json.dumps(material,default=encode)))


def operation(run_id,kind,key):
    with bind_origin(None):return identity(kind,run_id,key)


def read_context(service,task,run_id):
    kb=service.knowledge_base
    if kb is None:return service._read_memories(task)
    # Existing Learner deletion APIs already use Learner -> KB ordering.
    with service._lock,kb._memory_write_lock:
        run=next(item for item in service.data['runs'] if item['id']==run_id)
        if authority(service._find_task(task['id']))!=run.get('memory_authority') or authority(task)!=run.get('memory_authority'):
            raise ValueError('learner_task_changed_before_read')
        text,ids=service._read_memories(task)
        sources={}
        for mid in ids:
            memory=kb._find_memory(mid)
            if not memory or not kb.memory_allowed_for_use(memory):raise ValueError('learner_source_unavailable')
            sources[mid]=record_version(memory)
        run['memory_guard']={'task':authority(task),'epoch':kb.memory_use.snapshot(),'sources':sources}
        service._save()
        return text,ids


def valid(service,task,run_id,guard):
    kb=service.knowledge_base
    run=next((item for item in service.data.get('runs',[]) if item['id']==run_id),None)
    try:live=service._find_task(task['id'])
    except ValueError:return False
    if (service._shutdown_event.is_set() or not run or run.get('status')!='running'
        or not live.get('manage_memory') or authority(live)!=guard['task'] or authority(task)!=guard['task']
        or kb.memory_use.snapshot()!=guard['epoch']):return False
    return all(kb._find_memory(mid) and record_version(kb._find_memory(mid))==version
               and kb.memory_allowed_for_use(kb._find_memory(mid)) for mid,version in guard['sources'].items())


def related(service,run_id):
    return [m for m in service.knowledge_base.memories if m.get('source')=='learner'
        and str((m.get('source_detail') or {}).get('learner_run_id') or '')==str(run_id)
        and not m.get('is_archived') and service.knowledge_base.memory_allowed_for_use(m)]


def versions(records):return {m['id']:record_version(m) for m in records}


def guard_for(service,run_id):
    run=next((item for item in service.data.get('runs',[]) if item['id']==run_id),None)
    if not run or not run.get('memory_guard'):raise ValueError('learner_run_authorization_missing')
    return deepcopy(run['memory_guard'])


def _candidate(service,task,run_id,candidate,material,guard,index):
    kb=service.knowledge_base;content=candidate['content'];matches=related(service,run_id)
    duplicates=[m for m in matches if service._similarity(content,m.get('content'))>=.76]
    duplicates.sort(key=lambda m:(bool(m.get('is_important') or m.get('is_locked')),float(m.get('importance_score') or 0),str(m.get('date') or '')),reverse=True)
    sources={mid:dependency_version(kb._find_memory(mid)) for mid in guard['sources']}
    detail={'type':'learner_summary','learner_task_id':task['id'],'learner_run_id':run_id,
        'quality_tier':material.get('quality_tier'),'quality_notice':material.get('quality_notice',''),
        'learner_source_versions':sources,'sources':[{k:s.get(k) for k in ('title','url','kind','published_at','source_type')} for s in material.get('sources',[])[:10]]}
    actions=[];preserved=[]
    available=[m for m in duplicates if not m.get('is_locked') and not m.get('managed_slot')]
    if duplicates and not available:return [],[m['id'] for m in duplicates],True
    if not duplicates:
        actions=[{'action':'created','body':{'content':content,'title':candidate.get('title'),'source':'learner',
            'is_important':candidate.get('important',False),'category':'knowledge',
            'tags':list(dict.fromkeys(['learner',task.get('source_type')]+candidate.get('tags',[]))),
            'confidence':.66 if material.get('degraded') else .84,'source_detail':detail,
            'metadata':{'memory_layer':'semantic','memory_kind':'knowledge',
                        'retention_policy':'permanent' if candidate.get('important') else 'compressible',
                        'importance_score':candidate.get('importance',.6)}}}]
    else:
        primary=available[0];combined=service._merge_text(primary.get('content'),content)
        supersedes=list(primary.get('supersedes') or []);archives=[]
        for other in duplicates:
            if other['id']==primary['id']:continue
            combined=service._merge_text(combined,other.get('content'))
            if other.get('is_important') or other.get('is_locked') or other.get('managed_slot'):preserved.append(other['id']);continue
            # The original archive API links this duplicate back to primary.
            # Do not pre-link it in the primary update: that API also updates
            # the duplicate and would invalidate its frozen archive version.
            archives.append({'action':'archived','target':other['id'],'expected':record_version(other),
                             'body':{'is_archived':True,'superseded_by':primary['id']}})
        actions=[{'action':'merged','target':primary['id'],'expected':record_version(primary),'body':{
            'content':combined,'source_detail':detail,'tags':list(dict.fromkeys(list(primary.get('tags') or [])+['learner']+candidate.get('tags',[])))[:12],
            'is_important':bool(primary.get('is_important') or candidate.get('important')),
            'importance_score':max(float(primary.get('importance_score') or 0),candidate.get('importance',.6)),
            'supersedes':list(dict.fromkeys(supersedes))}}]+archives
    results=[]
    for seq,action in enumerate(actions):
        oid,scope=operation(run_id,'learner_item',f'{index}:{seq}')
        def current(_):
            if not valid(service,task,run_id,guard):return False
            if action.get('target'):
                original=kb._find_memory(action['target'])
                return bool(original and record_version(original)==action['expected'] and not original.get('is_locked') and not original.get('managed_slot'))
            return True
        def write(_):
            with bind_origin(None):
                if action['action']=='created':memory=kb.add_memory(**action['body'])
                else:
                    done=kb.update_memory(action['target'],action['body'],expected_version=action['expected'])
                    if not done:raise OSError('learner_update_not_committed')
                    memory=kb._find_memory(action['target'])
            return {'state':'committed','action':action['action'],'memory_id':memory['id'],'version':record_version(memory)}
        result=journal(kb).run(oid,scope,run_id,'learner_item',[action],verify=lambda db:True,valid=current,write=write,retention=30*86400)
        results.append(public_result(result))
        if not result.committed:return results,preserved,False
        # A known own update advances the global read epoch. Original parent
        # versions are still checked before any later action; external edits
        # cannot interleave while the existing two locks are held.
        guard['epoch']=kb.memory_use.snapshot()
    return results,preserved,True


def _empty():return {'created_ids':[],'merged_ids':[],'archived_duplicate_ids':[],
    'preserved_important_ids':[],'graph_rebuilt':False,'status':'pending','operations':[]}


def result_payload(result):
    item=result.items[0];payload={**_empty(),**item.get('result',{})}
    payload['operation']=public_result(result)
    payload['status']='success' if result.committed else item['state']
    if not result.committed:payload['reason']='Memory operations are incomplete; query this run before starting another write.'
    return payload


def manage(service,task,run_id,summary,material,credentials,use_model=True,*,candidate=None):
    kb=service.knowledge_base
    if kb is None:return {**_empty(),'status':'not_saved','skipped':True}
    ledger=journal(kb);oid,scope=operation(run_id,'learner_batch','memory-management')
    with service._lock,kb._memory_write_lock:
        old=ledger.lookup(scope,run_id,oid,kind='learner_batch')
        if old:return readonly_result(kb,run_id)
        guard=guard_for(service,run_id);before=versions(related(service,run_id))
        frozen=deepcopy(material);material_version=material_fingerprint(material)
        plan={'guard':guard,'targets':before,'material':material_version,'summary':fingerprint(summary)}
        def check():return valid(service,task,run_id,guard) and versions(related(service,run_id))==before and material_fingerprint(material)==material_version
        prepared=ledger.run(oid,scope,run_id,'learner_batch',[plan],verify=lambda db:True,valid=lambda p:check(),write=lambda p:{'state':'pending'},retention=30*86400)
    if prepared.items!=[{'state':'pending'}]:return result_payload(prepared)
    try:candidates=[deepcopy(candidate)] if candidate is not None else service._memory_candidates(task,summary,credentials,use_model=use_model)
    except BaseException:
        ledger.finish_prepared(scope,run_id,oid,kind='learner_batch',valid=lambda:False,write=lambda:None)
        raise
    def write():
        output=_empty();complete=True
        for index,value in enumerate(candidates[:6]):
            outcomes,preserved,ok=_candidate(service,task,run_id,value,frozen,guard,index)
            output['preserved_important_ids'].extend(preserved);output['operations'].extend(outcomes)
            for outcome in outcomes:
                for item in outcome['items']:
                    if item['state']=='committed':output[{'created':'created_ids','merged':'merged_ids','archived':'archived_duplicate_ids'}[item['action']]].append(item['memory_id'])
            if not ok:complete=False;break
        return {'state':'committed' if complete else 'partial','result':output}
    with service._lock,kb._memory_write_lock:
        final=ledger.finish_prepared(scope,run_id,oid,kind='learner_batch',valid=check,write=write)
    return result_payload(final)


def readonly_result(kb,run_id):
    if kb is None:return None
    path=Path(kb.base_dir)/'memory3.sqlite3'
    if not path.exists():return None
    oid,scope=operation(run_id,'learner_batch','memory-management')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("select 1 from sqlite_master where name='memory_write_operations'").fetchone():return None
        row=db.execute("select result from memory_write_operations where id=? and scope=? and session=? and kind='learner_batch'",(oid,scope,run_id)).fetchone()
        children=db.execute("select id,result from memory_write_operations where scope=? and session=? and kind='learner_item' order by rowid",(scope,run_id)).fetchall()
    if not row:return None
    from .memory_write_operations import SaveResult
    result=SaveResult(oid,json.loads(row[0]));payload=result_payload(result)
    if result.items[0].get('state') in {'pending','unknown'}:
        for child_id,body in children:
            child=SaveResult(child_id,json.loads(body));payload['operations'].append(public_result(child))
            for item in child.items:
                if item['state']=='committed':payload[{'created':'created_ids','merged':'merged_ids','archived':'archived_duplicate_ids'}[item['action']]].append(item['memory_id'])
    return payload
