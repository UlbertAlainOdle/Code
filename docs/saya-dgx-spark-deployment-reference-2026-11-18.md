# DGX Spark 私人 Agent 部署与 PC 分工指南

2026 年 11 月 18 日 · 适用于当前 Saya 源码候选 · 面向首次部署的人或 AI 编码代理

**首选方案是 Spark 运行 Saya 后端和本地推理，PC 保留浏览器、原生工具和原工作区。** Spark 提供与后端配套的网页文件，PC 浏览器下载并执行界面，使用 PC 的屏幕、GPU 渲染和扬声器。PC 无需启动第二份 Saya。Spark 是使用中会话与记忆的唯一写入方，PC 关机后 Spark 上配置为常驻的能力仍可运行。Anki、游戏、语音和 QQ 按各自依赖接入。 [全项目报告与接手指南](saya-project-report-2026-11-18.md)

## 1. 先选择部署形态

| 形态 | PC 负责什么 | Spark 负责什么 | 适用条件 |
| --- | --- | --- | --- |
| A．后端常驻 Spark，PC 负责交互（推荐） | 浏览器与本地设备，按需启动 Windows 原生界面，保存原仓和离线快照 | Saya、同版本静态文件、选定记忆与业务数据、独立模型服务 | 私人 Agent 要在 PC 关机后保持后端在线。浏览器或外部设备相关能力随相应客户端在线。 |
| B．Saya 仍在 PC，Spark 只提供推理 | 保留原网页服务、数据库、3D 资源、Anki/QQ/游戏接入 | 仅运行独立 Chat Completions 服务 | 想先验证 Spark 推理或最大程度保留原 PC 使用方式。PC 下线后 Saya 随之下线。 |
| C．PC 自己提供 HTML 与静态文件 | 本地同源入口提供指定静态文件，并转发其他请求 | 后端、推理、数据和动态资源 | 必须将网页文件实际存放并由 PC 提供时采用。需要实现路由分流并验证协议，优先于把浏览器直接指向两个源的做法。 |

若将 Saya 作为另一私人 Agent 的组件，从 A 开始。为其他 Agent 设计受限的只读查询或按编号读取，服务器绑定身份、项目和作用域，返回有界正文及来源。需要写入时再接现有预览、确认、版本检查和回执。当前 `/chat_stream` 是带页面凭据的会话接口，可能持久化会话或改变后台状态。现有应用没有面向第三方 Agent 的独立最小权限网关，接入前需要设计并验证适配层。 [主入口](../server5-22B----.py) · [记忆读写边界](memory-default-runtime-2026-10-12.md)

## 2. 哪些文件留在 PC，哪些应放到 Spark

表中以方案 A 为准。PC 可继续保存完整源工作区和原始资产，Spark 上的发行复本按允许清单构建。停用某个功能时，仍要保留主入口直接导入的 Python 模块。

