# 第153轮 游戏归档并发、来源重验及保存后文件恢复

2026-11-06，America/New_York。开工核对152轮677项清单、375项最终源码r4和部署r2，均匹配。游戏归档原服务与路由已在范围；原test_game_memory.py本轮首次纳入指纹。保留Listener、手动添加和此前各轮结果，不覆盖历史清单。

## 本轮结果

ConversationService.archive_game_session转发game_archive_writes，手动API和断开自动归档共用同一合同。以原游戏文件完整哈希、使用epoch及当前允许的最多200条来源形成确定性操作编号，生成前登记pending，模型等待后在原KB锁内重验。另一个实例并发请求只取得原pending结果，不再次生成摘要。源文件或权限变动时invalid，原文件保持，摘要不保存。取消也不保存。

来源经过memory_use当前投影并排除控制回执和不进入上下文的内容。游戏消息记录在未绑定上下文时绑定当前epoch，原有读取上下文则继续执行失效检查。正常写入、会话合并及归档最终提交共用KB→Conversation锁顺序，避免模型等待后来源已更新却保存旧摘要。

实际add_memory成功后先记录ID、实际版本和文件移动待办，再尝试移动原文件。移动失败时已保存事实不再新增；重试只恢复文件，核对原文件哈希、已存记忆版本和目标不存在。目标已存在不覆盖；开始时已有同名归档则使用包含本操作编号的独立文件名。文件移动成功但状态登记失败，重试可核对目标文件哈希后完成登记。原文件或已存记忆后来变动则恢复blocked，保留文件等待核对。

存储返回前异常记unknown，不假定成功、不重新调用模型或新增。游戏归档操作与Listener一样不随普通30天清理消失，仍受共用8192条容量限制。新增game_archive_write_operations_v1回退要求。GET /api/bridge/minecraft/chat/archive-result按原游戏会话和操作编号只读查询；不移动文件，不返回摘要正文。手动归档pending/invalid/unknown返回实际操作状态，不冒称找不到文件或成功。

## 验证和部署

修改原conversation.py、bridge路由、memory_write_operations和qq_rollback；新增game_archive_writes与16项专项，原游戏测试首次纳入指纹，原测试内容不改。首批聚焦31项通过。命令 D:/Program/anaconda3/python.exe -B -X utf8 .runtime/qq-upgrade/session153-verify.py 完整358项通过、36条既有FastAPI警告，网络拦截和用量隔离生效。新增专项覆盖并发、来源及权限变动、取消、文件失败前后恢复、同名历史不覆盖、unknown不重放、不随30天丢失、当前历史筛选、只读查询和回退兼容。重叠测试不累计。

最终378项session153-release-source-r1.json匹配。session153-deploy-r1-locked.py受管排空、三库备份及仅后端重启3.22秒完成，41项保留全通过。增加游戏原文件及已归档文件的私有备份与哈希核对；QQ原进程/账号匹配、原Listener文件、配置凭据、浏览器、人物声线、记忆/候选/预览、原生五表和事件控制保持，解除排空。手动启动、关闭QQ自动监督和20个观察缺口保持。无代理QQ代发或付费探针，隔离测试不签实际QQ验收。私有原对话及备份不入公开文档。

## 接续

先核对本轮清单、378项源码r1及部署r1。下一项为game_memory.record_task_event持久身份、资格/开关重验和unknown保护，随后Dream主题/日记及Learner逐项派生授权、来源、版本和结果。最后补实际QQ批量保存、结果查询及网页详情一致性验收。R2已通过、139原partial保持，R3整体未签，R4—R6不提前启动。
