// Run with: node --test tests/test_terminal_workspace.cjs
//
// static/terminal_workspace.js is a browser IIFE, so it is loaded into a
// hand-rolled DOM (the same approach as tests/test_app_navigation.cjs) and
// driven through the three behaviours that were broken:
//
//   1. opening a new shell session dropped every live tab from the tab strip
//      until a page refresh brought them back from the server snapshot,
//   2. a plain sentence about the machine went to the tool-less chat model,
//      which answered "I can't access or run a terminal on your device" and
//      ran nothing — it has to reach the agent runtime instead,
//   3. a clipboard paste outside xterm never reached the shell.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');

// Overridable so the same suite can be pointed at another copy of the client
// (e.g. to prove these tests really do fail against the old behaviour).
const SOURCE = process.env.NOVA_TERMINAL_JS || 'static/terminal_workspace.js';

const SESSIONS = [
  { id: 'pty-1', label: 'app', cwd: '/app/build', closed: false, created_at: 1 },
  { id: 'pty-2', label: 'logs', cwd: '/app/build', closed: false, created_at: 2 },
];

function fakeElement(id) {
  const classes = new Set();
  const handlers = new Map();
  const el = {
    id,
    tagName: 'DIV',
    hidden: false,
    disabled: false,
    value: '',
    innerHTML: '',
    textContent: '',
    title: '',
    dataset: {},
    style: { setProperty() {}, removeProperty() {} },
    scrollTop: 0,
    scrollHeight: 0,
    clientWidth: 800,
    clientHeight: 600,
    children: [],
    options: [],
    classList: {
      add: (v) => classes.add(v),
      remove: (v) => classes.delete(v),
      contains: (v) => classes.has(v),
      toggle(v, force) {
        if (force === undefined ? !classes.has(v) : force) classes.add(v);
        else classes.delete(v);
      },
    },
    addEventListener(name, cb) {
      if (!handlers.has(name)) handlers.set(name, []);
      handlers.get(name).push(cb);
    },
    removeEventListener() {},
    setAttribute() {},
    appendChild(node) { el.children.push(node); return node; },
    remove() {},
    focus() {},
    blur() {},
    contains() { return false; },
    closest() { return null; },
    querySelector() { return fakeElement('child'); },
    querySelectorAll() { return []; },
    fire(name, event = {}) { for (const cb of handlers.get(name) || []) cb(event); },
  };
  return el;
}

