# 第二十九轮：真实取消对账与无变化提交回执修复

2026-10-30，Asia/Shanghai（前次反馈与初步对账跨日续接）。176开工指纹匹配。用户明确确认：长任务有进度、运行中/status和/help都及时回复、取消确认后约30秒未收到该长答。只读对账任务/inbox均cancelled，3进度及3控制sent，最终结果映射0；最后公开phase为sources，不能据此证明取消瞬间远端网络请求在途。没有真人精确首提示/100次p95数据，不以模拟延迟代填。

## 发现与修复

对账见旧guard记录202项memory:committed。实际V4 upsert_memory在逻辑无变化时返回status=unchanged且仍附memory对象，旧_memory_write_confirmed误把原对象当作本次提交。任务时间窗V4条目创建和版本新增查询均0；这不是202条新增事实的证据，也不能凭单一计数证明其他库零元数据变化。原终态与失败证据不改写，不回滚或删生产记忆。

修改saya_app/services/qq_tasks.py：unchanged仅在evidence_added或明确committed存在时确认写入；确定无变化按既有failed（未提交）对账，异常仍unknown，不引入新持久状态。新增证据即便不改逻辑版本仍算实际提交；父操作已有嵌套确认后不能被后续no-op或失败反转。只改变回执判断，不改记忆存储或权限。

tests/test_qq_task_memory.py补实际临时V4存储的无变化upsert（版本数不变）及无变化/新增证据/实际更新/不明失败区别。先2失败、3通过的反例保留，修复后最终79相关通过、4既有FastAPI弃用警告、无skip/xfail。命令（项目Saya Python）：

```text
-m pytest tests/test_qq_task_memory.py tests/test_qq_task_guard.py tests/test_qq_tasks.py tests/test_qq_task_controls.py tests/test_qq_task_scheduler.py tests/test_qq_task_runtime.py tests/test_qq_task_delivery.py tests/test_qq_control_compat.py -q --junitxml=.runtime/qq-upgrade/session29-final.xml --basetemp=.runtime/qq-upgrade/session29-final --tb=short
```

私有session29-noop-red.xml、session29-final.xml、session29-memory-audit.json及源文件备份保持。前端未改，第二十七轮799/74及其他选择仅历史沿用，不冒充本轮重跑。

## 恢复运行

首次正常部署辅助脚本在本机API读取时URLError，stop_requested/start_requested均False，没有执行重启。随后确认端口关闭、归属后端与守护均不在、专用NapCat根进程0。不能沿用之前“进程一直保留”的描述，也不猜测停止原因。

在停止状态保存配置/观察台账及QQ、Memory3、Memory4 SQLite一致性备份，三库完整性ok；按既有管理入口恢复后端和专用NapCat，不启用持续监督或Windows自启动。已加载修复，当前ready/running/matched、OneBot和外网reachable；23项设置及配置/凭据/浏览器存档/监督策略哈希不变，群授权与未过期结果/分段/策略相等，QQ库integrity ok。原观察起点保留，缺口3→4、unknown不减少，不补算历史在线。无QQ代发或付费探测。

私有session29-deploy.json保留预检失败，session29-runtime-diagnosis.json记录停止状态，session29-recovered.json为PASS_ONLINE。后续不能重复恢复或照抄旧部署脚本；其旧排空running/draining错误条件在本轮准备脚本中修正，但本轮该部署脚本未过预检，没有执行其重启流程。

## 续接

已向用户发出三项组合反馈请求：先私聊“恢复正常”短答；唯一原主网页生成时启动原授权群长任务，并私聊甲乙两问；群/status和/cancel后网页仍完成、甲乙顺序正常、群长答停止。原共享模型锁允许普通答案排队，不承诺两模型同时推理。未命中同时运行只记未验，不能自动通过。

当前PENDING_USER，先收反馈再只读对账。不重复已通过的私聊长文取消、QQ-03、旧源码回退或无变化模块回归；QQ-04完整审阅之前QQ-05～08不开始。最新[第二十九轮清单](qq-implementation-manifest-2026-10-30-session29.json)，旧清单保持不覆盖。
