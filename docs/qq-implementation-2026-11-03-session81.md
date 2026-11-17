# 第81轮：时间标签泄漏修复、评分解释及长期记忆/逐人事件计划

日期：2026-11-03，America/New_York。开工核对第80轮441项指纹一致，相关原源码私有备份；不重置已有工作区。本轮用户授权简单问题直接修、复杂问题制定详细MD。

## 已实现

1. 新history_output：仅过滤应用产生的行首History sent_at时间标签；流式前缀分片也覆盖。保留普通时间/括号、行内/围栏/缩进代码、独立数学块示例。短前缀缓存有界，普通正文即时通过；完成前处理普通未决前缀，失败/取消不补发持有内容，关闭原生成器。
2. qq_presentation接入同一过滤，原心情/动作/thinking规则继续生效。QQ生成、旧助手历史的临时输入投影、旧待发片段和OneBot文字发送沿用原边界；不批量改旧聊天、不重新发送已成功消息。
3. server共用网页/QQ SSE包装器；正常、异常和取消收尾的助手保存去掉该标签。时间输入投影未修改，模型仍能看到真实历史时间，网页answer_complete及新保存正文不带泄漏标记。
4. QQMemoryManagement.detail及/memory show补当前兼容档、importance_score、无查询优先分、星标、锁定及标签字段；明确兼容分不等于实际V4召回分，不因查看而修改记忆或内容版本。

人物提示、心情系统/原过滤器、原记忆内容与评分算法、来源权限、原开关均未改写。没有用删除时间感知修复输出问题。

## 只读发现及计划

详见[记忆/来源事件/逐人事件详细方案](qq-memory-events-upgrade-plan-2026-11-03.md)。

- 已有网页编辑及QQ/memory edit后台，当前QQ修改管理关闭；自然补充的目标解析、预览及同ID更新流程尚不完整，不能说完全没有后端修改能力。
- 已有证据引用与观察时间，不等于完整保存了记忆产生的事件。V4→旧投影重建source/source_detail，也会妨碍可靠标记QQ来源。
- 旧网页分档优先分与V4实际召回评分口径不同，来源档存在固定上下限。模型/action可建议salience，未提供时管线有默认分，不是每条均由独立模型分级。
- QQ来源标签、动态重要级、1.08轻倍率仅作为拟定方案，未对生产批量回填/重评分或改变召回排序。长度只考虑有效信息完整度，不能把长文直接当重要。
- 事件目前仍按会话/群scope，不是逐人跨场合统一；/events只查看当前作用域。统一用户事件身份和来源可见范围、关系容量及迁移门槛已规划，未实现迁移或代开策略。
- 用户确认：普通补充先预览，明确更新且目标唯一可直接执行。网页重点为长期维持机制，包括重复/过时事实、评分自我强化、容量/压缩、索引投影一致性和长序列验证，非优先重做界面。下次按此继续，不重复问。

## 测试与遗留

新tests/test_history_output_guard.py最终26项，独立文件，不修改既有测试断言。覆盖每一个两段切点、逐字分片、字面示例、畸形文本、有界缓存、完成事件顺序、失败/取消关闭、旧缓存和实际发送替身、真实网页/QQ生成器与持久化、时间仍进模型及评分只读。

最终命令：

```powershell
D:\Program\anaconda3\python.exe -X utf8 -m pytest tests/test_history_output_guard.py tests/test_qq_presentation.py tests/test_qq_quote_presentation_regressions.py tests/test_qq_memory_management.py tests/test_qq_actions_time.py tests/test_qq_chat_runtime.py tests/test_qq_delivery.py tests/test_qq_forward.py tests/test_qq_task_guard.py tests/test_qq_task_memory.py tests/test_qq_control_receipt_isolation.py tests/test_qq_memory_natural_access.py tests/test_qq_memory_policy_isolation.py tests/test_qq_recall_topic.py -q --tb=line --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session81-release-tests-data --junitxml=.runtime/qq-upgrade/session81-release-tests.xml
```

