# QQ第63轮：按语义选择表达，取消固定类别优先级

日期：2026-10-31，America/New_York。用户反馈收藏不适合时几乎每条都加笑脸，颜文字和语音不出现，明确将原类别优先级改为结合语义选择。第62轮350指纹一致；本轮只修改表达选择服务、收藏页HTML/JS和对应Python测试，不修改人物/风格/声音文件。

## 原因与修正

第61轮选择提示要求每类各给一个候选、channel通常expression；服务器再选收藏→face→颜文字的第一个。因此宽泛“微笑/友善”容易匹配普通回复，face盖过颜文字；语音在提示层又被放在不常走的分支。这是应用选择设计问题，QQ显示正常或一次收藏发送通过不能证明日常表达自然。

只读诊断显示语音配置enabled、本地引擎已加载clone且空闲，近期12条正文11条符合原120字上限；近期表达为face，未发现对应新语音尝试。不能将本次“没有语音”简单归因于用户没开开关。未为诊断合成音频或改变声线。

新规则：

- 模型直接选一个 `kind=none|sticker|face|kaomoji|voice` 和合法编号；auto使用这一个语义结果，不再每类列候选后由代码取第一项。文字已经足够就none。收藏、QQ小表情、颜文字、语音没有固定优先级，不按类别回退。
- 日常陈述、确认、回答问题、友好本身不构成加笑脸理由；表情需对当前情绪/幽默/亲近等有具体表达价值。颜文字补充语义说明，语音按声音对安慰/祝福/招呼等的价值及当轮想听意图判断。没有强制轮换或类别配额，也不保证每类都出现。
- 提供同一真实账号、同一会话最近最多3次结果的类型/编号/状态/相对秒数，提醒谨慎重复，尤其连续笑脸；没有历史正文、跨群/跨账号/私聊内容，也不新建长期行为档案。复用短期投递记录，自然到期；不恢复旧对话或用日志采集私人历史。
- 语音可用性同时检查原媒体开关、长度、当轮拒绝、实际TTS开启/本地提供商及忙碌状态。原120字/时长、先文字后附加声音边界保持。没有扩成任意长篇朗读或改变音色。
- random只在模型明确认为本轮合适的候选中随机，可含none；指定模式仍受语义和本轮拒绝限制。非法/不可用结果和选择/下载失败只保留文字，不补默认笑脸或换另一类。取消/权限/回执不明/重启不重放、最多一种附加表达及每批5气泡保护保持。

状态接口投影 `selection=semantic_choice`、`priority=[]`，原mode/revision不改。QQ查询命令及网页说明同步更新，页面资源session63。当前auto/revision1、5张用户指定说明保持，无需重新指定或保存模式。

## 验证及证据范围

修改文件：`saya_app/services/qq_expressions.py`、`static/qq-favorites.html`、`static/js/qq_favorites.js`、`tests/test_qq_expressions.py`。

- 45项表达聚焦检查通过（4条既有FastAPI弃用警告）；9文件147项相关回归通过（12条既有警告），0失败/错误/跳过。数字重叠，不相加。含语义选择颜文字/face时不被收藏盖过、none/无效结果不补笑脸、auto语音与收藏/face互斥、本轮拒绝、random合法集合、近期账号/群/私聊隔离、取消/未知/过期与原语音/分段/群/转发兼容。
- 3项Node模拟DOM检查及JS语法通过。页面版本/文案HTTP检查另记，不冒充实际浏览器外观验收。
- **真实已配置模型的3条虚构短对话，通过实际生成器和新选择提示，分别得到none、kaomoji/shy、voice。** 普通确认不带类别要求；颜文字和语音两例明确表达相应意图，证明这些分支不再被旧优先级挤掉，不扩大成所有自然语义场景的主观质量已通过。每例约0.55至0.69秒，无QQ客户端、无代发、临时空白KB/会话、关闭检索/写入/agent/搜索，生产配置摘要保持，未新增会话。
- 语音实际状态为本地已加载/空闲；本轮没有新音频合成、QQ音频传输或听感实验。真实模型返回voice仅是选择层证据，自动FakeTTS也不能代签真实音频。

```powershell
python -m pytest tests/test_qq_expressions.py -q --tb=short --junitxml=.runtime/qq-upgrade/session63-focused.xml --basetemp=.runtime/qq-upgrade/session63-focused
python -m pytest tests/test_qq_expressions.py tests/test_qq_voice.py tests/test_qq_favorites.py tests/test_qq_official_packs.py tests/test_qq_task_scheduler.py tests/test_qq_task_controls.py tests/test_qq_delivery.py tests/test_qq_group_integration.py tests/test_qq_forward.py -q --tb=short --junitxml=.runtime/qq-upgrade/session63-expanded.xml --basetemp=.runtime/qq-upgrade/session63-expanded
node --test tests/qq_favorites_sources.test.cjs
node --check static/js/qq_favorites.js
```

## 部署与续接

排空0.01秒、三库一致性备份通过、后端重启4.77秒，原QQ实例保持，ready/running/matched。实际认证API回读semantic_choice/空priority且原auto/revision1；原24控制/每批5、六媒体revision1、5项指定/说明、官方样本、人物/声线/凭据/浏览器/新解释器绑定及监督/自启动选择保持。观察原起点/7缺口/unknown不减，不重置或补算在线。

私有session63-before/source-before/focused/expanded/semantic-model/三库备份/deploy/final-verified/resume保存原配置摘要、实验与部署证据，不公开原始身份、私人说明、URL或对话。代理0QQ发送、3次新的隔离选择模型请求、0搜索探针、0TTS合成；这些模型实验不是重复历史Search/Fetch探针。

用户直接按原方式聊天即可，不用改命令、重新指定表情、开关声音或重做复杂实验。整体仍第8阶段验收，旧群收藏及face回执是原传输证据，不代签新语义日常质量；根据后续具体反馈检查本轮单次决定和发送状态。仅在用户要求时使用原单类模式；不要为证明多样性强制轮播/随机配额，也不要恢复固定类别优先级。官方整包继续撤回，ASR/普通文件输出继续取消，旧独立待签范围保留。
