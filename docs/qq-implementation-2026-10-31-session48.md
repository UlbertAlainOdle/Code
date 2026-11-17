# 第48轮：修复QQ图片和文档实机读取失败

用户反馈第47轮实机步骤2和3均提示附件读取失败。本轮先核对第47轮302文件指纹全部一致，再读取既有真实QQ消息定位问题；保留无关工作区改动，不继续新增ASR或附件输出。

## 原因与修改

原自动测试使用了简单图片标识和安装目录中的模拟缓存，没有覆盖真实NapCat图片原始文件名中的标点，以及Windows系统Documents重定向后的QQ原生缓存。第44轮真实视觉模型请求只证明模型识图，不证明QQ附件协议可读。这是既有验证覆盖不足，用户实机失败按失败保留。

- `saya_app/services/qq_media.py`：兼容真实图片basename中的中文、标点；仍拒绝URL、协议、路径、控制字符和遍历。协议返回图片文件名存在时核对原标识。通过Windows SHGetFolderPath读取系统真实Documents位置，包括OneDrive重定向；在原专用安装/资料目录外，仅增加当前机器人账号Pic/File和NapCat固定temp目录，不信任整个Documents、Tencent Files或其他账号。会话重读、群/来源验证、路径解析后范围检查、尺寸/总字节/解码/解析及撤权取消保持。
- 图片作为PNG文件卡片发送时仍不按文档解析，改为明确提示应通过QQ图片消息发送；文档支持TXT/PDF/DOCX。失败日志只记录适配器原因码，不记录异常原文、路径、账号或正文。
- `tests/test_qq_media_native_cache.py`：补图片真实字符、路径/URL拒绝、重定向缓存、其他账号/目录/越界/体积拒绝、图片名称不符及PNG文件发送指引回归。
- 更新状态、QQ08验收/继续入口/实机步骤及本报告，新增第48轮不可变清单；不覆盖旧清单。

## 实验与检查

初始真实协议回读复现`invalid_image_reference`和`cache_path_outside_dedicated_installation`。修复后使用既有真实消息实际调用get_msg/get_image/get_file，在生产数据库副本运行完整QQMedia.prepare：私聊图片、私聊引用图片、群引用图片及TXT引用均成功；另一个PNG文件卡片得到新的明确提示，共5个回放用例通过。媒体字节未发送到模型，没有QQ代发，生产来源表不由回放写入。DOCX/PDF本轮只沿用实际限额解析及隔离链路的自动证据；不得声称已通过真实QQ三格式收发。用户反馈的实验2/3失败保留，修复后的最终QQ回答仍待复验。

首轮聚焦70项通过；最终修改后运行下列137项全部通过（12条既有FastAPI弃用警告，0失败/错误/跳过；两轮重叠不相加）。无JS业务变化，不重复40项历史JS检查，也不将第46轮1006项计成本轮执行结果。

```powershell
python -m pytest tests/test_qq_media_native_cache.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_voice.py tests/test_qq_voice_integration.py tests/test_qq_quotes.py tests/test_qq_rollback.py tests/test_qq_service_drain.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py -q --tb=short --junitxml=.runtime/qq-upgrade/session48-regression.xml --basetemp=.runtime/qq-upgrade/session48-regression
```

## 部署与保持

正常排空约0.02秒，包含voice_rendering后台计数；三库一致性备份通过，后端重启约3.12秒恢复ready/running/matched，原QQ进程保持。保留24项原全局设置、继承设置、人物/风格/声音与参考素材、凭据、本机配置和浏览器归档、监督及自启动选择。媒体策略revision=1、六项保持开启；有效投递/分段/选项/群/媒体来源/语音记录保持。原观察起点及6次采样缺口保留，unknown不减少，无制造24小时通过。

部署后独立重读配置/健康与测试源码hash通过；检查时媒体策略1、来源2、语音记录7，私人ACL/记忆操作均0。记录数会随用户聊天自然变化，不能以旧0行断言覆盖用户数据。未由代理发送QQ、修改生产记忆、发起付费模型探测或安装新依赖。

私有证据位于`.runtime/qq-upgrade/session48-*`，包括before、协议原始数据（不得公开）、diagnosis/replay、最终测试XML、源码冻结、deploy/三库备份和final-verified。公共报告不含真实账号、用户附件内容、原缓存路径或凭据。

## 恢复工作

先读取状态和第48轮清单。修复已上线，用户无需重启或重新开关。按[短实验说明](qq08-live-lab-2026-10-31.md)先重发一张图片（QQ图片消息）及一份TXT文件，再引用原消息提问；如仍失败，读取不含内容的原因码并继续定位，不能先推进其他阶段。当前第8/8阶段仍进行，ASR与附件输出未实现，旧QQ05/06/07真人待签独立保留。
