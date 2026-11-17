/* Streaming s16le reader; AudioWorklet owns the playback clock and resampling. */
(() => {
    class PCMVoicePlayer {
        static async create(settings, signal) {
            const context = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'interactive' });
            const player = new PCMVoicePlayer(context, signal);
            try {
                await context.resume();
                await context.audioWorklet.addModule('/static/js/pcm_voice_worklet.js?v=20261010-1');
                if (signal.aborted) throw new DOMException('Stopped', 'AbortError');
                player.node = new AudioWorkletNode(context, 'saya-pcm-voice', { numberOfInputs: 0, outputChannelCount: [1], processorOptions: { inputRate: 24000, playbackRate: settings.playback_rate, volume: settings.volume, bufferSeconds: settings.stream_buffer_seconds || 0.8 } });
                player.node.port.onmessage = event => {
                    const data = event.data;
                    if (data.started) player.metrics.firstPlaybackSeconds = (performance.now() - player.startedAt) / 1000;
                    if (data.queuedSeconds !== undefined) player.queuedSeconds = data.queuedSeconds;
                    if (data.underruns !== undefined) player.metrics.underruns = data.underruns;
                    if (data.error) { player.failure = new Error(data.error); player.reject(player.failure); }
                    if (data.drained) player.resolve(player.metrics);
                };
                player.node.connect(context.destination);
                return player;
            } catch (error) { await player.close(); throw error; }
        }
        constructor(context, signal) {
            this.context = context;
            this.signal = signal;
            this.queuedSeconds = 0;
            this.startedAt = performance.now();
            this.metrics = { underruns: 0, audioSeconds: 0, firstPlaybackSeconds: null };
            this.done = new Promise((resolve, reject) => { this.resolve = resolve; this.reject = reject; });
            this.done.catch(() => {});
            this.abort = () => { this.resolve(this.metrics); void this.close(); };
            signal.addEventListener('abort', this.abort, { once: true });
        }
        async read(response) {
            if (Number(response.headers.get('X-Audio-Sample-Rate')) !== 24000 || response.headers.get('X-Audio-Format') !== 's16le') throw new Error('流式音频格式不匹配。');
            if (!response.body) throw new Error('浏览器没有提供音频流。');
            // New servers bake speed with a pitch-preserving filter. Keep the
            // old worklet speed only when talking to an older server.
            if (response.headers.get('X-Audio-Playback-Rate') === '1') this.node.port.postMessage({ playbackRate: 1 });
            const reader = response.body.getReader();
            let carry = null;
            let received = 0;
            try {
                while (!this.signal.aborted) {
                    if (this.failure) throw this.failure;
                    while (this.queuedSeconds > 12 && !this.signal.aborted) await new Promise(resolve => setTimeout(resolve, 30));
                    if (this.signal.aborted) break;
                    const { value, done } = await reader.read();
                    if (done) break;
                    let bytes = value;
                    if (carry !== null) { bytes = new Uint8Array(value.length + 1); bytes[0] = carry; bytes.set(value, 1); }
                    carry = bytes.length % 2 ? bytes[bytes.length - 1] : null;
                    const length = Math.floor(bytes.length / 2);
                    const view = new DataView(bytes.buffer, bytes.byteOffset, length * 2);
                    const samples = new Float32Array(length);
                    for (let i = 0; i < length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
                    received += length;
                    this.metrics.audioSeconds += length / 24000;
                    this.queuedSeconds += length / 24000;
                    this.node.port.postMessage({ samples }, [samples.buffer]);
                }
                if (!this.signal.aborted && (carry !== null || !received)) throw new Error('流式音频不完整或为空。');
            } finally {
                await reader.cancel().catch(() => {});
                reader.releaseLock();
            }
        }
        async finish() {
            if (this.signal.aborted) return this.metrics;
            this.node.port.postMessage({ end: true });
            const metrics = await this.done;
            await this.close();
            return metrics;
        }
        async close() {
            this.signal.removeEventListener('abort', this.abort);
            this.node?.disconnect();
            if (this.context.state !== 'closed') await this.context.close().catch(() => {});
        }
    }
    window.SayaPCMPlayer = PCMVoicePlayer;
})();
