# QQ 第二十五轮：独立控制适配器、请求范围屏障与发送接入

2026-10-29，Asia/Shanghai。开工第二十四轮158个指纹经Python逐项核对全部一致；最初PowerShell汇总显示null是输出表达式问题，未发现实际源码漂移。旧QQ-03门禁不重做。

## 本轮改动

- 新增saya_app/services/qq_task_controls.py与tests/test_qq_task_controls.py：独立有界控制队列/worker，处理/status、/help、/tasks、/cancel；直接复用bridge.authenticate与normalize_event，要求签名/身份/时效/群结构化@/现有授权，执行前重核当前权限与共享范围。原模型队列忙碌不会阻塞该适配器。每范围0.75秒/全局0.05秒间隔、总容量16；独立增量去重表只存事件键、scope、时间和状态，无消息正文/凭据/原始异常。发送不明不重试，停止只取消本控制worker，不关闭共享客户端。实际webhook尚未路由到它。
- 扩展qq_tasks.py：本范围任务列表、ContextVar请求guard、同步线程可继承的checkpoint和effect预留/对账。未绑定的网页/旧QQ调用保持原行为；取消不操作共享任务或客户端。在途操作必须明确committed/failed，未确认默认unknown；异常不能撤销已确认提交。新增tests/test_qq_task_guard.py。
- 在qq_delivery.send_batch的每段发送前接入可选effect_current(send,scope)，在更改该段pending状态前原子检查任务取消及scope；有确认回执后记committed，异常仍按既有规则冻结unknown。新增tests/test_qq_task_delivery.py覆盖发送前取消、首段确认后取消、结果不明时取消与错scope拒发。原已发送段/回执不重排。主消费者尚未绑定guard，因此未启用线上取消。

共享修改仅qq_tasks.py、qq_delivery.py；新增控制适配器及3个测试文件。原bridge、真实生成器、记忆服务、设置/网页、QQ人物设定未改。私有session25-before保存修改前源码；无生产库迁移、配置修改或运行部署。

## 验证与证据

先写控制失败集时模块缺失是预期red；首次实现后的正常权限测试12失败/21通过，原因是新测试夹具遗漏必需的假OneBot token，已补假值，未放宽业务校验。原失败JUnit保留。受限沙箱asyncio用例挂起，正常本机权限运行；精确匹配本轮唯一basetemp参数终止原挂起pytest（未操作QQ/后端）。挂起不是通过，也未据此认定线上故障。

随后39项控制/guard契约通过；发送接入后120相关通过。最终命令：

```powershell
& 'D:\Program\anaconda3\envs\Saya\python.exe' -m pytest tests/test_qq_tasks.py tests/test_qq_task_controls.py tests/test_qq_task_guard.py tests/test_qq_task_delivery.py tests/test_qq_delivery.py tests/test_qq_natural_bubbles.py tests/test_qq_presentation.py tests/test_qq_bridge.py tests/test_qq_groups.py tests/test_qq_quotes.py tests/test_qq_chat_runtime.py tests/test_qq_capabilities.py -q --basetemp=.runtime/qq-upgrade/session25-final --junitxml=.runtime/qq-upgrade/session25-final-junit.xml
```

**267 passed，4个既有FastAPI警告；JUnit 0失败/0错误/0跳过，无xfail。** 包括原任务契约100轮取消/完成竞态。全部临时库、假模型/搜索/OneBot；不是完整C04门禁或真实QQ取消结果。未改前端，原76 JS/更早642累计为各自历史证据，未冒充本轮重跑或相加。

## 部署与精确续接

线上仍为第十九轮QQ-03代码，本轮没有部署、服务重启、QQ代发或付费探测。原22项设置、凭据、浏览器存档、持续监督/自启动选择和观察台账均未操作。此次改过磁盘发送层，但已运行后端未重启；后续若用户自行重启会载入该可选钩子，未绑定guard时走原行为，不会自动创建任务表或启用/cancel。

QQ-04仍进行中：适配器未接webhook；主队列未登记/绑定guard；真实检索/搜索/来源/记忆提交未接屏障；无公开进度发送和实际跨会话并发；无同源默认关闭开关。取消后旧未发缓存还需冻结/明确恢复语义，不能让新的自然续读绕过原取消。不要因为本轮适配器存在就通知用户/cancel已上线或部署。

下一次先核对本轮清单，从qq04-integration的第二十五轮精确入口继续：同源默认关闭开关与遗漏字段/CAS兼容；主队列登记、控制路由、请求范围真实读写屏障、取消缓存冻结、公开进度/资源预算并发；完整C04-01～12及相关累计回归通过后再正常排空部署并收真人结果。QQ-05～08未开始，QQ-03已通过项不重复。
