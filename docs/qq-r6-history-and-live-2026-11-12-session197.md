# R6有限历史来源与真实QQ连续验收

日期2026-11-12，America/New_York。第196轮799项清单及428项源码开工匹配。最终线上197r7。R5证据保持，R6整体未签。

## 实现与边界

版本快照继续作为审计，不自动成为可召回历史。新模块 `memory_temporal_history.py` 只为明确旧使用安排变更记录前一修订、前后指纹及历史屏障。主机写入器和原CAS确认入口提供许可；模型提案不能自带许可，普通编辑、事实否认、恢复、假设及转述不获得该能力。

明确问某次“改用／改为之前”时，正常生成器先核对当前权限、快照主体、范围、敏感性及指纹，再在原预算的同一来源块中显示有历史标记的前一安排。当前正文和存储修订不变。删除、归档、停用、投影未完成、篡改或读取中失权均排除；重新启用当前条目不重开旧屏障。旧正文的一般失效校验未放松，容量未扩大，旧库未补标记。旧读取器继续只读当前正文，没有新增旧读取器必须理解的隐私指令或数据库结构。

另修复更正命令冒号进入正文、命名对象条件问句漏召回、只读问答误带本轮无写入回执，以及已删条目的原保存结果被误报成索引异常。真实索引异常仍保持待核对；结果查询不重试写入。

## 实际结果

| 范围 | 结果 | 验收边界 |
| --- | --- | --- |
| 历史及当前原因 | 分别读到前一安排理由和当前变更理由 | 正常生成器、独立知识库、实际模型 |
| 纠错／停用后历史 | 旧原因未选入；重新启用不打开旧屏障 | 工程反例及实际模型材料核对 |
| 维护组合 | 确认/重复一致，改单条使旧关联失效，撤销重试不复活，遗忘后另一条仍可读 | 原服务、主机夹具来源及实际模型；非真实QQ维护命令 |
| 新主题及不同变更表达 | 前后来源和当前回答正确；过去答案仍补了无来源的材质属性 | 回答质量部分通过 |
| 主人QQ连续过程 | 实际9条入站、10条文字回执，保存、原编号读取、CAS更正、过去/现在、清理及原操作重查通过 | 过去/现在使用 `/recall`；未签默认Agent自然问答 |
| 清理及历史结果 | 用户授权后在QQ确认删除，当前条目消失、墓碑存在，原保存账本和其他旧条目保持 | 原聊天及封存证据保留 |

原保存结果查询首轮把已删除目标误报为索引待核对，失败回执保留。修复上线后只重查原操作，明确返回原提交与当前删除状态。正常QQ问答的保存审计、实际入站及每个送达编号相互匹配；完整回答与分段回执比较时仅规范化段落空白。

本轮14次实际模型调用：12次隔离、2次QQ回答，共31494个接口报告token，包含失败及重复，不相加为独立通过样本。原消息、账号、编号及日志只在私有 `.runtime/qq-upgrade/`，汇总 `session197-r6-reviewed-r1.json`。

## 验证与部署

最终182项相关回归通过，30条既有FastAPI弃用警告，430项源码冻结。初批3项失败及后续含糊回指夹具拒绝保留；修正冒号及夹具目标，没有放松指代或权限规则。

```powershell
& 'D:/Program/anaconda3/python.exe' -B -X utf8 .runtime/qq-upgrade/session197-verify-r7.py
& 'D:/Program/anaconda3/python.exe' -B -X utf8 .runtime/qq-upgrade/session197-deploy-r4.py --preflight
& 'D:/Program/anaconda3/python.exe' -B -X utf8 .runtime/qq-upgrade/session197-deploy-r4.py
& 'D:/Program/anaconda3/python.exe' -B -X utf8 .runtime/qq-upgrade/session197-deploy-readonly-reconcile-r5.py
```

首部署因控制锁占用未停止后端，锁自然释放后继续。后续两次部署均已就绪，各44项保留检查直接通过，1项为原保留期清扫差异。分别对照备份核对到期依赖、无快照旧上下文及完整性，最终均为 `PASS_WITH_ORIGINAL_RETENTION`，来源快照移除为0。原失败报告保留，没有为核对再次重启。最终后端3.20秒就绪、排空解除、QQ账号匹配，原进程、配置、启动/监督选择保持，观察缺口仍26。

业务修改：`core_services.py`、`server5-22B----.py`、`saya_app/services/memory_recall_contract.py`、`chat_memory_writes.py`、`memory_revisions.py`、`qq_memory_reference_dialogue.py`、`memory_v4/write_pipeline.py`、`memory_v4/prompt_compiler.py`；新增 `memory_temporal_history.py`。测试修改：`tests/test_r4_recall_continuity.py`、`test_web_write_receipt_answer.py`、`test_qq_chat_save_operations.py`；新增 `test_memory_temporal_history.py`。状态及验收表同步更新，新清单不覆盖历史。

## 精确接续

先核对本轮清单、`session197-source-r7.json`、私有增量复核及最终部署保留对账。优先接默认Agent历史来源合同，查询与按编号读取都约束在原问题和当前权限内，不能把当前版本当过去，也不用只读命令用例代签自然问答。再处理新表达中的无来源常识扩写，使用未用于提示调试的新问法核对材料和答案。继续代表性长讨论、容量、故障及并发的组合质量/成本审阅，继承R2—R5及本轮实机证据，仅补必要增量。
