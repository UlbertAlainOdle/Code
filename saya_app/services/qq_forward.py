"""Long answers: confirmed self messages first, then native ID-node forwarding."""
import json
import time
from . import qq_delivery
from .qq_tasks import checkpoint_current,current_guard,effect_current,TaskCancelled


class QQForward:
    def __init__(self,bridge):
        self.bridge,self.store=bridge,bridge.store
        with self.store.connect() as db:
            db.executescript('''create table if not exists qq_forward_results(
                result_id text primary key,scope text,state text,receipt text,error text);
                create table if not exists qq_forward_sources(
                result_id text,seq integer,text text,state text,receipt text,primary key(result_id,seq));''')
            # An interrupted upload may already have reached QQ. Never replay it.
            db.execute("update qq_delivery_parts set state='unknown' where result_id in (select result_id from qq_forward_results where state='sending') and state='pending'")
            db.execute("update qq_forward_results set state='unknown',error='restart_unknown' where state='sending'")
            db.execute("update qq_forward_sources set state='unknown' where state='sending'")
        self.prune()

    def prune(self):
        with self.store.connect() as db:
            for table in ['qq_forward_results','qq_forward_sources']:
                db.execute('delete from '+table+' where result_id not in (select id from qq_deliveries)')

    async def try_send(self,message,scope,result_id,*,permission_check=None):
        b=self.bridge;client=b.client
        if message.bot_id!=b.settings.bot_id or not b.settings.enabled or not b.settings.task_control_enabled:return False
        if not callable(getattr(client,'send_self_part',None)) or not callable(getattr(client,'send_merged',None)):return False
        record,parts=qq_delivery.lookup(self.store,scope,result_id)
        dialogue=[p for p in parts if p['content_kind']=='dialogue']
        if (not record or record['kind']!='final' or (len(parts)<=5 and len(record['canonical'])<=5*b.settings.bubble_chars) or len(parts)>500
                or any(p['state']!='pending' for p in dialogue)
                or any(p['state'] not in {'pending','sent'} for p in parts)):return False
        origin=json.loads(record['origin'])
        if scope!=message.scope+(':shared' if message.shared else ':isolated') or any(origin[k]!=getattr(message,k) for k in ['bot_id','user_id','group_id']):return False
        guard=current_guard()
        if guard:
            guard.result_ids.add(result_id);guard.registry.attach_result(guard.task_id,scope,result_id)
        def check():
            checkpoint_current()
            if permission_check is not None and not permission_check():raise TaskCancelled('forward_permission_changed')
            with self.store.connect() as db:
                valid=db.execute('select 1 from qq_deliveries where id=? and scope=? and expires>?',(result_id,scope,time.time())).fetchone()
                stopped=db.execute('select 1 from qq_delivery_stops where result_id=?',(result_id,)).fetchone()
            if not valid or stopped:raise TaskCancelled('forward_expired_or_cancelled')
        check();self.prune()
        # Operational notes remain standalone marked receipts, never forwarded
        # as statements made by the character. They precede all dialogue parts.
        prefix=[p for p in parts if p['content_kind']!='dialogue' and p['state']=='pending']
        if prefix:
            notice=await qq_delivery.send_batch(self.store,client,message,scope,result_id,len(prefix),
                b.settings.reply_reference,memory_check=permission_check)
            if notice:return False
            check()
        with self.store.connect() as db:
            previous=db.execute('select state from qq_forward_results where result_id=?',(result_id,)).fetchone()
            if previous:
                if previous['state'] in {'sending','unknown','sent'}:return True
                return False # A source failure may use ordinary text; never retry self uploads.
            db.execute("insert into qq_forward_results values(?,?,'preparing','','')",(result_id,scope))
            # Repack the same visible body into bounded source messages. No LLM,
            # extra private history or model-controlled node identities involved.
            body='\n\n'.join(p['text'] for p in dialogue)
            # Keep readable groups of ideas inside the record instead of one
            # 2200-character node. Tiny delivery fragments are already merged.
            chunks=[piece for part in dialogue for piece in qq_delivery.semantic_parts(part['text'],900)]
            if len(chunks)>100:chunks=qq_delivery.semantic_parts(body,900)
            db.executemany("insert into qq_forward_sources values(?,?,?,'pending','')",[(result_id,i,t) for i,t in enumerate(chunks)])
        try:
            receipts=[]
            for i,text in enumerate(chunks):
                check()
                with effect_current('send',scope=scope) as effect:
                    with self.store.connect() as db:db.execute("update qq_forward_sources set state='sending' where result_id=? and seq=?",(result_id,i))
                    try:
                        receipt=await client.send_self_part(message.bot_id,text)
                        effect.committed()
                        with self.store.connect() as db:db.execute("update qq_forward_sources set state='sent',receipt=? where result_id=? and seq=?",(receipt,result_id,i))
                    except BaseException:
                        with self.store.connect() as db:db.execute("update qq_forward_sources set state='unknown' where result_id=? and seq=?",(result_id,i))
                        raise
                receipts.append(receipt)
        except TaskCancelled:raise
        except BaseException as error:
            with self.store.connect() as db:db.execute("update qq_forward_results set state='source_failed',error='self_delivery_failed' where result_id=?",(result_id,))
            if isinstance(error,Exception):return False
            raise
        check()
        with effect_current('send',scope=scope) as effect:
            with self.store.connect() as db:db.execute("update qq_forward_results set state='sending' where result_id=?",(result_id,))
            try:
                receipt=await client.send_merged(message,receipts)
                effect.committed()
                with self.store.connect() as db:
                    db.execute("update qq_forward_results set state='sent',receipt=? where result_id=?",(receipt,result_id))
                    db.execute("update qq_delivery_parts set state='sent',receipt=? where result_id=? and state='pending'",(receipt,result_id))
                return True
            except BaseException:
                with self.store.connect() as db:
                    db.execute("update qq_forward_results set state='unknown',error='target_delivery_unknown' where result_id=?",(result_id,))
                    db.execute("update qq_delivery_parts set state='unknown' where result_id=? and state='pending'",(result_id,))
                raise
