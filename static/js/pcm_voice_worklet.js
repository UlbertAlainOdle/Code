/* Bounded mono PCM queue. Resample to the AudioContext clock continuously. */
class SayaPCMProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const settings = options.processorOptions || {};
        this.inputRate = settings.inputRate || 24000;
        this.rate = settings.playbackRate || 1;
        this.volume = settings.volume ?? 1;
        this.threshold = Math.round((settings.bufferSeconds || 0.8) * this.inputRate);
        this.ring = new Float32Array(this.inputRate * 30);
        this.read = 0;
        this.write = 0;
        this.phase = 0;
        this.started = false;
        this.ended = false;
        this.starved = false;
        this.underruns = 0;
        this.frames = 0;
        this.port.onmessage = event => {
            if (event.data.playbackRate === 1) { this.rate = 1; return; }
            if (event.data.end) { this.ended = true; return; }
            const samples = event.data.samples;
            if (!samples) return;
            if (this.write - this.read + samples.length > this.ring.length) {
                this.port.postMessage({ error: '语音缓冲区已满。' });
                return;
            }
            for (let i = 0; i < samples.length; i++) this.ring[(this.write + i) % this.ring.length] = samples[i];
            this.write += samples.length;
        };
    }
    process(inputs, outputs) {
        const output = outputs[0][0];
        if (!this.started && (this.write - this.read >= this.threshold || (this.ended && this.write > this.read))) {
            this.started = true;
            this.port.postMessage({ started: true });
        }
        const step = this.inputRate / sampleRate * this.rate;
        for (let i = 0; i < output.length; i++) {
            const available = this.write - this.read;
            if (!this.started || !available) {
                output[i] = 0;
                if (this.started && !this.ended && !this.starved) {
                    this.starved = true;
                    this.underruns++;
                }
                continue;
            }
            this.starved = false;
            const a = this.ring[this.read % this.ring.length];
            const b = available > 1 ? this.ring[(this.read + 1) % this.ring.length] : a;
            output[i] = (a + (b - a) * this.phase) * this.volume;
            this.phase += step;
            const advance = Math.floor(this.phase);
            this.read += Math.min(advance, available);
            this.phase -= advance;
        }
        this.frames += output.length;
        if (this.frames >= 2048) {
            this.frames = 0;
            this.port.postMessage({ queuedSeconds: (this.write - this.read) / this.inputRate, underruns: this.underruns });
        }
        if (this.ended && this.read === this.write) {
            this.port.postMessage({ drained: true, underruns: this.underruns });
            return false;
        }
        return true;
    }
}
registerProcessor('saya-pcm-voice', SayaPCMProcessor);
