# QQ 第99轮：统一事件首版部署，个人跨场合自查实机通过

日期：2026-11-04，America/New_York。承接第98轮499项清单，开工全部指纹一致。用户要求按新指导实施，并明确选择：好友/个人事件门槛重要及以上；明确指定的事件协调人定案，未指定保持提议；个人容量保持、每群5活跃/20总量。原177项清单测试文件未改。

## 实际结果

统一事件策略revision1已开启，minimum_level=important、group_active=5、group_total=20。关系学习仍未开启；原QQ监督false、手动启动方式、其他四项已授权true、时区和原个人预算保持。

用户在原授权群主动/events self后确认成功。实际发送分片sent，回执与原私聊包含同一事件编号、相同明确截止和high；卡仍revision1。该例证明取消默认来源场合门槛后的本人跨场合自查，不证明其他人可以读取个人事件，也不证明群多人模型整理已全部通过。没有让用户重做M1/M2/自然遗忘或此前的跨群改时间案例。

## 交付行为

### 个人归属与好友

- 版本化统一策略与只读预览/备份/CAS激活；个人卡不复制成私聊版、群版，原ID/期限/创建来源/停止记录保持。来源会话用于审计，不再默认限制本人在哪里读取。
- 同一真实账号在授权群和好友私聊中读取/更正自己的卡；当前账号不能修改其他人的个人卡。来源hash、管理版本、有效期、明确群抑制和旧待发保护保留。
- 好友资格采用确认的全局关系等级，重要及以上；不启用模型学习、不采用群局部关系分发放全局资格。主人固定资格，原白名单作为既有准入依据保留。
- 好友申请仍由主人明确审核，不自动批准；低于门槛不能批准。确认批准且等级满足后，仅新增最小私聊准入，不把对方设为主人、不开放主人长期记忆。未知结果仍不重放。
- 原个人容量表保持；降级不批量删除好友/事件，保留本人旧卡的明确查看、停止和删除管理入口，停止新建及自动使用。

### 群共同事件

- 新群归属事件与稳定贡献ID，同群成员共用事件池，私聊通过当前群参与核验的合格好友可修改同一条。不同群、同名个人卡不自动混合；私聊目标不明先澄清。
- 每条贡献保留作者、原话、来源、有效期及proposal/objection/progress/commitment/decision/withdrawal类型。更正只能替换自己的贡献，他人异议作为新主张保存。
- 摘要区分当前决定、各方主张、未决事项；不会取平均时间、按关系高低或最后一句话制造共识。协调人由主人明确指定，只有该协调人的直接明确决定才进入当前决定；引述、假设、建议或疑问不定案。
- 群内与私聊更新同一张卡。当前群可信入站证明当场参与；私聊访问先核验群成员身份。退出、权限变化、来源到期、版本变化使旧生成/待发及历史派生失效；公共群结果可以为其他当前成员重新授权，个人结果不能这样改换身份。
- “我不去了”等短句记录本人的退出；自然停止只停止对本人的跟进，不取消他人的安排。共同完成/取消需协调人；删除整卡有本人协调人、版本、期限绑定的预览确认。
- 每群最多5活跃/20总量，新增可使较旧项降温而保留证据；每条贡献上限2048字、每卡32贡献、摘要目标2000字，原总历史token和旧日预算保留。满额明确拒绝，不靠丢弃早期未决观点腾位置；长正文仅选完整证据片段并标明不完整。
- 时间提议独立保存在贡献中，只有当前协调人的决定提供结构化日期；使用原时区和原消息时刻。决定来源过期不回退旧日期，逾期不代表完成。查询/复述不增加重要度、次数或期限。

普通群消息仍遵守原@唤醒及群授权配置；本轮没有另开全群后台采集。群讨论的模型分类和自然语言效果仍需实际多人样本，不把固定分类输出的测试当作已完成真实模型收益对照。

### API失败传递

- QQ识别stream_error，零输出或部分输出失败都不作为正常回答；失败通知发送成功不再把生成任务标为completed。共享私聊生成器finally也不保存失败错误正文为AI对话。
- 历史中与主机已发送记录匹配的完整应用错误，只从模型投影排除，原聊天、回执与用户引用保留；群语境也执行相同来源核对。
- 群补充已提交而回答失败时，静态回执明确区分已提交副作用与生成失败，不要求重复提交。
- 新增请求关联号、重试次数、异常链类型及errno的无正文诊断，不记录目标URL、凭据或原话。原有证书和主机名校验保持。
- 修复的是失败传播与错误证据污染；第96轮ConnectError的具体历史网络根因仍无法从旧日志补证，不声称任何网络故障已永久消除。

## 文件与验证

新增api_failures.py、qq_unified_events.py、两个独立Python测试文件、新统一事件网页脚本及Node测试。修改qq_bridge.py、qq_chat.py、qq_contacts.py、qq_episode_context.py、qq_event_controls.py、qq_event_priority.py、qq_group_context.py、qq_person_events.py、qq_rollback.py、qq_task_controls.py、routers/qq.py、server5-22B----.py、qq-context.html和qq_contacts.js。网页仅按需读取、textContent展示、保留草稿与CAS；关系/好友页面说明同步更新。

