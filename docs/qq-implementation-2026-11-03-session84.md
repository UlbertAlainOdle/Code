# 第84轮：M1真实样本收尾与M2自然修订首版

日期：2026-11-03，America/New_York。开工第83轮451项指纹一致，源文件私有备份；156份原测试不变。用户要求继续详细方案。先完成M1既定真人门槛，之后实施M2，未跳过阶段。

## M1实际验收

开工现有候选仍pending，没有据“继续”替用户确认。用户随后在原主人QQ好友私聊确认保存，并反馈真实保存回执。只读核对同一轮实际成功sent回执、候选confirmed且指向唯一活动事实，V3/V4与网页实际只读API的ID、正文、QQ创建来源与两条来源事件（原陈述/确认）一致。

沿用原锁/排空/三库备份，仅后端维护重启3.14秒后再次核对网页与V4同一已保存记录，原ID/正文/创建来源及事件保持。**M1本条真实虚构事实门槛通过**；这不是模拟结果，也不扩成所有来源/问法通过。网页核对为真实API/数据链路，不声称用户亲眼看过全部界面。第82/83轮在保存前的重启未被倒算为本次证据。

## M2实现

新增memory_revisions统一服务。预览只存待执行意图，事实仍在原KB/V3/V4；更新调用原KnowledgeBase.update_memory、内容CAS、任务取消和原投影，不用另一套CRUD。保持memory_id、creation_time、原重要性/标签/星标/锁定/创建来源。来源事件分别保留补充原句及确认的通道/消息，之后的网页修改不把QQ创建来源覆盖成web。

支持保守且明确的三类入口：

- 普通补充：`补充蓝叶杯：它只在周末使用。`。唯一目标才出旧→新全文预览，原内容保持；确认前不写事实。预览回执中的“确认修改＋一次性码”绑定同一用户/话题/目标/旧版本，10分钟有效。
- 明确更正：`更正蓝叶杯：只在周末使用→只在工作日使用`。片段必须在唯一可用目标中恰好出现一次；只替换该片段，保留其他成立事实。完整新正文亦可预览/更新，但不猜测省略的主体、别人的身份或未明确的旧条件。
- 管理命令：`/memory append|correct 编号 内容版本 正文`、`/memory history 编号`、`/memory restore 编号 旧版本号`，以及显式`/memory revision-confirm 确认码`。恢复先预览，再按当前版本形成新修订，不直接覆盖活动库。

目标不唯一最多列3项供核对，无目标不新建重复记录。精确重复片段no-op；不是任意释义都已完成语义去重的声明。显式他人陈述、来源账号/项目/V4主体不符、引用/媒体/分享转发、假设/敏感内容、管理资料、锁定/归档/停用与本轮不保存要求不自动改写。模糊主体仍需显式目标或原编辑入口，未新增付费模型规划器。

实际写回后刷新最近条目指针，防止后续“刚才那条”落到更早保存的别条事实。正文/来源版本变化继续使原逐条群授权失效。成功回执是QQMemoryReceipt，原桥接队列按新记忆epoch投递，不把修改操作和确认码写入普通人物上下文。普通陈述的候选/已保存区别仍沿用第83轮合同。

网页原记忆菜单接同一服务，增加补充、更正、差异确认、最近20项历史与旧版恢复预览。使用卡片原content_version及project_id，冲突/部分投影时保留草稿，不自动重试保存。原布局/浏览器设置未重写，未接管用户主聊天页。QQ与网页共用事实和修订；未对网页应用QQ来源加分。

待执行意图保存在原memory3.sqlite3的新memory_revision_previews表：最多1024项，10分钟有效，超过到期一天的项在后续请求时清理，正文上限12000字。token只保存哈希，确认校验用户范围/话题/版本/当前权限。executing/unknown/partial不自动重放；V3索引或V4投影不一致单列待核对，不能向用户报全成功并诱发再存一条。重启可保留尚有效预览；未知提交须读回核对。新非空意图表要求memory_revisions_v1回退读者，不支持的旧程序拒绝接管。

修改历史读取原不可变V4账本的最近20项；本轮没有裁剪历史账本或声称其总存储已经有界。完整容量/压缩/长序列和更广泛自然语义仍属于后续M4，不用本轮确定性用例代签长期收益。

## 文件及验证

业务修改core_services.py、saya_app/routers/memories.py、memory_origin.py、qq_chat.py、qq_memory_management.py、qq_rollback.py、static/js/app.js；新增saya_app/services/memory_revisions.py。新增tests/test_memory_revisions.py、tests/test_qq_memory_revision_commands.py、tests/memory_revision_ui.test.cjs；原156份测试均未改。

扩展相关检查：

```powershell
D:\Program\anaconda3\python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['tests/test_memory_revisions.py','tests/test_qq_memory_revision_commands.py','tests/test_memory_origin_context.py','tests/test_qq_memory_save_contract.py','tests/test_memory_web_cas.py','tests/test_qq_memory_mutations.py','tests/test_qq_memory_management.py','tests/test_qq_memory_policy_isolation.py','tests/test_qq_control_receipt_isolation.py','tests/test_memory_default_runtime.py','tests/test_qq_group_context.py','tests/test_memory_rollback_gates.py','tests/test_qq_presentation.py','tests/test_qq_chat_runtime.py','-q','--tb=line','--basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session84-final-data','--junitxml=.runtime/qq-upgrade/session84-final.xml']))"
```

