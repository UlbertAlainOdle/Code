# QQ 第95轮：无回复与停止等待修复，日期和自动重要度单例实机通过

日期：2026-11-04，America/New_York。用户报告启动后QQ无回复，停止提示仍有任务。开工第94轮493项指纹一致；原176项清单测试文件保持。本轮未重置工作区、导入配置、启用QQ监督或代发QQ消息。

## 原因与恢复

两项独立故障叠加。模型流式请求发生证书链验证失败；同一会话的任务被取消后，收件箱已记为interrupted，但任务账本仍为running，阻塞后续6条排队消息。旧停止程序暂停新接收后把这些排队项也算作必须等待的工作，却没有取消它们，因此等待超时。不是缺少模型密钥，也不是此次日期规则拒绝回复。

先完成原三库一致SQLite备份、完整性检查和受保护资料备份。只取消6条尚未开始的排队项；孤立任务仅在收件已终结、调度器无对应worker、没有in_flight副作用的条件下收尾。已提交或结果未知的副作用不重放、不伪造成功；原账本的维护记录不能理解为新增长期事实。随后沿原停止流程完成用户要求的停止，没有强行结束忙碌或归属不明的进程。私有恢复脚本首次完成账本修复后遇到模块路径错误，修正路径重试时新增取消数为0，原尝试保留。

停止后用户自行重新启动，最新意图变为running。本轮遵从这个后续选择，保持当前运行，不再次停止。实际新进程已加载本轮源码；控制面板也在停止修复文件保存后启动，未留旧函数缓存。QQ监督仍为false，原手动启动方式不变。

## 修复

- 新api_tls.py为共享模型AsyncClient构造系统默认SSLContext，使用Windows系统信任及原配置的CA路径；保留CERT_REQUIRED和主机名验证。原HTTPX显式CA路径选择会跳过系统信任，此处不修改凭据、环境或安装证书，也不使用verify=False。三次无凭据、无正文的HEAD诊断分别验证原路径失败、系统路径及新辅助函数TLS成功；401为未携带认证的预期响应，未发模型请求。
- qq_tasks.py新增终结收件的孤立任务核对，只允许拥有调度器在worker已退出、无in_flight时执行；保留既有副作用记录、冻结未发送结果。qq_bridge.py在取消路径取消并等待子响应任务，等待真正进行中的逻辑提交后结束账本；调度器可收尾符合条件的历史孤立项，使下一条消息不再被永久阻塞。
- 原认证生命周期API新增cancel-pending，要求confirmed及排空状态；同时取消已注册和未注册的排队收件并清除待处理正文。调度器跳过已经取消的队列项，避免随后重放。停止程序先排空并取消未开始消息，再等待正在生成、发送、语音和真实活动任务结束；旧后端404/405才走兼容路径。
- 状态与停止门槛加入active_tasks，避免只看收件计数而漏掉仍活动的任务。WSL检查改用字节捕获，仅解码JSON标准输出，避免本机stderr编码差异触发解码线程异常。

业务改动：server5-22B----.py、saya_app/services/api_tls.py、qq_tasks.py、qq_bridge.py、saya_app/routers/qq.py、scripts/manage_qq_service.py、scripts/saya_qq_control.py。新增tests/test_qq_failure_shutdown.py，共8例；没有业务schema迁移或长期等级初始化。

## 检查与失败记录

所有pytest先执行qq_rollback.NETWORK_GUARD；无付费模型、搜索、TTS探针或QQ代发。新增用例覆盖取消后下一条可执行、历史孤立项收尾、排空鉴权/取消队列/等待活动任务、TLS验证与实际客户端工厂、真实后台提交完成后再取消、in_flight拒绝收尾及WSL编码。

旧实现新用例首次3失败/1通过；接线后一次新孤立项夹具使用时间戳0被正常TTL清理，改为当前测试时间。相关5文件最终67通过、无警告，18.53秒。后扩展出现新WSL夹具缺目录，以及原API测试受真实已保存本地配置覆盖假模型的3个失败；旧断言和用户配置未修改，全部失败XML保留。

修正新夹具目录后，以私有pytest夹具仅隔离test_api_async_hardening的凭据/共享配置读取，使其仍使用原MockTransport和原全部断言。最终5文件59通过，12条既有FastAPI警告，0失败/错误/跳过，24.07秒。67与59范围重叠，不累加。命令：

```powershell
D:/Program/anaconda3/python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import sys; sys.path.insert(0,'.runtime/qq-upgrade'); import pytest; raise SystemExit(pytest.main(['-q','-p','session95_api_fixture','tests/test_qq_failure_shutdown.py','tests/test_api_async_hardening.py','tests/test_qq_task_scheduler.py','tests/test_qq_full_control.py','tests/test_qq_service_drain.py','--junitxml=.runtime/qq-upgrade/session95-release-isolated.xml'])))"
```

最终私有session95-release-source.json冻结299项全部匹配，原176项测试指纹保持，旧清单/冻结/失败不覆盖。备份、原始聊天、账号、日志和私有夹具均留在.runtime/qq-upgrade，不进入公共文档。

## 实际恢复与验收

用户重启后，新跟进请求与/events self均收到并实际发送成功，两个任务正常completed，队列/生成/发送/语音/活动任务为0。ready/running/reachable/accepted/matched全部正常，新回复没有混入旧公园问题。

该新请求同时补齐第94轮待验收短例：实际新卡revision1、count1，自动重要度high、依据explicit_user_value、未人工锁定；明确截止2026-11-06T18:00:00+08:00，known/minute、原Asia/Shanghai时区，来源绑定本次真实收件。观察结果created，实际事件清单回执包含high与该日期。本例实机通过，不扩大为所有自然措辞或真实长期收益已验收；未让用户重做M1/M2/M5或遗忘单例。

原受保护配置哈希保持，候选不变；知识库仍30条，非日活动用户事实完全保持，系统原日活动刷新单列，无本次新安排的长期事实，旧长期等级初始化仍0。四项已授权true、关系学习关闭、预算/时区/监督/启动方式保持。观察起点和unknown保留，停启如实形成第18个缺口，不将过去离线补成在线。

## 续接

从[第95轮清单](qq-implementation-manifest-2026-11-04-session95.json)和299项源码冻结续接。当前服务按用户最新running选择保持，QQ监督false；本次短例无需重复。停止已成功恢复，后续取消/排空的新代码有隔离验证，但未把当前运行服务再次制造故障当作实机停止测试。

剩余为M3自然使用收益、M4真实长周期及复杂自然日期/管理措辞等稀疏观察，原其他待签/取消范围保持。若出现新卡状态或无回复问题，先按真实收件→任务/副作用→发送→来源版本核对，不重放历史排队消息、不延TTL、不批量重评，也不新增自动监控。
