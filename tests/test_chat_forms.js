// Run with: node --test tests/test_chat_forms.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/static/index.html'), 'utf8');
const start = html.indexOf("      currentSource.addEventListener('form_request'");
assert.notEqual(start, -1);
const handler = html.slice(start, html.indexOf("      currentSource.addEventListener('text_delta'", start));

function form() {
  const nodes = [], requests = [], context = {
    runId: 'run-1', currentRunId: 'run-1', replyNode: {},
    currentSource: {addEventListener(name, callback) { this[name] = callback; }},
    logInner: {insertBefore() {}}, setStatus() {}, scrollDown() {},
    el(tag, className, textContent) {
      const node = {
        tag, textContent, children: [], listeners: {}, style: {}, value: '',
        append(...children) { this.children.push(...children); },
        appendChild(child) { this.children.push(child); },
        setAttribute() {},
        addEventListener(name, callback) { this.listeners[name] = callback; },
      };
      nodes.push(node);
      return node;
    },
    async fetch(url, options) {
      requests.push({url, body: JSON.parse(options.body)});
      return context.response;
    },
    response: {ok: true, async json() { return {status: 'resumed'}; }},
  };
  vm.createContext(context);
  vm.runInContext(handler, context);
  context.currentSource.form_request({data: JSON.stringify({payload: {schema: {
    title: 'Clarification Request', fields: [
      {name: 'language', type: 'text'},
      {name: 'include_tests', type: 'boolean'},
      {name: 'max_steps', type: 'number'},
    ],
  }}})});
  nodes.find(node => node.name === 'language').value = 'Python';
  nodes.find(node => node.name === 'include_tests').value = 'false';
  nodes.find(node => node.name === 'max_steps').value = '3';
  return {
    context, requests,
    fields: nodes.find(node => node.tag === 'fieldset'),
    feedback: nodes.find(node => node.tag === 'p'),
    submit: () => nodes.find(node => node.tag === 'form').listeners.submit({preventDefault() {}}),
  };
}

test('clarification controls submit answers to the waiting run only once', async () => {
  const ui = form();
  await ui.submit();
  assert.deepEqual(ui.requests, [{url: '/api/runs/run-1/form_response', body: {answers: {
    language: 'Python', include_tests: 'false', max_steps: '3',
  }}}]);
  assert.equal(ui.fields.disabled, true);
  assert.equal(ui.feedback.textContent, 'Answers received.');
  await ui.submit();
  assert.equal(ui.requests.length, 1);
});

test('rejected answers leave the form editable for a retry', async () => {
  const ui = form();
  ui.context.response = {ok: false, async json() { return {detail: 'Invalid number'}; }};
  await ui.submit();
  assert.equal(ui.feedback.textContent, 'Invalid number');
  assert.equal(ui.fields.disabled, false);
  ui.context.response = {ok: true, async json() { return {}; }};
  await ui.submit();
  assert.equal(ui.feedback.textContent, 'Answers received.');
});

test('a form from a cancelled or completed run cannot submit', async () => {
  const ui = form();
  ui.context.currentRunId = null;
  await ui.submit();
  assert.equal(ui.requests.length, 0);
});
