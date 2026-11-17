"""Read-only, paginated projection diagnostics. Never load a model or rebuild indexes."""
from contextlib import ExitStack, closing
from pathlib import Path
import json
import sqlite3
import time

from .memory_versions import record_version, check_version, MemoryVersionConflict


def native_version(item, tombstoned):
    return record_version({'memory': item.to_dict() if item else None, 'tombstoned': bool(tombstoned)})


def inspect(kb, *, offset=0, limit=100, seconds=2):
    limit=max(1,min(200,int(limit)));offset=max(0,int(offset))
    deadline=time.monotonic()+max(.01,min(2,float(seconds)))
    with kb._memory_write_lock, ExitStack() as stack:
        pending=kb._pending_v4_projections()
        known={str(x.get('id') or '') for x in kb.memories}
        native_only=[{'id':mid,'_native_only':True} for mid in sorted(pending-known)]
        targets=(kb.memories+native_only)[offset:offset+limit]
        total=len(kb.memories)+len(native_only)
        connections={};states={};issues=[];checked=0
        def issue(mid,code,**extra):issues.append({'id':mid,'code':code,**extra})
        for name in ('memory3.sqlite3','memory4.sqlite3'):
            path=Path(kb.base_dir)/name
            if not path.exists():states[name]='absent';continue
            try:
                db=stack.enter_context(closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)))
                db.row_factory=sqlite3.Row
                db.set_progress_handler(lambda: int(time.monotonic()>=deadline),1000)
                connections[name]=db;states[name]='read_only'
            except sqlite3.Error:states[name]='unavailable'
        for item in targets:
            if time.monotonic()>=deadline:break
            mid=str(item.get('id') or '');expected=record_version(item)
            try:
                db=connections.get('memory3.sqlite3')
                if db is None and not item.get('_native_only'):issue(mid,'legacy_store_unavailable')
                elif not item.get('_native_only'):
                    row=db.execute('select payload,is_deleted from memories where id=?',(mid,)).fetchone()
                    if not row or row['is_deleted']:issue(mid,'legacy_missing',content_version=expected)
                    elif record_version(json.loads(row['payload']))!=expected:issue(mid,'legacy_payload_mismatch',content_version=expected)
                    fts=db.execute('select title,content,tags,category from memory_fts where memory_id=?',(mid,)).fetchall()
                    values=(str(item.get('title') or ''),str(item.get('content') or ''),' '.join(str(x) for x in item.get('tags',[]) if x),str(item.get('category') or 'general'))
                    wanted=[] if item.get('is_archived') else [values]
                    if [tuple(x) for x in fts]!=wanted:issue(mid,'lexical_index_mismatch',content_version=expected)
                    if kb.settings.get('embedding_enabled',False) and not item.get('is_archived'):
                        provider=getattr(getattr(kb,'embedding_provider',None),'key',None)
                        vectors=db.execute('select content_hash from memory_embeddings where memory_id=?'+(' and provider_key=?' if provider else ''),
                            (mid,provider) if provider else (mid,)).fetchall()
                        if not vectors:issue(mid,'vector_not_generated')
                        elif any(x[0]!=kb._embedding_content_hash(item) for x in vectors):issue(mid,'vector_content_stale')
                db=connections.get('memory4.sqlite3')
                if db is not None:
                    from .memory_v4.store import MemoryV4Store
                    row=db.execute('select * from memory_items where id=?',(mid,)).fetchone()
                    tomb=db.execute('select 1 from memory_tombstones where id=?',(mid,)).fetchone()
                    native=MemoryV4Store._row_to_item(row) if row else None
                    if mid in pending:
                        issue(mid,'native_projection_pending',native_version=native_version(native,tomb))
                    elif tomb:issue(mid,'native_tombstone_conflict')
                    elif native is None:
                        from .memory_v4.migration import MemoryV4Migration
                        if not MemoryV4Migration._is_daily(item):
                            converter=MemoryV4Migration.__new__(MemoryV4Migration)
                            _,reason,_=converter._memory_item(item)
                            issue(mid,'native_migration_excluded' if reason else 'native_missing',**({'reason':reason} if reason else {}))
                    else:
                        from .memory_importance import projection
                        from .memory_origin import projection as origin_projection
                        different=(native.canonical_text!=str(item.get('content') or '').strip() or
                            abs(native.salience-float(item.get('importance_score',.4)))>1e-9 or
                            native.pinned!=bool(item.get('is_important')) or native.locked!=bool(item.get('is_locked')) or
                            projection(native.metadata)!=projection(item) or origin_projection(native.metadata)!=origin_projection(item) or
                            bool(native.lifecycle=='archived')!=bool(item.get('is_archived')))
                        if different:issue(mid,'native_projection_mismatch')
                elif kb.settings.get('memory_v4_write_enabled',False) or kb.settings.get('memory_v4_read_enabled',False):issue(mid,'native_store_unavailable')
                checked+=1
            except (sqlite3.Error,ValueError,TypeError,KeyError):
                issue(mid,'inspection_incomplete');break
        end=offset+checked
        return {'status':'attention' if issues else 'checked_window_consistent',
            'writes':0,'model_calls':0,'checked':checked,'total_records':total,
            'offset':offset,'next_offset':end if end<total else None,'window_complete':checked==len(targets),
            'pending_projection_count':len(pending),'stores':states,'issues':issues,
            'reverse_orphan_scan':'not_performed','budget_seconds':max(.01,min(2,float(seconds)))}


