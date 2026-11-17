"""Conservative lifecycle frames and explicit selectors, never lexical permission."""
import re

DEICTIC={'这件事','这个事','此事','这事','这件','这个','那件事','那个事','刚才那件事',
    '之前那件事','这个计划','这项计划','当前计划','这个任务','这项任务','当前任务','这个事件','此事件'}


def lifecycle_intent(text):
    parts=re.split(r'[，,；;。！!\n]+',text.strip(),maxsplit=1)
    head=parts[0].strip();tail=parts[1].strip() if len(parts)>1 else ''
    if (not head or head.startswith('/') or re.search(
        r'''[?？“”「」『』‘’"'`]|什么意思|怎么办|会怎样|能否|是否|为什么|怎样|能不能|可不可以|请问|完成过|办完过|做完过|曾经|以往|过去|以前|当年|小时候|我(?:之前|早就)|当时|如果|假如|要是|可能|也许|好像|似乎|希望|期待|盼望|我觉得|他说|她说|有人说|据说|引用|转述|改为|改成|第一(?:步|阶段)|一部分|部分完成|只完成|还差|还剩|还没|尚未|未完成|没完成|并未|没有|并不|并非|不算|不想|不要|不能|取消暂停|取消(?:了)?的''',head)):
        return None
    if tail and re.search(r'^(?:才怪|不对|开玩笑|只是(?:举例|假设|说说))|(?:其实|但是|不过).{0,12}(?:还没|未完成|没做完|没有完成|只是举例|是假设)',tail):return None
    target='';action=None;management=False
    mark=re.fullmatch(r'(?:请)?(?:把|将)?(.{1,80}?)(?:标记为|标为|设为|设置为|记为)(已完成|完成|已取消|取消)(?:吧|了)?',head)
    cancel=re.fullmatch(r'(?:请)?取消(.{1,80}?)(?:吧|了)?',head)
    statement=re.fullmatch(r'(.{1,80}?)(?:已经|已)?(?:全部|全都|都|完全)?(完成|办完|做完|结束|搞定|取消)(?:了|啦)?',head)
    if mark:target=mark[1].strip();action='complete' if '完成' in mark[2] else 'cancel';management=True
    elif cancel:target=cancel[1].strip();action='cancel';management=True
    elif statement:target=statement[1].strip();action='cancel' if statement[2]=='取消' else 'complete'
    else:return None
    target=re.sub(r'^(?:我这边|我)?(?:把|将)','',target).strip()
    if target in {'我','我们','你','大家','已经','已','全部','都','完全'}:return None
    target=re.sub(r'^(?:事件|计划|任务)\s+(?=[a-f0-9]{16}\b)','',target)
    return {'action':action,'importance':None,'text':head,'target_selector':target,
        'strict_target':True,'dialogue':bool(tail) or not management,'dialogue_text':text}


def selector(text,action):
    text=text.strip().rstrip('。！!').strip()
    if re.fullmatch(r'(?:这件事|这件|此事)(?:很重要|非常重要)[，,]*(?:但是|但)?先别提',text):return '这件事'
    patterns={
        'stop':r'(?:忘掉|忘记|删掉|删除|停止跟进|不再跟进|不用再跟进|不用跟进|别再跟进|别再记着)\s*(.+)',
        'pause':r'(?:先别跟进|先别提|暂停跟进|暂停|暂时不跟进|暂时不提)\s*(.*)',
        'resume':r'(?:重新跟进|恢复跟进|继续跟进)\s*(.*)',
        'group_stop':r'(?:别|不要|不用|禁止)(?:再)?在(?:这个|这|本)?群(?:里)?(?:提|说|跟进)\s*(.*)',
    }
    m=re.search(patterns[action],text) if action in patterns else None
    if not m:return None
    value=m[1].strip().rstrip('吧了。！!').strip()
    value=re.split(r'[，,；;]',value,maxsplit=1)[0].strip()
    if not value:return '这件事'
    if value not in DEICTIC:
        value=re.sub(r'(?:的(?:事件|话题|计划|任务)|这件事|这件|这个事)$','',value).strip()
    return value


def named_match(card,target):
    if not target or target in DEICTIC:return False
    names={card.get('topic_anchor'),card.get('title')}
    names.update(f.get('topic_anchor') for f in card.get('facts',[]) if not f.get('superseded'))
    if target in names:return True
    def normalized(value):return re.sub(r'公开|虚构|测试用|测试|实验用|实验','',re.sub(r'^本群','',value))
    clean=normalized(target)
    return len(clean)>=4 and (clean in normalized(card.get('title','')) or any(
        clean in normalized(f['quote']) for f in card.get('facts',[]) if not f.get('superseded')))
