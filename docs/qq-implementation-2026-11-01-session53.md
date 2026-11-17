# 第53轮：现有收藏语义选择澄清与长回答合并转发交付

用户强调表情功能须读取指定已收藏表情的信息并按语境发送，不是自动收藏。本轮已写入权威修订范围：只读现有收藏，主人指定后做图像/文字语义档案，模型按当轮语境选合法ID或不发，服务端验证后发送；不新增/删除/修改QQ收藏。该语义选择/发送和互斥命令尚未实现，10项只读盘点不算实现。

317开工指纹一致，本轮按既定顺序先完成长回答自发后合并转发。新增qq_forward.py和test_qq_forward.py；修改qq_bridge.py接原QQ03结果投递、qq_delivery.py清理派生状态、qq_media.py能力描述、qq_rollback.py最低读者要求。人物/声线/收藏内容不改。

## 实现

- 仅新生成chat final、超过5个气泡或整份正文超过5倍当前bubble_chars触发，短答、控制、旧缓存续读不改。复用已有可见正文/回执，不再生成或写记忆；长代码即使少于5气泡也按正文长度进入。
- 将正文重排成有界自发消息，逐条发到机器人自身账号并取得真实回执，然后以这些回执作为ID节点，向原私聊或群发送原生合并聊天记录。没有模型控制的用户名/路径/CQ节点，没有发到其他暂存账号；转发成功不另附语音。
- 独立qq_forward_results/qq_forward_sources持久化源及目标状态。源上传失败/不明不再重试自发，降级原文字/续读；目标发送不明阻止普通文字补发/续读，避免重复。重启中断目标标unknown，源sending标unknown；取消、媒体/记忆/群权限、期限、范围和原收件身份检查在步骤间执行。已开始的上传不能保证物理撤回。
- 目标确认后才将原分段标sent，原历史/回执逻辑继续；过期、清空范围时删除派生正文。非空新表要求qq_self_merged_forward兼容能力，拒绝忽略状态的旧读者回退。保留每批5条及原其他设置。

## 实验

用户已授权这种自发/转发方式。本轮只用无私人内容的测试文本向机器人自己实际发1条文本，再向自己实际发1条合并转发，0次其他收件人发送、0模型或TTS探测。确认原消息回执、转发回执、com.tencent.multimsg卡片及get_forward_msg中的两段正文和顺序。初版探针错误期待forward段，QQ实际返回聊天记录JSON卡片；后续仅只读查卡片resid验证内容，没有重发。原失败探针证据保留，不把它当发送失败而重试。

原自动首轮5通过/1失败是测试将“已完成后继续说”误当旧结果续读，改为显式原结果/continue验证不重新生成；原自然语言继续逻辑未改。随后19文件247项扩展回归通过（12条既有FastAPI弃用警告），最后增加长结构化内容触发和到期/回退清理边界，6文件81项最终聚焦通过，均0失败/错误/跳过；两轮重叠不相加。覆盖真实ID节点协议、完整bridge→自发→目标、源/目标超时未知、范围伪造、撤权及重启阻断、清理及回退门禁。模拟目标不等于真实用户/群签收。

```powershell
python -m pytest tests/test_qq_forward.py tests/test_qq_media_phase52.py tests/test_qq_natural_bubbles.py tests/test_qq_quote_presentation_regressions.py tests/test_qq_presentation.py tests/test_qq_delivery.py tests/test_qq_group_document_ids.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_media_native_cache.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_qq_quotes.py tests/test_qq_rollback.py tests/test_qq_service_drain.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session53-verified.xml --basetemp=.runtime/qq-upgrade/session53-verified
python -m pytest tests/test_qq_forward.py tests/test_qq_media.py tests/test_qq_delivery.py tests/test_qq_voice.py tests/test_qq_rollback.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session53-extra.xml --basetemp=.runtime/qq-upgrade/session53-extra
```

## 部署和保持

排空0.01秒/三库一致性备份通过/后端重启3.12秒，原QQ进程不变，ready/running/matched。24项设置（包含每批5条）、六媒体开启revision1、人物/声线/参考/凭据/浏览器/监督自启动保持；有效投递/群/来源/语音和新增转发表保持，原观察起点及6次缺口/unknown不减。独立最终健康/配置/源码检查通过。验证时转发表0/0，用户真实长回答会自然新增，不得要求清零。未向生产个人记忆写入或改授权，未安装依赖。

私有session53-before/final-source/测试XML/self-probe/只读纠正验证/deploy/备份/final-verified，不公开真实回执、QQ身份、卡片resid或内容日志。新清单增加qq_self_merged_forward最低兼容声明。

## 下一步

先给现有收藏提供主人指定入口、受限图像读取和可审阅语义档案，再接上下文选择/真实发送及语音表情互斥命令。不得调用收藏新增/删除接口。长回答的最终用户/群显示可用“分八段、每段至少50字解释一个话题，不要表格”自然提问验收，应收到原生聊天记录卡片，不另附语音。仍在第8阶段，收藏语义/表达互斥未完；已取消的ASR/普通文件输出不再待办，历史真人待签不冒充通过。
