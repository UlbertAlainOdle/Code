const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8');
function snippet(start, end) {
    return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
function element() {
    return { dataset: {}, children: [], appendChild(child) { this.children.push(child); },
        querySelector() { return null; }, querySelectorAll() { return []; } };
}

test('pending candidates restore recovery controls while explicit saved cards render normally', () => {
    const stack = element(), body = element(), container = element();
    container.querySelector = selector => selector === '.chat-memory-confirm-body' ? body : null;
    const host = { querySelector: () => container };
    const row = { querySelector: () => host };
    const recovered = [];
    const context = vm.createContext({
        document: { getElementById: () => stack, querySelector: () => null, createElement: element },
        renderedMemoryCandidateIds: new Set(), cssEscapeValue: String, escapeHtml: String, escapeJsString: String,
        memoryCandidateCategoryOptions: () => '', chatMemoryImportanceRow: () => '', ic: () => '',
        bindChatMemoryImportanceSlider() {}, updateChatMemoryConfirmTitle() {}, renderMemoryConflictCard() {},
        markCandidatePending: (card, id, operation) => recovered.push({ id, operation }),
    });
    vm.runInContext(snippet('function renderAutonomousMemoryCandidates(', 'async function loadPendingMemoryCandidates('), context);
    vm.runInContext(snippet('function renderChatMemoryConfirm(', 'let candidateStatusMapPromise'), context);
    context.renderAutonomousMemoryCandidates([{ id: 'candidate', content: 'body', confirmation_operation: { operation_id: 'op' } }]);
    assert.equal(recovered.length, 1);
    context.renderChatMemoryConfirm(row, { explicit: [{ memory_id: 'saved', content: 'body' }] });
    assert.equal(recovered.length, 1);
    context.renderedMemoryCandidateIds.clear();
    context.renderChatMemoryConfirm(row, { candidates: [{ id: 'candidate', content: 'body',
        confirmation_operation: { operation_id: 'op' } }] });
    assert.equal(recovered.length, 2);
});

for (const committed of [true, false]) {
    test('recovery button uses ' + (committed ? 'file-only recovery' : 'read-only result lookup'), async () => {
        const card = element(), button = element(), input = element(), requests = [], resolved = [];
        card.querySelector = selector => selector === 'button.primary' ? button : null;
        card.querySelectorAll = () => [input, button];
        const context = vm.createContext({
            document: { createElement: element }, URLSearchParams,
            fetch: async (url, options) => {
                requests.push({ url, options });
                return { ok: true, json: async () => committed
                    ? { operation: { committed: true }, candidate_status_pending: false }
                    : { committed: false, items: [{ state: 'unknown' }] } };
            },
            resolveChatMemoryCard: (...args) => resolved.push(args), loadMemoryList() {},
        });
        vm.runInContext(snippet('function markCandidatePending(', 'async function confirmAutonomousMemoryCandidate('), context);
        context.markCandidatePending(card, 'candidate', { operation_id: 'op', committed });
        assert.equal(input.disabled, true);
        await button.onclick();
        assert.equal(requests.length, 1);
        if (committed) {
            assert.equal(requests[0].url, '/api/kb/candidates/reconcile');
            assert.deepEqual(JSON.parse(requests[0].options.body), { operation_id: 'op' });
            assert.equal(resolved.length, 1);
        } else {
            assert.match(requests[0].url, /^\/api\/kb\/write-result\?/);
            assert.equal(requests[0].options, undefined);
            assert.equal(resolved.length, 0);
        }
    });
}
