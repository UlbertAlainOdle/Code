# QQ 自动回复接入与功能报告

日期：2026-10-24。功能分支：`codex/qq-message-bridge`。

2026-10-27新增：[配置共享与服务运维](qq-configuration-and-service.md)、[本轮实施报告](qq-implementation-2026-10-27.md)、[后续工作入口](qq-implementation-status.md)。配置、单实例守护及健康基础已交付，后续阶段和完整验收仍按进度文件区分。

同日第二次继续：[NapCat守护、在线观察、网页排空与自启动验收报告](qq-implementation-2026-10-27-session2.md)。该轮源码清单与历史记录保留。

同日第三次继续：[重启恢复、离线登录诊断、隔离回退与双网页冲突验收](qq-implementation-2026-10-27-session3.md)，保留历史证据。

同日第四次继续：[本机API配置优先、独立浏览器草稿、缓存同步保护与真实命令冲突](qq-implementation-2026-10-27-session4.md)。Brave已导入，但真实请求遇服务用量限制，仍按严格串行补前两阶段门禁。

2026-10-28第五轮历史记录：[唯一主页面、完整浏览器设置存档与正确Chrome导入确认](qq-implementation-2026-10-28-session5.md)。该轮以session5清单为准，当时只存档；后续统一读取已在第六轮完成，当前行为以下方第七轮及进度入口为准。

2026-10-28第六/七轮：[本地统一配置补记](qq-implementation-2026-10-28-session6.md)、[DeepSeek独立Search/Fetch与厂家切换](qq-implementation-2026-10-28-session7.md)。本地统一读取已启用，正确Chrome的16项值保留；当前源码以session7清单为准。DeepSeek已选为搜索厂家，真实搜索/抓取通过，Brave原密钥保留；QQ新搜索的实际收发另按回执验收，原严格阶段顺序不变。

2026-10-26 后续建设资料：[八项实施编排](qq-roadmap-2026-10-26.md)、[验收矩阵与交付记录](qq-acceptance-2026-10-26.md)、[当前回归基线](qq-regression-baseline-2026-10-26.md)。以下方案包含早期阶段描述，当前行为与后续未实现能力以基线和编排文档区分。

更新：主人身份识别、主人邀请自动入群、邀请审核和持久化群回复管理已补齐，完整说明见 [主人与群接入更新](qq-owner-groups-2026-10-24.md)。主人身份不再依赖共享记忆开关。

网页设置入口：主界面左下角 **Settings → QQ 接入**，或 **Tools → QQ**。现支持开关与参数即时应用、群授权管理、入群邀请审核和连接检查，见 [网页 QQ 设置说明](qq-web-settings-2026-10-24.md)。

## 接入方案

在现有 Saya+ FastAPI 项目中新增 OneBot 11 HTTP 渠道，复用已有 `/chat_stream` 的模型生成、API 凭据解析、输出清理和用量记录，不另建一套 AI 服务。推荐使用独立 QQ 账号运行 NapCat：QQ 消息由 NapCat 上报，Saya 异步生成文本，再调用 OneBot 的发送接口。

