# 第105轮：双归属实机核对、作者标注与启动版本绑定修复

日期：2026-11-04，America/New_York。开工第104轮514项清单全匹配，181项既有测试文件保持。用户反馈个人/群列表确实都有新话题，但都用“我”而没有清楚说明是谁。

## 实机证据与判定边界

只读核对本次新虚构读书会：个人与群各1张、各revision1；同一来源与片段证据ID，个人subject/speaker、群actor、证据actor及路由actor一致。一次路由有personal/group两个committed目标。实际回答和两次列表共3个投递均sent；知识库仍30条，没有新增本次长期事实。

本次仅签普通发言双归属、同源作者与实际投递单例，**不签整体整理质量**。原显示缺口也被实际回执印证：个人列表缺归属标签、群列表缺发言者标签；原话直接作为标题，使第一人称脱离作者。不是底层作者账号丢失，也不能用账号存在证明模型理解一定正确。

## 作者显示和模型读取修正

- 新qq_event_attribution.py统一显示：昵称/群名片仅作辅助，QQ账号绑定身份；缺昵称显示QQ号，同昵称不同账号保持区分。名称限制长度并清理控制字符，不作为权限或人物设定。
- 个人self/all/user/show与网页增加明确归属、原话标题及逐项发言者。群列表显示标题原话作者、参与发言者，详情逐贡献标注真实发言者；共同决定保留独立决定者。
- 新发言保存入站提供的显示名称；旧卡不猜昵称、不批量重写。旧群标题只有能从原贡献唯一核对时才推导原话作者，否则指向逐项作者。
- 模型材料增加owner_id、speaker_display及subject=self时的subject_id；引文中直接第一人称绑定该引文作者，不绑定机器人或另一个读者。转述中的内层引文保留原归属，不做简单代词全替换。
- 原话、来源、证据ID、版本、TTL、资格、控制和改写权限不因显示修复而改变。

新增tests/test_qq_event_attribution.py最终8项。修改qq_person_events.py、qq_unified_events.py、qq_topic_routing.py、qq_episode_context.py、qq_chat.py、routers/qq.py、static/js/qq_context.js。后述启动修复还修改qq_bridge.py及server5-22B----.py。

## 部署校验发现的启动缺口及恢复

首次受管部署3.20秒完成启动，但完整数据校验失败，未记为PASS：新群卡被初始化清理，原个人卡保留。备份中群卡有效期尚余约49分钟，持久memory_use版本在维护前后未变；不能将其解释为正常到期。

根因：服务器先await qq_bridge.start()，事件构造器会清理群卡；此时memory_management/group_context尚未绑定，epoch临时返回0。真实群卡保存的是非零持久版本，因此被当成版本不符删除。以前缺真实共享群卡或测试用0版本时未暴露。

修复：服务器启动桥接前绑定kb_system.memory_use；事件模块优先使用这个持久来源。来源尚未就绪时，版本不一致不能作为破坏性清理依据，期限检查仍有效；来源就绪后的真实版本失效保护继续成立。

新增非零版本冷构造、实际桥接停止/启动用例。隔离通过后，在原完整控制锁和排空状态下备份当前库，再从首次一致备份只恢复这1张受影响群卡及其原停止行；验证持久版本相同、原来源仍有效，保持原编号/revision/正文/期限。没有全库回滚、延TTL、重发消息或重新建卡。

恢复部署3.19秒PASS；原个人与群两行随后逐字读回与维护前相同。网页个人归属、群详情原话作者/发言者均绑定实际作者，topic_routing_enabled保持true。原配置/QQ进程/登录、人物/声线/凭据/浏览器、候选/修改预览、知识库30及事件控制保持；运行running、QQ监督false、关系学习关闭、原群仅@、资格/容量/时区预算不变。观察原起点、unknown与18缺口保持。

## 验证

隔离pytest先执行qq_rollback.NETWORK_GUARD；沿用私有session104-test-fixture隔离生产用量写入，未进行新模型/搜索/TTS调用或QQ代发。

- 作者/话题/统一/个人/旧事件相关6文件90通过、18既有FastAPI警告，100.53秒。
- 增补网页API后作者专项6通过，11.38秒。
- 启动修复后作者专项8项连同旧事件、统一边界与实际服务器重绑，48通过、18既有警告，57.36秒。
- 网页相关Node7通过。范围重叠不累计，不冒充单次全量回归。
- 最终生产只读核对8项通过：两张原卡逐字不变、个人归属与群作者标注正确、新路由保持开启、309项源码指纹一致、健康running/matched。

相关实际命令（重跑需更换输出名）：

```powershell
D:/Program/anaconda3/python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import sys; sys.path.insert(0,'.runtime/qq-upgrade'); import pytest; raise SystemExit(pytest.main(['-q','--tb=short','-p','session104-test-fixture','tests/test_qq_event_attribution.py','tests/test_qq_episode_context.py','tests/test_qq_unified_events.py','tests/test_qq_unified_event_boundaries.py','tests/test_qq_responder_lifecycle_binding.py','--junitxml=.runtime/qq-upgrade/session105-epoch-r1.xml'])))"
```

最终私有session105-release-source-r2.json冻结309项。r1、失败部署校验、恢复前后备份及原第104轮证据均保留。首次恢复后读回脚本因缺失群卡触发StopIteration，是发现后的诊断失败；修复恢复后新读回8项全部通过，不覆盖首次失败或改写为成功。

## 下一步

已请用户只在原群@机器人重新读self/group，核对归属/标题作者/发言者是否清楚；无需重发原分工或重建话题。显示签收仍待用户，整体语义质量、完整跨投影更正/撤回、活动轮次等继续按第104轮边界，不扩大本次签收。

后续不得再从0版本冷启动测试推导非零持久版本重启安全；继续使用最新冻结与状态，不回退到缺启动绑定修复的来源。不得通过恢复过期/已停止或真实版本失效数据来制造保留通过。
