# 第149轮 R3 网页候选恢复和主机结果答复

日期为2026-11-06，America/New_York。开工核对148轮650项清单、356项最终源码冻结及部署r2，预期以外源码变化为零。

## 本轮实现

网页候选确认必须携带原64位候选版本。新 `web_candidate_writes.py` 以候选身份、原版本及完整编辑计划生成稳定操作身份，接共用结果记录。主机确认计划复制调用者的可变数据，并将计划指纹留在实际记忆来源中。候选状态未能落盘后改用另一份正文再次确认时，不能把旧提交冒充新计划的结果。

正文已经提交而候选状态文件失败，结果明确标为已提交、候选状态待修复。候选列表返回原操作的恢复信息，网页重载后冻结该卡编辑，只允许修复原状态文件。恢复器核对原记忆版本、来源候选、原候选版本及原提交；只改变候选文件，不调用新增记忆。结果未知时网页只查询原操作。保存后的记忆或候选发生变化，旧修复拒绝。原计划记录引入前已经提交的合法候选仍保留兼容恢复路径。

普通网页明确保存移到模型回答前取得真实主机结果，提供给普通回答和工具Agent的初始上下文；回执、归档及结果查询使用同一对象。已提交后、模型回答前取消请求时，用户消息保留回执。更正和删除仍按原epoch控制时序执行，本轮不将其提前。重复生成不重复触发冲突分析。此前显式保存失败后的候选回退抑制继续保留。

修改7个原业务文件：core_services、memories路由、memory_write_operations、chat_memory_writes、qq_rollback、server和app.js。新增上述服务、`test_web_candidate_write_operations.py`、`test_web_write_receipt_answer.py`及`candidate_write_recovery.test.cjs`。新增 `candidate_confirmation_plan_v1` 回退兼容要求，保留原QQ记录表和全部既有要求。

## 验证

首轮聚焦31项为28通过、3失败，定位为全局异常类导入缺失和unknown结果未绑定候选，原失败报告保留。修正后聚焦22通过。`D:/Program/anaconda3/python.exe -B -X utf8 .runtime/qq-upgrade/session149-verify.py` 最终261项通过，42条既有FastAPI警告。

随后仅增加确认计划对调用者列表的复制和对应反例。`D:/Program/anaconda3/python.exe -B -X utf8 .runtime/qq-upgrade/session149-verify-r2.py` 验证相对首轮只有这两处变化，相关候选、QQ修改及网页答复25项通过，6条既有警告。各批重叠，不相加。网络拦截和用量隔离生效，无付费探针或代理QQ代发，不把隔离HTTP/模型构造当作真实QQ。

`node --check static/js/app.js` 通过；`node --test tests/candidate_write_recovery.test.cjs tests/chat_agents.test.cjs` 6项通过，实际执行候选卡渲染、修复与查询分支。最终360项 `session149-release-source-r2.json` 匹配。

## 部署与运行边界

`session149-deploy-r2-locked.py` 的常规尝试在本地接口连接阶段失败。现场原后端及服务监督进程已退出，8000未监听，保存运行意图仍为running。该次未停止或重启任何进程，原 `BLOCKED_OR_FAILED` 记录保留。

`session149-offline-recovery-r1.py` 在完整控制锁内重验最终源码、离线保存配置及三库完整备份，再按原running意图恢复服务，没有停止QQ或改变自动启动策略。其等待完整QQ健康条件超时，记录 `startup_not_ready`，不改成成功。后续只读核对确认后端ready、consumer running、上下文和来源绑定就绪、未排空；QQ登录仍unknown，QQ自动启动/监督关闭选择保持。当前后端已加载149r2，但本轮完整QQ部署门槛未通过。

第一次只读比较发现结果缓存和来源表变化，保留失败记录。逐项追到新运行入站结果引起原每会话容量淘汰，其关联分片、选项、派生来源同步清理；戳记录仅清理恢复时已经超过24小时的原记录。第二次检查器误将新入站要求为备份中已有入站，该失败也保留。最终 `session149-recovery-verify-r3.py` 27项检查通过，状态为 `BACKEND_PASS_QQ_UNAVAILABLE`，不是完整部署PASS或真实QQ验收。

配置凭据、浏览器、人物、声线、长期记忆、候选、删除与使用状态、修订预览、现有事件和控制、操作结果及当前来源保持。没有从备份写回业务数据。观察起点及累计unknown不减，真实缺口由19增至20；不补造离线在线时长。备份、原会话、运行日志和详细比较均只留私有目录。

## 接续

先核对[最新状态](qq-implementation-status.md)、[不可变清单](qq-implementation-manifest-2026-11-06-session149.json)、最终360项冻结和恢复核对r3。不要重复已完成的R2或无变化回归，不重启来凑完整部署PASS。QQ连接须按现有手动启动选择恢复后只读检查。

R3下一项为原生结构化调用的来源权限和目标版本证明，详见[入口清单](qq-chat-write-entry-inventory-2026-11-05.md)。继续核对手动归档、被动候选和后台派生各自用途，完成全部入口合同后再做实际QQ批量保存、操作结果查询及网页详情一致性验收。R3工程及实际整体门槛仍待完成；R4、R5、R6按序，不提前开展。
