# 第88轮：网页角色隔离、长期有效期与有界投影核对

日期：2026-11-03，America/New_York。承接第87轮463项清单；M1/M2已签单例及M3确定性合同保持。QQ修改管理、来源轻偏好均保持true，旧等级继续逐条预览。原162份测试未改，未重置/提交既有工作区。

## 本轮工程

原网页多角色正文已经分离，但时钟的message_hint使用整个会话，且记忆失效后的上下文重建固定使用Saya角色。现在`entries_for_agent`统一过滤正文和时间；多人请求捕获同一轮开始时各自可见的时间行，其他角色新回复不从时钟旁路进入。记忆epoch变化后的重建仍保留当前角色。普通Saya、临时对话及QQ已有话题选择保持。第81轮该项原失败用例现已实际通过，不抹掉当时失败。

V4召回新增尚未生效的valid_from门槛；明确未来时间范围查询可以覆盖该范围，普通当前召回不会提前把未来状态当事实。有效期和范围改为比较实际时间点，兼容带偏移时间和纯日期的日末，不再直接比较字符串。未将准备事项批量改成已完成，也未重评持久偏好。

新`memory_health.py`及GET `/api/kb/health`分页只读核对旧payload/全文索引、当前提供者向量正文hash、V4正文/分数/来源/等级/锁/星标及待投影。每页最多200项、SQL进度回调与2秒预算，报告窗口/下页/缺项，结果不含事实正文或标题；反向孤立记录扫描明确未做，不冒充全库健康。不会初始化提供者、生成向量或写事实。原迁移排除和canonical首尾空白规范化分别识别，避免当作新数据丢失。

网页原记忆设置页增加手动只读检查和逐条修复预览；原布局/浏览器配置不重写。POST `/api/kb/health/repair`仅修指定版本的派生全文投影，或者把已提交的原生V4版本同步回原投影；旧版本、待对账和删除冲突拒绝。原生独有但旧JSON还没有的待投影事实也可看见，不引导另存。自动向量生成及压缩仍由原机制控制，不暗开新后台。

原`reconcile_v4_projections`增加每批最多20项/2秒及取消边界；网页一次只修1项，绑定V4内容和删除状态版本。启动只恢复既有待投影，不重放原控制。超预算保留剩余清单，可再继续；不能把partial显示成整库已完成。单个既有同步调用进行中不强制打断，2秒为条目间调度预算，不能冒称所有磁盘操作都有硬超时。

修改：`core_services.py`、`saya_app/routers/memories.py`、`saya_app/services/{chat_agents,chat_time_context,multi_agent_chat,memory_health}.py`、`memory_v4/retrieval.py`、`server5-22B----.py`、`static/js/app.js`；新增`tests/test_memory_long_term.py`。本轮未新建数据库schema或修改原人物/心情过滤器。

## 验证

Python命令均先执行`qq_rollback.NETWORK_GUARD`禁外网，使用`D:/Program/anaconda3/python.exe -X utf8 -c`调用pytest。真实生成器配模型/OneBot替身，不算真人QQ或真实模型长期效果。

- `tests/test_chat_agents.py tests/test_time_context.py`：9通过，6既有FastAPI警告，9.07秒；包含原角色泄漏失败用例。
- 首轮4文件：51通过/2失败，6既有警告。1个新增异常分支漏导入已修；1个新夹具将未生成向量也断言成投影错误，改为隔离夹具关闭嵌入并持久化。失败证据保留，原测试未改。
- 扩展8文件：`test_memory_long_term.py test_chat_agents.py test_time_context.py test_memory_v4_phase3.py test_memory_revisions.py test_memory_web_cas.py test_memory_control_conflicts.py test_memory_importance_policy.py`，121通过，12既有警告，29.93秒。
- `tests/test_memory_capacity.py tests/test_memory_ai_compression.py tests/test_qq_context_salience.py`：27通过，6既有警告，12.32秒，覆盖原容量/永久事实/否定与结构化压缩、冷存回滚及QQ降权上下文。
- 最终`tests/test_memory_long_term.py`：14通过，6既有警告，13.17秒；含后加规范化/排除规则。JS语法通过，原4份记忆UI Node测试23通过。范围重叠不累加。

新用例覆盖自定义角色epoch重建的真实请求捕获、30/90天隔离时钟的持久事实/临时过期/未来状态、跨网页/QQ12次修订的同ID/来源/去重、并发旧版本、停用及重启、分数V3/V4/重启一致、索引部分缺失及单条修复、原生独有投影、CAS、取消/条目数/时间预算和当前提供者陈旧向量。30/90天是模拟，不补原在线时长，也不是自然问法长期命中率或真实模型收益评价。

## 部署及实际只读状态

最终279项源码冻结一致，沿用原双锁、排空、三库备份和完整性检查，只重启受管后端3.09秒PASS；ready/running/reachable/matched，排空解除。原QQ进程/登录、当前M2事实、候选/确认期限、24控制/继承/网页及原人物/声线/凭据/浏览器/监督/启动保持。原11配置加用户已授权的来源策略均保持，来源偏好true；原16观察缺口/起点/unknown不减。

实际新健康API HTTP200：检查30条，无下一页、待投影0；1条旧记录按原规则排除（dangling_start），仅报告不擅自补全/迁移。没有因诊断执行生产修复、重评分、等级初始化或压缩。原重要级预览仍30条、生产初始化0，来源策略读回true。首次只读报告中的首尾空白及排除提示误分类已修正，前后报告留存，不改旧资料迎合检查。

无新付费模型/搜索/TTS或QQ代发。私有session88-release-source.json冻结279项，部署备份及全部失败/通过报告留存；公共文档无账号、原始对话、日志或正文。新[不可变清单](qq-implementation-manifest-2026-11-03-session88.json)承接第87轮，不覆盖历史。

## 下一步及未签范围

M4本轮工程已部署，真实自然长期效果、稀疏使用样本、反向孤立记录完整扫描及更大规模维护耗时仍需另记，不能签成整套长期效果已证明。原第81轮其余受管配置测试假设的失败/隔离诊断保留，不为无变化范围重复付费探针。

接续M5先只读统计旧事件scope/可信主体/混合公共卡与原开关，形成dry-run；身份与可见性分开、关系只改预算不改权限。不得按昵称猜人、跨scope公开私密标题/计数、清空旧卡，或未经收益门槛启用事件。先核对第88轮清单，保持两项已授权true、原事件关闭选择及16观察缺口。