def repair_index(kb, memory_id, expected_version):
    """Rebuild one derivative lexical row from current JSON; never revive a fact."""
    with kb._memory_write_lock:
        item=kb._find_memory(memory_id);check_version(item,expected_version or '')
        if item is None or str(memory_id) in kb.deleted_memory_ids:raise ValueError('not_available')
        if str(memory_id) in kb._pending_v4_projections():raise MemoryVersionConflict('native_projection_pending')
        native=kb._open_memory_v4_store_for_read()
        if native is not None and native.is_tombstoned(str(memory_id)):raise MemoryVersionConflict('native_tombstoned')
        if not kb.sqlite_store or not kb.sqlite_store.available:raise OSError('legacy_store_unavailable')
        result=kb.sqlite_store.upsert_memories([item])
        if result.get('status')!='success':raise OSError('legacy_index_repair_failed')
        return {'status':'success','indexed':1,'fact_writes':0,'model_calls':0,'content_version':record_version(item)}


def reverse_inspect(kb, *, source='legacy', after_id='', limit=100, expected_version=None, seconds=2):
    """Keyset-paged reverse inventory: derived rows without an active JSON owner.

    No deletion/repair is implied. Native-only, pending, tombstone and recoverable
    cold-storage records are distinguished instead of treating all as garbage.
    """
    sources={'legacy':('memory3.sqlite3','memories','id'),
        'native':('memory4.sqlite3','memory_items','id'),
        'lexical':('memory3.sqlite3','memory_fts','memory_id'),
        'vectors':('memory3.sqlite3','memory_embeddings','memory_id')}
    if source not in sources or not isinstance(after_id,str) or len(after_id)>512:raise ValueError('invalid_inventory')
    limit=max(1,min(200,int(limit)));budget=max(.01,min(2,float(seconds)));deadline=time.monotonic()+budget
    with kb._memory_write_lock:
        known={str(x['id']) for x in kb.memories if x.get('id')}
        pending=kb._pending_v4_projections();deleted=kb.deleted_memory_ids
        fact_version=record_version({'items':{str(x['id']):record_version(x) for x in kb.memories if x.get('id')},
            'pending':sorted(pending),'deleted':deleted})
        name,table,column=sources[source];path=Path(kb.base_dir)/name
        def signature():
            return [(p.stat().st_size,p.stat().st_mtime_ns) if p.exists() and p.stat().st_size else None for p in (path,Path(str(path)+'-wal'))]
        original_signature=signature()
        version=record_version({'facts':fact_version,'source':source,'files':original_signature})
        if expected_version is not None and expected_version!=version:raise MemoryVersionConflict('inventory_changed')
        result={'source':source,'inventory_version':version,'fact_inventory_version':fact_version,'checked':0,'after_id':after_id,'next_after_id':None,
            'complete':False,'issues':[],'expected_non_active':{},'writes':0,'model_calls':0,'budget_seconds':budget}
        if not path.exists():return result|{'status':'store_absent'}
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
                db.row_factory=sqlite3.Row;db.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
                ids=db.execute(f'select {column} as id from {table} where {column}>? group by {column} order by {column} limit ?',
                    (after_id,limit+1)).fetchall()
                for row in ids[:limit]:
                    if time.monotonic()>=deadline:break
                    mid=row['id'];reason=None;normal=None
                    if mid in known:
                        result['checked']+=1;continue
                    if mid in pending:normal='pending_projection'
                    elif source=='legacy':
                        state=db.execute('select is_deleted from memories where id=?',(mid,)).fetchone()
                        if state and state[0]:normal='deleted_legacy_row'
                        elif deleted.get(mid,{}).get('reason')=='cold_archive':normal='recoverable_cold_archive'
                        else:reason='legacy_without_JSON_owner'
                    elif source=='native':
                        state=db.execute('select lifecycle from memory_items where id=?',(mid,)).fetchone()
                        tomb=db.execute('select 1 from memory_tombstones where id=?',(mid,)).fetchone()
                        if tomb:reason='native_tombstoned_item_present'
                        elif state[0]=='archived' and deleted.get(mid,{}).get('reason')=='cold_archive':normal='recoverable_cold_archive'
                        else:reason='native_without_JSON_projection'
                    else:reason=source+'_without_JSON_owner'
                    if reason:result['issues'].append({'id':mid,'code':reason})
                    if normal:result['expected_non_active'][normal]=result['expected_non_active'].get(normal,0)+1
                    result['checked']+=1
                if result['checked']:
                    last=ids[result['checked']-1]['id']
                    if len(ids)>result['checked']:result['next_after_id']=last
                result['complete']=result['checked']==len(ids) and len(ids)<=limit
                if result['checked']==limit and len(ids)==limit:result['complete']=True
                result['status']='attention' if result['issues'] else 'checked_window_consistent'
                if result['checked']<min(len(ids),limit):
                    result['status']='budget_reached';result['next_after_id']=ids[result['checked']-1]['id'] if result['checked'] else after_id
        except sqlite3.Error:result.update(status='inspection_incomplete',next_after_id=after_id,complete=False)
        if signature()!=original_signature:raise MemoryVersionConflict('derived_inventory_changed')
        return result
