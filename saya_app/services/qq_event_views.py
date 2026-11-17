"""Requester-specific source views and action-relevant preview guards.

Shared evidence remains public to its other authorized readers. A personal stop
must nevertheless suppress its own source for that requester in every projection.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class QQEventViews:
    def __init__(self,service):
        self.service=service;self.store=service.store
        with self.store.connect() as db:
            db.executescript('''
                create table if not exists qq_event_source_masks(bot text,actor text,card text,evidence text,
                    visibility text,expires real,primary key(bot,actor,card,evidence,visibility));
                create table if not exists qq_event_preview_guards(token text primary key,kind text,
                    fingerprint text,expires real);
            ''')

    def mask(self,db,actor,card,data,visibility='*'):
        now=self.service.clock();bot=self.service.bridge.settings.bot_id
        if hasattr(self.service,'provenance'):self.service.provenance.invalidate(db,actor=actor)
        db.execute('delete from qq_event_source_masks where expires<=?',(now,))
        if db.execute('select count(*) from qq_event_source_masks').fetchone()[0]>131040:
            raise ValueError('event_source_mask_capacity')
        for fact in data.get('facts',[]):
            evidence=fact.get('evidence_id')
            if not evidence:continue
            # The guard carries identity only, never a second copy of the text.
            expires=max(fact['expires'],now+self.service.bridge.settings.retention_days*86400)
            db.execute('insert or replace into qq_event_source_masks values(?,?,?,?,?,?)',
                (bot,actor,card,evidence,visibility,expires))

    def unmask(self,db,actor,card,visibility):
        db.execute('delete from qq_event_source_masks where bot=? and actor=? and card=? and visibility in (?,?)',
            (self.service.bridge.settings.bot_id,actor,card,'*',visibility))

    def denied(self,message):
        s=self.service;now=s.clock();bot=s.bridge.settings.bot_id;view=s.people.visibility(message)
        with self.store.connect() as db:
            denied={r[0] for r in db.execute('select evidence from qq_event_source_masks '
                'where bot=? and actor=? and visibility in (?,?) and expires>?',
                (bot,message.user_id,'*',view,now))}
            # This also handles stops made before this reader was installed.
            for row in db.execute('select data from qq_episode_cards where scope=?',(s.people.scope(message.user_id),)):
                c=json.loads(row[0])
                if (c.get('tracking') in {'suppressed','paused'} or view in c.get('muted_views',[])
                        or c.get('epoch')!=s.epoch()):
                    denied.update(f['evidence_id'] for f in c.get('facts',[]) if f.get('evidence_id'))
            # Retained provenance guards make an explicitly removed card's sources
            # unusable for its own requester without erasing another member's card.
            exists=db.execute("select 1 from sqlite_master where name='qq_topic_history_guards'").fetchone()
            if exists:
                denied.update(r[0] for r in db.execute('select g.evidence from qq_topic_history_guards g '
                    'where g.bot=? and g.actor=? and g.expires>? and not exists(select 1 from qq_episode_cards c where c.id=g.card)',
                    (bot,message.user_id,now)))
        return denied

    def project(self,message,card):
        denied=self.denied(message)
        claims=[x for x in card['contributions'] if x.get('evidence_id') not in denied]
        if len(claims)==len(card['contributions']):return card
        c={**card,'contributions':claims}
        live=[x for x in claims if not x.get('superseded')]
        # Derived text must have the same effective sources as the claims.
        if not any(card['title'] in x['text'] for x in live):
            c.update(title=live[0]['text'][:120] if live else '当前请求者无有效来源',
                title_speaker=live[0]['actor'] if live else '',
                title_speaker_label=live[0].get('speaker_label','') if live else '',title_unavailable=True)
        if not any(c.get('topic_anchor','') in x['text'] for x in live):c.pop('topic_anchor',None)
        if not any(x['id']==c.get('current_decision') for x in live):c.pop('current_decision',None)
        return c

    def history_sources(self,message,*,refresh=True):
        """The same effective view protects raw turns as well as card summaries."""
        s=self.service;u=s.unified;blocked=[];public=set()
        group=message.group_id or (u.group(message) if hasattr(message,'text') else '')
        if not group or not u.member(message,group):return blocked,public
        visible=u.cards(message,group,refresh=refresh)
        for c in visible:
            for f in c['contributions']:
                if not f.get('superseded') and f.get('parent_digest'):
                    public.add((f['actor'],f['parent_digest']))
                elif not f.get('superseded') and f.get('evidence_id'):
                    with self.store.connect() as db:
                        public.update((r['actor'],r['digest']) for r in db.execute(
                            'select actor,digest from qq_topic_history_guards where bot=? and evidence=?',
                            (s.bridge.settings.bot_id,f['evidence_id'])))
        with self.store.connect() as db:
            stopped={r[0] for r in db.execute('select card from qq_shared_event_stops where bot=? and subject=?',
                (s.bridge.settings.bot_id,message.user_id))}
        denied=self.denied(message)
        for c in u.cards(message,group,include_stopped=True,refresh=refresh):
            for f in c['contributions']:
                if (c['id'] in stopped or f.get('evidence_id') in denied) and f.get('parent_digest'):
                    blocked.append((f['actor'],f['parent_digest']))
        effective={f.get('evidence_id') for c in visible for f in c['contributions'] if not f.get('superseded')}
        with self.store.connect() as db:
            roots=db.execute('select id,actor,data from qq_topic_evidence where bot=? and grp=? and expires>?',
                (s.bridge.settings.bot_id,group,s.clock())).fetchall()
            for row in roots:
                root=json.loads(row['data'])
                if not any(x['kind']=='group' for x in root.get('links',[])) or row['id'] in effective:continue
                # Removed/terminal/superseded shared sources must not survive as
                # unguarded raw turns. The public record is evaluated per reader.
                if root.get('parent_digest'):blocked.append((row['actor'],root['parent_digest']))
                else:
                    blocked.extend((r['actor'],r['digest']) for r in db.execute(
                        'select actor,digest from qq_topic_history_guards where bot=? and evidence=?',
                        (s.bridge.settings.bot_id,row['id'])))
        return blocked,public

    def fingerprint(self,kind,card):
        c={k:v for k,v in card.items() if k not in {'id','revision','attention_view','attention','state_reason'}}
        if kind=='group':
            if c.get('state') in {'active','cooling'}:c['state']='open'
            from .qq_topic_lifecycle import source_expiry
            c['contributions']=[{**x,'expires':source_expiry(card,x)} for x in c['contributions']
                if source_expiry(card,x)>self.service.clock()]
        else:
            # Passive quota/idle state changes are not content or authority changes.
            for key in ('off_topic','last_reference','reference_count','paused_for_actor'):c.pop(key,None)
            if c.get('state') in {'active','cooling'}:c['state']='open'
            if c.get('tracking') in {'active','cooling'}:c['tracking']='open'
            c['facts']=[f for f in c.get('facts',[]) if f['expires']>self.service.clock()]
        return digest(c)

    def save_preview(self,db,token,kind,card):
        db.execute('delete from qq_event_preview_guards where expires<=?',(self.service.clock(),))
        db.execute('insert into qq_event_preview_guards values(?,?,?,?)',
            (token,kind,self.fingerprint(kind,card),self.service.clock()+600))

    def preview_matches(self,db,token,kind,card,legacy_revision):
        row=db.execute('select fingerprint from qq_event_preview_guards where token=? and kind=? and expires>?',
            (token,kind,self.service.clock())).fetchone()
        return row[0]==self.fingerprint(kind,card) if row else card['revision']==legacy_revision
