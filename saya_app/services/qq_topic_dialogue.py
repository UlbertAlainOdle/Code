"""Qualified private discussion sources, registered before optional topic cards."""
import hashlib
import json
import re
from dataclasses import replace


ROUTE_WINDOW_SECONDS = 1800


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class QQTopicDialogue:
    def __init__(self,topics):
        self.t=topics;self.s=topics.service;self.b=topics.bridge;self.store=topics.store;self.bot=self.s.unified.bot
        with self.store.connect() as db:
            db.executescript('''
                create table if not exists qq_topic_dialogue_schema(bot text primary key,version integer,enabled integer);
                create table if not exists qq_topic_dialogue_units(id text primary key,bot text,actor text,scope text,source text,at real,expires real,epoch text,hash text,data text,blocked integer default 0);
                create index if not exists qq_topic_dialogue_scope on qq_topic_dialogue_units(bot,scope,at);
                create table if not exists qq_topic_dialogue_links(unit text,card text,evidence text,primary key(unit,card,evidence));
            ''')
            db.execute('insert or ignore into qq_topic_dialogue_schema values(?,1,0)',(self.bot,))

    def enabled(self):
        with self.store.connect() as db:row=db.execute('select version,enabled from qq_topic_dialogue_schema where bot=?',(self.bot,)).fetchone()
        return bool(row and row[0]==1 and row[1] and self.t.active())

    def configure(self,actor,enabled):
        if actor not in self.b.settings.owner_ids or type(enabled) is not bool:raise PermissionError('owner_dialogue_policy_required')
        with self.store.connect() as db:db.execute('update qq_topic_dialogue_schema set enabled=? where bot=? and version=1',(int(enabled),self.bot))

    def ordinary(self,m):
        if not self.enabled() or m.group_id or not self.s.unified.eligible(m.user_id) or m.kind!='message' or m.forward_present or m.media_types:return False
        text=m.text.strip()
        from .memory_reference import explicit_save_body
        from .qq_event_controls import control_intent
        from .qq_context_salience import topic_request
        return bool(text and not text.startswith('/') and not explicit_save_body(text) and not control_intent(text)
            and topic_request(text) is None and not self.t.status_read(text)
            and not re.fullmatch(r'(?:你好|早上好|晚上好|晚安|谢谢|收到|好的|嗯|哈哈|记住|对的|确认|取消)[呀啊呢哦吧。！!～~\s]*',text))

    def prepare(self,m):
        if not self.ordinary(m):return None
        now=self.s.clock();at=m.sent_at or now;expires=at+self.s.retention(m)
        if expires<=now:return None
        identifier=digest([self.bot,m.key])[:32];hashed=digest([m.user_id,m.scope,m.key,at,m.text,self.s.epoch()])
        from .qq_event_attribution import source_label
        data={'text':m.text[:2000],'truncated':len(m.text)>2000,'speaker_label':source_label(m)}
        with self.store.connect() as db:
            db.execute('delete from qq_topic_dialogue_units where expires<=?',(now,))
            db.execute('delete from qq_topic_dialogue_links where unit not in (select id from qq_topic_dialogue_units)')
            row=db.execute('select * from qq_topic_dialogue_units where id=?',(identifier,)).fetchone()
            if not row:
                if db.execute('select count(*) from qq_topic_dialogue_units').fetchone()[0]>=16000:
                    m.topic_coverage='普通来源索引已满，本条未自动整理';return None
                db.execute('insert into qq_topic_dialogue_units values(?,?,?,?,?,?,?,?,?,?,0)',
                    (identifier,self.bot,m.user_id,m.scope,m.key,at,expires,self.s.epoch(),hashed,json.dumps(data,ensure_ascii=False)))
                row=db.execute('select * from qq_topic_dialogue_units where id=?',(identifier,)).fetchone()
        if row['hash']!=hashed or row['blocked']:return None
        m.topic_dialogue_unit=identifier
        return {'kind':'dialogue','id':identifier,'hash':hashed,'expires':expires}

    def valid_ref(self,m,ref,cache=None):
        if m.group_id or not self.s.unified.eligible(m.user_id):return False
        cache={} if cache is None else cache
        key=('dialogue',ref.get('id'),ref.get('hash'),ref.get('expires'))
        if key in cache:return cache[key]
        with self.store.connect() as db:
            row=db.execute('select * from qq_topic_dialogue_units where id=? and bot=? and actor=?',(ref.get('id'),self.bot,m.user_id)).fetchone()
            links=db.execute('select * from qq_topic_dialogue_links where unit=?',(ref.get('id'),)).fetchall()
        valid=bool(row and type(ref.get('expires')) in (int,float) and not row['blocked'] and row['epoch']==self.s.epoch() and row['expires']>self.s.clock()
                   and row['hash']==ref.get('hash') and ref.get('expires',0)>self.s.clock() and ref['expires']<=row['expires'])
        if valid and links:
            for link in links:
                card=self.s.provenance.load(m,'personal',link['card'])
                if not card or not any(f.get('evidence_id')==link['evidence'] for f in card['_facts']):valid=False;break
        cache[key]=valid;return valid

    def context(self,m,*,proofs=None):
        if not self.ordinary(m):return []
        with self.store.connect() as db:
            rows=db.execute('select * from qq_topic_dialogue_units where bot=? and scope=? and actor=? and at>=? and source<>? order by at desc limit 12',
                (self.bot,m.scope,m.user_id,self.s.clock()-ROUTE_WINDOW_SECONDS,m.key)).fetchall()
        out=[]
        for row in reversed(rows):
            ref={'kind':'dialogue','id':row['id'],'hash':row['hash'],'expires':row['expires']}
            if self.valid_ref(m,ref):
                data=json.loads(row['data'])
                out.append({'id':row['id'],'text':data['text'],'author':m.user_id,'truncated':data.get('truncated',False)})
                if proofs is not None:
                    proofs.append((row['at'],ref))
        return out

    def valid_context(self, message, proofs):
        cutoff = self.s.clock() - ROUTE_WINDOW_SECONDS
        cache = {}
        return all(at >= cutoff and self.valid_ref(message, ref, cache) for at, ref in proofs)

    @staticmethod
    def selections(sources,context):
        """Freeze exact source spans; the model never supplies replacement prose."""
        if not isinstance(sources,list) or len(sources)>3:raise ValueError('invalid_dialogue_context_source')
        allowed={x['id']:x for x in context};out=[]
        for source in sources:
            legacy=isinstance(source,str)
            if not legacy and not isinstance(source,dict):raise ValueError('invalid_dialogue_context_source')
            unit=source if legacy else source.get('id')
            if not isinstance(unit,str) or unit not in allowed:raise ValueError('invalid_dialogue_context_source')
            entry=allowed[unit];text=entry['text']
            if entry.get('truncated'):raise ValueError('dialogue_context_source_changed')
            if legacy:
                if re.search(r'[；;\n]|另外|此外',text) or len(re.findall(r'[。！？!?]',text))>1:raise ValueError('dialogue_prior_source_needs_exact_span')
                quote=text;start=0;mode='question' if re.search(r'[?？]|为什么|是否|怎么',text) else 'reported';subject='general'
            else:
                quote=source.get('quote');start=source.get('start');mode=source.get('mode');subject=source.get('subject')
                if not isinstance(quote,str) or not quote.strip() or len(quote)>2000:raise ValueError('invalid_dialogue_prior_quote')
                if start is None and text.count(quote)==1:start=text.index(quote)
                if type(start) is not int or start<0 or text[start:start+len(quote)]!=quote:raise ValueError('invalid_dialogue_prior_span')
                if mode not in {'experience','opinion','proposal','question','hypothesis','reported','commitment','progress','decision','withdrawal','objection'} or subject not in {'self','other','general'}:raise ValueError('invalid_dialogue_prior_semantics')
            end=start+len(quote)
            if any(x['id']==unit and start<x['end'] and end>x['start'] for x in out):raise ValueError('overlapping_dialogue_prior_span')
            out.append({'id':unit,'quote':quote,'start':start,'end':end,'mode':mode,'subject':subject,'legacy':legacy})
        return out

    def bind(self,m,identifier,evidence):
        with self.store.connect() as db:
            row=db.execute('select id from qq_topic_dialogue_units where bot=? and source=? and actor=? and blocked=0',(self.bot,m.key,m.user_id)).fetchone()
            if row:db.execute('insert or ignore into qq_topic_dialogue_links values(?,?,?)',(row[0],identifier,evidence))

    def fresh_stopped_cards(self,m,guard,db):
        """Only a proved new direct input may create a new card after a stop.

        The old body guard is retained: unlabelled history never gains access.
        Backfill and legacy observers do not carry this direct-ingress proof.
        """
        ref=getattr(m,'topic_dialogue_ref',None);choice=getattr(m,'topic_choice',{})
        if m.group_id or not isinstance(ref,dict) or choice.get('action')!='create' or choice.get('event_id'):return set()
        policy=db.execute('select version,enabled from qq_topic_dialogue_schema where bot=?',(self.bot,)).fetchone()
        if not policy or tuple(policy)!=(1,1):return set()
        unit=db.execute('select * from qq_topic_dialogue_units where id=? and bot=? and actor=? and scope=? and source=?',
            (ref.get('id'),self.bot,m.user_id,m.scope,getattr(m,'topic_source',None))).fetchone()
        if not unit or unit['at']!=m.sent_at or not self.valid_ref(m,ref):return set()
        data=json.loads(unit['data']);span=getattr(m,'topic_metadata',{}).get('source_span')
        if data.get('truncated') or not isinstance(span,list) or len(span)!=2 or any(type(x) is not int for x in span):return set()
        if not 0<=span[0]<span[1]<=len(data['text']) or data['text'][span[0]:span[1]]!=m.text:return set()
        cards=[]
        for row in db.execute('select id,data from qq_episode_cards where scope=?',(self.s.people.scope(m.user_id),)):
            card=json.loads(row['data'])
            if any(f.get('digest')==guard['digest'] for f in card['facts']):cards.append((row['id'],card))
        if not cards or guard['event_id'] not in {i for i,c in cards}:return set()
        for identifier,card in cards:
            native=db.execute("select max(at) from qq_event_control_audit where bot=? and subject=? and event_id=? and action='stop'",
                (self.bot,m.user_id,identifier)).fetchone()[0]
            semantic=db.execute("select max(r.at) from qq_event_operation_results r join qq_event_operation_sessions o on o.id=r.session "
                "join json_each(r.data,'$.changes') c where o.bot=? and o.actor=? and o.state='done' and r.state='done' "
                "and json_extract(r.data,'$.action')='stop' and json_extract(c.value,'$.kind')='personal' "
                "and json_extract(c.value,'$.id')=? and json_extract(c.value,'$.after.tracking')='suppressed'",
                (self.bot,m.user_id,identifier)).fetchone()[0]
            times=[t for t in (native,semantic) if t is not None];stopped=max(times) if times else None
            if card.get('tracking')!='suppressed' or stopped is None or unit['at']<=stopped:return set()
        return {i for i,c in cards}

    async def backfill(self,m,identifier,ids,context):
        """Append original same-actor spans, retaining their lifetime and authorship."""
        if not ids:return []
        selections=self.selections(ids,context);results=[]
        for selected in selections:
            unit=selected['id']
            with self.store.connect() as db:
                row=db.execute('select * from qq_topic_dialogue_units where id=? and bot=? and actor=? and scope=?',(unit,self.bot,m.user_id,m.scope)).fetchone()
                links=db.execute('select card,evidence from qq_topic_dialogue_links where unit=?',(unit,)).fetchall()
            if not row or json.loads(row['data']).get('truncated') or not self.valid_ref(m,{'id':unit,'hash':row['hash'],'expires':row['expires']}):raise ValueError('dialogue_context_source_changed')
            raw=json.loads(row['data']);parent=raw['text'];text=selected['quote'];start=selected['start'];end=selected['end']
            if parent[start:end]!=text:raise ValueError('dialogue_context_source_changed')
            evidence=digest([row['source'],start,end])[:32]
            duplicate=False
            for link in links:
                if selected['legacy']:
                    if link['card']!=identifier:raise ValueError('dialogue_source_already_bound')
                    duplicate=True;continue
                linked=self.s.provenance.load(m,'personal',link['card'])
                fact=next((f for f in linked['_facts'] if f.get('evidence_id')==link['evidence']),None) if linked else None
                if not fact:raise ValueError('dialogue_context_source_changed')
                old_start,old_end=fact.get('source_span',[0,len(parent)])
                if start<old_end and end>old_start:
                    if (link['card']==identifier and fact.get('source')==row['source'] and old_start==start and old_end==end
                            and fact.get('quote')==text and fact.get('mode')==selected['mode'] and fact.get('subject')==selected['subject']):duplicate=True
                    else:raise ValueError('dialogue_prior_span_already_bound')
            if duplicate:continue
            card=next((c for c in self.s.people._canonical(m.user_id) if c['id']==identifier),None)
            if not card:raise ValueError('dialogue_card_missing')
            with self.store.connect() as db:db.execute('insert or ignore into qq_topic_dialogue_links values(?,?,?)',(unit,identifier,'pending'))
            anchor=card.get('topic_anchor') or next((f.get('topic_anchor') for f in card['facts'] if f.get('topic_anchor')),card['title'])
            earlier=replace(m,key=row['source']+':dialogue:'+identifier+':'+evidence,message_id=row['source'].split(':message:')[-1],text=text,sent_at=row['at'])
            earlier.context_snapshot=m.context_snapshot;earlier.topic_source=row['source'];earlier.topic_parent_text=parent+'\n'+anchor
            earlier.topic_dialogue_required=True
            earlier.topic_metadata={'evidence_id':evidence,'mode':selected['mode'],'subject':selected['subject'],'source_span':[start,end],'expires':row['expires'],
                'speaker_label':raw.get('speaker_label',''),'topic_anchor':anchor,'dialogue_unit':unit,
                'parent_digest':hashlib.sha256(('Doctor: '+parent).encode()).hexdigest()}
            earlier.topic_choice={'action':'update','event_id':identifier,'confidence':.95,'expected':card['revision'],'title':anchor,'topic_anchor':anchor,'revision_kind':'append','supersedes':[]}
            await self.s.people.observe(earlier,[])
            updated=next((c for c in self.s.people._canonical(m.user_id) if c['id']==identifier),None)
            if not updated or not any(f.get('evidence_id')==evidence for f in updated['facts']):raise ValueError('dialogue_backfill_not_committed')
            with self.store.connect() as db:
                db.execute("delete from qq_topic_dialogue_links where unit=? and card=? and evidence='pending'",(unit,identifier))
                db.execute('insert or ignore into qq_topic_dialogue_links values(?,?,?)',(unit,identifier,evidence))
            results.append(unit)
        return results

    def pending(self,m,db=None):
        if db is None:
            with self.store.connect() as connection:return self.pending(m,connection)
        rows=db.execute('select u.id,u.data,u.source,u.at,u.expires from qq_topic_dialogue_units u where u.bot=? and u.actor=? and u.epoch=? and u.expires>? and u.blocked=0 and not exists(select 1 from qq_topic_dialogue_links l where l.unit=u.id) order by u.id',
            (self.bot,m.user_id,self.s.epoch(),self.s.clock())).fetchall()
        return {'count':len(rows),'hash':digest([tuple(x) for x in rows]),'samples':[json.loads(x['data']).get('text','')[:80] for x in rows[:3]]}

    def block_pending(self,db,m,delete=False):
        db.execute('update qq_topic_dialogue_units set blocked=1'+(",data='{}'" if delete else '')+' where bot=? and actor=? and epoch=? and expires>? and blocked=0 and not exists(select 1 from qq_topic_dialogue_links l where l.unit=qq_topic_dialogue_units.id)',(self.bot,m.user_id,self.s.epoch(),self.s.clock()))
