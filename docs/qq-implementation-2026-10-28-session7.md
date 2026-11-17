# 第七轮：独立DeepSeek Search / Fetch与搜索厂家切换

日期：2026-10-28，Asia/Shanghai。功能分支`codex/qq-message-bridge`；[继续入口](qq-implementation-status.md)。

## 已交付

- Settings → LLM API → Search provider新增DeepSeek搜索密钥和“启用DeepSeek”，保留Brave及“启用Brave”。单个持久化`search_provider`只允许一家；激活仅提交对应密钥和选择，不保存其他API草稿。
- 配置沿用`knowledge_base/api_credentials.json`，没有第二份密钥库。缺省仍兼容Brave；同源管理会话、CAS修订、原子合并、防不同密钥覆盖、明确清空均保留。不同厂家保存两把密钥，切换不删除、不自动降级。
- 可明确复制本机DeepSeek官方模型密钥。只接受HTTPS官方主机及正常API基础路径，拒绝代理、userinfo、异常端口/查询参数。复制后固定存为独立搜索密钥，后续换聊天模型不影响它。
- 独立`POST /v1/tools/search`及`POST /v1/tools/fetch`适配器，不调用Responses内置搜索、不强制更换模型。网页普通搜索、QQ固定`/search`和自主搜索/网页读取均使用请求开始时同一厂家/密钥快照；已明确选择厂家后，旧浏览器请求密钥不能覆盖全局选择。
- 搜索结果转换为既有来源结构，保留来源编号、提示词证据、摘要降级和读取预算。Fetch正文只作不可信证据，继续检查公网URL/robots、限制响应体和正文长度；鉴权不跟随HTTP跳转，不返回服务端错误正文或密钥。
- QQ能力卡/健康信息显示当前厂家。切换后的新请求生效，正在生成的请求保留旧快照；模型配置和搜索选择的开关相互独立。

## 参数差异及限制

按[官方Search文档](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch文档](https://api-docs.deepseek.com/zh-cn/api/fetch/)和[指南](https://api-docs.deepseek.com/zh-cn/guides/search/)实施。DeepSeek查询及语言/日期条件合计最多128字符；过长明确拒绝，不截断站点/引号约束。语言和相对时间映射为支持的操作符；没有Brave地区参数的等价接口，明确提示，不虚构定位IP。DeepSeek不请求不存在的第二页。

DeepSeek未提供与Brave等价的safesearch/Goggles参数。严格安全搜索拒绝检索；中等模式明确提示不承诺等价内容过滤。Learner原有严格策略继续保留：选择DeepSeek时会明确报告不支持，不暗中放宽、不回退Brave。该限制不能记成Learner严格搜索实机通过。普通聊天和QQ默认中等模式可运行。

HTTP402余额不足与Brave用量上限区分，429、鉴权、无结果、响应异常、超长查询和不支持参数有独立结果；失败不循环消耗请求。源站robots仍影响读取；返回12000字正文是既有截取上限，并非整站完整抓取。

## 验收和部署

- 当前累计Python **389 passed**，12个既有FastAPI弃用警告；JS **64 passed**。证据`session7-release-junit.xml`及`session7-release-node-junit.xml`。覆盖两家切换、固定密钥、CAS/磁盘失败、跨源拒绝、协议、限额分类、工具脱敏、不可信证据及真实生成器中的网页/QQ路由。
- 初次累计回归388通过、1失败，发现Learner终态可见与Future清理竞态；保留原断言，修复终态与引用清理顺序及注册窗口后完整389通过。原失败证据`session7-regression-junit.xml`保留，不覆盖。
- 隔离真实浏览器已验证Brave→DeepSeek→Brave、复制假模型密钥、单一高亮、旧页面冲突拒绝、草稿保留及冲突后立即核对当前厂家。隔离页面和核验过所有者的临时进程已关闭。
- 最终后端约13.83秒恢复；QQ进程/账号、17项值、群授权、共享默认、用户16项非API偏好、NapCat与自启动选择保留。观察起点和缺口不重置。
- 已在生产启用DeepSeek，固定复制已有官方模型密钥；原Brave密钥及其他配置保持原值。真实独立Search返回3条结果，Fetch成功读取官方文档并按上限保留12000字；共1次Search、1次Fetch，无模型请求或QQ代发。证据`session7-provider-activation.json`、`session7-real-search-fetch.json`，不公开私有配置正文。
- 用户随后使用主人QQ执行 `/search DeepSeek Search Fetch 官方文档`，确认收到回复且来源链接可访问，真实QQ新搜索验收通过，私有回执为`session7-qq-live-confirmed.json`。模拟生成器与该真实回执分别记证据，不混算。其余QQ-01/02门禁继续保留，QQ-03～08未开始。

本轮也补齐第六轮中断交接，见[第六轮补记](qq-implementation-2026-10-28-session6.md)；补上此前未落盘的项目列表网络失败保护及JS用例。没有重做旧回退或已通过的真实命令冲突。

## 后续入口

当前源码以`qq-implementation-manifest-2026-10-28-session7-qq-live.json`为准，确认前的第七轮清单不覆盖。先核对状态、指纹和已通过QQ实机回执，勿再要求配置Brave Key或重复付费探测已通过的独立Search/Fetch及QQ搜索。继续补QQ-01手工组合与QQ-02真实观察/网络/启动门禁，保留严格顺序。修改搜索厂家只用启用按钮；不要把旧缓存中的厂家值导入覆盖服务器选择。
