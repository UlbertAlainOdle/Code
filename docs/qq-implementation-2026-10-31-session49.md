# 第49轮：群文件原生UUID兼容修复

用户确认图片在私聊及群聊均正常，私聊文件有效，群文件仍未生效。本轮将图片及私聊文件按用户报告范围记录真人通过，未扩大为所有文档格式或语音听感通过。群文件失败保持待修复复验状态，不重做第48轮已通过的图片缓存修复。

开工第48轮305文件指纹全部一致；保留无关工作区、人物、声音、凭据及当前六项媒体开启选择。

## 定位与改动

只读重取既有群文件引用，get_msg的消息/群/来源检查通过，随后在document_refs复现`invalid_document_reference`。私聊文件使用opaque ID，实际群文件则使用`/`加规范36字符UUID；原正则拒绝了这个群专用表示。

- `saya_app/services/qq_documents.py`：document_refs增加默认关闭的group参数；仅在group=True时接收精确的`/8hex-4hex-4hex-4hex-12hex`协议ID。原私聊opaque ID及原文件名/格式规则保持。该ID只传给get_file，不能作为本地路径打开，不采用文件名回退。
- `saya_app/services/qq_media.py`：在get_msg核对会话、消息ID及来源后，传入当前是否群会话。原群@唤醒、白名单、引用/附件开关、跨群拒绝、文件名核对、缓存目录/体积/解析/撤权取消保持。
- 新增`tests/test_qq_group_document_ids.py`：群引用及含结构化@的直接群文件消息的原生UUID、跨群同名来源拒绝、任意路径/URL/伪UUID拒绝、私聊不接收群例外，共11项。
- 更新状态、验收、继续入口、实机步骤、本报告与不可变清单。未改搜索、人物、声线、记忆权限或其他业务代码。

## 检查与证据边界

真实既有QQ消息实际调用get_msg/get_image/get_file，在生产数据库副本运行完整QQMedia.prepare。本轮8例通过：7例成功读取（含之前失败的群TXT引用、私聊文件、群及私聊图片），另1例PNG文件卡片获得既有明确提示。群TXT经过实际资源限额解析器提取正文。无模型调用、无代理QQ发送、无回放导致的生产来源表写入；不能将其描述成修复后最终群聊回答已验收。

以下13文件148项全部通过，12条既有FastAPI弃用警告，0失败/错误/跳过。无JS改动，不重跑历史40项或将历史1006项计为本轮执行。

```powershell
python -m pytest tests/test_qq_group_document_ids.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_media_native_cache.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_qq_quotes.py tests/test_qq_rollback.py tests/test_qq_service_drain.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session49-regression.xml --basetemp=.runtime/qq-upgrade/session49-regression
```

## 部署及继续

正常排空0.03秒（含voice_rendering），三库备份完整性通过，后端重启3.14秒恢复ready/running/matched，原QQ进程不变。原24设置/继承/人物/声线及参考素材/凭据/浏览器/监督自启动选择保持；媒体六项仍开启、revision1。有效投递、分段、群、媒体及语音状态保持。原观察起点、6次缺口及unknown不减少；未重置观察或制造24小时通过。

部署后独立健康/配置/源码指纹复核通过。检查时媒体策略1、来源5、语音记录7、私人ACL及记忆操作0；计数随用户聊天可正常变化，不使用历史空表断言覆盖。无代理QQ发送、生产记忆修改、新模型合成/付费探测或依赖安装。

私有证据`.runtime/qq-upgrade/session49-*`包含before、diagnosis/protocol-private、replay数据库副本、回归XML、final-source、deploy/备份与final-verified，不公开真实群号、文件ID、原始内容、缓存路径或凭据。

下一步仅请用户在原授权群引用原文件消息并从成员列表@机器人，要求读出内容，记录修复后的最终回答。用户无需重复图片或私聊实验、重启、重新开关或开群权限。若仍失败，先按该失败定位，不能直接推进ASR。当前第8阶段仍进行，ASR/附件输出、语音听感及未逐格式报告的文档验收保持待完成；原QQ05/06/07真人待签独立保留。
