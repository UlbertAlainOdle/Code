# 第89轮：按账号统一事件身份、可见来源与关系容量

日期：2026-11-03，America/New_York。承接第88轮468项清单；原163份测试未改。原工作区改动、M1/M2单例及M3/M4已交付合同保留，未重置/提交或重复原付费实验。

## 实现与状态

M5首版实现、隔离验证并部署；实际事件库为空，已在只读预览后备份并完成0条事实的身份schema迁移。**事件仍关闭，话题仍关闭，关系策略仍无记录/默认关闭**；QQ修改管理和来源轻偏好保持true，旧重要级仍逐条预览。迁移完成不等于事件已启用，也不等于真实模型收益或QQ实机通过。

新`qq_person_events.py`接原`QQEpisodeContext`，继续使用原`qq_episode_cards`，不另建长期事实副本。schema标记、旧ID映射及场合绑定删除确认另存原桥接SQLite。未迁移的历史库保留旧行为；不支持的未来schema明确拒绝，回退读者保护新增`qq_person_events_v2`。

- 个人主键为bot＋已验证账号＋事件ID，跨私聊/群保存同一条及revision，昵称不参与合并。每条原话单独保存private或指定group可见范围；同账号私聊可查看自己的来源，群只取该群来源。隐藏的新状态阻止旧公开状态/标题回流；最新状态过期也不回退为旧结论。来源TTL继续按原私聊7天、群策略上限执行，不因统一身份延长。
- 跨场合公开补充采用保守入口：用户在当前原话明确提供本人16位事件ID时，分类器只看到该ID及“本人指定事件”占位，不接收私密标题或原文。可写新的当前群公开进展，仍保留原ID。未提供ID且当前群没有可见唯一事件时，不从私人库猜测目标；不能宣称任意自然指代已经支持。另一个账号即使提供该ID也不能改写。
- 原话与来源仍有界：每卡8条、观察分类最多8个事件及各3条当前可见事实，原8次/分钟、超时、信心门槛及epoch/话题取消边界保留。相同账号/场合/有效来源的重复原话直接no-op，不再调模型或累计次数；引用、转发、媒体和控制消息不改人物事件。卡片仍是意图/原话证据，不把准备保存为已完成或永久记忆。
- 关系只决定容量：主人5/20，重要4/16，熟悉3/10，普通2/6，疏远1/3。主人身份固定；其他用户仅在关系开启时采用主人明确设置的全局等级，群局部学习/亲疏不改变跨场合预算；关闭关系时稳定普通额度，不暗开学习或提升权限。当前版本不从群内学习分推造全局关系。
- 降容量先冷却，超总量给予24小时缓冲后清理，原TTL更短时不延长。`/events keep 编号`优先保留、占本人容量，不延长来源寿命；已超容量时拒绝新增，不随机删除主人卡。全局上限256主体/2000卡，并为主人预留额度。压力测试覆盖两个上限，不能把此隔离压力当真实长期在线效果。

## 管理与迁移

`/events self`列本人当前场合可见事件；私聊可跨来源看自己的有效事件。`/events show 编号`查看可见事实；主人好友私聊可`/events all [分页位置]`及`/events user QQ号 [分页位置]`，每页20条。群不显示全局标题、人数/事件总数、本人私人总预算或其他人的私人卡。删除码绑定bot/账号/场合/版本/十分钟，跨群/私聊、旧版本、过期或重放不执行。迁移使旧删除预览和旧派生待发/历史快照失效，原始聊天保留。

GET/POST `/api/qq/person-events/migration`复用原本机管理认证。预览是内容/修订/策略及拟定容量的版本hash；主人明确确认后先做原桥接库私有备份和完整性检查，再在事务内重检CAS并迁移。只有旧主机事实来源、合法数字账号、唯一说话主体及可识别范围可迁移，不按昵称猜人；混合公共卡保留原scope，只在原授权公共群作为只读legacy视图。未知私有/范围不明记录保留待人工核对，不宣称全部已迁。最多预览2001项，超过2000卡或256可靠主体拒绝自动应用。旧ID/修订及映射保留，失败回滚，不清表造成功。

GET `/api/qq/person-events`为原认证网页提供按账号的20条分页、容量、状态及来源范围；`qq-context.html`原页增加手动读取、迁移预览及确认，文本用textContent，不自动读取私人卡、迁移或开策略。旧网页草稿/原时区/启动和单主页面选择不变。

