# 第82轮：M1可信来源与来源事件首版

日期：2026-11-03，America/New_York。按用户“开始实现详细计划”的指令推进M1；第81轮445项源码指纹开工一致。M2～M5未开始，M1代码部署与真人签收分开记录。

## 行为与范围

新增memory_origin主机上下文。QQ入口只使用原桥接器验证后的bot/user/message/session ID，网页入口绑定实际网页操作；不从昵称、文件名、引用、模型建议、请求metadata或source_detail推断可信QQ来源。verified_subject是来源发言账号，并非自动认定事实所描述的人。网页没有核验QQ账号时不冒充QQ主体。origin_version=1与独立origin字段进入V4 metadata及V3/source_detail白名单投影。

创建来源保留，后来编辑记last_modified_channel/last_modified_origin。旧记录无可信来源时仍unknown，即使现在从QQ编辑，也只证明本次修改来自QQ。只读展示为旧记录补unknown视图，不回写历史数据；本机29条旧事实均unknown。没有猜测回填或QQ倍率。

来源事件保存标题、原因、当句最多240字摘要、消息证据引用、来源账号、可见范围、观测时间和修订引用；不补造发生时间、不声称读取了完整前因后果。短句允许更短，不凑80字。一个原消息事件ID可关联多个事实；同一消息重放不追加重复来源事件。每事实最多16项，保留创建事件及最新15项，原V4版本账本仍留旧快照，不新建无界聊天副本。来源字段不占内容标签，添加来源不改变重要性、星标、锁定或授权。

接入QQ显式/自然保存、候选创建/再次观测/确认、原版本化edit，网页手动/显式/V4更正与恢复、V4→V3重投影及V3→V4同步。候选在QQ捕获后从网页确认，创建来源仍QQ；反向同理。延迟triage在私有任务中带原来源，执行时绑定该任务，旧无来源任务为unknown；不继承创建后台任务的另一轮对话。背景派生摘要不算新的用户来源证据。

网页原记忆卡增加可展开的来源通道、ID、来源事件、修改通道、发生/观测时间；全部转义。/memory show也显示创建/修改通道及最近当句来源摘要。群事实授权不授权来源故事：实际群投影仍只带授权事实正文，私人来源标题、摘要、计数不进入群快照。新来源字段不改变ACL。

恢复旧V4版本仍通过原服务作为新修订；保留实际创建来源与当前来源事件，而非把原来源倒退为旧快照。含新来源的数据增加memory_origin_context_v1回退读者要求，旧清单没有此能力时拒绝回退，数据保持原地。

## 修改文件

- 新增saya_app/services/memory_origin.py、tests/test_memory_origin_context.py。
- core_services.py、saya_app/routers/memories.py、saya_app/services/qq_chat.py、saya_app/services/qq_memory_management.py、server5-22B----.py。
- saya_app/services/memory_v4/write_pipeline.py、migration.py、store.py、synthesis.py，及saya_app/services/qq_rollback.py、static/js/app.js。
- 本报告、实施状态、原升级计划的进展注记及新不可变manifest。

原人物提示、心情/声音、检索排序、QQ策略、浏览器配置及既有测试断言均未改。原manifest未列migration.py，本轮新增纳入；其开工内容由本轮精确两行补丁反向重建在私有备份中，未替换生产源文件。没有借此声称原445清单已经覆盖该文件。

## 验证与保留失败

所有检查运行于隔离库。除首轮夹具检查外，后续Python进程启用现有NETWORK_GUARD拒绝非本机DNS/socket；模型/OneBot为替身，无真实厂家或QQ发送。

扩展相关检查：

```powershell
D:\Program\anaconda3\python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['tests/test_memory_origin_context.py','tests/test_memory_v4_phase2.py','tests/test_memory_v4_controls_api.py','tests/test_memory_default_runtime.py','tests/test_memory_web_cas.py','tests/test_qq_memory_management.py','tests/test_qq_memory_mutations.py','tests/test_qq_memory_policy_isolation.py','tests/test_qq_memory_natural_access.py','tests/test_qq_recall_topic.py','tests/test_qq_chat_runtime.py','tests/test_history_output_guard.py','tests/test_deferred_triage_scheduling.py','tests/test_memory_rollback_gates.py','-q','--tb=line','--basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session82-related-data','--junitxml=.runtime/qq-upgrade/session82-related.xml']))"
```

