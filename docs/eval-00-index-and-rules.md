# Saya 分阶段评测包：目录与统一规则

这套任务用于确认“哪些能力真的可用、哪些只在理想路径可用、下一轮先修什么”。请按编号逐份交给 GLM，每次只执行一份，不要一次性完成全部。

## 顺序与产出

1. `eval-01-baseline.md` → `evaluation_results/01-baseline.md`
2. `eval-02-chat.md` → `evaluation_results/02-chat.md`
3. `eval-03-memory.md` → `evaluation_results/03-memory.md`
4. `eval-04-personal-management.md` → `evaluation_results/04-personal-management.md`
5. `eval-05-web-and-integrations.md` → `evaluation_results/05-web-and-integrations.md`
6. `eval-06-reliability-ux-performance.md` → `evaluation_results/06-reliability-ux-performance.md`
7. `eval-07-final-report.md` → `evaluation_results/07-final-report.md`

首次执行时，把本文件与 `eval-01-baseline.md` 一起给 GLM。之后每次执行也要求 GLM遵守本文件。

## 强制规则

- 当前阶段只评测和记录，不修复、不重构、不格式化生产代码。
- 保留所有既有改动；禁止 reset、checkout、clean、删除或覆盖用户文件。
- 不修改 `server5-22B----.py`、`core_services.py`、`saya_app/`、`static/` 和真实 `knowledge_base/`。
- 合成数据、破坏性模拟必须使用临时目录或数据副本。
- 真实付费 API 默认不调用；优先 mock。真实网络测试需用户明确允许。
- 不输出 API Key、Cookie、Token、私人记忆或聊天全文；报告只写“已配置/未配置”。
- 被前置问题阻塞时，继续完成可做的静态检查，并明确标记阻塞。

## 状态与证据

状态：`PASS`、`PARTIAL`、`FAIL`、`BLOCKED`、`NOT_APPLICABLE`。

证据等级：

- `A-实测`：实际操作观察到；
- `B-自动测试`：可重复测试验证；
- `C-静态检查`：仅从代码/配置推断；
- `D-未验证`：受模型、密钥或环境限制。

不得把 C 或 D 写成“功能正常”。

严重度：`P0` 数据丢失/密钥泄露/任意文件或内网访问/无法启动；`P1` 核心主路径不可用或产生错误数据；`P2` 重要辅助功能、性能或反馈问题；`P3` 低频、视觉和维护性问题。

## 每份报告固定结构

```markdown
# 阶段名称
## 摘要
- 总体状态：
- 已验证/未验证：
- P0/P1 数量：
## 环境与方法
## 用例结果
| ID | 用例 | 状态 | 证据等级 | 结果 | 证据路径 |
## 缺陷
| ID | 严重度 | 标题 | 复现步骤 | 预期 | 实际 | 影响 |
## 阻塞与限制
## 阶段结论
```

命令和日志必须脱敏。完成当前报告后停止，等待下一份任务。

