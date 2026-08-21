# Saya MMD Motion Trigger Guide

This guide describes the current 47 motions loaded from `motion_manifest.json`.
The `description`, `source_name`, and `comment` fields are annotations only. They
are never read as trigger words.

## Runtime order

1. On model open or refresh, the startup pool chooses `Idle_normal_187.vmd` with weight 4 and `door_knock.vmd` with weight 1. The selected entry plays once/loops according to its manifest playback and is not repeated by the idle timer.
2. Chat no longer scans user text or Saya response text for keyword-to-action triggers. There is no chat-side `reaction_intent` priority path.
3. The model may emit one `[MOTION: category]` tag. Only the ten chat mood pools are accepted: `normal_idle`, `calm_idle`, `happy_mood`, `sad_emotion`, `gentle_comfort`, `curious_interest`, `giving_answer`, `praised`, `playful_magic`, and `neutral_waiting`.
4. When a valid chat mood tag is received, the runtime chooses a concrete VMD from that category pool by weight. If no valid tag is received, `giving_answer` is the fallback pool.
5. One-shot motions selected from a chat mood pool still play as one-shot actions and then return to idle, but they are pool selections rather than keyword-forced actions.
6. Night motions require an explicit timed-idle request. Ordinary chat, response tags, and direct motion API calls cannot select a sleepy category without that authorization.
7. A short click on the rendered model is raycast against the animated mesh. Head, hand/arm, upper-body, and lower-body regions use separate weighted reaction pools; pointer movement remains camera rotation and does not count as a click.

## Current semantic triggers

- `jealous_pout`: phrases about being ignored or not accompanied, being replaced by another person/AI, jealousy, or attention belonging to someone else.
- `greeting`: `你好`, `你好呀`, `早安`, `早呀`, `早啊`, `晚上好`, `在吗`, `回来啦`, `我回来了`, `嗨`, `哈喽`, and English greetings.
- `farewell`: `拜拜`, `再见`, `回头见`, `下次见`, `一会儿见`, `待会儿见`, `先走了`, `先去忙`, `下线了`, `回头聊`, `晚点聊`, and English farewells.
- `refusal`: an explicit request such as `拒绝我`, `请拒绝我`, `你说不行`, or `你不许我`; ordinary `不行/不要` is kept as waiting context unless Saya's response actually refuses.
- `confused`: `什么意思`, `啥意思`, `没懂`, `没看懂`, `看不懂`, `不明白`, `怎么回事`, and related confusion phrases.
- `coquettish_tease`: `撒娇`, `撒撒娇`, `撒一下娇`, `娇一点`, `卖萌`, `卖个萌`, `装可爱`, and matching English requests.
- `attention_call`: explicit requests such as `敲门`, `看这里`, `注意我`, and `回应我`.
- `complaint`: playful displeasure such as `哼`, `生气了`, `委屈巴巴`, and `坏蛋`.
- `cautious_smile`: explicit requests such as `笑一个`, `微笑一下`, and `给我笑笑`.
- User apology wording routes to `forgive_doctor` for Saya's reassurance response. Explicitly asking Saya to apologize routes to `apology_from_saya`.
- `sleepy`: no longer an input keyword trigger. During night idle, there is a 30% attempt chance once per five-minute interval; when selected, `Sleepy_yawn_idle_113.vmd`, `sleepy_tired.vmd`, and `sleepy_eyes_closed.vmd` have equal weights.
- Apology wording such as `我错了`, `对不起`, and `抱歉` is not treated as a user-side trigger. `apologizing.vmd` is selected when Saya's actual response is an apology; `reassuring_nevermind.vmd` is a separate reassurance response.

## Model click reactions

- Head: always non-negative; shy blush, cautious smile, coquettish tease,
  delighted surprise, or a small surprise.
- Hand / arm: 15% negative response probability.
- Upper body: 62% negative response probability.
- Lower body: 38% negative response probability.
- `bold_snicker.vmd` is explicitly excluded from every touch pool.
- Once a touch reaction begins, it owns a four-second uninterruptible window.
  Further touches, chat reactions, and idle changes cannot interrupt it.
- At four seconds the current action fades into the classic idle motion
  `Idle_normal_187.vmd` even if its clip is longer. The return path does not
  hard-restore the model to the binding pose; it lets the idle action take over
  bones and facial morphs, then gently settles leftover morphs that the idle
  action does not control. Queued chat or reaction motions wait until this
  short idle fade has settled.
- Interaction lookup accepts both manifest IDs and legacy URL-only
  `/get_vmd_files` responses, so clicks still work while an older backend is
  serving the current frontend.

Interaction selection uses weighted randomness, avoids immediately repeating the
same action, and applies a short per-motion cooldown. A pointer movement over
seven pixels or a press longer than 650 ms is treated as camera manipulation,
not a body click.

## Trigger priority

