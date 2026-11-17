# QQ 配置共享与本机服务使用说明

**2026-11-01 第66轮当前入口与退出语义：**同一根`start_saya_qq.bat`已升级为中文管理面板，带启动/停止/重启/状态与残留检查。正常关闭面板会先停止并核验后台，成功才退出；停止失败保留窗口。第65轮“关闭提示窗仍后台运行”仅为旧版本历史。当前操作、范围与CLI详见[后台管理](qq-backend-control.md)。原监督/Windows自启动选择保留，WSL系统宿主不按模型残留强杀。

**2026-11-01 第65轮推荐手动入口：**双击项目根`start_saya_qq.bat`，统一启动/复用QQ、NapCat、应用与QQ接收，等待实际READY；不用同时运行旧start_saya.bat/start_saya1.bat。登录过期需手机确认，陌生8000占用先正常关闭原窗口；不会强杀或自动改业务开关。READY后关闭提示窗口不停止后台；不会新增Windows自启动，语音按需加载。实测和限制见[第65轮报告](qq-implementation-2026-11-01-session65.md)。

更新：2026-10-28。[当前实施状态](qq-implementation-status.md) 单独区分代码交付和完整验收。

第七轮：[DeepSeek独立搜索与厂家互斥](qq-implementation-2026-10-28-session7.md)。**Settings → LLM API → Search provider**分别提供“启用Brave”和“启用DeepSeek”，只选一家；切换保留两把密钥，选择及搜索密钥独立保存到原API文件，网页和QQ共用。当前已启用DeepSeek并通过真实Search/Fetch验证，原Brave密钥保留。

DeepSeek可以填写独立搜索密钥，或勾选启用时复制本机官方模型密钥；复制后更换聊天模型不改变搜索密钥。覆盖不同已存值须明确允许覆盖，旧版本仍被拒绝。DeepSeek搜索含条件最多128字符，不支持Brave严格安全筛选；严格时会拒绝，不静默放宽或回退，Learner原严格策略同样保留。

第六轮已启用本地统一读取，详见[补记](qq-implementation-2026-10-28-session6.md)。Backup & Restore → 本地统一设置可开关、立即保存，Preferences备份包含该文件；当前不要再次导入旧缓存。

第五轮：[唯一主页面与完整设置存档](qq-implementation-2026-10-28-session5.md)。跨浏览器只允许一个可工作的主聊天页面，第二页显示占用提示；切换时关闭原页面，或使用 **Settings → Backup & Restore → 释放此页面占用，切换浏览器**。旧版已打开的页面须刷新。QQ后台不占网页槽位。

**Settings → Backup & Restore → 本地统一设置 · 含3D展示** 可预览导入22项登记范围中的已有设置（含实时3D快照）到 `knowledge_base/browser_settings.json`。用户常用Chrome已存16项，原缓存保留；当前统一读取已开启，缺失/重置不回流旧缓存，屏幕仅适配显示。API凭据沿用LLM API / Other API的受管文件。详细范围见[设置清单](browser-settings-inventory-2026-10-28.md)。

同日第二轮已补NapCat守护、在线观察与网页流排空；[第二轮证据](qq-implementation-2026-10-27-session2.md)单列，旧报告保留。

同日第三轮已补重启一次性恢复、离线登录诊断、隔离回退与真实双网页冲突验收，见 [第三轮证据](qq-implementation-2026-10-27-session3.md)。

第四轮已补本机API配置优先与浏览器独立草稿，见 [配置按钮与验收报告](qq-implementation-2026-10-27-session4.md)。

QQ表达现已独立适配：默认简短自然，保留原角色设定；正常回复不再附DeepSeek/Brave能力提示、来源清单或已知引用编号，内部证据保留。需要链接时在本次问题里明确说“附上原文链接”或“给我来源”。真正搜索失败/资料不完整仍有简短说明，记忆提交回执仍准确显示。用户明确要详细内容时可以展开。这是当前默认规则，尚无新增独立可视化长度档位；语意多消息/引用/续读仍按QQ-03前置门禁推进。详见[第十轮报告](qq-implementation-2026-10-28-session10.md)。

“检查连接”现在分别显示账号匹配和外网连通性；账号匹配不等于外网可用。外网探测仅公共HEAD、无密钥/搜索/模型调用、超时和缓存受限，不承诺具体QQ网关状态。真实断网后须等账号重新匹配再发新消息，未到后端的重连前消息不自动重放。Windows临时验收任务及撤销见[第十一轮检查点](qq-implementation-2026-10-28-session11.md)。

