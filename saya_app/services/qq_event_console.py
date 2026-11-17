"""Local owner consent for original private QQ event-operation proposals.

No fabricated transport receipt or inbound message. Native approvals are
explicitly identified in the original atomic operation audit.
"""
from dataclasses import replace
import json
import threading
from uuid import uuid4

from .memory_write_operations import fingerprint
from .qq_bridge import QQMessage
from .qq_responses import ConsoleNotice


class QQEventConsole:
    def __init__(self,operations):
        self.o=operations;self.s=operations.s;self.b=self.s.bridge;self.generation=uuid4().hex;self.lock=threading.RLock()

    def owner(self,owner):
        settings=self.b.settings
        if not settings or owner not in settings.owner_ids:raise PermissionError('native_event_owner_required')
        return settings

    def eligible(self,m):
        return bool(self.b.settings.enabled and self.b.settings.shared_memory
                    and m.user_id in self.b.settings.owner_ids and m.shared and not m.group_id and m.kind=='message'
                    and not m.reply_to and not m.forward_present and not m.media_types and not getattr(m,'quote_context',None))

    def bind(self,m,data):
        # Called only while preparing the host-selected original QQ request.
        with self.o.store.connect() as db:
            row=db.execute('select scope,payload from inbox where event_key=?',(m.key,)).fetchone()
        raw=json.loads(row['payload']) if row else {}
        if not row or row['scope']!=m.scope or any(raw.get(k)!=getattr(m,k) for k in ('bot_id','user_id','message_id','text','shared')):
            raise ValueError('native_event_original_ingress_required')
        data['preview_channel']='native'
        data['native_actor']={k:getattr(m,k) for k in ('scope','bot_id','user_id','group_id','message_id','shared','sent_at')}

    def command(self,m):
        """Explicit owner-private single-card actions reuse the original batch writer."""
        if not self.eligible(m):return None
        words=m.text.split()
        from .qq_event_console_controls import control_spec
        try:control=control_spec(self,m)
        except ValueError as error:return ConsoleNotice(str(error)+'；没有修改事件，请在后台核对。')
        lifecycle=len(words)==3 and words[0]=='/events' and words[1] in {'stop','pause','resume','cancel','complete','delete'}
        if not lifecycle and control is None:return None
        inventory=self.o.inventory(m)
        if not any(x['kind']=='personal' and x['card']['id']==words[2] for x in inventory):
            return ConsoleNotice('目前没有找到可操作的本人事件，请在后台核对当前条目。')
        plan={'action':control['action'] if control else words[1],'scope':'personal','quantifier':'one','target_ids':[words[2]]}
        return self.o.propose(m,plan,inventory,m.text,native_control=control)

    def row(self,owner,identifier):
        settings=self.owner(owner)
        with self.o.store.connect() as db:
            self.o.prune(db)
            row=db.execute('select * from qq_event_operation_sessions where id=? and bot=? and actor=? and scope=?',
                (identifier,settings.bot_id,owner,f'{settings.bot_id}:private:{owner}:shared')).fetchone()
        if not row:raise LookupError('native_event_unavailable')
        data=json.loads(row['data'])
        if data.get('preview_channel')!='native':raise LookupError('native_event_unavailable')
        return dict(row),data

    def message(self,row,data):
        m=QQMessage(key='native-console:event:'+row['id'],text=data['original_request'],**data['native_actor'])
        if not self.eligible(m) or m.bot_id!=self.b.settings.bot_id or self.o.scope(m)!=row['scope']:
            raise PermissionError('native_event_identity_changed')
        return m

    def listing(self,owner):
        settings=self.owner(owner)
        with self.o.store.connect() as db:
            self.o.prune(db)
            rows=db.execute("select id,state,expires,data from qq_event_operation_sessions where bot=? and actor=? and scope=? "
                "and json_extract(data,'$.preview_channel')='native' order by rowid desc limit 150",
                (settings.bot_id,owner,f'{settings.bot_id}:private:{owner}:shared')).fetchall()
        return {'available':True,'entries':[{'id':r['id'],'state':r['state'],'expires':r['expires'],
            'action':json.loads(r['data'])['plan']['action'],'scope':json.loads(r['data'])['plan']['scope']} for r in rows]}

    async def refresh_groups(self,owner,identifier):
        row,data=self.row(owner,identifier)
        if row['state']!='native_prepared':return
        m=self.message(row,data)
        for group in data['groups']:await self.o.u.prepare(replace(m,text='/events group '+group))

    def view(self,row,data):
        result={'id':row['id'],'state':row['state'],'expires':row['expires'],'action':data['plan']['action'],
                'scope':data['plan']['scope'],'targets':[]}
        if row['state']!='native_prepared':
            with self.o.store.connect() as db:
                saved=db.execute('select state,data from qq_event_operation_results where session=? order by at desc limit 1',(row['id'],)).fetchone()
            if saved:
                audit=json.loads(saved['data'])
                # The original private audit retains preview samples. A
                # terminal console receipt must never revive those sources
                # after their expiry, stop, or a later history boundary.
                result['result']={'state':saved['state'],'action':audit['action'],'scope':audit['scope'],
                    'changes':[{'kind':x['kind'],'id':x['id']} for x in audit['changes']],
                    'pending_source_count':(audit.get('pending_dialogue') or {}).get('count',0)}
            return result
        m=self.message(row,data)
        try:
            if not self.b.settings.enabled or not self.o.u.active() or not self.s.people.active():raise ValueError('disabled')
            inventory=self.o.inventory(m,data['groups'],refresh=False);selected=self.o.select(data['plan'],inventory)
            if self.o.snapshot(m,selected)!=data['snapshot'] or not self.o.dialogue_valid(m,data):raise ValueError('changed')
            if data.get('pending_dialogue') is not None and self.s.topics.dialogue.pending(m)!=data['pending_dialogue']:raise ValueError('pending_changed')
            m.event_operation_proposal=row['id']
            if not self.o.can_deliver(m):raise ValueError('changed')
        except (ValueError,PermissionError):
            return result|{'state':'stale'}
        if data.get('native_control') is not None:result['proposed']=data['native_control']
        for item in selected:
            card=item['card'];facts=card.get('facts',card.get('contributions',[]))
            result['targets'].append({'id':card['id'],'kind':item['kind'],'group':item['group'],'title':self.o.title(card),
                'current':self.o.control_state(card),'sources':[{'author':f.get('speaker',f.get('actor')),
                    'text':f.get('quote',f.get('text','')),'mode':f.get('mode',f.get('kind')),'expires':f['expires']}
                    for f in facts if not f.get('superseded') and f['expires']>self.s.clock()]})
            if data.get('native_control') is not None:
                from .qq_event_priority import timing_view
                result['targets'][-1]['settings']={k:card.get(k) for k in ('keep','importance','importance_locked')}
                result['targets'][-1]['settings']['timing']=timing_view(card,'private',self.s.clock())
        if data.get('pending_dialogue'):
            with self.o.store.connect() as db:
                rows=db.execute('select id,data,expires from qq_topic_dialogue_units u where bot=? and actor=? and epoch=? and expires>? and blocked=0 '
                    'and not exists(select 1 from qq_topic_dialogue_links l where l.unit=u.id) order by id limit 129',
                    (self.o.bot,m.user_id,self.s.epoch(),self.s.clock())).fetchall()
            sources=[{'id':r['id'],'expires':r['expires'],**json.loads(r['data'])} for r in rows]
            if len(sources)>128 or any(x.get('truncated') for x in sources) or sum(len(x.get('text','')) for x in sources)>131072:
                return result|{'state':'incomplete_sources','targets':[]}
            result['pending_dialogue']=data['pending_dialogue']|{'sources':sources}
        return result

    def preview(self,owner,identifier):
        with self.lock:
            row,data=self.row(owner,identifier);view=self.view(row,data)
            return view|{'preview_version':fingerprint([row,view,self.generation])}

    def hint(self,m):
        if not self.eligible(m):return None
        if any(r['state']=='native_prepared' for r in self.listing(m.user_id)['entries']):
            return ConsoleNotice('事件操作还在后台操作台等待核对，请在那里决定；这句回复没有执行操作。')
        return None

    def decide(self,owner,identifier,version,action):
        if action not in {'confirm','reject'}:raise ValueError('native_event_decision')
        with self.lock:
            current=self.preview(owner,identifier);row,data=self.row(owner,identifier)
            if row['state'] in {'done','refused'}:return current
            if current['preview_version']!=version:raise ValueError('native_event_preview_changed')
            if row['state']!='native_prepared':raise ValueError('native_event_not_pending')
            if action=='reject':
                with self.o.store.connect() as db:
                    db.execute("update qq_event_operation_sessions set state='refused' where id=? and state='native_prepared' and data=?",(identifier,row['data']))
                return self.preview(owner,identifier)
            if current['state']!='native_prepared':raise ValueError('native_event_sources_changed')
            m=self.message(row,data)
            def consent(stored):
                self.owner(owner)
                return all(stored[k]==row[k] for k in ('id','bot','actor','scope','data','state','expires'))
            if data.get('native_control') is not None:
                from .qq_event_console_controls import commit_control
                commit_control(self,m,row,data)
            else:self.o.execute(m,row,data,native_consent=consent)
            return self.preview(owner,identifier)
