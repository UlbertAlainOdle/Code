# Minecraft 酿造与炼药锅后续实现说明（2026-09-03）

## 本轮已经完成、不要重复改写的部分

- Desktop Console 的当前任务卡增加了“取消当前任务”；挂起任务按钮统一显示为“取消挂起任务”。
- `POST /api/bridge/minecraft/task/cancel` 会同时取消计划器和 Worker 前台任务，但保留持续跟随状态。
- 战斗识别范围由 `combat_detection_range` 控制（8–128，默认 48）；UI 按运行时实体 ID 锁定所点击的那一个实体，之后持续追击，只有收到 `entityDead` 才算击杀，也可人工取消。
- 跟随已从动态 `GoalFollow` 改成周期刷新的有界静态 `GoalNear`。落空时先等待着地，连续停滞会停止旧路径并冷却，三次恢复失败后进入安全等待，不再靠退出重进解决。
- Python 监督器的心跳重启计时器只允许安排一次；心跳导致重启时会打开跟随熔断器，避免自动恢复同一坏路径形成无限重连。
- 熔炉流程已完成：计划器会补工作台/熔炉/燃料/原料；Worker 会真实放入输入和燃料、等待并取出产物；高炉仅处理粗铁/粗金/粗铜，烟熏炉仅处理食物，其他配方只使用普通熔炉。步骤只在背包后置条件成立后推进。
- 对话、网页、游戏内聊天和 Desktop Console 共用 `ConversationService`；复杂自然语言会经大模型产生 goal，再由计划器拆步执行。
- 1.21.5 药水身份适配器、46 项版本限定注册表回退、确定性配方图、`brew` Worker 命令和 `cauldron` 方块交互命令已经实现。服务器下发动态注册表时会覆盖静态回退。

## 实机验证记录

环境：Minecraft 1.21.5，Mineflayer Worker，模型 `deepseek-v4-flash`。

1. 发送“看一下现有材料，想办法给我炼出三个铁锭”。大模型返回 `obtain_and_deliver iron_ingot ×3`，创建五步计划。
2. 当时背包已有铁锭 2、粗铁 23、圆石和木材，附近没有熔炉。Saya 放置工作台、制作并放置熔炉、只烧炼缺少的 1 个铁锭，最后把 3 个铁锭交给 `Kurosawa_Jin`，并确认被拾取。Worker 心跳约 0.4 秒，无假完成。
3. 对 36.6 格外的猫发起攻击，4 秒内实际移动约 12 格；调用取消接口后活动变为 `cancelled`，连接与心跳保持正常。
4. 再次攻击同一只猫，约 8 秒后收到死亡事件，活动以“已由死亡事件确认击杀 1/1”完成。
5. 跟随运行 16 秒时使用 `bounded_static_goal`，距离约 2.7–2.9 格，停滞和恢复次数均为 0，心跳持续正常。
6. 重载后的在线 Worker 显示 `potion_registry.size=46`、`source=vanilla_1.21.5`；持续跟随仍开启，Saya 与 `Kurosawa_Jin` 距离约 1.8 格。
7. Desktop 对话“酿造一瓶治疗药水”被统一对话层解析为 `brew healing 1` 并创建活动。现场 32 格内确实没有酿造台，活动明确以“32 格内没有酿造台”失败，随后 follow 自动恢复；没有伪造完成。
8. 后续现场放置酿造台并提供水瓶、下界疣、烈焰粉和闪烁的西瓜片后，`brew healing 1` 完整运行两阶段并以“已酿造并取出 healing x1”完成；随后 `give potion 1 to Kurosawa_Jin` 也确认被拾取，follow 恢复为 `near_target`。

## 已实现：酿造台执行器

实现文件：`minecraft_worker/potion.mjs`、`minecraft_worker/brewing.mjs`、`minecraft_worker/worker.mjs`。

- `potion`、`splash_potion`、`lingering_potion` 会从 `potion_contents` 或旧 NBT 解析出稳定药水身份。
- Java 1.21.5 服务器未下发内置 `minecraft:potion` 注册表，因此使用经 Yarn 1.21.5 声明顺序核对的 46 项回退；其他版本不会套用此表。
- `brew <potion> <1..3>` 使用确定性配方图，不让大模型自由猜配方。
- Worker 直接以 `bot.openBlock` 打开窗口，核对类型和槽位；先安全移出遗留瓶/原料，再放瓶、烈焰粉和每阶段原料。
- 每一阶段都等待瓶槽实际变为目标药水；最终必须确认背包新增目标药水，超时、错药、缺料、错容器都会失败。
- 取消使用现有前台任务 token，循环会在 250ms 边界终止，`finally` 关闭窗口并清理路径控制。
- 命令目录和中英文对话已接入，Desktop Console 会从目录显示“酿造药水（实验）”。

### 仍需下一次有材料的实机验收

单瓶治疗药水成功路径已经实机通过。下一位 AI 不应重写执行器，只需继续补齐批量与取消场景：

1. 放置一个酿造台；给 Saya 1 水瓶、1 下界疣、1 烈焰粉、1 闪烁的西瓜片。
2. `brew healing 1` 已通过；保留为后续版本升级后的基础回归即可。
3. 再用三瓶测试 `brew healing 3`；最后在酿造中途点“取消当前任务”，确认活动为 `cancelled`、窗口关闭且物品没有消失。
4. 如果连续三次都因窗口槽位协议问题失败，保存 `window.type`、`inventoryStart`、0–4 槽的脱敏 JSON 和 Worker 日志，再停止试验；优先修槽位同步，不得放宽后置条件。

