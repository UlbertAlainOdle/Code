# 第147轮 R3 QQ 保存操作结果与重试

日期为2026-11-05，America/New_York。开工核对第146轮641项清单、351项发布冻结及146r1部署，预期以外源码变化为零。R2实际验收与第139轮原部分完成记录沿用。

## 保存入口与结果

新增 `saya_app/services/qq_chat_saves.py`，用已有Bridge数据库登记操作身份、会话、计划指纹、期限及逐项状态。正文继续只由原存储服务写入。QQ字面保存以入站身份生成编号，单条来源与批量来源使用已送达完整预览的编号。批量先检查全部来源，再逐项保存并记下存储返回的ID与内容版本。原 `qq_memory_reference_batches` 结果保持可读。

提交记录先于回执索引。已提交后回执索引失败仍返回存储结果。候选正文提交成功但候选状态文件失败时保留已提交ID及待核对标记。中断或存储结果不明时留下unknown状态，同一操作不再执行。重复确认和 `/memory result 操作编号` 读取同一会话的已有记录。条目变化或当前不可用时，查询回执只列原提交ID。

`qq_memory_reference_dialogue.py` 接入字面、单条及候选计划，`qq_memory_reference_selection.py` 接入批量并兼容旧结果。`qq_memory_management.py` 将实际提交结果和显示回执分开。`qq_bridge.py` 与 `qq_action_proposals.py` 根据提交字段记录成功，并支持重复确认读取结果。缺少完整预览服务的候选回调返回未保存。已有候选摘要先展示完整正文再确认。共享生成器通过 `allow_save` 接受主机已处理的QQ保存边界，V4更正及删除保持原入口。

修复控制轮次在记忆修改后反复判定旧epoch，导致新发出的保存预览在下一句确认前被作废。历史筛选完成后即登记当前epoch。补充帮助中的候选全文预览和结果查询用法，并加入 `qq_chat_save_operations_v1` 回滚兼容要求。

## 验证

新增 `tests/test_qq_chat_save_operations.py` 覆盖首次私聊、入站重复、不同正文复用身份、同会话查询、候选状态文件失败、索引失败、已提交后异常和两线程同时重试。更新6份相关旧测试，分别核对候选全文确认、兼容回调拒写、实际Agent应答与主机保存结果。测试未代发QQ。

初批参考来源回归66通过、1项回执用词断言失败。后续43通过、1项旧回调预期失败。完整初轮214通过、3失败，定位了两项旧候选流程预期及控制轮次epoch问题。修正后 `D:/Program/anaconda3/python.exe -B -X utf8 .runtime/qq-upgrade/session147-verify-r2.py` 217通过、24条既有FastAPI警告。补充检查初轮38通过、1项旧能力测试预期失败，更新后最终 `session147-verify-r4.py` 39通过、6条既有警告。以上批次重叠，不累计为独立总数。网络拦截、用量隔离和最终353项源码指纹匹配。

## 部署及接续

命令 `D:/Program/anaconda3/python.exe -B -X utf8 .runtime/qq-upgrade/session147-deploy-r3-locked.py` 成功部署最终源码r4。受管排空、三库备份及后端重启3.62秒完成。37项数据、设置与结构检查通过，QQ身份匹配，解除排空，观察缺口保持19。新增操作表与原批量表纳入部署前后数据保留核对。原配置、候选与已保存记忆核对通过。

本轮为R3 QQ保存子范围的工程验证及部署，实际QQ验收留待R3整体验收。下一步核对[状态](qq-implementation-status.md)、[清单](qq-implementation-manifest-2026-11-05-session147.json)和353项发布冻结。从网页聊天的跨重试请求身份开始。当前 `ChatRuntimeManager.create` 每次生成新request_id，网页没有可复用的保存操作标识。再接V4结构化写入和更正删除，核对网页候选确认的CAS及来源合同，按[入口清单](qq-chat-write-entry-inventory-2026-11-05.md)逐项收敛。完成R3代码与实际验收后进入R4。
