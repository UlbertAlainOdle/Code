# 第79轮：图片、大表情与文件的发送者身份

日期：2026-11-03，America/New_York。先读实施状态，核对第78轮436项指纹一致，私有备份后修改。用户确认两种文字引用都能认对作者，又指出图片/大表情可读但不知发送者，怀疑文件也有同类缺口。本轮沿该反馈完善来源，不重做旧阶段。

## 原因及实现

原文字引用只有读到非空文字后才保留sender_id。纯图片引用进入no_text时丢失作者；图片模型请求只有图像数据，没有明确发送账号。文件摘录虽有source_speaker_id，但缺少与机器人/当前提问者/主人关系的清晰映射。另有机器人媒体的保守来源闸：私聊get_msg没有可靠收件人，不能仅凭sender=机器人就读取任意引用媒体。

- `qq_quotes.py`把内容可读性与消息来源分开。协议账号/消息ID/会话核对成功后，瞬态quote_source保留来源账号、来源消息、时间、内容类型和作为不可信显示标签的昵称/名片；no_text仍不冒充读到了文字。交给模型时使用source_only，明确谁发送与内容是否读到是两件事。冲突账号、跨群/私聊/机器人、错误消息ID不提供可信来源。
- `qq_media.py`为实际读取的当前或引用图片/文件返回同一来源消息，`qq_chat.py`将其映射成机器人本人/当前提问者/配置主人/其他账号，明确绑定当前图片parts和文档摘录。单独引用另一个人、同时自己附图时，两份来源分开。两次协议读取中作者改变则停止读取；图片文字、文件名和昵称不能改身份/权限。
- 说明只证明谁发送/上传到这次聊天，不证明谁绘画、写作、出现在图中或最初制作了转发资料。资料仍是DATA，不开启agent、自动个人记忆写入、搜索或主人权限。未读到正文时仍能回答已核验的发送者；来源不明时不猜同名别人。
- 机器人自己发出的收藏图片只有成功数字发送回执绑定同一机器人及收件人/群，才可作为可读引用；保留未知、跨目标或不确定发送的拦截。授权群已公开发送的表情可由本群另一人引用，不借此获得原发问者私聊权限。普通文件输出仍取消，不为此新增机器人文件发送。

## 补足5分钟计划与来源证据的寿命差异

首版部署后继续核对发现，表达计划原本只保留300秒，不能把它同时当成长期的已发送来源证明。未延长可投递计划、图片素材或声音缓存：`qq_expressions.py`只在真实sticker发送成功后，把最小收件回执写入独立`qq_bot_media_sources`，只含机器人、回执、会话范围、用户/群和固定到期时间，无图片、文件内容、正文、URL、路径或收藏说明。

来源回执最多24小时、全局1024条，读取/重启不续期；计划或父结果容量清理不抹去仍有效的收件凭证。启动仅从仍存在的sent/sticker/数字回执记录做有界补录，不从运行日志、昵称或用户声明补造，已经清理掉的旧回执无法恢复。`qq_rollback.py`增加非空来源账本需要`qq_bot_media_sources_v1`的回退读者检查，原账本/聊天不覆盖。

## 自动化验证

第一轮新测试夹具的回调参数名和SQL占位数错误导致21失败，旧用例均保留；修正夹具后27专项通过。随后增加独立多来源及失败分支，最终首版17文件291通过、54条既有FastAPI弃用警告、120.76秒，0失败/错误/跳过：

```powershell
D:\Program\anaconda3\python.exe -m pytest tests/test_qq_attachment_authorship.py tests/test_qq_quote_author_identity.py tests/test_qq_quotes.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_group_document_ids.py tests/test_qq_group_integration.py tests/test_qq_quote_presentation_regressions.py tests/test_qq_media_phase52.py tests/test_qq_media_native_cache.py tests/test_qq_expressions.py tests/test_qq_forward_input.py tests/test_qq_control_receipt_isolation.py tests/test_qq_actions_time.py tests/test_qq_voice_capabilities.py -q --tb=short --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session79-final-tests-data --junitxml=.runtime/qq-upgrade/session79-final-tests.xml
```

追加来源回执后，一项新回退夹具项目目录不符先失败，改为隔离标准目录的SQLite备份，不改业务断言。最终8文件229通过、18条既有警告、101.14秒，0失败/错误/跳过：

