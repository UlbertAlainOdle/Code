# 第三十六轮：群记忆细粒度权限基础

2026-10-30，Asia/Shanghai。用户明确要求群聊不直接失忆，按主人/其他人、可说/可写及谁提问区别。已询问并收到确认，“IP”指QQ ID/账号。属于用户主动修改群记忆范围，原一律隔离不是最终目标；原QQ05未验事实与QQ04延期保留，不冒充阶段PASS。195开工指纹匹配。

新增saya_app/services/qq_memory_access.py及tests/test_qq_memory_access.py。实现独立ACL元数据存储和纯策略/重新授权入口，主体/群/机器人/记忆内容版本绑定，管理CAS、默认private、指定群/公开受众、独立指定requesters及writers。owner在群提问仍要满足群公开条件；成员不能改写owner-private，委托成员写入仅自己/原群/明确保存/本人来源。正常敏感度不自动公开，secret/未知拒绝。只记录权限元数据，不复制记忆正文。

读grant与实际输出受众绑定；重查ACL版本、当前记忆版本/actor权限，撤销后旧生成不得继续披露。写前重查委托，关闭保存不落盘。可信Actor来源与管理发布确认须在接线阶段使用真实鉴权，不能把模型metadata或request body布尔值当授权。旧记忆尚未ACL化，不自动群公开。

项目Saya Python：

```text
-m pytest tests/test_qq_memory_access.py tests/test_qq_owner_alias_prompt.py tests/test_qq_context_links.py -q --junitxml=.runtime/qq-upgrade/session36-policy-final.xml --basetemp=.runtime/qq-upgrade/session36-policy-final --tb=short
```

最终38通过，无skip/xfail/警告（15新ACL用例与23身份/接续契约）；初版36通过保留。新增覆盖私聊能记/群未授权不能说、指定群和发问者、显式全员请求、非法/非验证/跨bot身份、内容版本/错误条目/secret、主人群内明确保存、成员不得覆盖主人、委托只写本人原群、群/非主人无发布权、CAS、策略撤销与在途grant/写委托失效。没有重复已部署229/11或付费验证。

**未接生产**：实际检索前过滤、提交/发送guard、逐条授权UI/API、群主体候选隔离和缓存撤权仍待。线上仍第三十五轮，群记忆暂仍旧隔离；不能说用户要求已经全部可用。无部署、生产表创建、旧正文迁移、人设/23设置/凭据/浏览器/群/观察/监督自启动变更，无QQ代发/付费探测。

接下来按[权限规则与接入顺序](qq-memory-sharing-policy-2026-10-30.md)先可信主人逐条授权管理，再统一读取/写入/发送路径。保留QQ05已部署及实机选源待办，不重做旧多窗口实验。用户询问看图：QQ08第8阶段；当前第5已部署，第6记忆管理与第7群语境在第8之前，未承诺具体日期或把网页多模态说成QQ已看图。最新[第三十六轮清单](qq-implementation-manifest-2026-10-30-session36.json)，私有session36-resume.json。
