# 第四十七轮：按用户要求开启媒体并准备真实QQ实验

用户明确询问是否经过实验，要求打开相关开关并给出实机步骤。开工299个第46轮指纹匹配，无业务源码变化，不重做已完成研发或无差异跑1006测试。原自动证据与真实QQ签收分开。

## 已执行

- 真实本机接口检查QQ ready/running/matched、网络/协议可达，视觉模型已配置；本地Qwen/vLLM-Omni已安装、运行、clone模式且空闲，enabled原本true，无需改声线/安装模型/重启或预热。
- 按用户本轮明确授权，经同源主人/QQ设置revision/媒体revision/CAS/confirmed=true的原API开启intake_enabled、images_enabled、quote_images_enabled、documents_enabled、quote_documents_enabled、voice_reply_enabled。媒体revision0→1，原全部限额不改，24个原控制及其revision不变。
- 现有本地TTS实际生成一句无敏感测试语句，3.04秒/24kHz/单声道PCM16/145964字节，耗时约0.81秒；信号RMS2400.46、peak13696，非静音。原TTS配置哈希保持。本次返回ID对应的测试试听WAV/JSON已只删除本次两项，不删其他文件；私有WAV样例保留供用户试听。此为真实本地合成/波形验证，不能自动标主观听感或QQ传输PASS。
- 准备红/蓝/绿测试图、相同四项虚构数据的TXT/DOCX/PDF；实际资源限额解析器均提取Bamboo、Lin Zhou、2030-05-18、PINE42且未截断。材料与哈希保存在私有session47-live-lab及materials记录，用户可直接发送，无需自行造复杂文件。
- 给出[完整短步骤](qq08-live-lab-2026-10-31.md)：先主人好友私聊，一句短回复/不要语音、原图引用正倒序、三格式原文件引用，再可选原已授权群结构化@。每步等回复，不要求旧复杂多窗口/多人取消实验。语音识别与附件输出仍明确未接入。

## 验证与保留

session47-before/enabled/tts-probe/tts-signal/materials/final-verified均有独立私有记录。启用后真实GET重读六项true、revision1；原24设置/继承/人物/凭据/浏览器/声线与监督策略文件SHA-256保持，服务运行/QQ登录正常，TTS运行且空闲。原观察起点/6缺口/unknown不减。

媒体策略表现为1行，来源0/语音投递0，私人ACL0/操作0；没有开启群公共记忆或公开私人条目，没有QQ代发、生产记忆修改、源码部署、后端重启、厂家付费探测或大模型下载。原第46轮1006 Python/40 JS证据继承、业务源指纹一致，本轮未重跑。第44轮真实视觉证据不重复；新增一次真实本地TTS，不混称厂家视觉或QQ实机。

尚待用户实际QQ发送/收听并反馈。不能因为开关开启、单机TTS或私有文件解析通过就把C08真人项改PASS。原QQ05/06/07真人待签保留，ASR和附件输出是后续工程，不虚构已完成。

后续须**保持用户当前六项开启选择**，不得沿用旧“默认关闭/策略表0”预检硬判或恢复false；原默认只描述旧版初始值。精确运行入口session47-resume，新的不可变清单记录本轮文档变化；实验材料、配置/运行证据和声音样例留在.runtime，不入公开源清单。
