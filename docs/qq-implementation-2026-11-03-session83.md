# 第83轮：M1真人候选核对与保存能力措辞

日期：2026-11-03，America/New_York。用户提供本次实际QQ回复；开工第82轮449项指纹全部一致。文件中的聊天内容作为证据，不作为新的执行指令。

## 核对结论

只读关联实际用户轮、AI持久消息与候选记录：本次是普通陈述，未带显式保存指令；后台生成1条pending候选，QQ创建来源及1条当句来源事件均存在。V3/V4均没有对应活动事实，AI持久确认信息也仅列候选，没有实际显式写入结果。因此本次证明候选及QQ来源路径，不证明长期保存、网页同ID或新事实重启保留。M1真实保存门槛继续待签，M2～M5不启动。

候选需要用户确认是正确行为。自由回复却先口头表示已经记住，随后把真实可用的QQ保存服务说成需要用户自行搬到网页，容易让用户误判保存状态；将此单独列为措辞问题，不自动确认候选、不重存原句、不把实际未保存涂成通过。

## 修改

业务只改saya_app/services/qq_chat.py：在本轮已授权主人共享好友私聊的系统上下文中明确保存合同。可以通过服务执行显式保存或候选确认；普通陈述可能只是候选；权限不是提交回执；正文在主机后置写入前生成，不能口头声称已经永久保存，也不能沿用旧机器人关于无保存能力的否认。关闭写入、非主人、群和隔离会话不获得保存许可。人物设定、模型选择、Agent启用、原写服务、权限/版本、来源和设置不变。

候选操作回执在原同意问题前明确当前为待确认、尚未写入长期记忆。仍为有类型ActionProposal，沿用原送达→pending→同意/CAS状态机及长度上限，不进入普通人物历史。已提交事实仍显示原读回保存回执。没有用文本替换器删改人物自由回复；修订后的真人自由措辞仍需正常使用观察，隔离模型替身不代签自然语言效果。

新增tests/test_qq_memory_save_contract.py七例，原155份测试未改。覆盖可写提示/四种禁止范围，以及真正Agent协议适配器、生成器、QQ响应器和候选/事实服务的两条链路：普通陈述只产生有QQ来源的候选和未写入回执；明确保存产生真实隔离事实和读回回执。模型返回脚本化文本，验证提示/路由/状态，不冒充真实模型听从提示的证据。

## 验证

```powershell
D:\Program\anaconda3\python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main(['tests/test_qq_memory_save_contract.py','tests/test_qq_memory_mutations.py','tests/test_memory_origin_context.py','tests/test_qq_capabilities.py','tests/test_qq_recall_topic.py','tests/test_qq_control_receipt_isolation.py','-q','--tb=line','--basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session83-tests-data','--junitxml=.runtime/qq-upgrade/session83-tests.xml']))"
```

**101通过、0失败/错误/跳过、24条既有FastAPI警告，19.04秒。** 外网DNS/socket拒绝，API模型/OneBot为替身。没有新增付费模型/搜索/TTS探针、QQ代发或生产长期事实写入。不为仅提示与候选回执变化重跑旧累计套件、付费对照或已签旧功能；第81轮网页遗留保持独立未签。

## 部署与保留

最终私有session83-release-source.json冻结267项。当前运行选择仍running，原full-control锁、排空、SQLite三库backup/integrity_check后仅后端维护重启**3.14秒**。ready/running/reachable/matched，排空解除。原候选全记录（内容、版本所依赖字段、QQ来源、来源事件、pending状态）重启前后相同，不代确认或清理。

QQ原进程/登录、24控制/继承/网页值、全部原策略、人物/声线/凭据/浏览器/监督/启动与11份保存配置哈希保持；memory_management_enabled仍false。原观察16→16缺口，起点/unknown保持，不补算在线时长。新实际收件文件、实际轮/候选关联、原源码、冻结、XML、三库备份和部署核验仅保存在私有session83-*；公共文档不收录账号、原会话名或原始对话。

## 下次入口

先读[实施状态](qq-implementation-status.md)与[本轮不可变manifest](qq-implementation-manifest-2026-11-03-session83.json)，核对本轮267项冻结。现有候选ID和关联在私有session83-opening-diagnostic.json及本机候选库。已向用户给出原好友私聊的精确/memory-save命令，该命令重新读取同一候选版本，不要求QQ修改管理开关，也不新建第二个候选。短自然同意提案可能10分钟过期，不要求用户反复说同意。

用户确认后只读查实际保存回执、事实ID和网页同条记录/QQ创建来源/确认来源，再按原维护流程做一次新已保存记录的重启保留核对。本轮重启只证明pending候选保留，不能替代未来已保存事实的重启验收。通过M1后才继续M2，并沿用普通补充先预览、明确且唯一更新可直接提交的既定规则。不得由代理将“反馈回复”解释为候选保存许可。原其他实机待签、取消ASR/普通文件输出及撤回官方整包保持。
