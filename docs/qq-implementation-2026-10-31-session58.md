# QQ第58轮：迁移到当前电脑已安装的QQ

日期：2026-10-31，America/New_York。用户说明原TIM目标已换成电脑QQ，并明确选择“同时迁移机器人运行环境到当前QQ”。开工核对第57轮336个指纹一致。历史TIM限制记录保留；当前界面目标是QQ，不能继续将TIM作为操作目标。

## 当前结果

已从工作区内置QQ 9.9.33-52230迁移为`F:\Tencent\QQ.exe`、QQ 9.9.36-53644。使用已验证的NapCat 4.18.41官方Shell包加载器，加载器仍在项目私有runtime；机器人账号与原profile/config、OneBot端口/密钥、Saya数据沿用。未写入、替换或删除现有QQ安装文件，QQ.exe及wrapper.node迁移前后摘要一致；旧内置运行时保留作回退资料。

用户在新实例手动扫码并回复“已登录”，实际get_login_info核对原机器人身份、get_status在线；最终ready/running/matched，唯一受管理机器人主进程。原启动入口可识别并接管现有实例，不重复启动/重启。观察到新实例启动后产生的两条实际机器人回执，get_msg回读的消息ID和机器人发送者均匹配；代理没有代发QQ或发起新模型/TTS/搜索探针。该真实回读不同于模拟测试，也不等于所有功能的最终屏幕显示/听感验收。

## 实现

- `qq_napcat.py`新增明确的external_shell模式。原内置模式与路径限制保持；外部QQ必须显式绑定可执行文件SHA256和当前版本，加载器/配置仍限项目runtime，五个关键启动文件校验摘要。QQ自动更新或加载器变更明确拒绝旧绑定，不能默默套用历史构建。
- 启动器按官方方式接收QQ路径、Hook DLL和机器人账号参数，设置原NAPCAT_WORKDIR及独立实例标记。进程归属同时核对PID创建时间、可执行文件、工作目录、原profile和实例标记。同一QQ.exe下未带这两项标记的普通桌面进程不认领、不作为冲突、不纳入清理；单侧标记异常仍阻止操作。
- `qq_media.py`在此模式仅信任项目加载器/原profile及已限制的机器人原生缓存，绝不把整个F盘Tencent父目录作为附件读取根；旁边的微信、TIM及个人文件仍不能读。
- 新`qq_runtime_migration.py`验证固定官方Shell ZIP SHA256、解压数量/体积/路径/符号链接后暂存；不改活动配置或QQ安装。正式切换使用修订摘要CAS、生命周期锁、相同账号/profile与其余元数据保持检查；旧受管理运行时未停止或有并发修改则拒绝覆盖，不自动关进程或改监督选择。
- 新`test_qq_external_runtime.py`覆盖安装绑定、QQ升级/加载器变化拒绝、普通QQ进程隔离、官方启动参数、媒体根、ZIP验证与解压保护、配置CAS及账号/profile保持。仅改两个既有业务文件，新增迁移服务和测试；无JS业务变化。

官方启动方式及兼容信息参考[Shell说明](https://napneko.github.io/guide/boot/Shell)、[固定4.18.41启动脚本](https://raw.githubusercontent.com/NapNeko/NapCatQQ/v4.18.41/packages/napcat-shell-loader/launcher-user.bat)和[官方发布记录](https://github.com/NapNeko/NapCatQQ/releases/)。最终结论以本机53644实际初始化、登录及OneBot回读为准，不把版本表当作实机验收。

## 检查和部署

最初4文件45项聚焦通过；18文件179项扩展通过（16条既有FastAPI弃用警告）；补正式切换CAS后最终4文件47项聚焦通过，0失败/错误/跳过。数字重叠不相加，无JS变化不重复旧前端检查。

```powershell
python -m pytest tests/test_qq_external_runtime.py tests/test_qq_napcat_supervision.py tests/test_qq_napcat_login_health.py tests/test_qq_media_native_cache.py -q --tb=short --junitxml=.runtime/qq-upgrade/session58-final-focused.xml --basetemp=.runtime/qq-upgrade/session58-final-focused
```

新进程53644实际Packet/Napi初始化成功，首次等待60秒未登录记录PENDING，未当作失败重启；用户手动登录后独立核验通过。内置浏览器自动打开受Work设置禁用，未绕过或自动操作认证；窗口只读枚举不算QQ UI实机发送验收。

排空约0.03秒、三库一致性备份通过、CAS切换install.json、后端重启约4.80秒，已启动的新机器人进程全程保留。有效投递/回执/群/媒体/语音/转发表及两类收藏目录保持；24项设置、每批5、六媒体开启revision1、API提供商/凭据/浏览器/人物/声线保持。长期监督和正式自启动仍关闭，不因迁移擅自开启。原观察起点/6缺口/unknown不减少，不倒填在线时间。

未对开工已有普通QQ进程发出关闭或终止命令。开工记录的两个根进程，收尾一个仍存在、另一个已退出，原因未建立，不宣称两个PID均保持。受管理新实例与普通桌面QQ按实例标记区分；日常QQ的UI功能未在本轮代签。

私有session58-before/source-before/candidate/启动日志/三库与配置备份/部署/登录验证/回读及final-verified保留；安装说明私有副本已更新。原消息、账号、凭据、QR和运行日志不进入公开文档。已撤回的第56轮官方包扩展未复制、未恢复，原83项删除及第55轮功能状态保持。

## 下次入口

最新清单`qq-implementation-manifest-2026-10-31-session58.json`。不要再按旧“安装必须全部在工作区”判断当前配置无效，或把新installed-QQ实例误认成日常QQ后强关；仍须要求本轮增加的profile+实例标记等完整归属校验。不要运行第56轮已撤回的扩展安装。

后续启动继续使用原入口：

```powershell
.\.runtime\napcat\start-all.ps1
```

它将使用当前QQ路径和原profile；不要手工恢复旧install.json覆盖已确认迁移。QQ后续升级导致绑定拒绝时，先重新核对版本/原生兼容，再显式暂存和切换，不导入旧默认。需回退则先排空，仅停止本实例经校验的进程，保留现有数据，按私有pre-migration-backup恢复原安装指针；不能整库回滚或终止同路径全部QQ。

本轮是运行环境迁移，不推进已撤回的官方包整包读取。整体仍在第8阶段，原上下文表情发送/互斥命令及其他真人待签保留；ASR/普通文件输出继续取消。普通文字和图片可在现有机器人会话继续验收，无须重新配置账号/群/模型/媒体开关。
