"""Explicit manual form plans; never interpret their text as chat commands."""
from copy import deepcopy
import re

from .chat_memory_writes import identity,journal,public_result
from .memory_versions import record_version
from .memory_write_operations import fingerprint


def save(kb,request):
    fields=('content','title','source','is_important','category','tags','confidence','source_detail','metadata')
    plan={key:deepcopy(request[key]) for key in fields if key in request}
    content=plan.get('content')
    if not isinstance(content,str) or not content.strip():raise ValueError('Content required')
    token=request.get('operation_id') or fingerprint(plan)[:32]
    if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{32}',token):raise ValueError('保存操作编号无效')
    operation,scope=identity('manual','',token)
    def write(_):
        memory=kb.add_memory(content,title=plan.get('title'),source=plan.get('source','manual'),
            is_important=bool(plan.get('is_important',False)),category=plan.get('category'),tags=plan.get('tags'),
            confidence=plan.get('confidence'),source_detail=plan.get('source_detail'),metadata=plan.get('metadata'))
        return {'state':'committed','memory_id':memory['id'],'version':record_version(memory)}
    result=journal(kb).run(operation,scope,'','manual',[plan],verify=lambda db:True,valid=lambda plan:True,
                           write=write,retention=30*86400)
    response={'status':'success' if result.committed else result.items[0]['state'],
              'operation':public_result(result),'maintenance':kb.last_maintenance_result}
    if result.committed:
        with kb._memory_write_lock:
            item=result.items[0];memory=kb._find_memory(item['memory_id'])
            response['memory_id']=item['memory_id']
            response['memory']=deepcopy(memory) if memory and record_version(memory)==item['version'] else None
            response['content_changed']=response['memory'] is None
    else:response['message']='本次保存结果待核对，请查询原操作，不要另建请求重复保存。'
    return response
