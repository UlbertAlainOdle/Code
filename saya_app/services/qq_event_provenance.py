"""Host-owned event dependencies for raw messages, derived replies and history.

Labels are metadata, never instructions or extra model calls. Source dependencies
survive harmless appends; derived views additionally track their semantic state.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class QQEventProvenance:
    LIMIT=4096

    def __init__(self,s):
        self.s=s;self.store=s.store;self.bot=s.bridge.settings.bot_id
        with self.store.connect() as db:
            db.executescript('''
                create table if not exists qq_event_provenance_schema(bot text primary key,version integer);
                create table if not exists qq_event_provenance_barriers(bot text,principal text,revision integer,at real,
                    primary key(bot,principal));
                create table if not exists qq_event_turn_dependencies(id integer primary key,tag text);
                create table if not exists qq_event_result_dependencies(result_id text primary key,tag text);
                create table if not exists qq_event_message_dependencies(event text primary key,scope text,tag text,expires real);
            ''')
            version=db.execute('select version from qq_event_provenance_schema where bot=?',(self.bot,)).fetchone()
            if version and version[0]!=1:raise ValueError('unsupported_event_provenance')
            if self.active():
                self.schema(db)
                people=set()
                for r in db.execute('select data from qq_episode_cards where scope like ?',(self.bot+':person:%',)):
                    c=json.loads(r[0])
                    if c.get('tracking') in {'suppressed','paused'} or c.get('muted_views') or any(f.get('superseded') for f in c['facts']):people.add(c['subject'])
                people.update(r[0] for r in db.execute('select distinct actor from qq_event_source_masks where bot=?',(self.bot,)))
                people.update(r[0] for r in db.execute('select distinct subject from qq_shared_event_stops where bot=?',(self.bot,)))
                people.update(r[0] for r in db.execute("select distinct subject from qq_event_control_audit where bot=? and action in ('stop','pause','group_stop','timing','cancel','complete')",(self.bot,)))
                people.update(r[0] for r in db.execute('select distinct g.subject from qq_person_event_source_guards g where g.bot=? '
                    'and not exists(select 1 from qq_episode_cards c where c.id=g.event_id)',(self.bot,)))
                for actor in people:
                    db.execute('insert or ignore into qq_event_provenance_barriers values(?,?,1,?)',(self.bot,'person:'+actor,s.clock()))
                for r in db.execute('select grp,data from qq_shared_events where bot=?',(self.bot,)):
                    c=json.loads(r['data'])
                    if c['state'] not in {'active','cooling'} or any(f.get('superseded') for f in c['contributions']):
                        db.execute('insert or ignore into qq_event_provenance_barriers values(?,?,1,?)',(self.bot,'group:'+r['grp'],s.clock()))
            self.prune(db)

    def active(self):
        if self.s.people.active() and self.s.unified.active():return True
        with self.store.connect() as db:return bool(db.execute('select 1 from qq_event_provenance_schema where bot=?',(self.bot,)).fetchone())

    def schema(self,db):db.execute('insert or ignore into qq_event_provenance_schema values(?,1)',(self.bot,))

    def prune(self,db):
        db.execute('delete from qq_event_message_dependencies where expires<=?',(self.s.clock(),))
        db.execute('delete from qq_event_turn_dependencies where id not in (select id from qq_context_turns)')
        db.execute('delete from qq_event_result_dependencies where result_id not in (select id from qq_deliveries)')

    def invalidate(self,db,*,actor=None,group=None):
        if not self.active():return
        self.schema(db)
        for principal in (['person:'+actor] if actor else [])+(['group:'+group] if group else []):
            db.execute('insert into qq_event_provenance_barriers values(?,?,1,?) on conflict(bot,principal) '
                'do update set revision=revision+1,at=excluded.at',(self.bot,principal,self.s.clock()))

    def barriers(self,m):
        principals=['person:'+m.user_id]+(['group:'+m.group_id] if m.group_id else [])
        with self.store.connect() as db:
            return {p:(db.execute('select revision from qq_event_provenance_barriers where bot=? and principal=?',
                (self.bot,p)).fetchone() or [0])[0] for p in principals}

    def empty(self,m):
        return {'version':1,'bot':self.bot,'epoch':self.s.epoch(),'private_subject':m.user_id if not m.group_id else '',
            'group':m.group_id or '', 'refs':[],'legacy':[]}

    def legacy(self,m):
        expires=self.s.clock()+3600
        with self.store.connect() as db:
            for r in db.execute("select min(json_extract(f.value,'$.expires')) from qq_episode_cards c,json_each(c.data,'$.facts') f where c.scope=?",
                    (self.s.people.scope(m.user_id),)):
                if r[0] is not None:expires=min(expires,r[0])
            if m.group_id:
                r=db.execute("select min(json_extract(f.value,'$.expires')) from qq_shared_events c,json_each(c.data,'$.contributions') f where c.bot=? and c.grp=?",
                    (self.bot,m.group_id)).fetchone()
                if r and r[0] is not None:expires=min(expires,r[0])
        tag=self.empty(m);tag['legacy']=[{'principal':p,'revision':v,'expires':expires} for p,v in self.barriers(m).items()]
        return tag

    def merge(self,m,*tags):
        out=self.empty(m);refs={};legacy={};cache={}
        for t in tags:
            if not t:continue
            if not self.valid(m,t,cache):raise ValueError('event_dependency_changed')
            for x in t['refs']:refs[digest(x)]=x
            for x in t['legacy']:
                old=legacy.get(x['principal']);legacy[x['principal']]={**x,'expires':min(x['expires'],old['expires']) if old else x['expires']}
        if len(refs)>self.LIMIT:raise ValueError('event_dependency_capacity')
        out['refs']=[refs[k] for k in sorted(refs)];out['legacy']=[legacy[k] for k in sorted(legacy)]
        if any(x.get('kind')=='dialogue' for x in out['refs']):out['version']=2
        return out

    @staticmethod
    def source_id(kind,f):return f['id'] if kind=='group' else f.get('evidence_id') or f['source']

    @staticmethod
    def source_hash(f):
        return digest([f.get('text',f.get('quote','')),f.get('actor',f.get('speaker')),f['source'],f['at'],
            f.get('kind'),f.get('mode'),f.get('subject')])

    def load(self,m,kind,identifier):
        s=self.s;now=s.clock()
        if not s.unified.collecting():return None
        with self.store.connect() as db:
            table='qq_episode_cards' if kind=='personal' else 'qq_shared_events'
            row=db.execute('select * from '+table+' where id=?',(identifier,)).fetchone()
            if not row:return None
            c=json.loads(row['data'])
            if c.get('epoch')!=s.epoch():return None
            if kind=='personal':
                if row['scope']!=s.people.scope(m.user_id) or not s.unified.eligible(m.user_id):return None
                c['unified_personal']=True
                if c['expires']<=now or not s.people.controls.recallable(c,s.people.visibility(m)):return None
                facts=[f for f in c['facts'] if not f.get('superseded') and f['expires']>now]
            else:
                if row['bot']!=self.bot or not s.unified.member(m,row['grp']) or c['state'] not in {'active','cooling'}:return None
                if db.execute('select 1 from qq_shared_event_stops where bot=? and subject=? and card=?',(self.bot,m.user_id,identifier)).fetchone():return None
                from .qq_topic_lifecycle import source_expiry
                denied=s.views.denied(m)
                facts=[{**f,'expires':source_expiry(c,f)} for f in c['contributions'] if not f.get('superseded')
                    and source_expiry(c,f)>now and f.get('evidence_id') not in denied]
                c['group']=row['grp']
            if not facts:return None
            c['_facts']=facts;return c

    def view_hash(self,kind,c):
        return digest({'sources':sorted((str(self.source_id(kind,f)),self.source_hash(f)) for f in c['_facts']),
            'lifecycle':c.get('lifecycle','open') if kind=='personal' else 'open',
            'decision':c.get('current_decision'),'coordinator':c.get('coordinator'),'timing':c.get('timing',{})})

    def refs(self,m,kind,identifier,*,source=None,view=False):
        c=self.load(m,kind,identifier)
        if not c:return []
        facts=[f for f in c['_facts'] if source is None or f['source']==source]
        out=[{'kind':kind,'id':identifier,'source':str(self.source_id(kind,f)),'hash':self.source_hash(f),
            'expires':f['expires'],'lifecycle':c.get('lifecycle','open'),'evidence':f.get('evidence_id')} for f in facts]
        if view and facts:out.append({'kind':kind,'id':identifier,'view':self.view_hash(kind,c),'expires':min(f['expires'] for f in facts)})
        return out

    def valid(self,m,tag,cache=None):
        cache={} if cache is None else cache
        if 'epoch' not in cache:cache['epoch']=self.s.epoch()
        if not isinstance(tag,dict) or tag.get('version') not in {1,2} or tag.get('bot')!=self.bot or tag.get('epoch')!=cache['epoch']:return False
        if tag.get('private_subject') and (m.group_id or tag['private_subject']!=m.user_id):return False
        if tag.get('group') and not self.s.unified.member(m,tag['group']):return False
        if not isinstance(tag.get('refs'),list) or len(tag['refs'])>self.LIMIT or not isinstance(tag.get('legacy'),list):return False
        if 'barriers' not in cache:cache['barriers']=self.barriers(m)
        barriers=cache['barriers']
        if any(not isinstance(x,dict) or x.get('principal') not in barriers or x.get('revision')!=barriers[x['principal']]
            or not isinstance(x.get('expires'),(int,float)) or x['expires']<=self.s.clock() for x in tag['legacy']):return False
        cards=cache.setdefault('cards',{})
        for ref in tag['refs']:
            if isinstance(ref,dict) and ref.get('kind')=='dialogue':
                if tag['version']!=2 or not self.s.topics.dialogue.valid_ref(m,ref,cache):return False
                continue
            if not isinstance(ref,dict) or ref.get('kind') not in {'personal','group'} or not isinstance(ref.get('expires'),(int,float)) or ref['expires']<=self.s.clock():return False
            key=(ref['kind'],ref.get('id'))
            if key not in cards:cards[key]=self.load(m,*key)
            c=cards[key]
            if c is None:return False
            if 'view' in ref:
                if ref['view']!=self.view_hash(ref['kind'],c):return False
            elif not any(str(self.source_id(ref['kind'],f))==ref.get('source') and self.source_hash(f)==ref.get('hash') for f in c['_facts']):return False
            if 'lifecycle' in ref and ref['lifecycle']!=c.get('lifecycle','open'):return False
        return True

    def current_sources(self,m,event=None,subject=None):
        tag=self.empty(m);event=event or m.key;subject=subject or m.user_id
        with self.store.connect() as db:
            people=[r[0] for r in db.execute('select id from qq_episode_cards where scope=?',(self.s.people.scope(subject),))] if subject==m.user_id else []
            group=m.group_id or (self.s.unified.group(m) if hasattr(m,'text') else '')
            groups=[r[0] for r in db.execute('select id from qq_shared_events where bot=? and grp=?',(self.bot,group))] if group else []
        for kind,ids in [('group',groups),('personal',people)]:
            for identifier in ids:tag['refs'].extend(self.refs(m,kind,identifier,source=event))
        # Public source projections suffice in a public turn; do not make that
        # source depend on another member's personal-card permission.
        public={x['evidence'] for x in tag['refs'] if x['kind']=='group' and x.get('evidence')}
        if m.group_id and public:tag['refs']=[x for x in tag['refs'] if x['kind']=='group' or x.get('evidence') not in public]
        return tag

    def direct(self,m,channel):
        tag=self.empty(m)
        unit=self.s.topics.dialogue.prepare(m)
        if unit:tag['refs'].append(unit);tag['version']=2
        for ref in channel.get('qq_episode',{}).get('refs',[]):tag['refs'].extend(self.refs(m,'personal',ref['id'],view=True))
        for ref in channel.get('shared_events',{}).get('refs',[]):tag['refs'].extend(self.refs(m,'group',ref['id'],view=True))
        return self.merge(m,tag,self.current_sources(m))

    def history_tag(self,m,row,side_tag=None,cache=None):
        tag=side_tag if side_tag is not None else row.get('qq_event_dependencies')
        if tag is not None:return tag if self.valid(m,tag,cache) else None
        # Old unlabelled material may already be contaminated even if its old
        # qq_episode snapshot happens to refer to a different active topic.
        if any(self.barriers(m).values()):return None
        old=row.get('qq_episode')
        if old and not self.s.valid(m,old,history=True):return None
        tag=self.legacy(m)
        return tag if self.valid(m,tag,cache) else None

    def inherit(self,m,base,rows):
        tags=[base]
        for row in rows:
            tag=row.get('qq_event_dependencies')
            if tag is None:
                tag=self.history_tag(m,row)
            if tag is None:raise ValueError('unlabelled_event_context')
            tags.append(tag)
        return self.merge(m,*tags)

    def bind_turn(self,m,channel):
        current=self.current_sources(m);base=self.merge(m,self.direct(m,channel),channel.get('_quote_event_dependencies'))
        user=self.merge(m,current,channel.get('_quote_event_dependencies')) if current['refs'] else base
        if getattr(m,'topic_coverage',None):
            uncertain=self.empty(m);uncertain['legacy']=[{'principal':p,'revision':v,'expires':self.s.clock()+3600} for p,v in self.barriers(m).items()]
            base=self.merge(m,base,uncertain);user=self.merge(m,user,uncertain)
        m.event_provenance_user=user;m.event_provenance_response=base
        channel['qq_user_event_dependencies']=user;channel['qq_event_dependencies']=base
        with self.store.connect() as db:
            self.schema(db);self.prune(db)
            if db.execute('select count(*) from qq_event_message_dependencies').fetchone()[0]>=65536:raise ValueError('event_message_labels_full')
            db.execute('insert or replace into qq_event_message_dependencies values(?,?,?,?)',
                (m.key,m.scope,json.dumps(user,ensure_ascii=False),self.s.clock()+self.s.bridge.settings.retention_days*86400))
        prior=channel.get('context_selector')
        def select(lines,rows):
            if callable(prior):lines,rows=prior(lines,rows)
            elif lines:
                rows=[{'role':'user' if x.startswith('Doctor: ') else 'ai','content':x.partition(': ')[2]} for x in lines]
                kept=[]
                for x in rows:
                    t=self.history_tag(m,x)
                    if t is not None:kept.append({**x,'qq_event_dependencies':t})
                rows=kept;lines=[('Doctor: ' if x['role']=='user' else 'Saya: ')+x.get('content',x.get('text','')) for x in rows]
            tag=self.inherit(m,base,rows)
            for key in ('group_context','context_link'):
                material=(channel.get(key) or {}).get('history' if key=='group_context' else 'turns',[])
                if material:tag=self.inherit(m,tag,material)
            channel['qq_event_dependencies']=tag;m.event_provenance_response=tag
            return lines,rows
        channel['context_selector']=select
        from .qq_tasks import current_guard
        guard=current_guard()
        if guard:
            previous=guard.permission_check
            guard.permission_check=lambda:(not previous or previous()) and self.valid(m,m.event_provenance_response)

    def label_rows(self,m,rows):
        kept=[]
        for row in rows:
            tag=self.history_tag(m,row)
            if tag is not None:kept.append({**row,'qq_event_dependencies':tag,
                '_qq_event_validated':row.get('qq_event_dependencies') is not None and row.get('_qq_event_validated',True)})
        return kept

    def bind_result(self,m,result_id,tag=None):
        tag=tag or getattr(m,'event_provenance_response',None)
        if tag is None:return
        if not self.valid(m,tag):raise ValueError('event_result_dependencies_changed')
        with self.store.connect() as db:
            self.schema(db);db.execute('insert or replace into qq_event_result_dependencies values(?,?)',(result_id,json.dumps(tag,ensure_ascii=False)))

    def valid_result(self,m,result_id):
        with self.store.connect() as db:
            row=db.execute('select tag from qq_event_result_dependencies where result_id=?',(result_id,)).fetchone()
            delivery=db.execute('select kind from qq_deliveries where id=?',(result_id,)).fetchone()
        if row is not None:return self.valid(m,json.loads(row[0]))
        # Old cached final answers must not bypass the legacy quarantine via
        # /continue. Host operation receipts carry no conversational evidence.
        return not (delivery and delivery[0]=='final' and any(self.barriers(m).values()))

    def protect_quote(self,m):
        q=getattr(m,'quote_context',None)
        if not q or q.get('status')!='ready' or not m.reply_to:return None
        sender=str(q.get('sender_id',''));tag=None
        with self.store.connect() as db:
            row=db.execute('select x.tag from qq_event_result_dependencies x join qq_delivery_parts p on p.result_id=x.result_id '
                'where p.receipt=? and p.state=?',(str(m.reply_to),'sent')).fetchone() if sender==self.bot else None
            if row:tag=json.loads(row[0])
            if sender!=self.bot:
                key=(f'{self.bot}:group:{m.group_id}:user:{sender}' if m.group_id else f'{self.bot}:private:{m.user_id}')+':message:'+str(m.reply_to)
                row=db.execute('select tag from qq_event_message_dependencies where event=? and expires>?',(key,self.s.clock())).fetchone()
                if row:tag=json.loads(row[0])
        if tag is None:
            if any(self.barriers(m).values()):
                m.quote_context={'status':'unavailable','reason':'unlabelled_event_reference'};return None
            tag=self.legacy(m)
            if self.valid(m,tag):return tag
            m.quote_context={'status':'unavailable','reason':'legacy_reference_expired'};return None
        if not self.valid(m,tag):
            m.quote_context={'status':'unavailable','reason':'event_source_unavailable'};return None
        return tag
