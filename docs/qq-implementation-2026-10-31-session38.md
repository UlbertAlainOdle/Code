# 第三十八轮：进入QQ06，逐条权限管理与分类浏览

日期：2026-10-31，Asia/Shanghai。用户询问上一轮是否有实验支撑，通过后要求进行第六阶段。本轮先核对第三十七轮203文件指纹，全部匹配；其200相关和21聚焦JUnit均0失败/错误/跳过，组间重叠，不相加。该证据支持自然确认闭环及双端逐条历史时间的实现/部署，真人QQ自然确认和时间理解反馈仍待，不倒填实机验收。

## 已实现并加载

- 新增qq_memory_management.py，复用原KnowledgeBase，不复制正文或另建CRUD。已保存/待确认候选两个列表，10条分页、文本搜索、稳定ID顺序和列表快照；翻页遇变化冲突。详情读取真实正文、来源与状态。主人QQ共享好友私聊新增`/memory list [页码]`、`/memory show 编号`；原`/memory`边界说明及`/memories`候选命令保留。
- 现有本机管理会话与同源确认下，增加`/api/qq/memory-management`、`/item`、`/access`；身份取配置主人QQ号，服务再次核验账号/bot/私聊管理上下文，不接受模型身份。写接口额外要求Origin、QQ设置revision、完整条目SHA-256、权限revision与明确确认；权限写入和读取正文版本在原KB写锁内检查，独立SQLite策略CAS持久化。正文、来源、关系、状态改变使原发布授权失效；读取计数/检索分数不制造内容改版。
- 网页QQ接入增加按条查看及授权、撤权面板，私有数据不在启动时自动加载；选择可说场合、指定群、额外可问账号与受托写入账号，明确勾选后保存。切换账号清详情，修改范围清确认，异步处理中禁用面板；冲突保留草稿，不影响原QQ设置草稿。所有条目采用textContent呈现。
- 原qq_memory_access.py允许V4合法personal敏感度，但仍须同时满足发问者和受众授权，secret/未知不公开。管理面拒绝未授权群、无效账号、归档/过期/失效或含明显凭据的公开条目；不把normal当公开。新增生产表为空，不给旧记忆批量授权。
- **这是第6/8阶段的第一批交付，尚非整个QQ06完成。** 群检索、工具、提交、输出/续读撤权屏障尚未统一，群记忆读取继续原隔离；保存授权不表示群已经能使用。修改/删除/不再使用、召回解释、候选并发与缓存防复活仍待后续接入，不宣称C06整体PASS。

## 测试证据

1. 首次先写测试，因新模块尚不存在而收集报错，保留session38-first.xml。
2. 独立管理+原ACL：31通过，session38-management-v1.xml。
3. 扩展相关集：125通过/2失败，均为新夹具错误（QQSettings非dataclass、发送记录不止两字段）；修正夹具，不删业务断言。保留session38-related.xml。
4. 最终187 Python通过、12既有FastAPI弃用警告，0skip/xfail；session38-final.xml。命令：

   `D:\Program\anaconda3\envs\Saya\python.exe -m pytest tests/test_qq_memory_management.py tests/test_qq_memory_access.py tests/test_qq_context_integration.py tests/test_qq_capabilities.py tests/test_qq_actions_time.py tests/test_qq_controls.py tests/test_qq_chat_runtime.py tests/test_qq_owner_alias_prompt.py -q --junitxml=.runtime/qq-upgrade/session38-final.xml --basetemp=.runtime/qq-upgrade/session38-final --tb=short`

5. 前端最终11通过：`node --test tests/qq_memory_management_ui.test.cjs tests/qq_context_ui.test.cjs tests/qq_delivery_ui.test.cjs`。覆盖明确确认、文本安全呈现、撤权、双版本CAS参数、冲突草稿、翻页快照、账号切换及原设置草稿保护。与较早8项重叠。

真实临时KB、SQLite提交失败触发器/回滚、并发审核仅一方成功、内容改版/撤权/重开、当前主人移除/群撤权、两个分类列表及实际bridge不调用模型的列表/详情链路均覆盖。真实生成器的旧身份、搜索、接续、时间和只读回归在相关集中保持。不是实际QQ消息收件或浏览器人工点选验收。

## 加载与保护

开工运行时原后端/监督进程/专用NapCat均不存在，端口未开，保存意图为running，未把旧ready状态当现时健康。先做私有源快照、配置/台账与三库SQLite一致性备份，完整性均ok；通过原管理入口恢复后端（约2.61秒），专用NapCat显式启动一次，持续监督与Windows自启动保持原选择。无停止/强杀、重复消费者或付费探测。

恢复后ready/running/matched、NapCat与网络reachable。23控制/继承配置/浏览器存档/凭据/人物风格文件保持；9份未过期结果、15段回执、9策略及1群逐行保持。原观察起点未改，历史缺口4→5按停机真实记录，unknown不减，不补算此前在线。真实本机新API只读浏览saved/pending及详情成功，ACL表0条，代理没有代选私人话题、授权发布或发送QQ消息。静态文案与脚本版本号最后热载，无二次后端重启。

私有证据：session38-before/、session38-live-backup/、session38-deploy.py/json、session38-verified.json、session38-resume.json。公开文档不含正文、账号、密钥或原始日志。加载记录的静态指纹为加载时快照，最终静态指纹见session38-verified.json及新清单，不覆盖历史证据。

## 续接与回退

精确入口：[QQ06接线](qq06-integration-2026-10-31.md)、[QQ06验收](qq06-acceptance-2026-10-31.md)。继续第6阶段，不启动QQ07/08，也不重新做旧多窗口长实验。原QQ05真实选源/接续缺项及第三十七轮真人反馈仍待。

旧源码不读取新增ACL表，回退必须保留该表及已授权策略，不能用旧数据库覆盖用户合法变更；未来群消费者不认识策略版本时须闭合，不把旧授权当public。此轮不新增QQMessage字段，第三十七轮旧源码回退的inbox投影条件继续适用。尚未开展本轮新生产回退实验，不冒充完成。
