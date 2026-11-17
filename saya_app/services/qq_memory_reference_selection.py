"""Bounded source selection and durable, non-replayed batch save results."""
import json
import re
import time
from .qq_responses import ControlReceipt
from .qq_memory_access import MemoryAccessDenied
from .qq_chat_saves import fingerprint


def selection_request(text,count):
    """Only a delivered, pending list gives these numbers their meaning."""
    text=text.strip().rstrip('。！!')
    fragment=re.fullmatch(r'(?:只|仅)?(?:保存|记住)?第?([1-9]\d?|[一二三四])条(?:中(?:的)?|里(?:的)?)[“「"](.{1,2000})[”」"]',text,re.S)
    if fragment:
        number='一二三四'.find(fragment[1])+1 if not fragment[1].isdigit() else int(fragment[1])
        return {'indices':[number-1],'fragment':fragment[2]} if number<=count else {'invalid':True}
    all_request=re.fullmatch(r'(?:把)?(?:上面|刚才|这|那)([两二三四234几])条(?:都)?(?:保存|记住)',text)
    if all_request:
        expected={'两':2,'二':2,'三':3,'四':4,'2':2,'3':3,'4':4,'几':count}[all_request[1]]
        return {'indices':list(range(count))} if expected==count else {'invalid':True}
    if text in {'全部','全选','都保存','全部保存','都记住','全部记住'}:return {'indices':list(range(count))}
    exclude=re.fullmatch(r'(?:排除|去掉|不要)(.+?)(?:后)?(?:其余|其他|剩下的)?(?:都)?(?:保存|记住)?',text)
    if not exclude:exclude=re.fullmatch(r'除了(.+?)[，,]?\s*(?:其余|其他|剩下的)(?:都)?(?:保存|记住)',text)
    match=exclude or re.fullmatch(r'(?:(?:只|仅)?(?:选|选择|保存|记住))?(.+)',text)
    if not match:return None
    body=match[1].translate(str.maketrans('一二三四','1234'))
    body=re.sub(r'第|条|号','',body)
    if not re.fullmatch(r'\d{1,2}(?:\s*(?:[、,，和及]|以及)\s*\d{1,2})*',body):return None
    numbers=[int(x)-1 for x in re.findall(r'\d+',body)]
    if len(set(numbers))!=len(numbers) or any(x<0 or x>=count for x in numbers):return {'invalid':True}
    indices=[x for x in range(count) if x not in numbers] if exclude else numbers
    return {'indices':indices} if indices else {'invalid':True}


class QQMemoryReferenceSets:
    def __init__(self,reference):
        self.r=reference;self.b=reference.b
        with self.b.store.connect() as db:
            db.execute('create table if not exists qq_memory_reference_batches(id text primary key,scope text,session text,fingerprint text,result text)')

    def fragment(self,option,text):
        if not text or text not in option['text']:return None
        return {**option,'text':text,'fragment':True}

    def list(self,message,session,options):
        if not 1<=len(options)<=4:
            self.b.action_proposals.supersede(message.scope+':shared',session)
            return ControlReceipt('可定位来源过多或为空，请说明作者、主题或更长的原文片段；本次尚未保存。')
        epoch=self.r.kb.memory_use.snapshot();expires=min(time.time()+600,*[o['source']['expires'] for o in options])
        options=[{**o,'source':{**o['source'],'expires':expires}} for o in options]
        payload={'options':options,'session':session,'epoch':epoch,'expires':expires}
        question='有多个可保存的来源，请选择编号（尚未保存）：\n'+'\n'.join(f'{i}. {o["author"]}'+('（'+o['recency_label']+'）' if o.get('recency_label') else '')+'：'+o['text'] for i,o in enumerate(options,1))+'\n可回复“选1”“选1、2”“都保存”或“排除2”；选择后展示完整正文，再确认保存。'
        return self.r.ask(message,session,'select_reference',payload,question)

    def preview(self,message,session,options):
        if len(options)==1:return self.r.preview(message,session,options[0])
        plans=[self.r.plan(message,session,o) for o in options]
        if not 2<=len(plans)<=4 or not all(self.r.valid(message,p) for p in plans):return ControlReceipt('来源已变化，未准备批量保存；本次尚未保存。')
        if len({p['content'] for p in plans})!=len(plans):return ControlReceipt('选择中有重复正文，请重新选择；本次尚未保存。')
        payload={'plans':plans,'options':options}
        text='长期记忆批量完整预览（尚未保存）：\n'+'\n'.join(f'{i}. 作者：{p["author"]}\n{p["content"]}' for i,p in enumerate(plans,1))+f'\n共{len(plans)}条，分别保存。可先“排除2”或“只保存1”重新预览；确认本次全部正文请回复“记住”，放弃请回复“取消”。'
        return self.r.ask(message,session,'save_reference_set',payload,text)

    def commit(self,message,proposal,payload):
        plans=payload['plans'];scope=message.scope+':shared'
        try:self.r.authorize(message,plans[0]['session'])
        except (ValueError,PermissionError,MemoryAccessDenied):return '批量保存权限或会话已变化，本次没有执行。'
        with self.b.store.connect() as db:
            old=db.execute('select * from qq_memory_reference_batches where id=?',(proposal,)).fetchone()
            if old:
                if (old['scope'],old['session'],old['fingerprint'])!=(scope,plans[0]['session'],fingerprint(plans)):
                    return '批量计划身份不一致，本次没有执行。'
                from .qq_memory_reference_dialogue import QQSaveReceipt
                result=json.loads(old['result'])
                return QQSaveReceipt(self.receipt(result),all(item['state']=='committed' for item in result))
        return self.r.commit_proposal(message,'save_reference_set',payload,proposal)

    def receipt(self,result):
        committed=[(i+1,x['memory_id']) for i,x in enumerate(result) if x['state']=='committed']
        prefix=f'已确认并保存全部{len(result)}条长期记忆。' if len(committed)==len(result) else f'批量未全部完成：已确认保存{len(committed)}/{len(result)}条；其余未执行、失效或结果待核对，不会自动重试。'
        return prefix+('\n'+'\n'.join(f'{i}. 编号：{mid}' for i,mid in committed) if committed else '')+'\n可用 /memory list 核对实际存储。'
