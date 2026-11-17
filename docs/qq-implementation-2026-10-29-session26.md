# QQ 第二十六轮：真实记忆提交及HTTP读取取消边界

2026-10-29，Asia/Shanghai。开工第二十五轮163指纹全部一致，原QQ-03门禁保持。没有重做真人QQ测试、旧观察或已完成组件。

本轮先处理真实提交屏障；默认关闭开关与主队列接入尚未实施。QQ-04仍在开发、未启用/部署，线上/cancel仍不可用，QQ-05～08未开始。

## 改动及保证范围

qq_tasks.py新增同步记忆操作guard及存储锁内guard。锁内预留保证等待锁时收到取消不会随后落盘。嵌套兼容写入只在同guard/同线程/活动提交范围内合并，对上下文快照的标记在退出时失效，防止迟到后台工作继承旧授权；已确认的嵌套提交不会被后续投影异常错误改成未提交。

core_services.py在实际V4写入口检查取消、保留TaskCancelled传播，在旧add/update/delete及候选处理入口设提交屏障；已提交V4结果仍完成legacy兼容投影。绑定QQ任务时不再启动脱离任务范围的自动后台synthesis；未绑定网页与旧QQ路径保持原行为。没有删除或回滚用户事实。

memory_v4/store.py在upsert_memory、tombstone_memory、upsert_candidate、update_candidate_status取得原存储锁后检查并预留实际操作；write_pipeline.py按每条proposal/删除对象检查，部分提交后取消返回status=cancelled与已有committed/candidates，后续不再写入，供上层继续投影/对账。没有把整轮多事实请求当作一个不可取消的大预留。

qq_chat.py在请求/流块/最终返回检查，取消后丢弃迟到模型结果并关闭迭代器。Brave、DeepSeek Search/Fetch及网页reader在实际HTTP请求前/返回后、重定向/流块边界检查；取消时关闭已取得的响应，不继续读取或下一跳。qq_task_controls.py将已提交提示明确为“记忆操作”，避免把一次操作计数冒充事实条数。

上述实际入口只有绑定请求guard时启用。主消费者尚未绑定guard，因此不能宣称真实QQ取消已可用；并非所有检索、工具/历史/活动/维护入口已经审计完毕。主调度接入前仍需检查executor上下文传播、所有派生副作用、候选/权限变更路径，以及取消后未发缓存冻结。

共享源码共9处：qq_tasks.py、qq_task_controls.py、qq_chat.py、core_services.py、memory_v4/store.py、memory_v4/write_pipeline.py及web_research/search.py、reader.py、deepseek.py。新增test_qq_task_memory.py、test_qq_task_reads.py。原其他工作区改动保留。私有session26-before保存原源码；控制文案修改前快照从单处已知替换逆向恢复并校验第二十五轮SHA一致，没有虚构原快照。

## 验证

首批真实记忆/guard/控制26通过；HTTP/迟到模型及发送/登记合并58通过；初版相关广回归456通过。复核发现需要把预留从整轮请求收紧到存储锁内/逐事实后追加两项，阶段测试59通过/1失败：新增在途提交夹具在更早的只读连接就触发取消，按新的锁内真实预留点修正夹具，保留失败JUnit，未放宽原断言。

最终**458 passed，4个既有FastAPI警告，0失败/错误/跳过，无xfail**。包含原100轮取消/完成竞态、实际临时KnowledgeBase的旧/V4保存、兼容投影、等待锁取消、多事实部分提交、复制上下文失效、未绑定网页保存，以及所有受影响QQ/记忆V4/搜索路径。没有真实付费模型/搜索、QQ代发或生产库故障注入。最终命令：

```powershell
& 'D:\Program\anaconda3\envs\Saya\python.exe' -m pytest tests/test_qq_tasks.py tests/test_qq_task_controls.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py tests/test_qq_task_memory.py tests/test_qq_task_reads.py tests/test_qq_delivery.py tests/test_qq_natural_bubbles.py tests/test_qq_presentation.py tests/test_qq_bridge.py tests/test_qq_groups.py tests/test_qq_quotes.py tests/test_qq_chat_runtime.py tests/test_qq_capabilities.py tests/test_memory_default_runtime.py tests/test_explicit_memory_intent.py tests/test_memory_v4_phase1.py tests/test_memory_v4_phase2.py tests/test_memory_v4_phase3.py tests/test_memory_v4_phase4.py tests/test_memory_v4_phase5.py tests/test_memory_v4_phase6.py tests/test_memory_v4_phase7.py tests/test_memory_v4_agent_adapter.py tests/test_memory_v4_controls_api.py tests/test_web_research.py tests/test_web_search.py tests/test_search_providers.py -q --basetemp=.runtime/qq-upgrade/session26-release --junitxml=.runtime/qq-upgrade/session26-release-junit.xml
```

证据在私有session26-memory-first.xml、guards.xml、final-junit.xml（456历史版本）、granular.xml（保留59/1）、release-junit.xml（最终458）及test-command.json。未改前端，76 JS/此前642累计仍为各自历史证据，不冒充本轮新运行，不与458相加。

## 运行与续接

未改线上22项配置/凭据/浏览器存档、人设、群授权、监督/正式自启动或观察台账；未重启、未部署、未迁移生产表。运行中的旧QQ继续原路径。若用户自行重启会加载这些源码，但无guard绑定时旧路径仍为默认，不能自行打开未完成任务取消。

下一步：先核对第二十六轮清单；补默认关闭的同源调度开关/旧字段遗漏/CAS/草稿兼容，主队列登记及运行guard绑定、同签名body独立控制路由；审计剩余实际检索/工具/历史/活动/维护提交与executor传播，取消缓存冻结；接公开进度与有界跨会话派发，完善保留上限及旧FIFO回退。完整C04-01～12与相关累计通过后才部署并补真人验收。勿重做本轮guard或QQ-03已通过项。
