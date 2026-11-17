"""Versioned event priority and explicit dates; never infer completion or renew TTL."""
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

VERSION=1
GRADES={'low':0,'normal':1,'high':2}
LABEL=r'截止(?:时间|日期|日)?|最晚|安排在|定在|集合时间'
VALUE=r'(?:\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|今天|明天|后天)(?:[T\s]*(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:Z|[+-]\d{2}:\d{2})?)?'
LABELS={'deadline':'截止时间','appointment':'安排时间'}


def parse_date(value,at,zone):
    value=str(value).strip().rstrip('。！!')
    local=datetime.fromtimestamp(at,ZoneInfo(zone))
    relative=re.match(r'^(今天|明天|后天)',value)
    if relative:
        date=(local.date()+timedelta(days={'今天':0,'明天':1,'后天':2}[relative[1]])).isoformat()
        value=date+value[len(relative[1]):]
    value=re.sub(r'^(\d{4})年(\d{1,2})月(\d{1,2})日',r'\1-\2-\3',value)
    match=re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s]*(\d{1,2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:\d{2})?)?',value)
    if not match:raise ValueError('请提供完整日期，或今天/明天/后天；时间使用24小时制。')
    year,month,day=map(int,match.group(1,2,3));precision='minute' if match[4] else 'day'
    try:naive=datetime(year,month,day,int(match[4] or 23),int(match[5] or 59),int(match[6] or (0 if match[4] else 59)))
    except ValueError:raise ValueError('日期或时间无效，请核对年月日和24小时制时间。') from None
    if match[7]:
        stamp=datetime.fromisoformat(naive.isoformat()+match[7].replace('Z','+00:00'))
    else:
        tz=ZoneInfo(zone);valid=[]
        for fold in (0,1):
            candidate=naive.replace(tzinfo=tz,fold=fold)
            if candidate.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)==naive:valid.append(candidate)
        if not valid or len({x.timestamp() for x in valid})!=1:
            raise ValueError('该本地时间不存在或有夏令时歧义，请补充明确的UTC偏移。')
        stamp=valid[0]
    return {'version':VERSION,'at':stamp.timestamp(),'precision':precision,'local':stamp.isoformat(timespec='seconds'),
        'timezone':zone,'status':'known'}


def timing_control(text):
    """Narrow explicit field edits; first-time event requests go to observation."""
    if re.match(r'^(?:请)?(?:帮我跟进|持续跟进)',text):return None
    if re.search(r'不要|不用|无需|别|不想|不能|[?？]|吗$|什么意思|为什么',text):return None
    clear=re.search(r'(?:清除|取消)(?:.{0,50}?)(截止(?:时间|日期|日)?|安排时间)[。！!]*$',text)
    if clear:return {'action':'timing','kind':'deadline' if clear[1].startswith('截止') else 'appointment','value':'clear','text':text,
        'target':re.sub(r'^(?:清除|取消)','',text[:clear.start(1)])}
    match=re.search(r'(截止(?:时间|日期|日)?|安排时间)(?:设置为|设为|改为|改到|调整为|[：:])\s*(.+?)[。！!]*$',text)
    if match:return {'action':'timing','kind':'deadline' if match[1].startswith('截止') else 'appointment','value':match[2],'text':text,'target':text[:match.start()]}
    return None


def extract_timings(text,at,zone):
    # Quoted/hypothetical dates are not statements about the current event.
    if re.search(r'[“”「」『』"`]|如果|假如|他说|她说|例如|比如',text):return {}
    found={}
    for match in re.finditer('('+LABEL+r')(?:设置为|设为|改为|为|到|是|[：:])?\s*([^，,；;。\n]+)',text):
        kind='deadline' if match[1].startswith(('截止','最晚')) else 'appointment'
        value=re.match(VALUE,match[2])
        try:
            if not value:raise ValueError('unknown')
            parsed=parse_date(value[0],at,zone)
            # Don't silently consume a second time or a missing AM/PM suffix.
            tail=match[2][value.end():]
            if re.match(r'\s*(?:\d|点|上午|下午|晚上|凌晨|中午|之前|以前|前|或|到|至|[-~～])',tail):raise ValueError('ambiguous')
        except ValueError:parsed={'version':VERSION,'status':'unknown','reason':'ambiguous_or_invalid_date'}
        if kind in found:parsed={'version':VERSION,'status':'unknown','reason':'multiple_dates'}
        parsed.setdefault('timezone',zone)
        found[kind]=parsed
    return found


