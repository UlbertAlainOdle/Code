"""Reversible QQ turn guidance and bounded content-free presentation telemetry."""
import json
import time
from pydantic import BaseModel,ConfigDict


class ExperiencePolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled:bool=False
    concise_replies:bool=True
    short_voice:bool=True


def component_active(service,name):
    if not service:return False
    values=service.state()['values']
    return bool(values.get('enabled') and values.get(name,True))


def turn_guidance(enabled):
    if not enabled:return ''
    return ('\n[QQ turn pacing; not a character definition]\n'
        '结合本轮问题、已授权上下文、关系和心情，先在心里判断对方要接话、陪伴、简答还是充分展开。'
        '人物性格、称呼和既有设定保持原样。不要把判断过程、类别或字数目标写出来。'
        '招呼、简单确认、轻松接话默认一句短话，通常不超过25字；日常分享或情绪回应默认一两句，通常20～50字；'
        '简单事实问答直接给答案及必要前提，不因它是问题就展开科普；一般解释通常80～180字。以上是软目标，不是最低字数，更不是截断线。'
        '明确要详细、复杂推理、多项要求、代码或步骤时按需充分展开，逐项答全；用户指定的篇幅优先。'
        '简单一句接住即可，不因历史回答长就沿用长文；不要习惯性把复述、分析、安慰、建议和总结全部说一遍。'
        '没有求建议时不自动布置任务，不每次用追问收尾。一个感受、一种安慰或一个形象表达一次就够，'
        '不要再叠加同义保证、人物自述或解释自己如何陪伴。招呼无需顺带纠正作息；只有时间误会影响本轮问题时才提醒。'
        '保持原人物的语气与情感，用一个贴切的细节表达即可；完成当前一个回应意图后停下来，等对方接话。'
        '思考是否深入与说多少分开：认真思考后也可以只说一句结论，复杂任务不能为短而漏答。'
        '若语气适合声音，可把一句真实、完整、适合朗读的话自然放在正文里；长解释仍可保留。'
        '服务器会从正文选择声音载体，不输出声音/表情计划，不声称已经发送，不人为增加表情或语音次数。\n')


def pacing_reminder(channel_turn):
    if not channel_turn or not channel_turn.get('experience_enabled'):return ''
    return ('\n[Current turn pacing]\n先看本轮到底要什么。普通接话只说一句，情绪陪伴只接住最重要的一点；'
        '已表达的关心不要再换几种说法，不追加无请求的建议、人物自述、作息提醒或例行追问。'
        '简短也保留原人物的称呼与情感；意图完成便结束。用户要详细、复杂任务或多项要求时则逐项答全，不能省略必要信息。\n')