1. An explicit user keyword reaction is emitted first and suppresses the
   ordinary answer motion at the end of the same reply.
2. A reaction found in Saya's response may replace an earlier generic model
   motion tag.
3. A model-generated motion category is used when no keyword reaction applies.
4. `giving_answer` is emitted only when none of the higher-priority routes
   selected a motion.

## Motion inventory

| File | Type | Description | Trigger / selection logic |
|---|---|---|---|
| `Idle_normal_187.vmd` | idle | 更好的平常站着。主待机循环，也可作为回答时的稳定动作。 | `normal_idle` fallback; also stable `searching_task`, `giving_answer`, `curious_interest`, and `calm_idle`. |
| `Idle4_157.vmd` | idle | 向左右边看看。自然待机与轻度关注。 | Weighted candidate for normal, task, answer, happy, and praised categories. |
| `a_idle_neutral_loop_88.vmd` | idle | 只是平常站着。普通小概率待机循环。 | Weighted normal/calm idle candidate and fallback variation. |
| `Idle_341.vmd` | idle | 左顾右盼。适合聆听、观察、情绪问题。 | Listening, emotional, answer, curious, sad, and waiting category candidate. |
| `lifegurad_idle_272.vmd` | idle | 手放在裙子上倾听。适合情绪倾听和安慰。 | Emotional listening and gentle comfort loop; also normal/sad fallback. |
| `wainting_359.vmd` | idle | 叉腰叹气。适合低落、等待、轻微埋怨。 | `sad_emotion`, `neutral_waiting`, and complaint category candidate. |
| `Idle_searchong_173.vmd` | idle | 向下查看。任务处理、查询、思考时循环。 | `searching_task` and thinking processing state. |
| `Idle_motion_confident_148.vmd` | idle | 给出回答时的自信动作。 | `giving_answer` and idea category candidate. |
| `looking_96.vmd` | idle | 向电脑外张望。适合心情好、好奇、轻松互动。 | Happy, praised, curious, and playful category candidate. |
| `Fairy_idle_362.vmd` | reaction | 升起来吓你一下。虽然文件名含 idle，但动作语义是彩蛋/惊吓式反应，更适合播放一次后回到待机。 | Easter egg or playful magic intent/category; replaces the removed Robot idle. |
| `hold_hands_119.vmd` | reaction | 伸手/牵手式回应。适合被夸赞或温柔安抚时播放一次，不作为常驻待机。 | `praised`, `comfort`, or `invite`; selected as a one-shot reaction. |
| `Sleepy_yawn_idle_113.vmd` | idle | 打哈欠。夜间或困倦时循环。 | `night_sleepy` routine candidate; never selected by the sleepy input keyword. |
| `Sleepy_idle_158.vmd` | reaction | 困死了被惊醒。语义是被打断后的反应，不适合作为普通夜间循环。 | Reserved for a future `sleepy_startled` interruption state; no ordinary keyword route. |
| `farewell_wave.vmd` | reaction | 拜拜。适合告别、下次见。 | Immediate `farewell` intent from farewell phrases. |
| `refusal_no.vmd` | reaction | 不行。明确拒绝、不要这样。 | Immediate `refusal` intent or a response that explicitly refuses. |
| `refusal_no_alt.vmd` | reaction | 不行_2。拒绝动作的变化版。 | Same `refusal` intent; recent-motion rotation chooses between refusal variants. |
| `playful_finger_gun.vmd` | reaction | 朝你开枪（耍可爱）。玩笑、调皮、彩蛋。 | `finger_gun`, playful tease, or easter egg intent. |
| `jealous_pout.vmd` | reaction | 吃醋了。适合吃醋、占有欲玩笑。 | Immediate `jealous_pout` intent; expanded ignored/replaced/other-AI phrases route here first. |
| `surprised_small.vmd` | reaction | 吃惊。轻度惊讶。 | `surprise` intent/category, weighted against other surprise variants. |
| `bold_snicker.vmd` | reaction | 大胆偷笑。玩笑、坏笑、轻松互动。 | `snicker`, playful tease, or laugh intent. |
| `loud_greeting.vmd` | reaction | 大声打招呼。适合见面、回来、呼唤。 | Immediate `greeting` intent; `door_knock` is also a greeting variation. |
| `apologizing.vmd` | reaction | 道歉。仅用于 Saya 的回复语义确实是在道歉、承认说错或被要求道歉，不用于用户道歉时。 | Response text containing apology/acknowledgement wording; not user `对不起/抱歉` input. |
| `detective_thinking.vmd` | processing_loop | 福尔摩斯思考。适合推理/分析期间维持思考状态，可循环。 | `searching_task`/thinking processing category, especially analysis or research. |
| `delighted_surprise.vmd` | reaction | 感到惊喜。开心的惊讶。 | `delighted_surprise`, surprise, praised, or laugh intent. |
| `hearty_laugh.vmd` | reaction | 哈哈大笑。明显开心或觉得好笑。 | `laugh` or praised intent; rotates with other laugh variants. |
| `shy_blush.vmd` | reaction | 害羞脸红。被表白、夸赞或亲密表达时。 | Immediate `shy_blush` from intimate/praise phrases. |
| `surprised_big.vmd` | reaction | 很吃惊。强烈惊讶。 | Immediate `surprise_big`; stronger surprise phrases are checked before ordinary surprise. |
| `confused_head_scratch_right.vmd` | reaction | 困惑挠头右。疑惑、不懂、思考卡住。 | Immediate `confused` intent; random intent rotation chooses a confused variant. |
| `confused_head_scratch_left.vmd` | reaction | 困惑挠头左。疑惑、不懂、思考卡住。 | Same immediate `confused` intent with recent-selection avoidance. |
| `sleepy_tired.vmd` | reaction | 困了。更像困倦表达/过渡反应，播放一次后回到夜间待机。 | Included in `night_sleepy` routine weighting; no direct sleepy keyword route. |
| `sleepy_eyes_closed.vmd` | idle | 困了闭眼。适合夜间或困倦状态下维持循环。 | `night_sleepy` or sleepy idle routine candidate; not an input reaction. |
| `reassuring_nevermind.vmd` | reaction | 没关系啊。仅用于用户道歉、自责或请求原谅时，Saya 做安抚/原谅回应；不等同于 Saya 道歉。 | Response-side `forgive_doctor` reassurance, kept distinct from `apology_from_saya`. |
| `confused_cheek_scratch.vmd` | reaction | 挠脸（困惑）。轻微困惑或不好意思。 | `confused` intent/category variant. |
| `teasing_are_you_picking_a_fight.vmd` | reaction | 你是在找茬吗。玩笑式挑衅或吐槽。 | `picking_a_fight`, playful tease, or complaint intent. |
| `door_knock.vmd` | reaction | 敲门。呼唤、打招呼、提醒注意；可作为 greeting 的变化。 | Startup pool weight 1 against `Idle_normal_187.vmd` weight 4; it may also be a weighted greeting variation, but never repeats from the idle timer. |
| `coquettish_you_are_bad.vmd` | reaction | 撒娇（你坏坏）。亲密玩笑、撒娇。 | `coquettish_tease`, playful tease, or shy-blush intent. |
| `inviting_left_hand.vmd` | reaction | 伸出左手（邀请）。牵手、邀请、靠近。 | Immediate `invite` intent. |
| `arms_crossed_front.vmd` | idle | 双臂插胸前。姿态型等待/不满动作，适合短时间循环维持情绪。 | Waiting, complaint, and sad category loop; not the one-shot refusal itself. |
| `thinking.vmd` | processing_loop | 思考。普通思考/处理中动作，适合循环。 | `searching_task` and thinking processing category. |
| `pout_turn_head_hmph.vmd` | reaction | 头扭到一边（哼）。撒娇式不满/委屈；也可作为吃醋语义的低概率变化。动作到达最后一帧后统一静止 1 秒，再执行后续动作。 | Complaint, playful tease, or weighted jealousy variation. |
| `helpless_listening_look.vmd` | listening_loop | 无奈看着你（听你说）。是持续倾听/陪伴姿态，适合作为情绪聆听循环。 | Emotional listening, gentle comfort, and waiting loop; generic comfort fallback. |
| `cautious_smile.vmd` | reaction | 小心地笑。轻柔开心、安抚、缓和气氛；用于夸赞/安慰的轻量变化。 | Praised, comfort, shy-blush, and happy response variation. |
| `got_an_idea.vmd` | reaction | 有办法了。想到方案或准备给答案。 | `idea` intent from solution/answer wording. |
| `got_an_idea_alt.vmd` | reaction | 有办法了_2。想到方案的变化版。 | Same `idea` intent; recent rotation avoids repeating the first idea motion. |
| `timid_door_knock.vmd` | reaction | 有点害怕的敲门。害怕、小心试探。 | Only `timid_knock` phrases such as 小心/害怕/试探敲门; never the ordinary startup knock. |
| `pointing_to_self_me.vmd` | reaction | 指向自己（我吗？）。疑问是不是自己、被点名。 | Immediate `self_question`; also a confused variant when the wording is ambiguous. |
| `troubled_left_head_scratch.vmd` | reaction | 左手挠头（困扰）。困扰、纠结、麻烦。 | `troubled` intent or confused category variant. |

## Maintenance rule for new motions

Add a manifest entry with a stable English `id`/`file`, Chinese `description`,
`kind`, `playback`, `return_category`, and one or more existing `intent` or
`categories`. Do not add Chinese descriptions to the trigger code. Only add a
new semantic rule in `core_services.py` when a new meaning genuinely cannot be
represented by an existing intent; variants of an existing meaning only need a
new manifest entry and weights.
