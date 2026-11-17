# QQ第67轮：心情驱动的组合表达与戳一戳

日期：2026-11-01，America/New_York。用户要求小表情/颜文字自然位置、语音跳过表情、语音仅与大表情互斥，以及根据心情/上下文处理戳一戳并评估原心情系统。第66轮367指纹一致，保留完整后台管理面板、原启动选择、人物/风格/声音文件。

## 原问题与交付

原小表情/颜文字由after_text单独发，必定出现在正文后；选择器虽然已无固定类别优先级，却没读取已有会话心情，并把所有表情/语音都互斥。原心情已具八维、会话快照和锁/关闭，基础可用；本轮不重做全局引擎，而是补齐QQ连接和互动状态。过滤词模块、角色风格文件不变。

- 小表情/颜文字通过合法片段锚点和before/after投递到同一文字气泡，原生face和固定文本段，不执行模型CQ。可句首/句间/语义适合的句末，位置错误就省略，不回退成独立尾部气泡。规范正文保持纯文本，回执和未知状态复用原分段事务；过滤后才定位，代码/公式等保守不插。内嵌不增加气泡数。
- 语音变成独立语义决定，可与一类内嵌视觉表达共存，收藏大表情强制排除语音。各随机候选携带自身位置和语音决定。正文确认后再合成；保留短语音长度/时长/本轮拒绝/忙碌和每批5条限制。语音只取未加装饰规范正文，QQ边界移除完整已知颜文字并复用现有speech_text清理，防止部分复合颜文字余留音节。原全局speech_text和QQ过滤器不修改。
- QQChatResponder保留结构化mood_state但隐藏所有标签。新增QQSocial按真实账号/群私会话隔离八维互动状态，真实时间闲置衰减、关闭/锁定保留；主人共享私聊继续以网页较新快照和锁为准，较新戳状态可进入下一次QQ回答，不伪造网页历史。八维值/本轮情绪/近期表达进入决策，没有硬性情绪阈值或类别配额。网页原全局面板不因戳事件被强行改写。
- 认证notify/poke进入原任务队列，核对self/target/原授权账号或群，忽略自身回声/别人互戳/过期重复。不需群@但不自动授权群。最近60秒戳次数和少量同会话已授权上下文用于决定沉默、原生send_poke戳回原人或一句短话。重复只给有限刺激，不固定次数生气。防循环另设3秒合并、全局8次判断/分钟、同人同会话2次戳回/分钟；超预算安静，不改发文字刷屏。无agent/搜索/自动长期记忆，个人历史先经原记忆投影并绑定epoch，群快照复核撤权。
- 原send_poke动作及user_id/group_id参数从当前固定NapCat源码核验。任务取消/撤权/重启不重放，未知发送不补发。新增qq_affect/qq_poke_events及qq_social_v1回退保护，组合投递有qq_expression_composition_v2能力门禁，旧读者不能忽略新事件/状态。

详细触发机制、例外和命令见[心情、表情、语音及戳一戳](qq-expression-and-poke.md)。当前auto/revision1、原5项收藏说明保持；官方整包仍撤回，ASR/普通文件输出仍取消。

## 检查与证据范围

改动10个既有文件：qq_bridge、qq_chat、qq_delivery、qq_expressions、qq_rollback、qq_voice、server5-22B----、收藏HTML/JS、test_qq_expressions。新增qq_social和test_qq_social。没有修改conversation_mood、mood_continuity、qq_presentation、speech_text或人物/声音投影文件。