更高级的自然语言“给我做两瓶治疗药水并交给我”尚未加入高层计划器依赖图。当前对话能创建 `brew` 直接任务，但不会自动探索下界获取下界疣/烈焰粉，也不会自动接续交付。后续应新增 `brew_and_deliver` 高层目标，缺少不可稳定采集的下界材料时挂起并列出缺料。

### 酿造验收测试

- 无酿造台、无烈焰粉、错误瓶中液体、槽位被其他玩家占用时均应安全失败或挂起。
- 三瓶同时酿造与一瓶酿造都应只计算真实新增的目标 NBT 数量。
- 中途点击取消后窗口关闭，背包/槽位物品不丢失，活动状态为 `cancelled`。
- 服务器延迟、窗口更新乱序或目标药水 NBT 不匹配时绝不能报告完成。
- 对话“给我做两瓶治疗药水并交给我”应创建依赖计划，并在最终交付被拾取后完成。

## 已实现：炼药锅基础交互

炼药锅不是通用制造站，因此没有伪造“炼药锅制造药水”。当前实现的是 Java 版可核验的桶交互：

- `cauldron fill water|lava|powder_snow`
- `cauldron collect water|lava|powder_snow`
- 装入要求空锅和对应满桶；取出要求对应满锅和空桶。
- 只有方块名变化与背包中目标桶增加同时成立才完成。

现场没有炼药锅或桶，成功路径尚未实机验证。下一次测试各选一个水装入/取出场景即可；部分水锅无法用桶取水时应该失败，这是正确行为。皮革染色/清洗和玻璃瓶逐级取水尚未实现。

## 下一位 AI 的推荐入口

- Worker 行为：`minecraft_worker/worker.mjs`
- 命令解析：`minecraft_worker/command.mjs`
- 确定性计划器：`saya_app/services/minecraft_planner.py`
- 对话路由：`saya_app/services/conversation.py`
- 命令目录：`saya_app/services/minecraft_catalog.py`
- 后端接口：`saya_app/routers/bridge.py`
- UI：`desktop/saya_companion_console.py`
- 现有回归：`minecraft_worker/test/command.test.mjs`、`tests/smoke_conversation_routing.py`、`tests/smoke_planner.py`

不要降级为只按物品名 `potion` 判断，也不要删除 1.21.5 版本限制。当前最有价值的下一步是用具备材料的生存测试场景完成上述三项酿造验收，然后再把酿造纳入高层计划器。


---

## 2026-09-03 第二轮补充（本轮新增，已回归通过）

以下为在本文件之后完成的内容，接手前请勿重复实现：

- 熔炼（`smelt`）、酿造（`brew`）、炼药锅（`cauldron fill/collect water|lava|powder_snow`）的 Worker 实现与目录条目均已存在且带严格后置条件；brew 有 `potionNamesById` 注册表守卫，缺少可靠药水数据时会明确拒绝而不是瞎酿。
- 新增 `sleep` 命令全链路：Worker `sleepInNearbyBed`（32 格找床、走向、尝试 `bot.sleep`；床被占时定位 2.2 格内的村民并走向推挤下床后重试，最多 3 次；白天/雷暴给出明确错误），目录条目（supports_llm）、`parse_intent` 中文路由（去睡觉/睡觉/上床睡觉/躺下睡/睡一觉）与人格化回复，Console 常用页按钮。stop 可叫醒。
- 执行器线程加固：`_run_plan` 异常不再杀死线程（原来会把计划永远卡在 running，即"完成的任务挂在上面/按钮无效"的根因）；`state()` 自带死线程回收（`_recover_dead_thread`），幽灵任务自动结案为 failed 并写明原因。
- 新增 `POST /api/bridge/minecraft/plan/suspend` 与 Console「挂起当前任务」按钮：手动暂停当前计划，进入挂起队列，仍可自动/手动恢复，不丢进度。
- 对话一致性：`/minecraft/chat` 在 Worker 在线时把 Console/网页消息写入当前游戏会话文件（`session_id` 贯穿）；Console 状态渲染捕获 `game_session_id`，切换世界自动刷新历史与会话列表；历史轮询（5s）因此显示同一份对话（含游戏内新消息）。启动/重连时自动刷新会话列表。
- 回退回复可诊断：LLM 调用异常时回复明确说明"模型出问题+原因+任务未创建"，并把异常打印到后端控制台（`[conversation] LLM call failed`）；中文输入得到非中文回复时同样打印原始输出并本地兜底（不再静默"我听到了，主人。"）。
- Console 全面换用现代暗色 QSS 主题（按钮/输入框/列表/进度条/工具箱/表格统一风格）。

仍未实现/待注意：酿造验收的完整离线测试（药水 NBT 快照测试）与 `brew` 的计划器依赖链（玻璃瓶/下界疣/烈焰粉前置）仍按上文"酿造台"章节方案执行；`sleep` 的"雷暴可睡"等边缘规则未覆盖；Console QSS 未做高分屏缩放适配。
