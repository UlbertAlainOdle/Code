"""Scoped natural-language consent. Suggestions never execute on their own."""
from contextlib import nullcontext
import json
import re
import time
from uuid import uuid4
from .qq_responses import ActionProposal


class QQActionProposals:
    def __init__(self,store,*,clock=time.time):
        self.store,self.clock=store,clock
        with store.connect() as db:
            db.execute('''create table if not exists qq_action_proposals(
                id text primary key,scope text not null,session_id text not null,
                kind text not null,payload text not null,question text not null,
                revision text not null,expires real not null,state text not null)''')
            # An interrupted side effect must never be retried from a new 'yes'.
            db.execute("update qq_action_proposals set state='unknown' where state='executing'")

    def ask(self,scope,session_id,kind,payload,question,revision):
        if not re.fullmatch(r'[1-9]\d{0,19}:private:[1-9]\d{0,19}:(?:shared|isolated)',scope):raise ValueError('private_scope_required')
        if kind=='search_on':
            if payload!={}:raise ValueError('invalid_payload')
        elif kind=='save_candidate':
            if set(payload) not in ({'candidate_id'},{'candidate_id','candidate_version'}) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',str(payload['candidate_id'])):raise ValueError('invalid_candidate')
            if 'candidate_version' in payload and not re.fullmatch(r'[0-9a-f]{64}',str(payload['candidate_version'])):raise ValueError('invalid_candidate_version')
        elif kind=='save_reference':
            if set(payload)!={'plan'} or not isinstance(payload['plan'],dict):raise ValueError('invalid_reference_plan')
        elif kind=='select_reference':
            if set(payload) not in ({'options','session'},{'options','session','epoch','expires'}) or not isinstance(payload['options'],list) or not 1<=len(payload['options'])<=4:raise ValueError('invalid_reference_options')
        elif kind=='save_reference_set':
            if set(payload)!={'plans','options'} or not isinstance(payload['plans'],list) or not 2<=len(payload['plans'])<=4 or len(payload['plans'])!=len(payload['options']):raise ValueError('invalid_reference_set')
        else:raise ValueError('unsupported_action')
        if not session_id.startswith('session_qq_') or not revision or not question or len(question)>(6000 if kind in {'save_reference','select_reference','save_reference_set'} else 240):raise ValueError('invalid_proposal')
        proposal=uuid4().hex
        with self.store.connect() as db:
            db.execute('begin immediate')
            db.execute("update qq_action_proposals set state='superseded' where scope=? and session_id=? and state in ('prepared','pending','clarifying')",(scope,session_id))
            db.execute('insert into qq_action_proposals values(?,?,?,?,?,?,?,?,?)',(proposal,scope,session_id,kind,json.dumps(payload),question,revision,self.clock()+600,'prepared'))
            db.execute("delete from qq_action_proposals where id in (select id from qq_action_proposals order by expires desc limit -1 offset 2048)")
        return ActionProposal(question)

    def delivered(self,scope,session_id,text):
        with self.store.connect() as db:
            rows=db.execute("select id,question,kind from qq_action_proposals where scope=? and session_id=? and state='prepared' and expires>?",(scope,session_id,self.clock())).fetchall()
            for row in rows:
                normalized=row[2] in {'save_reference','select_reference','save_reference_set'}
                question=re.sub(r'\s+','',row[1]) if normalized else row[1]
                delivered=re.sub(r'\s+','',text) if normalized else text
                if question in delivered:db.execute("update qq_action_proposals set state='pending' where id=? and state='prepared'",(row[0],))

    def supersede(self,scope,session_id,*,db=None):
        with (self.store.connect() if db is None else nullcontext(db)) as connection:
            connection.execute("update qq_action_proposals set state='superseded' where scope=? and session_id=? and state in ('prepared','pending','clarifying')",(scope,session_id))

    def confirmation_admissible(self,scope,session_id,text,revision):
        """Admission only: never approve a plan or mutate its state here.

        Completed/uncertain replies only read their original outcome, so they
        retain the existing reply() expiry semantics. Pending writes still
        require a fully delivered, current, unexpired proposal.
        """
        if text.strip().rstrip('。！!，,？?').strip() not in {'保存吧','可以保存','记住','记住吧','同意保存','记下来','对的','确认'}:return False
        with self.store.connect() as db:
            row=db.execute('select kind,state,revision,expires from qq_action_proposals where scope=? and session_id=? order by rowid desc limit 1',(scope,session_id)).fetchone()
        if not row or row[0] not in {'save_candidate','save_reference','save_reference_set'}:return False
        if row[1] in {'done','executing','unknown'}:return True
        return row[1]=='pending' and row[2]==revision and row[3]>self.clock()

    def reply(self,scope,session_id,text,revision,authorized,reply_to=''):
        raw_text=text.strip();text=raw_text.rstrip('。！!，,？?').strip()
        from .memory_reference import readonly_memory_interlude,save_reference
        general_yes={'同意','我同意','可以','好','好的','行','好啊','可以啊','行啊'}
        no=text in {'不同意','不要','不用','算了','不用了','不要保存','先不开'}
        with self.store.connect() as db:
            db.execute('begin immediate')
            row=db.execute("select id,kind,payload,revision,expires,state from qq_action_proposals where scope=? and session_id=? order by rowid desc limit 1",(scope,session_id)).fetchone()
            if not row:return None
            choice_request=None
            if row[1] in {'select_reference','save_reference_set'} and row[5]=='pending':
                from .qq_memory_reference_selection import selection_request
                choice_request=selection_request(text,len(json.loads(row[2])['options']))
            if row[1]=='search_on' and save_reference(text):
                db.execute("update qq_action_proposals set state='superseded' where id=? and state in ('prepared','pending')",(row[0],));return None
            if row[1] in {'save_reference','select_reference','save_reference_set'} and text=='取消':no=True
            if row[1] in {'save_candidate','save_reference','select_reference','save_reference_set'} and (readonly_memory_interlude(text) or (save_reference(text) and not choice_request)):return None
            if reply_to and row[5] in {'pending','done','executing','unknown'}:
                question=db.execute('select question from qq_action_proposals where id=?',(row[0],)).fetchone()[0]
                has_receipts=db.execute("select 1 from sqlite_master where type='table' and name='qq_delivery_parts'").fetchone()
                receipt=db.execute("select p.text from qq_delivery_parts p join qq_deliveries d on d.id=p.result_id where d.scope=? and p.receipt=? and p.state='sent'",(scope,str(reply_to))).fetchall() if has_receipts else []
                if row[1] in {'save_reference','select_reference','save_reference_set'} and receipt:
                    parts=db.execute("select p.text from qq_delivery_parts p join qq_deliveries d on d.id=p.result_id where d.scope=? and p.state='sent' and d.id in (select result_id from qq_delivery_parts where receipt=? and state='sent') order by p.seq",(scope,str(reply_to))).fetchall()
                    receipt=[[re.sub(r'\s+','',''.join(p[0] for p in parts))]];question=re.sub(r'\s+','',question)
                if not any(question in r[0] for r in receipt):
                    if text in {'记住','记下来','保存'}:return None
                    return {'status':'unrelated_reply'}
            if row[1] in {'select_reference','save_reference_set'} and row[5]=='pending':
                payload=json.loads(row[2]);selection=choice_request
                if (selection or (row[1]=='select_reference' and text in general_yes|{'记住','记住吧','确认','对的'})) and (row[4]<=self.clock() or row[3]!=revision or not authorized):
                    status='expired' if row[4]<=self.clock() else 'stale'
                    db.execute('update qq_action_proposals set state=? where id=?',(status,row[0]))
                    return {'status':status}
                if selection and row[4]>self.clock() and row[3]==revision and authorized:
                    if selection.get('invalid'):return {'status':'invalid_selection'}
                    if row[1]=='save_reference_set':
                        first=payload['plans'][0]
                        payload={'options':[{**o,'source':{**o['source'],'expires':min(o['source']['expires'],p['expires'])}} for o,p in zip(payload['options'],payload['plans'])],
                                 'session':first['session'],'epoch':first['epoch'],'expires':min(p['expires'] for p in payload['plans'])}
                    db.execute("update qq_action_proposals set state='executing' where id=?",(row[0],))
                    chosen=selection['indices'];choice={'selected':chosen[0]} if len(chosen)==1 else {'selection':chosen}
                    if selection.get('fragment'):choice['fragment']=selection['fragment']
                    return {'id':row[0],'kind':'select_reference','payload':{**payload,'expires':min(payload.get('expires',row[4]),row[4]),**choice},'status':'approved'}
                if row[1]=='select_reference' and text in general_yes|{'记住','记住吧','确认','对的'}:return {'status':'selection_required'}
            specific_yes=({'保存吧','可以保存','记住','记住吧','同意保存','记下来','对的','确认'} if row[1] in {'save_candidate','save_reference','save_reference_set'}
                          else {'开启吧','打开吧','同意开启搜索','可以打开搜索','开启搜索吧'})
            yes=text in general_yes|specific_yes
            if row[5]!='pending':
                if not yes or row[5] not in {'done','executing','unknown'}:return None
                if row[1] in {'save_reference','save_reference_set'}:
                    return {'status':'already','id':row[0],'kind':row[1]}
                return {'status':'already'}
            payload=json.loads(row[2])
            sources=payload.get('plan',{}).get('sources',[]) if row[1]=='save_reference' else []
            candidate_plan=len(sources)==1 and sources[0].get('candidate')
            correction=re.fullmatch(r'(?:(?:可以|好|好的)[，,]\s*)?(?:但(?:是)?(?:请)?|请)?改成[：:]?\s*(.{1,12000})',raw_text,re.S) if row[1]=='save_candidate' or candidate_plan else None
            if correction and row[4]>self.clock() and row[3]==revision and authorized:
                db.execute("update qq_action_proposals set state='executing' where id=?",(row[0],))
                if candidate_plan:payload={'candidate_id':sources[0]['candidate'],'candidate_version':sources[0]['version'],'reference_plan':payload['plan']}
                return {'id':row[0],'kind':'revise_candidate','payload':payload|{'content':correction.group(1)},'expires':row[4],'status':'approved'}
            if not yes and not no:
                db.execute("update qq_action_proposals set state='superseded' where id=?",(row[0],));return None
            state=('expired' if row[4]<=self.clock() else 'stale' if row[3]!=revision or not authorized else 'rejected' if no else 'executing')
            db.execute('update qq_action_proposals set state=? where id=?',(state,row[0]))
            return {'id':row[0],'kind':row[1],'payload':json.loads(row[2]),'expires':row[4],'status':'approved' if state=='executing' else state}

    def finish(self,proposal_id,success):
        with self.store.connect() as db:
            db.execute("update qq_action_proposals set state=? where id=? and state='executing'",('done' if success else 'unknown',proposal_id))
