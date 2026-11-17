const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function messageReader() {
    const source = fs.readFileSync('static/js/app.js', 'utf8');
    const start = source.indexOf('    function getAiMessageText(');
    const end = source.indexOf('    function showStoredTranslation(', start);
    const context = vm.createContext({});
    vm.runInContext(source.slice(start, end), context);
    return { source, context, read: row => context.getAiMessageText(row) };
}

test('chat read button excludes memory, tool and status panels from the reply', async () => {
    const { read } = messageReader();
    const bubble = { innerText: 'Saya: 你好。记忆已保存。Tool complete。已停止生成。来源。翻译。' };
    const row = { dataset: { rawContent: '你好。**今天辛苦了。** [说明](https://example.com)\n```js\n不应该朗读的代码。\n```' }, querySelector: () => bubble };
    const h = await harness({ stream_audio: true });
    await h.speech.speak(read(row));
    assert.equal(h.fetched.map(item => item.body.text).join(''), '你好。今天辛苦了。 说明');
});

test('reply deltas are buffered while the public progress remains visible', () => {
    const { source, context, read } = messageReader();
    const bubble = { innerText: 'Saya: 第一段。Tool running。' };
    const row = { dataset: { rawContent: '' }, querySelector: () => bubble };
    Object.assign(context, { aiDiv: row, fullResponseText: '', chatVoicePending: null, textSpan: {},
        renderMarkdownSafe: text => text, document: { getElementById: () => ({ scrollTop: 0, scrollHeight: 0 }) } });
    const start = source.indexOf('            const handleStreamEvent = ');
    const end = source.indexOf('            const handleSseBlock = ', start);
    vm.runInContext(source.slice(start, end), context);
    assert.equal(read(row), '');
    vm.runInContext("handleStreamEvent('text', {text: '第一句。'})", context);
    assert.equal(read(row), '第一句。');
    vm.runInContext("handleStreamEvent('text', {text: '第二句。'})", context);
    assert.equal(read(row), '第一句。第二句。');
    assert.equal(context.textSpan.innerHTML, undefined);
});

test('historical reading uses its frozen mood instead of the latest conversation mood', async () => {
    const h = await harness({mood_prosody: true, stream_audio: true});
    await h.speech.speak('一'.repeat(95) + '。', {sessionId:'old-session', moodState:{dominant:'neutral', speech_mood:'stressed'}});
    assert.equal(h.moodReads(), 0);
    assert.ok(h.fetched.length > 1);
    assert.ok(h.fetched.every(item => item.body.mood === 'stressed'));
});

test('reading a message without raw metadata uses only its body and never the bubble', () => {
    const { read } = messageReader();
    const bubble = { innerText: '回复正文。附加记录。', querySelector: selector => {
        assert.equal(selector, ':scope > .message-body');
        return { innerText: 'Saya: 回复正文。' };
    } };
    assert.equal(read({ dataset: {}, querySelector: () => bubble }), '回复正文。');
    assert.equal(read({ dataset: {}, querySelector: () => null }), '');
});

async function harness(overrides = {}) {
    const config = { enabled: true, provider: 'qwen_local', auto_read: false, mode: 'clone', language: 'Chinese', description: 'test', voice_id: '', x_vector_only: false, temperature: 0.9, top_p: 1, top_k: 50, repetition_penalty: 1.05, max_new_tokens: 2048, seed: 42, chunk_chars: 30, volume: 0.4, playback_rate: 1.2, idle_unload_seconds: 300, ...overrides };
    const elements = new Map();
    function element(id) {
        if (!elements.has(id)) elements.set(id, { value: '', checked: false, textContent: '', classList: { toggle() {} }, addEventListener() {}, replaceChildren() {}, add() {}, pause() {} });
        return elements.get(id);
    }
    const callbacks = [];
    const fetched = [], revoked = [], audio = [], blobs = [];
    let holdFetch = false, holdAudio = false, failFetch = false;
    const pcm = [];
    let moodReads = 0;
    const window = { addEventListener() {}, getSavedOtherApiSettings: () => ({ tts_voice_id: 'cloud-voice' }) };
    window.SayaPCMPlayer = { async create(config, signal) {
        const instance = { config, signal, reads: 0, closed: false, async read() { this.reads++; }, async finish() { return { underruns: 0 }; }, async close() { this.closed = true; } };
        pcm.push(instance);
        return instance;
    } };
    const context = {
        window, document: { getElementById: element, addEventListener: (_, callback) => callbacks.push(callback) },
        Option: function () {}, AbortController, console,
        URL: { createObjectURL(blob) { blobs.push(blob); return `blob:${blobs.length}`; }, revokeObjectURL(url) { revoked.push(url); } },
        URLSearchParams,
        Audio: class {
            constructor(url) { this.url = url; audio.push(this); }
            pause() { this.paused = true; }
            async play() { if (!holdAudio) queueMicrotask(() => this.onended?.()); }
        },
        async fetch(url, options = {}) {
            if (url.endsWith('/settings')) return { ok: true, json: async () => config };
            if (url.endsWith('/voices')) return { ok: true, json: async () => [] };
            if (url.endsWith('/status')) return { ok: true, json: async () => ({ streaming: true }) };
            if (url.endsWith('/prosody')) { moodReads++; return { ok: true, json: async () => ({ mood: moodReads === 1 ? 'sad' : 'excited' }) }; }
            fetched.push({ url, body: JSON.parse(options.body), signal: options.signal });
            if (holdFetch) await new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true }));
            if (failFetch && fetched.length > 1) return { ok: false, status: 503, json: async () => ({ detail: 'GPU unavailable' }) };
            return { ok: true, headers: { get: () => config.provider === 'qwen_local' ? 'audio/wav' : 'audio/mpeg' }, blob: async () => ({ type: config.provider === 'qwen_local' ? 'audio/wav' : 'audio/mpeg' }) };
        }
    };
    vm.runInNewContext(fs.readFileSync('static/js/voice_settings.js', 'utf8'), context);
    await callbacks[0]();
    await window.SayaVoice.ready;
    return { speech: window.SayaSpeech, voice: window.SayaVoice, pcm, fetched, revoked, audio, blobs, moodReads: () => moodReads, setHoldFetch: value => holdFetch = value, setHoldAudio: value => holdAudio = value, setFailFetch: value => failFetch = value };
}

