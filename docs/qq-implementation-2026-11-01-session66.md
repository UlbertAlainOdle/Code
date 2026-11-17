# QQ第66轮：可见管理面板、完整停止与残留核验

日期：2026-11-01，America/New_York。用户要求完整版本，能判断是否存在残余后台。第65轮360指纹一致；本轮将原启动器补成可见管理面板，原单纯启动窗口“关闭后服务继续”只为历史行为，现正常关闭面板会执行受控停止。

## 交付

根 `start_saya_qq.bat` 打开中文Tk面板，自动启动/复用；提供启动、停止全部、重启、刷新和停止并退出。显示类型/PID/总数、3000/6099/8000/8091端口及核验缺口，每6秒刷新。启停后台线程执行，界面持续反馈；同一工作区面板锁/启停锁防重复操作。重启先确认停止成功，失败不另启。X/Alt+F4排空、停止、核验后才销毁窗口；失败窗口保留解释，忙碌时记住退出请求而不直接丢弃控制。

停止先持久化人工停止意图，阻止守护拉起；排空QQ队列/生成/发送/后台语音渲染及网页写入，忙碌超时不强杀。新增认证 `POST /api/qq/lifecycle/stop-workers`，仅明确确认且draining/空闲时可卸载语音和停止原Minecraft辅助；清理期间计入active_web_requests，避免并发停机将其视为闲置。关闭应用后等待守护/后端退出，再按原QQ exe/profile/独立实例标记/创建时间规则关闭专用QQ及其子进程。

Windows辅助按固定项目脚本/可执行文件族/工作目录/完整命令/创建时间核验，不按进程名或任意子树终止。未受管项目后端/同端口程序显示异常且保留。WSL只核对当前工作区语音随机token及PID出生时间，卸载后复查必要时清理同token的存活工作进程，不关闭整个WSL或其他项目。短token登记留在私有runtime，避免原pid文件删除后漏查孤儿；旧pid文件指向已复用的WSL Relay不误报或清理为模型。

“已全部停止”要求项目进程空、相关端口空、核验缺口空；WSL/归属检查失败不标成功。系统WSL宿主与日常QQ不在停机范围，管理面板自身为可见前台，不计作后台残留。

修改：根bat、原启动PS/Python、QQ路由；新增scripts下saya_qq_control.py、saya_qq_panel.py、inspect_saya_voice.py及test_qq_full_control.py。使用说明见[后台管理](qq-backend-control.md)。未改人物/声线/语义策略/收藏/原24设置、媒体开关、凭据或浏览器档案。

## 验证

- 最终5文件35项检查通过，0失败/错误/跳过。包含原启动/监督/服务控制、网页排空，新增陌生进程保留/PID复用拒绝、任务/语音忙碌、重启停止失败不启动、面板未核验不销毁、认证/明确确认/闲置排空后才能卸载辅助服务。早期31/33项结果与最终重叠，不累计。
- 真实完整停机通过：应用、守护、专用QQ及辅助、WSL/GPU进程均空，3000/6099/8000/8091均无监听，issues=[]、stopped=true。后续从根bat启动实际面板，视觉检查布局/状态/PID；使用系统Alt+F4关闭，确实进入停止流程，窗口自行消失后第二次核验同样全部为空。不是仅mock或关闭一两个端口。
- 随后从同一根bat重开最终面板恢复运行，实际最终状态以私有session66-final-verified与状态文档为准。QQ曾短暂login_required后自然恢复匹配，没有代操作扫码或要求用户配合复杂实验。未代发QQ、调用搜索或付费模型；原语音预加载随原配置启动并可能执行既有本地短句热身，停机中明确卸载；没有独立音频测试或QQ语音代发。
- Computer-use技能实际窗口检查确认中文内容和按钮可见；原UIA标题栏关闭点击未触发，随后改用真实系统Alt+F4验证正常关窗。未用截图推定底层进程消失，另用进程/端口/WSL核验作证。

```powershell
python -m pytest tests/test_qq_full_control.py tests/test_qq_one_click_start.py tests/test_qq_napcat_supervision.py tests/test_qq_service_controls.py tests/test_qq_service_drain.py -q --tb=short --junitxml=.runtime/qq-upgrade/session66-final-tests-r3.xml --basetemp=.runtime/qq-upgrade/session66-final-tests-r3
python scripts/start_saya_qq.py --action stop
python scripts/start_saya_qq.py --action status
cmd.exe /c start_saya_qq.bat
```

首次实停被原排空屏障以503挡住普通卸载接口，流程如实未完成，未盲杀；新增认证排空专用入口后重测成功。首次部署因已有私有备份目录而在预检阶段报错，无停机，换新部署备份目录重试；保留原失败/成功日志和清单，不覆盖。原源码/配置摘要及两次三库一致性备份保留。成功源码部署排空0.01秒/后端重启5.38秒，当时QQ实例保持；后续专用QQ退出重启是用户要求的完整停机实验。

## 保持和续接

观察原起点与所有缺口/未知保留，完整停机及重新采样造成的缺口如实新增，不补在线信用，最终数量见最新状态。监督/正式自启动选择保持；没有开启NapCat崩溃守护、修改QQ登录数据或恢复被撤回的官方整包。ASR/普通文件输出继续取消。整体第8阶段业务及原真人待签范围不因进程演练扩大签收。

私有session66-before/source-before/final-source/static-final-source、测试XML、失败/成功停机日志、stopped-status/after-window-close、deployment-backup/deploy-r2/final-verified保存证据；不公开原账号/对话/凭据、模型token或原日志。用户以后双击同一bat，正常关窗等待面板自行退出；出现未完成按面板提示查看，不用手工全杀Python/QQ或关整个WSL。旧第65轮“关闭提示窗口仍后台运行”已由本轮新面板行为覆盖。
