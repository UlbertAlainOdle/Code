# Saya 全项目报告与接手指南

2026 年 11 月 18 日 · 面向第一次阅读仓库的人或 AI 编码代理

Saya 是一个以网页聊天、长期记忆和个人管理为核心的私用应用。它可以连接本地或外部模型，提供项目会话、打卡目标、联网研究、语音与 3D 陪伴，并可选接入 QQ、Anki 和 Minecraft。主要程序是根目录的 FastAPI 应用。浏览器、原生面板和外部渠道是交互入口，模型与工具服务提供能力，用户的长期状态由后端保存。

## 1. 第一次接手从哪里开始

1. 在仓库根目录读 `AGENTS.md`，用 `git status --short` 和 `git rev-parse HEAD` 确认源码位置和已有改动。当前分支为 `codex/qq-message-bridge`，基础提交为 `2940618db653a70e4fb4b3bef007c04758ccd4c8`。许多实际使用的文件和历史数据位于未提交工作区。需要复现本报告的版本时，应使用经核对的源码快照，单独确认所需外部资源和数据。
2. 要了解一个功能，先在下表找用户入口，再到路由看请求和响应、到服务看业务规则、到 `tests/` 找同类断言。重点读 `server5-22B----.py` 的装配、`saya_app/config.py` 的路径和 `core_services.py` 的共享对象。变更一个领域时先沿该领域调用链阅读，通常无需读完全部 QQ 历史。
3. 要试用，使用独立工作副本和空白或合成数据，给 `PROJECT_ROOT`、`DEAR_ROOT`、`SAYA_CHAT_MEMORY_DIR`、`SAYA_MMD_DIRECTORY` 指向隔离路径。从副本根运行 `python scripts/run_qq_backend.py --port 8000`。网页用 `http://127.0.0.1:8000/static/Chat4o5-Modern.html`，端口 `8000` 必须空闲。设置 `SAYA_STARTUP_MODEL=api` 可避免等待交互或装载 GGUF，隔离试用设置 `SAYA_QQ_ENABLED=false`，按需使用 `SAYA_SLU_CORPUS=0` 和 `SAYA_GAME_MEMORY=0`。分别检查推理服务、浏览器设置和后端实际生效配置。测试窗口结束后释放或关闭。
4. 要改动、部署或恢复，先把源码、配置、用户数据、运行环境和可选资源分别盘点。业务回归只在隔离副本运行，选与改动相关的测试。涉及 QQ 时先读最新交接和源码清单，原环境当前按记录保持手动停服。Spark 的布局、启动顺序和 PC 连接方式见部署参考。

[仓库协作规则](../AGENTS.md) · [启动说明](../readme.txt) · [入口源码](../server5-22B----.py) · [配置源码](../saya_app/config.py) · [DGX Spark 部署参考](saya-dgx-spark-deployment-reference-2026-11-18.md)

## 2. 功能地图

