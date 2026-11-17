"""Version-bound web candidate confirmation with file-only recovery."""
from copy import deepcopy
import re

from .chat_memory_writes import identity, journal, public_result
from .memory_versions import CandidateStateSaveError, MemoryVersionConflict, record_version
from .memory_write_operations import fingerprint
from .qq_tasks import TaskCancelled


def confirm(kb, candidate_id, updates, version):
    if not isinstance(version,str) or not re.fullmatch(r'[a-f0-9]{64}',version):
        raise ValueError('candidate_version_required')
    updates=deepcopy(updates)
    plan={'candidate_id':candidate_id,'version':version,'updates':updates}
    operation=fingerprint(['web_candidate',plan])[:32]
    _,scope=identity('candidate','')
    service=journal(kb)
    def write(_):
        try:
            value=kb.confirm_memory_candidate(candidate_id,updates,expected_version=version)
        except MemoryVersionConflict:
            return {'state':'invalid','error':'version'}
        except CandidateStateSaveError as error:
            memory=kb._find_memory(error.committed_memory_id) if error.committed_memory_id else None
            if memory:
                return {'state':'committed','status':'success','memory_id':memory['id'],
                        'version':record_version(memory),'candidate_status_pending':True}
            return {'state':'unknown'}
        except TaskCancelled:
            raise
        except Exception:
            return {'state':'unknown'}
        if not value:return {'state':'not_saved','status':'not_found'}
        memory=value.get('memory')
        return ({'state':'committed','status':value['status'],'memory_id':memory['id'],
                 'version':record_version(memory),'candidate_status_pending':False}
                if memory else {'state':'not_saved','status':value['status']})
    result=service.run(operation,scope,'','candidate',[plan],verify=lambda db:True,
                       valid=lambda plan:True,
                       write=lambda plan:{**write(plan),'candidate_id':candidate_id,'candidate_version':version},
                       retention=30*86400)
    if not result.fresh:
        return reconcile(kb,result.id)
    return result,response(kb,result)


def reconcile(kb,operation):
    if not isinstance(operation,str) or not re.fullmatch(r'[a-f0-9]{32}',operation):
        raise ValueError('invalid_candidate_operation')
    _,scope=identity('candidate','')
    def recover(items):
        item=items[0]
        if item.get('candidate_status_pending') is not True:return items
        try:
            resolved=kb.reconcile_confirmed_candidate(item['candidate_id'],item['memory_id'],
                                                     item['candidate_version'],item['version'])
        except CandidateStateSaveError:
            resolved=False
        return [{**item,'candidate_status_pending':False}] if resolved else items
    result=journal(kb).reconcile(scope,'',operation,kind='candidate',recover=recover)
    return (result,response(kb,result)) if result else (None,None)


def response(kb,result):
    item=result.items[0]
    with kb._memory_write_lock:
        memory=kb._find_memory(item.get('memory_id'))
        memory=deepcopy(memory) if memory and record_version(memory)==item.get('version') else None
    return {'status':item.get('status',item['state']),'memory':memory,
            'operation':public_result(result),'memory_current':memory is not None,
            'candidate_status_pending':item.get('candidate_status_pending',False)}


def pending_operation(kb,candidate):
    _,scope=identity('candidate','')
    service=journal(kb)
    with service.store.connect() as db:
        row=db.execute("select id,json_extract(result,'$[0].state') as state from memory_write_operations "
            "where scope=? and session='' and kind='candidate' "
            "and expires>? and json_extract(result,'$[0].candidate_id')=? "
            "and json_extract(result,'$[0].candidate_version')=? "
            "and (json_extract(result,'$[0].candidate_status_pending')=1 or json_extract(result,'$[0].state')='unknown') "
            "order by rowid desc limit 1",
            (scope,service.clock(),candidate['id'],record_version(candidate))).fetchone()
    if row:
        return {'operation_id':row['id'],'committed':row['state']=='committed'}
    return None
