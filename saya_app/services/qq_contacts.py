"""Friend applications are reviewed separately from group invitations.

Accepting a friend never grants chat access or private-memory access. Unknown
transport outcomes are terminal for automatic retries.
"""
import hashlib
import time


class QQContactReview:
    def __init__(self,bridge):
        self.bridge,self.store=bridge,bridge.store
        with self.store.connect() as db:
            db.execute('''create table if not exists qq_friend_requests(
                id text primary key,bot text,user text,event_key text unique,flag text,
                comment text,created real,state text)''')
            db.execute("update qq_friend_requests set state='result_unknown',flag='' where state in ('approving','rejecting')")

    def submit(self,event):
        from .qq_bridge import qq_id
        b=self.bridge
        if (event.get('post_type')!='request' or event.get('request_type')!='friend'
                or qq_id(event.get('self_id'))!=b.settings.bot_id or not qq_id(event.get('user_id'))):
            return 'ignored','unsupported_request'
        try:
            if not time.time()-b.settings.max_event_age<=float(event['time'])<=time.time()+60:return 'ignored','stale_event'
        except (ValueError,TypeError,KeyError):return 'ignored','invalid_time'
        flag=event.get('flag')
        if not isinstance(flag,str) or not 1<=len(flag)<=2048 or any(x in flag for x in ['\0','\r','\n']):return 'ignored','invalid_request_flag'
        key=hashlib.sha256((b.settings.bot_id+':friend:'+str(event['user_id'])+':'+flag).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('begin immediate')
            db.execute("update qq_friend_requests set state='expired',flag='' where state='pending' and created<?",(time.time()-86400,))
            db.execute('delete from qq_friend_requests where created<?',(time.time()-b.settings.retention_days*86400,))
            if db.execute('select 1 from qq_friend_requests where event_key=?',(key,)).fetchone():return 'duplicate',None
            if db.execute("select count(*) from qq_friend_requests where bot=? and state='pending'",(b.settings.bot_id,)).fetchone()[0]>=128:return 'busy','too_many_pending_friend_requests'
            db.execute("insert into qq_friend_requests values(?,?,?,?,?,?,?,'pending')",
                ('fr-'+key[:24],b.settings.bot_id,str(event['user_id']),key,flag,str(event.get('comment') or '')[:200],time.time()))
        return 'pending',None

    def state(self):
        b=self.bridge
        with self.store.connect() as db:
            rows=db.execute('select id,user,comment,created,state from qq_friend_requests where bot=? order by created desc limit 128',(b.settings.bot_id,)).fetchall()
        unified=getattr(getattr(b,'episodes',None),'unified',None);enabled=bool(unified and unified.active())
        return {'requests':[dict(r) for r in rows], 'allowed_users':sorted(b.settings.allowed_users|b.settings.owner_ids),
            'accepting_does_not_grant_chat':not enabled,'automatic_accept':False,
            'unified_events_enabled':enabled,'event_minimum_level':unified.policy()['values']['minimum_level'] if enabled else None}

    async def decide(self,owner_id,request_id,approve,*,confirmed):
        b=self.bridge
        if owner_id not in b.settings.owner_ids or confirmed is not True or type(approve) is not bool:
            raise ValueError('owner_explicit_confirmation_required')
        async with b._management_lock:
            with self.store.connect() as db:
                db.execute('begin immediate')
                row=db.execute('select * from qq_friend_requests where id=? and bot=?',(request_id,b.settings.bot_id)).fetchone()
                if not row or row['state']!='pending':raise ValueError('friend_request_already_handled_or_unknown')
                unified=getattr(getattr(b,'episodes',None),'unified',None)
                if approve and unified and unified.active():
                    ranks={'distant':0,'ordinary':1,'familiar':2,'important':3,'owner':4}
                    if ranks[unified.level(row['user'])]<ranks[unified.policy()['values']['minimum_level']]:
                        raise ValueError('friend_relationship_below_threshold')
                if time.time()-row['created']>86400:
                    db.execute("update qq_friend_requests set state='expired',flag='' where id=?",(request_id,))
                    db.commit()
                    raise ValueError('friend_request_expired')
                db.execute('update qq_friend_requests set state=? where id=?',('approving' if approve else 'rejecting',request_id))
            try:
                await b.client.call('set_friend_add_request',{'flag':row['flag'],'approve':approve,'remark':''})
            except BaseException:
                with self.store.connect() as db:db.execute("update qq_friend_requests set state='result_unknown',flag='' where id=?",(request_id,))
                raise
            with self.store.connect() as db:db.execute('update qq_friend_requests set state=?,flag=? where id=?',('approved' if approve else 'rejected','',request_id))
        unified=getattr(getattr(b,'episodes',None),'unified',None)
        return {'id':request_id,'state':'approved' if approve else 'rejected',
            'chat_access_changed':bool(approve and unified and unified.admitted(row['user']))}
