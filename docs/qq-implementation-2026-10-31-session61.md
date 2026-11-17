# QQ第61轮：收藏语义选择、原生表情及互斥表达

日期：2026-10-31，America/New_York。用户已指定5张现有收藏并补充说明，要求继续完成。开工第60轮344指纹一致，未重做迁移、图片缓存、原配置或已交付阶段。第56轮官方整包读取仍撤回；ASR及普通文件输出仍取消。

## 本轮交付

已接通“当前问题和回复＋主人指定的含义/适用场景→有界模型选择→服务端校验→实际图片/face/文字段发送”。只有指定且说明非空的现有收藏可成为候选，不新增、删除或修改QQ收藏，不重新分析或覆盖用户说明。模型只得到候选编号和说明，没有素材URL、缓存路径或私人历史；临时选择请求关闭agent、搜索、记忆检索/写入和会话保存，8秒响应读取/10秒总体预算，失败保持已生成文字。这会增加一次独立表达选择请求及其等待时间。

自动模式表情分支按合适的**收藏大表情→QQ原生小表情→颜文字**择一，也可不发。语音是互斥的另一个可选分支，符合短回答和现有声线/媒体权限才可选；每次最多一种附加表情或一条语音。严肃/技术/长篇/代码/链接/系统或保存回执、续读和合并转发不额外附表达，本轮“只用文字/不要表情/不要语音”优先。正文增加渠道投递约束，清除已知尾部装饰颜文字，保留代码与人物/风格文件。没有修改角色设定。

收藏下载仅固定QQ CDN、HTTPS、无跳转/无代理、8MiB及16MP上限；按原图片格式发送，GIF保留动画，分析/接收首帧逻辑不变。QQ小表情使用有限白名单的原生face段，颜文字使用服务器固定文本，模型不能提供任意协议段/路径。原生小表情目前作为独立附加气泡发送。下载或选择失败只留文字，发送结果不明不再降级补发其他类型。

新表达策略独立保存在SQLite，版本CAS/主人确认，不扩展或重置原24项设置。入口：

- 页面 `/static/qq-favorites.html` 的“读取模式/保存模式”。修改模式不覆盖收藏说明草稿。
- QQ `/expression` 或 `/表情模式` 查询。
- 主人好友私聊 `/expression auto|random|off|voice|sticker|face|kaomoji`；中文参数自动、随机、关闭、语音、收藏、小表情、颜文字。
- `auto`自主择一；`random`在语义匹配的合法候选中随机择一；`off`只发文字；指定类型也可因不适合、权限/本轮拒绝或长度限制而不发。语音模式仍先发文字，再附加短语音。群内/他人只能查询，不能改全局策略。

控制命令复用独立快通道，长任务期间可及时关闭。实际发送前重新检查策略revision、指定状态/说明摘要、原会话身份、群权限、记忆/媒体权限、任务取消/过期和正文确认回执；正文未全部发送或已达到每批5气泡，不加第6条。表达有持久化单次认领及回执；重启不重放准备中或结果不明的表情。缓存仅项目私有目录、有限文件/总大小、过期清理；旧版本回退须声明 `qq_expressions_v1`，不能忽略关闭策略或待决回执。

官方样本来源仍保留预览/指定/说明，当前自动发送只使用单张收藏来源；没有恢复被撤回的已添加整包枚举、原生扩展或按钮。页面和API分别说明，不用现有收藏发送冒充官方整包发送。

## 验证

修改：新增 `saya_app/services/qq_expressions.py`、`tests/test_qq_expressions.py`；更新qq_bridge、qq_chat、qq_favorites、qq_task_controls、qq_delivery、qq_rollback、QQ路由、收藏HTML/JS及Node测试。其余源文件指纹保持。

