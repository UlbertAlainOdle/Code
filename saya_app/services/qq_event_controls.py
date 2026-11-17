"""Trusted event management, independent of optional observation/model decisions."""
import json
import re

from .qq_responses import ControlReceipt
from .qq_tasks import checkpoint_current
from .qq_event_priority import timing_control,parse_date,attach_timings,timing_available,timing_view


def control_intent(text):
    text=str(text or '').strip()
    parts=re.split(r'[，,；;。！!\n]+',text,maxsplit=1)
    if len(parts)==2 and parts[1].strip():
        tail=parts[1].strip()
        if re.search(r'^(?:才怪|不对|开玩笑|别执行|不要执行|改成|改为|改去|改到|只是(?:举例|假设|说说))|(?:其实|但是|不过).{0,12}(?:还没|未完成|没做完|没有完成|只是举例|是假设|不要|别再)',tail):return None
        if re.search(r'^(?:确定吗|要不要|该不该|应不应该|能否|是否|能不能|可不可以|会怎样)|(?:这样|这么|那样)(?:做|处理|取消|删除|标记)?(?:真的)?(?:合适|有用|可行|可以|合理|安全).{0,2}[吗么?？]',tail):return None
        head_intent=control_intent(parts[0])
        tail_intent=control_intent(re.split(r'[，,；;。！!\n]+',tail,maxsplit=1)[0]) if head_intent else None
        if head_intent and not tail_intent:return {**head_intent,'dialogue':True,'dialogue_text':text}
        if head_intent and tail_intent and not re.fullmatch(r'(?:这件事|这件|此事)(?:很重要|非常重要)[，,]*(?:但是|但)?先别提[。！!]*',text):
            return {'action':'clarify','text':text,'dialogue':True}
    from .qq_event_intent import lifecycle_intent
    lifecycle=lifecycle_intent(text)
    if lifecycle:return lifecycle
    if re.match(r'^(?:我(?:已经|刚才|以前|曾经|又|不小心|主动|顺手)|以前|过去|曾经|当时|去年)',text) and re.search(r'删掉|删除|暂停|停止|取消|恢复',text):return None
    if (not text or text.startswith('/') or
        re.search(r'''[“”「」『』‘’"'`\n]|(?:他说|她说|有人说|引用|转发|如果|假如|要是|例如|比如|不是让|没说要|不想让|不要忘|别忘|不能忘|别再忘|我(?:已经|刚才|又|不小心)?忘(?:记|掉)|忘记(?:带|拿|吃|做|买|关|开|去))''',text) or
        re.search(r'(?:不要|别|不能|无需|不用|不想|不需要|不许)(?:再)?(?:忘|删|停止|暂停|取消)',text) or
        re.search(r'改成|改去|更正|什么意思|怎么办|会怎样|能否|是否|为什么|怎样|能不能|可不可以|请问|取消暂停|[?？]|(?:了吗|了么|吗)$',text) or
        '改为' in text and not re.search('重要度|优先级|截止|安排时间',text)):
        return None
    timing=timing_control(text)
    if timing:return timing
    action=None
    if re.search(r'(?:别|不要|不用|禁止)(?:再)?在(?:这个|这|本)?群(?:里)?(?:提|说|跟进)',text):action='group_stop'
    elif re.search(r'(?:忘掉|忘记|删掉|删除)\s*(?:这个|这件|此|刚才|之前|事件|本人|[\u3400-\u9fffA-Za-z0-9])|不再跟进|不用(?:再)?跟进|别再跟进|停止跟进|不跟进了|别再记着',text):action='stop'
    elif re.search(r'先别(?:跟进|提)|暂停(?:跟进)?|暂时不(?:跟进|提)',text):action='pause'
    elif re.search(r'重新跟进|恢复跟进|继续跟进',text):action='resume'
    elif re.search(r'(?:只是|仅是|就是).{0,6}(?:测试|实验).{0,12}(?:不用当真|不当真)',text):action='stop'
    importance=None
    match=re.search(r'(?:重要度|优先级)(?:设为|改为|调为|降为|升为|[:：])?(高|普通|低)',text)
    if match:importance={'高':'high','普通':'normal','低':'low'}[match[1]]
    elif re.search(r'(?:这件事|这件|此事).{0,5}(?:很重要|非常重要)',text):importance='high'
    if importance and not action:action='importance'
    return {'action':action,'importance':importance,'text':text} if action else None


