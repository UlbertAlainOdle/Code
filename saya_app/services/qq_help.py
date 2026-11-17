"""One role-aware help projection for both priority controls and FIFO replies."""


def command_help(bridge,message):
    settings=bridge.settings;group=bool(message.group_id);owner=message.user_id in settings.owner_ids
    lines=['直接发文字对话；群内请 @我。',
        '/whoami 身份；/status 状态；/reset 重置本会话历史（不删除长期记忆或网页历史）。',
        '/topics 未完回答；/continue [结果编号] 或“继续说”续读。']
    lines.append('/tasks 本会话任务；/cancel [任务编号] 取消。已发送消息和已提交记忆不会自动撤回。' if settings.task_control_enabled else '/tasks、/cancel 需先开启任务控制。')
    if getattr(bridge,'media',None):lines.append('图片、TXT/PDF/DOCX及语音需开启对应功能；动图看首帧。收到的语音不做识别。')
    if getattr(bridge,'expressions',None):lines.append('/expression 查看表达模式；可不发附加表达。语音与大表情互斥，可配颜文字。')
    if getattr(bridge,'dialogue_experience',None):lines.append('/experience status 查看总开关、篇幅引导与长文短句语音三个状态。')
    if getattr(bridge,'context_salience',None):lines.append('/topic status 话题策略；开启后可 /topic new [新话题] 或说“我们换个话题吧”。')
    if getattr(getattr(bridge,'episodes',None),'operations',None):
        lines.append('事件可自然提出停止或删除；“忘掉当前所有个人事件”先预览，收到完整问题后回复“对的”执行，回复“取消”放弃。群里请明确个人事件或本人群跟进；共同删改仍需协调人。')
        lines.append('/events group link 原编号 另一个原编号 预览群联合视图，两张卡均需协调人授权。' if group
                     else '/events link 原编号 另一个原编号 预览个人事件联合视图。')
    if group and getattr(bridge,'group_context',None):lines.append('群内：/gcontext 状态；/gmemories 本群记忆。/gremember、/gforget、/gconfirm及/gnew需相应群权限。')
    if owner and not group:
        lines.extend(['主人好友私聊：',
            '/settings 查看设置；/set 修改；/search 内容 本轮搜索。'])
        if settings.shared_memory:
            lines.append('/remember 内容 保存；/recall 关键词 检索；/memories 待确认；/memory-save 编号 查看全文再确认；/memory-ignore 编号 忽略。')
            lines.append('/memory list/search/show/why 查看已存记忆和使用依据；也可问“你现在对我有哪些长期记忆？”')
            lines.append('/memory result 操作编号 核对一次保存的结果。')
            if settings.memory_management_enabled:
                lines.append('/memory link 原编号 另一个原编号 预览联合视图，按回执确认。link-show 按原编号查，link-result 或 link-undo 按操作编号查或撤销。')
                lines.append('/memory archive 保留编号 归档编号 仅预览证据相同的重复副本，按回执确认。archive-show 按原编号查，archive-result 或 archive-undo 按操作编号查或撤销。')
            lines.append('/memory edit/delete/block/unblock/confirm需开启网页记忆修改管理。' if not settings.memory_management_enabled else '/memory edit/delete/block/unblock/confirm 修改管理；删除/停用/恢复需预览确认。')
        else:lines.append('个人记忆共享未开，可用 /set memory on 开启；当前不读取私人长期记忆。')
        if getattr(bridge,'expressions',None):lines.append('/expression auto/random/off/voice/sticker/kaomoji 修改表达模式。')
        if getattr(bridge,'dialogue_experience',None):lines.append('/experience on/off 总开关；/experience concise on/off 篇幅试用；/experience short-voice on/off 长文短语音。也可说“关闭短句语音”等明确开关请求。')
        if getattr(bridge,'forward_input',None):lines.append('/forward-input on/off 分享记录文字读取；卡片内图片/文件节点暂不展开。')
        if getattr(bridge,'proactive',None):lines.append('/proactive on/off 主动私聊；仍受来源、免打扰和上限约束。')
        if getattr(bridge,'context_salience',None):lines.append('/topic on/off 分日降权；/topic timezone 时区。')
        if getattr(bridge,'context_links',None):lines.append('/context status/off/new 网页接续；来源需网页选择确认。')
        if getattr(bridge,'relationships',None):lines.append('/relation 关系策略；亲近程度不改变身份和记忆授权。')
        if getattr(bridge,'episodes',None):lines.append('/events 事件卡；开启需先开/topic，卡片不自动写长期记忆。')
        lines.append('/groups 群列表；/requests 邀请；/group allow/block 群号；/approve 或 /reject 邀请编号。')
    return '\n'.join(lines)
