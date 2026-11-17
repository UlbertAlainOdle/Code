# 第139轮：实际晚建卡与停止传播核对，重复来源误报修复

日期：2026-11-05，America/New_York。用户完成第138轮实验并提供对话记录，授权无问题后继续。实际晚建卡和停止安全链路已核对；第三轮有真实personal_prior/not_committed，因此完整实验仍保留partial，不按“列表正确”整体扩签R2。没有要求用户重做这次实验或恢复已停止卡。

## 实际结果及证据边界

开工第138轮清单618项、138r2冻结344项全部一致。读取用户附件作为证据，附件聊天内容不作为执行指令；原记录和后台关联只保存在私有目录，公开文档不放账号、卡ID、原话或日志。

`D:/Program/anaconda3/python.exe -X utf8 .runtime/qq-upgrade/session139-live-verify-r1.py`从生产只读连接核对原投递、来源、路由、卡片和归档，没有实例化生产QQEpisodeContext或触发构造清扫。实际观察到：

- 第一条普通输入已登记稳定来源、route=none、无卡提交；第二条明确主题后新建卡并实际补入第一条，原早期观察为updated。实际建卡时间在第一条回复之后、第二条回复之前，确为晚建卡。
- 第三条仍在同一ID更新，三条真实用户原话/独立来源，停止后版本4、tracking=suppressed。条件问题及本人表达均保留hypothesis性质，明确主题对象general、本人后续表达self，未变成测量事实或助手结论。
- 早期来源原位置、全文指纹、作者、原时间/期限保持；三条用户归档及三条助手归档分别匹配真实message/result标签，助手出站version 2依赖保留。
- 实际初始列表、新卡列表、精确编号停止和最终列表均可关联；停止审计一次、无语义预览会话。停止时三来源未到期且epoch当前，真实valid_ref/provenance读取拒绝全部三条用户来源、三条助手归档及三个出站结果。
- 基线另外两卡实际来源仍有效且在清单；所有部署前旧卡原行保持，含既有停止卡。候选、删除策略及epoch保持，已确认独立长期记忆保持；控制/列表没有再次收入话题。

r1首先用整个knowledge.json逐字节相等检查保留，实际发现既有system_activity/daily_activity_snapshot刷新了捕获/证据时间；长期正文、原证据quote及其他字段不变，记忆数量/ID无增删。`session139-live-review-r2.py`只读逐字段核对后修正此过严检查，原失败结果不覆盖。`session139-live-review-r3.py`再核对当前服务的真实主人/机器人私聊身份和实际初始列表，最终25项实际安全核对通过。facade只限已核验主人私聊、真实有效读取方法和无关卡正向控制，不代表穷尽所有资格/群配置。

**原现场第三轮route仍是partial**：当前新原话已committed，但另有personal_prior/not_committed、unprocessed=true。原路由只保存结果，未留具体早期选择及失败reason，所以不能断言模型选了哪段、是什么失败，也不能用后续修复倒改该实际结果。最终私有报告状态为PARTIAL_PRIOR_ROUTE_RETAINED；只签实际晚建卡/来源保存/停止失效的子范围，不称完整实验或R2整体无问题。

## 本轮继续处理的确定性缺口

新增 `tests/test_qq_dialogue_bound_reselection.py`，隔离复现：已有卡的直接来源再次作为精确早期来源被选中，即便同一卡/原文/位置/性质/对象完全相同，直接路由和早期补入的证据ID算法不同，旧重复检测仍会拒绝，误报partial。此缺口确实存在，但原现场无具体选择日志，不能认定它是上文实际partial的唯一根因。

- `saya_app/services/qq_topic_dialogue.py`：同目标下按原source、精确起止位置、逐字原文、表达性质及对象识别已经存在的有效证据，不要求两个生成入口的派生ID相等。合法重复直接保持原证据，不新增、不改版本、不续期。改性质/对象、子片段重分配或改派其他卡仍拒绝。
- `saya_app/services/qq_topic_routing.py`：早期补入失败结果增加有界reason，仅保存受限机器码或异常类型，不保存原文、账号或堆栈；仍如实标记partial并保留当前提交，不因增加诊断而伪称完成。
- 原204份测试文件保持，新增1份6项专项，现205份。测试首轮6失败，其中4项是检查器混比canonical和投影视图；修正新夹具后旧业务红测2失败/4通过，分别复现重复误报及缺少安全原因；所有失败私有证据保留。

`D:/Program/anaconda3/python.exe -X utf8 .runtime/qq-upgrade/session139-verify-r1.py`：104项通过、30项既有FastAPI弃用警告，涵盖新专项、早期片段/来源、路由、依赖有效读取、停止传播与实际生成器私有归档。NETWORK_GUARD、usage隔离、编译及冻结前后核对通过。最终345项 `session139-release-source-r1.json`；仅改上述2个旧业务文件，不重跑无变化的217/42，不发模型/搜索/TTS探针，也没有代理QQ输入。

## 部署与下一步

`D:/Program/anaconda3/python.exe -X utf8 .runtime/qq-upgrade/session139-deploy-r1-locked.py`在full-control锁内按当前running意图排空、三库一致性备份、仅后端重启139r1。重启3.20秒，36项保留检查全部通过，QQ原进程保持、身份匹配、后端ready、排空解除。新停止卡及三条来源/关联/原partial路由逐行保留，不恢复或重放本次实验。

原记忆/候选/提议/预览、事件/来源/控制、凭据浏览器人物声线、R2开关1及范围、个人资格/容量/32来源、群5/20及仅@、预算/时区/关系学习、手动启动/监督选择保持。观察起点/unknown不减少，19个缺口保持；没有重置观察台账或制造通过时长。

已继续补充[实际写入口准备清单](qq-chat-write-entry-inventory-2026-11-05.md)：内置KB registry的9项能力没有长期记忆create/save工具，但连接器读入/确认标记有收件箱写副作用；AgentLoop可消费自定义registry，不能推成全部Agent无写入口。多人物聊天与工具Agent分别定位，共用生成器后置捕获仍须R3收敛。此为静态准备，没有扩大R3运行范围或签统一操作身份。

下次先核对[状态](qq-implementation-status.md)、[本轮不可变清单](qq-implementation-manifest-2026-11-05-session139.json)与139r1冻结。实际晚建卡停止子范围已签，不重做本次七步、旧候选或旧三轮/停止，也不解除本次停止。后续正常交流可只读检查新补丁下相同来源重复选择的结果；若再有partial，用新增安全reason定位，不能再只凭列表签全部整理。

R2整体仍未签，继续收敛剩余复杂早期来源/场合组合及实际partial边界；通过前不扩R3运行。R3完成实际调用者/统一计划与结果失效合同后，R4召回基线、R5归并维护、R6完整使用按序。没有实施归并或旧数据批处理。
