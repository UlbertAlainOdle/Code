"""Versioned, bounded idle fading. No model calls or retroactive TTL edits."""
import hashlib
import json
import math
import re
from pydantic import BaseModel,ConfigDict
from .qq_tasks import checkpoint_current


class IdlePolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version:int=1
    enabled:bool=False


def pending(card):
    if card['state'] not in {'active','cooling'}:return False
    claims=[x for x in card['contributions'] if not x.get('superseded')]
    finished={}
    for i,x in enumerate(claims):
        if x['kind']=='withdrawal' or (x['kind']=='progress' and x.get('subject','self')=='self' and
            re.search(r'已完成|已经完成|已做完|已结束',x['text']) and not re.search(r'未|没|尚|不|如果|假如',x['text'])):finished[x['actor']]=i
    return any(x.get('mode') not in {'hypothesis','reported','question'} and
        (x['id']==card.get('current_decision') or i>finished.get(x['actor'],-1) and x['kind']=='commitment' and
            re.search(r'我(?:会|负责|承担|承诺|来)|由我',x['text'])) for i,x in enumerate(claims))


def deadline(card):
    a=card.get('attention')
    if not a:return 0
    if a.get('version')!=1:raise ValueError('unsupported_topic_idle_version')
    factor=.9+.1*bool(a['pending'])
    lifetime=max(21600,a['half_life']*math.log2(factor/.01))
    return a['content_at']+min(1800,max(0,a.get('reference_credit',0)))+lifetime


def source_expiry(card,claim):
    if 'split_policy' in claim:
        if type(claim['split_policy']) is not int or claim['split_policy']!=1 or type(claim.get('split_expiry')) not in (int,float) or not math.isfinite(claim['split_expiry']):raise ValueError('unsupported_topic_split_source')
        if 'idle_policy' in claim and (claim['idle_policy']!=1 or not card.get('attention')):raise ValueError('unsupported_topic_idle_source')
        # A move is not a fresh continuation. Preserve its exact effective
        # expiry at confirmation, independently of the destination's heat.
        return claim['split_expiry']
    if 'idle_policy' not in claim:return claim['expires']
    if claim['idle_policy']!=1 or not card.get('attention'):raise ValueError('unsupported_topic_idle_source')
    end=deadline(card)
    if card['state'] in {'active','cooling'} and not claim.get('superseded') and claim['id']==card.get('current_decision'):
        # Preserve only a current, host-authorized dated decision, not every
        # old opinion in its topic. Attention still decays independently.
        end=max(end,card['attention'].get('dated_decision_until',0))
    return end


def view(card,now):
    a=card.get('attention')
    if not a:return None
    end=deadline(card)
    idle=max(0,now-a['content_at']-min(1800,max(0,a.get('reference_credit',0))))
    score=(.9+.1*bool(a['pending']))*2**(-min(1000,idle/a['half_life']))
    retained=any(source_expiry(card,c)>now for c in card['contributions'] if not c.get('superseded'))
    stage='expired' if now>=end and not retained else 'active' if score>=.45 else 'cooling' if score>=.1 else 'dormant'
    if stage=='active' and card['state']=='cooling':stage='cooling'
    return {'stage':stage,'score':score,'content_at':a['content_at'],'last_reference':a.get('reference_at',0),
        'pending_buffer':bool(a['pending']),'expires':end}


