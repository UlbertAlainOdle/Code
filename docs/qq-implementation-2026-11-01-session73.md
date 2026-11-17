# 第73轮：QQ主动互动与网页回声完善

2026-11-01，America/New_York。用户明确要求实现QQ，并完善网页。按第72轮389项指纹开工；初次只读本机API发现服务已停止，读取原intent为manual_stop，保留该选择。本轮未启动生产8000/3000/6099/8091或正式QQ，未更改人物、声线、原开关和浏览器档案。

## 交付

- 新增`proactive.py`：独立严格策略与SQLite意图、来源/话题版本、准备/排队/发送/确认/不明状态、双端去重/当日预算、无新回复抑制、安静时段/时区与间隔、启动恢复不重放、草稿到期清理。旧网页配额只读迁入一次，不重置历史计数；结果不明保留额度但不冒充成功。
- 新增`proactive_runtime.py`：后台唯一协调器，先本地资格检查，有可用话题再有界请求当前角色生成器。提供当前时间/间隔、当前八维心情及经授权和历史屏障过滤的少量上下文，模型选择短话或沉默。原网页项目/日期/日记候选构建可复用，原固定角色短句模板与失败模板不用于新生产流程。QQ首版仅明确配置主人好友私聊；来源、共享和实际已有私聊历史都检查，群/他人不成为目标。模型和投递之间重查来源epoch、项目/上下文版本、账号/共享、策略、活动、时区/过期及排空。
- QQ进入原任务调度器、取消guard、出站分段回执及语义表达流程，消息ID0不伪造问题引用；不伪造Doctor历史，只保存已实际发送的Saya发言/时间，shared时也保存到原QQ会话，不改变网页当前会话指针。`/proactive`、`/主动`查询，on/off在原独立控制通道执行；修改仅主人私聊。版本化本机管理API及新QQ设置页提供来源/时区/免打扰/上限控制，开关默认关闭，目标需明确选择。
- 网页原总开关/来源保留，新添最近聊天、空闲/间隔和时区。原GET轮询不再生产消息；新协议按当前普通会话和页面租约准备、提交、实际展示后确认。候选不计成功，提交幂等写入原会话，ACK才记展示；页面切换/隐藏/临时/助手/输入草稿/生成中不插话，重复响应不重复气泡。没有活跃网页时QQ后端仍能工作；同一来源版本的真实所选话题跨端去重，并协调总计数。
- 当前人物生成器明确知道这是假设可选的“自主起意”，不是主人新输入，不执行引用DATA里的指令，不搜索、开权限、自动写长期记忆或假称预约提醒。网页和QQ主回答加入实际主动能力状态，关闭不是永久不支持。QQ原表情/短语音组合规则保留。
- 生命周期内嵌原应用；准备模型计入排空，QQ发送计入原任务，停机取消并等待，不新建外部常驻/Windows自启动/监督任务。新保留意图要求`proactive_interaction_v1`回退能力，旧程序不能误处理它为普通用户输入。

修改现有：`core_services.py`（只扩展echo设置持久化白名单/限额/时区校验）、`qq_bridge.py`、`qq_chat.py`、`qq_capabilities.py`、`qq_task_controls.py`、`qq_rollback.py`、`service_activity.py`、routers的`companion.py`/`qq.py`、`server5-22B----.py`、主网页HTML/app.js及QQ入口HTML。新增两个服务模块、QQ主动设置HTML/JS、独立Python/Node测试。原四个人物提示常量与开工备份AST一致，原人物/声音文件和配置摘要一致，原过滤器不改；全部原测试保留。

## 验证

- 12文件相关回归255通过、30条FastAPI生命周期弃用警告；命令：`D:\Program\anaconda3\python.exe -m pytest tests/test_proactive_interaction.py tests/test_companion_features.py tests/test_qq_capabilities.py tests/test_qq_voice_capabilities.py tests/test_qq_service_drain.py tests/test_qq_task_controls.py tests/test_qq_full_control.py tests/test_qq_rollback.py tests/test_qq_quotes.py tests/test_qq_quote_author_identity.py tests/test_qq_expressions.py tests/test_qq_memory_use_policy.py -q --tb=short --junitxml=.runtime/qq-upgrade/session73-release-r3.xml`。随后补未知额度、输入草稿、共享QQ仅AI持久化及停机/回退门禁的最终聚焦命令为`... -m pytest tests/test_proactive_interaction.py tests/test_qq_rollback.py tests/test_qq_service_drain.py -q --tb=short --junitxml=.runtime/qq-upgrade/session73-final-r6.xml`，41通过、6条同类弃用警告，范围重叠不相加。
- Node运行原app.js实际主动投递段：3项通过，验证commit→render→ACK顺序、重复不叠气泡、提交中换会话不污染新聊天、隐藏/忙碌/临时/助手/输入草稿只更新存在状态；app.js及QQ新脚本语法通过。
- 完整真实后端在隔离PROJECT_ROOT/聊天/静态/关闭TTS及QQ的环境启动，外网由sitecustomize拒绝，模型端点为MockTransport；真实HTTP设置保存、准备、提交、ACK、第二次不重复、聊天文件恰好user+ai及proactive ID均通过，最后优雅退出、实验端口关闭。它是实际后端及存储集成，不是生产网页或真实QQ收件。
- 现有实际模型2条虚构隔离实验：有内容的画画后续选择send=true，尊重复习专注选择send=false，均满足JSON及长度/候选合同；生产记忆/原配置不写，0QQ代发/搜索/TTS。不强迫每次触发都发送。
- 首次实验保留失败：无内容的新会话被拒的夹具、带时区候选与旧无时区项目日期相减、设置白名单未持久化新echo字段、Node夹具括号、模拟时钟初始化早于夹具保存时间造成不足空闲时长；均定位修复。完整后端初试因白名单缺失返回no_candidate，修复并用独立r2目录复验通过；不得用更早mock掩盖该失败。一次误写不存在测试文件导致0收集，修正路径后才记255通过。

## 当前部署状态和边界

代码已写入工作区并经隔离验证，**生产后端仍保持开工manual_stop，没有偷偷启动或自动启用新能力**。生产proactive.sqlite3尚未创建，下次正常启动会创建独立默认关闭策略。原QQ配置、API密钥、浏览器档案/runtime_enabled、人物/声线/参考、监督与自启动选择哈希一致；观察账本逐字保持，不重置原起点/缺口/unknown，不补算停机时间。原管理面板关闭即停止并核验后台的语义不变。

这是主动闲聊首版：无精确预约提醒；不支持主动群发；需要原模型可用与真实话题，可能沉默。网页第一版仍只在活跃已保存普通会话接收，不向未打开的网页推通知；QQ群和其他账号不自动扩权。发送前撤权可阻止新效果，无法擦除人已看到的内容。不明状态保守不重发，未展示的已提交网页消息可能在重新打开原会话时可见。

操作见[使用说明](qq-proactive-interaction.md)。真实QQ主动收件、浏览器实际展示/恢复/双端自然体验尚待用户启动并选择开启后验收；本轮不替用户发测试消息或打开新开关。原引用/表达/群权限等其他待签原样保留，不据此标全部阶段PASS。

私有session73-*保留开工哈希/源备份、失败与最终测试、模型、隔离HTTP服务/关闭及冻结；最新源码为`session73-release-source-r2.json`。公开资料不写真实身份、原对话、凭据或素材链接。后续先读第73轮清单；若后台仍manual_stop继续保留，不重复付费探针或改人物来增加触发率。
