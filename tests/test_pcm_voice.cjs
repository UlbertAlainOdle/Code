const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function processor(options = {}) {
    let Processor;
    const events = [];
    vm.runInNewContext(fs.readFileSync('static/js/pcm_voice_worklet.js', 'utf8'), {
        AudioWorkletProcessor: class { constructor() { this.port = { postMessage: data => events.push(data) }; } },
        sampleRate: 48000, Float32Array, Math,
        registerProcessor: (_, Type) => { Processor = Type; }
    });
    const instance = new Processor({ processorOptions: { bufferSeconds: 0.1, ...options } });
    return { instance, events, push: samples => instance.port.onmessage({ data: { samples } }), end: () => instance.port.onmessage({ data: { end: true } }), block() { const output = new Float32Array(128); const live = instance.process([], [[output]]); return { live, output }; } };
}

test('24 kHz PCM resamples to 48 kHz, preserves volume, and drains exactly', () => {
    const p = processor({ volume: 0.4 });
    p.push(new Float32Array(24000).fill(0.5)); p.end();
    let samples = 0;
    while (true) {
        const { live, output } = p.block();
        assert.ok(output.every(value => Math.abs(value - 0.2) < 1e-6));
        samples += output.length;
        if (!live) break;
    }
    assert.equal(samples, 48000);
    assert.equal(p.events.filter(event => event.started).length, 1);
    assert.equal(p.events.at(-1).underruns, 0);
});

test('90 seconds of progressively generated audio plays without buffer underruns', () => {
    const p = processor({ bufferSeconds: 0.8 });
    let produced = 0;
    for (let block = 0; block < 48000 * 95 / 128; block++) {
        // 0.25 seconds of audio generated every 0.125 seconds, bounded prefetch.
        if (block % 47 === 0 && produced < 90 * 24000 && p.instance.write - p.instance.read < 10 * 24000) {
            p.push(new Float32Array(6000).fill(0.2));
            produced += 6000;
            if (produced === 90 * 24000) p.end();
        }
        if (!p.block().live) break;
    }
    assert.equal(produced, 90 * 24000);
    assert.equal(p.events.at(-1).drained, true);
    assert.equal(p.events.at(-1).underruns, 0);
});

test('slow generation produces measurable underruns; overflow is rejected', () => {
    const p = processor();
    p.push(new Float32Array(2400).fill(0.2));
    for (let i = 0; i < 100; i++) p.block();
    assert.equal(p.instance.underruns, 1);
    p.push(new Float32Array(31 * 24000));
    assert.match(p.events.at(-1).error, /缓冲区/);
});

test('short final audio starts even below the requested startup buffer', () => {
    const p = processor({ bufferSeconds: 5 });
    p.push(new Float32Array(100));
    assert.equal(p.block().live, true);
    assert.equal(p.instance.started, false);
    p.end();
    for (let i = 0; i < 3; i++) if (!p.block().live) break;
    assert.equal(p.events.at(-1).drained, true);
});

test('speed baked by the server is not applied a second time', () => {
    const p = processor({ playbackRate: 1.2 });
    p.instance.port.onmessage({ data: { playbackRate: 1 } });
    p.push(new Float32Array(24000).fill(0.2)); p.end();
    let samples = 0;
    while (true) { const { live, output } = p.block(); samples += output.length; if (!live) break; }
    assert.equal(samples, 48000);
});
