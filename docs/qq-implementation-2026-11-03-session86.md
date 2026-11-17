# 第86轮：确认码控制边界及M2单例实机通过

日期：2026-11-03，America/New_York。开工第85轮458项指纹一致，私有备份。M1已签保持；本轮修复M2确认边界并核对实际用户写回。

用户已收到正确旧→新预览，但复制确认行时带上操作回执的最后一个右引号。原全匹配只接受完整确认码，没有把错误格式留在控制入口，导致消息进入Agent。模型把确认码当记忆ID查询，并因内部工具只读误说QQ无法写。原预览仍pending，长期事实未改变；此次失败如实保留。

## 修正与验证

新增revision_confirmation统一识别“确认修改”的控制命名空间。完整32位码可忽略一个显示回执尾引号；双引号、多余正文、短码、整句外层引用等只返回格式说明，不送Agent或记忆查询。未知/过期/已处理码走原服务读回终态，不猜测、改写token或延长TTL。权限/账号/话题/内容版本仍由原CAS检查。

当前消息直接输入完整确认码时，即使引用了一条操作回执，也只根据这条当前明确输入的码和服务端范围/版本决定；被引用文本本身与单说“确认”仍不是写入许可。媒体/转发、其他账号、群、隔离或关闭写入保持阻断。QQ能力提示明确内部Agent工具可只读，主机写服务仍可用，确认码不是事实ID。

仅改memory_revisions.py、qq_memory_management.py、qq_chat.py，新增独立tests/test_qq_revision_confirmation_boundary.py，原160份测试指纹保持。首轮6文件83项中82通过/1失败、12既有FastAPI警告、28.65秒：新夹具误以为所有控制字符串都必须是ControlReceipt，实际成功/格式回执沿用QQMemoryReceipt；修正新夹具类型判断，业务校验断言保留，失败XML不覆盖。

最终命令：

```powershell
D:\Program\anaconda3\python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['tests/test_qq_revision_confirmation_boundary.py','tests/test_qq_memory_revision_commands.py','tests/test_qq_revision_copy_guidance.py','-q','--tb=line','--basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session86-final-data','--junitxml=.runtime/qq-upgrade/session86-final.xml']))"
```

**40通过、0失败/错误/跳过/警告，18.23秒；新增17例。** 覆盖回执尾引号、错误/过期/重复码、整句引用、额外正文、引用回执之外的直接确认、跨账号/群、实际桥接队列替身中码不作为记忆ID且不调用模型。外网拒绝，模型/OneBot为替身；不把这一结果冒充尾引号变体已真人复测。无JS变化，不重复前端或旧累计套件，范围不累加。

## 部署

私有session86-release-source.json冻结273项。原锁/排空/三库backup/integrity_check，仅后端维护重启3.17秒PASS，ready/running/reachable/matched，解除排空。原预览行（含expires）、原事实/候选、QQ进程/登录、24控制/继承/网页值、原策略/人物/声音/凭据/浏览器/监督/启动及11份配置哈希部署时一致。修改管理true保持，原16观察缺口/起点/unknown不减，不补在线时长。没有重放原用户消息或代理代确认。

## 实际验收

修复部署后，用户在原预览剩余期限内使用完整无尾引号确认行，收到真实同ID更新回执；再用精确片段更正条件，收到第二次同ID回执。核对两次实际sent/有回执，原预览expires与重启前相同，没有续期。真人此次重发没有尾引号，因此只签实际重发路径；尾引号兼容仍是隔离证据。

同一事实ID、creation_time及QQ创建origin保持；当前正文保留杯名和原事实归属，只含工作日条件、不再含周末条件。V3索引/V4/网页实际只读API正文和来源事件一致；同主题活动事实仍1条。V4修订1→2→3，历史分别为原事实、周末条件、工作日条件；来源事件2→4→5，补充原句及确认来源保留。原误复制形成的另一个普通候选仍pending，不当作活动事实，也未擅自删除或确认。

**M2此单例真实补充/确认/更正门槛通过。** 不扩成任意自然说法、实体识别或全网页可视界面均已验收。重复/并发/撤权/项目/失败投影等沿用已记录隔离合同，不要求用户重做复杂实验。生产事实两次更新由用户在QQ明确完成；代理无生产事实改写、QQ代发或付费模型/搜索/TTS探针。私有session86-*保存原失败、预览、实际源记录、写回聚合、XML与部署证据，不公开账号、原会话、原始聊天或确认码。

## 下一步

先读[实施状态](qq-implementation-status.md)、[本轮不可变manifest](qq-implementation-manifest-2026-11-03-session86.json)和[详细方案](qq-memory-events-upgrade-plan-2026-11-03.md)。M1/M2已签单例不重发；保持用户已开启修改管理true。原候选、观察缺口、用户监督/启动和浏览器选择均保留。

接M3重要性解释/升降级与QQ来源轻偏好，先只读统计/迁移预览并隔离对照，再决定数据和策略落地；不把来源档、重要性、检索视图混称同一个分数，不批量重算或回填旧来源。M4角色时钟隔离及长期机制、M5逐人事件尚待；其他原实机未签、取消ASR/文件输出及撤回官方整包保持。
