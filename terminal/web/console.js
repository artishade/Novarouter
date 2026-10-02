/**
 * NovaRouter terminal console — the standalone workspace client.
 *
 * One page for a terminal that may be hosted with nothing else: the shell tabs
 * on the left, Agentbox on the right, providers manageable in place. It talks
 * to the same contract the dashboard uses (`terminal/api.py`) under the host's
 * own `/terminal/pty` prefix, plus `/agent` for the AI side.
 *
 * Two rendering paths on purpose: xterm.js when the CDN is reachable (real
 * emulation — colour, cursor, vim, top), and a plain append-only viewer when it
 * is not, because a terminal that renders nothing the moment jsdelivr is
 * blocked is not a terminal. Both accept the same keystrokes.
 */
(function (global) {
  'use strict';

  const API = '/terminal/pty';
  const AGENT = '/agent';

  const state = {
    token: '',
    sessions: [],
    active: null,
    offset: 0,
    stream: null,
    cols: 120,
    rows: 32,
    history: [],
    providers: [],
    provider: '',
    pending: null,
    max: 8,
    font: 13,          // terminal text size, adjustable
    split: 380,        // chat pane width in px (desktop) / height (phone)
    stacked: false,    // phone layout: the splitter moves rows, not columns
    expanded: '',
  };

  const ZOOM = { min: 9, max: 24, step: 1 };
  const SPLIT = { min: 260, gutter: 6 };
  const PHONE = 860;    // the same breakpoint the stylesheet stacks at

  // ---- tiny DOM helper -----------------------------------------------------

  const $ = (id) => document.getElementById(id);
  const on = (el, name, fn) => el && el.addEventListener(name, fn);

  function say(who, text, cls) {
    const box = $('log');
    if (!box) return null;
    const empty = document.querySelector ? document.querySelector('#log .empty') : null;
    if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
    const el = document.createElement('div');
    el.className = 'msg ' + (cls || '');
    const label = document.createElement('span');
    label.className = 'who';
    label.textContent = who;
    el.appendChild(label);
    el.appendChild(document.createTextNode(String(text == null ? '' : text)));
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
    return el;
  }

  function showEmpty() {
    const box = $('log');
    if (!box || box.children.length) return;
    const el = document.createElement('div');
    el.className = 'empty';
    const strong = document.createElement('strong');
    strong.textContent = 'Agentbox';
    el.appendChild(strong);
    el.appendChild(document.createTextNode(
      state.providers.length
        ? 'Ask for a task. Every command it runs appears in a terminal tab.'
        : 'No provider yet — set NOVA_AGENTBOX_BASE_URL, or add one under providers.'));
    box.appendChild(el);
  }

  // ---- settings that survive a reload -------------------------------------

  function readSetting(key, fallback) {
    try {
      const raw = (global.localStorage && global.localStorage.getItem('nova_' + key)) || '';
      return raw === '' ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }

  function writeSetting(key, value) {
    try {
      if (global.localStorage) global.localStorage.setItem('nova_' + key, JSON.stringify(value));
    } catch (e) { /* private mode: the setting just will not persist */ }
  }

  // ---- text size ------------------------------------------------------------

  function isStacked() {
    if (global.matchMedia) return global.matchMedia(`(max-width: ${PHONE}px)`).matches;
    return state.stacked;
  }

  function applyFont() {
    state.font = Math.max(ZOOM.min, Math.min(ZOOM.max, Math.round(state.font)));
    if (screen.term && screen.term.options) screen.term.options.fontSize = state.font;
    if (screen.plain) screen.plain.style.fontSize = state.font + 'px';
    const read = $('zoomRead');
    if (read) read.textContent = state.font + 'px';
    writeSetting('font', state.font);
    screen.fit();          // a bigger font means fewer columns: refit and tell the shell
    postResize();
  }

  function zoom(delta) {
    state.font += delta;
    applyFont();
  }

  // ---- the draggable bar ----------------------------------------------------

  function viewport() {
    const doc = global.document || {};
    return Math.max(320, (doc.documentElement && doc.documentElement.clientWidth) || 1024);
  }

  /** Pure: how wide the chat pane should be, given a pointer position. */
  function splitFromPointer(at) {
    const stacked = isStacked();
    const total = stacked ? viewportH() : viewport();
    // Stacked: the bar is horizontal, so the pane is measured from the bottom.
    const raw = stacked ? total - at.clientY : total - at.clientX;
    const min = stacked ? 140 : SPLIT.min;
    const max = stacked ? total - 140 : total - 200;
    return Math.max(min, Math.min(max, Math.round(raw - SPLIT.gutter / 2)));
  }

  function viewportH() {
    const doc = global.document || {};
    return Math.max(320, (doc.documentElement && doc.documentElement.clientHeight) || 720);
  }

  function applySplit() {
    const doc = global.document || {};
    const root = (doc.documentElement && doc.documentElement.style) || null;
    if (!root) return;
    if (isStacked()) root.setProperty('--agent-rows', state.split + 'px');
    else root.setProperty('--split', state.split + 'px');
    const bar = $('split');
    if (bar) {
      bar.setAttribute('aria-valuenow', String(state.split));
      bar.setAttribute('aria-orientation', isStacked() ? 'horizontal' : 'vertical');
    }
    writeSetting('split', state.split);
    screen.fit();
    postResize();
  }

  function setSplit(px) {
    state.split = px;
    applySplit();
  }

  function startDrag(event) {
    const bar = $('split');
    if (!bar) return;
    const point = (e) => (e && ((e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]) || e));
    const start = point(event) || {};
    let moved = false;
    bar.classList.add('dragging');
    if (start.pointerId != null && bar.setPointerCapture) {
      try { bar.setPointerCapture(start.pointerId); } catch (e) { /* older Safari */ }
    }

    const move = (e) => {
      const at = point(e);
      if (!at) return;
      const along = isStacked() ? at.clientY - start.clientY : at.clientX - start.clientX;
      if (Math.abs(along) > 3) moved = true;
      if (moved && e && e.preventDefault && e.cancelable !== false) e.preventDefault();
      setSplit(splitFromPointer(at));
    };
    const up = () => {
      bar.classList.remove('dragging');
      if (global.removeEventListener) {
        global.removeEventListener('mousemove', move);
        global.removeEventListener('mouseup', up);
        global.removeEventListener('touchmove', move);
        global.removeEventListener('touchend', up);
      }
      if (!moved) togglePane('agent');   // a click, not a drag: collapse it
    };
    if (global.addEventListener) {
      global.addEventListener('mousemove', move);
      global.addEventListener('mouseup', up);
      global.addEventListener('touchmove', move, { passive: false });
      global.addEventListener('touchend', up);
    }
  }

  function nudgeSplit(step) {
    setSplit(Math.max(isStacked() ? 140 : SPLIT.min, state.split + step));
  }

  // ---- panels ---------------------------------------------------------------

  function body() {
    return (global.document && global.document.body) || null;
  }

  function collapsedClass(which) {
    return which === 'agent' ? 'agent-collapsed' : 'term-collapsed';
  }

  /** The one place a pane's visibility changes — so the phone switch, the
      chrome buttons and the drag bar can never disagree. */
  function setCollapsed(which, on) {
    const el = body();
    if (!el) return;
    const key = collapsedClass(which);
    el.classList.toggle(key, !!on);
    writeSetting(key, !!on);
  }

  function isCollapsed(which) {
    const el = body();
    return !!el && el.classList.contains(collapsedClass(which));
  }

  function markMobile(pane) {
    const box = $('mobileSwitch');
    if (!box) return;
    Array.prototype.forEach.call(box.children, (btn) => {
      const on = !!pane && btn.dataset && btn.dataset.pane === pane;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-selected', String(on));
    });
  }

  /** `open` is the *visible* state — the inverse of the class name. */
  function setPane(which, open) {
    setCollapsed(which, !open);
    // On a phone one pane is the other pane's absence, so keep the switch honest.
    if (isStacked()) markMobile(open ? which : null);
    setTimeout(() => { screen.fit(); postResize(); }, 180);
  }

  function togglePane(which) {
    setPane(which, isCollapsed(which));
  }

  function setExpanded(which) {
    const el = body();
    if (!el) return;
    const next = state.expanded === which ? '' : which;
    state.expanded = next;
    el.classList.toggle('expanded', !!next);
    const tBtn = $('expand');
    const aBtn = $('expandAgent');
    if (tBtn) {
      tBtn.setAttribute('aria-pressed', String(next === 'term'));
      tBtn.setAttribute('aria-label', next === 'term' ? 'restore the layout' : 'expand the terminal');
    }
    if (aBtn) {
      aBtn.setAttribute('aria-pressed', String(next === 'agent'));
      aBtn.setAttribute('aria-label', next === 'agent' ? 'restore the layout' : 'expand the chat panel');
    }
    writeSetting('expanded', next);
    setTimeout(() => { screen.fit(); postResize(); }, 180);
  }

  /** The phone's Terminal / Agent switch. */
  function showMobile(pane) {
    markMobile(pane);
    if (!isStacked()) return;
    setCollapsed('agent', pane !== 'agent');
    setCollapsed('term', false);
    setTimeout(() => { screen.fit(); postResize(); }, 180);
  }

  function copyScreen() {
    let text = '';
    if (screen.term && screen.term.getSelection) text = screen.term.getSelection();
    if (!text && screen.plain) text = screen.plain.textContent;
    if (!text) { say('TERMINAL', 'nothing selected to copy', 'err'); return; }
    const done = () => say('TERMINAL', 'copied the visible screen');
    if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
      global.navigator.clipboard.writeText(text).then(done, () => say('TERMINAL', 'the browser refused the clipboard', 'err'));
    } else {
      say('TERMINAL', 'this browser has no clipboard access', 'err');
    }
  }

  // ---- transport -----------------------------------------------------------

  function headers(extra) {
    const out = Object.assign({ 'content-type': 'application/json' }, extra || {});
    if (state.token) out['X-Nova-Terminal-Token'] = state.token;
    return out;
  }

  async function api(path, options) {
    const opts = Object.assign({}, options || {});
    opts.headers = headers(opts.headers);
    let res = await fetch(path, opts);
    if (res.status === 401 && !state.pending) {
      const token = askToken();                 // the host gates root shells
      if (token) {
        state.token = token;
        opts.headers = headers();
        res = await fetch(path, opts);
      }
    }
    return res;
  }

  async function json(path, options) {
    const res = await api(path, options);
    const body = await res.json().catch(() => ({}));
    return { status: res.status, body };
  }

  function askToken() {
    if (typeof prompt !== 'function') return '';
    const token = prompt('This terminal host requires its shared secret\n(NOVA_TERMINAL_TOKEN):', state.token || '');
    if (token) {
      try { global.localStorage.setItem('nova_terminal_token', token); } catch (e) { /* private mode */ }
    }
    return token || '';
  }

  function rememberToken() {
    try {
      state.token = (global.localStorage && global.localStorage.getItem('nova_terminal_token')) || '';
    } catch (e) { state.token = ''; }
  }

  // ---- the shell screen ----------------------------------------------------

  const screen = {
    term: null,
    plain: null,
    write(data) {
      if (this.term) { this.term.write(data); return; }
      if (this.plain) { this.plain.textContent += data; this.plain.scrollTop = this.plain.scrollHeight; }
    },
    resize(cols, rows) {
      state.cols = cols;
      state.rows = rows;
      if (this.term && this.term.resize) this.term.resize(cols, rows);
    },
    clear() {
      if (this.term && this.term.clear) this.term.clear();
      if (this.plain) this.plain.textContent = '';
    },
    focus() { if (this.term && this.term.focus) this.term.focus(); },
    fit() {
      if (this.term && global.FitAddon && this.term._fit) this.term._fit.fit();
    },
  };

  function buildTerminal() {
    const host = $('screen');
    if (!host) return;
    const TerminalCtor = global.Terminal;
    if (TerminalCtor) {
      screen.term = new TerminalCtor({
        convertEol: false,
        cursorBlink: true,
        fontSize: 13,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        scrollback: 5000,
        theme: { background: '#080c14', foreground: '#cbd5e1', cursor: '#34d399' },
      });
      screen.term.open(host);
      if (global.FitAddon) {
        screen.term._fit = new global.FitAddon.FitAddon();
        screen.term.loadAddon(screen.term._fit);
        screen.term._fit.fit();
      }
      screen.term.onData((data) => send(data));
    } else {
      // No CDN: still a usable terminal, just without emulation.
      const pre = document.createElement('pre');
      pre.className = 'plain';
      pre.setAttribute('aria-label', 'terminal output');
      host.appendChild(pre);
      screen.plain = pre;
      $('screen').setAttribute('tabindex', '0');
      // Named keys first: `Enter`.length is 5, so a printable-char test would
      // silently drop the most important key on the keyboard.
      const NAMED = {
        Enter: '\r', Backspace: '\x7f', Tab: '\t', Escape: '\x1b',
        ArrowUp: '\x1b[A', ArrowDown: '\x1b[B', ArrowRight: '\x1b[C', ArrowLeft: '\x1b[D',
        Home: '\x1b[H', End: '\x1b[F', PageUp: '\x1b[5~', PageDown: '\x1b[6~',
      };
      const capture = (e) => {
        let data = null;
        if (NAMED[e.key]) data = NAMED[e.key];
        else if (e.ctrlKey && e.key.length === 1) data = String.fromCharCode(e.key.toUpperCase().charCodeAt(0) - 64);
        else if (e.key.length === 1) data = e.key;
        if (data === null) return;
        e.preventDefault();
        send(data);
      };
      on($('screen'), 'keydown', capture);
    }
    postResize();
  }

  function throttle(fn, wait) {
    let timer = null;
    return function () {
      if (timer) return;
      timer = setTimeout(() => { timer = null; fn(); }, wait || 120);
    };
  }

  const postResize = throttle(() => {
    if (!state.active) return;
    api(`${API}/resize`, {
      method: 'POST',
      body: JSON.stringify({ session: state.active, cols: state.cols, rows: state.rows }),
    }).catch(() => {});
  }, 150);

  function send(data) {
    if (!state.active) return;
    api(`${API}/input`, { method: 'POST', body: JSON.stringify({ session: state.active, data }) })
      .catch(() => {});
  }

  // ---- sessions ------------------------------------------------------------

  async function loadSessions() {
    const { status, body } = await json(`${API}/sessions`);
    if (status === 401) return;
    state.sessions = (body && body.sessions) || [];
    state.max = (body && body.max_sessions) || state.max;
    renderTabs();
    const live = state.sessions.filter((s) => !s.closed);
    if (!state.active || !live.some((s) => s.id === state.active)) {
      const preferred = live.find((s) => s.active) || live[0];
      if (preferred) select(preferred.id);
      else if (state.active) select(state.active);
    } else if (live.length) {
      const focused = live.find((s) => s.active);
      if (focused && focused.id !== state.active) select(focused.id);
    }
  }

  function renderTabs() {
    const box = $('tabs');
    if (!box) return;
    box.textContent = '';
    state.sessions.forEach((s) => {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'tab' + (s.id === state.active ? ' on' : '');
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-selected', String(s.id === state.active));
      tab.setAttribute('aria-label', 'session ' + (s.label || s.id));

      const lbl = document.createElement('span');
      lbl.className = 'lbl';
      lbl.textContent = s.label || s.id;
      tab.appendChild(lbl);

      if (s.cwd) {
        const cwd = document.createElement('span');
        cwd.className = 'cwd';
        cwd.textContent = s.cwd;
        tab.appendChild(cwd);
      }

      const kill = document.createElement('span');
      kill.className = 'kill';
      kill.textContent = '×';
      kill.setAttribute('role', 'button');
      kill.setAttribute('aria-label', 'close ' + (s.label || s.id));
      kill.onclick = (e) => { e.stopPropagation(); closeSession(s.id); };
      tab.appendChild(kill);

      tab.onclick = () => {
        api(`${API}/activate`, { method: 'POST', body: JSON.stringify({ session: s.id }) }).catch(() => {});
        select(s.id);
      };
      tab.ondblclick = () => renameSession(s);
      box.appendChild(tab);
    });

    const sep = document.createElement('span');
    sep.className = 'sep';
    box.appendChild(sep);

    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'icon';
    add.setAttribute('aria-label', 'new session');
    add.textContent = '+';
    add.onclick = newSession;
    box.appendChild(add);

    const count = document.createElement('span');
    count.className = 'cwd';
    count.id = 'count';
    count.style.padding = '0 8px';
    count.textContent = `${state.sessions.length}/${state.max || 8}`;
    box.appendChild(count);
  }

  function select(id) {
    if (!id) return;
    state.active = id;
    state.offset = 0;
    screen.clear();
    renderTabs();
    attach();
    screen.focus();
    const here = state.sessions.find((s) => s.id === id);
    const hint = $('where');
    if (hint) hint.textContent = here ? (here.cwd || '') : '';
  }

  function attach() {
    if (state.stream && state.stream.close) state.stream.close();
    state.stream = null;
    if (!state.active) return;
    const source = new EventSource(`${API}/stream?session=${encodeURIComponent(state.active)}&offset=${state.offset}`);
    state.stream = source;
    source.onmessage = (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch (err) { return; }
      onChunk(data);
    };
    source.onerror = () => {
      source.close();
      if (state.active) setTimeout(attach, 1500);   // the host restarts shells; reconnect
    };
  }

  function onChunk(data) {
    if (data.o) {
      state.offset += data.o.length;
      screen.write(data.o);
    }
    if (data.error) say('TERMINAL', data.error, 'err');
    if (data.cwd) {
      const hint = $('where');
      if (hint) hint.textContent = data.cwd;
      const here = state.sessions.find((s) => s.id === (data.session || state.active));
      if (here) { here.cwd = data.cwd; renderTabs(); }
    }
    if (data.done) {
      say('TERMINAL', `session ended (exit ${data.exit == null ? 0 : data.exit})`);
      loadSessions();
    }
  }

  async function newSession() {
    const { body } = await json(`${API}/sessions`, {
      method: 'POST',
      body: JSON.stringify({ cols: state.cols, rows: state.rows }),
    });
    if (!body.session) { say('TERMINAL', body.error || 'could not open a session', 'err'); return; }
    select(body.session);
    await loadSessions();
  }

  async function closeSession(id) {
    const { body } = await json(`${API}/stop`, { method: 'POST', body: JSON.stringify({ session: id }) });
    if (state.active === id) { state.active = null; state.offset = 0; screen.clear(); }
    await loadSessions();
    if (!state.active && body && body.state) say('TERMINAL', 'session closed');
  }

  function renameSession(session) {
    const label = global.prompt ? prompt('tab name', session.label || '') : null;
    if (!label) return;
    api(`${API}/rename`, {
      method: 'POST',
      body: JSON.stringify({ session: session.id, label }),
    }).then(loadSessions).catch(() => {});
  }

  // ---- Agentbox ------------------------------------------------------------

  async function loadProviders() {
    const { status, body } = await json(`${AGENT}/providers`);
    if (status === 401 || !body || !body.providers) return;
    state.providers = body.providers;
    state.provider = body.active || (state.providers[0] && state.providers[0].id) || '';

    const pick = $('provider');
    if (pick) {
      pick.textContent = '';
      state.providers.forEach((p) => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.label || p.id;
        if (p.id === state.provider) opt.selected = true;
        pick.appendChild(opt);
      });
      pick.hidden = state.providers.length < 2;
    }
    renderProviders(body);
    const dot = $('adot');
    if (dot) dot.classList.toggle('bad', !state.providers.length);
  }

  function renderProviders(body) {
    const box = $('provList');
    if (!box) return;
    box.textContent = '';
    (body.providers || []).forEach((p) => {
      const row = document.createElement('div');
      row.className = 'prov' + (p.id === state.provider ? ' active' : '');

      const name = document.createElement('b');
      name.textContent = p.id;
      const url = document.createElement('span');
      url.className = 'u';
      url.textContent = `${p.base_url}${p.model ? ' · ' + p.model : ''}${p.has_key ? ' · ' + p.api_key : ''}`;
      const del = document.createElement('button');
      del.type = 'button';
      del.textContent = '×';
      del.setAttribute('aria-label', 'remove ' + p.id);
      del.onclick = async () => {
        await api(`${AGENT}/providers/${encodeURIComponent(p.id)}`, { method: 'DELETE' });
        loadProviders();
      };
      row.append(name, url, del);
      box.appendChild(row);
    });
    const where = $('registry');
    if (where) where.textContent = body.registry || '';
  }

  async function ask(message) {
    say('YOU', message, 'you');
    const pending = say('AGENTBOX', 'thinking…', 'bot');
    const { status, body } = await json(`${AGENT}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message, history: state.history, provider: state.provider || undefined }),
    });
    if (pending) pending.remove();
    if (status !== 200 || !body.ok) {
      say('AGENTBOX', body.error || `HTTP ${status}`, 'err');
      return;
    }
    (body.steps || []).forEach((step) => {
      const detail = step.tool === 'run_command'
        ? `$ ${step.args.command}\n${(step.result && step.result.output || JSON.stringify(step.result)).slice(0, 600)}`
        : `${step.tool} ${JSON.stringify(step.args).slice(0, 120)}\n${JSON.stringify(step.result).slice(0, 400)}`;
      say(String(step.tool).toUpperCase(), detail, 'tool');
    });
    say('AGENTBOX', body.reply || '(no reply)', 'bot');
    state.history.push({ role: 'user', content: message }, { role: 'assistant', content: body.reply || '' });
    loadSessions();   // its commands opened tabs — show them
  }

  async function saveProvider(form) {
    const payload = {
      id: $('p_id').value.trim(),
      base_url: $('p_url').value.trim(),
      model: $('p_model').value.trim(),
      api_key: $('p_key').value,
    };
    const { status, body } = await json(`${AGENT}/providers`, { method: 'POST', body: JSON.stringify(payload) });
    if (status !== 200) { say('AGENTBOX', body.error || `HTTP ${status}`, 'err'); return; }
    $('p_key').value = '';
    state.provider = body.provider.id;
    loadProviders();
    say('AGENTBOX', `provider ${body.provider.id} saved — using it for the next message`, 'bot');
  }

  // ---- wiring --------------------------------------------------------------

  function wire() {
    // panel chrome
    on($('new'), 'click', newSession);
    on($('rename'), 'click', () => {
      const here = state.sessions.find((s) => s.id === state.active);
      if (here) renameSession(here);
    });
    on($('copy'), 'click', copyScreen);
    on($('menu'), 'click', () => {
      const here = state.sessions.find((s) => s.id === state.active);
      say('TERMINAL', [
        `session: ${here ? (here.label || here.id) : 'none'}`,
        `text size: ${state.font}px`,
        'drag or click the bar to resize the chat pane',
        'ctrl/cmd + - and + change the text size',
      ].join('\n'), 'tool');
    });
    on($('expand'), 'click', () => setExpanded('term'));
    on($('closePanel'), 'click', () => togglePane('term'));
    on($('zoomIn'), 'click', () => zoom(1));
    on($('zoomOut'), 'click', () => zoom(-1));
    on($('zoomReset'), 'click', () => { state.font = 13; applyFont(); });

    // the draggable bar
    on($('split'), 'mousedown', startDrag);
    on($('split'), 'touchstart', startDrag, { passive: false });
    on($('split'), 'keydown', (e) => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { nudgeSplit(-24); e.preventDefault(); }
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { nudgeSplit(24); e.preventDefault(); }
      if (e.key === 'Enter' || e.key === ' ') { togglePane('agent'); e.preventDefault(); }
    });

    // agent
    on($('newChat'), 'click', () => {
      state.history = [];
      const log = $('log');
      if (log) log.textContent = '';
      showEmpty();
    });
    on($('expandAgent'), 'click', () => setExpanded('agent'));
    on($('closeAgent'), 'click', () => togglePane('agent'));
    on($('manage'), 'click', () => {
      const box = $('providerBox');
      box.hidden = !box.hidden;
      if (!box.hidden) loadProviders();
    });
    on($('provider'), 'change', (e) => { state.provider = e.target.value; });
    on($('provForm'), 'submit', (e) => { e.preventDefault(); saveProvider(); });
    on($('composer'), 'submit', (e) => {
      e.preventDefault();
      const box = $('input');
      const message = box.value.trim();
      if (!message) return;
      box.value = '';
      ask(message);
    });
    on($('input'), 'keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) $('composer').requestSubmit();
    });

    const box = $('mobileSwitch');
    if (box) {
      Array.prototype.forEach.call(box.children, (btn) => {
        on(btn, 'click', () => showMobile(btn.dataset.pane));
      });
    }

    // keyboard: the same shortcuts an editor gives you
    on(global, 'keydown', (e) => {
      const acc = e.ctrlKey || e.metaKey;
      if (!acc) return;
      if (e.key === '=' || e.key === '+') { zoom(1); e.preventDefault(); }
      else if (e.key === '-' || e.key === '_') { zoom(-1); e.preventDefault(); }
      else if (e.key === '0') { state.font = 13; applyFont(); e.preventDefault(); }
    });
    on(global, 'resize', () => { state.stacked = isStacked(); screen.fit(); applySplit(); });
  }

  function restoreSettings() {
    rememberToken();
    state.font = readSetting('font', 13);
    state.split = readSetting('split', 380);
    applyFont();
    state.stacked = isStacked();
    applySplit();
    if (readSetting('agent-collapsed', false)) setCollapsed('agent', true);
    if (readSetting('term-collapsed', false)) setCollapsed('term', true);
    setExpanded(readSetting('expanded', ''));
  }

  async function boot() {
    restoreSettings();
    wire();
    buildTerminal();
    showEmpty();
    try {
      await loadSessions();
      if (!state.sessions.length) await newSession();
    } catch (err) {
      say('TERMINAL', `cannot reach the terminal host (${err && err.message})`, 'err');
    }
    loadProviders();
    global.setInterval(loadSessions, 20000);
  }

  const NovaTerminalConsole = {
    state, screen, api, json, askToken, headers,
    loadSessions, renderTabs, select, attach, onChunk,
    newSession, closeSession, renameSession, send, postResize, buildTerminal,
    loadProviders, renderProviders, saveProvider, ask, boot, say,
    applyFont, zoom, applySplit, setSplit, splitFromPointer, startDrag, nudgeSplit,
    togglePane, setPane, setCollapsed, isCollapsed, markMobile, setExpanded, showMobile,
    copyScreen, restoreSettings, showEmpty, readSetting, wire,
  };

  global.NovaTerminalConsole = NovaTerminalConsole;
  if (global.document && global.document.addEventListener) {
    global.document.addEventListener('DOMContentLoaded', boot);
  }
})(typeof window !== 'undefined' ? window : globalThis);
