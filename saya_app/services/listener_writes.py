"""Versioned explicit Listener plans and file-only postcommit recovery."""
from copy import deepcopy
import re
import time

from .chat_memory_writes import identity, journal, public_result
from .memory_origin import bind_origin, ingress
from .memory_versions import record_version, MemoryVersionConflict
from .memory_write_operations import SaveResult, fingerprint
from .qq_tasks import TaskCancelled


def scope_for(session):
    with bind_origin(ingress('web',session_id=session)):
        return identity('listener',session)[1]


def operations(service, session):
    ledger=journal(service.knowledge_base())
    with ledger.store.connect() as db:
        rows=db.execute("select id,result from memory_write_operations where scope=? and session=? and kind='listener' "
                        'order by rowid desc',(scope_for(session),session)).fetchall()
    import json
    return [(row['id'],json.loads(row['result'])[0]) for row in rows]


def linked_id(service,session):
    direct=service._linked_memory_id(session)
    intended=None
    for _,item in operations(service,session):
        if item.get('state')=='committed':
            intended=item.get('memory_id');break
    if intended is None:
        intended=next((row.get('listener_memory_id') for row in reversed(service._entries(session))
                       if row.get('listener')=='confirm' and row.get('listener_memory_id')),None)
    if direct and intended and direct!=intended:
        raise MemoryVersionConflict('Listener关联与原提交不一致，未改派其他记忆')
    return direct or intended


def require_session(service,session):
    if (not isinstance(session,str) or not re.fullmatch(r'session_[A-Za-z0-9_.-]+\.jsonl',session)
            or session.startswith(('session_qq_','session_mc_')) or session=='session_saya_unified.jsonl'):
        raise ValueError('Listener会话无效')
    if (session not in service._load_state() and not any(row.get('listener') for row in service._entries(session))
            and not operations(service,session)):
        raise ValueError('请先打开已注册的Listener会话')


def version(service,session):
    kb=service.knowledge_base();mid=linked_id(service,session)
    memory=kb._find_memory(mid) if mid else None
    return fingerprint({'session':session,'entries':service._entries(session),
        'link':service._load_state().get(session),'memory_id':mid,
        'memory_version':record_version(memory) if memory else None,'epoch':kb.memory_use.snapshot()})


def pending(service,session,exclude=None):
    for operation,item in operations(service,session):
        if operation==exclude:continue
        if item['state'] in {'pending','unknown'} or (item['state']=='committed' and (
                item.get('link_pending') or item.get('receipt_pending'))):
            return {'operation_id':operation,'committed':item['state']=='committed',
                    'state':item['state'],'recovery_blocked':item.get('recovery_blocked',False)}
    return None


def state(service,session):
    kb=service.knowledge_base()
    with kb._memory_write_lock,service._lock:
        require_session(service,session)
        result=service._derive_state(session)
        result['confirmation_version']=version(service,session)
        result['pending_operation']=pending(service,session)
        if result['pending_operation']:result['locked']=True
        if result['locked'] and result.get('memory_id'):
            memory=kb._find_memory(result['memory_id'])
            result['proposal']=_final(memory) if memory else None
            result['memory_missing']=memory is None
        return result


def _final(memory):
    return {'title':memory.get('title',''),'content':memory.get('content',''),
            'category':memory.get('category','general'),'layer':memory.get('memory_layer','semantic')}


def recover(service,session,operation):
    kb=service.knowledge_base();ledger=journal(kb)
    with kb._memory_write_lock,service._lock:
        require_session(service,session)
        def repair(items):
            item=deepcopy(items[0])
            if item['state']!='committed' or not (item.get('link_pending') or item.get('receipt_pending')):return items
            memory=kb._find_memory(item['memory_id'])
            rows=service._entries(session)
            receipt=any(row.get('listener_operation_id')==operation and row.get('listener')=='confirm'
                        and row.get('listener_memory_id')==item['memory_id'] for row in rows)
            mapping=service._load_state();entry=mapping.get(session)
            same_link=isinstance(entry,dict) and entry.get('memory_id')==item['memory_id'] and entry.get('last_operation')==operation
            if (not memory or record_version(memory)!=item['version']
                    or (not receipt and fingerprint(rows)!=item['session_version'])
                    or (not same_link and fingerprint(entry)!=item['link_version'])):
                item['recovery_blocked']=True
                return [item]
            item.pop('recovery_blocked',None)
            if item.get('link_pending'):
                try:
                    mapping[session]={**(entry or {}),'memory_id':item['memory_id'],'last_operation':operation,
                                      'created_at':(entry or {}).get('created_at',time.time())}
                    service._save_state(mapping)
                    item['link_pending']=False
                except TaskCancelled:raise
                except Exception:pass
            if item.get('receipt_pending'):
                try:
                    if not receipt:
                        final=_final(memory)
                        service._record(session,'ai',f"记忆已保存（{final['title']}，分级 {final['layer']}）。此对话已锁定；开启新的Listener对话可记录下一条。",
                            {'listener':'confirm','listener_memory_id':item['memory_id'],'listener_proposal':final,
                             'listener_operation_id':operation,'context_eligible':False,'content_kind':'control_receipt'})
                    item['receipt_pending']=False
                except TaskCancelled:raise
                except Exception:pass
            return [item]
        result=ledger.reconcile(scope_for(session),session,operation,kind='listener',recover=repair)
        if result is None:raise ValueError('当前Listener会话没有该操作')
        return response(service,session,result)