依据 [NapCat 配置文档](https://napneko.github.io/config/basic)、[NapCat HTTP 上报实现](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/network/http-client.ts)、[OneBot HTTP POST 规范](https://github.com/botuniverse/onebot-11/blob/master/communication/http-post.md) 和 [公开 API](https://github.com/botuniverse/onebot-11/blob/master/api/public.md) 实现。特别注意：NapCat HTTP 客户端配置中的 `token` 是上报签名密钥，对应 Saya 的 `SAYA_QQ_WEBHOOK_SECRET`，而非 Bearer Token。

```text
QQ 好友 / QQ 群 @
  → NapCat HTTP 客户端（HMAC 签名）
  → POST /api/qq/events（验证、过滤、持久化、立即应答）
  → 单消费者 FIFO 队列
  → 当前本地模型 / 已配置 API 模型
  → NapCat HTTP 服务端（Bearer Token）
  → QQ 文本回复
```

## 已实现的功能

| 功能 | 行为 |
| --- | --- |
| 好友私聊 | 只接受白名单用户与主人 ID 的 `friend` 私聊；拒绝群临时会话 |
| 群聊 | 只接受白名单群；默认结构化 @机器人时触发；同群按发言人隔离 |
| 模型 | `auto` 跟随服务器当前模型；也可指定 `api` 或项目中的本地模型键 |
| API 凭据 | 沿用服务端环境变量及已有会话服务凭据，不从 QQ 文本接收密钥 |
| 连续对话 | 每会话保留最近 12 轮，可配置；QQ 重启后可继续 |
| QQ 命令 | `/help`、`/whoami`、`/status`、`/memory`、`/reset`，以及主人私聊群管理命令 |
| 群邀请 | 可选自动接受主人邀请；其他邀请待审核；入群成功后开启 @回复 |
| 群管理 | `/groups`、`/requests`、`/group allow/block 群号`、`/approve`、`/reject`，开关持久化 |
| 共通记忆 | 默认关闭；仅主人好友私聊可显式开启，沿用项目现有读写规则 |
| 权限隔离 | 主人好友私聊可按设置使用记忆与搜索工具；群聊及其他联系人隔离个人记忆，游戏与外部连接器执行仍不开放 |
| 鉴权 | 原始字节 HMAC-SHA1；或其他 OneBot 实现的 Bearer 上报模式；配置 HMAC 时不可被 Bearer 降级 |
| 防误触发 | 过滤自己发送的消息、非消息事件、匿名群消息、过期事件、图片/语音等非文本消息 |
| 消息处理 | 持久化去重、容量限制、冷却限流、异步回复、生成超时、文本截断和分段 |
| 输出协议 | OneBot 数组 text 段发送，模型生成的 CQ 码作为普通文字，不触发 QQ 操作 |
| 恢复 | 未执行且未过期的队列消息重启后恢复；生成中断、发送结果不明的消息不自动重放 |
| 诊断 | 状态页、内容无关错误代码、事件/成功回复时间、处理计数、受管理 Token 保护的只读账号匹配检查 |

## 快速启动

1. 在现有 Python 环境安装项目依赖。新增功能只使用项目已依赖的 FastAPI、Pydantic、httpx 与标准库，不引入 QQ SDK。
2. 安装 NapCat 并登录用于回复的 QQ 账号。扫码、设备验证等由 NapCat/QQ 完成，本项目不收集 QQ 密码，也不自动安装或修改 QQ。
3. 配置 NapCat HTTP 服务端：
   - 启用；地址 `127.0.0.1`；端口 `3000`；消息格式 `array`。
   - 设置强随机 Token，填入 `SAYA_QQ_ACCESS_TOKEN`。
4. 配置 NapCat HTTP 客户端：
   - 启用；上报地址 `http://127.0.0.1:8000/api/qq/events`。
   - 消息格式 `array`；关闭 `reportSelfMessage`。
   - 设置另一个强随机 Token，填入 `SAYA_QQ_WEBHOOK_SECRET`。
5. 将项目根目录 `.env.qq.example` 复制为 `.env.qq`，填写真实 QQ 号、白名单和 Token。可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成独立随机值。
6. 在项目根目录运行：

   ```powershell
   Copy-Item .env.qq.example .env.qq
   # 编辑 .env.qq 后再启动，示例 Token 会被启动脚本拒绝。
   .\scripts\start_qq.ps1
   # 可选：指定现有 Python 环境或另一个端口。
   .\scripts\start_qq.ps1 -Python .\.runtime\memory-python\Scripts\python.exe -Port 8001
   ```

   使用其他端口时，也要修改 NapCat 上报地址。`.env.qq` 是配置数据，不作为 PowerShell 脚本执行；只在本次启动进程中生效。应用不会自行加载 `.env.qq`，直接用 uvicorn 启动时须在启动环境中设置变量。

7. 从主页面 **Tools → QQ 接入** 打开 `/static/qq.html`，查看状态；用 `SAYA_QQ_ADMIN_TOKEN` 执行连接检查。该检查不发送 QQ 消息。
8. 白名单好友先发送 `/help`，再发送文字；白名单群成员 @机器人后发送文字。`/memory` 可核对当前会话记忆模式。

示例 NapCat 网络配置片段（合并到其现有网络配置中，不要覆盖整个配置）：

```json
{
  "httpServers": [{
    "name": "saya-api", "enable": true, "host": "127.0.0.1", "port": 3000,
    "messagePostFormat": "array", "token": "与 SAYA_QQ_ACCESS_TOKEN 相同"
  }],
  "httpClients": [{
    "name": "saya-events", "enable": true,
    "url": "http://127.0.0.1:8000/api/qq/events",
    "messagePostFormat": "array", "reportSelfMessage": false,
    "token": "与 SAYA_QQ_WEBHOOK_SECRET 相同"
  }]
}
```

## 共通记忆库的实际边界

| 会话 | 网页长期记忆 | 日记/项目等私人上下文 | 聊天历史 |
| --- | --- | --- | --- |
| 普通好友私聊 | 不读不写 | 不注入 | 独立 QQ 上下文 |
| 群内普通成员 | 不读不写 | 不注入 | 群 + 发言人独立 |
| 群内主人 | 不读不写 | 不注入 | 同样隔离 |
| 主人好友私聊，未开启共享 | 不读不写 | 不注入 | 独立 QQ 上下文 |
| 主人好友私聊，开启共享 | 沿用网页记忆库 | 沿用普通 Saya 私人上下文；项目专用资料仍需项目作用域 | QQ 专属持久会话，不自动拼接网页历史 |

开启共享需同时设置：

```dotenv
SAYA_QQ_OWNER_IDS=你自己的QQ号
SAYA_QQ_SHARED_MEMORY=true
```

主人 ID 是管理员配置的授权映射，不会根据昵称或消息中“我是主人”等声明自动识别。设置多个主人 ID 表示这些账号都被授权使用同一个网页个人记忆库；不是多个独立用户的记忆空间。修改环境配置后需重启。

共享模式调用当前 `kb_system`，而不是复制出新知识库。项目当前已把 V4 主读写、版本、证据和默认语义召回纳入主链路，见 [默认记忆运行说明](memory-default-runtime-2026-10-12.md)。QQ 共享仍受实际运行设置、语义模型可用性、作用域、相关性、敏感信息规则和候选审核规则约束；“请记住”不代表任何内容都能立即保存，也不保证每轮都召回全部记忆。

QQ 仅发送最终文本，不展示网页记忆候选卡、冲突复核卡和审核按钮。需要确认、纠正、删除或审核的记忆请回网页处理。本期不开放 QQ 内全部记忆导出、切换身份或管理操作。

隔离使用既有临时聊天隐私路径，但由 QQ 单独保存近期会话文本。共同使用的仍有服务器模型、基础系统提示词和角色设定；角色设定文件中不应放个人秘密。隔离不是独立进程、独立模型或独立的私人角色档案。共享开关切换前后的 QQ 上下文分区存储，防止以前的私人回答重新进入隔离会话。

`/reset` 只重置当前 QQ 发言人的对话上下文与该渠道会话绑定，不删除长期记忆，也不删除已存在的网页 JSONL 会话。主人共享会话会作为正常会话保存在网页历史目录，可回网页查看。

## 默认配置与容量

所有变量以 `SAYA_QQ_` 开头，完整示例见 `.env.qq.example`。

| 变量后缀 | 默认值 / 说明 |
| --- | --- |
| `ENABLED` | `false`；关闭时无 QQ 数据库、客户端或消费者 |
| `BOT_ID` | 必填，用于验证事件来源账号 |
| `API_URL` | `http://127.0.0.1:3000` |
| `ACCESS_TOKEN` | 启用时必填；OneBot HTTP 服务端 Token |
| `WEBHOOK_SECRET` | NapCat HTTP 客户端签名 Token；与 `WEBHOOK_TOKEN` 至少设置一个 |
| `WEBHOOK_TOKEN` | 可选 Bearer 上报鉴权，仅用于支持该模式的其他实现 |
| `ADMIN_TOKEN` | 可选；未配置则连接诊断接口不可用 |
| `ALLOWED_USERS` / `ALLOWED_GROUPS` | 空白名单拒绝相应渠道；支持逗号或空格分隔数字 ID |
| `OWNER_IDS` / `SHARED_MEMORY` | 空 / `false`；主人自动具备好友私聊接入资格 |
| `GROUP_MENTION_ONLY` | `true`；关闭后会回复白名单群所有可接受文字，仍按发言人隔离 |
| `AUTO_ACCEPT_OWNER_INVITES` | `false`；开启后只自动同意主人发出的机器人入群邀请并开启该群回复 |
| `OWNER_GROUP_AUTO_ENABLE` | `false`；开启后主人在新群首次明确 @机器人会授权该群，兼容 QQ 直接入群；不能覆盖手动关闭 |
| `MODEL` | `auto`；跟随当前服务器模型，不读取浏览器尚未提交的选择 |
| `COOLDOWN_SECONDS` | `3`；同一会话冷却期新消息被忽略，避免回复风暴 |
| `QUEUE_SIZE` | `32`；包含排队和正在处理的消息 |
| `MAX_EVENT_AGE` | `300` 秒；入站事件新鲜度及队列等待上限 |
| `REPLY_TIMEOUT` | `180` 秒；生成等待上限，不包括各段发送时间 |
| `HISTORY_TURNS` | `12` 轮；还受模型上下文预算限制 |
| `RETENTION_DAYS` | `7` 天；QQ 历史及终态去重记录的逻辑保留期 |
| `CHUNK_CHARS` / `MAX_REPLY_CHARS` | `1500` / `6000` 字符；超长回答截断提示 |

## 数据、恢复与运行限制

- QQ 数据位置：`knowledge_base/qq/bridge.sqlite3`。`.env.qq` 与该目录已加入 `.gitignore`。环境变量里的 Token 不会写进 QQ 数据库。
- 已完成、失败或结果不明的队列记录立即移除消息正文，只保留去重键、时间、处理状态与错误代码。QQ 数字身份仍会出现在去重键和会话作用域中。
- 队列等待中的正文和最近会话历史是本地明文 SQLite。历史最近活动时间超过保留期后清理；启动和有流量时每小时执行一次。活跃会话仅保留最近 N 轮，因此保留期是会话空闲期限，不是逐条消息的硬删除时间。SQLite 逻辑删除不等于磁盘安全擦除。
- QQ 数据库本期不纳入现有网页一键备份/恢复；如需备份，在停止 Saya 后复制整个 `knowledge_base/qq` 目录。主人共享写入的长期记忆仍走原记忆备份路径，网页会话走原会话备份路径。
- 单应用进程、单消费者按顺序生成，避免并发串话；暂不支持多 worker、多实例和跨机器共享队列。网页与 QQ 仍共用原模型锁、显存、API 配额和计费。繁忙时 QQ 可能排队或过期；低延迟场景可使用单独部署或 API 模型。
- 已验证的入站消息先持久化再 ACK；ACK 不表示 QQ 发送成功。发送阶段网络中断或进程退出可能已送达也可能未送达，记为 `delivery_unknown`，不会自动重发，避免双重回复。没有实现端到端 exactly-once。
- 队列满返回 503 + Retry-After，但实际重投能力取决于上游实现；不能保证 NapCat 会自动重投。入站冷却限流返回成功且标记忽略，不积压。QQ 平台限频、禁言、账号离线和风控无法由本项目绕过。
- 主人共享模式复用原流程，可能先完成网页历史/记忆写入，再发送 QQ；如果发送失败，共享写入可能已经完成。请勿把 QQ 到达回执当作记忆事务提交结果。
- 本地模型的底层推理取消能力受现有运行时影响；超时会取消异步等待，但不能保证立刻中止底层 C/CUDA 计算。
- 本期只实现 OneBot HTTP 上报 + HTTP API，没有反向 WebSocket、QQ 官方 AppID/AppSecret 接口、手机端自动化、离线补全历史、图片理解、语音收发、文件解析或主动定时推送。群管理限于机器人入群邀请审核与回复启停，不提供踢人、禁言等群成员管理。
- NapCat 是第三方 NTQQ 接入方案，不是腾讯官方机器人 API。需要持续运行 NapCat 和 Saya，扫码登录及账号验证仍由用户完成；QQ 客户端与 NapCat 升级可能影响兼容性。[QQ 官方机器人平台](https://github.com/tencent-connect/bot-docs/blob/main/docs/README.md) 是另一条接入路径，本期未实现其凭据与事件协议。
- 建议默认保持本机回环监听。跨机器部署需自行配置 HTTPS、可达地址、防火墙和 Token；此功能不会自动开放公网端口。公开状态接口只包含服务状态和统计，不暴露 QQ 号、Token 或原始消息。

## 故障排查

| 现象 | 检查项 |
| --- | --- |
| 配置有误 / 服务未运行 | QQ 号为正数字；启用时配置 bot ID、访问 Token、上报鉴权与至少一个白名单；参数范围合法；重启后生效 |
| 诊断 401 | 设置独立 `SAYA_QQ_ADMIN_TOKEN` 并在页面输入；NapCat 的 API Token 不是管理 Token |
| 诊断连接失败 | NapCat 已登录、HTTP 服务启用、端口正确、访问 Token 匹配；检查 `127.0.0.1` 在当前容器/主机中的含义 |
| 账号不匹配 | `SAYA_QQ_BOT_ID` 必须等于当前 NapCat 登录账号 |
| QQ 发消息无反应 | 先看“认证事件最近到达”；无事件则检查上报 URL、HMAC Token 与后端端口；有事件再检查白名单、好友类型、群 @、冷却时间和事件时间 |
| `generation_failed` / `generation_timeout` | 检查模型可用及本机API配置；需通过明确按钮从浏览器保存到本机，普通聊天不再自动同步；模型输出为空或生成错误也会失败 |
| `delivery_uncertain` | 检查 OneBot 发送权限、QQ 在线状态、群禁言和网络；不会自动重发旧消息，可发送新消息确认 |

## 验证记录

自动化验证覆盖签名、Bearer 鉴权、事件过滤、好友/群白名单、主人群聊不共享、上下文分区、去重、冷却/背压、重启恢复、生成失败、发送结果不明、协议 text 段、分段上限，以及真实聊天生成器的隔离/共享记忆和 QQ 禁止执行游戏指令。模型与 OneBot 接口均使用模拟传输，无需真实账号。

测试结果和本轮交付状态见 [验收报告](qq-bridge-report-2026-10-24.md)。真实 QQ 账号扫码、NapCat 实机互通及真实模型的回答质量，需要完成部署配置后进行验收；本轮不把模拟测试表述为真实 QQ 已上线。
