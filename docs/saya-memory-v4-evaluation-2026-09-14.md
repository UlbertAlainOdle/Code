# Saya Memory V4 评测与 clean-process 回归报告

日期：2026-09-14  
范围：阶段 1–7 的 deterministic contract seams；不接模型、不联网、不写真实 `knowledge_base`。

## 执行方式

```text
python scripts/eval_memory_v4.py --json-out <临时目录>/saya-memory-v4-eval-20260914.json
```

脚本每次在独立 Python 进程和临时目录中创建 SQLite V4 store，结束后不留下仓库数据。测试 corpus 只输出聚合指标，不输出 secret 或私人正文。

## 当前结果

| 维度 | 结果 | 门槛/说明 |
|---|---:|---|
| Write corpus | 200 条 | 达到阶段计划最低规模 |
| Write intent macro F1 | 0.927273 | 通过 ≥ 0.90 |
| unwanted permanent write rate | 0.0 | 通过 ≤ 1% |
| secret permanent write count | 0 | 通过必须为 0 |
| 兼容性关键词基线 intent macro F1 | 0.717647 | 同一 corpus 的简单旧式比较器 |
| 兼容性关键词基线 unwanted write rate | 0.75 | 比 V4 0.0 高；不是生产旧模型 replay |
| 兼容性关键词基线 secret permanent write count | 20 | 比 V4 0 高；用于回归方向校验 |
| evidence traceability | 1.0 | 通过 100% |
| multi-fact atomicity | true | 两个事实分别提交并各有 evidence |
| Retrieval Recall@5 | 1.0 | 10 个隔离查询 |
| Retrieval MRR | 1.0 | 10 个隔离查询 |
| Retrieval scale Recall@5 | 1.0 | 100/500/1k/5k/10k items |
| Retrieval scale P95 | 85.508/97.128/223.366/625.021/998.858 ms | 100/500/1k/5k/10k；当前 Windows 本地临时 SQLite 基线 |
| timeless prompt time block | false | 通过静态断言 |
| time query time block | true | 通过静态断言 |
| prompt audit full text | false | 仅保存 hash/metadata |
| Tool selection must/must-not/unavailable/permission/budget | 全部通过 | forbidden call count `0` |
| malicious tool result containment | 通过 | secret/raw reasoning 未进入公共结果 |
| Daily/connector safety contracts | 全部通过 | first-contact、D-4 purge、carryover、dedupe、cooldown、外部不升格 |

## 验收范围

- 200 条 corpus 覆盖显式保存、忘记、纠正、普通讨论、疑问、假设、引用、secret、协议残留、悬空句首和第三方陈述。
- unwanted-write 指标以完整 `WritePipeline` 的永久 `committed` 结果计算，而不是只看 Intent Gate；因此协议残留和悬空句首即使进入意图层，也会在 Validator 层被拒绝。
- 额外输出一个同 corpus 的 naive legacy keyword comparator：它把任意“记住/remember”当作 explicit save，用于验证 A/B 方向和防护收益；它不是生产旧实现的历史回放，不能替代真实模型 A/B。
- Retrieval 使用临时 store 和 10 条带唯一标识的事实，验证 bounded candidate/full fetch 的 Recall@5 与 MRR。
- 可选 `--scale` 曲线覆盖 100、500、1k、5k、10k 条；修复了 usage receipt 在候选循环中的 N+1 查询，改为单次聚合 score map。
- Prompt 静态检查验证 timeless query 不注入时间，时间问题注入时间块，审计不保存完整 prompt。
- Tool selection contract 覆盖 must_call、must_not_call、unavailable_tool、missing_permission、budget_exhaustion 和 malicious_tool_result；使用 `AgentLoop`/`CapabilityRegistry` 的确定性回调，不调用真实模型或 provider。
- Daily/connector contract 覆盖同日首次接触幂等、D-4 清理、未完成任务 carryover、重复外部事件去重、digest cooldown，以及外部事件不自动写入长期个人记忆。

## 尚未覆盖

这仍不是完整模型 A/B 评测：尚未接真实模型、embedding、provider 或完整 `/chat_stream` 工具循环。naive baseline 只验证写入安全方向；LongMemEval/LoCoMo、真实工具选择评测和 clean-process 下的全仓库既有测试权限问题仍需单独处理。生产 wiring 已增加主读、usage receipt、capability、Agent Loop、Daily、controls 和 synthesis 的受控 seam，默认 feature flags 不变。
## 2026-09-14 remaining-work pass

The clean-process contract suite now also reports retrieval safety metrics:

- `stale_assertion_rate`: `0.0`
- `conflict_resolution_accuracy`: `1.0`
- `cross_scope_leakage`: `0`
- `abstention_accuracy`: `1.0`
- direction-only legacy fixture: `1.0` stale rate, `0.0` conflict accuracy, `1` scope leakage, `0.0` abstention accuracy

The repository-wide regression command is now configured with a workspace-local
pytest temporary root and completes with **583 passed** in the current clean
process. This remains a deterministic contract evaluation; it does not claim a
live external-provider, LongMemEval/LoCoMo, or production embedding benchmark.

The same clean-process run now includes `daily_connector.go=true`, covering the
four-day Daily ring, first-contact idempotency, task carryover, connector
dedupe/cooldown, and the no-automatic-long-term-memory boundary.
