# QQ 第二十四轮：QQ-03完整验收与QQ-04独立契约

2026-10-29，Asia/Shanghai。开工第二十三轮153指纹全部匹配，保留全部无关工作区改动。

用户确认legacy与chat模式短问答均成功，已恢复chat；专用授权群出站引用和提醒对象均正确。实际本机核对settings文件与第二十三轮备份完全一致，22项运行控制保持chat/natural/reference=true；Saya ready、consumer running、NapCat reachable、QQ matched。剩余QQ-03真实项已齐，逐项门禁见qq-gate-review-2026-10-29-session24.md；QQ-03通过后才开始QQ-04。

新增QQ-04独立任务登记模块及隔离测试，未改原bridge/生成器/保存/发送/网页代码，未导入线上入口、未迁移生产库、未新增设置或部署。接口边界与后续精确接入顺序见qq04-integration-2026-10-29.md。尚无可用的线上高优先级/status或/cancel，也未实际开放并发，不把基础模块描述成整个QQ-04完成。

验证：先新增失败契约集，模块未存在时预期ModuleNotFoundError（red）保留；实现后19 passed。新增重复运行取消幂等及未执行任务不能成功终结两项后，最终21 passed（含取消/完成竞态100轮），无skip/xfail。命令：Python -m pytest tests/test_qq_tasks.py -q --basetemp=.runtime/qq-upgrade/session24-contract-final --junitxml=.runtime/qq-upgrade/session24-contract-final-junit.xml。只验证独立契约，未跑完整C04/旧路径累计；原QQ-03代码未改，既有148相关/642累计Python与76 JS明确为历史沿用。

覆盖范围：相同事件不改原配置快照、跨bot/人/群scope拒绝、队列容量/同scope顺序/有界claim、排队取消、迟到结果屏障、提交中取消立即登记/副作用对账、已保存不回滚、竞态单终态、重启不重排运行或未知effect、固定公开阶段节流、原inbox不改写。不是实际控制响应速度、真实模型取消或真实QQ发送的证明。

没有本轮服务重启、QQ代发、付费探测、监督/自启动变更或旧库覆盖。用户测试虚构记忆保留，不代删。在线观察台账保留所有缺口。

续接：先核对第二十四轮清单，沿用已完成QQ-03门禁，不再请求上述实机项目。按QQ-04接入顺序补独立控制防滥用、默认关闭开关/CAS兼容、请求范围提交屏障和生成器/发送集成，再做完整C04回归及真人验收。未接入前不要部署或建议用户使用尚不存在的/cancel；QQ-05～08未开始。