**225通过、0失败/错误/跳过、42条既有FastAPI警告，51.50秒。** 此时新增用例21项。之后增加实际群来源隔离、失败保存恢复、候选再次观测、任意网页来源标记及重放去重检查，并按证据调整来源事件ID不含操作原因，避免同一消息从create转confirm重复来源事件。

最终源码专项：

```powershell
D:\Program\anaconda3\python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['tests/test_memory_origin_context.py','tests/test_memory_v4_phase2.py','tests/test_memory_v4_controls_api.py','tests/test_memory_default_runtime.py','tests/test_memory_web_cas.py','tests/test_qq_memory_management.py','tests/test_qq_memory_mutations.py','tests/test_qq_memory_natural_access.py','tests/test_qq_memory_policy_isolation.py','tests/test_qq_group_context.py','tests/test_memory_rollback_gates.py','-q','--tb=line','--basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session82-final-data','--junitxml=.runtime/qq-upgrade/session82-final.xml']))"
node --check static/js/app.js
node --test tests/memory_controls_ui.test.cjs tests/memory_mutation_ui.test.cjs tests/qq_memory_management_ui.test.cjs
```

**116通过、0失败/错误/跳过、18条既有FastAPI警告，44.35秒。新增独立26项。18项Node通过，JS语法通过。** 225与116范围重叠，不相加；没有重跑旧累计套件或付费对照。原154份测试指纹全部保持。

首次33项检查32通过/1失败：新测试错误调用SQLiteMemoryStore.get_memory，该类实际接口为load_active_memories，已更正新夹具。后续21项检查20通过/1失败：新测试patch不存在的_persist_deferred_triage_outcome，删除该错误patch后验证真实执行入口。两份失败XML保留，未修改旧断言或抹除失败。原第81轮4项网页遗留及第77/80轮旧源码断言仍独立待核查，本轮不代签。

证据覆盖完整KnowledgeBase及三库投影/重启、创建时间/同ID、版本CAS、QQ与网页实际生成器/响应器数据流、跨端候选确认、当前任务/线程来源隔离、延迟任务恢复、原来源不可伪造、有界事件/重放、实际群授权事实与来源隔离、失败保存与旧读者阻断。真人QQ事实及新记录真实重启仍未签。

## 部署

开工当前intent=running，后端ready/running、NapCat reachable、登录matched。没有覆盖用户停机选择。最终私有session82-release-source-r2.json冻结266项，复核一致；早期265项快照保留，最终增加migration.py入清单。

沿用原full-control及部署锁、排空、SQLite backup API三库备份/integrity_check和维护重启，仅应用后端重启**3.19秒**。ready/running/reachable/matched，排空解除。QQ原进程/登录、24控制/继承/网页值、全部原策略、人物/声音/凭据/浏览器/监督/启动及11份保存配置哈希均保持。memory_management_enabled仍false，原事件/关系等不因M1代开。

原观察16→16缺口，原起点和unknown不减，不补算24小时。实际HTTP检查新app.js为200且哈希与磁盘一致，原网页记忆只读API为200、29条旧记忆全unknown；无生产事实写入、批量迁移、重评分、付费模型/搜索/TTS探针或人工QQ代发。私有session82-*保存备份、冻结、XML、部署和聚合核验，不公开原对话、账号、回执、日志或凭据。

## 下一步与验收边界

先读[实施状态](qq-implementation-status.md)、[本轮不可变manifest](qq-implementation-manifest-2026-11-03-session82.json)和[详细计划](qq-memory-events-upgrade-plan-2026-11-03.md)，核对266项最终私有冻结及清单，不重新实现M1或重测已签旧功能。

M1**首版代码已部署，整体门槛待真人验收**。主人原好友私聊可发送一次“记住：这是M1虚构验收，我的测试茶杯叫蓝叶杯。”。取得真实保存回执后只读核对新事实ID、网页来源/当句来源事件，再按原排空备份流程做一次必要重启，核对这一新记录仍在；当前部署重启发生在该事实之前，不能冒充已验证这一真实新记录。无需开启QQ修改管理或重发任何旧私人事实。

M1通过后再做M2自然补充/更正；普通补充先预览、明确更新且目标唯一可直接提交、有歧义才确认。不要重复询问这项规则。M3升降级/QQ轻倍率、M4完整长期机制、M5逐人事件/容量/迁移均未开始。原第8阶段剩余真人验收与第81轮网页遗留保持；ASR/普通文件输出取消、官方整包撤回不恢复。新来源事件只有本次及后续输入，不负责凭空补回旧聊天来龙去脉。
