# 第三十四轮：QQ-05独立接续契约

2026-10-30，Asia/Shanghai。用户明确“进行下一阶段吧”，记录为主动推进范围调整，当前第5/8阶段；原QQ04延期M及自然聊天修复待反馈保留，不倒填PASS。开工185指纹匹配，人物设定、既有运行设置与线上源码不改。

新增saya_app/services/qq_context_links.py、tests/test_qq_context_links.py。增量独立qq_context_links表仅关联元数据，构造不会操作inbox/记忆/投递；当前没有生产导入。以bot/主人好友私聊scope保存选源、generation、SHA、选择/到期时间；默认无关联、内部显式确认+CAS选择，可撤销和按bot全撤销。snapshot只对主人/共享/非群读取有效源，排除QQ/MC/统一和非法会话、临时标记/坏JSON/超限源；12条/12000字、有界2MiB源和1小时TTL，不调用摘要模型、不缓存正文。

快照保留来源版本/选择版本、时间/消息范围、private/status，读取时再核验关联代次；valid在源读取后再查撤销/切换，源文件变化或过期不接受旧结果。共享关闭观察到时撤销，真实设置事件无请求时仍必须由后续接线主动revoke_bot，不能因契约测试而宣称实际关闭路径已完成。历史内容按DATA包装、system/tool行不导入。MemoryManager适配器只读文件，实际临时管理器测试源字节及current_session_file不变。

项目Saya Python运行：

```text
-m pytest tests/test_qq_context_links.py -q --junitxml=.runtime/qq-upgrade/session34-contract-final.xml --basetemp=.runtime/qq-upgrade/session34-contract-final --tb=short
```

最终20通过，无skip/xfail；初版20通过见session34-contract.xml，补来源范围/读取末尾过期检查后重新20通过。没有重复旧166/799/JS或无变化基线。覆盖单源隔离、CAS、默认关闭/确认、群/非主人不读源、撤销/共享重开/过期不复活、源改删/读取中切换、损坏/临时/大文件、历史注入数据边界以及实际管理器只读。

**尚未完成**：生产初始化、网页同源确认API/UI、实际QQ命令/reset/设置撤权、真实生成器的权限/guard/版本传递和发送前屏障、部署及真人验收。没有为新模块重启服务、迁移生产表、修改原23控制/人物/凭据/浏览器/群/观察台账或监督/自启动，也没有QQ代发/付费探测；线上仍第三十三轮，不称QQ05可用。

已更新路线图/进度/阶段审阅，建立[QQ05接入入口](qq05-integration-2026-10-30.md)和[逐项验收](qq05-acceptance-2026-10-30.md)。下一步从网页确认与可信scope授权接线继续，复用本模块，不重写契约或旧实机实验。QQ06～08未开始。最新[第三十四轮清单](qq-implementation-manifest-2026-10-30-session34.json)，私有session34-resume.json。