- 14文件290项扩展通过（12条既有FastAPI弃用警告）；之后完善关闭/锁定/共享私聊新旧心情裁决和TTS复合颜文字处理，7文件173项聚焦通过（8条既有警告）；最终隐私epoch及事件路径32项通过，无失败/错误/跳过。范围重叠不累计。新增内嵌+语音同发但TTS纯正文、大表情排除语音、错误锚点不尾补、原生分段顺序、过滤不漏、认证/错目标/回声/群权限/过期/去重/合并、戳回循环预算、未知不重发、撤权/私人记忆变化阻止迟到文本、八维衰减/锁/关闭、共享私聊较新网页状态优先等检查。
- 3项Node模拟DOM/JS语法通过。初次工程测试7项旧“独立尾部表情”假设失败后改用新组合协议夹具，保留原文本/回执/去重断言；未放宽权限或过滤要求。一次扩展命令误写不存在的mood测试文件未收集，改为实际emotion_controls等文件后通过，未改环境。
- 收尾补上闲置时语气标签与数值一起回落、戳未知结果明确投影至任务发送不明、只含颜文字的有意回复不被擦空；最终2文件78项通过（4既有警告，0失败/错误/跳过，与前述重叠），对应session67-release-final.xml。只对这些新差异扩大验证，未重复全部既有阶段。
- 真实已配置模型及实际生成器，3条虚构隔离实验通过：颜文字选择在第一句话后、voice=true；庆祝收藏voice=false；忙碌/重复戳上下文选短话。最后一例只记选择与隔离假客户端，不签具体“不耐烦”程度或真实QQ发送。临时空KB/会话、无写入/搜索/agent，生产配置摘要保持，0真实QQ代发。
- 实际语音清理用FakeTTS入参验证中文正文不含颜文字残留、emoji或命名表情，原speech_text/过滤回归保持；本轮没有独立真人听感签收。内嵌原生face序列/戳目标由mock OneBot和本机源码核验，实际QQ显示及真实戳回以用户反馈为准。

```powershell
python -m pytest tests/test_qq_social.py tests/test_qq_expressions.py tests/test_qq_voice.py tests/test_qq_delivery.py tests/test_qq_presentation.py tests/test_qq_chat_runtime.py tests/test_qq_group_integration.py tests/test_qq_task_scheduler.py tests/test_qq_groups.py tests/test_qq_rollback.py tests/test_conversation_mood.py tests/test_emotion_controls.py tests/test_speech_text.py tests/test_qq_quote_presentation_regressions.py -q --tb=short --junitxml=.runtime/qq-upgrade/session67-expanded-r2.xml --basetemp=.runtime/qq-upgrade/session67-expanded-r2
python -m pytest tests/test_qq_social.py tests/test_qq_expressions.py tests/test_qq_chat_runtime.py tests/test_conversation_mood.py tests/test_emotion_controls.py tests/test_speech_text.py tests/test_qq_quote_presentation_regressions.py -q --tb=short --junitxml=.runtime/qq-upgrade/session67-final-focused-r2.xml --basetemp=.runtime/qq-upgrade/session67-final-focused-r2
python -m pytest tests/test_qq_social.py -q --tb=short --junitxml=.runtime/qq-upgrade/session67-social-final.xml --basetemp=.runtime/qq-upgrade/session67-social-final
node --test tests/qq_favorites_sources.test.cjs
node --check static/js/qq_favorites.js
```

## 部署与继续入口

排空0.02秒、三库一致性备份通过、后端重启3.77秒，保留当前QQ实例及可见管理面板。原24控制/每批5、六媒体revision1、收藏/官方样本、人物/声线/凭据/浏览器/监督自启动选择保持，观察原起点/10缺口/unknown不减，不补24小时。正常关管理面板仍按第66轮停止并核验，不恢复旧关窗留后台行为。

收尾差异通过后进行了第二次同样受控的源码部署，新的release-source/release-backup/deploy-r2单独保留，没有覆盖首次部署或其指纹；第二次同为0.02秒排空/3.77秒后端恢复，QQ实例和原设置保持。全程没有为了验收强关专用QQ或用户日常QQ。

私有session67-before/source-before/final-source、首次和最终测试XML、semantic-model、三库/部署/最终核对保存证据；不公开原身份、私人说明、素材URL或原对话。初次模型隔离脚本缺启用测试设置所需的假认证字段，在模型调用前报错；补齐后实际3请求通过。未向真实QQ注入虚构戳事件或代操作认证。用户只需正常聊天，可选一次真实私聊戳以核对上报；沉默也是合法选择，不要求复杂联合实验。

整体仍第8阶段真实验收。核心实现已接通，不把模型JSON/协议mock/旧传输签收扩大为新自然程度、内嵌显示、真实戳回和声音听感已全过。后续按该事件的状态/授权/决定/真实回执定位，不重新设计已完成配置/媒体/管理器，不恢复固定类别回退或用频率配额假装心情。心情仍是模型表达信号和有界状态连续性，不宣称独立完美的情绪理解。