class QQEventControls:
    def __init__(self,people):
        self.people=people;self.service=people.service;self.store=people.store
        with self.store.connect() as db:
            db.executescript('''create table if not exists qq_event_control_schema(bot text primary key,version integer);
                create table if not exists qq_event_control_versions(bot text,subject text,revision integer,updated real,
                    primary key(bot,subject));
                create table if not exists qq_event_control_audit(event text primary key,bot text,subject text,
                    event_id text,action text,visibility text,revision integer,at real);''')
            row=db.execute('select version from qq_event_control_schema where bot=?',(people.bot,)).fetchone()
            if row and row[0]!=1:raise ValueError('unsupported_event_control_schema')

    def version(self,subject,db=None):
        if db is None:
            with self.store.connect() as conn:return self.version(subject,conn)
        row=db.execute('select revision from qq_event_control_versions where bot=? and subject=?',
            (self.people.bot,subject)).fetchone()
        return row[0] if row else 0

    def bump(self,db,subject):
        cutoff=self.service.clock()-self.people.bridge.settings.retention_days*86400
        db.execute('delete from qq_event_control_versions where bot=? and updated<? '
            'and not exists(select 1 from qq_episode_cards c where c.scope=qq_event_control_versions.bot||\':person:\'||qq_event_control_versions.subject) '
            'and not exists(select 1 from qq_person_event_source_guards g where g.bot=qq_event_control_versions.bot and g.subject=qq_event_control_versions.subject and g.at>?)',
            (self.people.bot,cutoff,cutoff))
        if not self.version(subject,db) and db.execute('select count(*) from qq_event_control_versions where bot=?',(self.people.bot,)).fetchone()[0]>=4096:
            raise ValueError('event_control_capacity')
        revision=self.version(subject,db)+1
        db.execute('insert or replace into qq_event_control_schema values(?,1)',(self.people.bot,))
        db.execute('insert or replace into qq_event_control_versions values(?,?,?,?)',
            (self.people.bot,subject,revision,self.service.clock()))
        return revision

    def effective(self,card,view='private'):
        tracking=card.get('tracking',card['state'])
        lifecycle=card.get('lifecycle','open')
        if tracking=='suppressed':return 'suppressed'
        if view in card.get('muted_views',[]):return 'scope_stopped'
        if card.get('paused_for_actor') or tracking=='paused':return 'paused'
        if lifecycle!='open':return lifecycle
        if not timing_available(card,view,self.service.clock()):return 'source_unavailable'
        current=[f for f in card['facts'] if not f.get('superseded')]
        latest=card.get('latest_source',current[-1]['source'] if current else None)
        if not any(f['source']==latest and f['expires']>self.service.clock() for f in current):return 'source_unavailable'
        if tracking=='active' and self.service.clock()-card['last_user']>=7200:return 'cooling'
        return tracking

    def recallable(self,card,view):
        return self.effective(card,view) not in {'suppressed','paused','scope_stopped','source_unavailable','candidate'}

    def inventory(self,message):
        cards=[c for c in self.people.cards(message) if not c.get('legacy_public')]
        # A private owner can still stop/delete their own retained card when its latest
        # source expires. Do not expose an expired quotation as a management title.
        if not message.group_id or self.people.unified() and self.people.unified().eligible(message.user_id):
            known={c['id'] for c in cards}
            for c in self.people._canonical(message.user_id):
                if c['id'] not in known and c['epoch']==self.service.epoch() and c['expires']>self.service.clock():
                    cards.append({**c,'state':self.effective(c),'title':'来源已不可用的本人事件','facts':[],'source_unavailable':True})
        return cards

    def _target(self,message,intent):
        cards=self.inventory(message);text=intent['text']
        strict_timing=False
        if intent.get('action')=='timing':
            selector=re.sub(r'^(?:请)?(?:把|将|给)?','',intent.get('target','').strip()).rstrip('的').strip()
            strict_timing=bool(selector and selector not in {'这件事','这件','这个事','此事','这事','这个','那件','那个'})
            text=selector if strict_timing else '暂停这件事'
        identifiers=set(re.findall(r'(?<![A-Za-z0-9])[a-f0-9]{16}(?![A-Za-z0-9])',text))
        if identifiers:
            found=[c for c in cards if c['id'] in identifiers]
            return (found[0],None) if len(identifiers)==1 and len(found)==1 else (None,'当前场合没有这个可管理的本人事件。')
        from .qq_event_intent import selector,named_match,DEICTIC
        selected=intent.get('target_selector') or selector(text,intent['action'])
        if selected:
            if selected in DEICTIC:
                recent=[c for c in cards if not c.get('source_unavailable') and any(
                    f['source'].startswith(message.scope+':') and self.service.clock()-f['at']<=1800
                    and f['expires']>self.service.clock() and not f.get('superseded') for f in c['facts'])]
                if len(recent)==1:return recent[0],None
            else:
                exact=[c for c in cards if not c.get('source_unavailable') and named_match(c,selected)]
                if len(exact)==1:return exact[0],None
            return None,'请明确要操作的本人事件名称或编号，本次没有修改。'
        if intent.get('strict_target'):return None,'请明确要操作的本人事件名称或编号，本次没有修改。'
        # Require distinctive shared content rather than generic event/control words.
        clean=re.sub(r'虚构|测试|实验|事件|计划|这件|这个|那个|刚才|之前|本人|忘掉|忘记|跟进|重要度|优先级|截止时间|截止日期|安排时间|设为|改为|调为|降为|升为|普通|很重要|非常重要|公园',' ',text)
        terms=set(re.findall(r'[\u3400-\u9fff]{2}|[A-Za-z0-9_]{3,}',clean))
        matches=[c for c in cards if not c.get('source_unavailable') and (
            text in c['title'] or any(text in f['quote'] for f in c['facts']) if strict_timing else any(
            t in c['title'] or any(t in f['quote'] for f in c['facts']) for t in terms))]
        if len(matches)==1:return matches[0],None
        if len(matches)>1:cards=matches
        elif cards and not strict_timing and (re.search(r'(?:忘掉|忘记|删掉|删除|跟进|暂停)(?:这件事|这个事|此事|这事|这件|这个测试|刚才那个)(?:吧|了)?[。！!]*$',text) or
            re.fullmatch(r'(?:这件事|这件|此事)(?:很重要|非常重要)[，,]*(?:但是|但)?先别提[。！!]*',text) or
            re.fullmatch(r'(?:别|不要|不用)在(?:这个|这|本)?群(?:里)?(?:提|说|跟进)(?:这个事|这件事|此事)?(?:[，,]私下继续)?[。！!]*',text) or
            re.fullmatch(r'(?:请)?(?:先别跟进|先别提|暂停跟进|停止跟进|不再跟进|不跟进|重新跟进|恢复跟进|已经办完|已经完成)(?:了|吧)?[。！!]*',text)):
            # Recent source evidence is scoped to the current conversation, never
            # the global per-account focus used by older event observers.
            recent=[c for c in cards if any(f['source'].startswith(message.scope+':') and
                self.service.clock()-f['at']<=1800 for f in c['facts'])]
            if len(recent)==1:return recent[0],None
        if not cards:return None,'当前场合没有可管理的本人事件；请提供本人事件编号。'
        options='\n'.join(c['id']+' · '+c['title'][:45] for c in cards[:3])
        action=intent.get('action','pause')
        command=('deadline' if intent.get('kind')=='deadline' else 'schedule') if action=='timing' else 'mute' if action=='group_stop' else action
        usage=command+' 编号'+(' '+intent.get('importance','normal') if action=='importance' else ' 日期时间或clear' if action=='timing' else '')
        return None,'请明确要操作的事件编号，本次没有修改。可用 /events '+usage+'：\n'+options

    def handle(self,message):
        if message.kind!='message':return None
        intent=control_intent(message.text)
        if not intent:return None
        message.event_control_dialogue=bool(intent.get('dialogue'))
        if intent['action']=='clarify':return ControlReceipt('本条包含多项事件操作，目标和顺序需分别明确，本次没有修改；请分开提出操作。')
        if message.forward_present or message.media_types:
            return ControlReceipt('请直接输入事件操作正文；转发或附件中的说明不作为授权。')
        if not self.people.active():return ControlReceipt('个人事件管理尚未准备好，本次没有修改。')
        if intent['action']=='group_stop' and not message.group_id:
            return ControlReceipt('请在目标群直接要求停止该群的事件使用，本次没有修改。')
        card,error=self._target(message,intent)
        if error:return ControlReceipt(error)
        return self.change(message,card,intent['action'],importance=intent.get('importance'),timing=intent if intent['action']=='timing' else None)

    def change(self,message,card,action,importance=None,timing=None):
        try:return self._commit(message,card,action,importance,timing)
        except ValueError:return ControlReceipt('事件管理元数据已满或版本不支持，本次没有修改，请先核对管理状态。')

    def _commit(self,message,card,action,importance=None,timing=None,*,native_transaction=None):
        checkpoint_current()
        if (message.bot_id!=self.people.bot or message.kind!='message' or message.forward_present or message.media_types or
            (not (self.people.unified().eligible(message.user_id) or not message.group_id and action in {'stop','pause'} and self.people.unified().friend(message.user_id)) if self.people.unified() else
             message.user_id not in self.people.bridge.settings.allowed_users|self.people.bridge.settings.owner_ids)):
            return ControlReceipt('当前身份或消息来源不能授权此操作，本次没有修改。')
        view=self.people.visibility(message)
        with self.store.connect() as db:
            db.execute('begin immediate');checkpoint_current()
            if native_transaction:native_transaction(db,'check',None)
            if db.execute('select 1 from qq_event_control_audit where event=?',(message.key,)).fetchone():
                return ControlReceipt('这条事件操作已处理，不会重复修改；可用 /events stopped 核对。')
            row=db.execute('select revision,data from qq_episode_cards where id=? and scope=?',
                (card['id'],self.people.scope(message.user_id))).fetchone()
            if not row or row['revision']!=card['revision']:
                return ControlReceipt('事件已变化，请重新读取并操作，本次没有修改。')
            data=json.loads(row['data'])
            if data['epoch']!=self.service.epoch() or data['expires']<=self.service.clock():
                return ControlReceipt('事件来源已失效，本次没有修改或延长有效期。')
            before=json.dumps(data,sort_keys=True)
            if action in {'stop','pause'}:data['tracking']='suppressed' if action=='stop' else 'paused'
            elif action=='group_stop':data['muted_views']=sorted(set(data.get('muted_views',[]))|{view})
            elif action=='resume':
                if data.get('lifecycle','open')!='open':
                    return ControlReceipt('该事件已完成或取消；重新开始请明确创建新的计划，本次没有恢复。')
                if card.get('source_unavailable'):return ControlReceipt('来源已不可用，请提供新事实；没有恢复旧内容。')
                if message.group_id and data.get('tracking')=='suppressed':
                    return ControlReceipt('跨会话停止的事件请在本人私聊明确恢复，本次没有修改。')
                data['tracking']='active';data['muted_views']=[v for v in data.get('muted_views',[]) if v!=view]
            elif action in {'cancel','complete'}:
                data['lifecycle']='cancelled' if action=='cancel' else 'completed'
                data['lifecycle_visibility']=view
                data['lifecycle_expires']=min(data['expires'],(message.sent_at or self.service.clock())+self.service.retention(message))
                data['tracking']='cooling'
            elif action=='keep':
                kept=sum(bool(c.get('keep')) for c in self.people._canonical(message.user_id) if c['id']!=card['id'])
                if kept>=self.people.capacity(message.user_id)['max_total']:
                    return ControlReceipt('优先保留已达到本人容量，请先解除其他事件的保留，本次没有修改。')
                data['keep']=True
            elif action=='unkeep':data['keep']=False
            elif action=='timing':
                zone=self.people.bridge.context_salience.state()['values']['timezone']
                at=message.sent_at or self.service.clock()
                try:value={'version':1,'status':'removed','timezone':zone} if timing['value']=='clear' else parse_date(timing['value'],at,zone)
                except ValueError as error:return ControlReceipt(str(error)+'本次没有修改。')
                attach_timings(data,{timing['kind']:value},source=message.key,visibility=view,at=at,
                    expires=min(data['expires'],at+self.service.retention(message)))
            elif action!='importance':return ControlReceipt('不支持的事件操作，本次没有修改。')
            if importance in {'high','normal','low'}:data.update(importance=importance,importance_locked=True)
            elif importance=='auto':data['importance_locked']=False
            if action in {'stop','pause','resume','group_stop'}:data['tracking_reason']=action
            if action in {'cancel','complete'}:data['lifecycle_reason']=action
            if importance:data['importance_reason']='user_explicit'
            data['state_reason']=action
            # Count, source TTL and evidence do not change on management or queries.
            changed=json.dumps(data,sort_keys=True)!=before
            if changed:
                if action in {'cancel','complete','timing'} and hasattr(self.service,'provenance'):
                    self.service.provenance.invalidate(db,actor=message.user_id)
                views=getattr(self.service,'views',None)
                if views and action in {'stop','pause','group_stop'}:
                    views.mask(db,message.user_id,card['id'],data,view if action=='group_stop' else '*')
                elif views and action=='resume':views.unmask(db,message.user_id,card['id'],view)
                db.execute('update qq_episode_cards set data=?,revision=revision+1 where id=? and revision=?',
                    (json.dumps(data,ensure_ascii=False),card['id'],card['revision']))
                self.bump(db,message.user_id)
                if action in {'stop','pause','cancel','complete','group_stop','timing'}:
                    sql='update qq_person_event_source_guards set blocked=1 where bot=? and event_id=?'
                    params=[self.people.bot,card['id']]
                    if action=='group_stop':sql+=' and visibility=?';params.append(view)
                    db.execute(sql,params)
                if action=='resume':db.execute('delete from qq_episode_pauses where event_id=? and actor=?',(card['id'],message.user_id))
            db.execute('delete from qq_event_control_audit where bot=? and at<?',
                (self.people.bot,self.service.clock()-self.people.bridge.settings.retention_days*86400))
            db.execute('insert into qq_event_control_audit values(?,?,?,?,?,?,?,?)',
                (message.key,self.people.bot,message.user_id,card['id'],action,view,row['revision']+int(changed),self.service.clock()))
            db.execute('delete from qq_event_control_audit where bot=? and event not in '
                '(select event from qq_event_control_audit where bot=? order by at desc,event desc limit 2048)',
                (self.people.bot,self.people.bot))
            if native_transaction:native_transaction(db,'committed',{'revision':row['revision']+int(changed),'changed':changed})
        labels={'stop':'已停止此事件在本人各会话的自动使用和跟进',
            'pause':'已暂停此事件的跟进，明确要求恢复前不会自动使用',
            'group_stop':'已停止此事件在本群的使用，私聊跟进保持',
            'resume':'已恢复此事件的跟进，原来源有效期保持',
            'cancel':'已标记事件取消，退出活动跟进；历史查询会注明已取消',
            'complete':'已标记事件完成，退出活动跟进；历史查询会注明已完成',
            'importance':'已更新事件重要度设置','keep':'已优先保留此事件，不延长来源有效期',
            'unkeep':'已解除事件优先保留，不延长来源有效期','timing':'已更新事件日期安排，原来源有效期保持；截止不代表已完成'}
        if importance=='auto':labels['importance']='已解除重要度手动锁定；当前值保持，下一条有效进展按自动规则调整'
        from .qq_event_priority import timing_text
        tail=('；原聊天和独立长期记忆保留。删除卡片正文可用 /events delete '+card['id']) if action=='stop' else (
            '；'+timing_text(data,view,self.service.clock()) if action=='timing' else '')
        return ControlReceipt(labels[action]+tail+'。')

    def command(self,message):
        args=message.text.split()
        if message.kind!='message' or message.forward_present or message.media_types:
            return ControlReceipt('请直接输入事件命令；转发或附件不作为操作授权。')
        if len(args)==3 and args[1] in {'stop','pause','resume','cancel','complete','keep','unkeep','mute'}:
            if args[1]=='mute' and not message.group_id:return ControlReceipt('此命令只停止当前群的使用，请在目标群操作。')
            cards=self.inventory(message);card=next((c for c in cards if c['id']==args[2]),None)
            return self.change(message,card,'group_stop' if args[1]=='mute' else args[1]) if card else ControlReceipt('当前场合没有可管理的本人事件。')
        if len(args)==4 and args[1]=='importance' and args[3] in {'high','normal','low','auto'}:
            card=next((c for c in self.inventory(message) if c['id']==args[2]),None)
            return self.change(message,card,'importance',importance=args[3]) if card else ControlReceipt('当前场合没有可管理的本人事件。')
        if len(args)>=4 and args[1] in {'deadline','schedule'}:
            card=next((c for c in self.inventory(message) if c['id']==args[2]),None)
            return self.change(message,card,'timing',timing={'kind':'deadline' if args[1]=='deadline' else 'appointment',
                'value':' '.join(args[3:])}) if card else ControlReceipt('当前场合没有可管理的本人事件。')
        return None