function setup() {
  const elements = new Map();
  const docListeners = new Map();
  const calls = [];

  const body = fakeElement('body');
  const document = {
    body,
    documentElement: { style: { setProperty() {}, removeProperty() {} } },
    activeElement: null,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, fakeElement(id));
      return elements.get(id);
    },
    createElement: (tag) => fakeElement(tag),
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getSelection() { return null; },
    addEventListener(name, cb) {
      if (!docListeners.has(name)) docListeners.set(name, []);
      docListeners.get(name).push(cb);
    },
    dispatch(name, event) {
      for (const cb of docListeners.get(name) || []) cb(event);
    },
  };

  let sessions = SESSIONS.map((s) => ({ ...s }));
  const answer = (path, init) => {
    const method = (init && init.method) || 'GET';
    const body = init && init.body ? JSON.parse(init.body) : null;
    calls.push({ method, path, body });
    if (path.startsWith('/api/admin/terminal/pty/sessions')) {
      if (method === 'POST') {
        const info = { id: 'pty-3', label: 'build', cwd: '/app/build', closed: false, created_at: 3 };
        sessions = [...sessions, info];
        return { ok: true, status: 200, json: async () => ({ ok: true, session: 'pty-3', info }) };
      }
      return { ok: true, status: 200, json: async () => ({ sessions, active: 'pty-1' }) };
    }
    if (path === '/api/agent/tasks' && method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ id: 'task-1', status: 'queued' }) };
    }
    if (path.startsWith('/api/agent/tasks/')) {
      return {
        ok: true, status: 200,
        json: async () => ({ id: 'task-1', status: 'completed', max_steps: 8, steps: [], summary: 'done' }),
      };
    }
    if (path === '/api/admin/terminal/pty/input') {
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    if (path === '/api/v1/chat/completions') {
      const lines = ui_reply.split('|').map((t) => `data: ${JSON.stringify({
        choices: [{ delta: { content: t } }],
      })}\n\n`).join('') + 'data: [DONE]\n\n';
      let sent = false;
      return {
        ok: true, status: 200,
        body: {
          getReader() {
            return {
              async read() {
                if (sent) return { done: true };
                sent = true;
                return { done: false, value: new TextEncoder().encode(lines) };
              },
            };
          },
        },
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };

  let ui_reply = 'A transformer is a neural network.';
  const NovaUI = { toast() {}, copy() {} };
  const window = {
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    addEventListener() {},
    NovaUI,
  };

  runInNewContext(readFileSync(SOURCE, 'utf8'), {
    document, window, NovaUI, Date, Set, Map, JSON, Math, Object, String, Number, Boolean, Array,
    TextDecoder, TextEncoder, RegExp, Error, Promise, URL,
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {},
    EventSource: function FakeEventSource(url) { this.url = url; this.close = () => {}; },
    fetch: async (path, init) => answer(path, init || {}),
    setTimeout: (fn) => { fn(); return 0; },
    clearTimeout: () => {},
  });

  return {
    elements, calls, document,
    reply(text) { ui_reply = text; },
    tick: () => new Promise((resolve) => setImmediate(resolve)),
  };
}

test('opening a session keeps every existing tab in the strip', async () => {
  const ui = setup();
  await ui.tick();
  const tabs = ui.elements.get('term-tabs').innerHTML;
  assert.match(tabs, /app/);
  assert.match(tabs, /logs/);

  ui.elements.get('term-new').fire('click');
  await ui.tick();
  const after = ui.elements.get('term-tabs').innerHTML;
  for (const label of ['app', 'logs', 'build']) {
    assert.match(after, new RegExp('>' + label + '<'), label + ' must still have a tab');
  }
  // ...and the strip is re-synced from the server instead of guessed locally.
  assert.ok(ui.calls.some((c) => c.method === 'GET' && c.path.startsWith('/api/admin/terminal/pty/sessions')));
});

test('a question about this machine runs in the agent terminal tab', async () => {
  const ui = setup();
  const input = ui.elements.get('term-side-input');
  const goal = 'check config via terminal. ram cpu gpu, storage and tell me the config';
  input.value = goal;
  input.fire('input', { target: input });
  // The mode chip tells the user it is a task, not a chat turn.
  assert.equal(ui.elements.get('term-side-mode').textContent, 'agent');

  ui.elements.get('term-side-send').fire('click');
  for (let i = 0; i < 5; i++) await ui.tick();

  const task = ui.calls.find((c) => c.method === 'POST' && c.path === '/api/agent/tasks');
  assert.ok(task, 'the task must reach the agent runtime');
  assert.equal(task.body.goal, goal);
  assert.equal(ui.calls.some((c) => c.path === '/api/v1/chat/completions'), false,
    'the tool-less chat model must not answer it');
});

test('a real question stays a chat turn', async () => {
  const ui = setup();
  const input = ui.elements.get('term-side-input');
  input.value = 'what is a transformer?';
  input.fire('input', { target: input });
  assert.equal(ui.elements.get('term-side-mode').textContent, 'chat');

  ui.elements.get('term-side-send').fire('click');
  for (let i = 0; i < 5; i++) await ui.tick();

  assert.ok(ui.calls.some((c) => c.path === '/api/v1/chat/completions'));
  assert.equal(ui.calls.some((c) => c.method === 'POST' && c.path === '/api/agent/tasks'), false);
});

test('a chat model that refuses to touch the machine escalates to the agent', async () => {
  const ui = setup();
  ui.reply("I can't access or run a terminal on your device from this chat.");
  const input = ui.elements.get('term-side-input');
  input.value = 'read the disk usage for me';
  ui.elements.get('term-side-send').fire('click');
  for (let i = 0; i < 8; i++) await ui.tick();

  const task = ui.calls.find((c) => c.method === 'POST' && c.path === '/api/agent/tasks');
  assert.ok(task, 'the refusal must be rerun by the agent');
  assert.equal(task.body.goal, 'read the disk usage for me');
});

test('a clipboard paste is typed into the shell', async () => {
  const ui = setup();
  await ui.tick();
  ui.document.dispatch('paste', {
    target: ui.document.body,
    clipboardData: { getData: () => 'df -h\n' },
    preventDefault() {},
  });
  for (let i = 0; i < 5; i++) await ui.tick();

  const typed = ui.calls.find((c) => c.path === '/api/admin/terminal/pty/input');
  assert.ok(typed, 'the paste has to reach the PTY');
  // Multi-line clipboard text becomes CR-separated shell input, and the
  // trailing newline is dropped so pasting never submits by itself.
  assert.equal(typed.body.data, 'df -h');
  assert.equal(typed.body.session, 'pty-1');
});