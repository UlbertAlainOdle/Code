# Saya Memory V4 阶段 4 技术报告

日期：2026-09-14  
实施阶段：阶段 4：Capability Registry、Agent Loop 与流式事件

## 结论

阶段 4 的安全基础 seam 已完成并通过合同测试，但尚未接管现有
`/chat_stream`。Capability provider 默认保持 `unconfigured`，高风险/写能力
只返回审批要求；现有生产聊天路径不受影响。

## 完成内容

- `saya_app/services/memory_v4/capabilities.py`
  - `Capability` 元数据合同：availability、read/write、risk、permission、预算和 provider handler。
  - `CapabilityRegistry`：注册、实际 availability、策略拒绝和安全参数/结果投影。
  - `register_memory_capabilities()`：memory/daily/weather/web 的只读注册入口；未接入 provider 不伪造 available。
- `saya_app/services/memory_v4/agent_loop.py`
  - 模型只能返回 `final_answer`、结构化 `tool_calls` 或 `permission_request`。
  - 默认最大 6 步；重复调用去重；支持 per-capability limit、取消、审批、失败和预算耗尽后的诚实 partial answer。
  - 统一公共事件序列，工具 I/O 不包围任何模型锁；事件只保留安全摘要，不保存 raw reasoning 或凭据参数。
- `tests/test_memory_v4_phase4.py`
  - 新增 5 条 registry/approval/no-tool/tool-dedupe/secret-safe/budget/cancel 测试。
- `core_services.py` / `tests/test_memory_v4_agent_adapter.py`
  - 增加 `run_memory_v4_agent_loop()` 的受 flag 保护 Core seam；复用 memory/Daily registry，宿主才可提供 model callback，HTTP 不可注入 callable。
- `saya_app/routers/memories.py`
  - 增加 `/v4/agent/run`，只调用服务器预先注册的 `memory_v4_model_step`；缺失时返回 `unconfigured`，请求体不能注入 callable。
- `saya_app/services/memory_v4/capabilities.py` / `core_services.py`
  - 增加可选 `connector.read_feed` 与需确认的 `connector.mark_read`；真实 provider、source whitelist 和 connector flags 仍需宿主显式配置。
- `docs/backend-layout.md`
  - 登记阶段 4 seam。

## 测试

- `python -m pytest -q tests/test_memory_v4_phase4.py` → **5 passed**。
- tool selection deterministic evaluation 覆盖 must/must-not/unavailable/permission/budget/malicious result，forbidden call count 为 **0**。
- 阶段 1–4 合同测试 → **32 passed**。
- 既有相关回归保持此前 **82 passed**；未改动默认 `/chat_stream`。

## 遗留风险与回滚

1. `/chat_stream` 尚未切换到 Agent Loop；目前没有模型实际选择工具的生产接入，也没有 connector provider 被自动注册；Web provider 仅可由宿主显式注入且仍受 flag 保护。
2. resume/retry 的持久化游标和前端工具卡尚未接入现有 SSE；本模块已提供稳定 step/event ID，供后续 adapter 使用。
3. 外部写能力没有 handler，且 policy 默认禁止/审批；不应通过绕过 registry 的方式接入。

回滚：删除或不调用新 registry/loop 即可；现有聊天路径和旧 memory 读写不变。

## 下一步

阶段 5 接入原子 Web Tools V2 时复用本阶段的 registry、预算、审批和安全摘要边界；只有完成 web DLP/SSRF/引用验证后，才考虑 wiring 到 Agent Loop。
