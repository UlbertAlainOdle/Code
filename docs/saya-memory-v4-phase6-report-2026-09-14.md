# Saya Memory V4 阶段 6 技术报告

日期：2026-09-14  
实施阶段：阶段 6：只读连接器与 Attention Inbox

## 结论

只读连接器基础层已完成并通过合同测试，但没有连接真实 Discord/X 服务。默认白名单为空；未授权来源、频道或超过 L1 权限的事件不会进入 Inbox。

## 完成内容

- 新增 `saya_app/services/memory_v4/connectors.py`：
  - 严格 `ConnectorEvent` 合同，含 source event ID、author、channel/thread、trust、permission scope、read state、dedupe hash 和 expires_at。
  - `ConnectorPolicy` 支持 source/channel 白名单和 L0/L1 权限上限。
  - `ReadOnlyConnector` 只有 poll/ingest 方向，没有 send 方法。
  - `AttentionInbox` 支持去重、cooldown、重要度 digest、已读/忽略标记和 4 天 TTL。
- `saya_app/services/memory_v4/store.py`：新增独立 `connector_events` 表、唯一去重索引、查询/已读/过期清理和恢复快照支持。
- `saya_app/services/memory_v4/__init__.py`：导出连接器合同。
- `tests/test_memory_v4_phase6.py`：新增 4 条白名单、权限、去重、poll、digest、cooldown、read 和 TTL 测试。
- `docs/backend-layout.md`：登记阶段 6 seam。

## 行为与安全边界

- External event 与 personal memory 分库；Inbox 不调用 Write Pipeline，不会自动形成 Doctor 的个人事实。
- source_event_id 与 dedupe_hash 双重去重；同一线程在 cooldown 内合并提醒。
- 外部原文有 `expires_at`，默认收到后 4 天；D4 清理不会触碰长期记忆版本。
- `digest()` 只输出有限摘要；外部发送、编辑、删除接口不存在。

## 测试

- `python -m pytest -q tests/test_memory_v4_phase6.py` → **4 passed**。
- 阶段 1–6 合同测试 → **40 passed**。
- 未发起真实外部连接器请求。

## 遗留风险与下一步

1. Discord/X provider、Gateway/API 授权、限流和真正的 source adapter 尚未接入，避免在没有用户授权时读取外部数据。
2. 当前 priority 使用确定性 marker；静默时段和来源级 digest 调度仍需产品配置。
3. 阶段 7 才接后台 synthesis、用户控制和可选外部写入；任何外部写动作必须保持每次确认。

回滚：不创建/不注册 `ReadOnlyConnector`，或保持 connector flags 关闭；已存 Inbox 事件可按 TTL 清理，个人 memory 不受影响。