**239通过、0失败/错误/跳过、30条既有FastAPI警告，58.49秒。** 此时新增37例。之后补真实归一化media_types/forward_present/引用在模型前阻断的3例，6文件133通过、18既有警告、54.41秒，0失败/错误/跳过。

最终增加“更新另一条事实后，最近指针随实际目标移动”检查，最终源码：

```powershell
D:\Program\anaconda3\python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['tests/test_memory_revisions.py','tests/test_qq_memory_revision_commands.py','tests/test_qq_memory_natural_access.py','tests/test_qq_control_receipt_isolation.py','tests/test_memory_rollback_gates.py','-q','--tb=line','--basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session84-last-data','--junitxml=.runtime/qq-upgrade/session84-last.xml']))"
node --check static/js/app.js
node --test tests/memory_revision_ui.test.cjs tests/memory_controls_ui.test.cjs tests/memory_mutation_ui.test.cjs tests/qq_memory_management_ui.test.cjs
```

**56通过、0失败/错误/跳过、12条既有FastAPI警告，20.27秒。新增Python共41例。Node共23通过，其中新5例；JS语法通过。** 各轮范围重叠，不累加。

早期53项通过；随后147项中145通过/2失败。一个新夹具错误修改冻结QQSettings，改为model_copy；另一个是真实队列把新增确认命令投影成泛化说明，改为带码自然确认，不改旧提示过滤规则。仅失败2项原样重放仍失败、修夹具后的7项仍有1失败，均留XML；修复后49项通过。没有删除旧断言或把早期失败抹掉。

测试覆盖原ID/创建时间/标签/评分/来源保持、补充与精确更正/重复、并发确认、网页抢先编辑、TTL/重启/未知结果不重放、取消/撤权、V3/V4部分投影及重启同ID修复、真实桥接队列/类型化回执/无模型/不入聊天历史、原群授权失效、主体/项目/媒体/引用边界、网页草稿/版本/恢复及旧读者门禁。库/时钟/模型/OneBot为替身并启用外网拒绝；不冒充真人M2验收。

## 部署和用户设置

最终私有session84-release-source-r3.json冻结271项，旧r1/r2保留。按原锁/排空/三库backup/integrity_check，仅后端维护重启3.19秒PASS，ready/running/reachable/matched并解除排空。与前面M1核对的3.14秒是两次不同目的的维护动作，不混成一次。M1真实已保存记录/来源及候选confirmed状态再次保持。

部署时QQ原进程/登录、24控制/继承/网页值、原策略/人物/声线/凭据/浏览器/监督/启动及11份保存配置哈希一致；原16→16观察缺口/起点/unknown保持，不补在线信用。新JS与原记忆历史真实HTTP200，资产哈希和磁盘一致；实际历史API查同一M1事实内容一致。代理无生产记忆改写、QQ代发或新付费模型/搜索/TTS探针。

用户在本轮明确允许开启QQ记忆修改管理以验收。原版本化PUT接口仅将memory_management_enabled从false改true，读回运行态生效；其余23控制、inherit_fields/shared/网页值和其他保存文件保持，不重启。首次分项请求因QQControlsUpdate仍有8个必填原字段而422，旧revision和全部值未变；携原24值、原inherit_fields及新读取revision重试成功。私有证据保留拒绝和成功，不恢复为false。

## 网页长期机制的提前核查

没有趁本轮改动共享数据模型。只读源码与第81轮旧失败证据吻合：多角色的正文history_for_agent虽过滤其他角色，clock_context的message_hint仍读取全会话时间行，形成另一路内容输入；留待M4修复，本轮未签通过。

另外3项旧API失败通过私有进程级夹具仅隔离受管共享配置/本机凭据解析后，原3例均通过，6既有警告、8.57秒。原测试文件、生产设置和业务源码未为此改变，外网拒绝。说明这3例依赖受管配置，不能据旧失败认定本轮损坏流式/统计；也不把这次夹具重放当作原4项全部修复或与239等累计。重放命令为私有session84-web-isolation-probe.py，失败旧XML保持。

## 下一步

先读[实施状态](qq-implementation-status.md)、[本轮不可变manifest](qq-implementation-manifest-2026-11-03-session84.json)及[详细方案](qq-memory-events-upgrade-plan-2026-11-03.md)。M1单例已经通过，不重复保存。当前QQ修改管理true是用户新选择，后续保持。

**M2真人补充/更正门槛待本轮反馈。** 已请用户原好友私聊对同一事实先补充条件→按预览回执确认→将原条件精确更正。收到回执后核对原ID/创建时间不变、杯名和有效条件正确、旧条件不再活动、网页/V4一致及来源事件连续；可选重复同一补充应no-op，网页并发/撤权等沿用本轮隔离证据，不要求用户复杂联合实验。

M2通过前不进入M3。M3重要级/轻倍率、M4完整长期机制/角色时钟隔离修复及M5逐人事件/迁移仍未实施；ASR/普通文件输出取消、官方整包撤回、其他既有实机待签保持。不要因已开启修改管理而调整其他策略、重导浏览器缓存或回填旧来源。