class QQDialogueExperience:
    MAX_ROWS=4096
    TTL=7*86400

    def __init__(self,bridge,*,clock=time.time):
        self.bridge,self.store,self.clock=bridge,bridge.store,clock
        with self.store.connect() as db:
            db.executescript('''create table if not exists qq_experience_policy(bot text primary key,revision integer,policy text);
                create table if not exists qq_experience_metrics(result_id text primary key,created real,metrics text);''')
        self.prune()

    def state(self):
        with self.store.connect() as db:
            row=db.execute('select revision,policy from qq_experience_policy where bot=?',(self.bridge.settings.bot_id,)).fetchone()
        return {'revision':row[0] if row else 0,'values':ExperiencePolicy.model_validate(json.loads(row[1])).model_dump() if row else ExperiencePolicy().model_dump()}

    def configure(self,owner,revision,values):
        if owner not in self.bridge.settings.owner_ids:raise PermissionError('owner_required')
        if not isinstance(values,dict):raise ValueError('invalid_experience_policy')
        values=ExperiencePolicy.model_validate(self.state()['values']|values).model_dump()
        with self.store.connect() as db:
            db.execute('begin immediate')
            row=db.execute('select revision from qq_experience_policy where bot=?',(self.bridge.settings.bot_id,)).fetchone()
            if type(revision) is not int or revision!=(row[0] if row else 0):raise ValueError('experience_policy_changed')
            db.execute('insert or replace into qq_experience_policy values(?,?,?)',(self.bridge.settings.bot_id,revision+1,json.dumps(values)))
        return self.state()

    def prune(self):
        with self.store.connect() as db:
            db.execute('delete from qq_experience_metrics where created<?',(self.clock()-self.TTL,))
            db.execute('delete from qq_experience_metrics where result_id in (select result_id from qq_experience_metrics order by created desc,rowid desc limit -1 offset ?)',(self.MAX_ROWS,))

    def record(self,result_id,message,answer,plan):
        """Whitelist only measurements. Never persist message text, IDs or anchors."""
        from .qq_responses import dialogue_body
        with self.store.connect() as db:
            parts=db.execute('select state from qq_delivery_parts where result_id=?',(result_id,)).fetchall()
        numeric=getattr(message,'experience_timing',{})
        metrics={'version':2,'pacing_enabled':bool(getattr(message,'experience_enabled',False)),
            'short_voice_enabled':plan.get('short_voice_enabled') is True,
            'body_chars':len(dialogue_body(answer)),'parts':len(parts),'sent_parts':sum(p[0]=='sent' for p in parts),
            'unknown_parts':sum(p[0]=='unknown' for p in parts)}
        for key in ('generation_ms','first_bubble_ms','text_complete_ms','reply_complete_ms'):
            if key in {'text_complete_ms','reply_complete_ms'} and (not parts or any(p[0]!='sent' for p in parts)):continue
            value=numeric.get(key)
            if type(value) in (int,float) and 0<=value<=3600000:metrics[key]=round(value)
        # Enumerated values are checked rather than serialising exception/model output.
        enumerations={
            'kind':{'none','sticker','kaomoji','voice'},
            'decision_reason':{'off','text_only','empty','body_limit','notes','structured_body','not_allowed',
                'voice_mode','semantic_none','semantic_choice','random_none','random_choice','invalid_choice','invalid_anchor',
                'selector_timeout','selector_error','selector_json','selector_unconfigured','selector_truncated'},
            'selector_finish':{'stop','length','max_tokens','tool_calls','unknown'},
            'speech_source':{'labelled_segment','model_anchor','whole_body','explicit_fallback'},
            'voice_reason':{'available','not_selected','explicit_request','semantic_choice','voice_mode','invalid_speech_span','invalid_speech_section','labelled_section_too_long',
                'permission_unavailable','voice_service_unavailable','qq_voice_disabled','turn_text_only','legacy_delivery',
                'tts_disabled','local_tts_unavailable','tts_busy','text_length_limit','not_speech_text','no_speakable_text'}}
        for key,allowed in enumerations.items():
            if plan.get(key) in allowed:metrics[key]=plan[key]
        for key in ('selector_ms','speech_chars','selector_token_budget'):
            value=plan.get(key)
            if type(value) in (int,float) and 0<=value<=3600000:metrics[key]=round(value)
        with self.store.connect() as db:
            db.execute('insert into qq_experience_metrics values(?,?,?) on conflict(result_id) do update set metrics=json_patch(qq_experience_metrics.metrics,excluded.metrics)',(result_id,self.clock(),json.dumps(metrics)))
        self.prune()

    def outcome(self,result_id,state):
        if state not in {'sent','skipped','failed','unknown','cancelled','timed_out','expired','interrupted'}:return
        with self.store.connect() as db:
            db.execute("update qq_experience_metrics set metrics=json_set(metrics,'$.expression_outcome',?) where result_id=?",(state,result_id))
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_voice_deliveries'").fetchone():
                row=db.execute('select error from qq_voice_deliveries where result_id=?',(result_id,)).fetchone()
                if row and row[0] in {'permission_changed','tts_timeout','tts_busy','cache_full','cancelled','tts_or_audio_failed','transport_unknown'}:
                    db.execute("update qq_experience_metrics set metrics=json_set(metrics,'$.voice_outcome_reason',?) where result_id=?",(row[0],result_id))

    def summary(self):
        self.prune()
        with self.store.connect() as db:
            rows=db.execute('select created,metrics from qq_experience_metrics order by created').fetchall()
        values=[json.loads(r[1]) for r in rows]
        def counts(key):
            result={}
            for x in values:
                if key in x:result[x[key]]=result.get(x[key],0)+1
            return result
        def quantile(key,fraction):
            items=sorted(x[key] for x in values if key in x)
            return items[round((len(items)-1)*fraction)] if items else None
        return {'count':len(rows),'first_sample_at':rows[0][0] if rows else None,'last_sample_at':rows[-1][0] if rows else None,
            'coverage':'bounded retained replies; not complete uptime or all conversations','retention_seconds':self.TTL,
            'max_samples':self.MAX_ROWS,'decision_reasons':counts('decision_reason'),'voice_reasons':counts('voice_reason'),
            'kinds':counts('kind'),'outcomes':counts('expression_outcome'),
            'voice_outcome_reasons':counts('voice_outcome_reason'),'selector_finishes':counts('selector_finish'),
            'body_chars_p50':quantile('body_chars',.5),
            'latency_ms':{key:{'p50':quantile(key,.5),'p90':quantile(key,.9)} for key in ('generation_ms','first_bubble_ms','text_complete_ms','reply_complete_ms')}}
