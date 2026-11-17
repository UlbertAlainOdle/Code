# QQ 第94轮：事件日期、紧迫性与自动重要度已部署

日期：2026-11-04，America/New_York。承接第93轮487项清单，开工全部指纹一致；原173项清单内测试文件保持。用户要求继续完成剩余实现。

## 交付

新增qq_event_priority.py，将结构化日期、紧迫性和自动重要度v1接入原按人事件服务，不改长期记忆等级、QQ来源倍率、人物或关系。

- deadline与appointment分开。支持完整日期/时间、明确UTC偏移和按原QQ时区/消息观察时刻解析的今天、明天、后天，不按重启时刻重新解析。日历日期与04:00逻辑聊天日分开；日期级只展示日期，内部排序按当天结束，不伪称用户指定了23:59。DST不存在/歧义时刻须补偏移；模糊星期、范围、多日期、无效日期保持unknown，不猜时间。
- /events deadline或schedule编号日期时间或clear，以及明确“对象截止时间设为/改为…”均复用本人、场合、版本、取消、事务和主机回执。目标名称与日期参数分离，明确名称完整匹配，不凭共同年份或宽泛相似度选卡；歧义只列可见候选。回执读回实际时间，clear不是取消整件事。管理不增加count、不延TTL、不恢复暂停/遗忘。
- 时间证据独立携带来源、可见范围和到期。私聊新日期不泄露群，时间来源失效不复活旧时间，遥远截止或keep不延长来源保留期。截止逾期仅overdue，安排日期过去不证明完成。先过滤权限/有效性/遗忘，再按相关性、重要度、紧迫性、原跨日/新近性排序；同相关性/重要度下24小时内截止优先、刚逾期次之，逾期超过24小时不永久占前列。无新增提醒或常驻任务。
- 自动重要度仅处理原写服务接受的新独立用户进展：明确价值/优先级表述可高低变化，无新依据沿用。长度、重复、读取、机器人复述、confidence、不急和临近截止不自行升降重要度。引述/假设/模型旧话不作独立价值证据，相冲突表述不猜。人工锁定优先；importance auto保留当前值，到下一条有效进展再按规则调整。每卡最多12项评分审计，无正文。
- 旧卡不批量初始化或重评，合法新写入时逐条应用；已签遗忘保持。新回退标记qq_event_priority_v1，未来规则/日期版本拒绝。日期跨状态边界使旧生成/发送timing_state失效，不写分数或推断完成。

原聊天和事实保留，标题/正文/分类引用/QQ show、自查与网页采用当前结构化日期的显示投影，避免重述被更正的显式时间。unknown要求澄清，removed表示未设日期。网页认证API显示时间、规则/锁定及原因，仍手动读取/textContent，不自动拉取或渲染HTML。

## 固定隔离对照

先固定13例纯虚构集：6排序目标、7限制；相同6000历史预算、0.2旧日比例及原字符上限。门槛：新版≥5/6、较旧版提高≥3、零退步、限制零失败。语料sha256：08161da3990dfacff1db16198bb562befef6acdea6c3e40d41dfa75d0f356870。

冻结第93轮事件观察器/选择器A与当前B运行在独立库，共用未改变的鉴权/控制夹具；不是完整两套后端或真实模型盲测。结果A1/6→B6/6，B限制7/7、零退步。真实写入/范围过滤/选择器参与，分类输出固定，网络封锁、模型/搜索/TTS/OneBot调用均0。只能证明确定性排序合同，不能签成真实模型长期收益。

命令：`D:/Program/anaconda3/python.exe -X utf8 scripts/evaluate_qq_event_priority.py --baseline-source .runtime/qq-upgrade/session94-source-before --output .runtime/qq-upgrade/session94-priority-eval-r3`。首次准备误把normalize_event返回accepted当错误，失败目录保留；修正后r2通过，另修诊断按ID而非随机行顺序读取unknown卡，r3同门槛通过。旧结果不覆盖。

## 文件与检查

既有6份：qq_event_controls.py、qq_person_events.py、qq_episode_context.py、qq_rollback.py、routers/qq.py、static/js/qq_context.js。新增规则模块、Python13例、Node1例、固定JSON和隔离CLI。原173项清单测试文件不改。

Python全部先执行qq_rollback.NETWORK_GUARD再pytest。首次新7例3通过/4失败为尚未接入；接入后7通过，相关49通过。首次扩展80例79通过/1新自然定位失败（共享“资料”造成歧义），保留后改为明确名称全匹配；新夹具也改为按原事实而非通用标题查卡，旧断言不改。后控制/规则32通过。

最终7文件：test_qq_event_priority.py、test_qq_event_controls.py、test_qq_person_events.py、test_qq_event_history_guard.py、test_qq_episode_context.py、test_qq_context_salience.py、test_memory_rollback_gates.py，82通过、12既有FastAPI警告、0失败/错误/跳过，46.62秒。覆盖日期/DST/锚点/精度/模糊、范围/期限、锁定/解除/审计上限、旧快照/旧确认、无效输入不部分提交、跨账号、重启/网页同源/回退。选择重叠不累加。

JS语法及原3份相关UI＋新1份Node共7项通过。公共文档不含真实账号、事件ID、原始聊天或私有日志。

## 后端恢复与保留

部署前8000端口关闭，原后端/监督归属进程均不存在；磁盘意图仍running。QQ客户端也不在，NapCat监督enabled=false。首个只读HTTP预检连接拒绝保留，不误判为缺密钥。

原full-control锁内，离线完成原三库一致SQLite备份/完整性检查和配置/资料/候选/台账备份，再按原running意图恢复后端，8.02秒ready。未杀其他进程、未代开QQ或监督。启动后排空核对并解除，consumer running、五服务/事件控制绑定正常，新日期API实际加载。QQ unreachable/login unknown单列，不能签为QQ全链路在线。

原配置/浏览器/凭据/人物/声音/监督/启动、候选、用户事实、事件/控制审计/关系/群策略保持；原TTL自然过期差异单列，无批量重评或代操作遗忘卡。四项已授权true、关系关闭、Asia/Shanghai及预算保持，知识库30条、旧长期等级0初始化。

原观察起点不变，恢复后台自动台账把16缺口记为17，unknown_seconds从378242.5429394245增至431792.6884024143，离线不补在线信用。私有session94-recover-deploy.json结果PASS_backend_only_QQ_manual_client_offline。最终session94-release-source.json冻结297项全匹配，旧证据/冻结/失败不覆盖。

## 待验收与续接

剩余工程已实现并部署到后端，固定排序门槛通过，真实自然效果仍待正常样本。新QQ日期/自动重要度单例待用户按原方式启动客户端后反馈；已请求主人私聊发送明确偏移的虚构截止安排，再/events self。核对实际收件、日期/自动high、原来源期限、网页读回和无长期保存，并观察新请求是否误答旧问题。未获回执前不签实机通过，不重复已签遗忘/M1/M2/M5、不代开监督或迁移登录资料。

下次先读本轮清单与297项冻结，保持四项true、关系关闭、原手动QQ监督和17缺口，原其他待签/取消范围保持。
