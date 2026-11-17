# QQ 第56轮：读取账号官方表情包及整包成员

日期：2026-11-01，Asia/Shanghai。用户要求继续尝试完整官方包读取。本轮核对第55轮332个源码指纹一致；保留已有未提交改动和两类表情的主人选择。仍在第8阶段；表情语境选择发送、语音/表情互斥命令未完成。

## 交付结果和真实证据

已部署账号官方包列表读取及整包展开，不再要求逐张向机器人发送样本。专用QQ原生只读方法实际返回5个账号漫游官方大表情包；按验证后的包编号读取QQ固定官方CDN资料，共83个成员。真实本机认证API刷新返回完整目录、0个包资料失败，最后检查目录revision5（运行中允许用户自行刷新，不硬编码修订号）。这证明当时账号漫游列表及其公开成员可读，不证明本机已经下载、当前购买权益或实际发送成功。

真实图片预览每包各抽一张，共5张成功返回126×126静态PNG。它们是QQ官方缩略图，不能冒充完整动图的首帧；页面和视觉分析提示已明确区分。原收藏图片继续使用原有首帧流程。未调用真实视觉模型/TTS，未代用户选择或分析，没有QQ消息发送、收藏/安装/购买/删除操作。原单张收藏在刷新前后完整相等；官方已有指定、说明、分析状态按同一包/emoji身份保留。浏览器视觉验收仍待，HTTP/模拟DOM不替代真人QQ发送验收。

## 实现与边界

- 新`scripts/napcat_official_packs.mjs`为固定NapCat构建提供一个范围固定的认证loopback读取动作，复用原OneBot端口/Token；没有任意方法透传或新增公开端口。原生调用单飞、8秒超时后仍等待原请求结束才允许下一次，不堆积原生任务。实验还读取过包内本地信息/路径，返回无缓存；生产整包展开依据官方CDN资料，不使用任意本地路径。
- `scripts/install_qq_official_pack_reader.py`校验固定原始SHA256和唯一挂钩位置，保留原始适配器私有副本；未知构建或已有无关改动拒绝覆盖。变化安装要求专用进程已停止；脚本自身不停止/启动QQ。已有安装一致时可在线只读检查。QQ/NapCat升级后须重新核对，不能直接移植旧挂钩。
- `qq_official_packs.py`核对原生来源、当前机器人账号、成功状态和完整分页标志；仅接受实际账号大表情包编号，不将商城推荐或消息样本算已添加。特殊small/magic格式、未完整分页或格式不明明确失败，保留原目录。每次最多100包、每包128成员、总1024项；包资料单次8秒/512KiB、总30秒。超限不偷偷截断冒充完整。
- 包资料只构造固定QQ官方HTTPS地址，严格JSON解析，不执行JS，不带账号Cookie/API密钥，不使用代理或跟随跳转。成员ID严格32hex；图片仅构造固定`i.gtimg.cn`官方126像素PNG路径，复用原8MiB/16MP限制、单解码及静态规范化。名称不作为HTML执行。视觉分析仅按主人指定静态预览、无私人记忆/搜索/agent/自动记忆写入。
- 新`qq_official_pack_inventory`与原官方目录在同一修订事务提交。原目录选择CAS保持；资料请求期间主人修改会拒绝迟到结果。资料失败或已不在账号列表时保留旧指定/说明，分别标明当前包成员、历史样本；不因读取失败删除用户内容。原单张收藏仍限48项，官方目录独立支持1024项。新非空库存表要求`qq_native_pack_inventory`读者能力，旧目录读者不能忽略。
- 页面“官方表情包”独立标签改为“从QQ读取已添加官方包”，按真实包名分组；默认不下载全部图片、不调用模型。保存草稿/来源切换保护保留，官方图按钮注明“预览官方缩略图”。聊天自动发送尚未接入，不展示成已开启。

修改：qq_official_packs.py、qq_favorites.py、qq_bridge.py读取白名单、qq.py路由/请求上限、qq_rollback.py、qq-favorites.html/JS、原官方样本测试和来源DOM测试；新增两个安装/原生读取脚本、三份相关测试。样本读取方法保留为单独的refresh_samples，不能替代完整账号目录证据。

## 验证、失败和恢复

23文件283项扩展检查通过（16条既有FastAPI弃用警告）。补真实预览兼容后，最后6文件50项聚焦通过（4条既有警告，0失败/错误/跳过；与283重叠，不相加）。3项Node检查及JS语法通过。覆盖账号来源、分页/特殊包拒绝、83项跨过旧48上限、指定/语义保留、并发CAS、资料失败、固定CDN、安装幂等/未知改动保护、回退门禁和原长回答路径。

```powershell
python -m pytest tests/test_qq_native_packs.py tests/test_qq_native_pack_install.py tests/test_qq_official_packs.py tests/test_qq_favorites.py tests/test_qq_forward.py tests/test_qq_rollback.py -q --tb=short --junitxml=.runtime/qq-upgrade/session56-final-pass.xml --basetemp=.runtime/qq-upgrade/session56-final-pass
node --test tests/napcat_official_packs.test.mjs tests/qq_favorites_sources.test.cjs
node --check static/js/qq_favorites.js
python -m scripts.install_qq_official_pack_reader --check
```

初探针误将NapCat的get/call查询门面当成Map写入，QQ停在初始化；已撤回原适配器、恢复matched，再按真实门面修正并补测试，失败不计通过。官方原raw300地址实际302，目标旧资源404，5项首轮预览均失败；未开放通用重定向，改为实际存在的官方126像素缩略图后5项通过，原失败证据保留。测试夹具setup命名触发pytest旧式钩子、回退函数误名及新增测试作用域错误也已记录修正，不改业务安全断言绕过。

原生探测期间专用QQ共4次重连（含失败撤回恢复）；日常QQ/TIM未操作。后端两次排空/三库一致性备份/部署分别约0.03秒/3.28秒和0.01秒/3.22秒，每次后端部署保留当时QQ进程。最终ready/running/matched，24项设置/每批5条、六媒体开启revision1、原API提供商/凭据/浏览器/人物/声线/监督及自启动选择均保持。观察原起点和6个既有缺口保留，unknown不减少，不重置或倒填在线时间。生产记忆未修改。

原生方法的参数类型参考上游[NodeIKernelMsgService定义](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-core/services/NodeIKernelMsgService.ts)，最终以本机固定二进制及真实返回核对；该类型文件没有承诺所有QQ版本可用。私有session56源指纹、探针/失败恢复、CDN资料、预览、两次备份/部署、XML和最终验证均保留，不公开账号、原始消息、key、素材ID/URL或缓存路径。

## 下次继续

最新清单为`qq-implementation-manifest-2026-11-01-session56.json`，不要覆盖第55轮或更早清单。保留已部署的原生扩展；先运行上方--check，只检查不重启，原安装副本位于私有native-pack-reader目录。无需再次逐张发官方样本或重复真实列表/CDN探测。

用户刷新表情页面，切“官方表情包”→“读取本机清单”即可看到5包83张；按需预览、勾选保存、分析或手动填写含义。原“收藏表情”继续独立使用。当前普通官方包目录/缩略图已实际验证；真实模型语义和表情实际发送待验，不能用预览代签。

下一项接批准语义候选的上下文选择和真实发送，并统一语音/大表情/QQ小表情/颜文字互斥策略及命令。不能自动全选、替主人分析全部素材或恢复已取消的ASR/普通文件输出。原历史群文件、引用/Mood、顺序、声音听感等真人待签保持独立。
