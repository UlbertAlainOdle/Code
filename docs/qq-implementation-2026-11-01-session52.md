# 第52轮：用户修订QQ08范围与首批交付

开工第51轮313文件指纹匹配。用户取消ASR和普通文件附件输出，新增自发后聊天记录合并转发、指定QQ收藏表情及三类优先、动图首帧、自然附件失败说明、互斥自主/随机/指定表达命令、最多5气泡。[修订范围与继续顺序](qq08-revised-scope-2026-11-01.md)为后续权威入口，不能继续按旧ASR待办开发。

本轮完成并部署动图首帧、实际读取失败经原角色自然说明、去掉为凑条数合并完整段落，并将线上每批从3调5。长回答合并转发、收藏指定界面/发送、统一表达选择及命令仍未实现；不是整个第8阶段完成。用户确认素材来源是机器人QQ收藏，实际只读盘点到10项，不默认全部授权发送。

修改业务文件qq_media.py（GIF/动态WebP/APNG帧零转PNG、单帧静态证据、范围元信息）、qq_chat.py（实际读取失败通过无工具/自动写入的现有文本生成器，状态约束不得猜图，不重复附固定告警）、qq_delivery.py（取消为凑batch合并完整段落）；更新test_qq_media.py使旧动画拒绝改为用户新要求下的首帧接收，新增test_qq_media_phase52.py覆盖3种动图、长段落和失败自然生成。代码/列表/表格保持安全排版，短碎片合并保持。人物、原风格/声线不改。

首轮239通过/1失败暴露“关闭接收不应调用模型”的既有约束，已用read_attempted区分关闭/未配置与真正尝试读取失败，保持前者不调用模型，未改测试绕过断言。最终18文件240项通过、12条既有弃用警告、0失败/错误/跳过。实际现存QQ GIF缓存解码首帧转PNG，与原首帧RGB逐像素匹配。失败自然回复测试使用模拟模型，非真实QQ生成验收；没有重复付费模型探测或代理QQ发送。

```powershell
python -m pytest tests/test_qq_media_phase52.py tests/test_qq_natural_bubbles.py tests/test_qq_quote_presentation_regressions.py tests/test_qq_presentation.py tests/test_qq_delivery.py tests/test_qq_group_document_ids.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_media_native_cache.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_qq_quotes.py tests/test_qq_rollback.py tests/test_qq_service_drain.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session52-final.xml --basetemp=.runtime/qq-upgrade/session52-final
```

每批5条通过原CAS设置接口保存，只改变reply_max_messages（首次部分字段请求422未写入，随后携完整当前设置及revision保存通过）。部署排空0.03秒、三库备份完整性通过，后端重启3.28秒，原QQ进程保留、ready/running/matched。原其余23设置/继承/六媒体开启revision1/人物/声线/凭据/浏览器/监督自启动保持，有效投递和来源/语音记录保留，原观察起点及6缺口/unknown不减。最终独立检查通过。

私有session52-before/authorized-settings/favorites-private/favorites-summary/final-source/deploy/final-verified/replay及测试XML保留，不公开收藏URL、文件路径、QQ身份或用户正文。无新依赖、TTS合成、QQ代发、生产记忆修改或私人授权变化。图片双端及私聊文件既有用户确认保持，其他真实QQ待验不代签。

继续先做合并转发，再收藏指定和统一表达互斥命令；保留当前5条选择，不恢复3，不重做ASR或普通附件输出。默认旧语音逻辑仍有效，不能对用户声称自主/随机表达已上线。
