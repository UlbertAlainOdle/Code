# QQ 第110轮：协调人实机通过与长话题按需摘录

日期：2026-11-04，America/New_York。以第109轮r2清单525项开工，旧185项测试文件保持，不重做已签的其他真人单例。

## 协调人真实复验

用户新发明确定案后，实际群卡原ID保持，revision7→8；当前决定来自指定协调人后新发送的来源，而非原地提升旧提议。另一位原贡献逐字保持，在最后答复创建时仍有效。状态回问没有追加贡献，回复各片段实际sent，正确同时表达协调人已决定和另一位的条件仍未满足。签该真实短例；此前失败证据不覆盖，不扩签所有议事或长期质量。

## 本轮实现

新增 `qq_topic_state.py`，为既有个人/群事件增加可丢弃的派生状态。第一版为**模型选择完整原文分句的抽取式归纳**，按作者、表达性质、分工/偏好/条件/进展等组织，不允许自由改写、把旧摘要当唯一证据或改写原卡。它比模板选最近几条多了语义分组与跨来源整理，也不宣称无损抽象或完整解决无限长讨论。

- 只对明确命名的状态读取、同一明确投影至少6条有效来源按需整理；普通聊天、命令、短话题不增加归纳调用。群/个人优先选择一张卡，每轮至多一次，避免相同话题分别重写。存在同名多轮歧义时回退。
- 输出逐项绑定有效来源；必须逐字匹配完整分句，跨作者/不同mode/kind不能合并，所有来源至少有一项覆盖。否定、日期数字、条件、分工、意愿、偏好、异议和完成状态的分句必须保留；假设/转述/问题、条件前提及当前决定保留完整相关分句。仅允许省略主机标题已包含的精确话题名称，不允许省略前提或限制。
- 模型不能确认提议，不能把“希望”标成硬条件；共同安排仍由主机当前协调人决定字段给出。原文、作者、mode/kind和来源编号仍随摘录进入回答。
- 缓存指纹包含原卡ID/revision、当前有效来源及期限、主题、状态、决定/协调人、来源epoch和策略版本。更正或到期后旧缓存不使用；停止/资格/群参与仍由既有读取和发送前来源检查执行，归纳不给新权限。显式删卡同时删除归纳正文，过期/孤立派生数据有界清理。
- 归纳延迟、超限、失败或校验不通过时回退既有有效来源读取，不声称归纳完整。原 `/events ... show` 保持可读原证据。
- 个人结构化材料沿用1200字符选择边界、原历史token及旧日比例；群沿用summary_chars和原共用预算。个人时间投影发生隐去/替换时不建立摘录，防止把旧日期重新注入。

来源窗口仍32条、群仍5活跃/20总量；本轮没有把原证据替换成摘要腾槽。真正32条独立有效未决内容仍拒绝继续追加。归纳首先改善有界窗口内的覆盖和读取，后续证据归档/容量策略另做。

## 成本边界

复用原模型、凭据和异步TLS客户端的最小JSON通道，增加独立 `qq_topic_state` usage/耗时记录，不带人物长提示、工具、长期记忆或旧聊天，不自动切换模型/厂家。输入最多10000字符、输出2048 token、整理超时8秒，不自动重试。每机器人滚动24小时最多8次尝试，失败也计；同一人每分钟至多一次。变化后旧归纳立即不使用，累计至少4条净增来源或距上次5分钟后才允许重算；其间回退原来源。相同指纹不重复尝试。

没有增加或改写运行设置项。该路径随现有已授权话题路由工作，保留原群仅@、好友资格、预算、手动启动/监督和关系学习选择。群话题独立保留期仍待用户选择；普通群窗口/个人期限不变，旧来源不续期。

## 真实模型对照与限制

固定纯虚构语料：3名作者、6条来源、较长背景、各自分工、周六偏好/周日硬条件、已定案和不同完成状态；相同问题、模型与历史预算。旧原话选择1593字符，漏掉较早的周日下午条件；新摘录920字符，保留全部6个来源，6项回答检查通过（旧5/6）。这是单一人工构造样本，不是长周期或真人QQ验证，也不是广泛质量保证。