def attach_timings(card,values,*,source,visibility,at,expires):
    if not values:return
    timing=dict(card.get('timing',{}))
    for kind,data in values.items():
        timing[kind]={**data,'source':source,'visibility':visibility,'observed_at':at,'source_expires':expires}
    card['timing']=timing


def timing_available(card,view,now):
    return all(value['source_expires']>now and (card.get('unified_personal') or view=='private' or value['visibility']==view)
        for value in card.get('timing',{}).values())


def timing_view(card,view,now):
    if not timing_available(card,view,now):return {}
    output={}
    for kind,value in card.get('timing',{}).items():
        if value['status']!='known':
            output[kind]={'status':value['status']};continue
        seconds=value['at']-now
        state=('overdue' if seconds<=0 else 'due_soon' if seconds<=86400 else 'future') if kind=='deadline' else (
            'scheduled_past' if seconds<=0 else 'scheduled_soon' if seconds<=86400 else 'scheduled_future')
        output[kind]={k:value[k] for k in ('local','precision','timezone')}
        if value['precision']=='day':output[kind]['local']=value['local'].split('T')[0]
        output[kind]['status']=state
    return output


def timing_rank(card,view,now):
    value=timing_view(card,view,now).get('deadline',{})
    if value.get('status')=='due_soon':return 3
    if value.get('status')=='overdue':return 2 if now-card['timing']['deadline']['at']<=86400 else 0
    return 1 if value.get('status')=='future' else 0


def timing_text(card,view,now):
    states={'overdue':'已逾期，尚未确认完成','due_soon':'24小时内截止','future':'尚未截止',
        'scheduled_past':'安排日期已过，不代表已完成','scheduled_soon':'24小时内安排','scheduled_future':'后续安排',
        'unknown':'日期需澄清','removed':'未设置'}
    return '；'.join(LABELS[kind]+'：'+(value.get('local','')+' ' if value.get('local') else '')+
        states[value['status']]+('（'+value['timezone']+'）' if value.get('timezone') else '')
        for kind,value in timing_view(card,view,now).items())


def project_text(card,text,view,now,*,observed_at=None):
    """Don't restate an older explicit date after a field edit/correction."""
    if not card.get('timing'):return text
    def replace(match):
        kind='deadline' if match[1].startswith(('截止','最晚')) else 'appointment'
        latest=card['timing'].get(kind)
        if not latest:return match[0]
        try:old=parse_date(match[2],observed_at or latest['observed_at'],latest.get('timezone','Asia/Shanghai'))
        except ValueError:return match[0]
        if latest['status']!='known' or old['at']!=latest['at']:return '[旧日期安排已更正，以当前结构化时间为准]'
        return match[0]
    return re.sub('('+LABEL+r')(?:设置为|设为|改为|为|到|是|[：:])?\s*('+VALUE+')',replace,text)


def automatic_importance(card,text,*,source,visibility,at):
    if card.get('importance_locked'):return
    if re.search(r'如果|假如|假设|要是|例如|比如|你的回复|你(?:刚才|之前)?说|机器人说|回执',text):return
    # Only new direct user evidence triggers this function. Reading, time passage,
    # summaries, duplicate messages and model confidence cannot reinforce a grade.
    clean=re.sub(r'''“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"]*"|'[^']*'|`[^`]*`''', '',text)
    clean=re.sub(r'(?:并不|不是|并非|不算)(?:非常重要|重要安排|关键任务|很重要)', '',clean)
    high=bool(re.search(r'重要安排|非常重要|优先处理|关键任务|很重要',clean))
    low=bool(re.search(r'不重要|不太重要|低优先级|可有可无|仅供练习',clean))
    if high and low:return
    before=card.get('importance','normal')
    grade='high' if high else 'low' if low else before
    reason='explicit_user_value' if high or low else 'default_or_retained'
    first=card.get('importance_rule_version')!=VERSION
    card.update(importance=grade,importance_rule_version=VERSION)
    if first or grade!=before:
        card['importance_reason']=reason
        card['importance_audit']=(card.get('importance_audit',[])+[{
            'before':before,'after':grade,'reason':reason,'source':source,'visibility':visibility,'at':at,'version':VERSION}])[-12:]


def validate(card):
    if card.get('importance_rule_version',VERSION)!=VERSION:raise ValueError('unsupported_event_priority_version')
    if any(value.get('version')!=VERSION for value in card.get('timing',{}).values()):
        raise ValueError('unsupported_event_timing_version')
