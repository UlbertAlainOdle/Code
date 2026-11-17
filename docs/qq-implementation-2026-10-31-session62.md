# QQ第62轮：按现有配置启动后端

日期：2026-10-31，America/New_York。用户要求启动相关后端。第61轮348指纹一致；开工应用8000及Saya守护未运行，按现有受管入口恢复NapCat及应用，未重做功能开发。

首次入口读取install.json中旧Saya虚拟环境，实际缺少Pillow，应用启动失败；这与第61轮已验证使用的Anaconda根Python不同。失败进程自行退出，维护意图阻止继续误启动，没有强杀或终止QQ。私有备份后仅修正install.json的python字段为此前已验证可用的解释器，其他安装绑定、QQ构建、加载器/profile/账号和实例标记保持。再次运行原start-all.ps1成功，保留已启动的QQ实例；没有安装依赖或修改业务源码。

实际认证健康接口确认Saya ready、consumer running、NapCat reachable、QQ账号matched、network reachable。API模型及搜索显示configured_unverified，本轮未额外调用模型/搜索、TTS或发送QQ消息。原24控制、六媒体revision1、收藏5项指定及说明、表达auto/revision1保持；没有改变人物/声音/浏览器/凭据文件、NapCat崩溃监督关闭或正式登录自启动选择。

检查命令：`python scripts/manage_qq_service.py status`、`& ./.runtime/napcat/start-all.ps1`、认证`/api/qq/health`、原配置/安装差异及SQLite完整性。无业务代码变化，不重跑无关测试。首次启动等待端口时连接拒绝及旧环境Pillow错误保留为私有诊断，不计成功。正式健康检查通过后才记启动成功。

观察原起点和未知时段保持；此次恢复记录第7个采样缺口（开工6个），不回填停机为在线，不据此签过24小时门禁。当前第8阶段工程/真实验收状态仍沿用第61轮，本轮仅恢复运行。私有session62-start-before/install-before/start-verified保留，不公开原身份/日志/凭据。今后仍可使用原启动入口；不要恢复旧python字段或重置配置。停止/睡眠后不会因为本次启动自动获得Windows登录自启动能力。
