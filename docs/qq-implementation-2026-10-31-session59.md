# QQ第59轮：新QQ图片缓存兼容与收藏快照同步

日期：2026-10-31，America/New_York。用户反馈表情页面仍旧、收藏增删不更新、文件图片似乎全面失效；随后明确“保持撤回，只修现有功能”。第56轮官方整包读取不恢复。开工340指纹一致，保留当前installed-QQ 53644实例和原工作区改动。

## 原因和修复

图片问题已用真实消息复现：4条当前可回读的图片（3群引用、1私聊直接发送）get_image返回的文件存在、名称匹配，但位于机器人账号原生`nt_data/Emoji/emoji-recv`，旧白名单只有Pic/File等，读取被应用拒绝。第58轮真实登录/消息回执验证没有覆盖这种53644实际接收缓存，不能用当时回归数字抵销本次失败。

`qq_media.py`只增加当前机器人真实Documents下的`Emoji/emoji-recv`子目录，不信任整个Emoji、其他账号、Tencent父目录或任意消息路径。原身份/会话/引用/名称/大小/像素/静态首帧/取消/权限保护保持。现有目录测试覆盖新增目录及拒绝另一个账号、personal_emoji、日志和其他文件。

收藏问题实际为已保存快照仍10项，真实QQ只读接口返回5项（删除5、新增0）。原“读取本机清单”只读取本机快照，不调用QQ；完整同步逻辑本身可以反映删除。现明确区分“读取已保存清单（不联网）”与“同步机器人QQ收藏”，同步显示当前总数和新增/移除数量；未保存草稿仍阻止覆盖。官方样本标签单独说明不是当前已添加整包清单，不将样本缺席当作删除证据。HTML/JS缓存版本更新为session59-2。

部署后通过原认证/版本化API同步，当前快照已从10项更新为QQ实际5项，保留仍存在表情的指定/说明/分析状态，官方样本来源完整不变。没有增加/删除QQ端任何收藏，也没有重新接入已撤回的官方整包按钮、原生扩展或库存表。

修改：qq_media.py、qq-favorites.html、qq_favorites.js、test_qq_media_native_cache.py、test_qq_favorites.py、qq_favorites_sources.test.cjs。收藏服务/文档解析器/人物提示/媒体开关本轮未改。

## 实验和用户验收

- 修复前真实get_image的文件存在但越界，4条均落在新增接收缓存。修复后在数据库副本完整QQMedia.prepare重放这4条，全部取得静态规范化图像。仅协议读取与副本来源写入，无模型/QQ发送；不能称已验证最终视觉回答显示。
- 私聊近期真实文档通过get_msg/get_file、名称、允许目录及完整prepare/实际解析成功。与图片一起共5例完整回放通过。近期有8个较早来源的旧get_msg不可用，保留诊断缺口，未拿这些历史样本硬凑通过。
- 用户按单项复验在授权群引用之前失败的文档并@机器人，明确回复“正常读取”。后台再核对此次确为群引用PDF、get_msg/get_file和本地文件存在/名称/目录均通过。按该PDF第一行问答范围记真实用户PASS，不扩大为所有群文件格式均逐个通过。
- 8文件112项Python通过，8条既有FastAPI弃用警告，0失败/错误/跳过；最终2项Node及JS语法检查通过。新增收藏增删/存活指定保持、离线读取不假装同步、显式同步增删反馈、草稿保护和官方样本标签测试。

```powershell
python -m pytest tests/test_qq_media_native_cache.py tests/test_qq_external_runtime.py tests/test_qq_favorites.py tests/test_qq_official_packs.py tests/test_qq_media.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_group_document_ids.py -q --tb=short --junitxml=.runtime/qq-upgrade/session59-focused.xml --basetemp=.runtime/qq-upgrade/session59-focused
node --test tests/qq_favorites_sources.test.cjs
node --check static/js/qq_favorites.js
```

首个私有诊断错误使用不存在的received_at列，仅只读查询报错，已改实际id排序后继续，无运行状态修改。浏览器视觉检查未执行，实际HTTP已核对最终HTML版本及按钮文案，模拟DOM不冒充浏览器验收。

## 部署与保持

排空0.03秒、三库一致性备份通过、后端重启约3.17秒，当前installed-QQ实例未重启，ready/running/matched。原安装指针/账号/profile、24项设置/每批5、六媒体开启revision1、API提供商/凭据/浏览器/人物/声线、监督及正式自启动选择保持。有效投递/回执/群/语音/转发及官方样本保持，用户自然聊天计数可变。观察原起点/6缺口/unknown不减，未重置观察。代理无QQ发送或模型/TTS/付费搜索探针，未改生产记忆。

私有session59-before/diagnosis/protocol/favorites/history/5例replay/群PDF确认/三库备份/deploy/XML/final-confirmed及最终源码指纹保留；不公开账号、原消息、素材ID/URL、文件名或正文。主清单为`qq-implementation-manifest-2026-10-31-session59.json`，不覆盖第58轮和更早清单。

## 继续入口

用户刷新表情页后，QQ收藏新增/删除应点“同步机器人QQ收藏”；离线按钮只读已保存快照。当前已实际同步5项，不要求用户再清理QQ收藏、重开媒体开关、重新登录或重做全部实验。

普通图片/收到的表情图片继续走首帧读取；新QQ接收缓存保持精确子目录白名单。群引用PDF本轮用户确认通过，私聊文档/群私图片实际读取回放通过；其他格式和图片最终模型回答的真人待签保持各自范围，不用一个PDF扩大签收。整体仍在第8阶段，其余上下文表情发送/互斥命令待办不变；第56轮撤回、ASR/普通文件输出取消继续有效。
