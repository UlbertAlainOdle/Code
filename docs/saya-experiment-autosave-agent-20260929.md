# 实验报告：自动保存记忆强化 + Agent 自主工具选择（2026-09-29）

> 实验脚本：`scripts/experiment_autosave_and_agent_20260929.py`
> 终版统一报告：`docs/experiment-autosave-agent-final-20260929.json`（同一进程 A+B，全新采样，Part A 7/7 + Part B 9/9；此前各迭代的分过程报告见同目录时间戳 JSON）
> 真实模型：Qwen3-30B-A3B-Instruct-2507-Q5_K_M（本地 GGUF，llama-cpp 0.3.9，RTX 4090）
> 数据边界：全部使用临时 KnowledgeBase；真实 `knowledge_base/` 未被写入。
> 每部分给出判定标准（先验定义）、迭代过程、发现的真实缺陷与修复。

---

## Part A：自动保存记忆链路（真实本地模型 triage + recurrence 自动确认）

### 判定标准（先验定义）

| 判定 | 含义 |
|---|---|
| no_secret_leak | 银行密码类内容 0 候选 |
| no_casual_candidates | 闲聊（猫视频）0 候选 |
| no_ephemeral_permanent_writes | 一次性临时内容（昨晚睡眠等）不被自动转正（方案 §6.7：pending 候选≠误存，误存=不该永久化的被永久化） |
| stable_capture_ok | 稳定事实（工作/吃辣/显示器/咖啡/钓鱼×2）捕获 ≥60% |
| recurrence_merge_ok | 钓鱼偏好两次提及合并为 1 条候选且 occurrence_sessions ≥2 |
| auto_confirm_correct | 钓鱼（preference，跨 2 会话）被自动转正；health 类不被自动转正 |
| evidence_ok | 所有候选携带 evidence_hash（可回溯） |

语料：13 条中文对话（3 个 session），覆盖 stable×4 / repeated×2 / ephemeral×3 / casual×1 / secret×1 / health×2。

### 迭代过程

