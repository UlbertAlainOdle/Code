# 第78轮：实机验收发现语音选段错误，修复及能力联动

日期：2026-11-03，America/New_York。先读实施状态/编排/回归基线，核对第77轮430项指纹全部一致；不重做已交付阶段。用户实际启动原管理入口并反馈首个简单实验，本轮据此修复并完成受管部署。原工作区及旧测试保留。

## 实际问题与改动

主人私聊确实收到文字和语音，但声音对应长段开头而非标为“语音：”的短段。只读核对该条已发送结果：正文178字符，实际语音选择17字符，标注短段36字符。旧校验要求片段前为句末或换行，冒号后的原句不符合；旧计划也未优先遵守该标注段。**不能仅凭旧计划断言一定是模型误选还是校验回退。** 私有旧/新代码回放证明：旧校验拒绝实际标注段，新校验得到准确正文位置；未为此重发旧消息或调用新模型/TTS。

- `qq_expressions.py`新增有界独立语音标注段解析。唯一合法段优先于模型选的其他句；保留正文、校验原始偏移及摘要，仅TTS去掉标题/外层引号。没有标注仍按原完整原句选择；不要求每条回复都带标签。空、超长、歧义、代码/链接等不安全片段停止可选语音，不换成无关开头；模型超时和voice专用模式同样遵守。标签本身不强制发送。
- `qq_dialogue_experience.py`增加无正文的speech_source和失败原因枚举；`qq_rollback.py`为未过期标注段计划增加`qq_speech_section_v1`读者要求，旧源码不得忽略该语义直接回退。
- `qq_natural_actions.py`登记对话体验总开关、篇幅引导、短句语音的明确自然操作；调用原主人/CAS设置服务，每次只改一个分项，不顺带改表达模式、媒体开关或其他分项。总开关关闭、语音媒体关闭、模式限制、服务忙等回执如实区分。关闭短句策略不等于关闭所有语音。
- `qq_capabilities.py`每轮投射三个配置值和实际生效值，明确当前权限及语音选段规则。`qq_help.py`统一优先控制和普通FIFO的帮助，`qq_task_controls.py`/`qq_bridge.py`复用；帮助及自然操作回执仍不进入人物普通历史。修正设置帮助中“群永远隔离全部私人记忆”的旧表述，实际仍按群和逐条记忆授权。
- 新测试独立放在`test_qq_speech_sections.py`和`test_qq_experience_linkage.py`；创建前确认不存在，未覆盖原测试。人物四段提示、全局过滤、声线/TTS、图片/文件解析、收藏目录及启动监督源码未改。

主人复测随后确认**已经读短段**，但“（很轻）”也被读出。后台对应新计划为labelled_segment、选段24字符、语音sent且有回执。选段实机项由失败更新为通过；这是新发现的朗读清理缺陷，不能继续声称选段仍未验证。旧共享清理器刻意保留普通中文括号，导致该明确语气提示留下。

据此增加`qq_voice.py`的QQ边界清理及独立`test_qq_speech_cues.py`：仅完整匹配短小白名单语气/朗读提示（如很轻、轻声、压低声音、短暂停顿）及这些提示的并列形式才不读。原始文字/选段指纹不改；普通括号补充、姓名/年份、公式、未识别或混有正文的括号保留，不全删括号。不把提示词转换为新声线或改韵律，原共享`speech_text.py`/`speech_prosody.py`逐字指纹保持。

## 验证与失败记录

最终命令（独立绝对临时目录，串行）：

```powershell
D:\Program\anaconda3\python.exe -m pytest tests/test_qq_speech_sections.py tests/test_qq_experience_linkage.py tests/test_qq_expressions.py tests/test_qq_dialogue_experience.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_qq_voice_capabilities.py tests/test_qq_natural_actions.py tests/test_qq_actions_time.py tests/test_qq_task_controls.py tests/test_qq_task_scheduler.py tests/test_qq_capabilities.py tests/test_qq_controls.py tests/test_qq_control_receipt_isolation.py tests/test_qq_rollback.py -q --tb=short --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session78-final-tests-data --junitxml=.runtime/qq-upgrade/session78-final-tests.xml
```