| 要使用或排查的功能 | 正常入口 | 第一批要看的文件和行为 |
| --- | --- | --- |
| 网页聊天、分支、历史、搜索会话、临时聊天 | 主页面，`POST /chat_stream`、`/api/sessions*` 与 `/api/history/*` | `static/Chat4o5-Modern.html`、`static/js/app.js`、主入口、`services/chat_runtime.py`。流式响应采用 SSE。`static/js/web_window.js` 与 `routers/web_window.py` 通过 WebSocket 管理同一后端的唯一主页面。 |
| 人设、提示词角色、设置 | 主页面设置、`/api/chat-agents`、`/api/saya-style/settings`、`/api/system_prompt` 和版本化本地设置接口 | `services/chat_agents.py`、`multi_agent_chat.py`、`static/js/local_settings.js`。自定义角色用独立提示词与自己的回合记录。Saya 私人记忆、心情与工具链有独立边界。 |
| 文本推理、视觉、思考和用量 | `/api/current_model`、`/api/model/switch`、`/chat_stream`、`/api/usage` | `saya_app/model_runtime.py`、主入口的 API 传输、`services/api_usage.py`、`api_thinking.py`、`api_failures.py`。API 模式连接 OpenAI 兼容 Chat Completions，GGUF 按需加载，视觉能力取决于模型和附件路径。 |
| 长期记忆、召回、候选、版本和遗忘 | 网页记忆管理、`/api/kb/*`、`/api/listener/*`、聊天确认卡 | `routers/memories.py`、`services/memory_v4/`、`memory_use.py`、`memory_revisions.py`、`memory_write_operations.py`、`*_writes.py`、`core_services.py`。V4 默认主读写，仍保留 JSON 与 Memory 3 兼容。索引故障可降级到词法检索，来源与生命周期规则继续生效。 |
| 项目、打卡、目标和每日简报 | `/api/projects`、`/api/checkins`、`/api/goals`、`/api/daily_brief` | 同名 `routers/`、`core_services.py`。项目绑定会话和项目记忆，打卡与人生目标有联动，简报可汇总任务和进度。 |
| 日记、学习和 Anki | `/api/journal`、`/api/learner`、`/api/anki` | `services/journal.py`、`learner.py`、`learner_writes.py`、`anki.py`。Anki 后端按配置的主机路径读取 `collection.anki2` 的 SQLite 快照生成统计，有些操作还更新 Saya 打卡。 |
| 联网搜索、抓取、引用和外部集成 | 网页联网模式，按需通过 QQ 使用，天气/翻译/视频推荐等工具入口 | `services/web_research/`、`services/local_api_profile.py`、`routers/integrations.py`、`routers/video_recommendations.py`。Brave 与 DeepSeek 各用独立凭据，选中的搜索提供商在 Web 与 QQ 共用。搜索、阅读、引用校验与最终模型生成是不同步骤。 |
| 陪伴、心情、主动提示与反思 | `/api/companion/*`、心情控件、聊天输出、日记页 | `services/conversation_mood.py`、`mood_continuity.py`、`proactive_runtime.py`、`echo.py`、`dream.py`。后台生命周期由应用管理，浏览器在线、用户确认和实际通知送达各有条件。 |
| 语音、图片、3D、动作 | `/api/tts/*`、`/tts`、`/api/upload_file`、`/get_vmd_files`、`/MMD/*` | `routers/tts.py`、`media.py`、`motion.py`、`services/local_tts.py`、`omni_tts.py`、`static/js/voice_settings.js`、`static/` 和仓库外 MMD。浏览器渲染和播放，后端可生成音频、保存上传、列出动作资源。 |
| 游戏与桌面 Companion Console | `/api/bridge/*`、`desktop/saya_companion_console.py` | `routers/bridge.py`、`services/conversation.py`、`minecraft_mineflayer.py`、`minecraft_planner.py` 和 `minecraft_worker/`。后端启动 Node Worker，通过子进程管道通讯，桌面面板用 HTTP/WebSocket 访问后端。命令目录与游戏数据需要版本一致。 |
| QQ 与原生审批 | OneBot 事件、`/api/qq/`、`static/qq*.html`、Windows 管理入口 | `routers/qq.py`、`services/qq_*.py`、`scripts/saya_qq_*_panel.py`。QQ 接收、去重、任务、事件、送达及原生操作各有状态和权限。此能力独立于网页核心的首期启动。 |
| 备份和运维 | `/api/backups`、独占运行入口、各能力状态接口 | `services/backup_manager.py`、`service_activity.py`、`scripts/run_qq_backend.py`、`qq_lifecycle.py`。通用备份范围、QQ 私有库、会话和附件需按功能核对。 |

表中简写的 `services/`、`routers/` 在 `saya_app/` 下。`saya_app/data/minecraft/` 是游戏注册表。`scripts/` 同时装有正式入口、迁移、嵌入与语音 Worker、训练、诊断及实机验收工具，按用途挑选。`mmd_try_showcase_server.py`、`start_mmd_try_showcase.bat` 和 `MOTION_TRIGGER_GUIDE.md` 属于展示或资源维护。`archive_old/`、`airi-main/` 是历史与参考目录。`Anki/` 和 `desktop/vendor/` 含已有 Windows 组件。`models/`、`.runtime/`、`logs/`、`uploaded_files/` 和 `.pytest*` 不能按源码目录处理。

[后端布局与 Minecraft 组装](backend-layout.md) · [提示词智能体](chat-agents.md) · [记忆默认链路](memory-default-runtime-2026-10-12.md) · [语音说明](local-qwen-tts.md) · [Mineflayer Worker](../minecraft_worker/README.md)

## 3. 正文、工具、状态如何流动

**用户入口 → 会话与权限 → 上下文与候选 → 模型生成 → 确认、持久化与展示。** 主机选择会话、角色、项目和检索范围，再按权限、时间、来源及预算组织上下文。模型提出候选和生成答复，主机负责选择可用证据、执行受控动作和持久化。需要批准的操作提交时要重新核对身份、目标版本和当前来源。对话回复、外部送达与实际写入是独立结果。临时聊天、召回关闭、停用及遗忘限制可进入模型的内容。直接将兼容 JSON 整库导入另一个 Agent 的索引会绕过这些规则。

| 状态归属 | 位置与迁移提示 |
| --- | --- |
| 对话和会话侧文件 | `SAYA_CHAT_MEMORY_DIR`，默认在仓库外 `DEAR_ROOT/Talk/memory/chats`。包含 JSONL 和部分会话侧文件。原消息及编号可能被记忆或项目引用。 |
| 记忆和个人管理 | `knowledge_base/` 的 `memory3.sqlite3`、`memory4.sqlite3`、兼容 JSON、版本和使用设置、候选与墓碑、项目、目标、打卡、日记和后台状态等。按实际功能保全同一时点的状态。 |
| 用户配置和浏览器草稿 | 权威后端配置及 profile/style 投影位于 `knowledge_base/`。浏览器的 `localStorage` 随浏览器配置和页面 Origin 保存，未同步草稿应从正确配置单独保全。浏览器设置清单登记 22 类，现有受管档案保留其真实值。 |
| 私密凭据和外部配置 | `knowledge_base/api_credentials.json`、`.env.qq` 及集成专属配置按权限另存。配置字段和状态可以用于排障，报告不应包含具体值。 |
| 媒体和模型 | `static/uploads/` 有前端可访问媒体，`uploaded_files/` 有其他附件。默认 MMD、聊天目录和部分 GGUF 也位于仓库外。模型权重和索引可以按版本再建，用户媒体需根据历史引用和私人授权处理。 |
| 可选集成 | `knowledge_base/qq/bridge.sqlite3` 等维持 QQ 事件和写入记录，Anki 原始卡库留在所配置的设备，`knowledge_base/.anki_snapshots` 是应用的快照工作区。游戏配置、语义环境、语音素材和运行锁分别随所选能力确认。 |

