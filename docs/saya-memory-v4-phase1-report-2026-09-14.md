# Saya Memory & Agent V4 · 阶段 1 实施汇报

> 日期：2026-09-14
> 依据：[saya-memory-agent-v4-technical-plan.md](saya-memory-agent-v4-technical-plan.md)
> 范围：阶段 1 —— V4 Schema、Store、Version 与迁移预览
> 结论：阶段 1 的本地合同、独立存储、dry-run/apply、双写边界和回归测试已完成；真实 `knowledge_base` 只执行过只读 preview，未执行 apply。

## 1. 已完成

- 新增 `saya_app/services/memory_v4/models.py`：实现 `MemoryItemV4`、`MemoryEvidence`、`MemoryVersion`、`MemoryCandidateV4`、`DayState`、`TurnEvent` 和 `MemoryProposal`。所有跨存储对象在构造时校验 schema、枚举、时间、数值范围和 JSON 可序列化性；secret 正文、公有事件中的 token/system prompt/推理字段会被拒绝。
- 新增 `saya_app/services/memory_v4/store.py`：创建独立 `memory4.sqlite3`，包含 `memory_items`、`memory_evidence`、`memory_versions`、`memory_candidates`、`day_states`、`turn_events`、`memory_tombstones` 和元数据表。更新、pin/unpin、supersede、delete、restore 都保留不可变版本；墓碑可以没有正文，避免删除记录复活。
- 新增 `saya_app/services/memory_v4/migration.py`：`preview()` 完全只读；`apply()` 在写 V4 前复制 `knowledge.json`、删除账本、候选、设置和 legacy SQLite 到时间戳备份目录。旧 ID、正文、锁定、星标、来源、层级、证据和删除墓碑均有映射；Daily Activity 进入 `DayState`，可疑 `auto_explicit` 进入 `quarantined`，session summary 的解锁/取消星标只生成建议。
- 新增 `saya_app/services/memory_v4/compat.py`：显式开关控制双写，并提供旧形状漂移检查。新增 `flags.py` 记录 V4 rollout flags 的实际切换。
- `KnowledgeBase` 增加 V4 状态、preview/apply/export 和可选双写入口。默认 `memory_v4_write_enabled=false`、`memory_v4_read_enabled=false`、`memory_v4_shadow_read_enabled=true`，默认不会创建 `memory4.sqlite3`，旧回答路径不变。
- 新增本地 CLI：`scripts/migrate_memory_v4.py` 默认 dry-run，只有 `--apply` 才会创建 V4 库。普通备份也会包含 V4 source database 和 flag audit log。

## 2. 真实库只读预览

在当前 `knowledge_base` 上运行 preview，未产生 `memory4.sqlite3`、迁移备份或 legacy 文件变更：

| 项目 | 数量 |
|---|---:|
| source records | 22 |
| converted memories | 20 |
| DayState split | 1 |
| quarantined | 1 |
| suggest unpin | 2 |
| suggest unlock | 5 |
| conflicts | 3 |
| deletion tombstones | 172 |
| candidates | 0 |

与 Phase 0 审计中的风险项一致：残缺 `auto_explicit` 没有自动删除，Daily Activity 没有继续作为 Core 记忆写入。

## 3. 测试

- `python -m pytest -q tests/test_memory_v4_phase1.py` → **10 passed**。
- `python -m pytest -q tests/test_memory_v4_phase1.py tests/test_priority_improvements.py tests/test_memory3.py tests/test_memory_system_fixes.py tests/test_chat_prompt_web.py` → **72 passed**。
- 9 个本次相关 Python 文件 AST parse → **通过**。
- 全量 `python -m pytest -q tests/` → **500 passed, 4 failed, 17 errors**。4 个失败是 Phase 0 已知的 `test_memory_graph_ui.py` UI 重构断言；17 个 setup errors 是当前 Windows 临时目录 `pytest-of-lll` 的 PermissionError，集中在预先存在的 listener/skin/slu 测试，不涉及 V4 代码。
- 排除上述已知 UI 文件和临时目录权限受影响的 listener/skin/slu 文件后 → **482 passed**。

## 4. 数据迁移和回滚

- 本次没有对真实库执行迁移；legacy JSON、legacy SQLite、删除账本和设置均保持原样。
- 测试 apply 会先生成独立 `backups/memory-v4-migration.<timestamp>/`，再写 `memory4.sqlite3`。重复 apply 不重复创建版本，也不会覆盖已存在的 V4 修订。
- V4 支持 `export_state()` / `import_state()`；即使当前正文已 purge，历史 version snapshot 仍可由显式 `restore_version()` 恢复。关闭 `memory_v4_write_enabled` 即停止旧路径双写，不删除 V4 数据。
- 若要撤销本阶段代码，按新增文件和对应小范围 diff 回退；不要删除 legacy 文件。真实 V4 数据库若已 apply，应先保留 migration backup 或 export，再由用户明确决定清理。

## 5. 未实施

阶段 2 Write Pipeline、阶段 3 Retrieval V4/Daily State 四日环/Prompt Compiler、阶段 4 Agent Loop、阶段 5–7 Web Tools、连接器、后台综合和外部写入均未开始。V4 主读也仍保持关闭。

