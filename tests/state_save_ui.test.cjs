const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8');

function setup(ok) {
  const input = {value: 'Keep my goal'};
  const notices = [];
  let reloads = 0;
  const context = vm.createContext({
    fetch: async () => ({ok, status: ok ? 200 : 507, json: async () => ({detail: 'Disk full'})}),
    document: {getElementById: () => input},
    showToast: message => notices.push(message),
    loadQuests: () => {reloads++;},
  });
  // Execute the actual production functions, without booting the 3D UI.
  const start = source.indexOf('    async function checkedStateFetch(');
  const end = source.indexOf('    async function completeQuest(', start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);
  return {context, input, notices, reloads: () => reloads};
}

test('failed goal save keeps user input and reports failure without reloading', async () => {
  const app = setup(false);
  await app.context.addQuest();
  assert.equal(app.input.value, 'Keep my goal');
  assert.deepEqual(app.notices, ['Disk full']);
  assert.equal(app.reloads(), 0);
});

test('successful goal save clears input and refreshes the saved list', async () => {
  const app = setup(true);
  await app.context.addQuest();
  assert.equal(app.input.value, '');
  assert.equal(app.reloads(), 1);
  assert.deepEqual(app.notices, []);
});

test('checkin mutation rejects a failed response before success actions', async () => {
  const app = setup(false);
  await assert.rejects(app.context.checkedStateFetch('/api/checkins/tasks/update', {}), /Disk full/);
  assert.deepEqual(app.notices, ['Disk full']);
});
