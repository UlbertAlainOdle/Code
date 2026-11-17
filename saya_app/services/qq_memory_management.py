"""Trusted QQ management projection over the existing KnowledgeBase.

No new copy of memory content and no implicit publication of legacy memories.
This first QQ06 increment manages ACLs; production group recall stays closed
until prompt/commit/delivery revocation barriers are wired together.
"""
from dataclasses import asdict
import re
import time

from .qq_memory_access import (
    QQMemoryAccessStore, MemoryAccessDenied, MemoryAccessConflict, valid_id,
)
from .memory_v4.models import _contains_secret
from .memory_versions import record_version, CandidateStateSaveError
from .qq_memory_operations import QQMemoryOperations


class QQMemoryReceipt(str):
    """Only trusted management storage operations create these acknowledgments."""


def policy_content_revision(record):
    # SQLite signed integer, while browser CAS uses the complete SHA-256 above.
    return int(record_version(record)[:15], 16) + 1


class QQMemoryManagement:
    PAGE_SIZE = 10

    def __init__(self, bridge, kb):
        self.bridge, self.kb = bridge, kb
        self.access = QQMemoryAccessStore(bridge.store)
        self.history_manager = None
        self.operations=QQMemoryOperations(bridge.store,kb,self.authorize_operation)
        from .qq_memory_reference_dialogue import QQMemoryReferenceDialogue
        self.references=QQMemoryReferenceDialogue(self)
        self.console=None
        self.actions=None
        self.library=None
        self.overlap=None
        self.reads=None
        with bridge.store.connect() as db:
            db.execute('create table if not exists qq_memory_scope_epochs(scope text primary key, epoch text not null)')
            db.execute('create table if not exists qq_memory_history_floors(scope text primary key, floor text)')
            db.execute('create table if not exists qq_memory_recent(scope text primary key,session text,memory_id text,version text,created real)')

    def prepare_scope(self, scope, history):
        epoch = self.kb.memory_use.snapshot()
        floor = self.kb.memory_use.history_floor()
        with self.bridge.store.connect() as db:
            row = db.execute('select epoch from qq_memory_scope_epochs where scope=?', (scope,)).fetchone()
            floor_row=db.execute('select floor from qq_memory_history_floors where scope=?',(scope,)).fetchone()
            old = row[0] if row else '0'
            if old != epoch:
                if db.execute("select 1 from sqlite_master where type='table' and name='qq_action_proposals'").fetchone():
                    db.execute("update qq_action_proposals set state='superseded' where scope=? and state in ('prepared','pending')",(scope,))
                # Keep confirmed receipts, freeze only derived pending content.
                db.execute("insert or ignore into qq_delivery_stops select id,'memory_changed' from qq_deliveries where scope=? and kind='final'", (scope,))
                db.execute("update qq_delivery_parts set state='cancelled' where state='pending' and result_id in (select id from qq_deliveries where scope=? and kind='final')", (scope,))
        if floor and (not floor_row or floor_row[0]!=floor):history=[]
        return (list(history) if old == epoch else [line for line in history if line.startswith('Doctor: ')]), epoch

    def record_scope(self, scope, epoch):
        if epoch != self.kb.memory_use.snapshot():
            from .memory_use import MemoryUseChanged
            raise MemoryUseChanged('memory_context_changed')
        with self.bridge.store.connect() as db:
            db.execute('insert or replace into qq_memory_scope_epochs values(?,?)', (scope, epoch))
            db.execute('insert or replace into qq_memory_history_floors values(?,?)',(scope,self.kb.memory_use.history_floor()))

    def valid_delivery(self, scope, result_id, epoch):
        if epoch != self.kb.memory_use.snapshot():
            return False
        with self.bridge.store.connect() as db:
            row = db.execute('select d.kind,e.epoch from qq_deliveries d left join qq_delivery_memory_epochs e on e.result_id=d.id where d.id=? and d.scope=?', (result_id, scope)).fetchone()
        return row is not None and (row[0] != 'final' or (row[1] or '0') == epoch)

    def require_owner(self, actor):
        settings = self.bridge.settings
        if (not actor.accepted() or not actor.is_owner or actor.group_id or not settings
                or actor.bot_id != settings.bot_id or actor.user_id not in settings.owner_ids):
            raise MemoryAccessDenied('owner_private_management_required')

    def authorize_operation(self, actor, scope, session_id):
        self.require_owner(actor)
        settings=self.bridge.settings
        if not settings.shared_memory or not settings.memory_write_enabled or not settings.memory_management_enabled:
            raise MemoryAccessDenied('memory_management_readonly')
        if scope!=f'{actor.bot_id}:private:{actor.user_id}:shared' or self.bridge.store.conversation(scope)[1]!=session_id:
            raise MemoryAccessDenied('memory_topic_changed')

    def _record(self, memory_id):
        if not isinstance(memory_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', memory_id):
            raise ValueError('invalid_memory_id')
        record = self.kb._find_memory(memory_id)
        if record is None:
            raise LookupError('memory_not_found')
        return record

    def maintenance_review(self, actor, anchor_id, candidate_ids, *, limit=12, proposals=None):
        """Review a selected inventory of currently usable QQ-origin memories."""
        self.require_owner(actor)
        from .maintenance_review import memory_record, review_candidates
        from .memory_v4 import MemoryRetriever

        with self.kb._memory_write_lock:
            store = self.kb.memory_v4_store
            if store is None or not self.kb.settings.get('memory_v4_read_enabled'):
                return {'status': 'unavailable', 'anchor': None, 'candidates': []}
            reader = MemoryRetriever(store, permitted=self.kb.memory_allowed_for_use)
            return review_candidates(anchor_id, candidate_ids,
                                     lambda key: memory_record(self.kb, reader, actor, key, journal=self.bridge.store),
                                     limit=limit, proposals=proposals)

    def maintenance_preview(self, actor, anchor_id, candidate_id, *, anchor_version,
                            candidate_version, proposal=None):
        from .maintenance_review import source_preview

        review = self.maintenance_review(actor, anchor_id, [candidate_id],
                                         proposals={candidate_id: proposal})
        return source_preview(review, anchor_version, candidate_version)

    def maintenance_archive_preview(self, actor, session_id, keep, archive, *, keep_version, archive_version):
        """Check a selected pair without granting authority to archive it."""
        from .maintenance_archive import archive_preview

        return archive_preview(self.kb, self.maintenance_links(actor, session_id), self.access, keep, archive,
                               keep_version=keep_version, archive_version=archive_version)

    def maintenance_archive(self, actor, session_id):
        from .maintenance_archive import MaintenanceArchive

        return MaintenanceArchive(self.kb, self.maintenance_links(actor, session_id), self.access)

    def maintenance_links(self, actor, session_id, *, native_consent=None):
        """Bind confirmation and lookup to the existing owner management scope."""
        from .maintenance_links import MaintenanceLinks
        from .maintenance_review import MAX_SOURCES, memory_record
        from .memory_v4 import MemoryRetriever

        qq_scope = f'{actor.bot_id}:private:{actor.user_id}:shared'

        def authorize(write, members):
            if write:
                self.authorize_operation(actor, qq_scope, session_id)
            else:
                self.require_owner(actor)
                if not self.bridge.settings.shared_memory:
                    raise MemoryAccessDenied('memory_management_readonly')

        def load(identifier):
            store = self.kb.memory_v4_store
            if store is None or not self.kb.settings.get('memory_v4_read_enabled'):
                return None
            reader = MemoryRetriever(store, permitted=self.kb.memory_allowed_for_use)
            return memory_record(self.kb, reader, actor, identifier, protect=True, journal=self.bridge.store)

        return MaintenanceLinks(
            self.bridge.store, scope=('memory', actor.bot_id, actor.user_id), actor=actor.user_id,
            topic=session_id, clock=time.time, retention=lambda: self.bridge.settings.retention_days * 86400,
            lock=self.kb._memory_write_lock, authorize=authorize, load=load,
            fence=self.kb.memory_use.snapshot, source_limit=lambda: MAX_SOURCES,
            native_consent=native_consent,
        )

    def browse(self, actor, *, kind='saved', query='', page=1, snapshot='',usable_only=False):
        self.require_owner(actor)
        if kind not in {'saved', 'candidates'} or type(page) is not int or not 1 <= page <= 10000:
            raise ValueError('invalid_memory_page')
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('invalid_memory_query')
        with self.kb._memory_write_lock:
            records = (self.kb.get_memories(include_archived=True) if kind == 'saved' else
                [x for x in self.kb.memory_candidates if x.get('status') == 'pending'])
            if usable_only:records=[r for r in records if self.kb.memory_allowed_for_use(r) and not r.get('is_archived')
                and not self.kb._is_expired(r) and str(r.get('status') or 'active') in {'active','valid'}]
            records = sorted((r for r in records if query.casefold() in
                (str(r.get('title') or '') + '\n' + str(r.get('content') or '')).casefold()),
                key=lambda r: str(r.get('id') or ''))
            revision = record_version({'kind': kind, 'query': query,
                'records': [(str(r['id']), record_version(r)) for r in records]})
            if snapshot and snapshot != revision:
                raise MemoryAccessConflict('catalog_changed')
            start = (page - 1) * self.PAGE_SIZE
            items = [{'id': str(r['id']), 'title': str(r.get('title') or '')[:160],
                'preview': str(r.get('content') or '')[:180],
                'status': 'archived' if r.get('is_archived') else str(r.get('status') or 'active'),
                'source': str(r.get('source') or ''), 'content_version': record_version(r)}
                for r in records[start:start + self.PAGE_SIZE]]
            return {'kind': kind, 'items': items, 'page': page, 'page_size': self.PAGE_SIZE,
                'total': len(records), 'snapshot': revision,
                'has_next': start + self.PAGE_SIZE < len(records)}

    def detail(self, actor, memory_id):
        self.require_owner(actor)
        from .memory_importance import view as importance_view
        with self.kb._memory_write_lock:
            record = self._record(memory_id)
            policy = self.access.get(actor.bot_id, memory_id)
            return {'memory': {k: record.get(k) for k in ('id', 'title', 'content', 'source',
                'source_detail', 'status', 'is_archived', 'is_locked', 'managed_slot', 'sensitivity',
                'tags', 'importance_score', 'is_important', 'memory_layer',
                'origin', 'context_events', 'last_modified_channel', 'last_modified_origin')}
                | {'memory_tier': self.kb._memory_tier(record),
                   'base_priority_score': self.kb.memory_priority_score(record)}
                | importance_view(record),
                'content_version': record_version(record),
                'policy': asdict(policy) if policy else None,
                'policy_current': bool(policy and policy.memory_revision == policy_content_revision(record)),
                'authorized_groups': sorted(self.bridge.effective_groups()),
                'use_allowed':self.kb.memory_allowed_for_use(record),
                'group_recall_available':getattr(self.bridge,'group_context',None) is not None,
                'group_recall_enabled': False}

    def set_access(self, actor, memory_id, *, content_version, expected_revision,
            subject_id, visibility='private', groups=(), requesters=(), writers=(), confirmed=False):
        self.require_owner(actor)
        if confirmed is not True:
            raise MemoryAccessDenied('explicit_confirmation_required')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('invalid_policy_revision')
        groups, requesters, writers = tuple(groups), tuple(requesters), tuple(writers)
        if (any(not valid_id(x) for x in groups + writers)
                or any(x != '*' and not valid_id(x) for x in requesters)
                or max(len(groups), len(requesters), len(writers)) > 128):
            raise ValueError('invalid_principal')
        if not set(groups) <= self.bridge.effective_groups():
            raise MemoryAccessDenied('group_not_authorized')
        if visibility != 'groups' and groups:
            raise ValueError('groups_only_for_group_audience')
        with self.kb._memory_write_lock:
            self.require_owner(actor)
            record = self._record(memory_id)
            if record_version(record) != content_version:
                raise MemoryAccessConflict('memory_content_changed')
            publishing = visibility != 'private' or bool(requesters or writers)
            sensitivity = record.get('sensitivity', 'normal')
            secret = _contains_secret(str(record.get('title') or '') + '\n' + str(record.get('content') or ''))
            secret = secret or bool(re.search(r'密码|密钥|私钥|访问令牌|凭据', str(record.get('content') or '')))
            if publishing and (not self.kb.memory_allowed_for_use(record) or secret or sensitivity not in {'normal', 'personal', 'sensitive'}
                    or record.get('is_archived') or self.kb._is_expired(record)
                    or str(record.get('status') or 'active') not in {'active', 'valid'}):
                raise MemoryAccessDenied('record_not_publishable')
            self.access.set(actor, memory_id, policy_content_revision(record), subject_id,
                expected_revision=expected_revision, visibility=visibility, groups=groups,
                requesters=requesters, writers=writers)
            return self.detail(actor, memory_id)

    def recall_audit(self, actor, session_id):
        self.require_owner(actor)
        scope = f'{actor.bot_id}:private:{actor.user_id}:shared'
        if self.history_manager is None or self.bridge.store.conversation(scope)[1] != session_id:
            return '当前话题没有可核对的记忆召回审计。'
        entries = self.history_manager.load_session_full(session_id, make_current=False) or []
        entries = self.kb.memory_use.project_entries(entries)
        latest = next((x for x in reversed(entries) if x.get('role') in {'ai','assistant'}), None)
        audit = latest.get('memory_recall') if latest else None
        if not isinstance(audit, dict):
            return '上一轮没有可核对的记忆召回审计，不能据此推测用了哪些记忆。'
        selected = []
        for item in (audit.get('selected') or [])[:30]:
            memory = self.kb._find_memory(str(item.get('memory_id') or item.get('id') or ''))
            if memory is not None and self.kb.memory_allowed_for_use(memory):
                selected.append(f"{str(memory.get('title') or memory['id'])[:100]} · 来源：{memory.get('source','未知')} · {str(item.get('reason') or '已选入提示')[:80]}")
                if isinstance(item.get('base_score'),(int,float)):
                    selected[-1]+=f" · 基础分{item['base_score']:.6f} · QQ来源贡献{float(item.get('origin_contribution') or 0):.6f}（本轮查询视图，不写回重要级）"
        return ('上一轮实际送入提示的记忆（这不证明模型逐条使用）：\n' + '\n'.join(selected)
            if selected else '上一轮没有可列出的当前有效记忆。') + f"\n审计状态：{str(audit.get('status') or '未知')[:80]}。"

    def candidate_proposal(self, actor, candidate_id):
        self.require_owner(actor)
        with self.kb._memory_write_lock:
            candidate = self.kb._find_memory_candidate(candidate_id)
            if candidate is None or candidate.get('status') != 'pending':return None
            content=' '.join(str(candidate.get('content') or candidate.get('title') or '这条信息').split())
            preview=content[:160]+('（摘要）' if len(content)>160 else '')
            return {'candidate_id':candidate_id, 'candidate_version':record_version(candidate)}, preview

    def revise_candidate(self,actor,candidate_id,version,content):
        if not isinstance(version,str) or not re.fullmatch(r'[0-9a-f]{64}',version):raise MemoryAccessConflict('candidate_version_required')
        def authorize():
            self.require_owner(actor)
            if not self.bridge.settings.shared_memory or not self.bridge.settings.memory_write_enabled:raise MemoryAccessDenied('memory_readonly')
        authorize()
        candidate=self.kb.update_memory_candidate(candidate_id,{'content':content},expected_version=version,write_check=authorize)
        return self.candidate_proposal(actor,candidate_id) if candidate else None

    def confirm_candidate(self, actor, candidate_id, version,session_id='',*,reference_check=None):
        outcome=self.commit_candidate(actor,candidate_id,version,reference_check=reference_check)
        if outcome.get('candidate_status_pending'):
            return '长期记忆已保存，但候选状态待核对。'
        if outcome['state']=='committed':
            return self.saved_receipt(actor,outcome['memory_id'],session_id)
        if outcome['state']=='unknown':
            return '候选状态未保存，请重新核对。'
        return '候选已处理或内容已存在，本次没有新增长期记忆。'

    def commit_candidate(self, actor, candidate_id, version, *, reference_check=None):
        def authorize():
            self.require_owner(actor)
            candidate=self.kb._find_memory_candidate(candidate_id)
            from .memory_reference import unresolved_reference
            if candidate and unresolved_reference(candidate.get('content')):raise MemoryAccessDenied('candidate_content_unresolved')
            origin=(candidate or {}).get('origin',{})
            p=getattr(getattr(self.bridge,'episodes',None),'provenance',None)
            if p and p.active() and origin.get('channel')=='qq':
                import json
                from types import SimpleNamespace
                if origin.get('verified_subject')!=actor.user_id or origin.get('bot_id')!=actor.bot_id or origin.get('visibility',{}).get('kind')!='private':raise MemoryAccessDenied('candidate_source_scope_changed')
                with self.bridge.store.connect() as db:
                    row=db.execute('select tag from qq_event_message_dependencies where event=? and expires>?',
                        (f'{actor.bot_id}:private:{actor.user_id}:message:'+str(origin.get('message_id')),time.time())).fetchone()
                message=SimpleNamespace(bot_id=actor.bot_id,user_id=actor.user_id,group_id='')
                if not row or not p.valid(message,json.loads(row[0])):raise MemoryAccessDenied('candidate_source_unavailable')
            if reference_check and not reference_check():raise MemoryAccessDenied('reference_source_changed')
            if not self.bridge.settings.shared_memory or not self.bridge.settings.memory_write_enabled:
                raise MemoryAccessDenied('memory_readonly')
        authorize()
        try:
            result = self.kb.confirm_memory_candidate(candidate_id, expected_version=version, write_check=authorize)
        except CandidateStateSaveError as error:
            if not error.committed_memory_id:return {'state':'unknown'}
            memory=self.kb._find_memory(error.committed_memory_id)
            return {'state':'committed','memory_id':error.committed_memory_id,
                'version':record_version(memory) if memory else None,'candidate_status_pending':True}
        if result and result.get('memory'):
            memory=result['memory']
            return {'state':'committed','memory_id':memory['id'],'version':record_version(memory)}
        return {'state':'not_saved'}

    def saved_receipt(self,actor,memory_id,session_id,*,confirmed=True,index=True):
        self.require_owner(actor)
        memory=self._record(memory_id)
        scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
        if index:
            with self.bridge.store.connect() as db:
                db.execute('insert or replace into qq_memory_recent values(?,?,?,?,?)',
                    (scope,session_id,memory['id'],record_version(memory),time.time()))
        text=' '.join(str(memory.get('content') or '').split())
        return ('已确认并保存长期记忆：' if confirmed else '已保存长期记忆：')+text[:100]+('（摘要；可说“展开刚记的那条”）' if len(text)>100 else '')

    def natural_response(self,actor,text,session_id):
        text=text.strip().rstrip('？?。！!')
        from .memory_reference import save_reference
        reference=save_reference(text) or ('ambiguous' if text=='记住' else None)
        if reference:
            self.require_owner(actor)
            scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
            proposals=self.bridge.action_proposals
            if not self.bridge.settings.memory_write_enabled:return '长期记忆保存已关闭，本次没有保存。'
            with self.bridge.store.connect() as db:
                row=db.execute("select * from qq_action_proposals where scope=? and session_id=? and kind='save_candidate' and state in ('pending','clarifying') and expires>? order by rowid desc limit 1",(scope,session_id,time.time())).fetchone()
            if reference=='candidate' and row and row['revision']==self.bridge.controls()['revision']:
                import json
                payload=json.loads(row['payload']);current=self.candidate_proposal(actor,payload['candidate_id'])
                if current and proposals:
                    data,preview=current
                    return proposals.ask(scope,session_id,'save_candidate',data,
                        f'待保存候选预览：{preview}。确认保存请回复“记住”；本次尚未保存。',self.bridge.controls()['revision'])
            if row:
                with self.bridge.store.connect() as db:db.execute("update qq_action_proposals set state='clarifying' where id=? and state='pending'",(row['id'],))
                return '你指的是待确认的候选记忆，还是我随后那段解释？可以说“记住刚才那条候选”，或说明要保存的实际内容。本次尚未保存。'
            return '这句指向上文，但当前没有可直接确认的保存目标。请说明要保存哪段实际内容，本次尚未保存。'
        catalog=text in {'你记得我哪些事','你记住了我哪些事','你记得我什么','你有哪些长期记忆','你现在对我有哪些长期记忆','你对我有哪些长期记忆','你记住了我什么','你现在记得我什么'}
        detail=text in {'展开刚记的那条','刚才记住的那条具体是什么','你记住的那条具体是什么','刚才记下了什么'}
        why=text in {'刚才用了哪些记忆','你刚才用了哪些记忆','上一轮用了哪些记忆'}
        if not (catalog or detail or why):return None
        self.require_owner(actor)
        if why:return self.recall_audit(actor,session_id)
        if catalog:
            data=self.browse(actor,usable_only=True)
            return ('当前可用的已保存长期记忆：\n'+ ('\n'.join(f"{x['title']}：{x['preview']}" for x in data['items']) or '暂无。')
                + (f"\n先列出{len(data['items'])}条，共{data['total']}条；更多可用 /memory list 查看。" if data['has_next'] else ''))
        scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
        with self.bridge.store.connect() as db:
            row=db.execute('select memory_id,version from qq_memory_recent where scope=? and session=? and created>?',
                (scope,session_id,time.time()-86400)).fetchone()
        if not row:return '当前话题没有可核对的最近保存条目；可以询问长期记忆清单。'
        try:record=self._record(row[0])
        except LookupError:return '那条记录现在已不可用，不会猜测旧内容。'
        if not self.kb.memory_allowed_for_use(record):return '那条记忆现在已停用或来源失效，不会将旧内容重新带回聊天。'
        changed=record_version(record)!=row[1]
        return ('这条后来已更新，当前内容是：' if changed else '实际保存的内容是：')+str(record.get('content') or '')[:1600]

    def revision_response(self, actor, text, session_id, *, quoted=False):
        from .memory_revisions import revision_service, revision_request, revision_confirmation
        request = revision_request(text)
        fields = text.split(maxsplit=4)
        command = fields[1].lower() if len(fields)>1 and fields[0].lower()=='/memory' else ''
        consent=revision_confirmation(text)
        if consent is not None:
            command='revision-confirm';fields=['/memory',command,consent['token'] or '']
        if request is None and command not in {'append','correct','preview-correct','revision-confirm','history','restore'}:
            return None
        self.require_owner(actor)
        if consent is not None and consent['token'] is None:
            return QQMemoryReceipt('确认码格式不完整，本次没有修改。请直接复制预览末尾的“确认修改”和完整确认码；确认码不是记忆编号。')
        if quoted:
            return QQMemoryReceipt('引用或附件不能作为记忆修改许可；请直接说明要修改的事实。')
        if request and request.get('requires_direct'):
            message='这条是带外层引号的操作说明，尚未修改，也没有另存候选。请直接发送操作正文，不带外层引号或“发送”：'
            return QQMemoryReceipt(message+'\n'+request['direct_text'] if request['direct_text'] else '这条引用未作为记忆修改许可，本次没有修改或另存候选。')
        scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
        service=revision_service(self.kb)
        if command=='history':
            if len(fields)!=3:return QQMemoryReceipt('查看最近修订：/memory history 编号。')
            result=service.history(fields[2],subject_id=actor.user_id)
            lines=[f"版本{x['revision']}：{str(x['content'] or '')[:240]}" for x in result['items']]
            return QQMemoryReceipt('最近修改历史（最多20项）：\n'+'\n'.join(lines))
        if not self.bridge.settings.memory_management_enabled:
            return QQMemoryReceipt('QQ记忆修改管理尚未开启，本次没有更新；原记忆和保存候选保持。')
        authorize=lambda:self.authorize_operation(actor,scope,session_id)
        authorize()
        try:
            if command=='revision-confirm':
                result=service.confirm(fields[2] if len(fields)==3 else '',scope=scope,session_id=session_id,authorize=authorize)
            else:
                direct=False
                if command in {'append','correct','preview-correct'}:
                    if len(fields) != 5:
                        return QQMemoryReceipt(f'格式：/memory {command} 完整编号 内容版本 正文。先用 /memory show 查看条目。')
                    try:
                        target = self._record(fields[2])
                    except LookupError:
                        return QQMemoryReceipt('找不到该记忆编号。本次没有修改。请复制完整编号，包括原有的 memory: 前缀，再核对内容版本。')
                    version, content = fields[3], fields[4]
                    operation = 'correct' if command == 'preview-correct' else command
                    direct = command != 'preview-correct'
                elif command=='restore':
                    if len(fields)!=4:return QQMemoryReceipt('恢复预览：/memory restore 编号 旧版本号；恢复也会形成新修订。')
                    target=service.record(fields[2],subject_id=actor.user_id)
                    history=service.history(fields[2],subject_id=actor.user_id)
                    selected=next((x for x in history['items'] if str(x['revision'])==fields[3]),None)
                    if not selected:return QQMemoryReceipt('旧版本不可用，请先查看最近修改历史。')
                    version=record_version(target);operation='restore';content=selected['content']
                else:
                    if not request['safe']:
                        return QQMemoryReceipt('本次没有修改记忆；假设、引用、他人陈述、敏感内容或不保存要求不作为修改许可。')
                    with self.bridge.store.connect() as db:
                        recent=db.execute('select memory_id from qq_memory_recent where scope=? and session=? and created>?',
                            (scope,session_id,time.time()-86400)).fetchone()
                    targets=service.targets(request['selector'],request['content'],recent_id=recent[0] if recent else None,subject_id=actor.user_id)
                    if not targets:return QQMemoryReceipt('没有找到可唯一修改的已保存事实；请用 /memory search 关键词 查找，不会据此新建重复记忆。')
                    if len(targets)>1:
                        return QQMemoryReceipt('目标不唯一，本次没有修改。可选条目（最多3项）：\n'+'\n'.join(
                            f"{x['id']}：{str(x.get('content') or '')[:120]}" for x in targets[:3])+'\n请用 /memory show 编号 核对目标及版本。')
                    target=targets[0];version=record_version(target);operation=request['operation'];content=request['content']
                    from .memory_rules import fact_terms
                    direct=operation=='correct' and bool(request['selector']) and (
                        bool(re.search(r'→|=>',content)) or len(fact_terms(content)&fact_terms(target.get('content')))>=2)
                if not self.actions and not direct and len(str(target.get('content') or ''))+len(str(content))>2400:
                    return QQMemoryReceipt('完整差异较长，请在网页记忆条目中预览修改，本次没有提交。')
                result=service.preview(target['id'],operation,content,expected_version=version,
                    scope=('qq-console:'+scope if self.actions else scope),session_id=session_id,authorize=authorize,subject_id=actor.user_id)
                if self.actions and result['status']=='preview':
                    return self.actions.register_revision(actor,session_id,result)
                if direct and result['status']=='preview':
                    result=service.confirm(result['confirmation_token'],scope=scope,session_id=session_id,authorize=authorize)
            if result['status']=='preview':
                return QQMemoryReceipt(f"记忆修改预览（尚未提交）\n原内容：{result['old']}\n新内容：{result['new']}\n如需提交，请回复：确认修改 {result['confirmation_token']}")
            if result['status']=='applied':
                self.saved_receipt(actor,result['memory_id'],session_id,confirmed=False)
                return QQMemoryReceipt('已更新同一条长期记忆：'+str(result['content'])[:1200]+f"\n编号：{result['memory_id']}；原创建来源保持。")
            if result['status']=='noop':return QQMemoryReceipt('这些信息已在这条记忆中，本次没有重复保存或新增修订。')
            if result['status']=='partial':return QQMemoryReceipt('修改或投影状态待核对，请重新读取同一条记忆，勿重复保存。')
            return QQMemoryReceipt('本次没有再次修改：预览已处理、过期、失效或结果待核对，请重新读取并预览。')
        except ValueError as error:
            if str(error)=='correction_fragment_missing':
                return QQMemoryReceipt('已保存记忆中还没有找到要更正的原片段，本次没有修改。前一步补充可能尚未确认；请先生成并确认补充预览，再更正。')
            if str(error)=='correction_fragment_not_unique':
                return QQMemoryReceipt('原片段在这条记忆中出现多次，本次没有修改；请提供更完整且唯一的旧片段。')
            return QQMemoryReceipt('目标受保护、主体/范围不符或版本已变化，本次没有确认更新；请重新读取并预览。')

    def command(self, actor, text, session_id=''):
        self.require_owner(actor)
        revision=self.revision_response(actor,text,session_id)
        if revision is not None:return revision
        args = text.split(maxsplit=2)
        action = args[1].lower() if len(args) > 1 else 'help'
        if action == 'result' and len(args) == 3:
            return self.references.result(actor,session_id,args[2],across_topics=True,
                missing='当前账号下没有可核对的保存操作，请核对原操作编号及保留期限。')
        if action in {'edit','delete','block','unblock','confirm'}:
            if not self.bridge.settings.memory_management_enabled:
                return 'QQ记忆修改管理尚未开启，请在网页QQ接入中开启；浏览和召回仍可用。'
            if not self.bridge.settings.memory_write_enabled:
                return '保存记忆已关闭，当前只读；本次没有修改、删除或改变使用规则。'
            scope=f'{actor.bot_id}:private:{actor.user_id}:shared'
            self.authorize_operation(actor,scope,session_id)
            if action=='confirm' and len(args)==3:
                result=self.operations.confirm(actor,scope,session_id,args[2])
                if result.get('operation_state')=='done':
                    title={'delete':'已删除这条长期记忆。','block':'已停止使用这条记忆及其已知派生信息，接下来从新话题继续。',
                        'unblock':'已恢复这条记忆的使用；先前排除的历史仍保留在存档中。'}[result['action']]
                    if result.get('status')=='partial':title+='策略已生效，记忆显示状态尚待核对。'
                    return QQMemoryReceipt(title+'原聊天文件未删除。')
                if result.get('status') in {'restart_invalidated','expired','stale'}:
                    cause={'restart_invalidated':'服务已重启，旧确认已作废',
                           'expired':'确认已过期','stale':'条目内容版本已变化'}[result['status']]
                    action=result.get('action');identifier=result.get('memory_id')
                    if action in {'delete','block','unblock'} and identifier:
                        return QQMemoryReceipt(cause+'，本次未执行。请先用 /memory show '+identifier+
                            ' 核对，再发送 /memory '+action+' '+identifier+' 取得新预览；核对后使用新确认码。')
                if result.get('status') in {'unknown','executing'}:
                    return QQMemoryReceipt('上次操作结果待核对，本次不会自动重试。请先用 /memory show '+
                        str(result.get('memory_id') or '')+' 核对当前状态，勿重复提交。')
                if result.get('status')=='done':
                    return QQMemoryReceipt('这条确认对应的操作已处理，本次没有重复执行；请重新查看条目核对当前状态。')
                return '本次没有再次执行：确认已处理、过期或内容已变化，请重新查看条目。'
            if action=='edit' and len(args)==3:
                edit=args[2].split(maxsplit=2)
                if len(edit)!=3:return '修改格式：/memory edit 编号 内容版本 新正文。先用 /memory show 查看版本。'
                memory=self._record(edit[0])
                if memory.get('managed_slot') or memory.get('source')=='assistant_role':return '这条是专门管理的资料，请使用原网页资料入口修改。'
                if not 1<=len(edit[2])<=12000:raise ValueError('invalid_memory_content')
                if self.actions:
                    from .memory_revisions import revision_service
                    result=revision_service(self.kb).preview(edit[0],'correct',edit[2],expected_version=edit[1],
                        scope='qq-console:'+scope,session_id=session_id,
                        authorize=lambda:self.authorize_operation(actor,scope,session_id),subject_id=actor.user_id)
                    if result['status']=='preview':return self.actions.register_revision(actor,session_id,result)
                    return QQMemoryReceipt('这次没有生成待修改内容，请在记忆操作台重新核对原条目。')
                saved=self.kb.update_memory(edit[0],{'content':edit[2]},expected_version=edit[1],
                    write_check=lambda:self.authorize_operation(actor,scope,session_id))
                if not saved:return '修改未保存成功，请重新读取核对。'
                return QQMemoryReceipt('本次长期记忆修改已提交。旧回答不能续读，请重新读取当前版本。')
            if action in {'delete','block','unblock'} and len(args)==3:
                fields=args[2].split();memory=self._record(fields[0])
                if len(fields)>2:raise ValueError('invalid_memory_operation')
                version=fields[1] if len(fields)==2 else record_version(memory)
                result=self.operations.preview(actor,scope,session_id,fields[0],version,action=action)
                if result.get('status')=='blocked':return '这条记忆受保护，未执行操作，请使用原管理入口查看。'
                if self.actions:return self.actions.register_operation(actor,session_id,action,fields[0],result)
                return f"请核对：{str(memory.get('title') or memory['id'])[:120]}。\n{result['message']}\n十分钟内确认：/memory confirm {result['confirmation_token']}"
            return '请指定条目；/memory show 编号 查看内容及版本。'
        if action == 'why':
            return self.recall_audit(actor, session_id)
        if action == 'search':
            data = self.browse(actor, query=args[2] if len(args)>2 else '')
            return '已保存记忆搜索：\n' + ('\n'.join(f"{x['id']} · {x['title']}" for x in data['items']) or '没有匹配条目。')
        if action == 'list':
            page = int(args[2]) if len(args) > 2 else 1
            data = self.browse(actor, page=page)
            return (f"已保存记忆 · 第{data['page']}页 · 共{data['total']}条：\n" +
                ('\n'.join(f"{x['id']} · {x['title']} [{x['status']}]" for x in data['items']) or '暂无条目。') +
                '\n详情：/memory show 编号；待确认候选：/memories。')
        if action == 'show' and len(args) == 3:
            d = self.detail(actor, args[2]); m = d['memory']; p = d['policy']
            tier = {'about_you':'个人资料档', 'curated':'整理档', 'auto':'自动档'}.get(m['memory_tier'], m['memory_tier'])
            origin = m.get('origin') or {}
            origin_label = {'qq':'QQ（已验证）', 'web':'网页', 'unknown':'未知'}.get(origin.get('channel'), '未知')
            events = m.get('context_events') or []
            provenance = f"\n创建通道：{origin_label}；最近修改通道：{m.get('last_modified_channel') or '未知'}。"
            grade_label={'ordinary':'普通','attention':'关注','important':'重要','critical':'关键'}[m['importance_grade']]
            provenance+=f"\n重要级：{grade_label}（{'已初始化' if m['importance_grade_initialized'] else '按现值预览，未迁移'}）；重要性锁定：{'是' if m['importance_locked'] else '否'}。"
            if events:
                provenance += '\n最近来源事件（仅记录当句证据）：' + str(events[-1].get('summary') or events[-1].get('reason') or '')[:240]
            return (f"{m['title']}\n{str(m['content'] or '')[:1600]}\n来源：{m['source']}；" +
                ('权限尚未明确授权。' if not p else '权限：' + p['visibility'] +
                    ('；当前内容匹配。' if d['policy_current'] else '；内容已变，原授权失效。')) +
                f"\n当前归档：{tier}；重要性权重：{float(m['importance_score'] or 0):.2f}；无查询优先分：{m['base_priority_score']:.2f}。"
                f"\n置顶星标：{'是' if m['is_important'] else '否'}；锁定：{'是' if m['is_locked'] else '否'}。"
                + provenance +
                '\n以上为网页兼容评分；实际召回还受相关性、时效和权限限制。'
                f"\n自动使用：{'启用' if d['use_allowed'] else '停用或来源失效'}。\n内容版本：{d['content_version']}\n逐条权限请在网页QQ接入中确认；群内使用还需对应群开关与本条授权。")
        return ('已保存记忆：/memory list [页码]；搜索：/memory search 文字；详情：/memory show 编号；历史：/memory history 编号；保存结果：/memory result 操作编号；上一轮召回依据：/memory why。待确认候选：/memories。'
            + '\n自然补充：补充条目关键词：新增细节（先预览）；明确更正：更正条目关键词：新正文或旧片段→新片段（唯一目标才执行）。'
            + ('\n更正预览：/memory preview-correct 完整编号 内容版本 新正文。' if self.bridge.settings.memory_management_enabled else '')
            + ('\n修改：/memory edit 编号 内容版本 新正文；删除：/memory delete 编号；停用：/memory block 编号；恢复：/memory unblock 编号。后三项需核对预览后 /memory confirm 确认码；保存记忆关闭时只读。'
                if self.bridge.settings.memory_management_enabled else '\n修改管理默认关闭，可在网页QQ接入中开启。'))
