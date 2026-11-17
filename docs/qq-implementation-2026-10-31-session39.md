# 第三十九轮：QQ06共享修改基础、候选互斥与旧回答失效

日期：2026-10-31，Asia/Shanghai。用户要求继续、不需暂停或等待配合，尽量推进更多工作。本轮210开工指纹全部匹配，私有源快照保存；未改人物设定或原23控制。本轮在第三十八轮管理面基础上向共享存储和派生输出边界推进，没有重做列表/ACL UI。

## 完成的实现

- 新增memory_versions.py，完整条目SHA-256跨网页/QQ共用，忽略访问计数与临时检索字段。原KB update_memory、预览/实际删除、候选确认/忽略增加可选期望版本与受信提交回调；均在原写锁内再检查，QQTaskGuard继续覆盖真实提交。网页记忆/搜索/候选列表返回只读投影content_version，修改/候选/单条删除接口支持版本并返回409冲突；旧客户端不带版本仍按原兼容语义，不声称整个旧网页已启用强制CAS。
- 原删除签名只包含关系等字段，正文被另一端修改后旧确认仍可删除。本轮在签名中绑定相关条目内容版本，旧确认遇正文/关系变化重新预览，不能误删新版本。
- 候选创建、确认、忽略共用KB写锁；确认/忽略仅允许pending一次迁移。候选文件保存返回持久化结果，直接确认/忽略遇失败恢复待处理状态并明确失败。记忆提交已经成功但候选状态失败时，以原条目source_detail.candidate_id精确对账；重试/重开后只补状态，不新增第二条。API/QQ区分已保存但状态待核对、重复内容及真正成功，不报虚假保存回执。候选创建/其他后台维护的全部失败路径仍非本轮完整覆盖范围。
- 主人自然确认保存建议绑定具体候选版本。提议后候选被另一端修改，“同意”不再保存未审核的新内容；旧无版本建议在新版管理适配器下需重新读取。真实bridge普通提问→发送建议→同意的有效/陈旧两路均通过隔离真实KB验收。
- 新增memory_use.py，只保存epoch/变更时间，无正文。正文/来源/关系/有效性变更及删除在持久化前推进版本；失败也不复活旧在途读取。访问计数、无实质修改及单纯收藏不制造内容改版。V4用户控制也设版本边界。缺失/损坏已见版本不回到legacy状态；旧数据首次无版本按0兼容。
- 网页真实生成器绑定版本，每块输出及历史/记忆写边界检查；变更后迟到答案不输出/存档，API此类异常不进入普通重试。QQ共享范围近期助手回答、缓存结果、legacy逐段/自然续读在发送前核验版本，旧pending冻结，保留sent回执和原文件；跨端网页来源与时间提示同样过滤旧助手派生内容。普通删除/编辑保留原始用户发言，未把它们当作“不再使用历史”的授权；原聊天文件、网页指针不改。
- `/memory search 文字`及`/memory why`已接。why只读当前已验证QQ范围的实际会话审计，过滤旧版本、已删/无效条目，描述为选入提示而非证明模型真实逐条使用；无审计不猜测。
- 新增独立qq_memory_operations.py：删除预览与确认绑定bot/账号/话题/对象版本/关系签名/操作/10分钟期限，保存token哈希、不存正文；原KB删除/防复活账本复用，不另建CRUD。确认仅一次、执行中异常或恢复未知不自动重试。**该模块未接线上QQ修改/删除命令，也未创建生产操作表。**

## 实验与修复过程

先写8项失败用例，实际重现旧正文被删、确认后再忽略双迁移、状态写失败仍报成功及缺少版本检查；证据session39-first.xml保留。共享基础初版75通过。

失效集初版67通过/3失败：两项新客户端夹具递归调用、一项请求字段名错误；修正夹具后6通过/1真实失败，发现旧助手文字会经clock_context的message_hint回流。修复时间来源投影后，实际模型载荷不含旧助手正文或提示摘要；保留原始用户历史的界限明确。组合131通过/1新夹具错误（误以为MemoryManager初始指针为None），改为验证原指针不变。

