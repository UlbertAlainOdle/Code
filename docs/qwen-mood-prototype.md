# 固定音色的情绪韵律最小原型

2026-10-10。本次依据 [情绪连续体审计](mood-continuity-audit.md) 和 [本地 Qwen 语音说明](local-qwen-tts.md)，检查当前代码、已安装 SDK、vLLM-Omni 及真实 GPU 输出。只使用已有本地权重，没有下载新模型、调用云端语音或重设计参考音色。各轮对照不改保存偏好；用户认可后采用音色向量与模型指令方案，明确记录设置变更，参考 WAV / JSON 与陪伴状态保留。

**当前采用方案：同一参考的音色向量 + Omni 模型指令，难过和紧张使用加强指令。** 用户试听认为“仅原音色向量明显比原参考 ICL 更好”，并确认“仍像 Saya，可以采用”。这是本次用户认可的最小原型，不代表正式 Base 已支持可靠指令控制，也不保证所有文本的情绪、自然度和身份一致性。

旧语速/音量原型未达到情感顿挫要求，ICL + 指令的难过和紧张也仍轻快，失败对照保留。现在统一采用仅向量方式，平静和各情绪之间不切换克隆条件。模型指令通过 WAV / 流式 PCM 链路；通用默认仍关闭，本地按用户确认开启表达实验。各轮数据及局限见下文。

## 实际控制能力

| 路径 | 控制方式 | 本次验证与边界 |
| --- | --- | --- |
| 1.7B Base，原生 `generate_voice_clone` | 参考音频、逐字参考文字；或仅说话人向量；正文语义、标点；采样参数 | 没有正式 `instruct` 参数。`**kwargs` 能接收它，但本机 0.1.1 忽略它，三次输出完全相同。正文/标点可能影响韵律，不是稳定的独立情绪控制，不能靠增加括号指令而保证不被朗读。 |
| 1.7B Base，Omni 0.30.0 | 相同参考条件；适配器额外接受 `instructions`，构建器拼接指令前缀 | 本机实验确实改变了输出，不能说“Omni 也忽略”。这是 Base 的非官方控制路径，已接入默认关闭的表达实验；多文本、多种子和真人反馈显示，不能保证稳定的情绪遵从。 |
| 1.7B VoiceDesign | 自然语言 `instruct` / `instructions` 描述声音和表达 | 正式支持；当前 worker 与 Omni 都已正确传入 `description`。每次重新描述生成声音不能保证原克隆身份，故没有用来逐句改变 Saya 的情绪。 |
| 1.7B CustomVoice | 固定预置 speaker + 情绪/韵律指令 | 正式支持，但预置 speaker 不是当前保存的 Saya。仓库没有安装这套权重，本次未生成样本。 |

