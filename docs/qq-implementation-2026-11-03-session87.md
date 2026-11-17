# 第87轮：重要级、评分解释与QQ来源轻偏好

日期：2026-11-03，America/New_York。承接第86轮M2单例实机通过；M1/M2不重做。最终源码冻结275项全部一致，原161份测试未改。工作区既有改动保留，未提交、重置或修改浏览器所有权。

## 交付与生效状态

M3已实现、隔离验证并部署。用户明确选择“启用轻偏好，旧等级保持逐条预览”；已通过版本化策略接口开启并读回，`knowledge_base/memory_importance_policy.json`的enabled为true、倍率1.08。QQ修改管理继续true，其他24控制、继承、网页设置及原配置保持。以后不得用旧轮次的false恢复这两个选择。

30条现有记忆仍只有只读等级预览，生产初始化数为0；原分数、正文、星标与锁定不变。未批量重评、初始化旧等级或补造来源。新合法写入可初始化等级；旧条目在明确逐条初始化或后续合法更新时才持久化。用户没有授权批量操作。

## 实现

- 新`memory_importance.py`把ordinary/attention/important/critical重要级与about_you/curated/auto兼容来源档分开；阈值0.45/0.70/0.85、滞回0.03、规则版本1。人工分数可升降，重要性锁独立于星标及内容锁；自动操作不能解锁或置顶。补充、更正、恢复保留人工锁定选择，理由及before/after审计最多20项。现阶段长度调整为0，等义长短不加减分，不宣称已有通用语义重评分器。
- 原KnowledgeBase、V3/V4迁移/写管线/恢复及双向投影携带白名单等级字段。模型候选不能指定可信等级或锁。无查询兼容评分保持；网页查询不启用QQ偏好。
- 仅可信QQ主机入口且策略开启时，对已验证QQ创建来源使用查询视图贡献：`0.10*(min(1,salience*1.08)-salience)`，额外总分不超过0.008。先过权限/范围/删除/停用/相关性门槛，再在截断前排序；V4和旧回退只加一次，不写回分数/等级。QQ审计、Agent实际选入及`/memory why`能区分基础分和来源贡献。
- 网页原卡片显示等级、权重、锁和来源档，菜单提供逐条等级预览/初始化及重要性锁。原版本CAS保留；初始化备份knowledge.json、memory_settings、策略和两库到私有备份目录，V3/V4投影不一致返回待对账，不把部分提交误报为完整成功。再次初始化仍检查投影，不用“已初始化”掩盖失败。
- 回退读者保护新增`memory_importance_v1`，覆盖启用策略、旧投影等级字段及V4原生已提交而投影未完成的等级数据。原不可变版本账本未裁剪，不能把显示20条审计说成总存储已封顶。

修改文件：`core_services.py`、`saya_app/routers/memories.py`、`saya_app/services/memory_importance.py`、`memory_v4/{migration,retrieval,store,synthesis,write_pipeline}.py`、`qq_memory_management.py`、`qq_rollback.py`、`server5-22B----.py`、`static/js/app.js`及新`tests/test_memory_importance_policy.py`。

## 验证及边界

所有Python调用先执行`qq_rollback.NETWORK_GUARD`，禁外网，模型/OneBot为替身。命令前缀为`D:/Program/anaconda3/python.exe -X utf8 -c "from saya_app.services.qq_rollback import NETWORK_GUARD; exec(NETWORK_GUARD); import pytest; raise SystemExit(pytest.main([...]))"`；精确选集和原始JUnit保留在私有session87证据目录，不公开机器名或原对话。

| 范围 | 结果 |
| --- | --- |
| 初始原回归6文件 | 86通过，6既有警告，17.51秒 |
| 新策略初轮 | 22通过/1失败：新夹具把词法平局误作强网页命中；保留失败，改用明确语义分差的夹具，未改旧断言 |
| 扩展10文件 | 173通过，12既有警告，38.15秒 |
| 发布7文件 | 124通过，6既有警告，26.32秒 |
| 最后策略增补 | 28通过，无警告，7.74秒 |
| 最终策略与回退读者：`tests/test_memory_importance_policy.py tests/test_memory_rollback_gates.py` | 33通过，无失败/错误/跳过/警告，9.32秒；其中新策略29例 |
| `node --check static/js/app.js` | 通过 |
| `node --test tests/memory_revision_ui.test.cjs tests/memory_controls_ui.test.cjs tests/memory_mutation_ui.test.cjs tests/qq_memory_management_ui.test.cjs` | 23通过；最后文字微调后再作语法检查 |

范围重叠不相加。覆盖阈值、升降滞回、人工锁/星标、原分数迁移备份、版本冲突、投影部分失败、同相关性截断前轻偏向、强网页胜出、权限/相关性门槛、伪标签/未知来源无贡献、重复查询不写回、旧回退和网页不受影响、等义长短、审计20项、恢复保留现锁及原生V4回退读者。

实际已保存数据只读验证：同一虚构事实网页基础分0.880，启用QQ主机作用域后的查询视图0.884、贡献0.004；持久化salience/等级完全不变。实际策略读回和主机作用域开关均通过。此为真实存量数据的确定性只读对照，**不是新OneBot收件、真实模型收益或长期效果验收**。正常使用中的稀疏样本继续观察，不新增付费探针或要求用户守测。

## 部署与保留

沿用原双锁、排空、三库备份与完整性检查，仅重启受管后端；首次3.19秒，最终补充原生V4回退保护后3.12秒，均PASS并解除排空，ready/running/reachable/matched。原QQ进程/登录、当前M2工作日事实/原候选/预览及期限、24控制、人物/声音/凭据/浏览器/监督/启动和原11份配置哈希保持。之后按用户授权新建并开启一份来源偏好策略，无需再重启；原11份配置及knowledge.json仍不变。

原观察起点、unknown及16个缺口保留，不补24小时在线信用。无QQ代发、付费模型/搜索/TTS、生产事实写入、批量迁移或事件开关改变。私有`session87-release-source-r2.json`冻结275项；初版冻结、两次部署备份、失败JUnit和只读证据全部保留。新[不可变清单](qq-implementation-manifest-2026-11-03-session87.json)承接第86轮，不覆盖历史。

## 恢复入口与下一步

先核对第87轮清单；QQ修改管理及来源轻偏好均保持true，旧等级继续逐条预览。M3确定性合同和部署已交付，真实模型效益另记，不冒充已经验收。

接续M4：先修已定位的网页多角色`clock_context`使用整会话`message_hint`旁路，再覆盖长期序列、有效期、去重/失效、索引健康和有界维护；用隔离30/90天时钟明确区分模拟和在线观察。不得重跑M1/M2已签案例或旧付费探针。M5按人事件、关系容量和跨scope迁移仍未开始；实施前必须只读统计/预览，不能直接猜主体或打开生产事件策略。原八阶段其余待签/取消范围不变。
