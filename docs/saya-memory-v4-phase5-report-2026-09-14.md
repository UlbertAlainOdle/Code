# Saya Memory V4 阶段 5 技术报告

日期：2026-09-14  
实施阶段：阶段 5：Web Tools V2

## 结论

Web V2 原子工具 seam 已完成并通过合同测试。它复用了现有 SSRF/DNS/重定向/robots/下载大小限制和引用校验；没有自动联网，也没有接管当前聊天流程。

## 完成内容

- 新增 `saya_app/services/memory_v4/web_tools.py`：
  - `WebToolsV2.search()` 与 `WebToolsV2.read()` 分离搜索和深读。
  - 默认每 turn 最多 2 次 search、5 个 URL，且有总字符预算。
  - 使用会话内稳定 source ID；搜索重复 URL 去重，read 复用相同编号。
  - 私有词、email、电话和 secret 在 query 发出前脱敏；只记录字段级 redaction 结果。
  - read 内容标记为不可信并检测网页 prompt injection；不会把网页指令当系统指令。
  - 缺 provider 时 fail-open 返回 `unconfigured`/`failed`，不伪造结果。
  - `validate_answer()` 复用现有 lexical citation validator。
- `saya_app/services/memory_v4/capabilities.py`：注册可选 `web.read` capability。
- `tests/test_memory_v4_phase5.py`：新增 4 条 DLP、来源编号、SSRF、网页不可信标记、引用、预算和 provider fail-open 测试。

## 测试

- `python -m pytest -q tests/test_memory_v4_phase5.py` → **4 passed**。
- 阶段 1–5 合同测试 → **36 passed**。
- 既有 Web 回归保持原状态；阶段 5 没有修改旧 Web Research 实现。

## 安全边界

- 私网/回环 URL 即使传入 fake reader 也会在 facade 层阻断；真实 reader 仍会复验每次 redirect。
- API key 只作为 provider 调用参数，未进入结果、来源、事件或审计；搜索结果只返回摘要，不返回完整网页正文。
- 完整正文只存在于当前 `read` 返回中，并标记 `untrusted_content=true`；不写入个人记忆。
- web tool 未自动注册到生产 Agent Loop；普通聊天不会因为该 seam 产生不必要联网。

## 遗留风险与下一步

1. 私人姓名/地址的通用识别仍依赖调用方传入 `private_terms`；不能在没有用户上下文的情况下可靠猜测所有人名。
2. 当前 facade 使用现有 Brave adapter，真正的 `/chat_stream` wiring、web budget 与 Agent Loop 联动留待后续接入审查。
3. 网页知识要进入长期知识库时，仍必须经过明确保存、URL/标题/retrieved_at/validity/evidence contract；本模块不会自动提升。

回滚：不注册 `WebToolsV2` handler 或保持 `web_tools_v2_enabled=false`；不会改变旧聊天、旧 memory 或外部状态。
