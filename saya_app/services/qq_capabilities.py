"""Server-derived, per-turn QQ capabilities without secrets or persona changes."""
from .qq_voice import voice_requested


def snapshot(bridge,message):
    media=bridge.media;expressions=bridge.expressions
    policy=media.state()['values'] if media else {}
    allowed=bool(media and media.allowed(message))
    mode=expressions.state()['mode'] if expressions and expressions.active() else 'legacy_optional_voice'
    voice=bridge.voice.availability(message) if bridge.voice else {
        'supported':True,'available':False,'reason':'voice_service_unavailable'}
    if mode not in {'auto','random','voice','legacy_optional_voice'}:
        voice=voice|{'available':False,'reason':'expression_mode_'+mode}
    voice=voice|{'requested_this_turn':voice_requested(message.text),
        'delivery':'server sends optional audio after confirmed text',
        'compatible_with':['kaomoji'],'incompatible_with':['sticker']}
    experience=getattr(bridge,'dialogue_experience',None)
    from .qq_dialogue_experience import component_active
    if component_active(experience,'short_voice') and mode in {'auto','random','voice'}:
        voice['short_segment_supported']=True
        voice['segment_selection']='prefer the unique explicit speech-labelled section; otherwise select an exact complete sentence'
    experience_values=experience.state()['values'] if experience else {}
    return {'voice_output':voice,'voice_input_recognition':False,'expression_mode':mode,
        'dialogue_experience':{'supported':bool(experience),'enabled':bool(experience_values.get('enabled')),
            'concise_replies':bool(experience and experience_values.get('concise_replies',True)),
            'short_voice':bool(experience and experience_values.get('short_voice',True)),
            'effective_concise_replies':component_active(experience,'concise_replies'),
            'effective_short_voice':component_active(experience,'short_voice'),
            'persistent_change_allowed':bool(not message.group_id and message.user_id in bridge.settings.owner_ids)},
        'natural_actions':bridge.natural_actions.capabilities(message) if getattr(bridge,'natural_actions',None) else {},
        'topic_salience':bridge.context_salience.state()['values'] if getattr(bridge,'context_salience',None) else {'enabled':False},
        'relationship_policy':bridge.relationships.policy()['values'] if getattr(bridge,'relationships',None) else {'enabled':False},
        'working_event_cards':bridge.episodes.policy()['values'] if getattr(bridge,'episodes',None) else {'enabled':False},
        'expressions':{'favorite_stickers':mode in {'auto','random','sticker'},
            'kaomoji':mode in {'auto','random','kaomoji'},
            'choice':'mood and current meaning; no fixed priority; silence/none allowed'},
        'image_input':bool(allowed and policy.get('intake_enabled') and policy.get('images_enabled')),
        'document_input':bool(allowed and policy.get('intake_enabled') and policy.get('documents_enabled')),
        'attachment_note':'Only verified current attachments or supplied excerpts are readable, not arbitrary old files.',
        'native_poke_response':bool(bridge.social and mode!='off' and bridge.settings.task_control_enabled),
        'long_reply_merged_forward':bool(getattr(bridge,'forward',None)),
        'ordinary_file_output':False,
        'shared_chat_record_input':{'supported':bool(getattr(bridge,'forward_input',None)),
            'enabled':bool(getattr(bridge,'forward_input',None) and bridge.forward_input.state()['values']['enabled']),
            'readable_nodes':'text; record authors are claimed, media nodes are unread markers'},
        'proactive_private_messages':{'supported':bool(getattr(bridge,'proactive',None)),
            'enabled_for_this_account':bool(getattr(bridge,'proactive',None) and not message.group_id
                and bridge.proactive.state()['values']['enabled'] and bridge.proactive.state()['values']['owner_id']==message.user_id),
            'scheduled_reminders_supported':False}}


def prompt(capabilities):
    if not isinstance(capabilities,dict):return ''
    import json
    return ('\n[Current QQ capabilities; server checked for this turn]\n'
        +json.dumps(capabilities,ensure_ascii=False)
        +'\n这是本轮真实能力和权限，不是人物设定。不要根据旧对话或“纯文字正文”推断只能发文字。'
        '你生成正文，服务器负责语音、表情、戳动、合并转发等实际投递；不要输出控制标签或假装已经投递成功。'
        'voice_output.available为true且requested_this_turn为true时，直接给适合朗读的自然短回复，'
        '正文控制在max_chars以内；若short_segment_supported=true且同时要求详细文字，可保留完整解释，另在正文里自然写一句max_chars内的完整可朗读短句，服务器只读这一句。'
        '朗读部分不附能力说明、URL、代码或舞台动作；不要说不能发语音，也不要声称已经发送。'
        '非明确请求时，声音是否有表达价值由心情与语境选择，不必每轮发。语音只与收藏大表情互斥，可配颜文字。'
        '长文短句语音、篇幅引导、语音输出总开关是不同设置；分项已开但总开关关闭时尚未生效。'
        '已有唯一语音标注段时服务器优先朗读该段；不用为此给每次普通回复加“语音：”标签。'
        '主人好友私聊可明确说“开启/关闭对话体验、篇幅引导、短句语音”执行已登记开关；其他会话不能持久修改。'
        'available为false时区分本轮禁用、模式限制、忙碌与服务不可用，不能概括为永久没有语音能力。'
        '只在用户问能力或失败影响本轮请求时简短解释，不在普通聊天复述设置，不承诺一定送达。'
        '搜索、记忆等也只按本轮权限使用；保存、修改或权限变更必须有真实服务回执，不靠口头承诺。\n')