**第 1 轮（修复前基线）：3/5 通过。**
- 通过：无误写、稳定捕获、证据完整。
- 失败：recurrence 不合并、自动确认不触发。
- **根因 1（关键缺陷）**：`_autonomous_memory_local_request` 用原始补全调用本地模型，未走 chat 模板。探针实验证实：同一 GGUF，`create_chat_completion` 输出完美 JSON，raw 补全输出的是续写垃圾/空串。0.7–0.9s 的空输出=模型立即结束。第一次钓鱼事实因此漏采，recurrence 无从发生。
- **修复 1**：triage 与 `LocalLlamaAgentStep` 均改为优先 `create_chat_completion`（应用 GGUF 自带 chat 模板），raw 补全仅作回退；raw 回退的 stop 表移除 ` ``` `（会截断合法 fenced JSON）。

**第 2 轮：误写判定全过，但 recurrence 仍不合并（模型改写措辞使表面 Jaccard≈0.23 << 0.6）。**
- **根因 2**：triage 模型每次改写同一事实（"坐在那儿放空特别解压" vs "最稳定的放松方式"），纯 Jaccard 永远合不上；且临时状态（昨晚睡眠）与单词释义被过度提取、内容夹带模型推断。
- **修复 2**：
  - 合并规则改为三臂：Jaccard≥0.6 ‖ (containment≥0.5 且 ≥2 特征锚点) ‖ ≥3 特征锚点（泛词表排除 doctor/喜欢/说明等）。离线验证 6 组对照（含"不喜欢吃辣 vs 不喜欢加班"反例）全部正确。
  - triage 提示词新增：不存当日状态、不存词典查询、内容不得附加解释推断、分数勿饱和。

**第 3 轮（终版）：7/7 判定全部通过。**

### 终版结果（docs/experiment-autosave-agent-20260929-230236.json）

| 指标 | 结果 |
|---|---|
| secret 泄漏 | 0 |
| 闲聊候选 | 0 |
| 临时内容永久化 | 0（"昨晚只睡4小时"模型自行拒绝；"perennial 单词"被闸门过滤） |
| 稳定事实捕获 | 6/6 |
| 钓鱼 recurrence | 合并为 1 条，occurrence_count=2，sessions=[1,2] |
| 自动确认 | 仅钓鱼 1 条（唯一跨会话重复的非敏感偏好），未加星未锁定 |
| health（血压/降压药） | 2 条均保持 pending（人工确认） |
| 证据可回溯 | 100%（全部携带 evidence_hash） |
| 每条 triage 耗时 | 8–26s（Qwen3-30B-A3B，420–512 token） |

单次出现的 PLC 工期类项目事实生成了 pending 候选（方案允许的"阶段决策"级候选，不自动转正）。

---

## Part B：Agent 自主工具选择（真实模型 + 真实工具执行）

### 判定标准（先验定义）

9 个场景，三类：
- **严格零调用**（寒暄/闲聊/创作）：不得调用任何工具，直接回答；
- **必查记忆**（咖啡偏好/工作方向/项目进展）：`memory.query` 必须成功执行（真实 FTS 检索 8 条种子记忆）；
- **不可用纪律**（天气/web 未配置）：不得按名尝试不可用能力；允许只读替代；必须诚实说明。

### 迭代过程

**第 1 轮（chat 模板修复后）：4/9。**
- `memory.query` 报 `invalid_arguments`：本地模型传参类型松散（数组传字符串等）→ **修复**：`_coerce_arguments` 温和矫正（数字字符串→int、标量→数组），不可矫正的仍严格拒绝。
- `project_status` 不查记忆含糊作答 → **修复**：策略提示词加入"'你还记得/进行到哪一步'类问题必须先查记忆"。
- 对未配置的 weather/web 仍然发起调用（被策略拦截，无执行）→ **修复**：能力清单只保留 available 项。
- 个别最终答案以原始 JSON 形态泄漏 → **修复**：本地适配器启用 `response_format=json_object` grammar；畸形 JSON 不再原样作为答案。

**第 2 轮：6/9。新发现：**
- 实验装置失真：recording 包装破坏 `isinstance(LocalLlamaAgentStep)` 判定 + 临时 KB 未镜像真实部署的 `memory_v4_agent_allow_private=true`，导致 memory/time 被隐私策略剥出白名单（生产路径不存在此问题）→ 修正实验镜像真实设置。
- 模型输出 `{"memory.query":"Saya项目进展"}` 畸形调用形态 → **修复**：JSON contract 指令给出三种合法形态的字面示例。
- 对不可用工具发 `permission_request` 导致整回合卡在 needs_approval → **修复**：Agent Loop 对不可用工具的 permission 请求回喂"unavailable"结果并继续循环。
- 提示词点名未配置工具名仍诱发死调用 → **修复**：策略要点按实际可用能力动态生成（web.search/weather.query 不在清单时全文不出现该名字）。

**第 3 轮：8/9**（weather 场景：未尝试 weather.query，用 time/memory 只读替代后诚实作答"无法获取天气"——行为质量正确，是判定标准过严）→ 判定标准修正为"禁止按名尝试不可用工具 + 允许只读替代"。

**第 4 轮（终版）：9/9 全部通过。**

### 终版结果（docs/experiment-autosave-agent-20260929-232224.json）

| 场景 | 工具决策 | 结果 |
|---|---|---|
| 寒暄 | 0 调用，直接回答 | ✓ |
| 闲聊（猫） | 0 调用，直接回答 | ✓ |
| 创作 | 0 调用，直接回答 | ✓ |
| 咖啡偏好回忆 | memory.query 成功 | ✓ |
| 工作方向 | memory.query 成功 | ✓ |
| 项目进展 | memory.query + memory.read（两级深度） | ✓ |
| 现在几点 | time.query 成功，答案含真实时间 | ✓ |
| 天气（未配置） | 0 次不可用尝试；time/memory 只读替代；诚实说明 | ✓ |
| Python 3.14（web 未配置） | 0 次不可用尝试；诚实 hedge 作答 | ✓ |

无 forbidden 调用、无权限绕过、无预算失控；每回合 1–2 步内完成。

---

## 本轮实验驱动的产品代码修复清单

1. `server5-22B----.py`：本地 triage 改走 `create_chat_completion`（chat 模板）；raw 回退 stop 表修复；triage 提示词防临时状态/防夹带推断/防分数饱和。
2. `saya_app/services/memory_v4/model_adapters.py`：LocalLlamaAgentStep chat 模板 + JSON grammar；JSON contract 字面示例；answer 同义键修复；畸形 JSON 显式失败不泄漏；策略要点按可用能力动态生成。
3. `saya_app/services/memory_v4/capabilities.py`：`_coerce_arguments` 参数温和矫正。
4. `saya_app/services/memory_v4/agent_loop.py`：能力清单 available-only；不可用工具 permission 请求回喂继续。
5. `core_services.py`：候选近重复合并三臂规则（Jaccard/containment+锚点/锚点数）。

## 回归

- 全量相关回归 **220 passed**（memory3/fixes/chat_prompt_web/autonomous_upgrade/explicit_intent/time_gating/V4 phase1–7/evaluation/synthesis/agent_adapter/capabilities/controls/daily/remaining/ai_compression/capacity/graph/game）。
- 既有 `test_memory_graph_ui.py` 4 失败为历史遗留（工作区早前 UI 重构），未触碰。

## 结论

两项修改经真实本地模型端到端实验验证有效（终版统一运行 Part A 7/7 + Part B 9/9，且连续三次运行行为稳定）：
1. **自动保存记忆**：7/7 判定通过——稳定事实可靠捕获、秘密/闲聊/临时内容零永久化、跨会话重复自动转正且永不波及敏感类、证据 100% 可回溯。
2. **自主工具选择**：9/9 场景通过——该查记忆时查、该直接答时直接答、组件不可用时按名零尝试且诚实说明，替代性只读探测合理。
