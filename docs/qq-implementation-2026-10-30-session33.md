# 第三十三轮：主人称呼联动与提示审阅

2026-10-30，Asia/Shanghai。用户要求先做主人用户名/提示词主人称呼联动，允许非必要旧实验延期，明确禁止修改人物设定。182开工指纹全匹配，qq_chat.py/qq_presentation.py原源私存，人物资料/风格文件及基础人物提示常量另存指纹。

## 实现

仅改QQ传输提示层。sender_identity_context把Doctor/主人及人设owner-specific的user解释为已配置主人身份；当前已验证主人账号的群名片/昵称为当轮别名，结构化owner_alias_binding与当前speaker_labels一起传给实际API/agent/本地提示。更名直接随当前消息更新，不建立另一主人、不写长期记忆或新跨端账号绑定。其他人即使昵称Doctor/主人也无主人别名绑定，不继承主人私人历史或专属关系；姓名缺失不推测。

保留人设原有称呼偏好、性格/亲密关系/社交风格，不机械每句叫用户名。普通提示不再注入整套群管理命令，只有主动帮助才提供。QQ表达提示补充不播报接收/搜索/完成、不解释内部账号映射；没有重写profile、style、基础system/api人物提示、记忆事实、Minecraft姓名或网页默认身份。

## 验证与失败保留

新增tests/test_qq_owner_alias_prompt.py：主人两个名字同属一个身份、更名实时联动、同名成员不提权、缺名/重名、只在求助时显示命令。现有实际API两群成员身份相邻提示、群权限、安静/禁搜、引用和实际生成器组合继续通过。

项目Saya Python：

```text
-m pytest tests/test_qq_owner_alias_prompt.py tests/test_qq_quiet_identity.py tests/test_qq_groups.py tests/test_qq_presentation.py tests/test_qq_quotes.py tests/test_qq_capabilities.py tests/test_qq_chat_runtime.py tests/test_qq_task_combination.py -q --junitxml=.runtime/qq-upgrade/session33-final-v2.xml --basetemp=.runtime/qq-upgrade/session33-final-v2 --tb=short
```

最终166通过、12既有FastAPI警告，无skip/xfail。初版新测试假设身份JSON独立成行，但初版提示JSON接在段落后，3项解析失败/163通过；身份数据改独立行、语义断言保留，首份session33-final.xml不覆盖。前端未改，无重复JS/799累计回归。

## 部署与进度

正常排空、三库一致性备份后仅本项目后端重启约3.14秒，PASS。QQ matched、ready/running，23项/继承/配置凭据/浏览器存档及人物文件风格哈希保持；17结果36段17策略及群授权不变，QQ原进程保留。基础system_prompt/api_system_prompt/saya_identity_reminder AST指纹一致；原观察起点/4缺口及监督/自启动选择保持。无QQ代发、付费探测或真实聊天验收冒充。

已更新状态、总验收、C04入口/执行表、路线图执行位置，并形成[阶段审阅](qq-stage-review-2026-10-30-session33.md)。当前第4/8阶段，前三阶段完成，QQ-04实现部署完成但整体门禁收尾；复杂组合M按用户要求DEFERRED不再催做，不标实机PASS。QQ05～08未开始，人物设定保护贯穿后续。最新[第三十三轮清单](qq-implementation-manifest-2026-10-30-session33.json)，私有session33-resume.json。
