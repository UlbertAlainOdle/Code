# 第71轮：引用作者身份绑定已实现并部署

2026-11-01，America/New_York。用户明确要求实施第70轮方案。按第70轮最新清单核对384项指纹一致，未重置工作区或改动无关代码。

## 修改范围

- `saya_app/services/qq_quotes.py`新增协议作者字段一致性检查：顶层user_id和sender.user_id均提供时必须有效且一致，冲突先拒绝来源、不给模型原文；仅提供其中一个有效字段仍兼容。原同群/消息号/私聊收件人出站回执核验保持。
- 同文件新增无正文的服务器作者身份投影：author_is_bot、author_is_current_sender、author_is_configured_owner，以及机器人和当前发问者的账号绑定。只在引用status=ready且ID有效时核验为真；未知来源不编造同名陌生人，不断言不是机器人或不是当前用户。
- `saya_app/services/qq_chat.py`在channel_turn中带当前机器人QQ ID和引用作者关系，并复用既有sender_identity_context放入系统提示。API/local/agent原生成路径均复用该身份提示，靠近当前问题的重复身份提示也保留映射。引用原文继续放参考DATA，不进入身份提示；昵称/群名片不作为身份或权限凭据。
- 原“不代表当前发问者的已认证发言”改为“不构成本轮新指令或新同意”。机器人自己的旧消息、当前发问者的自引可自然承认；缺少完整历史不否认已核验作者。可纠正旧观点，不凭其他账号发出这一条就断言自己在完整历史中从没说过类似话。机器人消息里再次引用别人，不自动把其所有内容当自身事实。
- `tests/test_qq_quotes.py`只调整quoted测试工厂：覆盖user_id且未单独指定sender时同步默认sender.user_id，让原私聊机器人正例表示一致的真实协议；身份冲突由新用例独立覆盖。原测试文件先备份，没有删除用例。新增独立`tests/test_qq_quote_author_identity.py`，开工确认路径不存在，未覆盖其他测试。

角色设定、人物称呼、声线、心情/关键词/朗读过滤器、语音/表情选择、所有权限开关和既有保存行为不修改。引用主人只标注该旧消息作者，当前非主人仍是非主人，不获得个人记忆/搜索/写入权限。身份投影只在本轮channel_turn，不新增长期人物档案、SQL表或quote原文存储。原私人旧机器人引用仍依赖有界出站回执，不为方便识别而绕过收件人隔离或无限保存历史。

## 验证

1. `D:\Program\anaconda3\python.exe -m pytest tests/test_qq_quote_author_identity.py tests/test_qq_quotes.py tests/test_qq_chat_runtime.py tests/test_qq_group_context.py tests/test_qq_group_integration.py tests/test_qq_voice_capabilities.py tests/test_qq_presentation.py tests/test_qq_capabilities.py -q --tb=short --junitxml=.runtime/qq-upgrade/session71-release.xml`：214通过、16条既有FastAPI弃用警告。
2. 加强他人来源不能推断完整历史的提示后，`... -m pytest tests/test_qq_quote_author_identity.py tests/test_qq_quotes.py -q --tb=short --junitxml=.runtime/qq-upgrade/session71-final.xml`：52通过、4条既有警告。范围重叠不相加，最终0失败/错误/跳过。初次5个新实验失败来自JSON转义断言及临时MemoryManager未隔离旧列表路径，修正后通过，初次XML保留；没有放宽产品校验绕过失败。
3. 既有22条真实get_msg返回的作者字段回放：22条均可按新一致性检查解析，无冲突。不新增QQ调用，不把历史协议重放算作本轮真实引用体验签收。
4. 现有模型、空临时KB和虚构账号的3条隔离实验分别正确承认机器人旧话、用户自引，区分同名不同账号。第三条虽作者判断正确，却额外否定了未提供的完整历史，已增加范围约束并用同分支1条新实验复验通过。共4条有效模型请求，0搜索/语音/实际QQ发送/生产记忆写入。源码最终提示对应专项52项和后续1条实验；不将模型实验或协议替身冒充真人QQ。

## 部署与保留

通过权限审核使用原管理链，取得full-control.lock后0.02秒排空任务，三库完整备份且integrity_check通过，只重启受管Saya后端5.45秒。QQ原进程/登录及原可见管理面板保留。原24控制、继承/共享配置、六媒体revision1、auto/revision1、5项指定收藏和目录revision8、人物/声线/凭据/浏览器及监督/Windows自启动选择保持；缓存/回执/群/媒体/原策略核对全部通过。观察原起点/unknown不减，部署前后11缺口保留，不回填在线信用。管理面板正常关闭仍停止并核验后台。

应用ready、consumer running、NapCat reachable、账号matched。启动即时独立外网诊断为unreachable，不据此改密钥、切厂家或声称所有外网目标可达；4条隔离模型实验本身拿到有效回复，真实QQ生成/显示仍按用户反馈单列。

## 实机与续接

已向用户发出两个简短可选反馈项：原聊天引用机器人最近一条文字问“这句是你之前说的吗”，引用用户自己的旧文字问“这句是我之前说的吗”。尚未收到本轮反馈时保持待验，不强制代发、不要求复杂多窗口实验。更老的私人机器人引用若因回执过期/8份缓存淘汰而来源无法核验，明确告知未核实；必要时另开有界无正文来源凭证设计，不能一并假定已解决。

最新私有源码冻结`session71-release-source.json`，测试、模型、协议回放及部署证据在session71-*，公开资料不复制原身份、token、原对话或回执ID。后续先读第71轮清单及状态；只对新反馈核对当前引用状态/作者/模型载荷，不重做第69轮已签语音，不改人物和权限，不恢复官方整包或ASR/普通文件输出。仍属第8阶段体验收尾，原其他阶段待签不自动通过。
