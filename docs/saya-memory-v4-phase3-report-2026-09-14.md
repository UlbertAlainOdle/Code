# Saya Memory V4 阶段 3 技术报告

日期：2026-09-14  
实施阶段：阶段 3：Retrieval V4、Daily State 与 Prompt Compiler  
计划依据：`docs/saya-memory-agent-v4-technical-plan.md`

## 结论

阶段 3 的隔离 seam 已完成并通过合同测试。V4 读路径继续遵守 feature flag：
`memory_v4_read_enabled=false` 时只能返回 shadow 诊断结果，不能把 V4 item
注入旧回答；没有真实库迁移或主读切换。

## 修改文件

- `saya_app/services/memory_v4/retrieval.py`
  - `MemoryQuery` 做 depth、范围、候选数和 token budget 裁剪。
  - `MemoryNeedPlanner` 先判断是否真的需要记忆；`MemoryRetriever` 做词法 hybrid baseline、重排、lifecycle/validity/scope/event gate。
  - 先返回不含内部字段的 candidate summaries，再 fetch 最终完整 item；候选选择与实际使用通过 usage receipt 分开记录。
- `saya_app/services/memory_v4/daily_state.py`
  - `DayStateManager` 实现 page load/backend start/conversation 分开计数、`first_contact_today`、D0–D3 权重环、任务 ID rollover 和 D4 清理。
- `saya_app/services/memory_v4/prompt_compiler.py`
  - `PromptBlock`、budgeted compiler、身份/可靠性/style contract、时间门控、按需 memory/tool/Daily block 和 hash-only audit。
- `saya_app/services/memory_v4/store.py`
  - 新增独立 `memory_usage_events` 表以及记录、查询、导出和导入接口。
- `core_services.py`
  - 增加 `query_memory_v4()`、`acknowledge_memory_v4_usage()`、`compile_memory_prompt_v4()`；shadow 结果明确不进入 items。
- `core_services.py` / `server5-22B----.py`
  - 增加 flag-gated memory-only context adapter；仅 `memory_v4_read_enabled=true` 且 V4 store 可用时由 `/chat_stream` 采用，异常或不可用自动回退旧 memory builder，默认路径不变。
  - V4 main-read 回答完成后按 selected IDs 写入 usage receipt，并发出紧凑 `memory_usage` SSE 事件；旧 recall 路径不触发。
- `core_services.py` / `saya_app/routers/memories.py`
  - 增加 `daily_state_v2_enabled` 保护的 Daily adapter/API；支持 conversation、page load、backend start、first contact、D0–D3 ring 和 rollover，默认不创建 V4 store。
- `tests/test_memory_v4_phase3.py`
  - 新增 8 条检索、使用回执、DayState、时间/Daily、prompt budget 和 main-read adapter 测试。
- `docs/backend-layout.md`
  - 登记阶段 3 seam。

## 行为与安全边界

- 无 memory need 的普通问题不会检索 Core。
- superseded、archived、quarantined、过期当前事实默认不返回；event memory 需要事件/时间语义才可进入候选。
- `candidate_selected` 不等于 `used`；只有明确 acknowledgement 才记录实际使用。
- 默认 prompt 不显示 ID、source、tags、confidence、层级、星标、锁定或创建时间。
- 当前精确时间、有效期标签和 Daily state 只在时间/相对日期/天气等相关请求，或首次 Daily 简报时注入。
- 审计仅保存 block IDs、触发原因、估算 token、省略原因和 prompt hash，不保存完整 prompt。

## 测试

- `python -m pytest -q tests/test_memory_v4_phase3.py` → **8 passed**。
- 阶段 1–3 合同测试 → **28 passed**。
- 阶段 1+2 + Phase 0 相关回归此前为 **82 passed**；阶段 3 未改动默认旧读路径。

## 遗留风险与下一步

1. 当前 dense retrieval 仍是确定性词法 baseline，尚未接 embedding；跨语言同义表达需要后续评测和 provider 配置。
2. Prompt Compiler 已通过 flag-gated memory-only adapter 接入 `/chat_stream`；默认主读关闭，usage receipt 只在 V4 audit ready 时写入，未做自动 rollout，旧回答路径保持不变。
3. DayState rollover 已清理在线行，但外部任务实体仍由旧任务系统管理；尚未生成后台 synthesis candidate。
4. 阶段 4 才接 Capability Registry、Agent Loop 和统一流式事件；阶段 5 才接 Web Tools V2。

回滚：保持或关闭 `memory_v4_read_enabled`/`memory_v4_shadow_read_enabled`；V4 usage 与 prompt audit 不会改变 legacy memory。V4 数据仍可用 export/import/restore 流程恢复。
