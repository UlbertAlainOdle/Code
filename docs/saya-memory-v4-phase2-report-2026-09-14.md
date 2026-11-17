# Saya Memory V4 阶段 2 技术报告

日期：2026-09-14  
实施阶段：阶段 2：Write Pipeline  计划依据：`docs/saya-memory-agent-v4-technical-plan.md`

## 结论

阶段 2 的可独立落地部分已完成。新流水线默认不启用；只有显式设置
`memory_v4_write_enabled=true` 后，旧 `capture_explicit_memory()` 才会通过
V4 写入边界，并同步生成旧 JSON 兼容投影。V4 主读仍关闭，没有改变默认回答路径。

## 修改文件

- `saya_app/services/memory_v4/write_pipeline.py`
  - `WriteIntentGate`：结构化 action 优先，确定性句首保存/纠正/忘记兜底；拒绝普通讨论、疑问、引用、条件假设和凭据提示。
  - `EvidenceCollector`：当前用户消息 + 最多两个已完成问答对；排除未完成上下文。
  - `StructuredExtractor`：输出可审计 `MemoryProposal`，支持多事实拆分、作用域、有效期、重要度建议和证据引用。
  - `DeterministicValidator`：协议残留、悬空开头、疑问/假设/引用、秘密、来源和 hash 校验。
  - `WritePipeline`：显式 commit、被动 candidate、同属性去重、更新/supersede、冲突 candidate、显式 forget、`memory.updated` 事件。
- `saya_app/services/memory_v4/models.py`
  - proposal 增加 `valid_from`、`valid_to`、`suggest_pin`、`future_use`；证据 hash 和嵌套候选 proposal 严格校验；公共事件递归拒绝私有字段。
- `saya_app/services/memory_v4/store.py`
  - `update_memory()` 支持证据与操作类型；增加 candidate 查询接口。
- `core_services.py`
  - 增加受 feature flag 保护的 `process_memory_write_v4()`；旧显式捕获在 V4 开启时成为兼容 adapter；增加同 ID legacy projection 和避免重复 V4 转换的保存参数。
- `tests/test_memory_v4_phase2.py`
  - 新增 10 条阶段 2 合同与安全测试。
- `docs/backend-layout.md`
  - 登记阶段 2 的代码边界。

## 写入行为

| 输入 | 结果 |
| --- | --- |
| 句首明确“记住”且证据/校验通过 | 立即写入 V4，至少一条 evidence，产生 `memory.updated` |
| 普通聊天或一次被动观察 | 只写 pending candidate，不写 current memory |
| 同一 predicate/scope 的相同值 | 保留一个 current fact，补充证据/确认时间 |
| 显式新值 | 原 ID 新 revision，operation 为 `supersede`，旧版本仍可恢复 |
| 被动新值与 active 冲突 | pending candidate，记录 `metadata.conflict_with`，不并列写入 current |
| 显式忘记 | 写审计墓碑并产生 `memory.updated`，不静默抹除版本 |

凭据、secret、协议残留、悬空句首、疑问、条件假设和引用均不会成为永久 V4 记忆。
被动 candidate 不允许自动 pin 或以 `user_confirmed` 权威进入永久事实。

## 测试

- `python -m pytest -q tests/test_memory_v4_phase2.py` → **10 passed**。
- `python -m pytest -q tests/test_memory_v4_phase1.py tests/test_memory_v4_phase2.py` → **20 passed**。
- 阶段 1 + 阶段 0 相关回归命令 → **82 passed**。

阶段 0 已知的全量问题仍存在：4 条 `test_memory_graph_ui.py` 断言与既有 UI 重构不一致，另有 Windows 临时目录权限导致的 17 条 setup errors；本阶段没有扩大这些问题的范围。

## 遗留风险与回滚

1. Entailment/Repair 当前以确定性拒绝为主，未调用模型做代词消解或模糊冲突判断；这避免了不受控事实写入，但复杂表达会进入 rejected，需要后续 repair worker。
2. 旧 JSON 兼容投影只在 V4 flag 开启且通过 adapter 写入时更新；V4 主读、后台综合和外部写工具仍未启用。
3. 停止双写只需关闭 `memory_v4_write_enabled`；已写入的 V4 数据可用 `export_state()`、`import_state()` 和 `restore_version()` 恢复，legacy 文件不应直接删除。

## 下一步

阶段 3 才接入 V4 retrieval、DayState rollover 和 Prompt Compiler；在此之前不切换 V4 主读。