| 文件或资源 | PC 上留什么 | Spark 上放什么 |
| --- | --- | --- |
| `server5-22B----.py`、`core_services.py`、`saya_app/` | 原工作区作开发、历史留存或离线恢复 | 核心发行包，保留传递依赖与已选功能使用的数据文件。独立单 worker 运行。 |
| `scripts/run_qq_backend.py`、`saya_app/services/qq_lifecycle.py` | 可随原仓留档 | 可用作进程入口，提供固定工作目录、回环监听和独占锁。Linux 的锁分支使用 `flock`。入口名称带 QQ，网页服务可在 QQ 关闭时试跑。 |
| `static/Chat4o5-Modern.html`、`static/{js,css,icons,libs,config}/`、`static/browser_settings_inventory.json` 及保留功能引用的子页面 | 同版本源码备查。浏览器及其配置、本地草稿和设备留在 PC | 推荐由 Spark 的 `/static` 提供网页程序，含配套 JS、CSS 和使用到的资源。根据页面资源引用决定 `skins/`、`env/`、`skybox/` 等资产，避免遗漏或混用浏览器缓存中的旧版本。 |
| 仓库外 `MMD`、`MOTION_TRIGGER_GUIDE.md`、`mmd_try_showcase_server.py`、`start_mmd_try_showcase.bat` | 保留完整原始 MMD、动作试验项目和 Windows 展示入口 | 为 `/MMD` 提供已存在的目录。仅验证 API 或文本时可用空目录，标准网页仍可能报告 3D 资源缺失。需要 3D 时只部署已获使用许可的模型、动作及贴图，包括页面引用的同目录依赖。 |
| `knowledge_base/` 与仓库外会话目录 | 原数据只读备份、未迁移的个人资料 | 独立可写数据卷，只迁获准使用的状态。包含适用的 `memory3.sqlite3`、`memory4.sqlite3`、`knowledge.json`、版本、候选、归档、使用设置及删除账本，以及所选打卡、项目、目标、日记、心情和陪伴状态。对话 JSONL 与会话附属文件挂到 `SAYA_CHAT_MEMORY_DIR`。 |
| 浏览器档案与 `knowledge_base/saya/`、profile/style 文件 | 正确浏览器配置和未保存草稿留在 PC，原状态留档 | 保全确认过的 `knowledge_base/browser_settings.json`、有效人设和风格投影。继续 `runtime_enabled=true`。迁移后核对确切值，使用现有版本化接口处理差异。 |
| `models/`、仓库外 GGUF、`.runtime/embedding-model` 与 Worker | 留下原本地模型及缓存，是否清理由拥有者决定 | 在独立推理环境准备选中的权重和版本。需本地语义召回时部署 `scripts/embedding_worker.py`，重建 ARM64 环境与索引。需本地语音时另部署 `scripts/qwen_tts_worker.py` 和匹配资源。安装脚本需按目标环境核对。 |
| `static/uploads/`、`uploaded_files/`、TTS 生成物 | 留存原始文件、未获准迁移的私人附件 | 仅复制被保留历史引用且获准转移的内容，核对 URL 和读权限。`static/uploads/` 由静态服务提供。新上传可用独立数据卷承载，旧语音缓存仅在有保留需求时迁移。 |
| `desktop/saya_companion_console.py`、`desktop/vendor/` | 保留 Minecraft Qt 面板和 Windows PySide6 依赖 | 通常只需面板依赖的后端路由。游戏 Worker 可选安装。网页上的“打开桌面控制台”会在后端主机启动子进程，Spark 无桌面时无需部署此启动能力。 |
| `minecraft_worker/` 与 `saya_app/data/minecraft/` | 游戏客户端与 Qt 面板。游戏服务器按原位置保留或独立运维 | 用到自动游戏时，将 Python 游戏服务、Node Worker 代码、命令目录、依赖锁和匹配注册表一起部署，在 ARM64 重装 Node 依赖。Worker 由后端以管道启动，应与后端同机部署。 |
| `Anki/`、PC 的 Anki 用户目录、`knowledge_base/.anki_snapshots` | 保留 Anki 应用和卡库主副本，按需在 PC 生成一致的只读快照 | 需要统计时，只提供经过授权的快照和 Spark 上可访问的路径。先重配 `anki_settings.json` 对应的路径，历史快照缓存留档即可。 |
| `start_*.bat`、Windows 管理脚本、`scripts/saya_qq_*_panel.py`、QQ/NapCat 绑定 | 留在 Windows。保留原有监督、手动启动和安装绑定选择 | 未选 QQ 时关掉接入。选 QQ 时才迁对应桥接库、来源、角色与配置。需要使用原生审批时，按下文单独验证 PC 面板与 Spark 的协议。 |
| `.env.qq`、`knowledge_base/api_credentials.json`、服务密钥和 Cookie | 原配置私有保全，不因迁移覆盖浏览器草稿 | 仅通过权限受限的配置提供所选功能需要的凭据。模型、搜索、视频、翻译、TTS 各自提供。保留 Brave 和 DeepSeek 选择与密钥独立性，禁止隐式回退，严格 SafeSearch 不受支持时显式失败。 |
| `requirements.txt`、`pytest.ini`、`tests/`、迁移与验收脚本 | 原依赖定义、全量测试和维护源码 | 生产按实际业务导入生成 ARM64 依赖锁，`httpx` 是运行依赖。API 文本首期可不装 `llama-cpp-python`、`pyside6`。测试、发信工具、训练和修复工具使用隔离环境。 |
| `.runtime/` 的旧环境与锁、`logs/`、`.pytest*`、`__pycache__/`、`.idea/`、`.git/`、`airi-main/`、`archive_old/`、训练/诊断材料 | 用于原机复现、维护或归档。历史观察台账及私有取证单独保存 | 生产包排除这些生成物和旁路目录。目标机新建自己的 `.runtime/`、进程登记、环境与轮转日志。需要重建模型环境时从安全来源按版本安装。 |
| `AGENTS.md`、`readme.txt`、`docs/` | 保留完整接手资料 | 给运维另备本指南、快照标识、所选文件哈希、依赖锁及相关兼容说明。源码快照依据最新清单，基础 Git 提交和整目录复制都不足以准确构建当前发行版。 |

