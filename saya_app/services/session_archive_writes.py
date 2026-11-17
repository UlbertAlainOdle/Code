"""Source-bound explicit archive operations over the original memory writer."""
from copy import deepcopy
import re

from .chat_memory_writes import identity, journal, public_result
from .memory_origin import bind_origin, ingress
from .memory_versions import MemoryVersionConflict, record_version
from .memory_write_operations import fingerprint


def eligible_exchanges(kb, manager, entries):
    dictionaries = [entry for entry in entries if isinstance(entry, dict)]
    eligible = {id(entry) for entry in kb.memory_use.project_entries(dictionaries)
                if entry.get('context_eligible') is not False
                and entry.get('content_kind') not in {'control_receipt', 'action_proposal'}}
    legacy_allowed = kb.memory_use.snapshot() == '0' and not kb.memory_use.history_floor()
    exchanges = manager.build_archive_exchanges(entries)
    return [exchange for exchange in exchanges if all(
        (id(entry) in eligible if isinstance(entry, dict) else legacy_allowed)
        for entry in entries[exchange['question_index']:exchange['answer_index']+1]
        if not isinstance(entry, dict) or entry.get('role') in {'user', 'ai', 'assistant'})]


def snapshot(kb, key, entries):
    return fingerprint({'source': key, 'entries': entries, 'epoch': kb.memory_use.snapshot()})


def preview(kb, manager, load):
    with kb._memory_write_lock:
        key, entries = load()
        return {'session_id': key, 'snapshot': snapshot(kb, key, entries),
                'exchanges': eligible_exchanges(kb, manager, entries)}


async def archive(kb, manager, request, load, generate, parse, bound):
    request = deepcopy(request)
    token, source_version = request.get('operation_id'), request.get('expected_snapshot')
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('请重新打开归档预览，缺少有效操作编号')
    if not isinstance(source_version, str) or not re.fullmatch(r'[a-f0-9]{64}', source_version):
        raise ValueError('请重新打开归档预览，缺少有效来源版本')
    ids = request.get('selected_exchange_ids')
    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('请选择完整且不重复的问答编号')
    with kb._memory_write_lock:
        key, entries = load()
        with bind_origin(ingress('web',session_id=key)):
            operation, scope = identity('archive', key, token)
        ledger = journal(kb)
        # The plan excludes provider credentials and model settings. A retry
        # can never regenerate a different summary for an accepted operation.
        plan = {'source': key, 'snapshot': source_version, 'selected': ids}
        def valid():
            current_key, current_entries = load()
            return current_key == key and snapshot(kb, key, current_entries) == source_version
        existing = ledger.lookup(scope, key, operation, kind='archive')
        if existing is None:
            existing = ledger.lookup_plan(scope,key,'archive',[plan])
            if existing is not None and existing.items != [{'state':'invalid'}]:operation=existing.id
            else:existing=None
        if existing is None:
            if not valid():raise MemoryVersionConflict('归档来源或使用权限已变化，请重新预览')
            exchanges = eligible_exchanges(kb, manager, entries)
            selected = [x for x in exchanges if x['id'] in ids]
            if len(selected) != len(ids):raise ValueError('所选问答不可用，请重新预览')
        else:
            selected = []
        result = ledger.run(operation, scope, key, 'archive', [plan], verify=lambda db: True,
            valid=lambda p: valid(), write=lambda p: {'state': 'pending'}, retention=30*86400)
    if result.fresh and result.items == [{'state': 'pending'}]:
        try:
            text = await generate(selected, request)
            if not text:raise ValueError('归档模型没有返回摘要')
            title, summary = parse(text)
            summary, truncated, original_chars = bound(summary)
        except BaseException:
            ledger.finish_prepared(scope,key,operation,kind='archive',valid=lambda:False,write=lambda:None)
            raise
        def write():
            with bind_origin(ingress('web',session_id=key,message_id=operation,quote='选中问答归档')):
                memory = kb.add_memory(summary,title=title,source='session_archive',category='conversation_summary',
                    tags=['conversation','summary','episodic'],source_detail={
                        'type':'session_archive','action':'summarize_selected_exchanges','target_memory_layer':'episodic',
                        'session_id':key,'selected_exchange_ids':ids,'selected_exchange_count':len(ids),
                        'content_truncated':truncated,'original_generated_chars':original_chars,
                        'archive_operation_id':operation,'archive_source_version':source_version},
                    metadata={'memory_layer':'episodic','memory_kind':'summary','archive_semantics':'active_summary_not_archived_record'})
            return {'state':'committed','memory_id':memory['id'],'version':record_version(memory)}
        result = ledger.finish_prepared(scope,key,operation,kind='archive',valid=valid,write=write)
    item = result.items[0]
    response = {'status':'success' if result.committed else item['state'], 'operation':public_result(result),
                'session_id':key,'selected_exchange_count':len(ids),'memory_layer':'episodic'}
    if result.committed:
        with kb._memory_write_lock:
            memory = kb._find_memory(item['memory_id'])
            response['memory_id'] = item['memory_id']
            if memory and record_version(memory) == item['version']:
                response.update(title=memory['title'],summary=memory['content'])
            else:response['content_changed']=True
    else:
        response['message'] = ('来源或权限已变化，本次没有归档，请重新预览。' if item['state']=='invalid'
            else '本次归档处理中或结果待核对，请查询原操作，不要新建操作重复保存。')
    return response
