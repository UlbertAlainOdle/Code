# Saya Memory & Agent V4 · 阶段 0 实施汇报

> 日期：2026-09-14
> 依据文档：[saya-memory-agent-v4-technical-plan.md](saya-memory-agent-v4-technical-plan.md)（版本 1.0，最高优先级技术规格）
> 实施范围：**阶段 0 —— 基线、测试隔离与止损**（一次性完整实施，未提前进入后续阶段）
> 结论：**全部完成，验收项 5/5 通过**；核心回归 58 旧测试 + 24 新测试单进程通过，全量套件 503 passed。

---

## 1. 概述

本次按技术方案第 19 节完成阶段 0 的九项目标，集中在四类"止损"：

1. **测试隔离**：修复了 `test_memory3.py` 在模块导入阶段污染全局 `PROJECT_ROOT`/`DEAR_ROOT` 的根因，原 58 个相关测试首次可以在**同一条 pytest 命令**中收集并通过（此前组合收集即崩溃）。
2. **显式记忆误触发**：`capture_explicit_memory()` 从"未锚定全文搜索"改为"句首锚定命令 + 五类拒绝保护"，普通聊天中讨论"记住"不再被写成残句记忆；凭据类内容即使明确要求也永不入库。
3. **提示词时间污染**：新增 `needs_time_context()` 判定，无时间意图的回合不再注入精确时间块、Daily Activity 块和 API 用户消息时间前缀——本地与 API 两种模式同步生效。
4. **反馈循环与默认值**：召回选中不再自增 `access_count`（切断"误存→更易选中→排名上升"的自我强化循环）；候选记忆不再因"满三天 + 高置信"自动转正；手工新增记忆默认不加星。

同时交付：33 条显式记忆意图语料库、24 个新测试、只读审计脚本及首份审计报告（[memory-audit-2026-09-14.md](memory-audit-2026-09-14.md)）。

---

## 2. 修复前的已验证缺陷（方案 §3）

| 编号 | 缺陷 | 实测证据 |
|---|---|---|
| P0 §3.2 | 显式记忆未锚定正则误触发 | 7 个最小用例中 4 个普通"记住"讨论句全部被误存，截出"多少东西？"、"密码的吗？"等残句；线上库已存在同类残句 |
| P0 §3.3 | 候选仅凭"满 3 天 + confidence≥0.8"自动转正；确认时结构化字段丢失；手工新增默认勾选 Star | 代码路径核实（`auto_adopt_pending_candidates`、前端默认值） |
| P0 §3.4 | 每轮注入精确时间（本地系统块 / API `[月-日 时:分]` 用户前缀），诱导模型复述系统时间 | `build_prompt` 与 `event_generator` 代码路径核实 |
| P0 §3.5 | 候选被选中即 `access_count += 1`，与是否被使用无关 | `get_relevant_memories(mark_accessed=True)` 链路核实 |
| §3.7 | 三组测试组合收集失败 | 复现报错：`RuntimeError: Directory '...\saya-memory3-tests-...\project\static' does not exist` |

---

## 3. 修改明细

### 3.1 源码（小范围补丁，未整文件重写）

| 文件 | 修改内容 |
|---|---|
| `core_services.py` | ① `capture_explicit_memory()`：按方案 §6.2 原文实现锚定正则（中/英三式），另允许可选强化副词（一定/必须/务必/千万）；新增 `_explicit_memory_reject_reason()` 拒绝**疑问句尾、残句尾巴（了吗/呢/多少…）、仅代词（这个/它…）、凭据内容（密码/token/API key…）**；引号开头（引用他人话语）直接不匹配；`is_important` 仅在强化措辞时为 True。② `auto_adopt_pending_candidates()`：`candidate_auto_adopt_enabled` 代码默认 **True→False**（settings 文件无此键，实际生效）。③ `get_relevant_memories()` / `_get_relevant_memories_legacy()` / `build_prompt_context_with_audit()`：`mark_accessed` 默认**翻转为 False**，新增 `exclude_daily_activity` 参数贯通传参。 |
| `server5-22B----.py` | ① 新增 `needs_time_context()`（中英关键词启发式，含天气/截止/赛程/节日等时间意图词族）。② `build_prompt()` 新增 `include_time=None` 参数：自动探测或由调用方强制（如首次见面简报）；无时间意图时本地两个分支不再拼入 `Current time` 块；`skip_memory` 分支的 Daily Activity 块同样门控；do-recall 分支向 KB 传 `exclude_daily_activity`。③ `/chat_stream`：每回合计算一次 `turn_time_intent`，贯穿 recall、两次 build_prompt 和 API 用户前缀——**`[MM-DD HH:MM]` 前缀仅在时间意图回合添加，用户原始文本不修改**。 |
| `saya_app/services/conversation.py` | `_memory_lines()` 召回改为 `mark_accessed=False`。 |
| `static/js/app.js` | 手工新增记忆对话框 Star 默认不勾选；提交逻辑 `checked !== false`（缺省回退 true）改为 `checked === true`。 |

### 3.2 测试与工具（新增）

| 文件 | 内容 |
|---|---|
| `tests/test_memory3.py` | 移除模块级 `os.environ` 污染（测试隔离根因修复，附说明注释） |
| `tests/data/explicit_memory_intent_corpus.json` | 33 条意图语料：13 正例（中英、前后缀、强化措辞）+ 20 反例（疑问/引用/假设/能力讨论/残句/仅代词/凭据/非句首保守拒绝） |
| `tests/test_explicit_memory_intent.py` | 语料驱动测试 + 方案 §3.2 原案例回归 + 去重/开关/拒绝原因单元测试（10 个） |
| `tests/test_prompt_time_gating.py` | 时间意图判定正反例、timeless prompt 快照断言（无 Current time / Daily 块 / 用户文本原样）、`include_time` 强制覆盖、Daily 排除、access_count 门控、auto-adopt 默认关闭（14 个） |
| `scripts/audit_memory_baseline.py` | **严格只读**的记忆库审计脚本（`--kb-dir` / `--out` / `--json-out`），输出计数、分层/来源分布、风险标记与阶段 1 迁移提示 |
| `docs/memory-audit-2026-09-14.md` | 首份审计报告（见 §5 摘要） |
| `docs/backend-layout.md` | 追加"Memory V4 Phase 0"章节登记新行为与新文件 |

