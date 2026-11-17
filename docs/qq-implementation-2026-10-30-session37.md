# 第三十七轮：自然确认操作与QQ/网页逐条时间

2026-10-30，Asia/Shanghai。用户要求QQ06完成前先有“提出保存/开启搜索建议、自然同意后自行执行”和时间感知；工作中追问过去历史是否逐条也知道，已补为双端逐条时间。199指纹匹配，原qq_bridge/qq_chat/server快照私存。人物设定不改，单条群ACL仍未接入。

## 自然确认执行

新增qq_action_proposals.py独立表，只有search_on和save_candidate两类操作。现有候选SSE提供真实候选ID，询问“要我记住…吗”；搜索已配置但QQ off且当前问题需要实时资料时提出开启自动搜索建议。候选保存需要主人共享私聊及写入开启；搜索只调整QQ自动搜索，不改变网页或其他权限。模型候选来自现有服务，不解析任意文字为系统指令。

建议先prepared，实际成功发送且可核对问题文本才pending；仅当前主人好友私聊的同一bot/用户/shared或isolated范围、同一QQ话题及当前设置revision可同意，10分钟到期。自然同意词与具体动作匹配，另一个动作的“保存吧”不能开启搜索；拒绝/换题撤销，群与其他账号不激活。引用同意必须匹配真实建议回执；无引用时只接受唯一待确认建议。先原子领取executing，再复用现有memory-save/QQControls CAS，动作回执写当前QQ上下文，重复同意不重复写。执行中重启→unknown不重试，不谎报未确认副作用撤销。

兼容真实旧候选ID的时间戳小数格式，测试实际KnowledgeBase.add_memory_candidate和服务器_qq_memory_action，确认一次、重复同意仍一条。未自动更改生产开关，未发送真实实验消息。网页自然语言动作闭环未实现，不对网页也已支持作泛化声明。

## 时间检查与增强

原网页needs_time_context只对时间意图/日活动注入当前时间；普通“你好”可能没有。QQMessage原没有发送时间。现在保存规范化QQ发送timestamp（经过原时效验证）、上次会话更新/间隔，加入每轮Server conversation clock；网页与QQ实际API/agent/默认本地提示均可见当前Asia/Shanghai时刻，未知/负间隔保持unknown，不把所有历史当刚刚发生。time-intent仍负责日活动等门控，不通过开启日活动污染所有聊天。

用户追加逐条历史后新增QQTurnTimes：在成功完成一轮后记录scope/session、内容SHA与两条实际时间，不复制正文；有界清理、跨人/话题隔离。QQ共享主人旧历史可从已有MemoryManager记录恢复对应time，隔离群旧记录没时间就unknown。网页沿用load_session_full每条time，只读且不切当前会话。根据角色+精确内容、重复出现顺序匹配，再做prompt-only历史投影，原文件与人物不重写。时间片段不应被模型复制到新回答。

逐条时间附在本轮实际参与上下文的历史消息上（范围有界，不无限导入全部聊天），补充当前时刻与最近间隔。QQ05来源历史本身保留time/来源范围，权限边界不变。源缺失/内容无法精确匹配/时间不合法均不编造；不能宣称所有旧QQ群消息可以准确补回。

## 验证

```text
-m pytest tests/test_qq_actions_time.py tests/test_qq_chat_runtime.py tests/test_qq_context_integration.py tests/test_qq_task_runtime.py tests/test_qq_capabilities.py tests/test_qq_bridge.py tests/test_qq_controls.py tests/test_qq_task_scheduler.py tests/test_prompt_time_gating.py tests/test_qq_owner_alias_prompt.py -q --junitxml=.runtime/qq-upgrade/session37-history-final.xml --basetemp=.runtime/qq-upgrade/session37-history-final --tb=short
-m pytest tests/test_qq_actions_time.py -q --junitxml=.runtime/qq-upgrade/session37-focused-v2.xml --basetemp=.runtime/qq-upgrade/session37-focused-v2 --tb=short
```

历史增强相关200通过/20既有FastAPI弃用警告；末段21聚焦/4警告，含最后补的bridge真实记录并传入下一轮验证；无skip/xfail。选择重叠不相加。初步60、中间195/196及20聚焦日志保持；末段仅加不复制时间前缀的文案和记录集成断言，未无故重复大回归。实际API分别验证QQ/网页历史用户/AI消息09:00/09:00:05对应时间；5小时跨度18000秒、同字重复排序、缺失unknown和话题重置隔离通过。旧纯build_prompt时间门控测试保持，新增实际流路径每轮注入单独验证。

动作覆盖发问送达前不可执行、自然否定、超时/配置变更/主体/会话切换、换题/模糊同意、限定动作、执行崩溃、真实bridge问答闭环与真实临时KB候选一次保存。不是付费模型或真实QQ显示验收。

## 部署与回退

两次正常排空、三库一致性备份（首版与用户中途追加历史增强），最终PASS约3.56秒恢复，QQ ready/running/matched。23项/继承、人物风格/凭据/浏览器归档、17结果36分段17策略/群及QQ原进程保持；原观察起点与4段缺口、监督/自启动不变，无QQ代发或付费探测。新增qq_action_proposals与qq_turn_times实际可用，基础人物提示常量与第三十三轮一致。默认没有待执行建议，不在部署时自行开启搜索/提交记忆。

QQMessage新增sent_at和previous_conversation_at默认0兼容旧排队记录；回退旧源码时旧构造器不能接受新字段，必须先保存当前队列备份并只对待处理旧源码投影去掉这两个字段。隔离取第三十七轮前QQMessage源码验证：直接新payload不兼容、投影后原身份恢复，报告session37-rollback-schema.json保留。不写生产旧库、不重放发送未知操作，不称无条件直接回退。

当前优先两项已部署，真实自然确认/时间表达反馈待收。QQ05默认显式绑定保持；QQ06/07逐条群记忆ACL仍仅基础独立，接下来按其既定入口做授权UI及检索/提交/发送屏障。QQ08媒体尚未开始。最新[第三十七轮清单](qq-implementation-manifest-2026-10-30-session37.json)，私有session37-resume.json。
