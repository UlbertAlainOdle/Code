# 第80轮：自然提问漏召回已存长期记忆

日期：2026-11-03，America/New_York。先读第79轮状态，核对438项指纹一致，相关源码和配置留私有备份。用户反馈主人私聊中`/memory search`能查到，但自然询问不知道或答错。本轮修复读取衔接，不重写保存机制或人物。

## 已确认的证据与边界

本次确实是主人私聊，shared_memory=true、recall_mode=on、agent_mode=true。目标在V3/V4两库及本地向量索引都存在、状态有效，不是未保存、索引未同步或群权限问题。该次Agent共3个工具调用，审计只选入2条不相关记忆，没有目标；机器人随后错误地把本次未找到说成没有相关保存记录。

只读词法回放中，明确两字主题能将目标排第1；原整句问题排第3。一次真实本机只读查询原问题也能返回目标、排第3，证明原库可读；不能把这等同于当时Agent确实用了这个查询。工具参数只留哈希，本轮有限核对没有还原出实际参数，因此**不声称已证明模型当时输入了哪两个查询词，也不把某个猜测写成唯一根因**。生产原问题、目标ID/内容、回复、审计和相关来源仅保存于私有`session80-*`，不进公开文档。

## 修改内容

- 新`memory_query_topic.py`仅从直接、明确的过去话题问句识别主题，例如虚构的“最近我说了什么关于园艺的事情”。不含领域专属事实；引用、代码、指令、否定/排除或不明确主题不做猜测，普通聊天不强制查询。
- `qq_chat.py`只在已授权主人共享私聊且召回未关闭时，给现有记忆工具传递本轮topic_seed；不修改持久开关、不关Agent，不把搜索回执带进普通历史，也不将群/其他人的提问导入私人库。读取提示同时说明有限检索不是全库不存在证明，当前真实记录优先于旧机器人否认；不能要求用户把已存事实再保存一遍。
- `core_services.py`现有memory.query第一次读取优先保留此明确主题，之后仍可由模型细化查询。继续使用同一个工具、原次数和预算，保留kinds、scope、time过滤及使用授权，没有新增串行分类模型或另一套召回控制器。工具结果明确标为current_question_topic。
- `memory_store.py`补齐trigram FTS不能覆盖中文两字主题的问题：对精确两字查询，在原活动/未删除/项目范围和数量限制内查标题/正文/标签，只返回候选ID，防止宽泛向量候选排除确定的词法证据。普通长词FTS/向量检索原样；新路径不复活删除/停用/归档/他项目记录。

没有修改原记忆内容、重新训练/重建向量库、启用网页默认值或新永久写入。网页共享此SQLite候选修复，但QQ首轮主题提示限定当前已授权QQ路径，不据此宣称网页真实自然问答已验。

## 测试

新测试独立`tests/test_qq_recall_topic.py`，创建前确认不存在。最初2及二次2失败均为新夹具问题：add_memory参数、后续保存覆盖了临时项目标记、测试模型思考配置、只替换普通流而没有替换Agent原生适配器；保留失败，修正夹具后实际运行原生AgentLoop/工具/生成器。27专项先通过，随后补一条停用/过滤检查。

广域命令：

```powershell
D:\Program\anaconda3\python.exe -m pytest tests/test_qq_recall_topic.py tests/test_memory3.py tests/test_memory_v4_agent_adapter.py tests/test_memory_v4_phase3.py tests/test_memory_v4_phase4.py tests/test_qq_memory_use_policy.py tests/test_qq_capabilities.py tests/test_qq_memory_natural_access.py tests/test_qq_chat_runtime.py tests/test_qq_memory_policy_isolation.py tests/test_qq_control_receipt_isolation.py tests/test_qq_attachment_authorship.py tests/test_qq_voice_capabilities.py tests/test_chat_prompt_web.py -q --tb=short --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session80-final-tests-r2-data --junitxml=.runtime/qq-upgrade/session80-final-tests-r2.xml
```

**270通过、1条已知遗留失败、48条既有FastAPI弃用警告，87.08秒。** 遗留为`MemoryV4ChatWiringTests.test_v4_main_read_acknowledges_actual_response_usage`对旧源码feedback字段的断言；第77轮已记录，本轮server源码与开工备份逐字一致，该断言所需字符串开工时已不存在，不计通过、不改旧断言。一次更早命令误写不存在的测试路径，0收集，原失败XML保留。

最终聚焦复验：

```powershell
D:\Program\anaconda3\python.exe -m pytest tests/test_qq_recall_topic.py tests/test_memory3.py tests/test_memory_v4_agent_adapter.py tests/test_qq_memory_use_policy.py -q --tb=line --basetemp=D:/Dear/Talk/mistral_models/Code/.runtime/qq-upgrade/session80-final-focused-data --junitxml=.runtime/qq-upgrade/session80-final-focused.xml
```

**82通过，0失败/错误/跳过，6条既有警告，24.56秒**，与前270范围重叠不相加。152份原测试文件指纹保持。覆盖多个虚构主题、引用/否定/普通问句、主人/非主人/群/未共享/召回关、首次主题及后续细化、预算和模型过滤、停用/删除/归档/项目隔离，以及无关向量池仍保留精确两字事实。真实生成器和原生Agent适配器使用模型替身，验证空历史下工具结果含旧事实及审计，不算真实模型或QQ签收。

## 部署与待验

最终261项私有源码冻结通过，原锁/排空/三库完整备份、仅后端维护重启**3.09秒ready**，解除排空，ready/running/reachable/matched。原QQ进程/登录、24控制/继承/网页值、memory_settings、媒体/收藏/表达/新策略、人物/声线/凭据/浏览器和监督自启动核对保持；仍random/revision3、体验总开/短句开/篇幅试用关。观察原起点/unknown、16缺口保持，不补24小时信用。原管理面板正常关窗停止语义不变。

只做一次真实本机只读检索探针，无新付费模型/搜索/TTS探针、人工QQ代发、永久记忆写入或清空历史；没有重跑付费48对照或1112累计。原ASR/普通文件输出取消、官方整包撤回及原八阶段未签门禁保持。

用户已经只重发原自然问题并确认**这次答对原有相关事实**。只读核对同一主人私聊、问题与失败前逐字一致；新回答的实际召回审计命中原目标ID，发送气泡均sent/有回执。失败时选入2条却不含目标，本次选入1条即原目标；工具调用3→1。无需重新搜索、把答案写入问题、重存事实或重置聊天。**这一个真实长期记忆自然召回用例通过**；不把单例工具数变化称为总体准确率/性能提升，也未将prompt_included自动改记为已证明used。

本项不再重复，其他问题形式/跨端/群逐条授权仍按各自证据。后续按[小步验收](qq-live-acceptance-2026-11-03-session78.md)推进其他独立项，已签语音/附件来源不重测。

后续入口：[最新状态](qq-implementation-status.md)及第80轮不可变manifest；核对私有session80-release-source.json共261项和当前用户意图/开关。此项已签，不重做；有新漏召回反馈时再区分未调用工具、候选未命中、被过滤及模型拿到却未使用，不恢复旧缓存、改人设、复写记忆或扩大权限。其余分享记录/可选策略等实际验收继续单列。
