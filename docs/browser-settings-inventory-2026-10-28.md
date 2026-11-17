# 浏览器设置清单与本机存档

核对日期：2026-10-28。范围为当前主页面和项目自有前端源码；配置登记表为 `static/browser_settings_inventory.json`。扫描所有自有HTML/JS后，浏览器存储调用集中在 `app.js`、`chat_experience.js`、`local_api_profile.js`、独立工具 `mmd_try_showcase.js`。没有发现自有代码使用sessionStorage或IndexedDB保存其他设置。HTTP资源缓存、第三方库内部行为不算应用配置。

## 当前行为（第六轮已启用，非仅存档）

入口：**Settings → Backup & Restore → 本地统一设置 · 含3D展示**。

用户已经确认正确Chrome的16项存档，文件 `knowledge_base/browser_settings.json` 的统一读取已开启。主页面获得占用后先读文件，再初始化消费者；登记设置使用显式适配层，缺失/重置项使用程序默认，不回流旧缓存。修改自动串行保存，冲突或失败保留草稿并显示同步状态；聊天和释放窗口前检查保存结果。关闭统一读取是明确的来源切换，原缓存仍保留。

“预览本浏览器全部设置/确认保存到本机存档”仍用于明确导入旧缓存，通常不需要重复执行，禁止用自动化默认值覆盖正确存档。API凭据继续使用 `api_credentials.json`；Search provider的互斥选择也在该文件。回复风格和个人资料以各自服务端文件为权威，归档副本仅保留，不反向覆盖。

存档保存原始字符串，不把旧JSON重新规范化；缺失项不删除已有存档。不同非空同名值默认拒绝覆盖，明确勾选允许覆盖后仍需通过修订检查。保留最近3份历史快照，原子写入失败不改变原文件。预览只展示类别、字段名称和新增/相同/冲突，不回传配置正文。文件含私人偏好和个人资料副本，已加入Git忽略，不进入源码清单。

## 22项登记内容

| 类别 | 存储键 | 内容及下一阶段注意事项 |
| --- | --- | --- |
| 3D展示 | `saya_mmd_3d_settings` | 当前模型、普通/高级灯光、背景、雾、天空盒、相机视角、领带物理模式 |
| 3D展示 | `saya_mmd_light_defaults` | 按模型保存的灯光默认值；`_view`还可包含相机位置/目标、模型缩放/Y位置及展示框宽高 |
| 3D展示 | `saya_3d_live_view_snapshot_v1` | **新增实时快照，不是旧缓存键**：当前相机位置/目标/zoom、模型与灯光、模型缩放/Y位置、展示框left/top/width/height、视口尺寸/像素比、侧栏收起状态 |
| 聊天偏好 | `saya_thinking_effort` | 浏览器思考档位；后续需与共享默认/QQ覆写的来源规则协调 |
| 聊天偏好 | `saya_recall_mode_v1` | 旧召回模式，保留兼容语义 |
| 聊天偏好 | `saya_search_mode_v1` | 旧搜索模式，不能借此绕过自主工具策略 |
| 聊天偏好 | `saya_agent_tools_enabled_v1` | 自主工具开关 |
| 聊天偏好 | `saya_mode_setting_v1` | 记忆、搜索策略和预设，包括safe search、来源策略等对象内字段 |
| 聊天偏好 | `saya_input_limits_v1` | 近期历史、输入/上下文限制 |
| 聊天偏好 | `saya_output_limits_v1` | 各模型输出预算 |
| 聊天偏好 | `saya_assistant_strong_logic_enabled_v3` | Assistant强逻辑开关 |
| 展示与个人偏好 | `saya_style_settings_v1` | 回复语言、可读性及风格设置；启动读取既有服务端风格文件投影 |
| 展示与个人偏好 | `saya_user_profile_v1` | 个人资料投影；以服务端文件为准，不再自动用旧缓存迁移覆盖 |
| 展示与个人偏好 | `saya_memory_graph_display_scales_v1` | 记忆图谱各维度显示缩放，非记忆内容 |
| 展示与个人偏好 | `saya_daily_brief_card_settings_v1` | 每日卡片开关、顺序、渐变颜色等完整对象 |
| 展示与个人偏好 | `saya_weather_city` | 天气城市 |
| 浏览定位快照 | `saya_rooms_v1` | 房间列表；后续恢复时校验有效会话/房间 |
| 浏览定位快照 | `saya_current_room_v1` | 当前房间，不自动向其他浏览器切换会话 |
| 浏览定位快照 | `saya_current_role_mode` | 当前角色模式，需与服务端会话角色一致 |
| 浏览定位快照 | `saya_current_project_id` | 当前项目定位，后续先校验项目是否仍存在 |
| 独立MMD工具 | `mmd_try_showcase_motion_marks_v1` | 动作试验标记；主页面只能读取同一源下的缓存 |
| 独立MMD工具 | `mmd_try_showcase_motion_annotations_v1` | 动作试验备注；不混入聊天/长期记忆 |

3D展示框位置/尺寸可能只存在当前内存，或只在点击“保存模型默认值”时进入缓存。只复制localStorage不足以保证保存当前视角，因此本轮额外抓取实时快照。后续文件接管应按目标屏幕重新约束展示框边界，不能无条件照搬旧像素坐标。

## 不重复复制的内容

- `saya_api_settings_v1`、`saya_other_api_settings_v1`：继续使用现有API配置入口，避免生成第二份密钥源。
- `saya_api_cache_last_backup_v1`及`saya_api_cache_backup_*`：用于现有缓存回退，不当作全局偏好。
- `saya_assistant_role_entry_memory_stamp`、`saya_assistant_role_memory_changed`：会话有效性标记，跨浏览器恢复会产生误判，明确排除。
- `saya_user_profile_server_migrated_v1`：迁移完成标志，不当作用户偏好。
- 本地语音设置已经保存到 `knowledge_base/tts/settings.json`；QQ设置/群权限、共享聊天默认、长期记忆和聊天记录均已有服务端存储。本次不再复制这些数据。
- 从未写入缓存的默认值仍来自当前源码/服务端；22项是登记范围，不代表每个浏览器都有22个已存项。预览会显示实际数量。

前端会对当前源的缓存键进行清单核对：未登记键只提示名称，不读取或上传其值。`tests/browser_settings_archive.test.cjs`还会核对源码中的静态存储键，新增设置未登记会使测试失败。

## 来源与恢复规则

第五轮只存档是历史状态，第六轮已实现并启用统一读取。实时3D快照优先于旧每模型默认视角，按目标屏幕约束框位置/尺寸；仅改变屏幕大小不自动回写存档。个人资料/风格读取各自既有服务端文件，临时聊天和会话有效性标记不跨浏览器恢复。房间沿用有效列表校验；项目列表读取失败不再当作删除项目，只有成功读取才能校验失效定位。Preferences备份包含本地设置文件，恢复后旧页面需重新加载，修订保护会阻止旧草稿覆盖。
