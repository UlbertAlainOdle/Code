# QQ 第八轮：网页历史边界修复与速度诊断

日期：2026-10-28。分支 `codex/qq-message-bridge`，未提交或重置其他工作区改动。前序门禁与下一步见[实施进度](qq-implementation-status.md)。

## 结果与原因

主人QQ共享长期记忆的会话原本已保存到独立 `session_qq_*.jsonl`，群/其他联系人的临时隔离路径不变。网页历史筛选漏排QQ命名空间，且直接指定会话的网页端点也未执行频道边界，因此不只是视觉命名问题。现统一限制网页历史入口，保留内部QQ读写和原有共享记忆权限；未迁移、删除或重写已有会话。

这不代表已经对所有功能完成全范围隐私审计，也不表示群聊获得主人记忆。当前回归覆盖原有群/主人权限、模拟真实生成器的共享/隔离、搜索、保存记忆及新增历史边界。历史上用户确认的真实QQ搜索成功证据保留，本轮没有重新要求QQ收发验收。

## 改动

| 文件 | 行为 |
| --- | --- |
| `core_services.py` | 公共网页筛选排除QQ，覆盖列表、搜索、月/日统计；内部文件读写保留 |
| `server5-22B----.py` | 网页会话读/写/归档/分支/续聊校验；多角色公开入口也受限，内部临时角色生成保持兼容 |
| `static/js/app.js` | 加载HTTP失败后保留当前聊天与ID，避免404被误当作空会话 |
| `static/Chat4o5-Modern.html` | app版本更新为g78-history-boundary |
| `tests/test_qq_chat_runtime.py` | 新增15个历史/端点/续聊兼容用例，原模拟QQ共享生成器改用实际QQ命名空间 |
| `tests/chat_experience.test.cjs` | 加载失败保留当前聊天、正常加载继续切换两项执行测试 |

## 速度诊断

搜索路径是Search → 候选筛选 → 串行Fetch正文 → 模型生成 → QQ发送。默认最多3份可读来源；遇到不可读候选还可能尝试替代来源，包含DNS、公开地址与robots检查。关闭思考只影响模型思考，不省略其余步骤；QQ当前在完整生成后发送，尚无QQ-04进度呈现。

本轮只读配置发现QQ仍 `auto`、未继承思考默认，网页保存值 `none`。可在主人私聊发送 `/set thinking none`，或在网页Settings的QQ接入设置里修改QQ思考档位；本轮未改用户当前值。没有阶段耗时测量，不能确定阅读、推理或网络各占多少；前轮独立Search/Fetch成功耗时也不能充当本次QQ整轮耗时。

## 验证与部署

最终407 Python、66 JS通过，12个既有FastAPI弃用警告。Python采用[状态文件的累计命令](qq-implementation-status.md)，另含SessionRuntimeTests；独立临时目录 `session8-release-final-tests`。JS执行原8个测试文件，报告 `session8-release-node-junit.xml`。

保留中间失败：首次未指定独立basetemp而遇到旧目录权限错误；新测试最初错误假设默认无当前会话、未隔离旧档案读取路径，已修正夹具；累计回归发现临时多角色生成的内部 `temporary_*` ID被过宽校验误拒，已修复且未删改原有断言。最终完整累计回归通过。上线核对最初把加载器附加revision与磁盘原字段作全字典比较失败，改为逐项比较原字段后通过，监督开关未变化。

所有权核对、排空后部署，约13.23秒恢复；QQ主进程与登录账号保持，配置、群授权、凭据文件和16项偏好完全保留。真实本机网页API列表未出现QQ条目，加载实际QQ文件返回404，该文件摘要前后相同。未新增付费请求或代发QQ消息。本轮网页界面仅执行JS隔离测试及实际HTTP检查，未声称实际浏览器点击或真实QQ消息已重测。

私有证据均在 `.runtime/qq-upgrade/`：`session8-focused*-junit.xml`、`session8-release-junit.xml`（中间1失败）、`session8-release-final-junit.xml`、`session8-release-node-junit.xml`、`session8-final-live-deploy.json`、`session8-live-history.json`。源码和配置开工快照在 `2026-10-28-session8-baseline/`；报告不含凭据、会话正文或运行日志。

## 后续入口

先读[第八轮清单](qq-implementation-manifest-2026-10-28-session8.json)并核对新差异，不重复迁移用户Chrome配置或重做本次历史修复。刷新旧网页以加载新脚本。若曾手工把QQ内容复制/分支成普通网页会话，该副本无法仅按文件名自动判定，本轮不删除或重写这种用户数据；本轮也未发现或推断这类复制实际发生。

QQ-01/02剩余手工组合与24小时观察、Windows重登、物理网络与NapCat本体故障恢复仍按原门禁推进；保持监督和自启动选择。观察起点、2个缺口及未知68587.56秒保留，未把缺口算在线。QQ-03～08均未开始；后续如要性能优化，先增加不含正文的阶段耗时测量，再依据证据调整读取预算或并行方式，不通过提前跳过门禁实现QQ-04。
