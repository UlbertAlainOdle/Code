# QQ 第112轮：闲置主导的群话题淡出

日期：2026-11-04，America/New_York。用户同意第111轮方向并要求实施，明确待处理内容的权重应更低，普通讨论不必等到有结果才结束。

## 权重和成本

采用本地规则：`score = 闲置衰减 × (0.90 + 0.10 × 待处理缓冲)`。两个分量都随闲置趋零，**没有10%永久保底分**。未定论、普通问题或多人意见不一致不会单独触发待处理保护；明确承诺/当前正式决定仅提供弱缓冲，完成/撤回及共同终态按原权限处理，不把冷却改写为已完成或已取消。

衰减计算、热度、待处理弱信号和排序不调用模型；复用已有路由的真实作者、kind/mode、来源和结构化时间，没有新增模型返回字段，也没有逐条额外分类。更久保留可能间接增加后续候选和上下文，不能保证总token零变化；原候选4/4、输入10000、历史/旧日预算及归纳每天8次等限制保持，本轮不重做付费模型对照。

## 已实现规则

- 普通半衰期45分钟；按有效贡献的30分钟时间桶及真实作者人数提供有上限的缓冲，最多4倍即3小时。同作者同时间桶的消息数量不累计倍率；极高消息量不产生无限寿命。
- 活跃阈值0.45、休眠阈值0.10；普通无缓冲话题约45分钟后降温、约2小时23分钟后休眠。休眠退出默认群列表，明确名称/编号仍可在来源有效时读取或接续。
- `/events group dormant` 可只读列出休眠话题，查询本身不加热。
- 普通证据闲置至少6小时且衰减充分后清理；最大热度下约20小时没有有效接续也会退出。真实内容持续接续可继续保留，不按创建后固定天数截断。
- 实质状态回问只提供累计最多30分钟的关注缓冲，不改内容观察时间；相同请求去重，超过上限不继续续命。管理自查、附和、机器人回复和重复贡献不增热度。
- 有明确日期、且由主机确认的当前协调人决定，可在休眠后单独保留其必要决定证据，至日期后一天、同时不超过最后实质接续后7天。只保护该条决定，不给全部历史观点续期、不让话题继续占据活跃关注；共同完成/取消后取消这层保护。普通没结论的讨论不享有这一例外。

这些是首版可解释参数，不是普遍最优值。时间桶/人数限幅和弱待处理缓冲不代表判断出了群外热度或现实任务是否已完成。只依据已接收/@消息，未扩大监听。

## 来源、容量及兼容

新策略独立版本化，默认关闭，经本轮授权启用后只给后续实际路由产生的群贡献加 `idle_policy=1`；旧来源保留原expires，不批量修改旧卡、不恢复过期内容。新旧贡献可以同卡共存，逐来源校验。个人投影的原期限和普通群窗口保持，不能因为群来源更久就延长个人卡。

动态期限由来源标记与原卡关注元数据计算，在读取投影中给出有效expires，不回写旧来源的原时间。成功的新内容更新仍有效的新策略证据及其规范路由索引；过期旧记录不通过新消息恢复。有效来源/版本继续约束归纳、时间字段、生成和待发检查；变化后旧缓存弃用，过期或删卡时清理派生正文。明确停止者不会因他人继续讨论而恢复自动使用。

容量仍为每群5活跃/20总量、每卡32条；活跃容量优先按衰减分和最近内容排序，休眠不进入弱相关自动召回。尚未实现满额后对所有独立有效来源的自动归档/分支；有效材料真正超限仍拒绝并说明，不静默删分歧或加大容量。该剩余项不由本轮热度规则冒充完成。

新增独立主人认证/CAS的生命周期策略接口，保留原QQ设置项和统一策略。启用后加入 `qq_topic_lifecycle_v1` 最低读者门禁；有动态来源时即使关闭未来采集也保留门禁，防止旧读者按原一小时误删或误解释。关闭只影响未来来源，已有来源仍按记录版本读取。

## 文件与验证

新增 `qq_topic_lifecycle.py`、`test_qq_topic_lifecycle.py`；修改 `qq_unified_events.py`、`qq_topic_routing.py`、`qq_chat.py`、`qq_rollback.py`、QQ路由，以及本方案/状态文档。原186项测试文件不改。

专项包括90/10且无保底、单人刷屏/多人跨时段与热度上限、无结果清理、明确日期例外只保留决定、旧策略不迁移、混合来源到期、真实接续、重复/管理查询、个人期限保持、停止、非零epoch冷构造、实际响应器超过旧窗口读取且无额外选择器、API认证/CAS和回退门禁。首轮9通过；扩展后148回归通过；补明确日期保护时1个简化测试来源漏expires导致失败，补齐真实来源字段后最终结果见状态及清单，失败XML保留，不修改业务读取来纵容缺失来源。

本轮无代理QQ代发、模型/搜索/TTS探针。模拟时钟和真实响应器测试不是实际QQ验收。部署及策略启用各有独立私有备份和保留检查，最终结果见状态文档。

## 续接

待一次新的普通群话题实际建卡，核对新来源版本、真实作者、关注时间、无长期保存及实际投递，不重做原协调人/多人分工。不要要求真人等待几小时才能证明边界；长时间边界使用可控时钟，真实长期使用另记。

剩余：满额证据整理、跨投影完整更正/撤回矩阵、更多固定预算语料及长周期观察；抽取式归纳自然长话题真人验收仍待完成。重要及以上、原群仅@、监督/手动启动、关系学习关闭、时区预算及观察台账保持。

发布结果：151项/36个既有警告，最终22项相关检查/12个既有警告通过（重叠不累计）；112r5冻结316项一致。仅后端受管重启3.14秒PASS；单独启用策略revision0→1，7项保留检查PASS。原卡、来源、设置及观察起点/unknown/18缺口保持。真正QQ新普通话题路径仍待验收。

最终测试命令记录（历史临时目录及XML不得覆盖，再跑须换新名称）：

```powershell
D:/Program/anaconda3/python.exe -X utf8 -c "import sys,pytest; from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD,{}); sys.path.insert(0,'.runtime/qq-upgrade'); sys.exit(pytest.main(['-q','--tb=short','--basetemp=.runtime/qq-upgrade/session112-tests-r5','-p','session104-test-fixture','tests/test_qq_topic_lifecycle.py','tests/test_qq_topic_routing.py','tests/test_qq_topic_state.py','tests/test_qq_unified_event_boundaries.py','tests/test_qq_unified_events.py','tests/test_qq_topic_binding.py','tests/test_qq_responder_lifecycle_binding.py','tests/test_qq_event_controls.py','tests/test_qq_event_compaction.py','tests/test_qq_person_events.py','tests/test_qq_rollback.py','--junitxml=.runtime/qq-upgrade/session112-release-final.xml']))"
D:/Program/anaconda3/python.exe -X utf8 -c "import sys,pytest; from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD,{}); sys.path.insert(0,'.runtime/qq-upgrade'); sys.exit(pytest.main(['-q','--tb=short','--basetemp=.runtime/qq-upgrade/session112-tests-r6','-p','session104-test-fixture','tests/test_qq_topic_lifecycle.py','tests/test_qq_unified_events.py','--junitxml=.runtime/qq-upgrade/session112-final-command.xml']))"
```