第十二轮收尾：用户取消Windows注销实验，临时登录任务已撤销，正式自启动保持原关闭。真实登录触发未验收，不承诺注销后自动恢复；后续不再主动安排该实验，见[实施状态](qq-implementation-status.md)。

## 网页设置

第十五轮引用修复：针对“原文已成功读取却仍要求贴原文”，已把引用放在模型本次问题旁边，API/本地/自主工具均一致，保留原用户输入的存档和记忆归属。历史中“看不到引用”的回答不代表当前引用可用性；若确实撤回/超时/范围不符仍会如实说明。管理员`/api/qq/settings`的`quote_diagnostics`仅含状态、耗时与匹配布尔值，不含原文、编号、账号或密钥；不作为QQ附注显示。当前稳定性连续实测见[最新状态](qq-implementation-status.md)。

入站引用原文现已读取：你在群中引用其他人的文字并@机器人，或在私聊引用当前会话的文字提问，机器人会读取一层原文作为本次参考。无需另开设置；“引用原消息”开关只控制机器人发出的回复引用。每次最多2000字/3秒，核对同群或同私聊；撤回、过期、媒体/转发链、跨会话或无法核对来源时请直接贴文字。机器人自己发出的私聊消息需有本机会话发送回执，旧消息或过期缓存可能无法确认。权限/人设不因引用改变，见[第十四轮报告](qq-implementation-2026-10-28-session14.md)。

QQ-03已部署：Settings → QQ接入 → 会话与响应，新增“分段聊天/旧格式”“引用原消息”“单条长度目标”“每次最多消息数”，网页和主人命令使用同一本机文件及修订保护。默认chat/引用开/240字/3条，可用`/set delivery legacy`回退；`/set quote off`、`/set bubble 180`、`/set bubbles 2`修改其他项。长回答按提示发送`/continue`或带结果编号续读，使用已生成内容，不再次搜索/保存。24小时过期、每会话8份/全局128份、单份48000字上限；发送不明的结果会拒绝继续，防止重复/跳段。这里24小时是结果缓存寿命，与用户调整为20小时的前置观察条件不同。真实QQ验收状态见[逐项表](qq03-acceptance-2026-10-28.md)。已打开页面请刷新后使用新控件。

打开 `http://127.0.0.1:8000/static/Chat4o5-Modern.html`，左下角 **Settings → QQ 接入**。

1. **共享默认 · 网页与 QQ**：填写思考、工具、记忆召回/预算、保存、搜索、输出与历史参数，保存共享默认。
2. **QQ 设置来源**：逐项勾选跟随共享默认，再保存并应用。不勾选的项保持 QQ 独立值。
3. **网页普通聊天跟随共享默认**：开启后采用共享的思考/记忆等参数。API连接另由“本机配置优先”决定，默认优先本机文件；两个开关相互独立。
4. **连接与服务健康**：点击“检查连接”，分别查看 Saya、消费者、配置、OneBot、鉴权、QQ 登录、模型与搜索配置。QQ 已暂停时仍可检查。

旧17项值不会自动变成继承。本轮临时测试后已恢复原值，共享默认也清回空值、关闭网页跟随。尚无共享值时，“等待共享值”明确使用原QQ值暂用；`off`、`0`、`auto` 都不代表继承。

账号、主人、群授权、私人记忆权限、QQ总开关不继承，不能扩大权限。主人私聊共享长期记忆的原开关保持有效；群与其他联系人继续隔离个人记忆。

QQ的**网络搜索**与**自主工具**可同时开启：网络搜索为`auto`时，命中网页同一套自动判断（如新闻、天气、汇率、明确搜索请求或网页链接）的主人好友私聊，会先运行固定Search/Fetch再回答，不再依赖模型自选工具；未命中的普通聊天仍可使用自主记忆工具。`off`仍关闭自动联网，`/search 内容`仍可显式请求本次联网。群聊和其他联系人没有因此获得搜索或私人记忆权限。具体修复见[第九轮报告](qq-implementation-2026-10-28-session9.md)。

## QQ 命令

仅已配置主人好友私聊可修改。网页QQ面板和命令使用同一配置版本。

```text
/settings
/set thinking inherit
/set thinking auto
/set thinking none
/set thinking low
/set thinking high
/set thinking max
/set memories 50
/set budget 16000
/set search off
/set search auto
/set search force
/set save on
/set tools on
```

具体值变为QQ独立覆写；共享字段的 `inherit` 恢复跟随。QQ专属开关不支持继承。显式思考档位不支持时在保存前拒绝；忙碌时沿用旧的拒绝修改规则，待任务结束后再改。本阶段没有新增取消任务功能。