```powershell
D:\Program\anaconda3\python.exe -m pytest tests/test_qq_attachment_authorship.py tests/test_qq_expressions.py tests/test_qq_quotes.py tests/test_qq_quote_author_identity.py tests/test_qq_rollback.py tests/test_qq_voice.py tests/test_qq_speech_sections.py tests/test_qq_speech_cues.py -q --tb=short --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session79-final-receipts-tests-data --junitxml=.runtime/qq-upgrade/session79-final-receipts-tests.xml
```

范围重叠不累加。新用例独立在`tests/test_qq_attachment_authorship.py`，原151份测试文件指纹不改。覆盖私聊/群、图片/文件、当前/引用、机器人/本人/其他账号、同名及伪身份、双来源、读取失败仍有身份、跨目标拒绝、不确定/过期/非图片回执、计划与父结果清理后仍可核验、重启不续期、数量上限及回退读者。真实生成器配隔离模型/OneBot替身，核对系统身份及实际图像parts，**不是QQ实机或真实模型语义实验**。

## 实机反馈与证据边界

用户已确认文字引用中的机器人本人和用户本人都能认对。本轮首版部署后，用户继续确认自己的图片正确认出自己，以及群内自己上传的文件正确认出自己。只读核对部署后两份实际结果均为群消息：来源诊断两次no_text、同群/消息ID核验正确，inbox=sent、实际气泡均sent/有回执。**计为群内本人图片和群文件的发送者实机通过，不扩大成私聊图片也已验收。** 第一份核验查询把已脱敏清空的inbox payload当成持久引用字段，得到0条；保留该查询，改按实际结果时间/群范围和来源诊断核对，未虚构空集合通过。

用户随后已确认机器人新表情也正确认出自己。只读核对本次为群引用：no_text、同群及消息ID核验通过，对应答复inbox和气泡sent/有回执，**群内机器人图片的发送者识别通过**。这次仅询问发送者，不能扩大为已实测图片内容读取；也不能扩大为私聊机器人图片已验。当前新来源账本仍0行，没有捕获到应用成功sticker新增回执，因此**回执保留的真实写入/跨5分钟及私聊机器人图片仍待自然产生适当样本**，隔离清理/重启测试不替代实机。最初核验脚本把“应有新增来源行”作为必需条件导致断言，保留失败，随后分开核对身份答复及账本范围，不制造回执或改测试结论。

不重复已确认的本人图片/群文件/群内机器人发送者。当前无可补录的旧机器人表情回执，不能靠名字补认；不改random、不刷表情、不代发QQ。旧已清理回执、超期或超数量上限后的私聊媒体可能无法核验收件人，应如实说明，不能据此否认自己曾发送。

## 部署、保持和恢复

首版及最终r2分别通过原full-control锁、排空和三库完整备份，只维护重启应用后端，均3.12秒ready；QQ原进程/登录保持，排空解除，ready/running/reachable/matched。新来源账本启动前后均0条，后续只记录实际成功发送；旧缓存/回执/24控制/继承/网页值、媒体/收藏/表达与新策略、人物/声线/凭据/浏览器/监督自启动选择核对保持。仍random/revision3，体验enabled=true、concise_replies=false、short_voice=true，未替用户开其他策略。

两次部署前后16观察缺口、原起点和unknown保持，不补在线信用。人物和共享过滤器、声音及第78轮已实测短句/提示省略/纯文字禁用保持；ASR、普通文件输出取消和官方整包撤回保持。没有新增agent模型/搜索/TTS探针或人工QQ代发，不重跑付费48对照或1112累计。

最终私有冻结`session79-release-source-r2.json`共259项，原冻结/失败/备份/核验证据均保留，公开文档不含账号、原对话、文件内容、回执或凭据。[最新状态](qq-implementation-status.md)及第79轮不可变manifest为后续入口；第78轮清单不覆盖。

下次先核对第79轮指纹、当前意图/设置；本轮三种群附件来源已确认不重测。只有应用真实发送sticker并产生新来源行后，才安排私聊机器人图片或跨5分钟来源保留的自然观察，不强制发送或为等样本停住独立工作。随后按[小步验收清单](qq-live-acceptance-2026-11-03-session78.md)做真实长期召回等；原第8阶段实际收尾及跨日/事件收益/电脑顺序等未签门禁继续单列，不重开旧工程或改人设。
