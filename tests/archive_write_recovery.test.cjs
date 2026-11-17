const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/app.js'), 'utf8');
const start = source.indexOf('const archiveSelectionState =');
const end = source.indexOf('function triggerUpload(', start);
function element() {
    return {children: [], style: {}, textContent: '', appendChild(child) {this.children.push(child);}};
}
function lab() {
    const nodes = new Map();
    const requests = [];
    const context = vm.createContext({Set, URLSearchParams, crypto: {randomUUID: () => 'a'.repeat(32)},
        document: {getElementById: id => {if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id);},
            querySelectorAll: () => [], createElement: element},
        fetch: async (url, options) => {
            requests.push({url, options});
            if (url.includes('archive-preview')) return {ok: true, json: async () => ({session_id: 'session_test.jsonl',
                snapshot: 'b'.repeat(64), exchanges: [{id: 'qa-0-1', question: 'question', answer: 'answer'}]})};
            if (url.includes('write-result')) return {ok: true, json: async () => ({committed: true, items: [{memory_id: 'stored'}]})};
            return {ok: true, json: async () => ({status: 'unknown', message: '待核对', operation: {operation_id: 'c'.repeat(32)}})};
        }, renderSurfaceState() {}, saveCurrentApiSettings() {}, collectArchiveGenerationSettings: () => ({}),
        escapeHtml: String, escapeJsString: String, showToast() {},
    });
    vm.runInContext(source.slice(start, end), context);
    return {context, nodes, requests};
}
test('archive submits preview version and stable operation, then queries unknown result without another save', async () => {
    const {context, nodes, requests} = lab();
    await context.openArchiveSelection('session_test.jsonl');
    await context.submitArchiveSelection();
    const body = JSON.parse(requests[1].options.body);
    assert.equal(body.expected_snapshot, 'b'.repeat(64));
    assert.equal(body.operation_id, 'a'.repeat(32));
    assert.deepEqual(body.selected_exchange_ids, ['qa-0-1']);
    assert.equal(nodes.get('archive-selection-submit').disabled, true);
    const button = nodes.get('archive-selection-status').children.at(-1);
    await button.onclick();
    assert.match(requests[2].url, /^\/api\/kb\/write-result\?/);
    assert.equal(requests[2].options, undefined);
    assert.match(nodes.get('archive-selection-status').textContent, /stored/);
});
test('double click cannot start a second archive while the first request is pending', async () => {
    const {context, requests} = lab();
    await context.openArchiveSelection('session_test.jsonl');
    let release;
    context.fetch = async (url, options) => {requests.push({url, options}); return new Promise(resolve => {release = () => resolve({
        ok: true, json: async () => ({status: 'success', selected_exchange_count: 1})});});};
    const first = context.submitArchiveSelection();
    await context.submitArchiveSelection();
    assert.equal(requests.length, 2);
    release();
    await first;
});
