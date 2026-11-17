# 第四十五轮：QQ08 文档输入完成并默认关闭部署

用户要求继续完成。开工283个第四十四轮指纹全部匹配，按照最新入口继续TXT/PDF/DOCX，不重做已完成QQ06/07或图片/真实视觉接口探测。仍为第8/8阶段，整个多媒体阶段未完成；语音和附件输出保留未实现，真人QQ验收独立。

## 实现

- 新增QQDocumentParser及独立qq_document_worker.py，读取有界UTF8/UTF16/GB18030 TXT、有文字层PDF、DOCX正文段落和表格。默认2份/每份5MiB、PDF20页、12000字符，文本总量24000字符，图片与文档共用总字节限额；超文本限额标为摘录，不伪称全文。
- 解析单并发，专属Python -I进程，仅stdin字节，不传用户路径、凭据或完整父进程环境；Windows Job或POSIX限额先设置再读输入，256MiB、CPU5秒、墙钟默认8秒。父进程50ms检查原QQ任务权限/取消，只结束自己创建的子进程，超时/取消/撤权均收尾，无遗留parser active。真实单独进程分配300MiB被内存限额阻断，不是只检查常量。独立资源控制依据[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)；PDF内容流可能远大于文件大小，不能仅靠上传字节限制，见[pypdf说明](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)。这里是资源限额进程，不声称操作系统权限沙箱。
- 沿用实际当前消息/同会话引用get_msg核对，再get_file(file_id)。只认协议file_id，不用同名文件别名、用户URL或路径，防止同名文件跨群串线；返回file_name与原来源一致，文件仍须在专用资料/安装目录。已只读核对当前NapCat4.18.41的file段与get_file实现，不按过时假想字段接线。群文件仍需引用并结构化@，group_upload通知不自动扫描。
- PDF严格类型、加密/超页/无可提取文字拒绝，只有文字层不做扫描OCR；DOCX不解压到磁盘，成员/路径/总展开/XML限额，宏、嵌入、实体拒绝，XML只读取正文和类型。worker网络禁用，不执行文档命令、宏或外部资源。不承诺DOCX页眉页脚/批注/图表、PDF视觉排版或中文OCR全覆盖。
- 媒体服务新增文档与引用文档独立开关/限额，旧存档补默认false；部分旧客户端更新合并当前策略，不能无意清掉新文档选择。原24全局控制不增加字段，图片与文档各自开启。界面改为“QQ附件接收（图片与文档）”，未接的旧后端禁用文档控件。
- 提取文本和文件名/原发言者/消息ID/可信原时间作为当前外部DATA交原生成器，文档可用普通文本模型，不要求视觉路由；媒体轮继续关闭agent/自动个人写入。原始提取文本不作为用户历史追加，正常模型回答按既有会话规则保留；二进制只在请求/管道中，无新原文缓存或永久base64。群文件不读私人历史/记忆。策略/来源保护接续图片原事件与投递链，旧续读撤权失效，普通文本引用不再附加图片缺失告警。
- 回退能力预检补qq_media_documents，旧程序不能忽略新文档策略/来源运行；关闭开关不是删数据或回写旧库。

## 验证与失败

| 证据 | 结果 |
| --- | --- |
| session45-images-first.xml | 原图片/原生成器26通过，4既有警告 |
| session45-documents-first.xml | 22通过/2 setup-teardown错误：新参数化自动将完整DOCX字节放进测试ID，Windows PYTEST_CURRENT_TEST超32767字符；改成短显式ID，不改业务规则 |
| session45-documents-v2.xml | 23通过；含三格式真实解析、编码、类型/加密/页/zip/XML/宏/嵌入、实际内存限额、协程取消与来源范围 |
| session45-combined-first.xml | 53通过/3新集成夹具失败：错误读取不存在的kb.knowledge；改为断言临时KB文件字节完全未变，保留真实不写入目标 |
| session45-integration-v2.xml | 7通过，4既有警告；三格式真实bridge→原生成器→fake模型、群私人隔离；/cancel/撤权终止实际parser，超时清理，旧源码能力拒绝 |
| session45-focused.xml | 89通过，8既有警告 |
| session45-final.xml | **81文件931 Python通过，72既有FastAPI弃用警告，0失败/错误/跳过**，冻结最终源码后运行 |
| 前端 | **39 JS通过，0失败/取消/跳过**，含文档独立授权/CAS/草稿与原功能；JS语法和6个变更Python模块编译通过 |

各范围重叠不累加。Python最终命令为python -m pytest（第44轮79文件加test_qq_documents.py、test_qq_documents_integration.py）-q --tb=short，独立JUnit/basetemp，原样参数存私有session45-final-command.json。聚焦：python -m pytest tests/test_qq_documents.py tests/test_qq_documents_integration.py tests/test_qq_media.py tests/test_qq_media_integration.py tests/test_qq_quotes.py tests/test_qq_rollback.py -q。Node：node --test tests/qq_media_ui.test.cjs tests/qq_group_contacts_ui.test.cjs tests/qq_memory_management_ui.test.cjs tests/memory_controls_ui.test.cjs tests/memory_mutation_ui.test.cjs tests/qq_context_ui.test.cjs tests/qq_delivery_ui.test.cjs tests/qq_health_ui.test.cjs。

临时真实文件、原解析子进程、bridge/生成器配fake协议/模型属于自动集成，不是真QQ收发，也不是厂家真实文档理解验证；原第44轮真实视觉一次请求证据保持，本轮0厂家调用，无新依赖安装或大模型下载。初次只读NapCat检查命令引号错误已换为固定私有脚本，无生产操作。没有隐藏失败或删断言换PASS。

## 部署与状态

session45-deploy/final-verified均PASS：正常排空0.01秒、三库一致性备份/完整性ok，仅自有后端正常重启约4.47秒，原专用QQ进程保持。ready/running/matched，协议/网络可达，7份真实HTTP静态资源、媒体只读能力/原视觉配置、V4版本核对通过。

原24控制/继承/共享/人物/凭据/浏览器/本机运行和监督自启动选择不变；修改、群、图片和文档新开关都保持关闭。8未过期结果/19段/8策略/1授权群逐行保持，媒体策略/来源0行、群/好友表0、私人ACL0/操作0/投影pending0。观察原起点/6缺口/unknown不减。无代理QQ代发、生产记忆修改/授权、审批、重复厂家探测或开关启用制造验收。

变更：新增services/qq_documents.py、qq_document_worker.py、tests/test_qq_documents.py、test_qq_documents_integration.py；修改qq_media.py、qq_chat.py、qq_bridge.py、qq_rollback.py、Chat4o5-Modern.html、qq_media.js、qq_media_ui.test.cjs及进度/验收文档。服务端人物常量和core_services.py字节保持，完整路径/指纹见最新不可变清单，私有原文件快照session45-before。

下一步可选TTS回复→ASR与附件输出；真QQ文件/图片显示与前序真人待签保留。文档需由用户在本机附件面板明确开启，不自动公开或开启。见[第8阶段验收](qq08-acceptance-2026-10-31.md)和[精确入口](qq08-integration-2026-10-31.md)。