**264通过、0失败/错误/跳过、42条既有FastAPI弃用警告，57.41秒。** 早先96/263范围重叠不累加。其后补上成功完成事件前的普通短前缀刷新，最终264针对最终源码；没有覆盖失败文件。

扩展网页/主动互动/原记忆修改检查：

```powershell
D:\Program\anaconda3\python.exe -X utf8 -m pytest tests/test_history_output_guard.py tests/test_chat_agents.py tests/test_api_async_hardening.py tests/test_prompt_time_gating.py tests/test_proactive_interaction.py tests/test_qq_memory_mutations.py tests/test_qq_memory_natural_access.py tests/test_qq_memory_policy_isolation.py tests/test_qq_recall_topic.py -q --tb=line --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session81-web-final-tests-data --junitxml=.runtime/qq-upgrade/session81-web-final-tests.xml
```

**146通过、4失败、48既有警告，49.98秒。** 单列失败：

- test_chat_agents.test_real_stream_dispatch_prompts_persistence_and_default_saya[False]：多角色时间上下文仍出现另一角色旧回复的断言。
- test_api_async_hardening.ChatStreamApiModeTests.test_first_visible_timeout_reports_and_gives_up：预期无可见内容，与得到迟到文字的结果不一致。
- 同类test_happy_path_streams_text_and_records_usage及test_usage_metadata_persisted_on_ai_message：测试预期mock模型，实际受管配置选择不同，使用统计键/模型断言失败。

仅这4例用开工备份server重放，**同4例均失败，12警告、15.30秒**；导入钩子重定向到私有原源码，无生产文件替换。证明不是本轮新增输出包装器引入；不把这些失败计PASS、不删断言，也不把“原本就失败”当其行为合理。归入网页后续机制核查：先分清真实角色上下文问题与测试配置隔离/超时模拟，再修复。第77/80轮旧feedback源码断言另有历史证据，本轮不重复运行或代签。

私有session81-*保留首轮/最终XML、原源码重放、源码备份、只读聚合及部署证据。全部模型/网络/OneBot为替身，未新增付费模型/搜索/TTS、人工QQ代发或生产记忆写入；不把自动化签成用户真实收件。既有所有测试文件指纹保持。

## 部署与保留

最终私有session81-release-source.json冻结263项。原full-control锁、排空、三库备份及integrity_check后，仅应用后端维护重启**3.16秒**，ready/running/reachable/matched、排空解除。原QQ进程/登录、24控制/继承/网页值、媒体/收藏/表达/主动/话题/事件/关系/体验、人物/声线/凭据/浏览器/监督/启动选择核对保留，11份配置哈希相同。

原观察起点、unknown、16→16缺口保留；不补算24小时。修改管理保持关闭，事件/关系等不因新计划自动开启。没有生产重评分、旧来源推断回填、跨群事件迁移或另加后台进程。历史旧消息不删除；新版本只在显示/保存边界过滤。

实际QQ/网页的这次时间泄漏修复未取得新的用户收件确认；自动化证明对应路径，正常使用继续观察，若再出现按发生端/新消息定位，不要求用户重跑整套旧实验。

## 下一次入口

先读[实施状态](qq-implementation-status.md)、本轮不可变manifest及[详细方案](qq-memory-events-upgrade-plan-2026-11-03.md)。T0已交付，按M1可信来源与来源事件→M2自然修订→M3分数/轻加权→M4长期机制→M5逐人事件推进；网页遗留缺陷核查可提前，避免在有隔离债务时扩大接入。

这5项是用户新增范围；原工程仍第8阶段实机收尾。已签语音/群附件身份/第80轮同句召回不重复，原跨04:00/来源保留/顺序等未签保持；取消的ASR/普通文件输出及撤回官方整包不恢复。
