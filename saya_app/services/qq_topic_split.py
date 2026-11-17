"""Explicit, previewed partition of whole group sources within existing quotas."""
import hashlib
import json
import re
from copy import deepcopy
from uuid import uuid4
from .qq_responses import ControlReceipt
from .qq_tasks import checkpoint_current
from .qq_topic_lifecycle import source_expiry,pending


class QQTopicSplit:
    def __init__(self,unified):
        self.u=unified;self.s=unified.service;self.store=unified.store
        with self.store.connect() as db:
            db.execute('create table if not exists qq_topic_split_previews(token text primary key,bot text,actor text,grp text,expires real,data text)')

    @staticmethod
    def fingerprint(row,policy,stops):
        return hashlib.sha256(json.dumps([tuple(row),policy,stops],sort_keys=True).encode()).hexdigest()

    def plan(self,db,message,group,parent,ids,title):
        u=self.u;now=self.s.clock();policy=u.policy()
        if not u.collecting() or not u.member(message,group):raise ValueError('权限或收集状态已变化')
        row=db.execute('select * from qq_shared_events where id=? and bot=? and grp=?',(parent,u.bot,group)).fetchone()
        if not row:raise ValueError('没有这个群话题')
        card=json.loads(row['data'])
        if card.get('epoch')!=self.s.epoch() or card['state'] not in {'active','cooling'}:raise ValueError('原话题已停止或来源失效')
        if card.get('coordinator')!=message.user_id:raise ValueError('须由此话题明确指定的协调人整理')
        if not isinstance(title,str) or not 4<=len(title)<=60 or re.search(r'[\r\n：:<>\[\]]',title):raise ValueError('请提供4至60字的独立子话题名称')
        if title==card.get('topic_anchor') or title==card['title']:raise ValueError('子话题须使用不同名称，避免同名误合并')
        for other in db.execute('select data from qq_shared_events where bot=? and grp=?',(u.bot,group)):
            c=json.loads(other[0])
            if title in {c.get('topic_anchor'),c['title']}:raise ValueError('已有同名话题，请换一个明确名称')
        claims=[x for x in card['contributions'] if not x.get('superseded') and source_expiry(card,x)>now]
        by_id={x['id']:x for x in claims}
        if not ids or len(ids)>32 or len(set(ids))!=len(ids) or not set(ids)<=set(by_id):raise ValueError('请使用当前有效来源编号，不重复或猜测旧编号')
        selected=set(ids)
        # Keep an author's commitment/withdrawal/progress chain together.
        # The preview shows every expanded source before any mutation.
        sensitive={x['actor'] for x in claims if x['kind'] in {'commitment','withdrawal','progress'}}
        actors={by_id[i]['actor'] for i in selected}&sensitive
        selected.update(x['id'] for x in claims if x['actor'] in actors)
        if any(by_id[i]['kind']=='decision' for i in selected):raise ValueError('正式及历史决定留在原话题，不能移走决定依据')
        if len(selected)>=len(claims):raise ValueError('须为原话题保留有效来源；不能把整卡搬空')
        stops=[tuple(x) for x in db.execute('select subject,at from qq_shared_event_stops where bot=? and card=? order by subject',(u.bot,parent))]
        if any(x[0]==message.user_id for x in stops):raise ValueError('请先明确恢复本人跟进，再整理此话题')
        if db.execute('select count(*) from qq_shared_events where bot=? and grp=?',(u.bot,group)).fetchone()[0]>=policy['values']['group_total']:raise ValueError('本群总容量已满，不能新建子话题')
        if db.execute('select count(*) from qq_shared_events where bot=?',(u.bot,)).fetchone()[0]>=2000:raise ValueError('群话题总容量已满')
        if db.execute('select count(*) from qq_shared_event_stops').fetchone()[0]+len(stops)>16384:raise ValueError('停止跟进元数据容量不足')
        selected=[x for x in claims if x['id'] in selected]
        return row,card,selected,stops,self.fingerprint(row,policy,stops)

    def command(self,message,group):
        args=message.text.split(maxsplit=5)
        if len(args)<3 or args[:2]!=['/events','group'] or args[2] not in {'split','split-confirm'}:return None
        if message.kind!='message' or message.forward_present or message.media_types:return ControlReceipt('请直接输入拆分命令，引用或附件不作为授权。')
        if not group or not self.u.member(message,group):return ControlReceipt('当前没有此群的参与权，本次没有整理。')
        try:
            with self.store.connect() as db:
                db.execute('begin immediate');checkpoint_current();now=self.s.clock()
                db.execute('delete from qq_topic_split_previews where expires<=?',(now,))
                if args[2]=='split':
                    if len(args)!=6:return ControlReceipt('用法：/events group split 原话题编号 来源编号,来源编号 子话题名称；先show查看来源编号。')
                    parent=args[3];ids=args[4].split(',');title=args[5].strip()
                    row,card,selected,stops,key=self.plan(db,message,group,parent,ids,title)
                    if db.execute('select count(*) from qq_topic_split_previews').fetchone()[0]>=256:raise ValueError('待确认整理已满，请稍后再试')
                    token=uuid4().hex
                    data={'version':1,'parent':parent,'ids':[x['id'] for x in selected],'title':title,'fingerprint':key,
                          'authorization':self.u.authorization(message.user_id),'membership':self.u.member_version(message.user_id,group)}
                    db.execute('insert into qq_topic_split_previews values(?,?,?,?,?,?)',
                        (token,self.u.bot,message.user_id,group,now+600,json.dumps(data,ensure_ascii=False)))
                    from .qq_event_attribution import author_ref
                    lines=[x['id']+' · '+author_ref(x['actor'],x,message)+' · '+x['kind']+' · '+x['text'] for x in selected]
                    # No truncated source preview can authorize a move.
                    preview='\n'.join(lines)
                    if len(preview)>5000:raise ValueError('完整预览超过5000字，请减少选中来源；本次没有整理')
                    return ControlReceipt('拆分预览（尚未移动）：'+title+'\n'+preview+
                        '\n承诺/退出/进展涉及同一作者时已整组纳入上方预览。原话题保留'+str(len([x for x in card['contributions'] if not x.get('superseded') and source_expiry(card,x)>now])-len(selected))+
                        '条有效来源；子话题独立管理，占本群现有总量1条，初始为降温，不占新活跃名额。原话/作者/来源保持，不延长期限；所有原停止设置继承，正式决定留原卡。删除或取消一张卡不自动作用另一张。\n确认：/events group split-confirm '+token)
                if len(args)!=4 or not re.fullmatch('[a-f0-9]{32}',args[3]):return ControlReceipt('用法：/events group split-confirm 预览确认码。')
                saved=db.execute('select * from qq_topic_split_previews where token=? and bot=? and actor=? and grp=? and expires>?',
                    (args[3],self.u.bot,message.user_id,group,now)).fetchone()
                if not saved:raise ValueError('预览已过期、不属于当前账号或已处理')
                data=json.loads(saved['data'])
                if data.get('version')!=1:raise ValueError('不支持此预览版本，请重新预览')
                if data['authorization']!=self.u.authorization(message.user_id) or data['membership']!=self.u.member_version(message.user_id,group):raise ValueError('参与权或身份已变化，请重新预览')
                row,card,selected,stops,key=self.plan(db,message,group,data['parent'],data['ids'],data['title'])
                if key!=data['fingerprint'] or {x['id'] for x in selected}!=set(data['ids']):raise ValueError('来源、停止设置、策略或版本已变化，请重新预览')
                child_id=uuid4().hex[:16];moved={x['id'] for x in selected}
                child=deepcopy(card)
                child.update(title=data['title'],title_kind='assigned_split',title_speaker=message.user_id,
                    state='cooling',contributions=deepcopy(selected),updated=max(x['at'] for x in selected),
                    split_from={'version':1,'id':row['id'],'actor':message.user_id,'at':now})
                child.pop('current_decision',None);child.pop('split_children',None);child.pop('title_unavailable',None)
                from .qq_event_attribution import source_label
                child['title_speaker_label']=source_label(message);child['topic_anchor']=data['title']
                for x in child['contributions']:
                    x['split_expiry']=min(x.get('split_expiry',float('inf')),source_expiry(card,x))
                    x['split_policy']=1
                if child.get('attention'):
                    child['attention']['pending']=pending(child);child['attention'].pop('dated_decision_until',None)
                card['contributions']=[x for x in card['contributions'] if x['id'] not in moved]
                card['split_children']=(card.get('split_children',[])+[child_id])[-32:]
                if card.get('attention'):card['attention']['pending']=pending(card)
                db.execute('update qq_shared_events set revision=revision+1,data=? where id=? and revision=?',
                    (json.dumps(card,ensure_ascii=False),row['id'],row['revision']))
                db.execute('insert into qq_shared_events values(?,?,?,?,?)',(child_id,self.u.bot,group,1,json.dumps(child,ensure_ascii=False)))
                for actor,at in stops:db.execute('insert into qq_shared_event_stops values(?,?,?,?)',(self.u.bot,actor,child_id,at))
                for x in selected:
                    if not x.get('evidence_id'):continue
                    root=db.execute('select data from qq_topic_evidence where id=? and bot=? and actor=? and source=?',
                        (x['evidence_id'],self.u.bot,x['actor'],x['source'])).fetchone()
                    if not root:raise ValueError('原来源关联无法核实，请重新读取；不能推测迁移')
                    evidence=json.loads(root[0])
                    if evidence.get('quote')!=x['text'] or evidence.get('superseded_by'):raise ValueError('来源内容或修订状态已变化')
                    matched=False
                    for link in evidence.get('links',[]):
                        if link.get('kind')=='group' and link.get('id')==row['id']:link['id']=child_id;matched=True
                    if not matched:raise ValueError('原投影关联已变化，请重新读取')
                    db.execute('update qq_topic_evidence set data=? where id=?',(json.dumps(evidence,ensure_ascii=False),x['evidence_id']))
                db.execute("delete from qq_topic_state_cache where bot=? and kind='group' and card=?",(self.u.bot,row['id']))
                db.execute('delete from qq_topic_split_previews where token=?',(args[3],))
                return ControlReceipt('已按预览移入独立子话题：'+data['title']+'；编号：'+child_id+'。原话题编号保持：'+row['id']+'；原作者和来源保持，来源不续期。后续请用新子话题名称接续；两张卡独立管理。')
        except ValueError as error:return ControlReceipt(str(error)+'；本次没有整理。')
