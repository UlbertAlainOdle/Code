# QQ 第十一轮：专用QQ与真实断网验收、Windows重登检查点

日期：2026-10-28，Asia/Shanghai。用户明确授权三项实机窗口：专用机器人重启、用户手动物理断网、用户手动注销/重新登录Windows。保留原QQ表达、人物设定、历史隔离、自动搜索、API和偏好文件，不重复开发。

## 已完成的实机验收

专用QQ本体恢复PASS：排空且确认无忙碌任务，核对专用profile、PID和创建时间，先用SQLite backup保留QQ库副本，仅结束已验证的专用根进程，再以原profile一次性启动。约12.8秒恢复，账号匹配、无需扫码、专用根进程仍一个，QQ设置及NapCat监督策略保持。没有操作日常TIM或删除登录文件。证据`session11-native-recovery-live.json`（复用已备妥的第十轮私有脚本，原输出同时保留）。

物理网络第一次实测捕获缺陷：用户确认断网约60秒、恢复后QQ回复一次，但75个样本中旧OneBot登录检查始终matched，没有反映外网离线。本次诊断FAIL保留，不能将它改写成PASS；恢复后的单次收发是独立通过项。

已修复：新增独立、无凭据的公共HEAD连通性探测，账号匹配与外网连通性分别展示；公开站点为QQ和DeepSeek首页，不使用Key、Cookie、搜索或模型接口，不跟随重定向，严格超时、5秒缓存与合并并发。一个端点收到HTTP响应即证实可达；均失败则不可达；超时/异常为未知。探测不可达不等于账号已退出，也不承诺具体QQ网关状态。网页“检查连接”不再仅凭账号匹配报正常；新网络状态不可达/未知不会增加健康在线时长。

修复后物理复测PASS：真实捕获32个外网不可达样本，随后可达及账号匹配恢复。用户最初在QQ尚未重连完成时发出的消息没有到后端、队列也无生成任务，未自动重放；确认账号重连后用户发新消息，确认仅收到一次回复。恢复网络与QQ重连完成存在时间差，此限制保留。证据`session11-network-fixed-live-result.json`、两次原始脱敏采样、两次用户反馈均保留，不能删第一次失败。

## 代码与验证

新增`qq_network_health.py`及其8项Python用例、`qq_health_ui.test.cjs`的5项UI执行测试；修改QQ诊断、路由、观察对新network字段的判定、网页健康显示及JS版本，保留旧记录/旧接口兼容。不改变原始观察起点、总计数或历史缺口。

最终459 Python、71 JS通过，12个既有FastAPI警告。新网络传输在测试中全部模拟；真实联网HEAD另测约0.2秒。首次物理失败及第二次QQ重连未完成的无回复报告属于真实证据，未被单元测试替代。部署约13.39秒，QQ过程中的原数据、设置、群授权、API凭据、16项偏好保持；人物设定和QQ表达规则未改。

实际修改文件：`saya_app/services/qq_network_health.py`、`qq_health.py`、`qq_observation.py`、`saya_app/routers/qq.py`、`static/js/qq_settings.js`、主HTML版本、`tests/test_qq_network_health.py`、`test_qq_lifecycle.py`及`tests/qq_health_ui.test.cjs`。Node新增用例直接执行真实健康渲染回调，验证网络不可达不被matched掩盖。

## 24小时门禁的证据可靠性

原台账与缺口完整保留。第一次失真采样窗口加两侧各90秒边界，保守隔离约402.91秒，不用于未来在线门禁信用；记录在`session11-network-before-fix-findings.json`，没有重写原台账。修复前没有network字段的历史只能按原证据审阅，不能追认成独立外网验证。

Windows重登前原有效采样约18.81小时；即使只扣上述隔离区间，仍不足24小时。未来还需审阅方法变化、故障、计划停止和新的采样缺口，不能只看原累计小时或当前绿灯自动PASS。

## 当前临时状态：等待用户手动Windows重登

第十二轮核查补充：用户确认本次实际为锁屏/解锁或切换用户。原临时任务未运行、后端原进程保留，QQ正常；真正注销验收NOT_RUN。原临时任务已撤销，另按已有授权准备第十二轮窗口。后续必须按[第十二轮报告](qq-implementation-2026-10-28-session12.md)的新证据文件取证并撤销当前任务，不覆盖本轮结果。下文为当时检查点原文。

**已注册本项目临时当前用户登录任务，尚未手动运行。** 原正式自启动偏好为关闭；此任务仅为本次验收，用户确认重登并核对运行证据后必须精确撤销，取消测试也必须撤销。不要把它误当成用户新开启的长期自启动，不要再创建重复任务。

任务使用原`start_qq_managed.ps1`、`-RespectStopIntent`、实际Python路径、8000端口、Interactive/Limited和IgnoreNew。NapCat持续监督仍关闭，原intent为running。注册前确认目标任务不存在；注册后保存了尚未运行的任务信息，代理没有调用Start-ScheduledTask来伪造登录触发。

当前窗口阶段记录：`.runtime/qq-upgrade/session11-relogin-plan.json`；注销前配置/群/凭据/偏好摘要、后端身份、队列、监督和台账在`session11-before-windows-relogin.json`。用户需要先保存其他应用工作，再手动注销/登录。代理不执行Windows注销命令。

## 用户返回后的精确步骤

1. 先读本文件、[实施状态](qq-implementation-status.md)和[本窗口清单](qq-implementation-manifest-2026-10-28-session11-window.json)，确认用户说“已重登”或取消；当前临时任务不得遗忘。
2. 未手动启动服务前，运行`.runtime/qq-upgrade/session11-capture-logon-task.ps1`记录真实任务LastRunTime/LastResult。必须晚于注册且正常结束；若尚在Running，等待后另存新证据，不覆盖既有文件。不要手动Start-ScheduledTask或手动启动后再声称自动登录成功。
3. 运行私有`session11-verify-after-logon.py`，核对新拥有的后端创建时间、账号匹配、外网可达、唯一专用QQ、设置/继承/共享默认/群授权/API/16项偏好、原监督选择以及观察起点/缺口。若需扫码，由用户扫码，不把任务启动成功等同QQ登录成功；未自动恢复则如实记FAIL/待恢复。
4. 捕获任务和组件结果后，用`scripts/register_qq_service.ps1 -Remove`精确撤销本项目临时任务，再确认任务不存在，更新relogin-plan临时标记为false。即使本次测试失败或用户取消，也先保存故障证据并恢复原自启动关闭选择；不要清理QQ登录态、数据库或其他计划任务。
5. 用户用QQ再发一条短消息验证只回复一次（需要用户操作，不由代理代发）。Windows重登完成与登录/收发结果分别记实机证据；保留新增观察缺口，不重置台账。
6. QQ-02仍需真实观察时长与可靠性审阅。当前QQ-03～08均未开始；满足所有前置门禁后才继续语意分段/引用/续读，不重做已交付的自然表达、隐藏附注或自动搜索。

公开文档不含凭据、用户完整会话、原始运行日志。原459/71报告：`session11-release-junit.xml`、`session11-release-node-junit.xml`。原验收和源码清单均保留，新的Windows结果另存，不覆盖本窗口历史证据。
