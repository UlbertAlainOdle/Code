# Saya 本地语音：Qwen3-TTS 1.7B

聊天回复现使用各自保存的心情快照；喇叭再次点击停止，点击另一条回复停止上一段。自动朗读等完整回答及当轮心情确定后开始，PCM 音频仍流式播放。沿用已认可的 Saya 原音色向量。详见 [聊天体验刷新记录](chat-experience-refresh.md)。

在设置 → **Voice 语音**中使用本地声音设计、声音克隆和朗读，不需要 API 密钥。当前已部署 vLLM-Omni 流式加速，仍使用原来的 1.7B 权重和保存的克隆参考声音。

## 日常使用

- 声音设计：填写描述并生成试听，满意后保存为参考声音，再切换到克隆模式。
- 手动朗读：点击 AI 回复的朗读按钮，音频边生成边播放。
- 自动朗读：开启“自动朗读回复”；同时开启“输出第一句后开始自动朗读”，可在文字回复结束前读出完整句子。
- 当前保存的是流式加速、0.8 秒缓冲、闲置释放时间 0（常驻）。原自动朗读偏好仍为关闭。
- “停止播放”取消后续朗读；加速后端的流式请求会同时取消 GPU 请求。“释放显存”关闭整个语音服务，之后重新加载需要等待。
- 后端正常退出会释放语音服务。Windows 退出守护也监测后端和 WSL 启动进程的实际进程句柄，终端关闭或强制结束后清理本次启动的语音进程组。它不会关闭整个 WSL 或其他 Linux 程序。相关实测和本地聊天模型共存预算见 [退出与资源检查](tts-lifecycle-and-local-models.md)。

参考音频支持 3–30 秒、无背景音乐的单人 WAV / FLAC / MP3，最多 10 MB。准确的参考文字有助于克隆；没有文字时可以开启“仅提取音色”。Base 克隆跟随参考声音，不使用声音设计描述。

温度、Top P、Top K、重复惩罚控制采样，不是情绪控制参数。音量作用于播放器；WAV 播放速度由播放器保音高处理，流式 PCM 的速度由服务端保音高处理，避免直接重采样改变音色。固定种子方便比较，但加速计算和并发批次仍可能产生不同的采样输出。最大生成 token 过低会截断音频。朗读按完整句子分组，超长句按设置的字符上限拆分。

克隆模式增加默认关闭的“表达实验 · 跟随陪伴心情”。默认实验方式为**模型指令**：只在流式加速后端向 Omni 传入独立 `instructions`，正文、固定参考和采样参数不变；WAV 和流式 PCM 均接入，情绪本身不再靠整句变速/调音量实现。普通朗读冻结开始时的陪伴心情；试听可以临时选择心情。原生 Base 的 `instruct` 对照输出逐样本完全相同，因此原生后端遇到非平静模型指令会明确报错。Omni 的前缀能改变输出，但正式模型没有保证 Base 的指令控制能力。

真人反馈：旧“仅语速 / 音量演示”没有改变情感顿挫；ICL + 模型指令的难过和紧张仍偏轻快。后续加强指令 + 仅音色向量明显更好，用户确认“仍像 Saya，可以采用”。**本地已采用同一参考的音色向量与模型表达方案**：平静及各心情统一仅提取音色，难过和紧张使用这次试听的加强指令。原参考 WAV / 文字、采样及自动朗读偏好保留，需要重新加载后端并刷新页面使用新提示。效果仍可能因文本而异；同模型声音相似度下降记录保留，不能代替用户听感。带表达的试听不能保存为参考声音。旧变速方式仍可选作对照，需要 FFmpeg；用户流式播放速度也依赖 FFmpeg 保音高处理。实测、试听、失败案例和采用记录见 [原型记录](qwen-mood-prototype.md)。

## 加速环境

Windows 项目管理 WSL Ubuntu 内的独立 vLLM 服务，监听本机 8091 端口；Saya 的语音接口仍在 8000。后台使用 CUDA Graph、编译后的代码预测器和流式波形解码；前端使用一个连续的 PCM AudioWorklet 和有界缓冲。

运行库和权重缓存放在 WSL 的 Linux 磁盘，避免 Windows 挂载盘的小文件访问和内存映射瓶颈。缓存由已有项目权重复制并校验 SHA-256，不重新下载模型。模型设计与克隆模式按需切换，只有一套模式驻留。

RTX 4090 实测：510 字生成 92.16 秒音频，耗时 14.47 秒；浏览器持续播放约 93 秒，缓冲断流次数 0。预热后首包约 0.15 秒，真实浏览器约 0.7 秒出声。语音服务额外占用约 8015 MiB 显存；缓存后的冷加载含预热约 43.5 秒，首次编译更久。常驻可以避免每次重新加载。大型本地聊天模型同时运行时，需要为语音保留这部分显存。

设置、参考音色与试听仍保存在 `knowledge_base/tts`；加速日志是 `omni-worker.log`。更多测试见 [流式优化记录](tts-streaming-optimization.md)。

## 安装与恢复

已安装的环境由 `.runtime/qwen-omni-installed.json`记录 Linux Python 和权重缓存路径。不要删除其中指向的 WSL 目录。原生 Windows 环境 `.runtime/qwen-tts`保留，设置中选择“原生 Python”可使用完整分段生成。

在 PowerShell 中重新安装加速环境：

```powershell
wsl -d Ubuntu -u root -- apt-get install -y build-essential python3-dev
wsl -d Ubuntu -- bash /mnt/d/Dear/Talk/mistral_models/Code/scripts/setup_qwen_omni.sh
```

使用 Ubuntu 24.04、Python 3.12；按需要先更新 Ubuntu 软件包索引。部署脚本默认从 PyPI 安装。下载慢时，可在 WSL 中设置 `UV_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple`后运行同一脚本。不要以 root 身份运行部署脚本；上述 root 命令只用于安装系统编译依赖。

关键依赖固定在 `scripts/requirements-qwen-omni.txt`。启动器使用环境自带的 CUDA 13.0 编译器，避免系统旧 nvcc 干扰。部署脚本对 Omni 0.30.0 做一个重复惩罚参数兼容补丁；升级依赖时应重新验证。

重启 Saya 并刷新页面。启动时会后台加载和预热，不阻塞聊天界面。运行时仅使用本地模型；ElevenLabs 的可选云端配置保留。

官方来源：[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、[vLLM-Omni GPU 安装](https://docs.vllm.ai/projects/vllm-omni/en/latest/getting_started/installation/gpu/)、[Qwen 在线语音服务](https://docs.vllm.ai/projects/vllm-omni/en/latest/user_guide/examples/online_serving/text_to_speech/)。