**325通过，0失败/错误/跳过，36条既有FastAPI弃用警告，177.35秒。** 原句偏移、队列实际交给替身TTS的文本、文字不改写、超时、歧义/空/过长标注、权限/开关/策略版本、互斥及禁用、能力说明、回执隔离、回退要求均纳入。真实生成器使用隔离模型/传输替身，不算真人QQ。

较早142语音相关及69修正专项通过，范围重叠不相加。初版联动用例有7失败，二次有2失败：新夹具错把normalize返回元组当消息、拿含动态任务计数的快照整体比较、未装语音替身、误用dataclass替换Pydantic设置；另原帮助断言暴露任务控制关闭时缺少/cancel说明。均保留失败记录，修正新夹具及共享帮助的真实限制说明后再完成最终325项。没有修改原断言或重复跑1112累计/付费48场景来凑成绩。

收到第二次反馈后的相关命令：

```powershell
D:\Program\anaconda3\python.exe -m pytest tests/test_qq_speech_cues.py tests/test_qq_speech_sections.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_speech_text.py tests/test_local_tts.py -q --tb=short --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session78-cue-tests-data --junitxml=.runtime/qq-upgrade/session78-cue-tests.xml
```

**121通过，0失败/错误/跳过，6条既有警告，38.94秒**，与前325范围重叠不相加。新队列用例核对TTS实际只收到口语正文，QQ显示文字和原始span仍保留括号提示；原共享语音测试及本地TTS回归通过。对主人新一条真实选段作旧/新清理回放也证实提示词前有/后无；未重放QQ、未新增合成探针。

私有`session78-*`保存开工备份、两条生产错误/反馈证据、旧/新回放、失败与最终XML、初版257/最终r2共258项源码冻结及两次部署备份；公开文档不包含账号、正文、回执、凭据或运行日志。agent新增模型/搜索/TTS探针0、人工QQ代发0；用户自然实验产生的真实收件另计。

## 部署和配置

用户启动后实际健康为ready/running/reachable/matched。最终测试后持原full-control锁、排空任务、三库完整备份，仅维护重启应用后端，**3.14秒ready**，排空解除；QQ原进程/登录保持。24控制、继承与网页投影、六媒体、收藏/表达及回执、主动/话题/分享/关系/事件和体验策略、人物/声音/凭据/浏览器及监督自启动选择核对保持。

第二次反馈修复后，再按同一流程受管部署r2，**3.16秒ready**，三库备份及全部配置/原QQ/回执/健康核对再次通过，解除排空。不是重发旧任务或重启QQ。

体验仍为revision1：enabled=true、concise_replies=false、short_voice=true；表达仍random/revision3。未代开启篇幅、分享记录、主动互动、关系或事件卡。观察原起点/unknown保持，本次部署前后16缺口不减，不能把第77轮历史15作为当前值，也不补24小时信用。原管理面板正常关窗仍停止受管后台。

## 实际验收与下一步

第1次真实收发通过但对应失败；第一次修复后主人已确认读到了正确短段，对应项通过。新增“很轻”误读已修复并部署r2，**主人第三次确认只读正文、没有读提示词**；后台同一新结果原片段确含提示、labelled_segment、语音sent且有回执，清理回放省略提示，故本次真实省略听感通过，不扩大到所有未知动作描述。[少量实验清单](qq-live-acceptance-2026-11-03-session78.md)按顺序安排纯文字禁用、引用身份、真实长期召回、分享记录及可选篇幅/主动体验，不再让用户同时进行复杂组合。已请求下一句纯文字禁用；图片、群文件和指定收藏等已确认项目不重做。

后续主人已确认该句纯文字要求只有文字、无额外表达；后台同一新结果text_only/none/skipped且无语音记录，**本次纯文字禁用实机通过**，配置未改变。当前已请求两种简单引用作者检查，待反馈；不将第71轮模型/模拟证据代替这两次实机观察。

仍处原第8阶段实际验收收尾，新增七项首版工程已交付；事件卡收益对照、跨04:00真实观察、若干真人/跨端组合及桌面气泡顺序根因仍单列待验，不能用325回归代签。ASR、普通文件输出取消和官方整包撤回保持。

下次读[最新状态](qq-implementation-status.md)及第78轮不可变manifest，核对最终r2共258项冻结和当前用户开关。先接收引用身份反馈，不自动重启/重放旧语音；短段选取/此次提示省略/纯文字禁用已确认不重复要求。用户之后的启动/停机与配置选择优先。
