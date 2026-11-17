# 第85轮：M2实机未通过后的复制提示及错误说明修正

日期：2026-11-03，America/New_York。开工第84轮456项指纹全部一致，私有源码备份；用户已授权开启的QQ修改管理仍true，本轮没有再次请求或调整该许可。

## 实际证据与判定

用户提供补充变新候选、更正被拒及引用旧操作回执后普通确认无效的真实反馈。只读关联原用户轮、候选、活动事实和V4记录：第一条把指引中的“发送”及外层成对引号也复制进了正文，未匹配原句首修订语法，因此普通模型候选抽取生成了新的pending候选。原长期事实仍只有杯名，没有待更正的使用条件；V3修订预览表没有本次操作。

后续片段更正缺少原片段，拒绝写入正确，但笼统描述为保护/范围/版本问题不准确。最后的普通确认引用的是旧操作回执，既不是新的修改预览，也没有该预览的一次性码，不能据此提交。没有后台补做条件、自动确认新候选或删除原候选来把失败改成通过。

**本轮M2真人流程未通过，修复后的真人路径仍待验。** M1原真实保存/网页API同ID来源/新事实重启保留已签，不重新验或撤销其证据。原事实正文、ID、创建时间及两条来源事件保持；本次额外待确认候选留存，不把它保存成第二条长期事实。

## 修正

- memory_revisions.revision_request识别直接引用的修订语句及“发送/输入/回复＋引号”形式，返回requires_direct=true、safe=false。它只识别误复制以便澄清，绝不去掉外层后自动执行。普通引用讨论仍不属于操作；敏感或不确定的内容不回显为可执行正文。
- qq_memory_management在任何模型/候选抽取前说明当前未修改、未另存候选，并展示可直接发送的正文。此前已存在的候选不受这条提示影响。片段缺失与片段多次出现分别说明，前者提示先完成补充预览和确认；再次提交已完成的旧→新更正为no-op，不新增修订。
- qq_chat对引用操作回执后的普通确认仍保持只读，明确需要先取得真实修改预览并直接发送完整确认码。移除会被既有展示过滤改写成泛化说明的命令提示，原展示过滤规则、权限和确认协议不变。

仅以上3个业务文件及新tests/test_qq_revision_copy_guidance.py变化；原159份测试指纹不变。没有改模型、人设、声音、来源/评分、数据库schema、浏览器配置或其他策略。

## 验证

实际首条合成事实输入用开工memory_revisions源码与修订后源码做只读回放：开工解析为None；修订后仅要求直接正文且safe=false。原片段更正错误从correction_fragment_not_unique分离为correction_fragment_missing。原源码SHA和关联数据仅保存在私有session85-opening-replay.json等证据，未替换生产文件或在生产库故障注入。

```powershell
D:\Program\anaconda3\python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['tests/test_qq_revision_copy_guidance.py','tests/test_memory_revisions.py','tests/test_qq_memory_revision_commands.py','tests/test_qq_memory_save_contract.py','tests/test_qq_control_receipt_isolation.py','tests/test_qq_presentation.py','tests/test_qq_memory_mutations.py','-q','--tb=line','--basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session85-tests-data','--junitxml=.runtime/qq-upgrade/session85-tests.xml']))"
```

**114通过、0失败/错误/跳过、12条既有FastAPI警告，28.64秒。新增独立12例。** 覆盖各种外层引用、敏感内容不回显、准确拒绝缺失/不唯一片段、重复更正no-op、引用旧回执确认只读、普通引用讨论不误判，以及真实桥接队列替身中误复制不进入模型/候选→直接正文出预览→码确认同ID写回。原追加/更正、权限、来源、CAS、部分投影及回执隔离检查同时通过。

模型/OneBot为替身，进程启用现有NETWORK_GUARD拒绝外网。此结果不代签新真人收件、实际M2写回或全部自然说法。没有JS修改，不重复前端、旧累计套件或付费对照；第84轮新增41例和旧失败证据保留，不累加或涂为本轮成绩。

## 部署与保留

私有session85-release-source.json冻结272项并复核一致。按原full-control锁/排空/三库SQLite backup及integrity_check，仅后端维护重启**3.09秒**PASS，ready/running/reachable/matched、排空解除。原M1活动事实与所有当前候选（包含此次未确认候选）重启前后逐项相同。

QQ原进程/登录、24控制/继承/网页值、原策略、人物/声音/凭据/浏览器/监督/启动与11份保存配置哈希保持；memory_management_enabled=true不回退。观察16→16缺口、原起点/unknown保持，不补在线时长。代理无生产事实写入、人工QQ代发或新增付费模型/搜索/TTS探针。原实际输入、回执/候选关联、源码、XML、三库备份和部署核验仅留私有session85-*，不公开身份、原会话或原始聊天。

## 下次入口

先读[实施状态](qq-implementation-status.md)、[最新不可变manifest](qq-implementation-manifest-2026-11-03-session85.json)及[详细方案](qq-memory-events-upgrade-plan-2026-11-03.md)，核对272项冻结。保持用户已开启的QQ修改管理及原所有选择，不重做M1。

本次先单步请用户直接发送操作正文，已给单行代码块，避免把指引语或引号也复制进去。先核对收到的是包含旧→新内容和一次性码的M2预览，而非新候选提案；用户用该码在原QQ确认，读回同一事实成功后才进行条件更正。不要确认本次普通候选、重新保存旧事实，或由代理在后台代执行用户待确认意图。

取得实际新回执后核对原ID/创建时间、有效条件、网页/V4和来源事件，再判定M2；未通过前不进M3。原网页角色时钟旁路及配置隔离债务留待M4，M3评分/倍率、M5逐人事件尚未开始。其他原实机待签、取消ASR/普通文件输出及撤回官方整包保持。