---

## 4. 测试与验收

### 4.1 测试命令与结果

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/test_memory3.py tests/test_memory_system_fixes.py tests/test_chat_prompt_web.py`（**验收命令**，之前组合收集即失败） | **58 passed** |
| 五文件合并（+两个新测试文件） | **82 passed** |
| 十个记忆/提示词相关文件合并 | **117 passed** |
| 全量套件 `pytest tests/`（排除下述预先损坏文件） | **503 passed** |

唯一失败：`tests/test_memory_graph_ui.py` 4 个用例。**与本次修改无关，属预先存在**——已核实：工作区早前未提交的 UI 重构移除了 gradient/memory-graph 功能（相关字符串在 git HEAD 中存在、工作树中早已删除），本阶段的 app.js 改动仅两行且不涉及这些断言。按方案 §0"不清理无关改动"未处理，留待该 UI 会话收尾。Node 测试未运行（未触碰 minecraft_worker，无影响面）。

### 4.2 验收项对照（方案 §19 阶段 0）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 原 58 个相关测试同一 pytest 命令收集并通过 | ✅ | 4.1 第一行 |
| 误触发 corpus 中所有普通"记住"讨论均不写入 | ✅ | 20 条反例全过（含"多少东西？""密码的吗？"原案例） |
| 明确保存命令仍能保存 | ✅ | 13 条正例全过，存储内容无命令词残留、无问句残尾 |
| timeless prompt 不含 current time、Daily block 或 API 用户前缀 | ✅ | 三层各有断言：`Current time` 缺席、`Today's Activity Memory` 缺席、API 前缀与 `needs_time_context` 同谓词门控 |
| 手工新增普通记忆 `is_important=False` | ✅ | 前端默认不勾选 + 提交逻辑修正 + 显式路径仅强化措辞加星 |

阶段 0 禁止事项核对：未迁移数据库主结构、未删除任何记忆、未实现外部连接器、未大规模重写聊天入口。✅

---

## 5. 记忆库审计摘要（只读，未修改任何数据）

- **总量**：22 条活跃记录（与方案 §16.2 观察一致），0 归档；来源分布 learner 8、session_archive 6、about_you 2、system_activity 2、system/manual/assistant_role/auto_explicit 各 1。
- **风险发现**：
  - 1 条 `auto_explicit` 残句（`dangling_start`）："你的人，所以是有作用的。但是遇到那种会随时忘掉你的人……"——正是 §3.2 描述的误存类，阶段 1 迁移时应标记 `quarantined` 待用户确认，**不自动删除**；
  - Daily Activity（`system-daily-activity-current`）位于 Core 且 locked+starred——阶段 1 应迁为 DayState D0；
  - 6 条 Core 记录（Life Quests、About You×2、Daily、Anki 状态、1 条手工 Important）已列入清单，供阶段 1 迁移预览；
  - 5 条受保护的会话摘要（locked/starred）将进入 `suggest_unlock/suggest_unpin` 预览，不自动执行。
- **auto-adopt 设置**：settings 文件中无该键，代码默认现已为 False。

---

## 6. 已知限制

1. **非句首保存命令暂不保存**（"对了，你顺便记住我中午喜欢喝美式"、"I want you to remember…"）——锚定兜底刻意保守，语料中已标注 `known_conservative_miss`，等待阶段 2 Write Intent Gate 的结构化意图判断/工具调用主路径。
2. **时间意图判定为关键词启发式**：未知措辞退化为"无时间注入"（宁漏勿乱）；阶段 3 Prompt Compiler 将以结构化 turn context 完善。
3. **Core 无条件注入问题（§3.5）未动**，属阶段 3；本阶段仅实现 Daily 快照的按需排除。
4. `test_memory_graph_ui.py` 4 个失败为预先存在，待 UI 重构会话自行收尾。

---

## 7. 回滚方法

- 本阶段**无数据迁移、无 feature flag 切换**，源码改动按文件粒度 `git checkout -- <file>` 回退即可；
- 新增文件（测试/脚本/报告）可直接删除，不影响运行时；
- 如需恢复旧的候选自动转正行为：在 `knowledge_base/memory_settings.json` 显式写入 `"candidate_auto_adopt_enabled": true`；
- 如需恢复每轮时间注入：`build_prompt(include_time=True)` 或在调用点移除 `turn_time_intent` 门控（改动点集中、易定位）。

---

## 8. 后续阶段（均未开始）

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段 1 | V4 Schema、Store、Version 与迁移预览（dry-run report、双写、幂等 apply） | 待实施 |
| 阶段 2 | Write Pipeline（write intent、evidence、extractor、validator、supersede） | 未开始 |
| 阶段 3 | Retrieval V4、Daily State 四日环、Prompt Compiler | 未开始 |
| 阶段 4 | Capability Registry、Agent Loop、统一事件流 | 未开始 |
| 阶段 5–7 | Web Tools V2 / 只读连接器 / 后台综合与用户控制 | 未开始 |

阶段交接记录已按方案 §25 模板追加至技术方案文档末尾。**本次未自动开始阶段 1，等待确认。**