QQ面板每5秒检查修订，回到窗口也检查；有未保存编辑时保留并提示冲突。旧表单不能抹除继承。聊天请求开始时绑定配置快照，之后修改不改写该快照。

## 模型和搜索凭据

- **Settings → LLM API / Other API → 本机配置 · 跨浏览器共用**：在有缓存的浏览器点击“浏览器配置保存到本机”。支持16项模型连接/路由、Brave、翻译、语音API字段。Brave输入框位于LLM API → Search provider。
- 默认只补空缺；不同非空值须勾选允许覆盖，仍有修订冲突检查。空白不清空；需要清空时在折叠区明确选择字段并确认。模型Key/URL/Model必须整组保存，清空也整组进行。
- 本机配置优先默认开启，运行时读取同一文件，浏览器输入框保持草稿。关闭优先选项并单独保存后可使用浏览器配置。网页共享默认的思考/记忆开关与此独立。
- “本机配置同步到此浏览器”才会回写缓存；回写前完整备份两个缓存原字符串，另有“恢复上次浏览器缓存”。普通读取、编辑、聊天、旧轮询不会写入共同文件或自动把本机值覆盖到浏览器缓存。
- QQ面板旧搜索同步按钮转向同一版本化入口；受管配置拒绝旧无修订同步，避免未刷新的旧网页抹掉新值。
- 共同存储是 `knowledge_base/api_credentials.json` 或已指定路径；旧QQ搜索文件仅兼容读取。设置GET只返回配置状态，不返回Key。
- 模型优先环境变量的完整连接组合，再服务端文件，不把环境Key拼接到文件的另一个URL。搜索优先环境，再共同字段，再旧QQ文件。
- “已配置，未发起服务验证”只表示有配置，健康按钮不产生模型调用费用。历史none/low真实测试见执行报告；用户最新导入模型尚未再次付费验证。

用户已从常用Chrome导入Brave密钥，另一浏览器已读到。真实搜索返回 **HTTP402 / USAGE_LIMIT_EXCEEDED**，成功搜索验收 **BLOCKED（服务用量上限）**，不再是缺密钥；请检查Brave账户限制后再测。不要重复导入密钥或把开关auto/已配置当作搜索成功。

## 启动和维护

QQ依赖电脑中的 **Saya后端 + NapCat + 已登录的机器人QQ**。可以关网页，不能关闭必要后台；电脑关机、睡眠、断网时无法保证收发。云模型还需访问外部模型服务。

项目根目录PowerShell：

```powershell
$qqPython = 'D:\Program\anaconda3\envs\Saya\python.exe'
# 已有NapCat则保留；启动Saya守护
& ./scripts/start_qq_managed.ps1 -Python $qqPython
# 后端网页打不开时也能查看
& $qqPython scripts/manage_qq_service.py status
# 人工停止Saya，保留NapCat登录与数据
& $qqPython scripts/manage_qq_service.py stop
# 禁止守护重启并进入维护
& $qqPython scripts/manage_qq_service.py maintenance
# 明确恢复并重新启用守护
& $qqPython scripts/manage_qq_service.py resume
```

网页暂停只暂停QQ消费者，不退出Saya，不会被守护当作崩溃。人工停止/维护先保存意图，阻止新QQ事件和网页写请求；QQ任务或网页流仍忙时返回 `busy_preserved`，完成后再执行停止；状态未知返回 `status_unknown_preserved`，保留进程。已有SSE按整个响应体计数，不在头部返回时提前归零；恢复接收用 `resume`。这不是取消任务，已返回202的独立后台作业不在活动HTTP计数内。

守护只接管/停止匹配PID、创建时间、工作区、固定入口的进程。其他服务占端口时记录 `blocked_foreign_port`，不杀进程。OS锁随进程退出释放，不靠删除锁文件解锁；标准入口只有一个worker，在模型导入前取得锁。

Saya意外退出后2秒起步指数退避，上限60秒，连续启动失败最多5次；稳定120秒后重置连续失败数。耗尽为 `retry_exhausted`，修复后明确 `resume`。导入启动等待上限600秒，超时只记录，不强杀未知进程。

意图、所有权、状态在 `.runtime/qq-service/`；私有 `service.log` 按2MiB/3份备份轮转。后端关闭时用本机CLI诊断。

现支持 **Saya后端和专用NapCat两个独立守护**。Saya守护已运行；新NapCat守护默认关闭，可在 **Settings→QQ接入→后台守护与在线观察→自动恢复专用NapCat** 立即开启，不需要改原17项QQ设置。关闭只停止自动恢复，不结束当前QQ或注销登录。