[路径配置源码](../saya_app/config.py) · [资源和生成物](cleanup-guide.md) · [浏览器设置登记](browser-settings-inventory-2026-10-28.md) · [第 210 轮清单](qq-implementation-manifest-2026-11-15-session210.json)

## 3. 让 PC 前端连到 Spark

### A．PC 浏览器打开 Spark 提供的同源页面

先让 Spark 的 Saya 监听 `127.0.0.1:8000`。PC 端确认原应用未占用本机 `8000`，再在 PowerShell 建立转发。将 `user@spark-host` 改为经核实的 SSH 账户和主机。转发仅监听 PC 回环地址，WebSocket、SSE、静态资源和普通 HTTP 都经过同一条连接。限可信用户掌握该 SSH 入口，因为它可以访问本机管理能力。 [OpenSSH `-L` 文档](https://man.openbsd.org/ssh#-L)

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -L 127.0.0.1:8000:127.0.0.1:8000 user@spark-host
```

在原先使用的 Chrome 配置打开 `http://127.0.0.1:8000/static/Chat4o5-Modern.html`。原来使用 `localhost` 的配置应核对实际 Origin，再保全该配置下的草稿。相同的本地 Origin 可能继续使用原来保存的值，切换前先留档并检查页面从哪台机器加载、当前 API 设置的有效值及所连数据，确认后才发送消息。一个后端仅有一个活跃主页面。若遇占用，保留现有草稿并通过原页面释放或关闭后重试。

保留 PC 侧 `8000` 是当前版本的兼容条件。`static/js/app.js` 会在页面端口不是 `8000` 时将以 `/` 开头的 `fetch` 请求改发到 `http://127.0.0.1:8000`，窗口 WebSocket 却使用页面自己的 Host 与端口。把 PC 侧转发改成 `18000` 可能把页面和 API 分到两个后端。HTTP 的回环、Host、Origin 检查，WebSocket 对应检查和页面令牌也需要保留。若 `8000` 被占用，应先判断所属应用并安排端口，或做完整的同源改造。 [网页请求重写](../static/js/app.js) · [主页面 WebSocket](../static/js/web_window.js) · [后端窗口检查](../saya_app/routers/web_window.py)

验收按顺序看静态 JS/CSS、`/api/web-window/connect` 接受窗口、`/api/current_model` 读取目标实例、`POST /chat_stream` 返回增量 SSE、浏览器取消与重连、会话重开回读。然后才验选用的上传、语音与 `/MMD`。关闭浏览器、断开 SSH 或关闭 PC 时，应验证 Spark 进程和不依赖客户端的任务继续工作。只检查页面能显示标题或 `curl /` 成功无法覆盖上述链路。

### B．PC 保留原 Saya，Spark 只提供模型接口

PC 使用原后端与前端，所有会话、小组件、Anki 文件路径和数据库仍由 PC 管理。假设 Spark 模型服务监听其回环 `8001`，可另将 PC 的空闲 `18080` 转发到该端口，在 **PC 后端所用** 模型配置中填入 `http://127.0.0.1:18080/v1` 与实际模型名、非空匹配凭据。保持页面入口为 PC 本机 `8000`。Spark 只接收实际发送给模型的提示词和附件，若需要数据留在 PC，应核对这些内容的出站边界。此模式对验证 ARM64 推理有用，无法让 PC 上的 Saya 在关机后继续处理。 [API 模式路径拼接](../server5-22B----.py)

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -L 127.0.0.1:18080:127.0.0.1:8001 user@spark-host
```

### C．必须由 PC 提供静态网页文件时

可在 PC 运行一个仅面向回环的同源入口 `127.0.0.1:8000`，用另一空闲端口作 SSH 转发的上游。PC 入口只直接提供已列出的静态 HTML、JS、CSS 和选定资源。其余路径逐条映射到 Spark，包括 `/api/*`、`/chat_stream`、`/reset`、`/set_emotion`、`/tts`、`/get_vmd_files`、需要由 Spark 提供的 `/MMD/*` 和动态 `/static/uploads/*`。保留上传、取消、超时和音频读取行为。网关须保持浏览器所见 Origin，并与后端的 Host/Origin 校验一致，支持 WebSocket Upgrade，避免 SSE 缓冲。旧方式下从文件系统双击 HTML、用 Live Server 开跨端口页面或直接对 LAN 地址访问 API，无法完整覆盖当前的主窗口协定和管理检查。

若 MMD 大文件坚持只留 PC，可让 PC 网关同源处理对应 `/MMD/*`，同时盘点 `media.py` 中在服务器文件系统上枚举动作的路由。Spark 要有一致目录，或需新增并验证可用资源目录适配。仅让静态 URL 能下载文件会造成下拉列表和实际文件不同步。PC 与 Spark 发布版应绑定同一前端版本，网关方案验收通过后再供日常使用。 [静态挂载与入口](../server5-22B----.py) · [动作资源枚举](../saya_app/routers/media.py)

## 4. PC 保留功能怎样继续工作

| PC 侧能力 | 连接方式与迁移限制 |
| --- | --- |
| 浏览器外观、3D、音频与草稿 | PC 运行渲染与播放，Spark 返回资源、音频及回复。清点本机 Origin 和正确浏览器配置，确认过的后端档案按版本同步。先对照草稿和实际值，避免用空配置或自动化标签页的默认值回写。 |
| TTS、视觉和语音服务 | 文本首期可以不启用。浏览器通过 `/api/tts/*` 或 `/tts` 请求后端，浏览器的扬声器与哪台机器负责合成是独立的选择。将模型放 Spark 时核对资源峰值。若坚持让 PC 合成，应提供与现有集成兼容的受限接口，单独验证超时、音频流和 PC 离线降级。Windows 的 WSL 和退出守护按原机保留。 |
| Anki 卡组 | 现有后端读取其文件系统可见的 `collection.anki2` 并生成 SQLite 快照。Spark 上的页面输入 `C:\...` 无法直接访问 PC 的库。若开启 Spark 统计，PC 可在一致性窗口生成副本，经私有传输定期更新 Spark 的只读快照，目标以本机路径配置；为脱机快照记录时间。用 SQLite 支持的备份方式处理活库，避免通过文件共享直接访问 PC 正在写入的集合。需要实时数据则另行实现 PC 侧适配。 |
| Minecraft 与原生面板 | 游戏客户端和 Qt 界面留 PC。若 Worker 迁 Spark，Spark 需能连接游戏服务器。PC 在转发建立后可运行 `python desktop/saya_companion_console.py http://127.0.0.1:8000`，通过 `/api/bridge/minecraft/*` 与目标后端通讯，单独确认状态 WebSocket、目录和命令。启动 PC 面板应在 PC 执行，网页的服务器侧启动按钮不会在 PC 弹出窗口。若游戏服务仅 PC 在线时可用，常驻核心需容忍游戏离线。 |
| QQ 与原生审批 | 后续可保留 PC 专用 QQ/NapCat，只让 Spark 承担唯一桥接业务，或者单独验证兼容的外部网关。需同时设计 **PC OneBot → Spark 事件入口** 和 **Spark → PC OneBot 动作接口**，包含签名、身份、允许列表、可靠送达和状态核对。浏览网页的 `-L` 隧道只解决 PC 发起的浏览器连接。原 Windows 启停面板操作本机进程；原生审批面板的 HTTP 连接能否沿隧道使用，需与目标版本分开验证，不能把它视作 Spark 的启停器。PC 关机时本机 QQ 接入会不可用。 |
| 天气、搜索、视频、翻译与学习资料 | 这些请求由所选后端和相应服务发起。先核对 Spark 的出口可达性、适用的代理、附件上传策略和各集成密钥；只迁所启用集成的配置。网页使用能正常联网的 PC，并不证明 Spark 能访问同一供应商。 |

[Anki 文件读取实现](../saya_app/services/anki.py) · [PC 原生游戏面板](../desktop/saya_companion_console.py) · [游戏面板的后端启动](../saya_app/routers/bridge.py) · [语音退出与资源](tts-lifecycle-and-local-models.md) · [Windows QQ 管理说明](qq-backend-control.md)

## 5. Spark 首次实施和验收顺序

1. **确定工作副本与依赖。** Spark 采用 Arm CPU、Blackwell GPU 和 128 GB CPU/GPU 统一内存，依赖与容器用 Linux ARM64。推理与应用独立，可先选 NVIDIA 的单机 vLLM 配方，复用 GGUF 时也可评估独立 `llama.cpp`。固定模型名、权重、上下文和依赖版本。以实际中文回答、流式、工具结构化响应和并发决定配置，按统一内存给系统、KV 缓存、嵌入和可选 TTS 留空间。 [NVIDIA 硬件](https://docs.nvidia.com/dgx/dgx-spark/hardware.html) · [软件栈](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/latest/porting/software-requirements.html) · [单机推理配方](https://build.nvidia.com/spark/vllm/choose-recipe) · [推理选项](https://build.nvidia.com/spark)
2. **用空库试跑。** 从已核验源码快照发一份独立发行副本。显式设 `DEAR_ROOT`、`PROJECT_ROOT`、`SAYA_CHAT_MEMORY_DIR`、`SAYA_MMD_DIRECTORY`，最后一个目录需存在。`knowledge_base/` 在 `PROJECT_ROOT` 下，给它独立可写卷，给运行入口的 `.runtime/` 独立可写位置。设置 `SAYA_STARTUP_MODEL=api`、`SAYA_QQ_ENABLED=false`、`SAYA_SLU_CORPUS=0`、`SAYA_GAME_MEMORY=0`。用 `python scripts/run_qq_backend.py --port 8000` 单实例运行。设实际 `API_URL`、`API_MODEL` 和受管凭据，例如模型服务的 Chat Completions 在 `/v1/chat/completions` 时填 `/v1` Base URL。显式核对网页草稿或本地配置没有指向别的模型服务。未选的周期写入、外联、语音和游戏操作保持关闭。
3. **先做跨机验证，再迁正式状态。** 用上文 PC 连接方式验证合成样本的流式、取消、短时断连、会话重开、临时会话、记忆来源、确认、纠错、停用和备份恢复。测试项目、目标、打卡和附件时保留同一会话的引用完整性。启用语义索引时在 ARM64 重建 Worker，读 `/api/kb/embeddings/status` 判断实际状态。模拟响应、真实模型、浏览器实际操作和真实外部送达分开记录。原 PC 继续按现有选择保持手动停服。
4. **切换数据所有权。** 先在旧端停写并记录快照时点，对 SQLite、JSON、JSONL 和必要附件做一致性备份。能安全取得 SQLite 一致副本时可使用备份 API；多个库及 JSON/JSONL 的跨库一致性仍需要应用静止窗口。核对各库完整性、记录数、编号、来源引用、版本链、权限和删除账本；按需迁移浏览器档案和既有业务配置。隔离验证后只让 Spark 写入正式数据。不要双向同步运行中的库或挂同一数据目录给两个后端。
5. **设置长期运行。** 可用 `systemd` 运行低权限的单实例 Saya，推理可用独立服务或 GPU 容器。明确启动次序、就绪信号、限速、崩溃后的有界重试和正常排空。SSH 登录与 PC 界面断开不应停止常驻服务，人工停机应保持停止。开机自启由主人在目标机决定。只开放所需受控入口。 [NVIDIA 容器运行时](https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html)
6. **观察新环境 24 小时并逐步启用外围。** 监控应用、模型、实际请求和数据健康，记录延迟、内存、温度、盘容量、失败率、费用、采样缺口和恢复耗时。使用 `/api/kb/health` 与 `/api/kb/v4/status` 辅助诊断，配合代表性的网页和模型请求。验证时区、午夜与任务恢复，按需要启用 Anki、游戏、TTS 或 QQ。QQ 沿最新交接保持顺序和兼容约束，确保单活、未知送达不重发。旧台账及采样缺口继续留存，Spark 连续运行记录单独从实际启动验收起算。 [QQ 实施交接](qq-implementation-status.md) · [回归边界](qq-regression-baseline-2026-10-26.md)

## 6. 初次排障与回退

| 现象 | 先查哪里 |
| --- | --- |
| PC 的 `8000` 无法监听，或显示旧会话 | 查端口实际归属和 SSH 转发，明确是否误连旧 PC 实例。保全会话和草稿后再作切换。 |
| 页面打开但不能聊天或设置不显示 | 查页面 Origin、`/api/web-window/connect`、唯一页面占用、实际 `fetch` 目标、后端是否就绪。仅在反代场景继续查 WebSocket、Host 和 SSE 缓冲。 |
| 能显示普通文本，3D、语音或附件失败 | 对照页面请求路径与文件来源，核对 `/MMD`、主机目录、`/api/tts/*`、Worklet、动态上传和相关插件是否启用。 |
| 只有模型能力失败或用错模型 | 从 Spark 的应用进程侧验证模型 Base URL、端口、模型名及有效凭据，再看页面设置与受管版本。浏览器网络通不表示服务端能调用该模型。 |
| Spark 出错时需回到 PC | 先暂停 Spark 写入，保全切换后的新会话、记忆及未决操作。恢复上一发行版前检查新库与旧代码兼容，必要时先用副本演练。核对编号、墓碑及来源后再恢复单个写入方。停止 PC 上的网页转发后才恢复原本地 `8000`，防止误连。若有 QQ，额外核对发送状态后再恢复接收。 |

首期交付标准是 PC 浏览器能可靠访问 Spark，PC 离线不影响 Spark 上已启用的核心服务，文本和记忆经过恢复验证且有自己的 24 小时连续记录。本文件描述源码可支持的路径和需要适配的边界，尚无本项目在 Spark 或通过 PC→Spark 隧道实测的记录。
