/* Nova Console + Root@Build terminal workspace — shared client runtime.
 * Loaded by the standalone /terminal page and the embedded dashboard
 * fragment. Owns: session tabs, xterm viewport, SSE attach, one-shot exec,
 * the console sidebar (chat / $cmd / !goal / /shortcut) and the config
 * drawer actions that are not HTMX.
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const root = $('tab-terminal');
  if (!root) return;
  if (root.dataset.wsReady === '1') return;   // htmx swaps can double-insert
  root.dataset.wsReady = '1';

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const icons = () => { if (window.lucide) window.lucide.createIcons(); };
  const toast = (msg, type) => { if (window.NovaUI) NovaUI.toast(msg, type || 'info'); };
  const shortCwd = (p) => {
    const home = '/root';
    const s = String(p || '');
    return s === home ? '~' : (s.startsWith(home + '/') ? '~' + s.slice(home.length) : s);
  };

  /* ============================ sessions ============================ */

  const view = $('term-view');
  const fallback = $('term-fallback');
  const noticeTextEl = $('term-notice-text');
  const noticeBox = $('term-notice');
  const XTERM = window.Terminal && window.FitAddon;

  let term = null;
  let fit = null;

  if (XTERM) {
    term = new window.Terminal({
      fontFamily: "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: Number(localStorage.getItem('nova.term.fontsize') || 12.5),
      lineHeight: 1.25,
      cursorBlink: true,
      cursorStyle: 'bar',
      scrollback: 10000,
      allowTransparency: true,
      convertEol: false,
      theme: {
        background: '#070b13', foreground: '#cbd5e1', cursor: '#34d399', cursorAccent: '#070b13',
        selectionBackground: 'rgba(30,41,59,0.85)',
        black: '#0f172a', red: '#fb7185', green: '#34d399', yellow: '#fbbf24',
        blue: '#60a5fa', magenta: '#c084fc', cyan: '#22d3ee', white: '#e2e8f0',
        brightBlack: '#64748b', brightRed: '#fda4af', brightGreen: '#6ee7b7', brightYellow: '#fde047',
        brightBlue: '#93c5fd', brightMagenta: '#e9d5ff', brightCyan: '#67e8f9', brightWhite: '#f8fafc',
      },
    });
    fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(view);
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true;
      const acc = ev.ctrlKey || ev.metaKey;
      if (acc && ev.shiftKey && ev.key.toLowerCase() === 'c') { copyOutput(); return false; }
      if (acc && ev.key.toLowerCase() === 'l') { term.clear(); return false; }
      return true;
    });
    term.onData((data) => send(data));
  } else {
    fallback.hidden = false;
    noticeTextEl.textContent = 'The terminal emulator could not load, so interactive shell sessions are unavailable.';
    noticeBox.hidden = false;
    $('term-new').hidden = true;
  }

  const write = (data) => {
    if (term) term.write(data);
    else if (data) fallback.textContent += String(data).replace(/\r\n/g, '\n');
  };
  const dim = (s) => '\x1b[38;5;244m' + s + '\x1b[0m';
  const bold = (s) => '\x1b[1m' + s + '\x1b[0m';
  const green = (s) => '\x1b[32m' + s + '\x1b[0m';

  function copyOutput() {
    if (!term) return;
    const text = term.getSelection();
    if (text) { if (window.NovaUI) NovaUI.copy(text, 'Terminal output copied'); return; }
    const buffer = term.buffer.active;
    const lines = [];
    for (let i = 0; i < buffer.length; i++) lines.push(buffer.getLine(i).translateToString(true));
    if (window.NovaUI) NovaUI.copy(lines.join('\n').trim(), 'Terminal output copied');
  }

  const state = { sessions: [], active: null, es: null, busy: false };
  const tabsEl = $('term-tabs');
  const modeEl = $('term-mode');
  const cwdEl = $('term-cwd');
  const sizeEl = $('term-size');
  const countEl = $('term-count');

  const session = (id) => state.sessions.find((s) => s.id === id) || null;

  function renderTabs() {
    if (!tabsEl) return;
    const tabs = [];
    state.sessions.forEach((s) => {
      const active = state.active === s.id;
      tabs.push(
        '<div class="term-tab group ' + (active ? 'is-active' : '') + ' flex h-7 max-w-[210px] shrink-0 items-center gap-1 rounded-md pl-2 pr-1 text-[11px]" role="presentation">' +
          '<button type="button" role="tab" data-session="' + esc(s.id) + '" title="' + esc(s.cwd) + '" aria-selected="' + active + '" ' +
            'class="flex min-w-0 items-center gap-1.5 text-left">' +
            '<span class="size-1.5 shrink-0 rounded-full ' + (s.closed ? 'bg-slate-600' : 'bg-emerald-400 term-pulse') + '"></span>' +
            '<span class="truncate font-medium">' + esc(s.label) + '</span>' +
            '<span class="hidden truncate font-mono text-[10px] text-slate-600 md:inline">' + esc(shortCwd(s.cwd)) + '</span>' +
          '</button>' +
          '<button type="button" class="term-tab-close ml-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded text-slate-500" ' +
            'data-close="' + esc(s.id) + '" aria-label="Close session ' + esc(s.label) + '" title="Close session">' +
            '<i data-lucide="x" class="size-3"></i></button>' +
        '</div>'
      );
    });
    tabsEl.innerHTML = tabs.join('');

    if (countEl) countEl.textContent = state.sessions.length + (state.sessions.length === 1 ? ' session' : ' sessions');
    const live = session(state.active);
    if (modeEl) {
      modeEl.textContent = live ? (live.closed ? 'Session ended' : 'Interactive shell') : 'Connecting…';
      modeEl.className = 'inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 ' +
        (live && !live.closed ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' : 'border-slate-700 text-slate-400');
    }
    if (cwdEl) {
      cwdEl.textContent = live ? shortCwd(live.cwd) : '~';
      cwdEl.title = live ? live.cwd : '';
    }
    icons();
  }

  async function refreshState() {
    try {
      const res = await fetch('/api/admin/terminal/pty/sessions');
      if (!res.ok) return;
      const data = await res.json();
      state.sessions = data.sessions || [];
      if (!session(state.active) || (session(state.active) || {}).closed) {
        const live = state.sessions.find((s) => !s.closed);
        if (live) attach(live.id);
        else if (state.active) { state.active = null; renderTabs(); }
      } else {
        renderTabs();
      }
    } catch (e) { /* transient */ }
  }

  function clearScreen() {
    if (term) term.clear();
    else if (fallback) fallback.textContent = '';
  }

  function detach() {
    if (state.es) { try { state.es.close(); } catch (e) {} state.es = null; }
  }

  function send(data) {
    const live = session(state.active);
    if (!live || live.closed) return;
    fetch('/api/admin/terminal/pty/input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: live.id, data }),
    }).catch(() => {});
  }

  function attach(id) {
    if (state.active === id && state.es) { if (term) term.focus(); return; }
    detach();
    state.active = id;
    renderTabs();
    const live = session(id);
    if (!live) return;
    noticeBox.hidden = true;
    clearScreen();
    resize();
    const es = new EventSource('/api/admin/terminal/pty/stream?session=' + encodeURIComponent(id));
    state.es = es;
    es.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.o) write(msg.o);
      if (msg.cwd) {
        const s = session(id);
        if (s) { s.cwd = msg.cwd; s.label = msg.label || s.label; renderTabs(); }
      }
      if (msg.exit !== undefined || msg.done) {
        detach();
        write(dim('\r\n[process exited with code ' + (msg.exit === undefined ? 0 : msg.exit) + ']\r\n'));
        refreshState();
      }
    };
    es.onerror = () => {
      const s = session(id);
      if (s && s.closed) { detach(); refreshState(); }
    };
    if (term) term.focus();
  }

  /* ------------------------ one-shot exec bar ------------------------ */

  async function runCommand(cmd) {
    write(green('~ Root@Build:~# ') + cmd + '\r\n');
    if (cmd === 'clear' || cmd === 'reset') { clearScreen(); return; }
    try {
      const res = await fetch('/ui/terminal/exec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.error) || 'HTTP ' + res.status);
      if (data.output) write(String(data.output).replace(/\r?\n/g, '\r\n') + '\r\n');
      if (data.hint) write('\x1b[33m' + data.hint + '\x1b[0m\r\n');
      if (typeof data.code === 'number' && data.code !== 0) write(dim('exit code ' + data.code) + '\r\n');
    } catch (err) {
      write('\x1b[31m' + ((err && err.message) || 'The command could not be run') + '\x1b[0m\r\n');
    }
  }

  /* -------------------------- session actions -------------------------- */

  async function createSession() {
    const btn = $('term-new');
    if (btn) btn.disabled = true;
    try {
      const res = await fetch('/api/admin/terminal/pty/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cols: 120, rows: 32 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.error) || 'HTTP ' + res.status);
      state.sessions = state.sessions.filter((s) => s.closed);
      state.sessions.push(data.info);
      attach(data.session);
      toast('Shell session started', 'success');
    } catch (err) {
      toast((err && err.message) || 'Could not start a shell session', 'error');
    }
    if (btn) btn.disabled = false;
  }

  async function closeSession(id) {
    const s = session(id);
    try {
      await fetch('/api/admin/terminal/pty/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: id }),
      });
    } catch (e) { /* the reaper cleans up regardless */ }
    if (state.active === id) { detach(); state.active = null; }
    state.sessions = state.sessions.filter((x) => x.id !== id);
    renderTabs();
    refreshState();
    if (s) toast('Closed "' + s.label + '"');
  }

  async function renameSession(id) {
    const s = session(id);
    if (!s) return;
    const label = window.prompt('Name this session', s.label);
    if (label === null) return;
    try {
      const res = await fetch('/api/admin/terminal/pty/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: id, label }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.error) || 'HTTP ' + res.status);
      s.label = data.info.label;
      s.cwd = data.info.cwd;
      renderTabs();
    } catch (err) {
      toast((err && err.message) || 'Could not rename the session', 'error');
    }
  }

  if (tabsEl) {
    tabsEl.addEventListener('click', (e) => {
      const close = e.target.closest('[data-close]');
      if (close) { e.stopPropagation(); closeSession(close.dataset.close); return; }
      const tab = e.target.closest('[data-session]');
      if (tab) attach(tab.dataset.session);
    });
    tabsEl.addEventListener('dblclick', (e) => {
      const tab = e.target.closest('[data-session]');
      if (tab) renameSession(tab.dataset.session);
    });
  }

  $('term-new').addEventListener('click', createSession);
  $('term-clear').addEventListener('click', clearScreen);
  $('term-copy').addEventListener('click', copyOutput);

  const setFont = (delta) => {
    if (!term) return;
    const size = Math.min(18, Math.max(10, term.options.fontSize + delta));
    term.options.fontSize = size;
    try { localStorage.setItem('nova.term.fontsize', String(size)); } catch (e) {}
    resize();
  };
  $('term-font-up').addEventListener('click', () => setFont(0.5));
  $('term-font-down').addEventListener('click', () => setFont(-0.5));

  /* ------------------------------ sizing ------------------------------ */

  let resizeTimer = null;
  function resize() {
    if (!fit || !term) return;
    if (!view.offsetParent) return;          // hidden tab — dimensions are 0
    try { fit.fit(); } catch (e) { return; }
    const live = session(state.active);
    if (!live || live.closed) return;
    if (live.cols === term.cols && live.rows === term.rows) return;
    live.cols = term.cols;
    live.rows = term.rows;
    if (sizeEl) sizeEl.textContent = live.cols + ' × ' + live.rows;
    fetch('/api/admin/terminal/pty/resize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: live.id, cols: term.cols, rows: term.rows }),
    }).catch(() => {});
  }
  const queueResize = () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 120);
  };
  if (window.ResizeObserver) new ResizeObserver(queueResize).observe(view);
  window.addEventListener('resize', queueResize);

  /* ====================== Nova Console sidebar ====================== */

  const side = $('ws-console');
  const sideBody = $('term-side-body');
  const sideStream = $('term-side-stream');
  const sideInput = $('term-side-input');
  const sideSend = $('term-side-send');
  const sideMode = $('term-side-mode');
  const sideHist = [];
  let chatBusy = false;

  const MODES = [[/^\$/, 'command', 'text-sky-300'], [/^\//, 'shortcut', 'text-amber-300'], [/^!/, 'agent', 'text-purple-300']];

  function bubble(cls, html) {
    const el = document.createElement('div');
    el.className = cls;
    el.innerHTML = html;
    sideStream.appendChild(el);
    sideStream.scrollTop = sideStream.scrollHeight;
    icons();
    return el;
  }

  function route(text) {
    if (/^\$/.test(text)) return { kind: 'terminal', arg: text.slice(1).trim() };
    if (/^\/(status|models|gpu|storage|keys|agents|boost|help)\b/.test(text)) return { kind: 'shortcut', arg: text };
    if (/^!/.test(text)) return { kind: 'agent', arg: text.slice(1).trim() };
    return { kind: 'chat', arg: text };
  }

  async function sideCommand(cmd) {
    const res = await fetch('/ui/terminal/exec', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd }),
    });
    const data = await res.json().catch(() => ({}));
    bubble('rounded-xl border border-slate-800 bg-[#070b13] px-3 py-2 font-mono text-[11px] text-slate-300',
      '<div class="text-emerald-400">~ Root@Build:~# ' + esc(cmd) + '</div><pre class="mt-1 max-h-56 overflow-auto whitespace-pre-wrap">' +
      esc(data.output || data.error || '(no output)') + '</pre>');
  }

  async function sideAgent(goal) {
    bubble('rounded-xl border border-purple-500/30 bg-purple-500/10 px-3 py-2 text-[12px] text-purple-200',
      '<span class="inline-flex items-center gap-1.5"><i data-lucide="loader-2" class="size-3 animate-spin"></i>Agent started: ' + esc(goal) + '</span>');
    try {
      const res = await fetch('/api/agent/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal, max_steps: 8 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.error) || 'HTTP ' + res.status);
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const poll = await fetch('/api/agent/tasks/' + encodeURIComponent(data.id));
        if (!poll.ok) continue;
        const task = await poll.json();
        const running = task.status === 'queued' || task.status === 'running';
        const steps = (task.steps || []).map((s) =>
          '<li class="truncate"><span class="' + (s.status === 'error' ? 'text-rose-300' : 'text-emerald-300') + '">•</span> ' +
          esc(s.title || s.action) + '</li>').join('');
        bubble('rounded-xl border border-slate-800 bg-[#0d1322] px-3 py-2 text-[11px]',
          '<div class="mb-1 font-semibold uppercase tracking-wider text-emerald-400">Agent · ' + esc(task.status) + '</div>' +
          (steps ? '<ol class="space-y-1 text-slate-400">' + steps + '</ol>' : '') +
          (running ? '<div class="mt-1 text-slate-500">working…</div>' : '') +
          (!running && task.summary ? '<div class="md-body mt-2 border-t border-slate-800 pt-2 text-slate-300">' +
            esc(task.summary).slice(0, 600) + '</div>' : ''));
        if (!running) return;
      }
    } catch (err) {
      bubble('rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-200',
        'The agent could not finish: ' + esc((err && err.message) || 'unknown error'));
    }
  }

  async function sideChat(text, context) {
    const holder = bubble('rounded-xl border border-slate-800 bg-[#0d1322] px-3 py-2 text-[12px] text-slate-200',
      '<div class="md-body whitespace-pre-wrap"><span class="nova-caret text-emerald-400">▌</span></div>');
    const body = holder.querySelector('.md-body');
    try {
      const res = await fetch('/api/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: 'auto', stream: true, messages: context.concat([{ role: 'user', content: text }]) }),
      });
      if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let content = '';
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buf += decoder.decode(chunk.value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const raw = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!raw.startsWith('data:')) continue;
          const payload = raw.slice(5).trim();
          if (payload === '[DONE]') continue;
          try {
            const obj = JSON.parse(payload);
            const choice = obj.choices && obj.choices[0];
            const piece = (choice && ((choice.delta && choice.delta.content) || (choice.message && choice.message.content))) || '';
            if (!piece) continue;
            content += piece;
            body.innerHTML = '<span class="whitespace-pre-wrap break-words">' + esc(content) +
              '</span><span class="nova-caret text-emerald-400">▌</span>';
            sideStream.scrollTop = sideStream.scrollHeight;
          } catch (e) { /* partial frame */ }
        }
      }
      body.innerHTML = '<span class="whitespace-pre-wrap break-words">' + esc(content || '(empty response)') + '</span>';
    } catch (err) {
      body.innerHTML = '<span class="text-rose-300">' + esc((err && err.message) || 'The request failed') + '</span>';
    }
  }

  async function sideSendNow() {
    const text = (sideInput ? sideInput.value : '').trim();
    if (!text || chatBusy) return;
    if (sideInput) sideInput.value = '';
    chatBusy = true;
    if (sideSend) sideSend.disabled = true;
    bubble('ml-auto max-w-[85%] rounded-2xl rounded-tr-md border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-[12px] text-slate-100',
      '<p class="whitespace-pre-wrap break-words">' + esc(text) + '</p>');
    sideHist.push({ role: 'user', content: text });
    if (sideHist.length > 16) sideHist.splice(0, sideHist.length - 16);

    const target = route(text);
    const context = sideHist.slice(-8).map((m) => ({ role: m.role, content: String(m.content).slice(0, 700) }));
    try {
      if (target.kind === 'terminal') await sideCommand(target.arg);
      else if (target.kind === 'shortcut') {
        if (target.arg === '/help') {
          bubble('rounded-xl border border-slate-800 bg-[#0d1322] px-3 py-2 text-[11px] text-slate-300',
            'Shortcuts: <span class="font-mono text-slate-400">/status</span> <span class="font-mono text-slate-400">/models</span> ' +
            '<span class="font-mono text-slate-400">/gpu</span> <span class="font-mono text-slate-400">/keys</span> ' +
            '<span class="font-mono text-slate-400">/storage</span> <span class="font-mono text-slate-400">/agents</span> — or just ask a question.');
        } else await sideCommand('nova ' + target.arg.replace(/^\//, ''));
      } else if (target.kind === 'agent') await sideAgent(target.arg);
      else {
        await sideChat(text, context);
        sideHist.push({ role: 'assistant', content: 'done' });
      }
    } catch (err) {
      bubble('rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-200',
        esc((err && err.message) || 'Something went wrong'));
    }
    chatBusy = false;
    if (sideSend) sideSend.disabled = false;
    if (sideInput) sideInput.focus();
  }

  if (sideInput) {
    sideInput.addEventListener('input', () => {
      const text = sideInput.value.trim();
      if (!sideMode) return;
      if (!text) { sideMode.textContent = 'chat'; sideMode.className = 'font-mono text-[10px] text-slate-600'; return; }
      const match = MODES.find(([re]) => re.test(text));
      sideMode.textContent = match ? match[1] : 'chat';
      sideMode.className = 'truncate font-mono text-[10px] ' + (match ? match[2] : 'text-slate-600');
    });
    sideInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sideSendNow(); }
    });
  }
  if (sideSend) sideSend.addEventListener('click', sideSendNow);

  const sideToggle = $('term-side-toggle');
  if (sideToggle && side && sideBody) {
    sideToggle.addEventListener('click', () => {
      const collapsed = sideBody.classList.toggle('hidden');
      side.classList.toggle('xl:w-[380px]', !collapsed);
      side.classList.toggle('xl:w-[52px]', collapsed);
      side.classList.toggle('xl:w-[420px]', !collapsed);
      sideToggle.classList.toggle('rotate-180', collapsed);
      sideToggle.title = collapsed ? 'Expand the console panel' : 'Collapse the console panel';
      setTimeout(queueResize, 60);
    });
  }

  /* --------------------------- lifecycle --------------------------- */

  function cleanup() {
    detach();
    clearTimeout(resizeTimer);
  }
  const content = $('tab-content');
  if (content) {
    content.addEventListener('htmx:beforeSwap', () => { if (root.isConnected) cleanup(); });
    content.addEventListener('htmx:beforeCleanup', cleanup);
  }

  /* ------------------------------ init ------------------------------ */

  renderTabs();
  refreshState();
  setTimeout(resize, 120);
  icons();
})();