test('long sentences are complete, bounded, WAV playback retains volume and speed', async () => {
    const h = await harness();
    const text = '一'.repeat(95) + '。第二句话。';
    await h.speech.speak(text);
    assert.equal(h.fetched.map(item => item.body.text).join(''), text);
    assert.ok(h.fetched.every(item => item.body.text.length <= 30));
    assert.equal(h.audio.length, h.fetched.length);
    assert.ok(h.blobs.every(item => item.type === 'audio/wav'));
    assert.ok(h.audio.every(item => item.volume === 0.4 && item.playbackRate === 1.2));
    assert.ok(h.audio.every(item => item.preservesPitch === true));
    assert.equal(h.revoked.length, h.audio.length);
});

test('one read session freezes mood across chunks and refreshes on the next read', async () => {
    const h = await harness({ mood_prosody: true, stream_audio: true });
    await h.speech.speak('一'.repeat(95) + '。');
    assert.equal(h.moodReads(), 1);
    assert.ok(h.fetched.every(item => item.body.mood === 'sad'));
    const count = h.fetched.length;
    await h.speech.speak('第二次朗读。');
    assert.equal(h.moodReads(), 2);
    assert.equal(h.fetched[count].body.mood, 'excited');
});

test('streaming chat freezes mood before the first sentence', async () => {
    const h = await harness({ mood_prosody: true, auto_read: true, stream_audio: true, stream_chat: true });
    const session = await h.voice.beginChat();
    session.push('第一句。第二句。');
    await session.finish();
    assert.equal(h.moodReads(), 1);
    assert.ok(h.fetched.every(item => item.body.mood === 'sad'));
});

test('Assistant reading stays neutral and never reads companion mood', async () => {
    const h = await harness({ mood_prosody: true, auto_read: true, stream_audio: true, stream_chat: true });
    const context = { roleMode: 'assistant' };
    const session = await h.voice.beginChat(context);
    session.push('Assistant 的回答。');
    await session.finish();
    await h.speech.speak('手动朗读。', context);
    await h.voice.autoRead('自动朗读。', context);
    assert.equal(h.moodReads(), 0);
    assert.ok(h.fetched.every(item => item.body.mood === 'neutral'));
});

test('stop aborts an in-flight fetch and cannot start delayed playback', async () => {
    const h = await harness();
    h.setHoldFetch(true);
    const task = h.speech.speak('你好。');
    await new Promise(resolve => setImmediate(resolve));
    h.speech.stop();
    await task;
    assert.equal(h.fetched[0].signal.aborted, true);
    assert.equal(h.audio.length, 0);
});

test('stop during playback resolves the old task and revokes prefetched audio', async () => {
    const h = await harness();
    h.setHoldAudio(true);
    const task = h.speech.speak('一'.repeat(35) + '。');
    await new Promise(resolve => setImmediate(resolve));
    h.speech.stop();
    await task;
    assert.equal(h.audio[0].paused, true);
    assert.equal(h.revoked.length, 2);
});

test('prefetch failures surface once, without silently skipping a sentence', async () => {
    const h = await harness();
    h.setFailFetch(true);
    await assert.rejects(h.speech.speak('一'.repeat(35) + '。'), /GPU unavailable/);
    assert.equal(h.audio.length, 1);
    assert.equal(h.fetched.length, 2);
});

test('disabled speech makes no request; ElevenLabs keeps its existing endpoint', async () => {
    const disabled = await harness({ enabled: false });
    await assert.rejects(disabled.speech.speak('你好。'), /开启语音/);
    assert.equal(disabled.fetched.length, 0);
    const cloud = await harness({ provider: 'elevenlabs' });
    await cloud.speech.speak('Hello.');
    assert.equal(cloud.fetched[0].url, '/tts');
    assert.equal(cloud.fetched[0].body.voice_id, 'cloud-voice');
    assert.equal(cloud.blobs[0].type, 'audio/mpeg');
});

