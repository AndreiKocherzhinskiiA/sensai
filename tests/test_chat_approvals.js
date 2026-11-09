// Run with: node --test tests/test_chat_approvals.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/static/index.html'), 'utf8');
const handlers = [...html.matchAll(/      currentSource\.addEventListener\('approval_request'/g)]
  .map(match => html.slice(match.index, html.indexOf('      currentSource.addEventListener(', match.index + 1)))
  .join('\n');
assert.ok(handlers);
const fileFunctions = html.slice(html.indexOf('    let pendingApproval = null;'), html.indexOf('    function finish('));

function chat(payload) {
  const nodes = [], requests = [], callbacks = [];
  function el(tag, className, textContent) {
    const node = {
      tag, className, textContent, children: [], listeners: {},
      classList: {add() {}, remove() {}},
      append(...children) { this.children.push(...children); },
      appendChild(child) { this.children.push(child); }, setAttribute() {},
      addEventListener(name, callback) { this.listeners[name] = callback; },
    };
    nodes.push(node); return node;
  }
  const context = vm.createContext({
    runId: 'run-1', currentRunId: 'run-1', el,
    currentSource: {addEventListener(name, callback) { callbacks.push(callback); }},
    document: {getElementById() { return null; }},
    logInner: {appendChild() {}}, setStatus() {}, scrollDown() {},
    toolCard(title) { return el('details', 'mcp-action', title); },
    async fetch(url, options) {
      requests.push({url, body: options.body ? JSON.parse(options.body) : null});
      return {ok: true, json: async () => ({})};
    },
  });
  vm.runInContext(fileFunctions + '\n' + handlers, context);
  callbacks.forEach(callback => callback({data: JSON.stringify({run_id: 'run-1', payload})}));
  const buttons = name => nodes.filter(node => node.tag === 'button' && node.textContent === name);
  return {nodes, requests, buttons, context};
}

for (const approved of [true, false]) {
  test(`MCP ${approved ? 'approval' : 'rejection'} displays one decision and uses its per-call token`, async () => {
    const ui = chat({kind: 'mcp', tool: 'search__search_web', arguments: {query: 'Python sqlite3'},
      approval_id: 'call-token', timeout_seconds: 120});
    assert.equal(ui.buttons('Approve').length, 1);
    assert.equal(ui.buttons('Reject').length, 1);
    await ui.buttons(approved ? 'Approve' : 'Reject')[0].listeners.click();
    assert.deepEqual(ui.requests, [{url: '/api/runs/run-1/mcp/decision', body: {approval_id: 'call-token', approved}}]);
    assert.ok(ui.buttons('Approve')[0].disabled);
    assert.ok(ui.buttons('Reject')[0].disabled);
  });

  test(`file write ${approved ? 'approval' : 'rejection'} still reaches its own endpoint`, async () => {
    const ui = chat({action: 'write', path: 'report.md', size_bytes: 10, plan_id: 'plan-1'});
    assert.equal(ui.buttons('Approve').length, 1);
    assert.equal(ui.buttons('Reject').length, 1);
    await ui.buttons(approved ? 'Approve' : 'Reject')[0].listeners.click();
    assert.deepEqual(ui.requests, [{url: `/api/runs/run-1/${approved ? 'approve' : 'reject'}`, body: null}]);
  });
}

test('an unknown approval event cannot become a file-write decision', () => {
  assert.equal(chat({kind: 'unknown', action: 'write'}).buttons('Approve').length, 0);
});

test('MCP approval from a finished run cannot be submitted', async () => {
  const ui = chat({kind: 'mcp', tool: 'search__search_web', arguments: {}, approval_id: 'old', timeout_seconds: 120});
  ui.context.currentRunId = null;
  await ui.buttons('Approve')[0].listeners.click();
  assert.equal(ui.requests.length, 0);
});
