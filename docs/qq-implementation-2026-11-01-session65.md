# QQ第65轮：双击统一启动入口

日期：2026-11-01，America/New_York。用户要求一键完整启动。第64轮354源码指纹一致；当时占用8000的手动进程本轮开工已自行退出，未强杀、接管或修改它。原start_saya.bat使用旧Saya环境直接uvicorn，只启动网页且不加载受管QQ链路，是此前网页可开但QQ暂停的一条原因。

新增项目根 `start_saya_qq.bat` → `scripts/start_saya_qq.ps1` → `scripts/start_saya_qq.py`，从现有安装元数据读取第62轮修正后的Python，复用原NapcatSupervisor、管理器和单实例/归属锁。先确认端口无陌生服务，再启动/复用专用QQ及NapCat、应用和Saya运行守护；等待认证健康达到Saya ready、consumer running、NapCat reachable、QQ matched后才输出READY。QQ需确认、错账号、暂停、绑定问题或等待超时均明确返回未完成，不把已创建进程当启动成功。

重复运行保留现有进程；外国端口占用先报错，不强杀/接管。未改原24项业务设置、媒体/表达策略、人物/声线/浏览器/凭据或QQ安装绑定，未更改旧bat/受管PS入口。未启用NapCat崩溃守护、注册Windows登录自启动、下载模型或预载语音；语音沿用现有按需加载。显式双击是手动启动意图，不等于开机自启。

验证：20项启动/监督/服务控制相关检查通过；修正实际wrong_account状态分支后最终8项新启动测试通过，数字重叠不相加，0失败/错误/跳过。测试覆盖迟到就绪/未知账号不得成功、暂停/错账号/需登录、陌生端口保留、超时不重启、原实例复用及NapCat启动拒绝。实际PS启动完整链路、再次PS复用、根bat经cmd执行均READY，后两次后端与Saya守护PID/创建时间相同，原专用QQ创建时间保持。实际批处理链通过不是用户已双击GUI的反馈。未代发QQ、调用模型/搜索/TTS。

```powershell
python -m pytest tests/test_qq_one_click_start.py tests/test_qq_napcat_supervision.py tests/test_qq_service_controls.py -q --tb=short --junitxml=.runtime/qq-upgrade/session65-start.xml --basetemp=.runtime/qq-upgrade/session65-start
python -m pytest tests/test_qq_one_click_start.py -q --junitxml=.runtime/qq-upgrade/session65-final.xml --basetemp=.runtime/qq-upgrade/session65-final
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start_saya_qq.ps1
cmd.exe /c "start_saya_qq.bat <nul"
```

当前ready/running/matched，原QQ进程及配置摘要保持；恢复Saya守护后观察自然记录第8个缺口（原7），原起点/未知时段保留，不补算停机或签24小时门禁。私有session65-before/first-start/verified及测试XML保留，不公开身份/凭据/日志。无需源码部署或重新登录，旧手动进程原已退出，没有进行服务强制切换。

用户以后双击根start_saya_qq.bat即可；看到READY可关闭启动提示窗口，后台继续运行。不要再同时运行旧start_saya.bat/start_saya1.bat竞争8000；已有陌生端口占用时由其原窗口正常退出后再运行新入口，不自动关闭。QQ登录失效仍需手机确认。第8阶段功能和其他真人待签沿用第63轮，不因启动检查通过代签语义自然程度/听感。后续保留此手动启动方式和原监督/自启动选择。
