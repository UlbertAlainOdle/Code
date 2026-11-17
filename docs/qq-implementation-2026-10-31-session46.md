# 第四十六轮：QQ08短回复附加本地语音完成并默认关闭部署

用户要求继续完成。本轮289个第四十五轮指纹全部匹配，按入口接可选TTS。当前仍第8/8阶段，图片、文档和短回复附加语音工程已接；ASR和附件输出未实现，前序及真实QQ媒体签收独立保留，不标整体PASS。

## 实现

- 新增QQVoice：只对新生成且文字所有段落已确认发出的短回答，独立开关允许时附一次本地语音；默认先文字，控制/保存回执/续读/legacy、长文/代码/链接和当次“不要语音”不生成声音。文字保持可读，语音失败不触发新模型或改写已确认文字为发送不明，不添加常规进度刷屏。
- 复用现有LocalTTS及原声音、参考、语言/韵律配置，增加纯内存synthesize_ephemeral，不调用发布网页试听的接口。Omni限制下载字节；transformers worker增加ephemeral_audio能力和纯字节输出，在写文件/元数据/清理试听之前返回。旧运行worker不支持此协议就文字降级，等待正常空闲重载，不改原设置/强停服务或偷用旧公开试听路径。临时请求最多1024生成token，不保存该覆盖，不向云TTS移用QQ正文或密钥。
- 独立持久投递记录只存结果ID/范围/策略与声音版本哈希/回执/状态/期限，不存朗读正文。rendering→ready→sending→sent；无有效数字回执不成功。未知/重启sending→unknown，rendering/ready→interrupted，均不重试；只按原已认证结果的账号/群发送record，原生自发消息仍过滤，避免回环。
- 本轮只承诺单声道PCM16、8～48kHz、单个2MiB、默认120字符/30秒/等待45秒；声线或参考版本、群记忆ACL、来源/结果/策略变化及取消都再检查。状态等待后也检查，防止取消后才开始新合成。原任务的文字/回执已经发出不能撤回，不伪称全部取消。
- 不为单个QQ取消关闭共享TTS。已开始的同步合成可能自然结束，迟到字节丢弃，单个后台render锁阻止无限排队；超时/取消可释放QQ任务，voice_rendering仍计入正常排空，不能忽略它强制升级。原TTS自身超时/关闭行为不改，原声线/参考/人物保留。
- 私有临时音频在.runtime，非网页公开资产；最多32个/32MiB，默认300秒TTL并每30秒清理，到期删除但保留防重放状态至结果过期。发送不明文件暂留给已开始的协议上传，不重复发；缓存满时文字降级，不删除别人待上传文件腾位置。
- QQ媒体面板新增独立语音开关和限额，不隐含附件读取，保持原24全局控制。/help不再误称只有文字。回退预检新增qq_voice_reply；旧程序不能忽略新状态继续发。已只读核对当前NapCat本机file URI解码和非silk转NT-silk路径，不以此代替实际QQ收听。

## 测试及失败记录

| 证据 | 结果 |
| --- | --- |
| session46-voice-first.xml | 8通过/11失败：新语音服务误把原投递canonical字段写作body，导致可选语音进入前被失败降级保护拦住；改为原真实字段。保留失败，不误称已发语音 |
| session46-voice-v2.xml | 19通过；真实bridge先文字后音频、取消/撤权/声线改版、格式/体积/时长/未知、去重/重启/TTL/账号隔离/自回复过滤 |
| session46-tts-first.xml | 45通过；本机原TTS/关闭、真实worker分支AST配测试模型、Omni有界HTTP、旧worker能力拒绝及无公开试听资产；早于后续取消状态等待细化 |
| session46-focused.xml | 75通过，4既有警告；新增状态等待后取消不启动模型、原生成器一次模型/一次语音、文字后群记忆撤权阻止迟到语音、缓存配额及网页语音回归 |
| session46-final.xml | **88文件1006 Python通过，76既有FastAPI弃用警告，0失败/错误/跳过**，最终源码冻结后执行 |
| Node与语法 | **40 JS通过，0失败/取消/跳过**；变更JS检查、8个相关Python/启动脚本编译通过 |

最终Python在第45轮81文件基础加入test_qq_voice.py、test_qq_voice_tts.py、test_qq_voice_integration.py及原test_local_tts.py、test_voice_shutdown.py、test_speech_prosody.py、test_speech_text.py，python -m pytest -q --tb=short，独立JUnit/basetemp；完整参数私有session46-final-command.json。聚焦即这七文件。Node沿用八文件命令，包含qq_media_ui.test.cjs；原样命令与范围见前轮报告及本轮私有记录。各范围重叠不累加。

单元测试使用生成的测试WAV、fake TTS/OneBot及实际worker分支/原生成器，不是实际GPU发音或真QQ语音收发/听感。没有主动调用生产TTS生成验收音频、代发QQ、做付费厂家验证或安装新依赖；原启动预热选择保留，不将原服务自动预热算作新真人证明。

新测试的多行with写法在执行前修正，未产生额外有效JUnit；初始额外快照/路由复制父目录缺失的操作已修正。omni/worker额外快照由精确本轮补丁反向重建，原清单未覆盖，不能冒称旧清单指纹证据；现已纳入最新清单。路由反向快照与第45轮哈希完全匹配，其余变更文件直接开工快照。未重置无关改动或改写历史清单。

## 部署和边界

session46-deploy/final-verified均PASS。正常排空0.02秒、三库备份完整性ok，后端正常重启约4.30秒，原专用QQ进程保持。排空脚本新增voice_rendering检查，并将原TTS设置/当前参考音频和元数据加入保留哈希与私有备份。ready/running/matched，协议/网络可达，7份HTTP静态资源与媒体只读能力核对通过。

原24控制/继承/共享/人物/凭据/浏览器/监督自启动保持，修改及群/图片/文档/语音开关均仍关闭；当前8结果/19段/8策略/1授权群保持，媒体策略/来源0行，语音记录0/临时音频0，群/好友表0、私人ACL0/操作0/投影pending0。原观察起点/6缺口/unknown不减。没有生产记忆写入/授权、审批或QQ代发。

新增qq_voice.py及三个测试；修改local_tts.py、omni_tts.py、qwen_tts_worker.py、qq_bridge.py、qq_media.py、qq_rollback.py、qq路由、服务端挂接、HTML、qq_media.js及其测试/进度文档。原人物常量AST和core_services.py字节保持。完整路径和哈希见新不可变清单。

下一步ASR和附件输出；长回复/云TTS/替代文字语音模式属于额外范围，不作为重做本批工程的理由。真实TTS发音与QQ媒体签收保留待验，用户明确开启且现有本地Qwen可用才会尝试。见[第8阶段验收](qq08-acceptance-2026-10-31.md)与[继续入口](qq08-integration-2026-10-31.md)。