- 12文件226项相关Python回归通过（16条既有FastAPI弃用警告，0失败/错误/跳过），覆盖原语音、收藏、官方样本、控制/取消、分段、引用、生成器、群及转发/回退。后续补实际生成器和过期/语音撤销用例、修正来源发送能力投影后，最终3文件54项通过（8条既有警告；含32项表达检查，数字与226重叠，不相加）。
- 最终3项Node模拟DOM检查及JS语法通过：原双来源/草稿、QQ同步、独立表达模式CAS且不覆盖未保存说明。
- 实际生成器走mock HTTP验证临时请求参数、当前语义、无私人测试记忆、无工具/URL、无新增会话；mock OneBot验证图片/face/文本原生段、群目标、回执及未知不重发；真实GIF字节两帧保持。以上是自动化证据，**不是实际QQ收发或模型语义效果验收**。
- 实际本机API成功启用并回读auto/revision1；5项指定/非空说明、目录revision8及官方样本保持；最终HTML资源session61、SQLite完整性、ready/running/matched通过。
- 用户明确确认“收到了合适的收藏大表情”。后台核对实际发生在授权群：收藏图的真实发送回执get_msg回读确为机器人图片消息；另有两条原生face回执回读通过。三次均先确认正文，结果没有附语音。按**这一轮授权群收藏的语境与显示**记真人PASS；原生face两条为真实协议回读，不冒充用户主观显示确认，私聊/颜文字/语音/动画显示仍各自待签。没有要求用户重做复杂多窗口实验。

```powershell
python -m pytest tests/test_qq_expressions.py tests/test_qq_favorites.py tests/test_qq_official_packs.py tests/test_qq_voice.py tests/test_qq_task_controls.py tests/test_qq_task_scheduler.py tests/test_qq_delivery.py tests/test_qq_presentation.py tests/test_qq_chat_runtime.py tests/test_qq_group_integration.py tests/test_qq_forward.py tests/test_qq_rollback.py -q --tb=short --junitxml=.runtime/qq-upgrade/session61-expanded.xml --basetemp=.runtime/qq-upgrade/session61-expanded
python -m pytest tests/test_qq_expressions.py tests/test_qq_favorites.py tests/test_qq_official_packs.py -q --tb=short --junitxml=.runtime/qq-upgrade/session61-final-focused-r2.xml --basetemp=.runtime/qq-upgrade/session61-final-focused-r2
node --test tests/qq_favorites_sources.test.cjs
node --check static/js/qq_favorites.js
```

首轮误用旧Saya环境缺Pillow而未收集；改用当前后端同一Python环境，没有安装/修改环境。初期新测试发现语音权限谓词递归，已修复并用真实原语音服务验证互斥；缺fixture和会话列表属性/旧会话发现的夹具错误也已修正，保留首次失败XML，不放宽隐私断言。

## 部署和继续入口

排空0.02秒、QQ/Memory3/Memory4三库一致性备份通过，后端重启3.16秒，保留当前已登录53644 QQ实例，ready/running/matched。安装绑定/原24项设置/每批5、六媒体revision1、API提供商/凭据/浏览器/人物/声线、有效投递/群/转发/收藏保持；监督和正式自启动仍关闭。观察原起点/6缺口/unknown不减，不重置或补算24小时。仅有意新增表达auto策略。

私有session61-before/source-before/final-source、首轮及最终测试XML、三库备份/deploy/live-activated/resume保留，不公开原始身份、说明、素材URL、对话或日志。代理没有QQ代发、真实模型/TTS或付费搜索探针。

新增私有live-confirmed/protocol-private记录用户自然聊天的真实发送回执；这些模型/QQ发送由用户自然消息触发，不计作代理代发。首个只读回读脚本缺模块路径、随后按题目误限定私聊而未匹配；根据真实scope改为授权群后核验通过，期间没有发送或改配置。

整体处于**第8阶段实际验收**。当前用户保留范围的工程项已接通，授权群收藏自动选择和显示已获用户确认；其他范围不能由这一次或自动测试代签。用户刷新收藏页可读到auto模式，无需重复指定。若以后只收到文字，先按该任务检查选择/下载/权限状态，不清空目录、不重做迁移/登录，不把可选不发一概判失败。可在主人私聊用 `/expression face`、`/expression kaomoji` 分别检查单类，最后 `/expression auto` 恢复；不要连发复杂多窗口实验。

后续只处理真实反馈和原独立待签范围；群引用PDF第一行已有用户确认，不重复全部格式实验。保留原桌面气泡顺序/合并记录/动图显示和观察门禁的证据边界。下一轮以本报告和最新第61轮清单续接，不返回“发送尚未实现”的第60轮历史状态。
