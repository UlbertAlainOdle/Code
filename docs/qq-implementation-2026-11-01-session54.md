# 第54轮：合并记录分节点与指定收藏语义档案入口

用户反馈合并记录已显示，但内部仅一条消息，允许根据判断小改，并要求继续。后续“继续”承接本轮未完成工作；最新已发布状态此前为第53轮。本轮先核对第53轮321文件指纹全部匹配，保留既有未提交改动，不重做已完成长转发/媒体接入。

## 完成内容

- qq_forward.py不再把正文重打包到2200字符一个节点，改为保留自然完整段落、每块最多约900字符，过多节点再有界打包；结构化代码保持必要连续性。测试原9段对应9个自发节点，原真实ID引用转发/状态机不变。用户已确认原卡片可见，但未逐项签收群/私聊及新分节点效果，不扩大真人通过范围。
- 新qq_favorites.py：只读fetch_custom_face_detail、48项/512KiB元数据限制；本机QQ目录有独立revision/事务CAS，刷新时相同资源保留选择及说明、消失或变动的资源撤销，默认不选任何项。指定与语义档案不写个人长期记忆。新增非空目录回退要求qq_curated_favorites，旧读者不得忽略。
- 预览仅从认证QQ收藏清单中的p.qpic.cn地址读取，固定HTTPS、不跟重定向、不用环境代理、8MiB/16MP限制、单解码锁取消后等有界工作结束，动图只取首帧、转PNG去元数据。浏览器不接收原私有CDN URL/QQ缓存路径；仅本机主人登录可查看PNG。
- 指定后点击分析，调用当前已配置视觉模型和原生成器，临时无会话/不读私人记忆/不写记忆/无agent/无搜索，实际图片首帧作为视觉输入。产出画面/文字含义、情绪、适合及避免语境的≤1200字符建议说明，可手动修正。清单变化/撤销导致迟到结果失效，不覆盖草稿。未选择不分析，不批量自动付费调用。
- 新本机页面`/static/qq-favorites.html`、qq_favorites.js，QQ接入页增加入口。主人可读取/刷新、逐项预览、勾选保存、逐张分析/编辑；页面明确自动发送未实现，QQ收藏不会新增、删除或修改。原有单主聊天页面占用规则保持，页面不创建主聊天实例。
- qq.py增加本机主人/确认/版本保护API；qq_bridge.py启动本地目录服务及受限元数据调用；qq_rollback.py声明保护。未改人物/心情/声音设置。自动语境选择与实际表情发送、QQ内联小表情/颜文字互斥、语音互斥命令仍未完成，不能将档案入口称为完整表情发送功能。

## 检查与实验边界

20文件258项扩展检查通过（16条既有FastAPI弃用警告），最后6文件73项聚焦通过（4条既有警告），0失败/错误/跳过，重叠不相加。JS语法node --check通过；浏览器实际外观/交互尚未做人工或截图验收，不用HTTP成功冒充浏览器QA。

初期两次聚焦失败分别是测试未模拟运行中bridge导致503、以及使用不存在的role_mode请求字段；对照真实ChatWithMotion后移除无效字段并用真实schema/生成器验证。后来新增真实生成器测试时测试夹具误patch不存在的conversation_service.kb，已对齐既有隔离方式；保留初始失败记录，不改安全断言换PASS。最终生成器经mock HTTP收到视觉模型及image_url输入，私人记忆样本没有进入请求；该证据为隔离测试，不是真实付费视觉分析。

```powershell
python -m pytest tests/test_qq_favorites.py tests/test_qq_forward.py tests/test_qq_media_phase52.py tests/test_qq_natural_bubbles.py tests/test_qq_quote_presentation_regressions.py tests/test_qq_presentation.py tests/test_qq_delivery.py tests/test_qq_group_document_ids.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_media_native_cache.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_qq_quotes.py tests/test_qq_rollback.py tests/test_qq_service_drain.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session54-final.xml --basetemp=.runtime/qq-upgrade/session54-final
python -m pytest tests/test_qq_favorites.py tests/test_qq_forward.py tests/test_qq_media.py tests/test_qq_delivery.py tests/test_qq_rollback.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session54-final-focused.xml --basetemp=.runtime/qq-upgrade/session54-final-focused
node --check static/js/qq_favorites.js
```

扩展258项和最后聚焦存在重叠，新增真实生成器用例及刷新/解码边界改动后运行最终聚焦，不能把258称为最终版本的全量重跑。

实际认证QQ清单读取10项，在隔离数据库副本/目录成功读取其中一项CDN图片并规范为PNG（约279KB），未选择/调用模型。部署后通过本机owner API刷新生产目录，catalogue_revision1、10项全部未选，真实预览端点PNG和页面HTTP200通过。0次QQ发送、0收藏变更、0付费模型/TTS探测；未代用户指定或分析私人表情。原账号、URL/文件路径/图片内容不放公共报告。

## 部署和保持

排空0.05秒（包括voice_rendering）、三库一致性备份通过、后端重启3.12秒，原QQ进程保留，ready/running/matched。原24设置/每批5条、六媒体开启revision1、人物/声线/参考、凭据、本机配置与浏览器、监督自启动保持；有效投递/转发/来源/语音记录保持。原观察起点、6缺口及unknown不减。新增生产目录1行含10项全部未选，不能用历史空目录断言重置用户之后选择。

修改源码：qq_favorites.py、qq_forward.py、qq_bridge.py、qq_rollback.py、qq.py，static/qq-favorites.html、static/js/qq_favorites.js、static/qq.html；测试test_qq_favorites.py/test_qq_forward.py及相关文档。私有session54-before/preview-probe/测试XML/final-source/deploy/备份/live-page/final-verified/resume保留。

## 下一步

用户可打开`http://127.0.0.1:8000/static/qq-favorites.html`，读取清单（已刷新10项）、点预览、勾选保存，再点逐张分析；不要让用户在聊天发送凭据。指定为发送候选的意向，不代表现阶段已自动发送。下一轮实现基于已指定且有语义说明候选的上下文择一和原收藏实际发送，并统一语音/三类表情策略/命令；用户未选期间可继续独立开发，不能自行全选。仍第8阶段，ASR及普通文件输出继续取消。