[路径定义](../saya_app/config.py) · [浏览器设置清单](browser-settings-inventory-2026-10-28.md) · [资源和生成物](cleanup-guide.md) · [读写与迁移规则](memory-default-runtime-2026-10-12.md)

## 4. 已知证据与如何验证下一处改动

| 范围 | 已有证据 | 面向下一次改动的验证入口 |
| --- | --- | --- |
| 网页与聊天 | 聊天体验记录包含实际页面检查、会话心情及取消等回归。模型响应在该批测试中使用模拟数据。 | `tests/test_chat_agents.py`、`test_api_async_hardening.py`、`test_web_window.py`、`web_window.test.cjs`。浏览器实测另核对单窗口、流式与取消、恢复旧会话。 |
| 记忆 | V4 默认启用有历史运行、索引和小语料证据。小语料只能证明特定检索能力，实际多年数据效果需要另验。 | `tests/test_memory_default_runtime.py`、`test_memory_revisions.py`、`test_memory_web_cas.py`。迁移应验旧条目、事实版本、来源权益、停用和备份恢复。 |
| 个人管理、联网和可选集成 | 有打卡与目标、Anki、学习、联网解析、语音及 Minecraft 的模块级测试。对接云端、音频听感和游戏连通性按实际用到的功能验证。DeepSeek Search/Fetch 有历史独立探针，Brave 的历史 HTTP 402 是用量限制。 | `tests/test_goal_checkin_links.py`、`test_anki.py`、`test_learner.py`、`test_web_research.py`、`test_web_citations.py`、`test_local_tts.py`、`test_minecraft_planner_executor.py`；Node Worker 另看 `minecraft_worker/test/`。 |
| QQ | 第 210 轮记录最终 463 项相关回归，12 条既有 FastAPI 警告，另有部分隔离桌面通过。已核对最新 866 项清单和 466 项冻结。原环境上次完整受管部署为 `203r4`，第 204–210 轮待上线且保持 `manual_stop`，QQ 安装绑定待复核，R5 按实施范围签收，R6 整体待验。原 24 小时连续门槛暂无签收，历史调整后的约 20.14 小时范围已审阅通过，最新台账留有 30 处缺口。 | 先读最新交接与对应阶段证据。只补受当前改动影响的验证。隔离桌面、模拟传输、实际模型和真实 QQ 消息分别记录。 |

测试举例均为仓库现有文件。只在隔离副本运行与任务对应的 `python -m pytest <测试文件> -q` 或 `node --test <测试文件>`。首轮浏览器实验应避免触发真实 QQ、生产库写入和未授权的付费请求。`docs/eval-00*` 至 `eval-07*` 是历史评估方案，阅读其结果时须区分验收要求、回归记录和实测结果。 [聊天体验记录](chat-experience-refresh.md) · [记忆评估](saya-memory-v4-evaluation-2026-09-14.md) · [评估索引](eval-00-index-and-rules.md) · [QQ 最新交接](qq-implementation-status.md) · [第 210 轮清单](qq-implementation-manifest-2026-11-15-session210.json)

## 5. 维护与交接边界

- **后端按单个状态所有者运行。** 主入口和 `core_services.py` 仍持有部分会话、模型锁及共享服务。要增加外围能力，优先从经过授权的接口接入。拆核心服务或增加 worker 需要验证现有并发、去重与权限合同。
- **前端代码的存放和展示执行是两件事。** PC 上的浏览器执行 UI、3D 和音频播放，当前服务统一提供 HTML、静态资源、HTTP API、SSE 和 WebSocket。迁移时优先保持前端和后端同源且部署版本匹配。PC 保留桌面程序时，单独验证其接口及主机路径。
- **以源码快照、私有状态与维护记录三份材料交接。** 已跟踪文件也可能是旧数据、日志或附件。发布包按允许清单生成，私有数据与密钥另移交，记录当前应用与推理版本、功能开关、路径、测试及恢复步骤。

[DGX Spark 部署参考](saya-dgx-spark-deployment-reference-2026-11-18.md) · [QQ 兼容与回归基线](qq-regression-baseline-2026-10-26.md)

本报告基于源码、清单和既有记录审阅。文中的启动与迁移步骤供未来目标环境使用，当前没有 Spark 实机记录。
