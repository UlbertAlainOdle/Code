"""Small typed action registry; only authenticated direct requests may execute."""
import re
from .qq_responses import ControlReceipt
from .qq_tasks import checkpoint_current


CONTROL_ALIASES={'取消当前任务':'/cancel','停止当前回答':'/cancel','查看任务状态':'/status','查看帮助':'/help'}


def control_alias(text):return CONTROL_ALIASES.get(text.strip().rstrip('。！!'))


def parse_action(text):
    text=text.strip().rstrip('。！!')
    if text in {'以后自动搜索吧','以后自动搜索','开启自动搜索','打开自动搜索','把自动搜索打开'}:return {'id':'search_mode','value':'auto'}
    if text in {'关闭自动搜索','把自动搜索关掉','以后不用自动搜索了','不要自动搜索了'}:return {'id':'search_mode','value':'off'}
    if text in {'以后自己决定用语音还是表情','自动决定表情和语音','改为自动表达','以后自动选择表情和语音'}:return {'id':'expression_mode','value':'auto'}
    features='语音|主动消息|主动互动|自动召回|保存记忆|话题降权|分享记录读取|关系分级|事件卡|对话体验|篇幅引导|精简回复|简洁回复|短句语音|长文短语音'
    match=re.fullmatch(r'(?:请|帮我)?(?:把)?('+features+r')(打开|开启|关闭|关掉)(?:吧)?',text)
    if not match:match=re.fullmatch(r'(?:请|帮我)?(打开|开启|关闭|关掉)('+features+r')(?:吧)?',text)
    if not match:return None
    a,b=match.groups();feature,verb=(b,a) if a in {'打开','开启','关闭','关掉'} else (a,b)
    enabled=verb in {'打开','开启'}
    return {'id':{'语音':'voice_output','主动消息':'initiative','主动互动':'initiative','自动召回':'recall_mode','保存记忆':'memory_write','话题降权':'context_policy',
        '分享记录读取':'forward_input','关系分级':'relationships','事件卡':'episodes','对话体验':'experience_master',
        '篇幅引导':'concise_replies','精简回复':'concise_replies','简洁回复':'concise_replies','短句语音':'short_voice','长文短语音':'short_voice'}[feature],'value':enabled}


