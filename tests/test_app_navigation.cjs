// Run with: node --test tests/test_app_navigation.cjs
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');

function setup() {
  const listeners = new Map();
  const classes = () => {
    const values = new Set();
    return {
      add: (value) => values.add(value),
      remove: (value) => values.delete(value),
      contains: (value) => values.has(value),
      toggle(value, force) {
        if (force === undefined ? !values.has(value) : force) values.add(value);
        else values.delete(value);
      },
    };
  };
  const element = (id) => ({
    id, hidden: false, classList: classes(), attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener() {},
  });
  const target = element('tab-content');
  const loading = element('tab-loading');
  const skeleton = element('skeleton');
  const error = element('error');
  loading.querySelector = (selector) => selector === '[data-tab-error]' ? error : skeleton;
  const retry = element('tab-retry');
  retry.addEventListener = (_, callback) => { retry.click = callback; };
  const navButtons = Object.fromEntries(['overview', 'models', 'providers'].map((tab) => [tab, {
    ...element(tab), dataset: { tab, label: tab, subtitle: '' },
  }]));
  const ids = { 'tab-content': target, 'tab-loading': loading, 'tab-retry': retry,
    'refresh-btn': element('refresh-btn'), 'tab-title': element('tab-title'),
    'tab-subtitle': element('tab-subtitle') };
  const document = {
    body: element('body'), hidden: false,
    getElementById: (id) => ids[id] || null,
    querySelector(selector) {
      const match = selector.match(/^#sidebar \.nav-item\[data-tab="(.*)"\]$/);
      return match ? navButtons[match[1]] || null : null;
    },
    querySelectorAll: (selector) => selector === '#sidebar .nav-item' ? Object.values(navButtons) : [],
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    dispatch(name, detail, eventTarget = target) {
      const event = { target: eventTarget, detail };
      for (const callback of listeners.get(name) || []) callback(event);
      return event;
    },
  };
  const requests = [];
  const triggers = [];
  const htmx = {
    ajax(method, path, options) {
      const xhr = { readyState: 1, abort() {
        this.readyState = 4;
        document.dispatch('htmx:afterRequest', { xhr: this, successful: false });
      } };
      requests.push({ method, path, options, xhr });
      document.dispatch('htmx:beforeRequest', { xhr, target });
    },
    trigger(_element, name) { triggers.push(name); },
  };
  const window = { htmx, scrollTo() {} };
  const fetches = [];
  runInNewContext(readFileSync('static/app.js', 'utf8'), {
    document, window, Date, Set, Map, WeakMap,
    localStorage: { getItem: () => null },
    fetch(path) { fetches.push(path); return Promise.resolve({ ok: false }); },
    setInterval() {},
  });
  return { document, window, requests, triggers, fetches, target, loading, error, retry, navButtons };
}

test('navigation uses fresh HTMX requests and hides the old tab without a prefetch', () => {
  const ui = setup();
  ui.window.NovaUI.nav('models');
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].path, '/partials/tab/models');
  assert.equal(ui.requests[0].options.source, ui.navButtons.models);
  assert.equal(ui.target.hidden, true);
  assert.equal(ui.loading.hidden, false);
  ui.window.NovaUI.nav('models');
  assert.equal(ui.requests.length, 1, 'active tab clicks do not duplicate a load');
  assert.deepEqual(ui.fetches, ['/api/admin/meta']);
  ui.document.dispatch('htmx:afterSwap', {}, ui.target);
  assert.equal(ui.target.hidden, false);
  assert.equal(ui.loading.hidden, true);
});

test('out-of-order and initial overview responses cannot swap over a newer tab', () => {
  const ui = setup();
  const initial = { readyState: 1, abort() { this.readyState = 4; } };
  ui.document.dispatch('htmx:beforeRequest', { xhr: initial, target: ui.target });
  ui.window.NovaUI.nav('models');
  assert.equal(initial.readyState, 4);
  const first = ui.requests[0];
  ui.window.NovaUI.nav('providers');
  assert.equal(first.xhr.readyState, 4);
  assert.equal(ui.document.dispatch('htmx:beforeSwap', { xhr: initial, target: ui.target, shouldSwap: true }).detail.shouldSwap, false);
  assert.equal(ui.document.dispatch('htmx:beforeSwap', { xhr: first.xhr, target: ui.target, shouldSwap: true }).detail.shouldSwap, false);
  assert.equal(ui.document.dispatch('htmx:beforeSwap', { xhr: ui.requests[1].xhr, target: ui.target, shouldSwap: true }).detail.shouldSwap, true);
  assert.equal(ui.target.hidden, true, 'old requests cannot reveal hidden content');
});

test('failed navigation shows retry instead of exposing the previous tab', () => {
  const ui = setup();
  ui.window.NovaUI.nav('models');
  const first = ui.requests[0];
  ui.document.dispatch('htmx:afterRequest', { xhr: first.xhr, successful: false });
  assert.equal(ui.target.hidden, true);
  assert.equal(ui.loading.hidden, false);
  assert.equal(ui.error.classList.contains('hidden'), false);
  ui.retry.click();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].path, '/partials/tab/models');
  assert.equal(ui.error.classList.contains('hidden'), true);
});

test('manual refresh is deferred during navigation and restored after the swap', () => {
  const ui = setup();
  ui.window.NovaUI.nav('models');
  ui.window.NovaUI.refresh();
  assert.deepEqual(ui.triggers, []);
  ui.document.dispatch('htmx:afterSwap', {}, ui.target);
  ui.window.NovaUI.refresh();
  assert.deepEqual(ui.triggers, ['nova:refresh']);
});
