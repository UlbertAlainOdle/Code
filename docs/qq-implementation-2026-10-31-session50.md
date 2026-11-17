# 第50轮：普通文字引用误报附件失败及Mood别名泄漏

用户在第49轮群文件最终复验前发现两项问题：引用文字本身可理解，但先回复附件读取失败；一条回复末尾出现plain Mood内部标签，并询问心情功能是否需要。本轮308开工指纹一致，先修已报告问题，不推进ASR或扩大情绪系统范围。图片双端和私聊文件的既有用户确认保留；群文件最终回答仍未获得用户反馈。

## 原因与源码

- `saya_app/services/qq_media.py`：原流程在判断引用内容类型之前，先拒绝机器人附件来源，导致引用机器人普通文字也被当成不可验证附件。现在在原会话/消息核验后先判断是否真的含图片/文件，无附件则返回None交原文字引用路径；实际机器人附件来源门禁保持。附件接收或读取功能关闭时，不因普通引用产生媒体告警；直接附件的关闭提示保持。普通引用不创建媒体来源快照、不误关原文字工具/写入路径。
- `saya_app/services/qq_presentation.py`：原清洗覆盖MOOD_SAYA/MOOD_USER等，漏了模型输出的plain Mood别名。新增该协议别名的大小写、全角及尾部未闭合过滤，沿用既有生成前持久化/分段、旧缓存和OneBot直接出口过滤。代码/数学中的字面示例、普通括号、用户原历史保持。
- 新增`tests/test_qq_quote_presentation_regressions.py`共11项：群内机器人/他人普通引用到原响应器、已知私聊机器人文字引用、真实机器人图片仍拒绝、附件关闭不误告警、Mood变体/历史投影/代码保留、旧缓存不重复已发段及直接出口清洗。

未改人物设定、提示词人格、心情开关或算法。现有会话心情服务用于语气连续性，语音可据此调节表达；建议保留为轻量内部状态，心情不代替事实确信或权限判断，内部标签不属于QQ正文。此次泄漏属于展示协议变体遗漏，不能据此声称心情功能无效，也不擅自关闭。

## 真实回放与测试

在私有数据库副本上用实际OneBot重读2条机器人普通文字引用。为对照重构旧QQMedia源码并验证其SHA256匹配第49轮清单，旧流程两例均返回附件误告警；新流程两例都返回None且原文字引用解析ready，无媒体快照。真实群TXT及私聊TXT各1例读取和实际限额解析保持成功。无模型调用、QQ代发或回放导致的生产写入。Mood过滤使用合成标签/模拟协议出口，不冒充真实模型或QQ发送；最终线上表现仍待用户确认。

首轮143项聚焦通过，补充出口/缓存用例后下列16文件最终218项通过（12条既有FastAPI弃用警告、0失败/错误/跳过；重叠不相加）。无JS/心情算法变化，不重复无关JS或心情系统整套验证。

```powershell
python -m pytest tests/test_qq_quote_presentation_regressions.py tests/test_qq_presentation.py tests/test_qq_delivery.py tests/test_qq_group_document_ids.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_media_native_cache.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_qq_quotes.py tests/test_qq_rollback.py tests/test_qq_service_drain.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session50-regression.xml --basetemp=.runtime/qq-upgrade/session50-regression
```

## 部署与保持

排空0.03秒，包含后台voice_rendering；三库备份完整性通过，后端重启3.16秒，原QQ进程保留，ready/running/matched。原24全局设置/继承/人物/风格/声线与参考/凭据/浏览器归档/监督自启动不变，六媒体开关开启及revision1保持，有效投递/分段/群/来源/语音记录保持。原观察起点、6缺口及unknown不减少，无重置或制造24小时通过。

部署后独立健康/配置/源码指纹验证通过；检查时媒体策略1、来源7、语音8、私人ACL/记忆操作0，计数可随用户聊天变化。没有代理QQ发送、生产记忆修改、TTS合成/模型付费探测或新依赖安装。

私有证据在`.runtime/qq-upgrade/session50-*`：before、protocol-private/diagnosis、旧源码hash对照、replay/副本、回归XML、final-source、deploy/备份和final-verified。原只读诊断帮助函数未传群标志产生的群ID提示不作为新业务失败；完整prepare回放的群TXT读取成功。公共报告不含用户原文、账号/群号、文件ID、缓存路径或凭据。

## 继续入口

先读最新状态和第50轮清单。请用户仅引用一条机器人普通文字并@提问，确认不再出现附件告警；随后完成原群文件引用@短复验。无需重新开开关、重启或重测已确认的图片/私聊文件。保留未完成QQ05/06/07真人签收、语音听感及文档未逐格式报告部分；第8阶段ASR和附件输出仍未实现。