class QQNaturalActions:
    def __init__(self,bridge):self.bridge=bridge

    def capabilities(self,message):
        owner=message.user_id in self.bridge.settings.owner_ids and not message.group_id
        return {'supported_actions':['search_mode','recall_mode','memory_write','voice_output','expression_mode','initiative','context_policy','forward_input','relationships','episodes','experience_master','concise_replies','short_voice'],
            'persistent_change_allowed':owner,'execution':'explicit current direct request or delivered version-bound proposal only',
            'one_turn_search':'/search or explicit search request; does not change persistent mode',
            'unsupported':'arbitrary commands, shell, unregistered actions or implicit privacy expansion'}

    async def execute(self,message,text=None):
        action=parse_action(message.text if text is None else text)
        if action is None:return None
        b=self.bridge
        if message.group_id or message.user_id not in b.settings.owner_ids:
            return ControlReceipt('这些持续开关仅由主人好友私聊修改；本次没有执行。')
        checkpoint_current()
        kind,value=action['id'],action['value']
        if kind in {'experience_master','concise_replies','short_voice'}:
            service=getattr(b,'dialogue_experience',None)
            if not service:return ControlReceipt('对话体验策略服务当前不可用，本次没有修改。')
            key='enabled' if kind=='experience_master' else kind
            state=service.state()
            if state['values'][key]!=value:state=service.configure(message.user_id,state['revision'],{key:value})
            current=state['values'];switch=lambda flag:'开启' if flag else '关闭'
            if key=='enabled':
                return ControlReceipt('对话体验总开关已'+switch(current['enabled'])+'；篇幅引导'+switch(current['concise_replies'])+'，长文短句语音'+switch(current['short_voice'])+'。分项选择保留。')
            title='篇幅引导' if key=='concise_replies' else '长文短句语音策略'
            note=title+'已'+switch(current[key])+('（试用）。' if key=='concise_replies' and value else '。')
            if value and not current['enabled']:note+='体验总开关仍关闭，当前不生效；可说“开启对话体验”。'
            elif key=='short_voice' and not value:note+='整篇短回复仍可按原语音规则发送；要关闭所有语音，可说“关闭语音”。'
            elif key=='short_voice' and value:
                from .qq_capabilities import snapshot
                voice=snapshot(b,message)['voice_output']
                if not voice['available']:
                    reason=voice['reason']
                    if reason=='qq_voice_disabled':note+='QQ语音输出总开关仍关闭；可说“开启语音”。'
                    elif reason.startswith('expression_mode_'):note+='当前表达模式仍限制语音；本次没有改模式，可说“改为自动表达”。'
                    elif reason=='tts_busy':note+='语音服务当前忙碌，空闲后才可尝试。'
                    else:note+='当前语音输出尚不可用；策略已保存，可在网页核对语音服务状态。'
            return ControlReceipt(note)
        if kind in {'search_mode','recall_mode','memory_write'}:
            key={'search_mode':'web_search_mode','recall_mode':'recall_mode','memory_write':'memory_write_enabled'}[kind]
            target=value if kind in {'search_mode','memory_write'} else ('auto' if value else 'off')
            state=b.controls()
            if state['settings'][key]!=target:
                await b.update_controls(state['settings']|{key:target},state['revision'],command=True,
                    inherit_fields=[x for x in state['inherit_fields'] if x!=key])
            current=getattr(b.settings,key)
            return ControlReceipt({'search_mode':'自动搜索','recall_mode':'自动召回','memory_write':'保存记忆'}[kind]
                +('已开启。' if current not in {False,'off'} else '已关闭。'))
        if kind=='expression_mode':
            state=b.expressions.state()
            if state['mode']!=value:b.expressions.configure(message.user_id,state['expression_revision'],value,confirmed=True)
            return ControlReceipt('已设为语义与心情自动选择表达；可以只发文字，语音只与大表情互斥。')
        if kind=='voice_output':
            if not b.media:return ControlReceipt('语音策略服务当前不可用，本次未修改。')
            state=b.media.state()
            if state['values']['voice_reply_enabled']!=value:
                b.media.configure(message.user_id,state['media_revision'],{'voice_reply_enabled':value},confirmed=True)
            note='语音输出已'+('开启。' if value else '关闭。')
            mode=b.expressions.state()
            if value and mode['mode']=='off':
                b.expressions.configure(message.user_id,mode['expression_revision'],'voice',confirmed=True)
            elif value and mode['mode'] not in {'voice','auto','random'}:
                note+='当前指定表情模式仍限制语音；可说“改为自动表达”后再请求。'
            return ControlReceipt(note)
        if kind=='initiative':
            if not b.proactive:return ControlReceipt('主动互动服务当前不可用，本次未修改。')
            state=b.proactive.state();values=state['values']|{'enabled':value,'owner_id':message.user_id}
            b.proactive.configure(message.user_id,state['revision'],values)
            return ControlReceipt('主动互动已'+('开启；仍受原话题来源、免打扰及上限约束。' if value else '关闭。'))
        if kind=='context_policy':
            state=b.context_salience.state()
            b.context_salience.configure(message.user_id,state['revision'],state['values']|{'enabled':value})
            return ControlReceipt('话题降权已'+('开启；原历史保留。' if value else '关闭。'))
        if kind in {'forward_input','relationships','episodes'}:
            service=getattr(b,{'forward_input':'forward_input','relationships':'relationships','episodes':'episodes'}[kind])
            state=service.state() if kind=='forward_input' else service.policy()
            values=state['values']|{'enabled':value}
            if state['values']['enabled']!=value:
                try:
                    if kind=='relationships':service.configure_policy(message.user_id,state['revision'],values)
                    else:service.configure(message.user_id,state['revision'],values)
                except ValueError:return ControlReceipt('依赖或参数尚未满足；事件卡需先明确开启话题降权。本次未修改。')
            return ControlReceipt({'forward_input':'分享记录文字读取','relationships':'关系分级','episodes':'工作事件卡'}[kind]+('已开启。' if value else '已关闭。'))
        return ControlReceipt('未登记的操作，本次没有执行。')
