# Saya Memory V4 阶段 7 技术报告

日期：2026-09-14  
实施阶段：阶段 7：后台综合、用户控制与可选外部写入

## 结论

阶段 7 的安全 seam 已完成并通过合同测试。后台综合只接受带 evidence refs 的 JSON patch；用户控制可以查看、纠正、pin/unpin、禁止主动提及、忘记和恢复；Temporary Chat 完全隔离。外部写入仍默认禁用，没有连接器发送动作。

## 完成内容

- 新增 `saya_app/services/memory_v4/synthesis.py`：
  - `BackgroundSynthesizer` 只允许 `replace/remove` bounded JSON-pointer patch，操作必须引用已有 evidence；不允许 root rewrite 或 secret。
  - `BackgroundSynthesisScheduler` 只处理 bounded pending candidate batch；成功后将 candidate 标记为 `accepted`，校验失败标记为 `rejected` 并保留原因；没有服务端 producer 时明确返回 `unconfigured`，不会自行调用模型或执行任意请求体代码。
  - 过期记忆可转为 `expired`，保留版本链。
  - `MemoryControls` 提供 summary、why-used、correct、pin/unpin、do-not-mention、forget、restore。
  - `TemporaryChat` 的 query/write/prompt_context 均是隔离边界。
  - `ExternalWriteGate` 要求 enabled、preview、approved、幂等键和参数 hash 一致；重复执行返回 already_executed。
- `retrieval.py`：尊重 `metadata.do_not_mention`，除非调用方显式允许。
- `core_services.py` / `saya_app/routers/memories.py`：新增 flag-gated V4 status/query/controls/daily/capabilities API；controls 支持 summary、why-used、correct、pin/unpin、do-not-mention、forget、restore，纠正字段采用白名单并同步兼容 legacy projection；`/v4/synthesis/run` 只触发服务端已配置的 producer。
- `tests/test_memory_v4_controls_api.py`：验证 controls flag、版本纠正、非法字段拒绝、forget/restore。
- `store.py`：connector/usage/export 仍保持独立；synthesis 版本通过事件审计。
- `tests/test_memory_v4_phase7.py`：覆盖 patch、用户控制/恢复、Temporary Chat、外部写 gate 和 scheduler 校验；新增 `test_memory_v4_synthesis_adapter.py` 覆盖 Core 入口与 flag/producer 边界。
- `docs/backend-layout.md`：登记阶段 7 seam。

## 测试

- `python -m pytest -q tests/test_memory_v4_phase7.py` → **4 passed**。
- 阶段 1–7 合同测试 → **44 passed**。
- 最终阶段回归 → **44 passed**；排除既有 UI/Windows 临时目录问题后的全量回归 → **516 passed**。
- 原始全量测试 → **534 passed, 4 failed, 17 errors**；4 个失败仍为 `test_memory_graph_ui.py` 的既有 UI 合同不一致，17 个错误仍为 `test_listener.py`、`test_skin_config.py`、`test_slu.py` setup 扫描 `C:\Users\lll\AppData\Local\Temp\pytest-of-lll` 时的 `PermissionError`，未发现本阶段新增失败。
- 追加 deterministic evaluation 与 main-read adapter 后，评测合并合同集为 **48 passed**，排除既有问题后的扩展回归为 **520 passed**，原始全量为 **538 passed, 4 failed, 17 errors**；失败与错误集合未变化。
- 修正了重复显式保存：相同 predicate/scope/value 只补 evidence/确认时间，不新增内容 revision；显式新值仍产生 supersede revision。
- 没有执行外部写动作，没有改变 legacy memory 或现有聊天路径。

迁移安全检查：真实 `knowledge_base` 未创建 `memory4.sqlite3`、WAL/SHM 或 `memory-v4-migration.*` 备份；CLI 仍只执行 preview。

## 遗留风险与回滚

1. scheduler 已接入受控 Core/API seam，但尚未配置真实模型综合器；没有证据的修改会被拒绝。
2. 用户控制已接受控 HTTP 路由，尚未接 Memory UI；路由仍由 V4 write/read flag 保护。
3. `ExternalWriteGate` 是安全壳而不是已批准的连接器写 provider；必须通过独立安全评审后才能启用。
4. 全套 A/B 评测、真实模型工具选择、Discord/X 授权和 `/chat_stream` wiring 仍未完成。

回滚：保持 synthesis/controls 不调用，保持 `external_write_tools_enabled=false`；Temporary Chat 不会写入任何 V4/legacy 数据。

## 后续接入补充

- `BackgroundSynthesisScheduler` 已经通过 Core/API 受控接缝接入；candidate 成功后为 `accepted`，验证失败为 `rejected`，producer 缺失为 `unconfigured`，默认不会自动运行。
- controls、Daily、capability discovery、Agent Loop adapter 和 `/chat_stream` 的 V4 usage receipt 已有独立合同测试；Web Tools 只有显式 provider 注册后才可用。
- 最新回归：排除既有 UI/Windows 临时目录问题后 **535 passed**；原始全量 **553 passed, 4 failed, 17 errors**，失败/错误集合仍未变化。