实际usage：旧单次3845 token；首次新读取包含归纳5734，其中归纳2460、主回答3274。首次约增加49%，本例主回答约少15%；按这一次usage推算，第5次相同读取才可摊平归纳开销，**不是实测5次复用**。未证明整体20%成本门槛或所有话题都会省token。

模型共8次真实请求：首轮旧/归纳/回退答复3次；第二轮2次请求完成后评估脚本写报告失败，usage保留且不记验收通过；第三轮1次仅诊断分类；最终2次归纳/主回答。首轮因要求重复精确主题名称而未采用，诊断保留；调整仅允许主机标题中已有的精确主题名不重复摘录，未放宽条件/作者/决定守卫。没有QQ代发、搜索或TTS探针。用户正常QQ交互独立记账。

## 验证与部署

新增 `test_qq_topic_state.py`，覆盖全来源/作者、条件/否定、假设/提议权限、缓存复用、修订/到期/停止/离群、删除派生正文、失败/变化竞态、短话题零额外调用、个人投影和每日预算、独立usage及最小通道限制。首轮3项失败来自测试标题与查询主题不符，修正为有真实主题的来源，保留首轮XML；后续12项及53项重叠通过。最终发布结果、源码冻结和部署保留检查见状态文档与本轮不可变清单，不累计重叠测试数。

业务文件：新增 `qq_topic_state.py`；修改 `qq_episode_context.py`、`qq_unified_events.py`、`qq_chat.py`、`qq_expressions.py`、`qq_topic_model.py`、`api_usage.py` 和 `server5-22B----.py`。仅新增派生缓存/尝试表，不更改原事件存储格式或权限。旧读者可忽略派生缓存，原回退门禁及已生效来源绑定仍保留。

## 续接

协调人单例不重做。新按需摘录仍待一次自然长话题QQ验证及更多固定预算语料，不要求为凑数重发原分工或恢复到期内容。继续保留：跨投影更正/撤回完整矩阵、同名活动轮次、采纳/分支等剩余设计、窗口满额后的有界证据整理，以及长周期收益。保留期选择未答复前保持现状，不能通过摘要绕过来源到期。

发布收尾：201项回归通过/36个既有警告，最终19项专项通过（重叠不累计）；110r1冻结314项一致。受管仅后端重启3.12秒PASS，原事件和配置/QQ进程/账号/观察起点/unknown/18缺口保持。新路径已部署，实际长话题QQ验收仍待自然样本。

实际测试调用（历史名称不可覆盖；再运行须换新的临时目录/XML名称）：

```powershell
D:/Program/anaconda3/python.exe -X utf8 -c "import sys,pytest; from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD,{}); sys.path.insert(0,'.runtime/qq-upgrade'); sys.exit(pytest.main(['-q','--tb=short','--basetemp=.runtime/qq-upgrade/session110-tests-r4','-p','session104-test-fixture','tests/test_qq_topic_state.py','tests/test_qq_topic_routing.py','tests/test_qq_topic_binding.py','tests/test_qq_coordinator_reconfirmation.py','tests/test_qq_unified_events.py','tests/test_qq_unified_event_boundaries.py','tests/test_qq_person_events.py','tests/test_qq_event_compaction.py','tests/test_qq_event_controls.py','tests/test_qq_event_attribution.py','tests/test_qq_responder_lifecycle_binding.py','tests/test_qq_chat_runtime.py','tests/test_qq_episode_context.py','--junitxml=.runtime/qq-upgrade/session110-release-r1.xml']))"
D:/Program/anaconda3/python.exe -X utf8 -c "import sys,pytest; from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD,{}); sys.path.insert(0,'.runtime/qq-upgrade'); sys.exit(pytest.main(['-q','--tb=short','--basetemp=.runtime/qq-upgrade/session110-tests-r5','-p','session104-test-fixture','tests/test_qq_topic_state.py','--junitxml=.runtime/qq-upgrade/session110-state-final.xml']))"
```