所有pytest先执行qq_rollback.NETWORK_GUARD。新Python共34项、新Node2项；原177项测试文件指纹保持，不修改旧断言。证据范围不累加：

- 新首批10通过；相关6文件89通过，12既有FastAPI警告。
- 扩展20通过/1新夹具失败：旧lab未创建好友审核器，新夹具显式创建后21通过。
- 后续26通过/1新夹具失败：错误地从包装回执字符串取确认码尾引号，改用payload，不放宽实际删除条件。
- 发布12文件145通过/1新夹具失败，36既有警告：回退报告字段/仿真目录与实际接口不符，按真实missing字段和knowledge_base/qq路径修正；失败XML保留。
- 最后相关63通过/18既有警告；共享历史再授权47通过/12警告；跨群边界21通过/6警告；好友最后4通过/无警告。
- 大范围终检14文件177通过、36既有警告、0失败/错误/跳过，149.49秒。随后补网页到期决定展示、已提交进展但回答失败的回执区分，以及热路径仅核对引用行/清理节流；最终新规则2文件34通过、12既有警告、0失败/错误/跳过，40.85秒，最后4个到期/容量/成员边界9.57秒通过且无警告。网页Node最终14通过，无失败。

历史执行命令（复跑须换新私有输出名，不能覆盖既有XML；旧API用例继续使用第95轮私有配置隔离夹具，原用户配置和断言不动）：

```powershell
D:/Program/anaconda3/python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import sys; sys.path.insert(0,'.runtime/qq-upgrade'); import pytest; raise SystemExit(pytest.main(['-q','-p','session95_api_fixture','tests/test_qq_unified_events.py','tests/test_qq_unified_event_boundaries.py','tests/test_qq_person_events.py','tests/test_qq_event_controls.py','tests/test_qq_event_history_guard.py','tests/test_qq_event_priority.py','tests/test_qq_task_scheduler.py','tests/test_qq_failure_shutdown.py','tests/test_api_async_hardening.py','tests/test_qq_contacts.py','tests/test_qq_working_event_capture.py','tests/test_memory_rollback_gates.py','--junitxml=.runtime/qq-upgrade/session99-release-r1.xml'])))"
D:/Program/anaconda3/python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['-q','tests/test_qq_unified_events.py','tests/test_qq_unified_event_boundaries.py','--junitxml=.runtime/qq-upgrade/session99-final-dates.xml'])))"
node --test tests/qq_unified_events_ui.test.cjs tests/qq_group_contacts_ui.test.cjs tests/qq_context_ui.test.cjs tests/qq_person_events_ui.test.cjs tests/qq_event_controls_ui.test.cjs tests/qq_event_priority_ui.test.cjs
```

首份发布XML的已知新夹具失败是保留的历史，不覆盖成全绿。最终两个新测试文件是34项，不将多轮选择相加冒称全量回归次数。

## 部署和数据保留

三次原full-control/control锁、排空和三库一致备份，仅重启后端3.14/3.17/3.24秒，均PASS；QQ原进程/登录保持。最终ready/running/reachable/accepted/matched、排空解除，队列/生成/发送/语音/活动任务为0。用户最新running意图保持。

新策略按当前预览开启，备份与CAS成功。首次启用后核查脚本访问了不存在的关系诊断URL而404，策略当时已经提交；后续先读回确认，未重复PUT，改用只读数据库核对原关系关闭。原失败尝试单列私有记录，不反复启用或覆盖设置。

原配置/凭据/人物/声线/浏览器/监督/启动、候选与修改预览保持；知识库30、非日活动事实不变、旧长期等级0初始化。系统原日活动刷新单列。旧个人事件3条保留，本轮没有由代理新建真实群事件、代发QQ消息或发起付费模型/搜索/TTS请求。

观察原起点、unknown与18缺口如实保持，短维护没有补造在线信用。新回退要求qq_unified_events_v1，旧清单读者不相容时阻止回退。最终私有session99-release-source-r3.json冻结305项全匹配，r1/r2冻结、三次备份、全部失败和历史清单均不覆盖。

## 后续入口

先读[第99轮清单](qq-implementation-manifest-2026-11-04-session99.json)与最终r3冻结；统一模式保持开启、重要及以上、群5/20、协调人定案、关系学习关闭。已确认的参数不再重复询问，也不恢复旧来源场合过滤。

个人跨场合self此次单例实机已签，不要求重发。后续有群多人自然样本时，核对各方主张→本人更正→协调人决定→私聊修改→群读回的同ID/来源/版本；真实模型固定预算对照与长周期效果仍未签，不用模拟替代。原其他待签/取消范围保持，不新增自动监控、不批量重评或擅自接管他人的个人事件。

已另请求一个新虚构群活动→/events group的短回执，以核对真实群建卡与模型连接；本轮收尾时尚未收到，不代发、不把个人self成功扩签为群生成成功。
