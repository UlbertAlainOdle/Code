# 第五轮：唯一网页与完整浏览器设置存档

日期：2026-10-28，Asia/Shanghai。分支 `codex/qq-message-bridge`。[继续入口](qq-implementation-status.md)。

## 用户新增优先项

本轮先处理“同一后端只能有一个主聊天页面”和“找齐API之外的缓存配置并能存本机”，再回到原QQ前两阶段验收。用户明确把全量本机文件运行控制留到下一次；本轮没有启用该控制，也未开始QQ-03～08。

开工81个源码指纹全部吻合，已另存源码及观察/policy快照。旧清单和旧测试证据不覆盖，用户已导入API凭据保留。

## 唯一主聊天页面

- 新增服务端WebSocket占用管理，同一后端的Chrome、Edge、应用内浏览器及同源不同标签共用一个槽位；不依赖localStorage锁。
- 后打开的页面显示占用说明，不启动聊天、语音、轮询或3D应用脚本。21个依赖/应用脚本只有获准后才按顺序加载，适配原DOMContentLoaded/load初始化顺序。
- 原页面关闭完成后可点击“重新尝试”。若上一条网页聊天仍在生成，等待其结束，不为切换窗口强杀任务；普通后台小组件请求不会长期阻挡切换。原服务维护排空仍统计全部写请求，未削弱。
- **Settings → Backup & Restore → 释放此页面占用，切换浏览器** 可显式释放；服务端回执后旧页面暂停，内容/草稿保留，其他浏览器可进入。
- 断线后页面暂停，同源新请求被阻止；后端恢复后可重连，不重复初始化旧UI。加载中失败要求刷新，避免脚本部分执行后重复注册。
- `/chat_stream`的HTTP入口校验占用证明，旧页面未刷新也不能绕过继续发送；QQ仍在服务端直接调用原聊天生成器，不占用网页槽位。
- 已打开的旧版页面需要刷新才能显示新占用界面。此功能限制的是一个**可工作的主界面**，浏览器仍然可以创建显示占用提示的标签页，无法强制关闭用户其他浏览器的窗口。

实际验证：两个生产浏览器页先后进入，第二页只显示占用提示；关闭第一页面完成后第二页面可进入。关闭后的即时重试曾赶上占用/请求收尾，稍后重新尝试成功，未强制接管。主动释放另有生产页面实测。WebSocket独立客户端、并发竞争、跨源拒绝、HTTP拒绝和断线请求保护均有自动测试。

## 配置清单及本机存档

完整清单见 [浏览器设置清单](browser-settings-inventory-2026-10-28.md)，登记表 `static/browser_settings_inventory.json`，共22项，其中1项是新增实时3D快照。涵盖3D普通/高级灯光、背景/天空盒/雾、相机、模型、领带物理、每模型默认值、展示框位置/尺寸，以及聊天策略/预算、角色/房间定位、个人资料副本、每日卡片、图谱显示和独立MMD工具标记。

入口 **Settings → Backup & Restore → 浏览器设置完整存档 · 含3D展示**：预览 → 明确保存。目标 `knowledge_base/browser_settings.json`；默认拒绝不同同名值覆盖，确认允许后仍做修订检查；缺失项不删除，原始字符串保留，原子写入，保留3份历史。浏览器缓存不回写，API凭据仍走原入口，不产生第二个密钥源。新文件已加入Git忽略。

本轮仅建立迁移存档。没有替换Storage API，也没有自动把存档注入所有浏览器；便携偏好、屏幕布局、会话定位和既有服务端设置的合并规则留到下一阶段。

实际验证：生产页只读预览成功，含3D缓存和实时展示框快照，未把自动化浏览器的默认值保存到生产。隔离IPv6测试中实际预览/保存成功，文件中的3D参数及实时框尺寸已核对；隔离进程/页面已清理。用户随后确认已在常用Chrome完成保存；生产文件有16项，含3D参数、每模型默认值、实时相机/模型/展示框快照。22项是登记范围，缺少未使用的可选项不补默认。私有证据`session5-user-archive-confirmed.json`只记录数量与存在性，不公开值。

用户报告其他浏览器3D显示不同：本轮存档不自动应用，浏览器切换后的差异仍可能来自各自缓存，不能据此判存档失败。若另一个新版主界面仍占用，第二页应该只见占用提示；旧版已开页面须刷新。下一阶段应以本次正确Chrome存档为迁移输入，禁止重新导入测试浏览器默认值。

## 回归与部署

- Python **280 passed**，8个既有FastAPI弃用警告，证据`.runtime/qq-upgrade/session5-final-junit.xml`。新增窗口/存档/HTTP拒绝共20项，原260项保留。
- 核心JS **25 passed**（窗口、存档、API配置、聊天呈现/智能体），另外语音/3D物理专项 **24 passed**，本轮合计 **49项**。没有把静态检查算作真实QQ验证。补充失败重新预览时作废旧快照及保存按钮，避免保存未经当前预览的配置。
- 中途WebSocket测试因TestClient默认ws://testserver与指定Origin不同返回4403，改为明确的loopback URL；HTML脚本静态测试曾把data属性误识别成src，修正匹配后通过，未放宽实际页面唯一性约束。
- 部署先排空，仅重启后端，最终约13.22秒恢复。QQ进程、群授权、17项原设置、用户API凭据、NapCat policy及原观察起点/缺口保持。新增状态不修改正式自启动选择。
- 新增`.gitignore`私有存档规则；私有配置、缓存值、会话和原始日志不写公开报告/清单。

相关命令：

```powershell
node --test tests/web_window.test.cjs tests/browser_settings_archive.test.cjs tests/local_api_profile.test.cjs tests/chat_experience.test.cjs tests/chat_agents.test.cjs
node --test tests/test_voice_playback.cjs tests/mmd_tie_physics.test.cjs
# Python累计回归沿用状态文档命令，并加入：
# tests/test_web_window.py tests/test_browser_settings_archive.py
```

私有证据：`session5-final-live-deploy.json`、`session5-single-window-blocked-final.png`、`session5-settings-preview-final.png`、`session5-archive-lab-saved.png`、`session5-archive-browser.json`、`session5-handoff-live.json`。源码清单 `qq-implementation-manifest-2026-10-28-session5.json`；前四轮清单保留。

## 后续与兼容要求

1. 自动化测试页面必须释放/关闭，不能阻挡用户在正确Chrome中预览保存；禁止用空白自动化缓存替换用户档案。
2. 用户确认生产存档后，下一阶段才实施本地文件统一控制。UI读取/覆盖、缺失与清空、模型API安全来源、会话定位和屏幕约束分别验收，避免旧缓存反向覆盖。
3. 原QQ严格串行继续有效：Brave仍为此前HTTP402 / USAGE_LIMIT_EXCEEDED，未重复消耗请求；真实成功搜索、24小时观察及部分实机门禁仍待补。不重做已通过的历史回退/命令冲突。
4. 新的单页面要求优先于原“两网页同时编辑”手工场景；旧CAS证据保留，今后用“唯一主页面＋QQ命令/过期API客户端”验证覆盖保护，并另验重复页面被阻止。
