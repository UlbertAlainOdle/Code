const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/app.js', 'utf8');
const start = source.indexOf('    let dreamPendingRequest = null;');
const end = source.indexOf('    window.runDreamNow = runDreamNow;', start);
function lab(fetch) {
    const notices = [];
    const context = vm.createContext({fetch, showToast: text => notices.push(text), loadCompanionPane: () => {}, encodeURIComponent});
    vm.runInContext(source.slice(start, end), context);
    return {context, notices, run: () => vm.runInContext('runDreamNow()', context)};
}
const reply = data => ({ok: true, json: async () => data});
test('lost response keeps original server date and next action only queries', async () => {
    const calls = [];
    const fixture = lab(async (url, options) => {
        calls.push([url, options]);
        if (url.endsWith('/status')) return reply({target_date: '2026-11-05'});
        if (calls.length === 2) throw new Error('lost response');
        return reply({date: '2026-11-05', operations: {dream_theme: {items: [{state: 'unknown'}]}}});
    });
    await fixture.run(); await fixture.run(); await fixture.run();
    assert.equal(JSON.parse(calls[1][1].body).date, '2026-11-05');
    assert.equal(calls.filter(([url]) => url.endsWith('/run')).length, 1);
    assert.ok(calls.slice(2).every(([url]) => url.endsWith('/result?date=2026-11-05')));
    assert.ok(fixture.notices.at(-1).includes('未知'));
});
test('known staged diary permits only same-date repair and reports pending', async () => {
    const calls = [];
    const fixture = lab(async (url, options) => {
        calls.push([url, options]);
        if (url.endsWith('/status')) return reply({target_date: '2026-11-05'});
        return reply({report: {operations: {dream_theme: {items: [{state: 'committed'}]}, dream_diary: {items: [{state: 'committed', file_pending: true}]}}}});
    });
    await fixture.run(); await fixture.run();
    assert.equal(calls.length, 3);
    assert.equal(calls[1][1].body, calls[2][1].body);
    assert.ok(fixture.notices.at(-1).includes('文件待恢复'));
});
test('double click does not issue another request while model response waits', async () => {
    let finish; let calls = 0;
    const fixture = lab(async url => {
        calls++;
        if (url.endsWith('/status')) return reply({target_date: '2026-11-05'});
        return await new Promise(resolve => {finish = () => resolve(reply({report: {reason: 'empty_transcript'}}));});
    });
    const first = fixture.run();
    while (!finish) await new Promise(resolve => setImmediate(resolve));
    await fixture.run(); assert.equal(calls, 2); finish(); await first;
});