温度、Top P、Top K、重复惩罚和种子控制随机采样，不是 happy/sad 旋钮。仅说话人向量模式减少参考语气条件，同时可能降低克隆质量，也不是情绪控制。正式能力依据 [官方模型能力表和使用说明](https://github.com/QwenLM/Qwen3-TTS)、[正式克隆接口源码](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py)。本机 Omni 依据 `tts_adapters/qwen3_tts.py` 和 `prompt_embeds_builder.py` 的实际分支；[版本源码](https://github.com/vllm-project/vllm-omni/blob/v0.30.0/vllm_omni/model_executor/models/qwen3_tts/prompt_embeds_builder.py)。

## 模型真实实验

统一文字：**博士，我们先把今天的事情安排好，然后一起休息一会儿。** 参考音色 ID `1fd8568ff260441587cbe97a01c0ca83`，参考 SHA-256 `5297a210f300cc7d70559499b6c360dec066241886d82f26a79c4a16e6ebb962`，准确参考文字、ICL 克隆、Chinese、seed 42、temperature 0.9、top_p 1、top_k 50、repetition_penalty 1.05。

原生：qwen-tts 0.1.1、Torch 2.6.0+cu124、BF16、SDPA，生成上限 512。缓存相同 clone prompt，在每次生成前重置种子。

| 额外参数 | 时长 | float 波形 SHA-256 | 对基线最大绝对差 |
| --- | ---: | --- | ---: |
| 无指令 | 4.88 秒 | `089eeaaa…95eb4ed` | 0 |
| `instruct="用非常开心、兴奋的语气说。"` | 4.88 秒 | 同上 | 0 |
| `instruct="用非常悲伤、低落的语气说。"` | 4.88 秒 | 同上 | 0 |

三条波形均为 117120 个 float 样本，逐样本相等。完整结果：[native/probe.json](experiments/qwen-mood/native/probe.json)。这是“参数接受但没有控制效果”的实证，不是只检查函数签名。

Omni：同一 Base 权重、相同固定参考，保持保存的 2048 token 上限。直接调用 8091 `/v1/audio/speech`，绕过原型 DSP。

| 请求 | HTTP | 时长 | 中位 F0 | 与基线说话人向量余弦 |
| --- | ---: | ---: | ---: | ---: |
| 基线 | 200 | 5.36 秒 | 314.60 Hz | 1.0000 |
| 重复基线 | 200 | 5.36 秒 | 314.60 Hz | 1.0000 |
| 开心 instructions | 200 | 4.88 秒 | 321.02 Hz | 0.9966 |
| 悲伤 instructions | 200 | 5.60 秒 | 291.00 Hz | 0.9955 |

最初单句轮次的两次基线字节哈希相同，指令输出不同。速度/音高方向与提示有一定一致性，**它只证明输出变化，未证明情绪控制**。当时没有真人听辨、多文本或多种子验证；后续实验和真人反馈见下节。加速/并发本身也可能改变采样，不能因 SDK 测试无效而将 Omni 判为绝对不可行。最初结果：[audit.json](experiments/qwen-mood/audit.json)、[measurements.json](experiments/qwen-mood/measurements.json)。

## 模型指令与负面情绪实验（真人反馈后）

第一轮反馈：“感觉只有语速变了，情感上顿挫等一点没变”。旧 DSP 无法改变原来的重音、停顿分布和语调轮廓，因此按失败对照保留，没有继续把整句变速当成情绪表演。

第二轮：3 条中文正文 × seed 42/77 × 平静/开心/难过/紧张，共 **24 条模型原始输出及 24 条等速等响输出**。保持同一参考 WAV、准确参考文字、ICL、采样参数，只改独立 `instructions`；没有向正文加入会被朗读的括号指令。等速等响使用平静时长及同组最低 RMS 的 85% 做平均速度/电平对齐，保音高；它不是感知响度标准化，局部拉伸也可能有伪影，所以同时保留原始音频。

用户听后反馈：“有一些变化，但是难过和紧张的两个没那么明显，感觉语气还是很轻快，尤其是紧张”。**模型指令提供了一些句内变化，但负面情绪未达到要求；这不是盲听统计或可靠遵从率。** 对固定参考的自然度、文字完整性和身份也没有独立 ASR / 多人盲听验收。

[模型等速等响试听](experiments/qwen-mood/direction.html)、[输入与输出清单](experiments/qwen-mood/direction/direction.json)、[声学测量](experiments/qwen-mood/direction/measurements.json)。原始音频对同文本同种子平静的说话人余弦范围：开心 0.99573–0.99744，难过 0.99449–0.99786，紧张 0.99584–0.99786。无削波。MFCC DTW 粗略对齐后去掉整体音高/电平差，仍有局部 F0、能量和停顿变化；这不等于情绪识别或逐字重音定位。

第三轮：加强难过/紧张指令，并对比 ICL 与**同一份参考的仅说话人向量**；2 条正文 × 2 种子 × 6 条件，共 **24 条原始及 24 条等速等响输出**。仅音色向量平静作为额外控制，避免把切换克隆条件的效果误当成情绪指令效果。强指令的全文及实际请求条件存入清单，没有写入用户保存设置或当前映射。

| 加强指令条件 | 对原 ICL 平静的说话人余弦范围 | 中位 F0 范围 | 当前可支持的判断 |
| --- | ---: | ---: | --- |
| ICL 难过 | 0.99524–0.99701 | 273.9–309.2 Hz | 局部变化，身份代理较接近；不能据此保证悲伤听感 |
| 仅向量难过 | 0.97403–0.98254 | 222.5–261.5 Hz | 音调降低更明显，但身份代理下降；尚不能用于固定音色自动映射 |
| ICL 紧张 | 0.99657–0.99745 | 280.3–361.4 Hz | 音调方向不一致，稳定紧张表演未证实 |
| 仅向量紧张 | 0.98718–0.99235 | 257.8–303.9 Hz | 仍需真人判断，不可直接当作解决方案 |

平静仅从 seed 42 换成 77，去整体偏移后的局部音高差已有 170 / 110 cents（两条正文）；与许多带指令差值处于相近量级。因此“曲线变化”本身不足以证明情绪遵从。同一个模型的 speaker encoder 也不是独立身份鉴定，没有设一个余弦阈值就宣布音色完全不变。

当前参考录音本身轻快。ICL 的参考表达可能影响负面情绪，是原因假设；向量对照改变的不仅是表达条件，也改变克隆信息，不能证明因果已完全分离。[加强负面 / 仅向量试听](experiments/qwen-mood/direction.html?test=negative)、[实验清单](experiments/qwen-mood/direction/negative/direction.json)、[指标及种子控制](experiments/qwen-mood/direction/negative/measurements.json)。

后续真人反馈：“仅原音色向量明显比原参考 ICL 更好”；追问固定身份时确认“仍像 Saya，可以采用”。因此不再用同模型余弦下降否定用户听感，但保留指标作为音色风险记录。这是一位用户对当前样本的比较评价，没有多人盲听、自动逐字 ASR 或长期跨文本统计。

## 已采用的向量方案 v3

使用原 voice_id 和原 WAV，仅将 `x_vector_only=true` 固定用于所有心情。`mood_prosody=true`、`mood_control="model"`；难过/紧张提示逐字沿用第三轮获得认可的提示，快乐沿用笑意/重音提示，平静不加指令。`sad/depressed`、`happy/excited/hyper` 分别共用表达提示。没有整句情绪变速或调电平，也没有按心情动态切换克隆条件。Assistant 保持 neutral。

真实完整链路验证：平静、开心、难过、紧张的 `/preview`、`/synthesize`、`/stream` 输出各自哈希一致；平静、难过和紧张也逐字节匹配用户已听的第三轮向量原始样本。时长为 6.72、6.24、8.64、7.44 秒，预热后首包约 0.099–0.119 秒，没有情绪 DSP。完整记录见 [vector-routes/audit.json](experiments/qwen-mood/direction/vector-routes/audit.json)。开心的向量输出是本轮新增链路样本，尚无单独真人评价。

本地配置已保存，采用记录见 [adoption.json](experiments/qwen-mood/direction/adoption.json)。原参考 WAV、参考文字、种子和采样参数保留，自动朗读仍关闭，陪伴状态未变。设置变更需要重新加载 Saya 后端并刷新设置页，才能使用 v3 提示；验证时 8000 主服务未运行，使用临时 TTS-only 服务，验证完成后已释放。

真实 Saya 接口验证：开心、难过、紧张各经过 `/preview`、`/synthesize`、`/stream`。每个心情的三种输出波形哈希相同；表达计划 tempo=1、gain=1，实际传入模型指令，没有情绪 DSP。时长分别 6.88、7.92、6.64 秒；预热后流式首包约 0.129–0.157 秒。仅证明条件确实抵达模型且接口一致，不能证明情绪成功。记录见 [routes/audit.json](experiments/qwen-mood/direction/routes/audit.json)。

## 当前接入

新增 `VoiceSettings.mood_prosody=false`、`mood_control="model"`。Voice → 克隆模式中的“表达实验 · 跟随陪伴心情”勾选并保存后才应用。模型方式只支持 Omni，非平静原生请求返回明确错误，不暗中降级成变速。试听下拉框可临时选择心情，不修改陪伴状态。

当前 v3 映射：neutral 无指令；happy/excited/hyper 使用笑意、重音和语调起伏提示；sad/depressed 使用第三轮沉重、犹豫停顿、句末无力下沉提示；stressed 使用第三轮声线收紧、压声、重音后犹豫提示。完整提示在 `saya_app/services/speech_prosody.py::MODEL_INSTRUCTIONS`。情绪本身的 tempo/gain 均为 1，不按心情修改参考、正文、克隆方式或采样。**这些名称代表请求目标，不能保证每句的情绪强度。** 第二轮清单保留当时 v2 的较弱提示，与当前 v3 不同。

`mood_control="timing"` 保留旧“仅语速 / 音量演示”，仅供比较：

| 连续状态 dominant | 语速倍率 | 音量倍率 | 仅提供的线索 |
| --- | ---: | ---: | --- |
| neutral / 未知 | 1.00 | 1.00 | 原始克隆 |
| happy | 1.08 | 1.00 | 轻快 |
| excited / hyper | 1.14 | 1.00 | 更轻快 |
| sad / depressed | 0.88 | 0.78 | 舒缓、较轻 |
| stressed | 1.04 | 0.88 | 略紧凑、收敛，情绪可辨性最弱 |

原型使用现有 dominant 标签；快乐、活力、压力通过连续引擎的既有规则决定 dominant，没有另造第二套心情数值。锁定保留当前心情；自动取状态复用 `mood_continuity.snapshot` 的锁和闲置回落；关闭连续功能时自动路线使用 neutral。显式试听/API mood 是一次性的实验覆盖，即使连续关闭也可以试听。关闭语音韵律、声音设计、ElevenLabs 都不使用此情绪映射。

Assistant 角色的流式聊天、自动朗读和手动朗读明确使用 neutral，不读取 Saya 陪伴心情，保持原来的角色隔离。

`GET /api/tts/prosody` 返回当前映射。`POST /preview`、`/synthesize`、`/stream` 增加严格枚举 `mood`（默认 auto）。前端在一次手动/自动朗读开始时取一次状态，把它传到所有分句，避免一句话中途忽快忽慢。流式聊天取的是**开始时**的心情；这一回复后来才出现的 MOTION / MOOD 标签从下一次朗读体现。这是当前文字情绪信号时序的限制。

参考音频、参考文字、x_vector_only、正文和生成采样参数不因心情而改变。模型方式在生成前加入独立指令。旧 timing 方式生成后用 FFmpeg `atempo` 与 `volume`，不使用改变音高的重采样。WAV 两种后端在父服务处理；需要变速的 PCM 对一次上游请求使用一个持续滤镜，跨网络包保持状态。FFmpeg 管道提供背压，停止/断连时关闭滤镜、生产任务、上游 HTTP 与 busy 计数；没有对每个小包独立做拉伸。

同时修正原 PCM 播放速度直接重采样的音高漂移：用户播放速度在服务端一起保音高处理，响应 `X-Audio-Playback-Rate: 1`，前端避免再加一次速度；WAV 播放器明确 `preservesPitch=true`。普通默认速度 1、neutral 原始波形直接通过。实际 DSP 需要 PATH 中的 FFmpeg；缺失时在 GPU 生成前报错。可以关闭原型并将流式播放速度恢复 1。

带模型指令或 DSP 的试听在 UI 和服务端均禁止保存为参考声音，避免实验表达进入下一轮 ICL 参考。声音设计试听和平静对照仍可保存新参考。保存的原音色和偏好未变更。

## 旧 timing 对照与音色代理指标（未达到情感顿挫要求）

A/B 复用**同一份生成波形**再加工，排除重复生成的采样差异。

| 样本 | 时长 | F0 相对平静 | 说话人余弦对平静 | 说话人余弦对固定参考 | 削波样本 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 平静 | 5.360 秒 | 1.0000 | 1.0000 | 0.9913 | 0 |
| 开心 | 4.977 秒 | 0.9885 | 0.9989 | 0.9903 | 0 |
| 兴奋 | 4.713 秒 | 0.9942 | 0.9986 | 0.9899 | 0 |
| 低落 | 6.075 秒 | 1.0000 | 0.9984 | 0.9897 | 0 |
| 紧张 | 5.167 秒 | 1.0000 | 0.9990 | 0.9901 | 0 |

一次后处理约 44–51 ms。中位 F0 的最大变化约 1.15%，说话人向量余弦最低 0.9984（对未加工波形），支持这条短句的音高/身份基本保持。F0 使用 librosa.pyin，身份使用 Qwen Base 自带 speaker encoder；它们不是独立情绪识别，也不能证明所有语句都自然或绝无音色变化。`atempo` 可能带来局部拉伸伪影，故限制变化幅度且默认关闭。

真实现有 HTTP 链路：WAV sad 6.075 秒；流式 excited（三次重复文本）输出 12.546 秒，生成耗时 2.255 秒，首包 0.157 秒，148 包。0.8 秒缓冲的模拟首播放 0.449 秒、断流 0。这是网络到达和模拟排队验证，**没有声称本轮真人浏览器听感/实际设备播放无断流**。冷加载含本轮首个 preview 48.06 秒。

[旧试听页面](experiments/qwen-mood/index.html)保留同源 A/B、平均电平对齐、接口输出和最初指令实验。用户认为只有语速改变，没有所需情感顿挫；旧方案不满足任务。客观保音高和相似度通过，不能推翻真人反馈。

## 可行的下一步

1. **使用已认可的向量 + Omni 方案**：当前样本的情绪改善和 Saya 身份由用户确认；继续观察不同文本中的文字完整性、自然度和情绪强度。不能据一次认可承诺全部语句，失败时保留样本继续评估。原生不会悄悄变速降级。
2. **评估具备独立克隆与情绪控制的模型**：IndexTTS-2 / 2.5 官方提供独立 `spk_audio_prompt` 与 `emo_audio_prompt` / `emo_vector`，还支持情绪强度 `emo_alpha`。可固定现有 Saya WAV，依次测试平静、sad、afraid 的向量与强度，`use_random=False`，与 Qwen 原始 Saya 等速等响对照。压力并不等同于 afraid，仍要选择合适表达并验收音色与文字。该路线的接口明确，**本次未安装或生成，不能承诺它一定保持现有音色**。[官方 API 与限制](https://github.com/index-tts/index-tts)。
3. **同一说话人的情绪参考库**：采集当前同一身份的 neutral/happy/sad 等参考及准确文字，选择参考时保持同一个人。音色一致性必须重新评估；不要用不同 VoiceDesign 描述各生成一人然后假称固定身份。本次没有伪造参考库。
4. **CustomVoice 或微调**：若接受预置声音，可换 1.7B CustomVoice 正式指令控制；若必须保持当前 Saya，研究用同一身份、带情绪标注的数据微调并验证控制能力。正式仓库提供 Base speaker 微调示例，但不能据此承诺一次 speaker 微调就自动得到高质量情绪控制。需要额外权重、数据和训练，本次未执行。

## 复现与检查

```powershell
.runtime/qwen-tts/Scripts/python.exe scripts/probe_qwen_clone_control.py
python scripts/audit_qwen_mood.py
.runtime/qwen-tts/Scripts/python.exe scripts/measure_qwen_mood.py
python scripts/audit_qwen_direction.py
.runtime/qwen-tts/Scripts/python.exe scripts/measure_qwen_direction.py
python scripts/audit_qwen_negative.py
.runtime/qwen-tts/Scripts/python.exe scripts/measure_qwen_direction.py --folder direction/negative
python scripts/audit_qwen_direction_routes.py
python scripts/audit_qwen_direction_routes.py --folder direction/vector-routes --x-vector-only
python -m http.server 18081 --bind 127.0.0.1 --directory docs/experiments/qwen-mood
```

HTTP 审计脚本启动专用 18080 TTS-only 服务，结束后关闭该服务及自己启动的 Omni。需要 18080/8091 可用；不要与已有 Saya 语音服务同时占用 8091。脚本直接请求 Base 指令作为实验，不保存这些参数到用户设置。

本次记录保存了原生、旧 DSP、Omni 初步指令、两轮多文本指令对照和实际接口输出。每轮生成审计中的参考 WAV、参考 JSON、语音设置及 `emotion_state.json` 均未变更；采用阶段只修改上述语音配置字段，单独记录。脚本重跑会覆盖对应结果；历史用户反馈保留在本报告，重跑时不能当成对新音频的评价。

回归覆盖现有本地语音、情绪锁/解锁/回落/关闭/持久化、陪伴功能、退出守护，以及真实正弦音的变时不变音高、PCM 分包一致性、停止清理、同次朗读心情冻结、Assistant 隔离、不重复加播放速度、模型指令透传、平静/情绪统一向量方式与保护固定参考。最终 **67 项 Python、20 项 Node 测试通过**。测试中的模型响应使用模拟；上述 GPU 实验使用真实模型。已有 FastAPI on_event 弃用提示不影响结果。
