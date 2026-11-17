# 第四十三轮：QQ07工程实现、回归和默认关闭部署

用户要求离开电脑至少2h期间尽可能继续后续工作，随后再次要求继续。本轮开始262个第四十二轮指纹全部匹配。QQ06开发收尾已完成，原真人待签保留；依用户主动推进后续的范围进入QQ07，没有将QQ06或QQ07伪签为整体PASS。

## 交付

- 新增QQGroupContext：逐群显式配置，公共话题、发言者/时间、有界上下文、独立群记忆、角色与委托写入、引用唤醒均默认关闭。公共语境按群串行，同群各账号任务/投递仍隔离；原账号隔离作为关闭回退。只记已确认发出的回答，不把未发送正文或群回答保存为私人助手历史。
- 群读取只选当前使用有效、逐条ACL授权且当前发问者/输出群受众均允许的私人记录，复用QQ06策略和内容版本；不导入个人网页全文/私聊历史或默认开启个人工具。真实原生成器请求的隔离测试验证允许记录进入、未授权正文/私人网页历史不进入。
- 派生来源随群语境、事件、结果保存内容无关的版本要求，生成/每块/发送/标题/续读重查；撤权、改版、停用、换话题、关闭范围后失效。事件预绑定覆盖缓存已创建但结果绑定失败窗口。引用派生机器人旧答需对新发问者重新授权，曾启用群记忆后未知来源机器人引用默认拒绝；仅协议时间证明早于启用的旧答保留原引用行为。
- /gcontext、/gnew、/gmemories、/gremember、/gforget→/gconfirm；单独群数据库命名空间、不写私人KB。管理员通过当前OneBot成员账号/群/role核验；普通白名单成员仅管理自己的群记录，不能改全群话题。删除确认绑定账号、群、版本、策略、10分钟，保存关闭拒绝写入，已知凭据文本拒绝保存。角色接口依据[OneBot 11公开API](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)。
- 好友申请独立持久审核区；陌生人不自动通过，flag不出网页，主人明确确认后仅一次尝试；结果不明/进程中断保留待核对，不自动重放。接受好友不改变聊天允许列表、friend私聊要求或私人记忆授权；原群邀请/block规则保持。
- 前端两独立面板显式读取/确认，冲突保留草稿，普通QQ全局表单不被新字段弄脏。QQMemoryManagement显示逐群能力与逐条授权条件，不再误称服务未接。回退预检声明新group provenance/contact-review能力，避免旧程序忽略新状态。

## 变更文件

新增qq_group_context.py、qq_contacts.py、qq_group_context.js、qq_contacts.js、三个Python测试和qq_group_contacts_ui.test.cjs。修改qq_bridge.py、qq_chat.py、qq_quotes.py、qq_memory_management.py、qq_rollback.py、routers/qq.py、server5-22B----.py、Chat4o5-Modern.html、qq_settings.js、qq_memory_management.js。完整路径和字节指纹见本轮清单。core_services.py和人物原始提示未改；server人物/风格常量AST与开工快照一致。

## 测试与失败记录

| 证据 | 结果 |
| --- | --- |
| session43-group-first.xml | 9通过/2新夹具失败：测试第二群90001未列入事件规范化白名单；补夹具，不放松生产鉴权 |
| session43-group-v2.xml | 86通过 |
| 首次integration | 暂停于等待模型入口：新引用唤醒权限检查错误读取尚未解析的动态quote_context；仅停止匹配此测试命令的pytest进程。改为持久QQMessage两字段并给等待点加5秒上限；无有效JUnit，不计PASS |
| session43-integration-v2.xml | 16通过，4既有警告 |
| session43-group-combined.xml | 70通过，4既有警告 |
| session43-focused-v3.xml | 67通过，0失败/错误/跳过 |
| session43-final.xml | **77文件875 Python通过，64既有FastAPI弃用警告，0失败/错误/跳过**；冻结最终源码后执行 |
| Node前端 | **35通过，0失败/取消/跳过**，包含5个新群/好友授权与草稿测试；四个变更JS的node --check通过 |

Python命令为python -m pytest，最终选取第四十二轮74个文件并加test_qq_group_context.py、test_qq_group_integration.py、test_qq_contacts.py，带-q --tb=short与独立JUnit/basetemp。聚焦命令为python -m pytest tests/test_qq_group_context.py tests/test_qq_group_integration.py tests/test_qq_contacts.py tests/test_qq_memory_policy_isolation.py tests/test_qq_memory_access.py tests/test_qq_quotes.py -q。完整最终参数保存在私有session43-final-command.json。

前端命令：node --test tests/qq_group_contacts_ui.test.cjs tests/qq_memory_management_ui.test.cjs tests/memory_controls_ui.test.cjs tests/memory_mutation_ui.test.cjs tests/qq_context_ui.test.cjs tests/qq_delivery_ui.test.cjs tests/qq_health_ui.test.cjs。各组重叠，不累加。全部隔离KB/ASGI/原生成器配fake模型或协议；绝不是真人QQ验证。辅助脚本的断言/引号失败未触碰生产数据；QQSettings原快照在首个变更后反向移除本轮唯一新增排除项重建，并与session42原SHA-256完全吻合，其他开工文件直接快照。

## 部署和边界

session43-deploy/final-verified均PASS：正常排空0.02秒，三库一致性备份/完整性ok，仅自有后端正常重启约4.41秒，原专用QQ进程保留；ready/running/matched，网络/协议可达。原24控制、继承、共享、人物、凭据、浏览器、本机运行及监督/自启动选择不变，memory_management_enabled仍false。原观察起点/6缺口/unknown不减。

重启时未过期8结果/19段/8策略和1授权群逐行保留；新群策略/语境/群记忆/来源/删除令牌/好友请求表均0行，私人ACL0、操作0、投影待对账0。六份真实HTTP静态资源哈希、群/好友只读管理接口、V4版本均通过。无代理QQ代发、生产私人记忆改写/授权、好友审批、付费厂家调用或开新群功能来制造证据。

QQ07工程与自动回归已完成并默认关闭部署；C07原真人项目与QQ06/05旧真人项独立待验。QQ08图片/语音/文件尚未实现，已只读确认网页附件和视觉模型路由可复用；下一步从受控图片协议引用开始，不重写视觉后端。见[验收](qq07-acceptance-2026-10-31.md)、[精确入口](qq07-integration-2026-10-31.md)。
