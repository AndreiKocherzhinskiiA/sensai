// Run with: node --test tests/test_chat_sessions.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/static/index.html'), 'utf8');
const sessionFunctions = html.slice(
  html.indexOf('    function resetView()'),
  html.indexOf('    loadSession(sessionId);'),
);

test('loading a saved chat restores its token estimate and empty chats reset it', () => {
  const usage = [];
  const context = vm.createContext({
    personaSelect: {}, personaDD: {render() {}}, addLine() {},
    setUsage: (last, total) => usage.push([last, total]),
  });
  vm.runInContext(`
    let savedPersonaId, sessionTotalTokens = 0, currentMessages = [];
    ${html.slice(html.indexOf('    function applySessionData('), html.indexOf('    function resetView()'))}
    applySessionData({total_tokens: 42, messages: [{role: 'user', content: 'hello'}]});
  `, context);
  assert.deepEqual(usage, [[0, 42]]);
  assert.equal(vm.runInContext('sessionTotalTokens', context), 42);
  vm.runInContext('applySessionData(null)', context);
  assert.deepEqual(usage, [[0, 42], [0, 0]]);
});

function chat() {
  const requests = [];
  const displayed = [];
  const context = vm.createContext({
    localStorage: {setItem() {}},
    logInner: {replaceChildren() { displayed.length = 0; }},
    shareBox: {classList: {remove() {}}},
    setUsage() {}, showEmpty() {}, refreshSessions() {}, setTemporaryUI() {},
    applySessionData(data) { if (data) displayed.push(data.id); },
    fetch(url) {
      return new Promise(resolve => requests.push({
        url, respond(id) { resolve({ok: true, json: async () => ({id})}); },
      }));
    },
  });
  vm.runInContext(`
    let sessionId = 'initial', currentMessages = [], sessionTotalTokens = 0;
    let pendingFile = null, sessionLoadVersion = 0, temporary = false, lastRealSessionId = 'initial';
    ${sessionFunctions}
  `, context);
  return {requests, displayed, run: code => vm.runInContext(code, context)};
}

test('a slow previous chat cannot append messages to the selected chat', async () => {
  const c = chat();
  const first = c.run('loadSession("A")');
  const second = c.run('loadSession("B")');
  c.requests[1].respond('B');
  await second;
  c.requests[0].respond('A');
  await first;
  assert.deepEqual(c.displayed, ['B']);
  assert.equal(c.run('sessionId'), 'B');
});

test('returning to the same chat still ignores its earlier request', async () => {
  const c = chat();
  const loads = ['A', 'B', 'A'].map(id => c.run(`loadSession('${id}')`));
  c.requests[2].respond('current A');
  await loads[2];
  c.requests[0].respond('old A');
  c.requests[1].respond('B');
  await Promise.all(loads);
  assert.deepEqual(c.displayed, ['current A']);
});

test('switching chats discards the pending file attachment', async () => {
  const c = chat();
  c.run('pendingFile = "private-notes.md"');
  const load = c.run('loadSession("B")');
  assert.equal(c.run('pendingFile'), null);
  c.requests[0].respond('B');
  await load;
});

test('clearing the view discards attachments and invalidates pending loads', async () => {
  const c = chat();
  const load = c.run('loadSession("A")');
  c.run('pendingFile = "private-notes.md"; resetView()');
  c.requests[0].respond('old A');
  await load;
  assert.equal(c.run('pendingFile'), null);
  assert.deepEqual(c.displayed, []);
});