def contribute(card,actor,at,now):
    at=min(at,now)
    a=card.get('attention',{'version':1,'buckets':[]})
    if a.get('version')!=1:raise ValueError('unsupported_topic_idle_version')
    content=max(at,a.get('content_at',0))
    # Old/delayed messages cannot refresh attention to the delivery time.
    bins={int(b['slot']):set(b['actors']) for b in a['buckets'] if b['slot']*1800>content-21600}
    slot=int(at//1800);bins.setdefault(slot,set()).add(actor)
    bins={k:sorted(v)[:8] for k,v in sorted(bins.items())[-12:]}
    people={p for actors in bins.values() for p in actors}
    multiplier=min(4,1+.5*min(3,max(0,len(bins)-1))+.5*min(3,max(0,len(people)-1)))
    card['attention']={'version':1,'content_at':content,'half_life':2700*multiplier,
        'buckets':[{'slot':k,'actors':v} for k,v in bins.items()],
        'pending':pending(card),'reference_credit':0,'reference_at':0,'references':[]}
    decision=next((c for c in card['contributions'] if c['id']==card.get('current_decision') and c.get('idle_policy')==1 and not c.get('superseded')),None)
    dates=[x['at'] for x in (decision or {}).get('proposed_timing',{}).values() if x.get('status')=='known' and x.get('at',0)>content]
    if dates:card['attention']['dated_decision_until']=min(max(dates)+86400,content+7*86400)


class QQTopicLifecycle:
    def __init__(self,unified):
        self.u=unified;self.s=unified.service;self.store=unified.store
        with self.store.connect() as db:db.execute('create table if not exists qq_topic_lifecycle_policy(bot text primary key,revision integer,data text)')

    def policy(self):
        with self.store.connect() as db:row=db.execute('select revision,data from qq_topic_lifecycle_policy where bot=?',(self.u.bot,)).fetchone()
        value=IdlePolicy.model_validate(json.loads(row[1]) if row else {})
        if value.version!=1:raise ValueError('unsupported_topic_idle_policy')
        return {'revision':row[0] if row else 0,'values':value.model_dump(),
            'rules':{'idle_weight':.9,'pending_weight':.1,'base_half_life_seconds':2700,'max_half_life_seconds':10800,
                'reference_credit_limit_seconds':1800,'minimum_cleanup_idle_seconds':21600}}

    def configure(self,owner,revision,values):
        if owner not in self.u.bridge.settings.owner_ids:raise PermissionError('owner_required')
        value=IdlePolicy.model_validate(values)
        if value.version!=1:raise ValueError('unsupported_topic_idle_policy')
        if value.enabled and not self.s.topics.active():raise ValueError('topic_routing_required')
        with self.store.connect() as db:
            db.execute('begin immediate');checkpoint_current()
            row=db.execute('select revision from qq_topic_lifecycle_policy where bot=?',(self.u.bot,)).fetchone()
            if type(revision) is not int or (row[0] if row else 0)!=revision:raise ValueError('topic_idle_policy_changed')
            db.execute('insert or replace into qq_topic_lifecycle_policy values(?,?,?)',(self.u.bot,revision+1,value.model_dump_json()))
        return self.policy()

    def reference(self,message):
        if (not self.policy()['values']['enabled'] or not self.s.topics.active() or message.text.startswith('/') or
                not self.s.topics.status_read(message.text)):return
        if getattr(message,'idle_reference_checked',False):return
        message.idle_reference_checked=True
        focus=self.s.topics.named_focus(message)
        if not focus:return
        cards=[c for c in self.u.cards(message) if c.get('attention') and
            (c.get('topic_anchor')==focus or focus in c['title'])]
        if len(cards)!=1:return
        card=cards[0];now=self.s.clock()
        digest=hashlib.sha256((message.user_id+'\0'+message.text.strip()).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('begin immediate');checkpoint_current()
            row=db.execute('select revision,data from qq_shared_events where id=?',(card['id'],)).fetchone()
            if not row or row[0]!=card['revision']:return
            data=json.loads(row[1]);a=data['attention']
            if deadline(data)<=now or digest in a.get('references',[]):return
            credit=min(1800,max(0,(message.sent_at or now)-a['content_at']))
            if credit<=a.get('reference_credit',0):return
            a.update(reference_credit=credit,reference_at=message.sent_at or now,
                references=(a.get('references',[])+[digest])[-8:])
            db.execute('update qq_shared_events set data=?,revision=revision+1 where id=?',(json.dumps(data,ensure_ascii=False),card['id']))
            # Cached state used a prior row revision and cannot be sent as current.
            db.execute("delete from qq_topic_state_cache where kind='group' and card=?",(card['id'],))