- 大范围47文件：618 Python通过、40既有FastAPI弃用警告、无skip/xfail，session39-wide.xml。命令选择tests/test_qq_*.py及memory_confirm_payload、autonomous_memory_upgrade、memory_v4_controls_api、memory_v4_phase4、memory_system_fixes、memory_default_runtime、prompt_time_gating。
- 最后边界收敛后的140聚焦通过、20既有警告，session39-final-focused.xml；覆盖新增修改/失效/确认契约、管理面、时间/跨端、真实生成器、任务取消、V4控制。大范围证据与最终聚焦的范围重叠，不相加或冒充每次运行均对应最后细化之前完全相同源码。
- 最后新增完整自然确认实验初10通过/2假凭据夹具不完整失败；补configured等假API配置后，12最终通过，session39-consent-final-v2.xml。覆盖真实bridge提议后候选改版拒绝及未改版仅保存一次。无真实厂家调用。
- 前端未修改，相关11 JS通过，包含原设置草稿与跨端选择回归。

核心命令：

`D:\Program\anaconda3\envs\Saya\python.exe -m pytest tests/test_qq_memory_mutations.py tests/test_qq_memory_invalidation.py tests/test_qq_memory_operations.py tests/test_qq_actions_time.py tests/test_qq_memory_management.py tests/test_qq_context_integration.py tests/test_qq_task_runtime.py tests/test_qq_chat_runtime.py tests/test_memory_v4_controls_api.py -q --junitxml=.runtime/qq-upgrade/session39-final-focused.xml --basetemp=.runtime/qq-upgrade/session39-final-focused --tb=short`

`D:\Program\anaconda3\envs\Saya\python.exe -m pytest tests/test_qq_memory_mutations.py -q --junitxml=.runtime/qq-upgrade/session39-consent-final-v2.xml --basetemp=.runtime/qq-upgrade/session39-consent-final-v2 --tb=short`

本轮是真实组件、隔离存储、受控HTTP/OneBot替身，不是实际QQ收件/真人网页交互；既有前阶段实机待验不补PASS。

## 部署与保留

正常排空约0.02秒，配置及三库一致性备份完整性ok，仅本项目自有后端正常重启约3.14秒，PASS。最终ready/running/matched，NapCat/外网reachable；原专用QQ进程保持。23控制及继承、共享、凭据、浏览器存档、人物风格文件保持；9未过期结果/15段回执/9策略/1群逐行保持。原观察起点和5缺口保持，unknown不减少；持续监督/自启动选择保持。基本人物提示AST值与开工完全一致。

实际本机管理列表及网页版本投影只读成功。生产ACL仍0条，新的QQ删除操作表不存在，代理没有生产记忆变更、QQ代发、授权发布或付费探测。生产epoch文件尚未产生，只有用户实际相关编辑/删除时才推进；不为了验证而修改用户私人事实。

私有证据：session39-before/、session39-live-backup/、session39-deploy.py/json、session39-verified.json、全部JUnit、session39-resume.json。新清单217文件（以实际清单为准），历史清单不覆写。

## 阶段位置与继续入口

**仍在第6/8阶段，未整体完成；不启QQ07/08。** 下一步继续原KB派生摘要/工具缓存与“不再使用”统一过滤，完善网页客户端CAS及QQ默认关闭的修改/删除适配，再接独立删除确认执行器、持久化回执和回退门禁。未确认的QQ执行入口保持关闭，不通过开放群全量私有记忆制造进度。详见[QQ06入口](qq06-integration-2026-10-31.md)和[当前验收](qq06-acceptance-2026-10-31.md)。无需用户继续复杂实验或重新确认推进。

回退必须保留memory_use_state.json、删除账本、候选来源对账字段、新策略/epoch表及合法用户变更。使用过新失效边界后，不能直接换成不识别屏障的旧完整源码并恢复旧数据库；须保留兼容屏障、冻结派生待发、撤销旧来源绑定和版本建议后进行独立演练。本轮没有伪称完成该新回退门禁。QQMessage无新增持久化字段，第三十七轮更旧源码的inbox字段投影要求仍有效。
