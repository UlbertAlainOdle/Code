# QQ 第二十七轮：主调度、控制路由与进度集成（已部署开启）

2026-10-29，Asia/Shanghai。开工第二十六轮168个指纹全匹配。QQ-03门禁保留，QQ-05～08未开始。

本轮已完成主队列接线：task_control_enabled默认False、旧字段遗漏/修订CAS兼容，签名body控制路由，单会话顺序/最多两个调度worker，实际请求guard绑定，取消缓存原子冻结及旧FIFO终态投影，公开阶段/进度节流与发送不明不重试，任务侧表保留上限。新增设置为第23项，原22项不重置；新网页面对尚未更新的22字段后端会禁用并省略新字段，避免未部署期间破坏原设置保存。

补充真实生成器边界：会话历史写入有请求guard；在取得既有模型锁后重查取消，工具和Agent Loop每步检查；QQ延期记忆条目在内存保留本任务guard，仅持久化任务标识，重启不脱离原guard重放，非QQ延期任务保持。模型等待超时先请求取消并丢弃迟到结果，不强杀同步提交或共享客户端/模型锁；有在途副作用时保持占用并等对账。现有API路径仍受原共享模型锁约束，两个调度worker不等于两个模型同时推理。

实际模型假HTTP验证了取消后不发迟到QQ答案、不保存测试事实，排队网页仍能完成。新增测试首轮存在两个夹具错误（异步流收集/临时目录）及一个把共享模型锁误当允许并发的场景假设，均保留JUnit；修正测试以验证真实资源约束，并在锁取得后加取消检查，没有放宽模型锁。之后真实生成器2项通过。

已验证：首轮接线88、组合152、进度与调度9、取消/恢复/实际生成器20；累计799 Python通过、1既有过时断言原样单列排除、24个FastAPI弃用警告，前端74 JS通过。末段新增群节流/撤权与已提交说明后相关53通过。原取消完成竞态100轮保持。已修改新发送取消测试将pending加强为cancelled，旧QQ-03断言不削弱。详细命令/JUnit私存session27-*。

控制性能：10个已登记长任务（同范围1运行/9排队）下100次被接受状态/取消，模拟传输p95约0.032秒，最大约0.219秒；为测旁路排队延迟，控制限流窗口时钟按每次1秒推进，真实反滥用另测。不是100次真人QQ到达或10个模型并行的证明。

发布前实际只读预检：QQ账号matched、Saya/consumer正常、外网/OneBot可达，原22设置，1位主人、无其他授权私聊联系人，1个既有授权群。没有代理QQ代发或付费调用。

上段为发布前检查点。其后已完成两次隔离旧源码兼容演练、主版本部署/带revision启用与去重修复部署；当前已开启，真人QQ-04仍待用户，不能把自动化标成完整实机门禁。

## 去重补修与回退

新增反例：新通道已回复的同一/status事件，关闭开关后旧FIFO仍会再次接收。先保存失败JUnit，再修复qq_task_controls.py与qq_bridge.py：控制接收事务同时写无正文state=control的inbox终态；新启动对历史控制事件增量补投影（包括新开关关闭时），冷却查询排除control投影。普通聊天仍受原冷却，既有普通事件也不能换正文变成新控制。最终120相关项通过，另7项投影/切换检查通过，无skip/xfail，4个既有FastAPI警告。

前一源码由开工私有快照取出并核对3处原指纹，使用隔离新库/合成数据：先新路径取消及保留已确认分段，再关闭开关、仅从旧源码配置投影移除新字段（完整新配置仍私存），实际旧bridge/controls/delivery运行。两次PASS；最终演练额外验证旧源码不会重放已回复控制事件，旧新问答仍工作。没有恢复生产旧库或发真实QQ消息。

## 实际部署与失败保留