修改：`saya_app/services/{qq_person_events,qq_episode_context,qq_rollback}.py`、`saya_app/routers/qq.py`、`static/qq-context.html`、`static/js/qq_context.js`；新增`tests/test_qq_person_events.py`及`tests/qq_person_events_ui.test.cjs`。

## 验证

Python命令前缀：`D:/Program/anaconda3/python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main([...]))"`，外网封锁。模型/OneBot均为替身，没有真实QQ代发或付费模型/搜索/TTS。

- 首次选集误写不存在的旧测试路径，0项执行，错误报告保留。纠正为`test_qq_relationship_policy.py`后3文件22通过/1新夹具失败：标题夹具没有包含期望的私人canary，改为明确该标题，旧断言不变。
- 6文件`test_qq_person_events.py test_qq_episode_context.py test_qq_relationship_policy.py test_qq_context_salience.py test_qq_control_receipt_isolation.py test_memory_rollback_gates.py`：48通过，12既有FastAPI警告，27.79秒。
- 增补备份副本恢复后，4文件46通过/1新夹具未初始化expressions失败；补齐只读恢复夹具，随后该新夹具一次缩进收集错误保留。未把夹具错误算业务成功，未修改原测试。
- **最终源码4文件**：`tests/test_qq_person_events.py tests/test_qq_episode_context.py tests/test_qq_relationship_policy.py tests/test_qq_experience_linkage.py`，48通过、0失败/错误/跳过/警告，28.92秒；新Python21例。
- `node --check static/js/qq_context.js`通过；`node --test tests/qq_context_ui.test.cjs tests/qq_person_events_ui.test.cjs`最终5通过，其中新2例，首次新DOM夹具缺owner元素的2失败保留并补齐夹具。验证无启动自动读取、拒绝确认不写、原版本提交、冲突不重试且保留所选账号。范围重叠不相加。

覆盖空迁移/关闭/幂等/备份副本恢复、同账号跨场合同ID且分类提示无私人原文、同名账号隔离/伪目标、隐藏更正及过期不回流、legacy混合保留与旧快照失效、五级全局容量/局部关系不覆盖、关闭稳定默认、24小时缓冲、上限与主人保留、重复证据no-op、引用/媒体/转发拒绝、删除主体/场合/版本/TTL和回退读者。隔离结构合同不代签真实语义关联、盲测收益或QQ实机。

## 部署与实际状态

私有session89-release-source.json冻结282项全部一致；沿用原双锁/排空/三库备份，受管后端重启3.12秒PASS并解除排空，ready/running/reachable/matched。原QQ进程/登录、M2当前事实/候选/预览、24控制/继承/网页/人物/声音/凭据/浏览器/监督/启动及原11配置和已启用的来源策略保持，16观察缺口/起点/unknown不减。

部署后用原本机API会话读取迁移预览：事件卡0、策略0、来源快照0、待删除0、关系策略0。预览后执行原库备份与0条身份迁移，schema2读回成功；新建一行语义仍关闭的事件策略/revision以失效旧快照。话题完整原值保持，事件仍false，关系策略仍0；knowledge及原JSON文件hash保持，来源轻偏好true，未生产写入事实或等级。

私有备份、原失败/全部JUnit、空迁移前后及来源冻结保留；新[不可变清单](qq-implementation-manifest-2026-11-03-session89.json)承接第88轮，不覆盖历史。公共文档不包含实际账号、原对话、确认码、凭据或运行日志。

## 未签门槛及具体下次入口

M1/M2仍仅签原真人单例；M3确定性排序已启用、真实模型收益另记；M4模拟长序列和工程通过，真实长期自然效果及反向孤立完整扫描未签。**M5首版工程和空库迁移通过，真实模型与仅A上下文的收益对照、实际QQ跨场合单例未完成，事件保持关闭。** 不把全部代码落地写成整份计划已验收完成。

下次先核对第89轮清单与两项已授权true、原事件/话题/关系关闭选择；不重做M1/M2或再次初始化旧等级。固定虚构连续性/更正/错误主体/私密来源的盲测集、总上下文预算和门槛，再做仅A与A＋事件的同条件真实模型对照，保留失败/成本。收益门槛通过后，才请求主人明确开启原/topic及/events，并安排一个私聊虚构计划→本人ID群公开补充→私聊回指短案例；不要求导出私人原聊天，也不由代理代发QQ。原其他待签/取消范围保持。