电脑重启后，`start_qq_managed.ps1`的一次性NapCat启动会限时推进旧owner退出等待、清理和启动，不再停在第一次tick；这不会顺带开启持续守护或Windows自启动。若QQ提示登录态失效，仍须扫码，本轮已实际遇到并恢复。

“检查连接”在OneBot未启动时可补读NapCat管理状态：需要扫码、连接离线、已确认等待初始化分别显示；管理页显示登录成功不等于账号已核对，最终账号匹配仍看OneBot。

```powershell
# 先运行Saya守护，再明确开启NapCat自动恢复
& ./scripts/start_qq_managed.ps1 -Python $qqPython -SuperviseNapCat
# 与网页使用同一持久化策略；已有Saya守护约2秒内读取
& $qqPython scripts/manage_qq_service.py napcat-enable
& $qqPython scripts/manage_qq_service.py napcat-pause
& $qqPython scripts/manage_qq_service.py napcat-status
```

仅管理专用安装目录/工作目录/profile匹配的QQ。原主进程退出后等10秒，只清理此前记录且重新验证的残留子进程，再有界启动；预算跨守护重启保留。无权限确认、陌生profile、多实例、端口被其他程序占用时保留现状并提示。登录失效或进程活着但OneBot异常不会触发强杀；扫码仍由用户完成。本轮已验接管现有QQ，实际NapCat崩溃后登录保留仍待实机验证。

## 在线观察

Saya守护运行时每60秒采样，网页展示有效观察、健康时长和未知时段。本地台账 `.runtime/qq-service/observation.json` 只保留组件状态及最后100次变化；不含QQ号、Key或聊天。

超过90秒的采样缺口不算在线，当前未采样尾段显示未知；时钟回拨也不重复累计。24小时后只提示人工审阅，仍需核对重启/停止/断网场景。已有进程运行几小时不能回填为几小时的观察；不要删除台账重新开始以掩盖缺口。

## 可选登录自启动

正式自启动**未注册**，默认关闭。已实测单独的Validation临时任务注册、无副作用执行及精准撤销；并在隔离副本验证启动意图。真实Windows重新登录仍待用户安排，不等同于临时任务运行成功。

```powershell
& ./scripts/register_qq_service.ps1 -WhatIf -Python $qqPython
# 明确需要时注册当前用户登录后启动
& ./scripts/register_qq_service.ps1 -Python $qqPython
# 精准撤销当前工作区任务，不删除QQ登录和数据
& ./scripts/register_qq_service.ps1 -Remove
```

任务名含工作区摘要，当前用户普通权限、隐藏启动、忽略重复实例。自动入口尊重持久化人工停止/维护 `-RespectStopIntent`。移除前核对工作区说明、唯一动作和完整参数；同名陌生任务保留。这是用户登录后启动，不是无人登录前的系统服务。验收脚本 `tests/test_qq_startup_registration.ps1`只操作带Validation名称且动作含ValidationOnly的临时任务，最终会撤销。

原前台方式 `& ./scripts/start_qq.ps1 -Python $qqPython` 仍可使用；切换前先停止所属守护/后端，不同时另起多worker。

## 回退

先解除全部继承并保存，关闭网页跟随，QQ设置会重新写成旧版平面格式。用管理命令停止且确认停止，再按阶段源码副本回退需要的文件；不重置工作区，不把旧库覆盖到真实新增会话/记忆之上。

关闭新增共享不等于关闭原主人记忆共享。关闭守护不删授权、登录、记忆或队列。设置格式往返、SQLite恢复副本、重启配置保留已验证；最新隔离源码回退结果见下文，生产应用与数据库未做降级。

第三轮已完成**隔离源码/网页回退及最新数据副本兼容**；未做生产降级，不能覆盖用户之后新写入。需要复测且相关代码有变化时可运行：

```powershell
& $qqPython scripts/rehearse_qq_rollback.py --name new-unique-name --include-private-data --run-tests
```

只写入新的`.runtime/qq-upgrade/rollback-rehearsals/<name>/`，不覆盖已有目录。`code/`用于旧187项测试，使用空白环境与Python外部网络拦截；`private-data/`是最新私有副本，不自动接入测试或实际服务。继承项在副本中冻结为有效值、原元数据备份在`rollback-origin/`；共同搜索凭据显式转换到旧格式，清空状态不会复活旧值。

完整旧网页预览只在本轮单独验收，不由工具自动启动。旧网页固定非8000端口API到生产8000，故不能直接用8029等端口隔离；本轮使用独立IPv6回环同端口并已关闭临时进程。模型、头像和3D资源不在源码包中，相关显示不能视为通过。
