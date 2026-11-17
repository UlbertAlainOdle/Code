/* Local voice settings and interruptible sentence playback. */
(() => {
    'use strict';
    const pageReady = window.SayaWebWindow?.whenReady || ((fn,options)=>document.addEventListener('DOMContentLoaded',fn,options));
    const $ = id => document.getElementById(id);
    let settings = null;
    let preview = null;
    let previewController = null;
    let dirty = false;
    const defaults = { enabled: true, provider: 'qwen_local', auto_read: false, engine: 'auto', stream_audio: true, stream_chat: true, stream_buffer_seconds: 0.8, mode: 'design', language: 'Chinese', description: '', voice_id: '', x_vector_only: false, temperature: 0.9, top_p: 1, top_k: 50, repetition_penalty: 1.05, max_new_tokens: 2048, seed: 42, chunk_chars: 100, volume: 1, playback_rate: 1, idle_unload_seconds: 300 };
    defaults.mood_prosody = false;
    defaults.mood_control = 'model';
    const checkboxes = new Set(['enabled', 'auto_read', 'x_vector_only', 'stream_audio', 'stream_chat', 'mood_prosody']);
    const numbers = new Set(['temperature', 'top_p', 'top_k', 'repetition_penalty', 'max_new_tokens', 'seed', 'chunk_chars', 'volume', 'playback_rate', 'idle_unload_seconds', 'stream_buffer_seconds']);

    async function api(path, options = {}) {
        const response = await fetch('/api/tts/' + path, options);
        if (!response.ok) {
            let message = `语音请求失败 (${response.status})`;
            try {
                const body = await response.json();
                message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body);
            } catch (_) {}
            throw new Error(message);
        }
        return response;
    }
    const jsonPost = value => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
    function message(text, error = false) {
        $('voice-message').textContent = text;
        $('voice-message').classList.toggle('voice-error', error);
    }
    function collect() {
        const result = { ...(settings || defaults) };
        Object.keys(defaults).forEach(key => {
            const element = $('voice-' + key);
            if (element) result[key] = checkboxes.has(key) ? element.checked : numbers.has(key) ? Number(element.value) : element.value;
        });
        return result;
    }
    function modeChanged() {
        $('voice-design-fields').hidden = $('voice-mode').value !== 'design';
        $('voice-clone-fields').hidden = $('voice-mode').value !== 'clone';
        $('voice-local-fields').hidden = $('voice-provider').value !== 'qwen_local';
        $('voice-cloud-note').hidden = $('voice-provider').value !== 'elevenlabs';
    }
    function apply() {
        Object.entries(settings).forEach(([key, value]) => {
            const element = $('voice-' + key);
            if (element) {
                if (checkboxes.has(key)) element.checked = value;
                else element.value = value;
            }
        });
        modeChanged();
    }
    async function voices() {
        const items = await (await api('voices')).json();
        const select = $('voice-voice_id');
        const selected = select.value || settings?.voice_id || '';
        select.replaceChildren(new Option('选择参考声音', ''));
        items.forEach(item => select.add(new Option(`${item.name} · ${item.duration}s`, item.id)));
        select.value = selected;
    }
    async function refresh() {
        const status = await (await api('status')).json();
        const backend = status.engine === 'vllm_omni' ? '流式加速' : '原生分段';
        $('voice-runtime-status').textContent = !status.installed ? '未安装' : status.busy ? `${backend} · 正在生成 / 加载` : status.loaded_mode ? `${backend} · ${status.loaded_mode === 'design' ? '设计' : '克隆'}模型已加载${status.allocated_mb ? ' · ' + status.allocated_mb + ' MB' : ''}` : `${backend} · 已安装 · 尚未加载`;
    }
    async function save() {
        const value = collect();
        const switching = !value.enabled || value.provider !== settings?.provider || value.engine !== settings?.engine;
        if (switching && (Speech.audio || Speech.pcm || Speech.chat || Speech.controller)) Speech.stop();
        settings = await (await api('settings', { ...jsonPost(value), method: 'PUT' })).json();
        dirty = false;
        apply();
        if (!settings.enabled) Speech.stop();
        message('设置已保存。');
        return settings;
    }
    async function action(button, task) {
        button.disabled = true;
        try { await task(); }
        catch (error) { if (error.name !== 'AbortError') message(error.message, true); }
        finally { button.disabled = false; }
    }
    function cleanSpeech(text, partial = false) {
        let raw = String(text || '');
        // Hold unfinished markup and faces instead of reading a prefix that later disappears.
        if (partial) raw = raw.replace(/&(?:#x?[\da-f]*|[a-z]*)$|:[a-z_+-]+$|\([^()\n]*$|（[^（）\n]*$/gi, '');
        let clean = raw.replace(/&#(x[\da-f]+|\d+);/gi, (entity, code) => {
            const point = code[0].toLowerCase() === 'x' ? parseInt(code.slice(1), 16) : Number(code);
            return point <= 0x10ffff && !(point >= 0xd800 && point <= 0xdfff) ? String.fromCodePoint(point) : entity;
        }).replace(/&(nbsp|amp|lt|gt|quot|apos);/g, (_, name) => ({nbsp:' ', amp:'&', lt:'<', gt:'>', quot:'"', apos:"'"}[name]));
        clean = clean.replace(/```[\s\S]*?(?:```|$)/g, '')
            .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
            .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
            .replace(/\[(?:MOTION|MC|MOOD_SAYA|MOOD_USER)[^\]]*\]/gi, '')
            .replace(/<br\s*\/?>/gi, '\n').replace(/<\/?(?:p|div|span|strong|em|b|i|a|img)\b[^>]*>/gi, '')
            .replace(/\[(?:微笑|笑哭|捂脸|偷笑|大笑|害羞|可爱|调皮|眨眼|开心|难过|流泪|大哭|哭泣|委屈|发怒|生气|惊讶|震惊|疑问|思考|叹气|无语|白眼|坏笑|比心|爱心|心碎|拥抱|抱抱|亲亲|飞吻|点赞|鼓掌|加油|握手|玫瑰|太阳|月亮|星星|狗头|旺柴|呲牙|嘻嘻|哈哈|汗|囧|笑)\]|:(?:smile|smiley|joy|laughing|laugh|sweat_smile|wink|blush|heart|heart_eyes|sob|cry|thinking|thinking_face|sunglasses|grin|grinning|rofl|thumbsup|thumbsdown|clap|pray|hugging_face|sparkles|fire|tada|warning|white_check_mark|100):/gi, ' ')
            .replace(/\([^()\n]{1,64}\)|（[^（）\n]{1,64}）/g, value => {
                const body = value.slice(1, -1);
                if (/[\u3400-\u9fff0-9]/.test(body)) return value;
                if (/[A-Za-z]/.test(body) && !/^[TQOoUwW_\-^;:><\s]+$/.test(body)) return value;
                return /[ωΩ▽ヮ⌣ᴗᵕ◍•⌔﹏╥ಥಠ눈∀∇‿益Д≧≦°＾^_;]/.test(body) || /^[><=;:._\-\s]{2,}$/.test(body) ? ' ' : value;
            })
            .replace(/(?<![A-Za-z0-9])(?:QAQ|QwQ|T_T|Q_Q|UwU|OwO|\^[_﹏-]\^)(?![A-Za-z0-9])/g, ' ')
            .replace(/(?<![A-Za-z0-9/:])(?:[:;=8][-^']?[)DdpP(\\/]|<3)(?=$|[\s，。！？!?,])/g, ' ')
            .replace(/[0-9#*][\uFE0E\uFE0F]?\u20E3|[\u{1F000}-\u{1FAFF}\u2600-\u27BF\u2194-\u2199\u21A9\u21AA\u231A\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA\u24C2\u25AA\u25AB\u25B6\u25C0\u25FB-\u25FE\u2934\u2935\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299\u00A9\u00AE\u2122]+/gu, ' ')
            .replace(/[\u200B-\u200F\u2066-\u2069\uFE0E\uFE0F\u20E3\u{E0020}-\u{E007F}]/gu, '')
            .replace(/\[[^\]]*$|\[[^\]]*\]\([^)]*$/g, '')
            .replace(/[*#`]/g, '').replace(/(?<![\p{L}\p{N}_])_+|_+(?![\p{L}\p{N}_])/gu, '')
            .replace(/[ \t]+/g, ' ').replace(/[ \t]+([，。！？,.!?;；:：])/g, '$1').replace(/[\uD800-\uDBFF]$/, '');
        return clean;
    }
    function speechLanguage(text, configured) {
        if (configured !== 'Chinese' || !/[A-Za-z]/.test(text)) return configured;
        return /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]/.test(text) ? 'Auto' : 'English';
    }
    function chunkSettings(text, snapshot) {
        return snapshot.provider === 'qwen_local' ? {...snapshot, language: speechLanguage(text, snapshot.language)} : snapshot;
    }
    function firstSentenceLength(text) {
        for (let i = 0; i < text.length; i++) {
            if (/[。！？!?\n]/.test(text[i])) return i + 1;
            if (text[i] !== '.' || /[A-Za-z0-9]/.test(text[i + 1] || '')) continue;
            const token = text.slice(0, i).match(/[A-Za-z.]+$/)?.[0] || '';
            if (/^(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|[A-Za-z])$/i.test(token)) continue;
            return i + 1;
        }
        return 0;
    }
    function wordSafeCut(text, limit, final = true) {
        let cut = Math.min(limit, text.length);
        const word = char => /[A-Za-z0-9_'’\-]/.test(char || '');
        if (cut < text.length && word(text[cut - 1]) && word(text[cut])) {
            while (cut > 0 && word(text[cut - 1])) cut--;
            if (!cut) {
                cut = limit;
                while (cut < text.length && word(text[cut])) cut++;
                if (cut >= 1000) cut = limit; // Keep the API's text bound for pathological tokens.
            }
        } else if (!final && cut === text.length && word(text[cut - 1])) {
            while (cut > 0 && word(text[cut - 1])) cut--;
        }
        // Never cut a Unicode surrogate pair, even for uncommon scripts.
        if (cut && /[\uD800-\uDBFF]/.test(text[cut - 1])) cut--;
        return cut;
    }
    function chunkText(text, limit) {
        const clean = cleanSpeech(text).trim();
        const parts = [];
        for (let offset = 0; offset < clean.length;) {
            const length = firstSentenceLength(clean.slice(offset)) || clean.length - offset;
            parts.push(clean.slice(offset, offset + length));
            offset += length;
        }
        const chunks = [];
        let buffer = '';
        for (const sentence of parts) {
            if (buffer && (buffer + sentence).length > limit) { chunks.push(buffer.trim()); buffer = ''; }
            let remainder = sentence;
            while (remainder.length > limit) {
                const cut = wordSafeCut(remainder, limit);
                chunks.push(remainder.slice(0, cut).trim()); remainder = remainder.slice(cut);
            }
            buffer += remainder;
        }
        if (buffer.trim()) chunks.push(buffer.trim());
        return chunks.filter(part => /[\p{L}\p{N}]/u.test(part));
    }

    async function canStream(snapshot) {
        if (!snapshot.stream_audio || snapshot.provider !== 'qwen_local' || snapshot.engine === 'transformers') return false;
        const status = await (await api('status')).json();
        return status.streaming === true;
    }

    async function sessionMood(snapshot, context = {}) {
        if (context.roleMode === 'assistant') return 'neutral';
        if (!snapshot.mood_prosody || snapshot.mode !== 'clone' || snapshot.provider !== 'qwen_local') return 'neutral';
        if (context.moodState) return context.moodState.speech_mood || context.moodState.dominant || 'neutral';
        if (context.sessionId !== undefined) {
            const state = await (await fetch('/api/companion/mood?session_id=' + encodeURIComponent(context.sessionId))).json();
            return state.enabled === false ? 'neutral' : state.speech_mood || state.dominant || 'neutral';
        }
        return (await (await api('prosody')).json()).mood;
    }

    class ChatReadSession {
        constructor(player, controller, snapshot, token, mood) {
            this.player = player; this.controller = controller; this.snapshot = snapshot; this.token = token;
            this.mood = mood;
            this.raw = ''; this.cursor = 0; this.queue = []; this.ended = false; this.wake = null;
            this.done = this.run();
            this.done.catch(error => {
                if (error.name !== 'AbortError' && this.token === Speech.token) window.showToast?.(error.message, { type: 'error', duration: 5000 });
            });
        }
        push(delta) {
            if (this.controller.signal.aborted || this.ended || this.token !== Speech.token) return;
            this.raw += delta;
            const clean = cleanSpeech(this.raw, true);
            while (this.cursor < clean.length) {
                const remaining = clean.slice(this.cursor);
                let length = firstSentenceLength(remaining);
                if (!length && remaining.length < this.snapshot.chunk_chars) break;
                length = wordSafeCut(remaining, Math.min(length || this.snapshot.chunk_chars, this.snapshot.chunk_chars), !!length);
                if (!length) break;
                const text = remaining.slice(0, length).trim();
                this.cursor += length;
                if (/[\p{L}\p{N}]/u.test(text)) this.queue.push(text);
                this.wake?.(); this.wake = null;
            }
        }
        finish(finalText = '') {
            if (this.ended) return this.done;
            if (finalText.startsWith(this.raw)) this.push(finalText.slice(this.raw.length));
            const tail = cleanSpeech(this.raw).slice(this.cursor).trim();
            if (tail) this.queue.push(...chunkText(tail, this.snapshot.chunk_chars));
            this.ended = true;
            this.wake?.(); this.wake = null;
            return this.done;
        }
        cancel() { this.controller.abort(); this.wake?.(); this.wake = null; void this.player.close(); }
        async run() {
            try {
                while (!this.controller.signal.aborted) {
                    if (!this.queue.length) {
                        if (this.ended) break;
                        await new Promise(resolve => { this.wake = resolve; });
                        continue;
                    }
                    const text = this.queue.shift();
                    const response = await api('stream', { ...jsonPost({ text, settings: chunkSettings(text, this.snapshot), mood: this.mood }), signal: this.controller.signal });
                    await this.player.read(response);
                }
                if (!this.controller.signal.aborted) Speech.lastMetrics = await this.player.finish();
            } finally {
                this.controller.abort();
                await this.player.close();
                if (Speech.chat === this) Speech.chat = null;
            }
        }
    }

    const Speech = {
        token: 0, audio: null, controller: null, finish: null, pcm: null, chat: null, lastMetrics: null,
        stop() {
            this.token++;
            this.chat?.cancel(); this.chat = null;
            if (this.pcm) void this.pcm.close();
            this.pcm = null;
            this.controller?.abort();
            this.controller = null;
            this.audio?.pause();
            this.finish?.();
            this.audio = null;
            this.finish = null;
        },
        async fetchChunk(text, signal, snapshot, mood) {
            let response;
            if (snapshot.provider === 'qwen_local') {
                response = await api('synthesize', { ...jsonPost({ text, settings: chunkSettings(text, snapshot), mood }), signal });
            } else {
                await window.SayaLocalProfile?.ready();
                const cloud = window.getSavedOtherApiSettings?.() || {};
                response = await fetch('/tts', { ...jsonPost({ text, api_url: cloud.tts_api_url, api_key: cloud.tts_api_key, voice_id: cloud.tts_voice_id, model_id: cloud.tts_model_id }), signal });
                if (!response.ok) {
                    const body = await response.json().catch(() => ({}));
                    throw new Error(body.detail || `TTS failed (${response.status})`);
                }
            }
            if (!response.headers.get('Content-Type')?.includes('audio')) throw new Error('语音接口没有返回音频。');
            return URL.createObjectURL(await response.blob());
        },
        play(url, snapshot) {
            return new Promise((resolve, reject) => {
                const audio = new Audio(url);
                this.audio = audio;
                audio.volume = snapshot.volume;
                audio.playbackRate = snapshot.playback_rate;
                audio.preservesPitch = true;
                const finish = error => {
                    if (this.audio === audio) { this.audio = null; this.finish = null; }
                    error ? reject(error) : resolve();
                };
                this.finish = () => finish();
                audio.onended = () => finish();
                audio.onerror = () => finish(new Error('音频无法播放。'));
                audio.play().catch(() => finish(new Error('浏览器阻止自动播放，请先点击一次朗读或试听。')));
            });
        },
        async speak(text, context = {}) {
            this.stop();
            this.lastMetrics = null;
            const token = this.token;
            await window.SayaVoice.ready;
            if (token !== this.token) return;
            if (!settings?.enabled) throw new Error('请在 Voice 语音设置中开启语音。');
            if (dirty) await save();
            if (token !== this.token) return;
            const snapshot = { ...settings };
            const chunks = chunkText(text, snapshot.chunk_chars);
            if (!chunks.length) return;
            const mood = await sessionMood(snapshot, context);
            const streaming = await canStream(snapshot);
            if (token !== this.token) return;
            const controller = new AbortController();
            this.controller = controller;
            if (streaming) {
                if (token !== this.token) { controller.abort(); return; }
                let player;
                try {
                    player = await window.SayaPCMPlayer.create(snapshot, controller.signal);
                    this.pcm = player;
                    for (const text of chunks) {
                        if (controller.signal.aborted || token !== this.token) break;
                        const response = await api('stream', { ...jsonPost({ text, settings: chunkSettings(text, snapshot), mood }), signal: controller.signal });
                        await player.read(response);
                    }
                    if (!controller.signal.aborted) this.lastMetrics = await player.finish();
                } catch (error) {
                    if (error.name !== 'AbortError' && token === this.token) throw error;
                } finally {
                    controller.abort();
                    await player?.close();
                    if (this.pcm === player) this.pcm = null;
                    if (this.controller === controller) this.controller = null;
                }
                return;
            }
            let pending = null;
            let url = null;
            try {
                for (let i = 0; i < chunks.length; i++) {
                    if (token !== this.token) break;
                    url = pending ? await pending : await this.fetchChunk(chunks[i], controller.signal, snapshot, mood);
                    pending = null;
                    if (token !== this.token) break;
                    // Catch immediately to avoid unhandled rejections while the current audio plays.
                    pending = i + 1 < chunks.length ? this.fetchChunk(chunks[i + 1], controller.signal, snapshot, mood).then(value => ({ value }), error => ({ error })) : null;
                    await this.play(url, snapshot);
                    URL.revokeObjectURL(url); url = null;
                    if (pending) {
                        const next = await pending; pending = null;
                        if (next.error) throw next.error;
                        if (token !== this.token) { URL.revokeObjectURL(next.value); break; }
                        pending = Promise.resolve(next.value);
                    }
                }
            } catch (error) {
                if (error.name !== 'AbortError' && token === this.token) throw error;
            } finally {
                controller.abort();
                if (url) URL.revokeObjectURL(url);
                if (pending) {
                    const next = await pending.catch(() => null);
                    if (typeof next === 'string') URL.revokeObjectURL(next);
                    else if (next?.value) URL.revokeObjectURL(next.value);
                }
                if (this.controller === controller) this.controller = null;
            }
        }
    };
    window.SayaSpeech = Speech;
    window.SayaVoice = {
        ready: null,
        refresh: () => refresh().catch(error => message(error.message, true)),
        async beginChat(context = {}) {
            const token = Speech.token;
            await this.ready;
            if (!settings?.enabled || !settings.auto_read || !settings.stream_chat) return null;
            if (dirty) await save();
            const snapshot = { ...settings };
            if (!await canStream(snapshot)) return null;
            const mood = await sessionMood(snapshot, context);
            if (token !== Speech.token) return { push() {}, cancel() {}, async finish() {} };
            const controller = new AbortController();
            Speech.controller = controller;
            try {
                const player = await window.SayaPCMPlayer.create(snapshot, controller.signal);
                if (token !== Speech.token) { controller.abort(); await player.close(); return { push() {}, cancel() {}, async finish() {} }; }
                const session = new ChatReadSession(player, controller, snapshot, token, mood);
                Speech.lastMetrics = null;
                Speech.chat = session;
                return session;
            } catch (error) {
                controller.abort();
                if (error.name !== 'AbortError') window.showToast?.(error.message, { type: 'error', duration: 5000 });
                return null;
            }
        },
        async autoRead(text, context = {}) {
            await this.ready;
            if (settings?.enabled && settings.auto_read) {
                if (context.button?.dataset.manualVoiceRequested === 'true') return;
                try { if (context.button && window.SayaChatUI) await window.SayaChatUI.speak(context.button, text, context); else await Speech.speak(text, context); }
                catch (error) { window.showToast?.(error.message, { type: 'error', duration: 5000 }); }
            }
        }
    };
    window.addEventListener('beforeunload', () => Speech.stop());
    window.SayaVoice.ready = new Promise(resolve => pageReady( async () => {
        try {
            settings = await (await api('settings')).json();
            await voices(); apply();
        } catch (error) { message(error.message, true); }
        resolve();
        $('voice-settings-form').addEventListener('input', () => { dirty = true; message('有未保存的设置。'); modeChanged(); });
        $('voice-save').onclick = event => action(event.currentTarget, save);
        $('voice-refresh').onclick = event => action(event.currentTarget, refresh);
        $('voice-load').onclick = event => action(event.currentTarget, async () => { await save(); message('正在加载模型，首次加载需要一点时间…'); await api('load', jsonPost({})); await refresh(); });
        const stopPlayback = () => window.SayaChatUI ? window.SayaChatUI.stopSpeech() : Speech.stop();
        $('voice-unload').onclick = event => action(event.currentTarget, async () => { stopPlayback(); previewController?.abort(); $('voice-preview-audio').pause(); await api('unload', jsonPost({})); await refresh(); message('已释放语音模型显存。'); });
        $('voice-stop').onclick = () => { stopPlayback(); previewController?.abort(); $('voice-preview-audio').pause(); message('已停止播放并取消后续朗读；释放显存可关闭语音模型。'); };
        $('voice-preview').onclick = event => action(event.currentTarget, async () => {
            Speech.stop(); await save();
            if (settings.provider === 'elevenlabs') {
                preview = null;
                $('voice-save-reference').disabled = true;
                await Speech.speak($('voice-test-text').value);
                message('云端试听已播放。');
                return;
            }
            message('正在生成试听…');
            previewController?.abort();
            const controller = new AbortController();
            previewController = controller;
            const text = cleanSpeech($('voice-test-text').value).trim();
            if (!/[\p{L}\p{N}]/u.test(text)) throw new Error('没有可朗读的文字，表情和装饰符号不会朗读。');
            preview = await (await api('preview', { ...jsonPost({ text, settings: chunkSettings(text, settings), mood: $('voice-preview-mood').value, session_id: window.getSayaChatContext?.().sessionId ?? '' }), signal: controller.signal })).json();
            if (controller.signal.aborted) return;
            previewController = null;
            const audio = $('voice-preview-audio');
            audio.src = preview.url;
            audio.volume = settings.volume;
            audio.playbackRate = settings.playback_rate;
            audio.preservesPitch = true;
            audio.hidden = false;
            await audio.play().catch(() => {});
            $('voice-save-reference').disabled = !!preview.prosody?.instructions || preview.prosody?.tempo !== 1 || preview.prosody?.gain !== 1;
            message(`生成 ${preview.duration} 秒音频，用时 ${preview.elapsed_seconds} 秒${preview.peak_allocated_mb ? '，PyTorch 峰值分配 ' + preview.peak_allocated_mb + ' MB' : ' · 流式加速后端'}。`);
            await refresh();
        });
        $('voice-save-reference').onclick = event => action(event.currentTarget, async () => {
            if (!preview) throw new Error('请先生成试听。');
            const voice = await (await api('voices/from-preview', jsonPost({ preview_id: preview.id, name: $('voice-name').value }))).json();
            await voices(); $('voice-voice_id').value = voice.id; $('voice-mode').value = 'clone'; modeChanged(); await save(); message('已保存声音并切换为克隆模式。');
        });
        $('voice-upload').onclick = event => action(event.currentTarget, async () => {
            const file = $('voice-reference-file').files[0];
            if (!file) throw new Error('请选择参考音频。');
            if (file.size > 10 * 1024 * 1024) throw new Error('音频不能超过 10 MB。');
            const params = new URLSearchParams({ name: $('voice-name').value || file.name, text: $('voice-reference-text').value });
            const voice = await (await api('voices/upload?' + params, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file })).json();
            await voices(); $('voice-voice_id').value = voice.id; $('voice-mode').value = 'clone'; modeChanged(); await save(); message('参考声音已导入并启用。');
        });
    }, { once: true }));
})();