test('streaming reader preserves complete text and uses one continuous player', async () => {
    const h = await harness({ stream_audio: true, stream_buffer_seconds: 0.8 });
    const text = '一'.repeat(95) + '。第二句话。';
    await h.speech.speak(text);
    assert.equal(h.fetched.map(item => item.body.text).join(''), text);
    assert.ok(h.fetched.every(item => item.url === '/api/tts/stream'));
    assert.equal(h.pcm.length, 1);
    assert.equal(h.pcm[0].reads, h.fetched.length);
    assert.equal(h.pcm[0].closed, true);
    assert.equal(h.audio.length, 0);
});

test('chat starts on a complete sentence and does not repeat it at completion', async () => {
    const h = await harness({ auto_read: true, stream_audio: true, stream_chat: true });
    const session = await h.voice.beginChat();
    session.push('第一');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.fetched.length, 0);
    session.push('句话。第二');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.fetched[0].body.text, '第一句话。');
    session.push('句话。尾句');
    await session.finish('第一句话。第二句话。尾句');
    assert.equal(h.fetched.map(item => item.body.text).join(''), '第一句话。第二句话。尾句');
    assert.equal(h.pcm[0].closed, true);
});

test('streaming chat omits fenced code even when delimiters span text events', async () => {
    const h = await harness({ auto_read: true, stream_audio: true, stream_chat: true });
    const session = await h.voice.beginChat();
    session.push('你好。```js\n代码。');
    session.push('\n```结束。');
    await session.finish();
    assert.equal(h.fetched.map(item => item.body.text).join(''), '你好。结束。');
});

test('stopping streaming chat aborts generation and prevents later queued text', async () => {
    const h = await harness({ auto_read: true, stream_audio: true, stream_chat: true });
    h.setHoldFetch(true);
    const session = await h.voice.beginChat();
    session.push('第一句。第二句。');
    await new Promise(resolve => setImmediate(resolve));
    h.speech.stop();
    await assert.rejects(session.done, error => error.name === 'AbortError');
    assert.equal(h.fetched.length, 1);
    assert.equal(h.fetched[0].signal.aborted, true);
    assert.equal(h.pcm[0].closed, true);
});

test('emoji, faces and image labels are removed consistently before all speech requests', async () => {
    const cases = JSON.parse(fs.readFileSync('tests/speech_text_cases.json', 'utf8'));
    for (const value of cases) {
        const h = await harness({stream_audio: true, chunk_chars:300});
        await h.speech.speak(value.raw);
        assert.equal(h.fetched.map(x => x.body.text).join(''), value.clean, value.raw);
        assert.ok(h.fetched.every(x => x.body.settings.language === value.language), value.raw);
        if (!value.clean) assert.equal(h.pcm.length, 0);
    }
});

test('Chinese preference adapts only the current mixed or English segment, in PCM and WAV', async () => {
    for (const stream_audio of [false, true]) {
        const h = await harness({stream_audio, chunk_chars:300});
        await h.speech.speak('请说 hello world，然后继续中文。');
        await h.speech.speak('Hello world.');
        await h.speech.speak('纯中文。');
        assert.equal(h.fetched[0].body.settings.language, 'Auto');
        assert.equal(h.fetched[1].body.settings.language, 'English');
        assert.equal(h.fetched[2].body.settings.language, 'Chinese');
    }
});

test('sentence splitting keeps English words, decimal points and names intact', async () => {
    const h = await harness({stream_audio: true, chunk_chars:30});
    const text = '中'.repeat(26) + ' pronunciation 和 OpenAI。版本 3.14 正常。';
    await h.speech.speak(text);
    const parts = h.fetched.map(x => x.body.text);
    assert.ok(parts.some(x => x.includes('pronunciation')));
    assert.ok(parts.some(x => x.includes('OpenAI')));
    assert.ok(parts.some(x => x.includes('3.14')));
    assert.equal(parts.join('').replace(/\s/g, ''), text.replace(/\s/g, ''));
});

test('streaming text holds unfinished emoji entities, faces, and English word tails', async () => {
    const h = await harness({auto_read:true, stream_audio:true, stream_chat:true, chunk_chars:30});
    const session = await h.voice.beginChat();
    session.push('中'.repeat(25) + ' pronun');
    session.push('ciation 😊。谢谢你 (^');
    session.push('_^)！博士&#x1F6');
    session.push('0A;，hello world。');
    await session.finish();
    const parts = h.fetched.map(x => x.body.text);
    assert.ok(parts.some(x => x.includes('pronunciation')));
    assert.ok(parts.every(x => !/😊|\^|#x|1F60A|_/.test(x)));
    assert.ok(h.fetched.some(x => x.body.settings.language === 'Auto'));
    assert.equal(parts.join('').replace(/\s/g,''), '中'.repeat(25) + 'pronunciation。谢谢你！博士，helloworld。');
});