主版本、去重修复均正常排空后只重启本项目后端，分别保存配置及QQ/Memory3/Memory4一致性备份，三库integrity_check=ok。task_control_enabled由同源带revision接口在主版本启动后开启，补修部署保持原True，未重复切换。最终独立复核QQ matched、Saya ready、consumer running、OneBot/外网reachable、draining=False；23项及继承原值、配置/凭据/正确Chrome存档/监督策略哈希保持，16份结果/25段/9份策略和群授权逐行相等，QQ原进程早于备份且未重启。观察起点未变、缺口3→3、unknown未减少；监督和Windows自启动选择不变。

两份部署等待超时及启用末尾断言失败原始报告均保留。原因已从实际health实现和脚本调用顺序确定：脚本每轮设置draining=True，却要求consumer=running；inspect_health在排空时正确返回draining，故条件不可成立。启用脚本末尾同样保持排空；finally释放后独立复核全部通过。不能把它描述成启动超过45/120秒，亦未为超时额外重复重启。首次补修启动误用无pydantic的系统Python，仅导入失败、无服务操作；改用D:\Program\anaconda3\envs\Saya\python.exe。原始失败文件不覆盖，最终证据另存session27-hotfix-verified.json。

## 文件与验证命令

本轮业务修改：qq_bridge.py、qq_controls.py、qq_tasks.py、qq_delivery.py、qq_task_controls.py、qq_chat.py、qq.py路由、core_services.py、server5-22B----.py、memory_v4/agent_loop.py、memory_v4/capabilities.py、web_research/reader.py/search.py/deepseek.py，以及QQ设置HTML/JS。新增qq_task_progress.py；新增test_qq_task_scheduler.py、test_qq_task_progress.py、test_qq_task_runtime.py、test_qq_control_compat.py，调整task delivery与UI测试。准确路径/哈希以[部署后清单](qq-implementation-manifest-2026-10-29-session27-deployed.json)为准，其他工作区变更保留。

- 累计：项目Python运行`.runtime/qq-upgrade/session27-run-release.py`，它逐字执行session27-release-command.json保存的参数；799通过、1项既有过时断言原样排除、24警告。完整选择与JUnit私存，避免误称本轮再跑旧642/76。
- 最终补修：项目Python `-m pytest tests/test_qq_task_scheduler.py tests/test_qq_task_controls.py tests/test_qq_bridge.py tests/test_qq_controls.py tests/test_qq_groups.py tests/test_qq_task_progress.py tests/test_qq_task_runtime.py -q`；对应session27-dedup-backfill.xml共120通过。`tests/test_qq_control_compat.py`加scheduler同一控制切换去重单例补充7通过见session27-projection.xml，末段权限等53通过见session27-permissions.xml；不与799相加冒充独立用例总数。
- 前端：`node --test tests/qq_delivery_ui.test.cjs tests/qq_health_ui.test.cjs tests/local_settings.test.cjs tests/web_window.test.cjs tests/browser_settings_archive.test.cjs tests/local_api_profile.test.cjs tests/chat_experience.test.cjs tests/chat_agents.test.cjs tests/test_voice_playback.cjs tests/mmd_tie_physics.test.cjs`，74通过。
- 隔离前版：项目Python运行私有session27-old-compat.py及session27-old-compat-v2.py，两次PASS。生产排空/备份/部署与独立复核由session27-live-deploy.py、session27-final-enable.py、session27-hotfix-deploy.py、session27-hotfix-verify.py记录；这些是已执行脚本，含失败等待条件，不能照抄重复运行。

## 精确续接

当前已请求真人私聊/status，待回复；随后按[C04验收表](qq04-acceptance-2026-10-29.md)只补本阶段实时进度/控制旁路/取消/群与网页组合。若反馈异常，先保存相关脱敏任务/控制状态和当前源指纹再定向修复，禁止凭自动化通过标真人PASS。没有新业务改动不重复799回归或部署，不重做QQ-03。QQ-04门禁审阅前不做QQ-05～08；原观察台账和关闭的监督/自启动继续保留。
