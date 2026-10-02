// Run with: node --test tests/test_terminal_console.cjs
//
// terminal/web/console.js is the client for a terminal hosted with nothing
// else. It is a browser IIFE, so it is loaded into a hand-rolled DOM (the same
// approach as tests/test_terminal_workspace.cjs) and driven through the
// behaviours that break first when a terminal is deployed on its own:
//
//   1. the host answering `/` with a bare 404 — the page has to exist at the
//      root, or a deployed terminal is unusable in a browser,
//   2. opening a session dropping every live tab, and the stream cursor
//      restarting so the same output is replayed forever,
//   3. a terminal that renders nothing when xterm.js cannot be fetched,
//   4. the shared secret not being attached to the API calls,
//   5. the agent's commands being invisible because the tab strip never
//      refreshes after a task.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');

const SOURCE = process.env.NOVA_TERMINAL_CONSOLE_JS || 'terminal/web/console.js';

function fakeElement(id) {
  const handlers = new Map();
  const classes = new Set();
  let text = '';
  const el = {
    id,
    tagName: 'DIV',
    hidden: false,
    disabled: false,
    value: '',
    title: '',
    dataset: {},
    style: { setProperty() {}, removeProperty() {} },
    scrollTop: 0,
    scrollHeight: 0,
    clientWidth: 800,
    clientHeight: 600,
    children: [],
    childNodes: [],
    options: [],
    className: '',
    setAttribute() {},
    getAttribute() { return null; },
    removeAttribute() {},
    appendChild(child) {
      this.children.push(child);
      this.childNodes.push(child);
      child.parentNode = this;
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((c) => c !== child);
      return child;
    },
    remove() {
      const parent = el.parentNode;
      if (parent && parent.removeChild) parent.removeChild(el);
      else if (Array.isArray(el._parent)) el._parent.children = el._parent.children.filter((c) => c !== el);
    },
    // Setting textContent empties the element, like a browser — the client
    // clears the tab strip this way before rendering it again.
    get textContent() { return text; },
    set textContent(value) {
      text = value == null ? '' : String(value);
      if (text === '') { this.children = []; this.childNodes = []; }
    },
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
    dispatch(name, event) { (handlers.get(name) || []).forEach((cb) => cb(event || {})); },
    focus() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  return el;
}

function makeEnvironment({ sessions = [], withTerminal = true } = {}) {
  const elements = {};
  const ids = ['log', 'screen', 'tabs', 'where', 'count', 'new', 'clear', 'dot', 'adot',
    'provider', 'manage', 'providerBox', 'provList', 'registry', 'provForm',
    'p_id', 'p_url', 'p_model', 'p_key', 'composer', 'input'];
  ids.forEach((id) => { elements[id] = fakeElement(id); });

  const calls = [];
  const streams = [];
  const writes = [];

  const xterm = {
    written: writes,
    loadAddon() {},
    write(data) { writes.push(data); },
    resize(cols, rows) { xterm.size = `${cols}x${rows}`; },
    clear() { writes.length = 0; },
    focus() {},
    onData(cb) { xterm._onData = cb; },
    open(host) { xterm.host = host; },
    size: '',
  };

  class FakeEventSource {
    constructor(url) { this.url = url; this.onmessage = null; this.onerror = null; streams.push(this); }
    close() { this.closed = true; }
  }

  const fetchCalls = [];
  function fetchImpl(url, options = {}) {
    fetchCalls.push({ url, options, headers: (options && options.headers) || {} });
    const respond = (status, body) => Promise.resolve({
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (url.endsWith('/terminal/pty/sessions') && (options.method || 'GET') === 'GET') {
      return respond(200, { active: (sessions[0] || {}).id, max_sessions: 8, sessions });
    }
    return respond(200, { ok: true, session: 'pty-new' });
  }

  const window = {
    document: {
      getElementById: (id) => elements[id] || null,
      createElement: (tag) => fakeElement(tag),
      createTextNode: (text) => ({ textContent: String(text) }),
      addEventListener() {},
    },
    localStorage: {
      _v: '',
      getItem() { return this._v; },
      setItem(_k, v) { this._v = v; },
    },
    EventSource: FakeEventSource,
    fetch: fetchImpl,
    setInterval() {},
    setTimeout,
    clearTimeout,
    prompt: () => 'typed-secret',
  };
  if (withTerminal) {
    window.Terminal = function () { return xterm; };
    window.FitAddon = { FitAddon: function () { return { fit() {} }; } };
  }

  // The client uses bare `fetch` / `localStorage` / `prompt` (browser globals),
  // so the sandbox needs them at the top level as well as on `window`.
  const sandbox = { window, document: window.document, console, setTimeout, clearTimeout,
                    setInterval() {}, JSON, Math, Date, Object, Array, String, Number, Promise,
                    fetch: fetchImpl, localStorage: window.localStorage,
                    prompt: window.prompt, EventSource: FakeEventSource };
  sandbox.globalThis = sandbox;
  sandbox.window.window = sandbox.window;
  runInNewContext(readFileSync(SOURCE, 'utf8'), sandbox);

  const client = sandbox.window.NovaTerminalConsole || sandbox.NovaTerminalConsole;
  // The page wires the screen at boot; do the same so writes land somewhere.
  client.buildTerminal();
  return { client, elements, calls, streams, writes, xterm, fetchCalls };
}

test('the console client boots and keeps every live tab when one opens', async () => {
  const env = makeEnvironment({
    sessions: [{ id: 'pty-1', label: 'app', cwd: '/app/build', closed: false }],
  });
  await env.client.loadSessions();
  assert.equal(env.elements.tabs.children.length, 1, 'the first tab renders');

  await env.client.newSession();
  await env.client.loadSessions();

  // Regression guard: the tab strip used to drop the tab that was already
  // there, which made a multi-tab terminal look broken after every new tab.
  const labels = env.elements.tabs.children.map((t) => t.children[0].textContent);
  assert.ok(labels.includes('app'), `existing tab survives: ${labels}`);
});

test('the stream cursor advances so output is never replayed', async () => {
  const env = makeEnvironment({ sessions: [{ id: 'pty-1', label: 'app', cwd: '/app', closed: false }] });
  await env.client.loadSessions();
  assert.ok(env.streams.length >= 1, 'a stream was opened');

  const stream = env.streams[env.streams.length - 1];
  stream.onmessage({ data: JSON.stringify({ session: 'pty-1', o: 'hello world', cwd: '/app/build' }) });
  assert.equal(env.client.state.offset, 'hello world'.length);
  assert.deepEqual(env.writes, ['hello world']);
  assert.equal(env.elements.where.textContent, '/app/build', 'cwd updates live');

  stream.onmessage({ data: JSON.stringify({ o: '!' }) });
  assert.equal(env.client.state.offset, 'hello world!'.length);
});

test('keystrokes and resizes are posted for the focused session', async () => {
  const env = makeEnvironment({ sessions: [{ id: 'pty-9', label: 'logs', cwd: '/app', closed: false }] });
  await env.client.loadSessions();
  env.client.send('ls -la\r');

  const input = env.fetchCalls.find((c) => c.url.endsWith('/terminal/pty/input'));
  assert.ok(input, 'input was posted');
  assert.deepEqual(JSON.parse(input.options.body), { session: 'pty-9', data: 'ls -la\r' });

  env.client.state.cols = 100;
  env.client.state.rows = 40;
  env.client.postResize();
  await new Promise((resolve) => setTimeout(resolve, 250));   // it is throttled
  const resize = env.fetchCalls.find((c) => c.url.endsWith('/terminal/pty/resize'));
  assert.ok(resize, 'resize was posted');
  assert.deepEqual(JSON.parse(resize.options.body), { session: 'pty-9', cols: 100, rows: 40 });
});

test('the plain viewer still takes input when xterm.js is unavailable', async () => {
  const env = makeEnvironment({
    withTerminal: false,
    sessions: [{ id: 'pty-1', label: 'app', cwd: '/app', closed: false }],
  });
  await env.client.loadSessions();
  assert.ok(env.client.screen.plain, 'fell back to the append-only viewer');
  assert.equal(env.client.screen.term, null);
  // Enter / ctrl-c / arrows reach the shell as real bytes.
  env.elements.screen.dispatch('keydown', { key: 'Enter', preventDefault() {} });
  env.elements.screen.dispatch('keydown', { key: 'c', ctrlKey: true, preventDefault() {} });
  env.elements.screen.dispatch('keydown', { key: 'ArrowUp', preventDefault() {} });
  const typed = env.fetchCalls.filter((c) => c.url.endsWith('/terminal/pty/input'))
    .map((c) => JSON.parse(c.options.body).data);
  assert.deepEqual(typed, ['\r', '\x03', '\x1b[A']);
});

test('the shared secret rides along on every API call', async () => {
  const env = makeEnvironment({ sessions: [{ id: 'pty-1', label: 'app', cwd: '/app', closed: false }] });
  env.client.state.token = 'typed-secret';
  await env.client.loadSessions();
  const sessionCall = env.fetchCalls[env.fetchCalls.length - 1];
  assert.equal(sessionCall.headers['X-Nova-Terminal-Token'], 'typed-secret');
});

test('a dead session reports instead of leaving a blank screen', async () => {
  const env = makeEnvironment({ sessions: [{ id: 'pty-1', label: 'app', cwd: '/app', closed: false }] });
  await env.client.loadSessions();
  const stream = env.streams[env.streams.length - 1];
  stream.onmessage({ data: JSON.stringify({ error: 'that session is no longer running', code: 'session_gone' }) });
  const last = env.elements.log.children.at(-1);
  assert.match(last.children.at(-1).textContent, /no longer running/);
});

test('an agent task refreshes the tab strip so its shell is visible', async () => {
  const env = makeEnvironment({ sessions: [{ id: 'pty-1', label: 'app', cwd: '/app', closed: false }] });
  await env.client.loadSessions();
  const before = env.fetchCalls.filter((c) => c.url.endsWith('/terminal/pty/sessions')).length;
  await env.client.ask('list the files');
  const after = env.fetchCalls.filter((c) => c.url.endsWith('/terminal/pty/sessions')).length;
  assert.ok(after > before, 'sessions were re-read after the task');
});
