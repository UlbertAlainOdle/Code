# 第四十四轮：进入QQ08，图片首批完成并默认关闭部署

用户再次要求继续完成后续工作。本轮274个第四十三轮指纹全部匹配，进入第8/8阶段，先完成图片输入适配；不将语音、文档和附件回复写成已实现，也不将前序真人待验改成PASS。延续第43轮交付的群公共语境/逐条授权/好友审核，原人物设定不改。

## 实现与范围

- 新增QQMedia服务及独立同源主人配置面板/API，接收附件、当前图片理解和引用图片分开默认关闭，策略版本/CAS/严格确认；原24个全局控制不增加字段，不覆盖本机视觉模型路由或密钥。只支持静态JPEG/PNG/WebP；语音、文档、视频、附件输出明确未接入。
- 正常QQ鉴权后入队仅保存媒体类型/是否纯附件，不保存事件URL/本机路径/file token或base64。纯图可被接收为有类型请求，关闭时准确回执；只有URL而无有效图片引用的旧无文字事件仍忽略，伪CQ文字不变附件。图文读取失败保留文字问题。
- 先get_msg重读实际当前消息或明确开启后的同会话引用，核对消息ID、群/好友范围和实际发言者，再对协议产生的opaque引用调用get_image。消息里的任意URL/路径不调用下载器；返回本地文件必须在专用安装/资料目录，拒绝越界/UNC/格式/体积/像素/多帧。拒绝来源不明机器人引用图片，不跨群或借私人消息ID取图。使用本机已安装NapCat4.18.41的get_image/GetFile实现只读核对：ID由内部缓存映射peer/msg/element，并返回下载本地file，不靠猜测远程CDN地址。
- 元数据get_msg/get_image经512KiB解压响应上限，不跟随重定向；图片校验后去元数据并规范化PNG，单张和合计预算计原始/规范化较大值，单解码worker并发，取消后等有界worker落定，晚到不交模型。应用不落图片缓存或永久base64，不删除QQ自身已有缓存；协议下载若已开始不能承诺被远程撤回或由本应用限制其内部缓存大小。
- 复用原ChatWithMotion附件/视觉模型路由，将图与本次文字同一请求发送，实际视觉路由来自现有model_routes.multimodal_model_name，不改提供商或移用密钥。图片文本属于外部DATA，图文轮agent与自动个人写入关闭，纯文字原路径保持；群图仍原隔离/逐条授权，不导入私人网页或私聊历史。
- 任务/策略/来源范围读取中重查；元数据先绑定事件，策略撤销使待发/旧续读失效；清理保留活跃投递所需来源。视觉模型SSE失败给出“未获得可用识图结果”，不猜图、不伪造保存回执。多主人初次管理可手填账号，修复第43轮空选择框首次无法选择多主人的界面入口；群/好友原管理协议保持。

## 测试、失败与真实视觉验证

| 私有证据 | 结果 |
| --- | --- |
| session44-media-first.xml | 18通过/3失败：直接服务夹具没有inbox导致新来源立即被清理，调整为先清理再绑定；缓存测试同因；API夹具错误用started属性代替真实bridge.start，改为实际启动/停止 |
| session44-media-v2.xml | 76通过/2失败：新缓存断言用错字段（应reason）；旧URL-only无文字事件被新入口接收的兼容反例，改为有实际file引用才计有类型图片，不放松URL/路径保护 |
| session44-media-v3.xml | 46通过 |
| session44-media-v4.xml | 50通过，4既有警告；含原生成器真实组装视觉请求与群私密不进入、真实bridge读取中取消、失败回执和图文文字保留 |
| session44-focused.xml | 73通过，8既有警告；新增规范化总预算、活跃来源清理覆盖 |
| session44-final.xml | **79文件901 Python通过，68既有FastAPI弃用警告，0失败/错误/跳过**，冻结最终源码后运行 |
| Node | **38通过，0失败/取消/跳过**；四个变更JS语法与六个Python/服务端脚本py_compile通过 |
| session44-vision-probe.json | **真实现有视觉接口一次请求HTTP200，正确识别自制红/蓝/绿三色顺序**；仅自制360×120图片和问题，无私人聊天/记忆，原文件哈希保持，未重试 |

Python：python -m pytest，最终在第43轮77文件基础增加test_qq_media.py、test_qq_media_integration.py，-q --tb=short与独立JUnit/basetemp；参数原样保存在私有session44-final-command.json。聚焦命令：python -m pytest tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_group_integration.py tests/test_qq_task_scheduler.py tests/test_qq_delivery.py tests/test_qq_rollback.py -q。Node命令在第43轮七文件基础加入tests/qq_media_ui.test.cjs。各范围重叠不相加。

自动集成使用临时真实KB/原生成器和fake协议/模型，不是真QQ收发。另有一次新视觉厂家验证，也不是真QQ，更不代表中文OCR、扫描件和任意视觉模型全覆盖；原成功Search/Fetch未重做，未为ASR下载大模型。此次新增厂家调用总数1，不得沿用前轮“0付费探测”描述本轮总数；部署脚本自身无厂家调用，其字段0仅指部署操作。

## 部署与保留

session44-deploy与final-verified均PASS。正常排空0.03秒、三库一致性备份/完整性ok，仅自有后端正常重启约4.33秒，原专用QQ进程保留；ready/running/matched，协议/网络可达。原24控制/继承/共享/凭据/人物/浏览器/运行控制与监督自启动保持；memory_management_enabled仍false，群及图片新策略均未开启。7份HTTP静态资源、媒体只读管理面和现有视觉路由配置核对通过。

未过期8结果/19段/8策略、1授权群保持；新媒体策略/来源表0，群/好友新表0，私人ACL0/操作0/投影待对账0。观察原起点/6缺口/unknown不减。无代理QQ代发、生产记忆写入/授权、好友审批或新开群/图片功能制造验收。未触及用户正在打开的NapCat登录页或浏览器草稿。

源码新增qq_media.py、qq_media.js及两Python/一JS测试；修改bridge/chat/rollback、qq路由、服务端挂接、HTML、全局表单排除及群/好友主人输入。人物原始提示AST和core_services.py完全保持；完整路径与指纹见最新清单。

当前为**QQ08图片首批工程完成、整体多媒体阶段进行中**。使用图片需用户在本机QQ图片面板读取并明确开启，真实QQ图片待签；文档、TTS发送、ASR和附件输出是明确剩余工程，入口及依赖见[QQ08续接](qq08-integration-2026-10-31.md)与[验收](qq08-acceptance-2026-10-31.md)。QQ05/06/07原真人待签继续独立保留，不重开已完成研发或旧复杂实验。