def response(service,session,result):
    item=result.items[0]
    committed=item['state']=='committed'
    incomplete=committed and (item.get('link_pending') or item.get('receipt_pending'))
    data={'type':'recovery_pending' if incomplete else ('updated' if item.get('action')=='updated' else 'confirmed') if committed else 'pending',
          'memory_id':item.get('memory_id'),'action':item.get('action'),'operation':public_result(result),
          'locked':True,'recovery_pending':bool(incomplete),'recovery_blocked':item.get('recovery_blocked',False)}
    memory=service.knowledge_base()._find_memory(item.get('memory_id')) if committed else None
    if memory and record_version(memory)==item.get('version'):data['proposal']=_final(memory)
    elif committed:data['content_changed']=True
    if incomplete:data['message']='记忆已保存；关联或回执待恢复，请核对原操作，不要重复保存。'
    elif not committed:data['message']='本次保存尚未确认提交，请核对原操作，不要重新新建记忆。'
    try:data['confirmation_version']=version(service,session)
    except MemoryVersionConflict:
        data['confirmation_version']=None;data['recovery_blocked']=True
    return data


def commit(service,session,content,layer,category,title,*,update=False,expected_version=None,operation_id=None):
    kb=service.knowledge_base()
    with kb._memory_write_lock,service._lock:
        require_session(service,session)
        raw=service._derive_state(session)
        if expected_version is None:expected_version=version(service,session)
        if not isinstance(expected_version,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_version):
            raise ValueError('Listener提案版本无效，请重新读取')
        if len(str(content or '').strip())>2000:
            raise ValueError('Listener单条内容最多2000字，请精简后重新确认；本次未保存')
        final={'title':str(title or '').strip()[:40] or str(content or '').strip()[:20],
               'content':str(content or '').strip()[:2000],'layer':str(layer or '').strip().lower(),
               'category':str(category or 'general').strip().lower() or 'general'}
        if not final['content'] or final['layer'] not in {'core','semantic','episodic'}:raise ValueError('记忆内容或分级无效')
        token=operation_id or fingerprint([expected_version,final,update])[:32]
        if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{32}',token):raise ValueError('Listener操作编号无效')
        with bind_origin(ingress('web',session_id=session)):
            operation,scope=identity('listener',session,token)
        ledger=journal(kb)
        old=ledger.lookup(scope,session,operation,kind='listener')
        if old is None:
            if version(service,session)!=expected_version:raise MemoryVersionConflict('Listener提案或关联记忆已变化，请重新读取')
            if pending(service,session):raise MemoryVersionConflict('上次Listener操作待核对，未再次写入')
            if not update and raw['locked']:raise ValueError('该对话已确认，请使用更新按钮')
            if not update and not raw.get('proposal'):raise ValueError('请先获得Listener提案')
        mid=linked_id(service,session)
        if update and not mid:raise ValueError('该对话还没有关联的记忆')
        memory=kb._find_memory(mid) if mid else None
        target_version=record_version(memory) if memory else None
        if old is None and mid and (not memory or memory.get('managed_slot')):raise ValueError('原关联记忆不可用，未新建替代条目')
        entries_version=fingerprint(service._entries(session));link_version=fingerprint(service._load_state().get(session))
        def valid():return version(service,session)==expected_version and not pending(service,session,exclude=operation)
        def write(plan):
            with bind_origin(ingress('web',session_id=session,message_id=operation,quote=final['content'])):
                detail={**((memory or {}).get('source_detail') or {}),'listener_session_id':session,'listener_operation_id':operation}
                if mid:
                    updates={'title':final['title'],'content':final['content'],'category':final['category'],
                             'memory_layer':final['layer'],'source_detail':detail,'tags':list(memory.get('tags') or ['listener'])}
                    if not update:updates.update(source='listener',tags=['listener'],confidence=1.0)
                    if not kb.update_memory(mid,updates,expected_version=target_version):raise OSError('listener_update_unconfirmed')
                    saved=kb._find_memory(mid)
                else:
                    saved=kb.add_memory(final['content'],title=final['title'],source='listener',category=final['category'],
                        confidence=1.0,tags=['listener'],metadata={'memory_layer':final['layer']},source_detail=detail)
            return {'state':'committed','memory_id':saved['id'],'version':record_version(saved),
                    'action':'updated' if mid else 'created','session_version':entries_version,'link_version':link_version,
                    'link_pending':True,'receipt_pending':True}
        # Only the explicit user plan is fingerprinted. A retry must not bind
        # itself to the now-updated linked entry or manufacture a new request.
        plan={'version':expected_version,'final':final,'update':update}
        result=ledger.run(operation,scope,session,'listener',[plan],verify=lambda db:valid(),
                          valid=lambda plan:valid(),write=write,retention=30*86400)
        if result.items[0]['state']=='committed':
            data=recover(service,session,operation)
            data['operation']['replayed']=not result.fresh
            return data
        return response(service,session,result)
