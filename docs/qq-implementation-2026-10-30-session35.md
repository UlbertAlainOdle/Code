# 第三十五轮：群新闻入口修复与QQ05接入部署

2026-10-30，Asia/Shanghai。用户要求继续QQ05，并提供新闻问句得到“无法获取实时消息”的实际反例；用户明确位置是QQ群@机器人。191开工指纹匹配。源/测试私有备份session35-before；人物设定和既有无关改动保持。

## 搜索诊断

本机QQ有效搜索模式auto、自主工具开。旧qq_chat对任何group_id都设置web_mode=off，因此根本不进入搜索厂家；不能将这条表现归为密钥失效/HTTP故障。近期事件正文未找到，不伪造请求日志。本轮没有付费调用验证厂家当前额度/网络可用性；历史DeepSeek真实验证保留，Brave原402不改写。

将公开搜索与私人记忆权限分离：已验证主人在经bridge授权的群可使用现有auto/force及/search；普通成员仍无搜索工具，群/非主人请求显式memory_write_enabled=False、temporary=True、recall=off、agent=False。单轮“不要联网搜索”继续覆盖搜索设置。权限断言只改变用户本次要求的主人群公开搜索部分，私人边界未放宽。

实际生成器测试补群与共享组合，使用用户原新闻问句验证search→fetch→answer、缺额度时search→失败说明，不重复付费探测。群搜索选择106通过/8既有警告。新闻修复先行正常部署。

## QQ05完整接线

- qq_bridge挂context_links，生产启动QQContextLinks及只读MemoryManager适配，按当前主人/共享/任务控制资格撤销旧关联；默认空表未绑定。API通过既有本机管理session、同源Origin、配置主人ID和设置revision/链接generation校验，忙碌409，不把客户端confirmed布尔值当权限。
- 网页QQ设置新增独立来源选择/明确确认/停止入口，复用原cookie和CAS，不改23项原控制；不自动加载/绑定私人会话。来源显示使用textContent，冲突保留选择草稿，接续选择不会污染原QQ设置dirty标记。
- QQ/context显示关联来源，/context off/new与/reset停止、移出当前派生QQ上下文，源网页/人物/长期记忆/原QQ历史存档保留；普通回复不自动尾注命令。网页绑定/停止与新QQ话题及旧派生待发缓存清理同事务，保存失败回滚；避免新绑定叠加旧选源QQ历史。
- QQChatResponder仅主人共享好友私聊读取快照，归档数据经user参考段进入API/agent/本地提示，不进入system身份，不作为当前用户事实送写管线；原引用与当前姓名/主人别名保持。来源SHA/关联代次有效性接入当前task guard permission_check，模型/线程/记忆提交/发送各原checkpoint共同拦截；另在SSE块及最终答案接受前检查。无guard的旧FIFO不能建立接续：网页选择要求任务控制开启，关闭时撤销旧关联。
- 过期/来源消失/共享关闭返回独立话题并切断旧派生缓存；实际关闭共享或任务控制无下一条QQ也立即撤销。生产重启核验已配置主人，旧被撤账号不能恢复绑定。源2MiB/12条/12000字/1小时限制、原始版本和时间/消息范围保留，不生成付费摘要、不拼接全历史。

## 测试与失败保留

初版集成53通过/1失败：测试预期跨源403，现有cookie鉴权先返回401。保留session35-context-first.xml，修正测试为实际401，不修改鉴权；中间226/228通过保留。最终：

```text
-m pytest tests/test_qq_context_links.py tests/test_qq_context_integration.py tests/test_qq_capabilities.py tests/test_qq_chat_runtime.py tests/test_qq_bridge.py tests/test_qq_controls.py tests/test_qq_quotes.py tests/test_qq_task_scheduler.py tests/test_qq_task_memory.py tests/test_qq_task_runtime.py tests/test_qq_quiet_identity.py -q --junitxml=.runtime/qq-upgrade/session35-final-v3.xml --basetemp=.runtime/qq-upgrade/session35-final-v3 --tb=short
node --test tests/qq_context_ui.test.cjs tests/qq_delivery_ui.test.cjs tests/qq_health_ui.test.cjs
```

229 Python通过、16既有FastAPI警告、无skip/xfail；11 JS通过。选择与早先106/20有重叠，不相加。实际临时模型HTTP/OneBot/库隔离，不是QQ收件验收。增加存储失败原子回滚、撤销后迟到答案不收、关闭任务控制回退保护等；原QQ身份/引用/取消/消息回归保持。

## 部署与当前状态

两次正常排空/三库一致性备份：先搜索修复、再完整QQ05。最终context部署PASS约3.59秒就绪，QQ matched、ready/running，OneBot/外网reachable。23项、继承、凭据/浏览器/人物风格文件哈希、17结果37段17策略/群授权、QQ原进程保持；基本人物提示AST与第三十三轮一致。原观察起点/4缺口保持、监督/自启动不变。无QQ代发或付费探测。

前端最后补一处接续选择不改变原表单dirty状态，11 JS通过，静态文件直接热载；此JS哈希另记session35-static-final.json，不覆盖原部署日志、不声称多做一次重启。实际本机GET context-links成功，owner/session选项可读、活动关联0、未由代理选源。原记录含少量私有统计不公开历史预览或ID。

当前第5/8阶段工程已部署、真实验收未过，不进QQ06。用户可在唯一原网页QQ接入中“读取可选话题”，选择来源后明确确认；QQ/context查看、/context off或/reset停止。绑定未发生前继续独立QQ聊天。需要实际群新闻新回复、用户明确选择后接续/停止的反馈；不重做旧多窗口长文实验。最新清单见[第三十五轮](qq-implementation-manifest-2026-10-30-session35.json)，私有session35-resume.json。
