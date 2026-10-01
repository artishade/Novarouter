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

  const phone = window.matchMedia('(max-width: 767px)').matches;
  if (XTERM) {
    term = new window.Terminal({
      fontFamily: "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
      // A phone shell has to be readable at a glance, and the A-/A+ keys take
      // it from there (saved per device).
      fontSize: Number(localStorage.getItem('nova.term.fontsize') || (phone ? 14 : 12.5)),
      lineHeight: phone ? 1.35 : 1.25,
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

  /* ------------------------- text selection ------------------------- */
  /* xterm paints every cell on a <canvas>, so the browser has no DOM text to
   * select: a finger drag highlights nothing and there is no native
   * cut/copy/cut menu — which is why output could not be copied on a phone at
   * all. Long-pressing the terminal now selects the word under the finger
   * (a second long press inside a selection takes the whole line), which the
   * Copy button then copies. Desktop keeps xterm's own mouse selection. */
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

  function cellAt(clientX, clientY) {
    if (!term) return null;
    const el = view.querySelector('.xterm-screen') || term.element || view;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const buffer = term.buffer.active;
    const col = clamp(Math.floor(((clientX - rect.left) / rect.width) * term.cols), 0, term.cols - 1);
    // xterm's selection API addresses absolute buffer rows, so the scroll
    // offset (viewportY) has to be folded in.
    const rowInView = clamp(Math.floor(((clientY - rect.top) / rect.height) * term.rows), 0, term.rows - 1);
    const row = clamp(buffer.viewportY + rowInView, 0, Math.max(0, buffer.length - 1));
    return { col, row };
  }

  function selectAt(clientX, clientY) {
    const cell = cellAt(clientX, clientY);
    if (!cell) return;
    const buffer = term.buffer.active;
    const line = buffer.getLine(cell.row);
    const text = line ? line.translateToString(true) : '';
    const current = term.getSelectionPosition ? term.getSelectionPosition() : null;
    const insideCurrent = current
      && current.start.y === cell.row
      && cell.col >= current.start.x && cell.col <= current.end.x;
    if (insideCurrent) {
      term.selectLines(cell.row, cell.row);          // widen: whole line
      toast('Line selected — tap Copy', 'info');
      return;
    }
    const isWord = (ch) => !!ch && !/\s/.test(ch);
    let start = Math.min(cell.col, text.length);
    let end = start;
    while (start > 0 && isWord(text[start - 1])) start--;
    while (end < text.length && isWord(text[end])) end++;
    if (end === start) {
      term.selectLines(cell.row, cell.row);
      toast('Line selected — tap Copy', 'info');
      return;
    }
    term.select(start, cell.row, end - start);
    toast('"' + text.slice(start, end).slice(0, 24) + '" selected — tap Copy', 'info');
  }

  let pressTimer = null;
  let pressFrom = null;
  let pressHandledAt = 0;
  function clearPress() { clearTimeout(pressTimer); pressTimer = null; pressFrom = null; }

  if (view) {
    view.addEventListener('pointerdown', (e) => {
      if (!term || e.pointerType === 'mouse') return;
      pressFrom = { x: e.clientX, y: e.clientY };
      clearPress();
      pressTimer = setTimeout(() => {
        pressHandledAt = Date.now();
        pressTimer = null;
        // The callout Android/iOS raise over a canvas has nothing to select,
        // and the tap that follows would raise the keyboard over the
        // selection — so take the word now and let the Copy button do the rest.
        const active = document.activeElement;
        if (active && active !== document.body && view.contains(active)) {
          active.blur();          // drop the keyboard, keep the selection readable
        }
        selectAt(e.clientX, e.clientY);
      }, 420);
    }, { passive: true });
    view.addEventListener('pointermove', (e) => {
      if (!pressFrom) return;
      if (Math.abs(e.clientX - pressFrom.x) > 12 || Math.abs(e.clientY - pressFrom.y) > 12) clearPress();
    }, { passive: true });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach((evt) =>
      view.addEventListener(evt, clearPress, { passive: true }));
    // Suppress the native long-press menu over the terminal: there is no DOM
    // text there, so it can only cover the line the user just selected.
    view.addEventListener('contextmenu', (e) => {
      const touched = e.pointerType === 'touch' || mqMobile.matches;
      if (!touched) return;
      e.preventDefault();
      if (Date.now() - pressHandledAt > 1200) selectAt(e.clientX, e.clientY);
    });
  }

  function selectAllOutput() {
    if (!term) return;
    term.selectAll();
    toast('Output selected — tap Copy', 'info');
  }

  async function pasteClipboard() {
    const live = session(state.active);
    if (!live || live.closed) { toast('Open a terminal session first', 'info'); return; }
    try {
      const text = await navigator.clipboard.readText();
      if (!text) { toast('Clipboard is empty', 'info'); return; }
      send(text.replace(/\r\n/g, '\r').replace(/\n$/, ''));
      toast('Pasted', 'success');
    } catch (e) {
      toast('This browser will not hand over the clipboard — long-press the input instead', 'info');
    }
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
    state.sessions.forEach((s) => {        const active = state.active === s.id;
        const isAgent = String(s.label || '').split('·')[0].trim() === 'agent';
        tabs.push(
          '<div class="term-tab group ' + (active ? 'is-active' : '') + ' flex h-7 max-w-[210px] shrink-0 items-center gap-1 rounded-md pl-2 pr-1 text-[11px]" role="presentation">' +
          '<button type="button" role="tab" data-session="' + esc(s.id) + '" title="' + esc(s.cwd) + '" aria-selected="' + active + '" ' +
            'class="flex min-w-0 items-center gap-1.5 text-left">' +
            '<span class="size-1.5 shrink-0 rounded-full ' + (s.closed ? 'bg-slate-600' : (isAgent ? 'bg-purple-400 term-pulse' : 'bg-emerald-400 term-pulse')) + '"></span>' +
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
    renderPromptBadge();
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

  /* Keystrokes go over the wire, so they used to be one POST per key: the
   * browser opened a parallel request per character and the PTY could receive
   * them out of order. On a phone keyboard (fast repeats, slow link) that
   * showed up as garbled words with the spaces landing in the wrong place —
   * `ls -la` arriving as `ls-la`. One in-flight request at a time, and every
   * byte pressed meanwhile is coalesced into the next one, so the shell sees
   * exactly what was typed, in order, with one request per burst instead of
   * one per key. */
  let sendQueue = '';
  let sendTarget = null;
  let sendTimer = null;
  let sendBusy = false;

  function send(data) {
    const live = session(state.active);
    if (!live || live.closed || !data) return;
    if (sendTarget && sendTarget !== live.id) sendQueue = '';  // session switched mid-flight
    sendTarget = live.id;
    sendQueue += data;
    if (sendBusy) return;                 // flushed when the request settles
    clearTimeout(sendTimer);
    sendTimer = setTimeout(flushSend, 12);
  }

  async function flushSend() {
    if (sendBusy || !sendQueue) return;
    const id = sendTarget;
    const payload = sendQueue;
    sendQueue = '';
    sendBusy = true;
    try {
      await fetch('/api/admin/terminal/pty/input', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: id, data: payload }),
      });
    } catch (e) { /* dropped keystrokes are recoverable by retyping */ } finally {
      sendBusy = false;
      if (sendQueue) flushSend();
    }
  }

  function dropQueuedInput() {
    sendQueue = '';
    sendTarget = null;
    clearTimeout(sendTimer);
  }
  /* ------------------------ mobile extra-key grid ------------------------ */
  /* A phone keyboard hides every modifier, so the grid underneath the shell
   * carries what a real terminal emulator provides: arrows, Tab, Esc,
   * Ctrl/Alt modifiers, Home/End/PageUp/PageDown, space, backspace and the
   * shell control keys. Three layouts, rendered from JS so the user can switch
   * them on the device (the ⇄ button) instead of it being baked into HTML. */
  const KEY_BYTES = {
    Up: '\x1b[A', Down: '\x1b[B', Right: '\x1b[C', Left: '\x1b[D',
    Home: '\x1b[H', End: '\x1b[F', PageUp: '\x1b[5~', PageDown: '\x1b[6~',
    Tab: '\t', Esc: '\x1b', Enter: '\r', Backspace: '\x7f',
  };
  const ctrlByte = (ch) => {
    const c = ch.toLowerCase();
    if (c >= 'a' && c <= 'z') return String.fromCharCode(c.charCodeAt(0) - 96);
    if (c === '@' || c === ' ') return '\x00';
    if (c === '?') return '\x7f';
    if (c === '[') return '\x1b';
    if (c === '\\') return '\x1c';
    if (c === ']') return '\x1d';
    if (c === '^') return '\x1e';
    if (c === '_') return '\x1f';
    return '';
  };
  // [kind, ...] -> kind decides how the key behaves
  //   key  = named control key      text = literal characters
  //   ctrl = an explicit control key (^A etc.)   mod = sticky modifier
  //   paste / sel = clipboard in, whole output selected
  const KEY_LAYOUTS = [
    {
      id: 'keys', label: 'keys',
      keys: [
        ['key', 'Esc', 'ESC'], ['text', '/'], ['text', '-'],
        ['key', 'Home', 'HOME'], ['key', 'Up', '\u2191'], ['key', 'End', 'END'], ['key', 'PageUp', 'PGUP'],
        ['key', 'Tab', 'TAB'], ['mod', 'ctrl', 'CTRL'], ['mod', 'alt', 'ALT'],
        ['key', 'Left', '\u2190'], ['key', 'Down', '\u2193'], ['key', 'Right', '\u2192'], ['key', 'PageDown', 'PGDN'],
      ],
    },
    {
      id: 'edit', label: 'edit',
      keys: [
        ['text', ' ', 'SPACE'], ['key', 'Backspace', '\u232b'], ['sel'], ['paste'],
        ['ctrl', 'a', '^A'], ['ctrl', 'e', '^E'], ['ctrl', 'k', '^K'],
        ['ctrl', 'u', '^U'], ['ctrl', 'w', '^W'], ['ctrl', 'z', '^Z'],
        ['ctrl', 'd', '^D'], ['mod', 'ctrl', 'CTRL'], ['mod', 'alt', 'ALT'],
      ],
    },
    {
      id: 'sym', label: 'symbols',
      keys: [
        ['text', '~'], ['text', '|'], ['text', '$'], ['text', '"'], ['text', "'"], ['text', '`'],
        ['text', '*'], ['text', '+'], ['text', '_'], ['text', '%'], ['text', '#'], ['text', '@'], ['text', '^'],
        ['text', '='],
      ],
    },
  ];
  const keyRow = $('ws-keyrow');
  const keysLabel = $('term-keys-label');
  let stickyMod = null;          // 'ctrl' | 'alt' | null — one-shot modifier
  let layoutIndex = 0;
  try {
    const saved = Number(localStorage.getItem('nova.term.keylayout') || 0);
    if (Number.isFinite(saved) && saved >= 0 && saved < KEY_LAYOUTS.length) layoutIndex = saved;
  } catch (e) { /* first run / private mode */ }

  function renderKeyRow() {
    if (!keyRow) return;
    const layout = KEY_LAYOUTS[layoutIndex];
    keyRow.innerHTML = layout.keys.map((key) => {
      const kind = key[0];
      if (kind === 'text') {
        return '<button type="button" class="ws-key' + (key[2] === 'SPACE' ? ' is-wide' : '') +
          '" data-text="' + esc(key[1]) + '" title="' + esc(key[2] || key[1]) + '">' + esc(key[2] || key[1]) + '</button>';
      }
      if (kind === 'ctrl') {
        return '<button type="button" class="ws-key" data-ctrl="' + key[1] + '" title="Ctrl+' +
          key[1].toUpperCase() + '">' + esc(key[2]) + '</button>';
      }
      if (kind === 'mod') {
        return '<button type="button" class="ws-key' + (stickyMod === key[1] ? ' is-sticky' : '') +
          '" data-mod="' + key[1] + '" title="Arm ' + key[2] + ' for the next key">' + esc(key[2]) + '</button>';
      }
      if (kind === 'paste') {
        return '<button type="button" class="ws-key" data-paste="1" title="Paste the clipboard into the shell">Paste</button>';
      }
      if (kind === 'sel') {
        return '<button type="button" class="ws-key" data-select="1" title="Select the whole output, then tap Copy">Sel</button>';
      }
      return '<button type="button" class="ws-key" data-key="' + key[1] + '" title="' + esc(key[2] || key[1]) +
        '">' + esc(key[2] || key[1]) + '</button>';
    }).join('');
    if (keysLabel) keysLabel.textContent = layout.label;
    renderPromptBadge();
  }

  function renderPromptBadge() {
    const badge = $('term-prompt-badge');
    if (!badge) return;
    const live = session(state.active);
    const where = live ? shortCwd(live.cwd) : '~';
    badge.textContent = (stickyMod ? stickyMod.toUpperCase() + ' ' : '') + where + ' $';
    badge.title = live ? live.cwd : 'No session';
  }

  function sendKey(bytes) {
    if (!bytes) return false;
    send(bytes);
    if (term) term.focus();
    return true;
  }

  function clearSticky() {
    if (!stickyMod) return;
    stickyMod = null;
    if (keyRow) keyRow.querySelectorAll('.ws-key.is-sticky').forEach((b) => b.classList.remove('is-sticky'));
    renderPromptBadge();
  }

  if (keyRow) {
    renderKeyRow();
    keyRow.addEventListener('click', (e) => {
      const btn = e.target.closest('.ws-key');
      if (!btn) return;
      e.preventDefault();
      const live = session(state.active);
      if (!live || live.closed) { toast('Open a terminal session first', 'info'); return; }
      if (btn.dataset.mod) {
        stickyMod = stickyMod === btn.dataset.mod ? null : btn.dataset.mod;
        btn.classList.toggle('is-sticky', stickyMod === btn.dataset.mod);
        renderPromptBadge();
        return;
      }
      if (btn.dataset.paste) { pasteClipboard(); return; }
      if (btn.dataset.select) { selectAllOutput(); return; }
      if (btn.dataset.ctrl) {
        const byte = ctrlByte(btn.dataset.ctrl);
        clearSticky();
        sendKey(byte);
        return;
      }
      if (btn.dataset.text) {
        const ch = btn.dataset.text;
        const bytes = stickyMod === 'ctrl' ? ctrlByte(ch) : stickyMod === 'alt' ? '\x1b' + ch : ch;
        clearSticky();
        sendKey(bytes);
        return;
      }
      const name = btn.dataset.key;
      clearSticky();
      sendKey(KEY_BYTES[name] || '');
    });
    // A second tap on the same layout button cycles to the next one.
    const nextBtn = $('term-keys-next');
    if (nextBtn) {
      nextBtn.addEventListener('click', () => {
        layoutIndex = (layoutIndex + 1) % KEY_LAYOUTS.length;
        try { localStorage.setItem('nova.term.keylayout', String(layoutIndex)); } catch (e) { /* private mode */ }
        renderKeyRow();
      });
    }
  }

  /* ------------------ mobile command + prompt bars ------------------ */
  /* Everything the desktop header hid is one tap away here, and the prompt
   * bar doubles as a status line: it shows the live cwd and an armed
   * modifier, so it is obvious what the next key press will send. */
  function requireSession() {
    const live = session(state.active);
    if (!live || live.closed) { toast('Open a terminal session first', 'info'); return null; }
    return live;
  }
  const interruptBtn = $('term-interrupt');
  if (interruptBtn) {
    interruptBtn.addEventListener('click', () => {
      if (!requireSession()) return;
      sendKey('\x03');
      toast('Sent Ctrl+C', 'info');
    });
  }
  const kbdBtn = $('term-kbd');
  if (kbdBtn) {
    kbdBtn.addEventListener('click', () => {
      if (!term) return;
      const active = document.activeElement;
      const inside = active && active !== document.body && view.contains(active);
      if (inside) { active.blur(); kbdBtn.classList.remove('is-on'); }
      else { term.focus(); kbdBtn.classList.add('is-on'); }
    });
  }
  ['term-copy-m', 'term-clear-m', 'term-font-down-m', 'term-font-up-m'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('click', () => {
      if (id === 'term-copy-m') return copyOutput();
      if (id === 'term-clear-m') return clearScreen();
      setFont(id === 'term-font-up-m' ? 0.5 : -0.5);
    });
  });
  // Both "Environment Setup" and the gear open the same drawer.
  ['term-envsetup', 'term-config-gear'].forEach((id) => {
    const el = $(id);
    const drawer = $('ws-config');
    if (!el || !drawer) return;
    el.addEventListener('click', () => { drawer.hidden = false; icons(); });
  });


  /* ------------------ mobile sheets (corner FABs) ------------------ */
  const mqMobile = window.matchMedia('(max-width: 767px)');
  const fabChat = $('ws-fab-chat');
  const fabTerm = $('ws-fab-term');
  const landing = $('ws-landing');
  const sheetChat = document.querySelector('[data-sheet="chat"]');
  const sheetTerm = document.querySelector('[data-sheet="term"]');
  let openSheet = null; // 'chat' | 'term' | null (mobile only)

  function applySheets() {
    if (!sheetChat || !sheetTerm) return;
    // Leaving mobile: hand the layout back to the stylesheet entirely.
    if (!mqMobile.matches) {
      sheetChat.style.display = '';
      sheetTerm.style.display = '';
      if (landing) landing.classList.remove('is-hidden');
      if (fabChat) fabChat.classList.remove('is-open');
      if (fabTerm) fabTerm.classList.remove('is-open');
      return;
    }
    sheetChat.style.display = openSheet === 'chat' ? 'flex' : 'none';
    sheetTerm.style.display = openSheet === 'term' ? 'flex' : 'none';
    // With no panel open the landing card is the screen — big tap targets
    // instead of an empty dark page.
    if (landing) landing.classList.toggle('is-hidden', !!openSheet);
    // Both FABs stay reachable so the user can hop between tools; the one
    // whose panel is open dims to read as "close".
    if (fabChat) fabChat.classList.toggle('is-open', openSheet === 'chat');
    if (fabTerm) fabTerm.classList.toggle('is-open', openSheet === 'term');
    if (openSheet) {
      setTimeout(() => { queueResize(); }, 80);
      if (openSheet === 'term' && term) term.focus();
    }
  }
  function toggleSheet(which) {
    openSheet = openSheet === which ? null : which;
    applySheets();
    if (!openSheet) refreshState();
  }
  function closeSheet() { openSheet = null; applySheets(); refreshState(); }
  if (fabChat) fabChat.addEventListener('click', () => toggleSheet('chat'));
  if (fabTerm) fabTerm.addEventListener('click', () => toggleSheet('term'));
  // Landing buttons (mobile only) open a panel directly.
  if (landing) {
    landing.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-sheet-open]');
      if (btn) toggleSheet(btn.getAttribute('data-sheet-open'));
    });
  }
  // Close handles: the chat panel's collapse button (desktop) and the
  // terminal sheet's own close button (mobile only).
  document.querySelectorAll('.ws-sheet-close').forEach((btn) => btn.addEventListener('click', closeSheet));
  const termSheetClose = $('term-sheet-close');
  if (termSheetClose) termSheetClose.addEventListener('click', closeSheet);
  if (mqMobile.addEventListener) mqMobile.addEventListener('change', applySheets);
  // Phones land on the picker card; nothing is open until a choice is made.
  applySheets();

  function attach(id) {
    dropQueuedInput();          // never replay half-typed keys into another tab
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
    // Not `view.offsetParent`: on phones the sheet is position:fixed, and
    // offsetParent is always null for fixed elements — that made fit() a
    // no-op and left xterm at its 1x1 default. clientWidth/Height is 0 only
    // when the panel is genuinely not laid out (hidden tab / closed sheet).
    if (!view.clientWidth || !view.clientHeight) return;
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

  /* The agent types its shell steps into a real PTY tab of its own — one tab
   * per task, labelled `agent · <goal>`. Attaching keeps that tab streaming
   * (its output is what you can read back), and the report bubble carries a
   * "watch" button instead of hijacking the phone's screen. */
  let agentTabId = null;
  function newestAgentTab() {
    return (state.sessions || [])
      .filter((s) => !s.closed && String(s.label || '').split('\u00b7')[0].trim() === 'agent')
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0] || null;
  }
  async function watchAgentTerminal() {
    await refreshState();
    const agent = newestAgentTab();
    if (!agent) return null;
    if (agentTabId !== agent.id) agentTabId = agent.id;
    if (state.active !== agent.id) {
      attach(agent.id);
      fetch('/api/admin/terminal/pty/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: agent.id }),
      }).catch(() => {});
    }
    return agent;
  }
  function showAgentTab() {
    if (!agentTabId) { toast('The agent has not opened a terminal tab yet', 'info'); return; }
    const live = session(agentTabId);
    if (!live || live.closed) { toast('That agent tab was closed', 'info'); return; }
    attach(agentTabId);
    if (mqMobile.matches && openSheet !== 'term') { openSheet = 'term'; applySheets(); }
  }

  /* A task report the way a person would read it: what was asked, what ran,
   * whether it worked, how long it took. The raw shell scrollback stays in the
   * agent's terminal tab — none of it is pasted into the chat. */
  const ANSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;
  const cleanText = (s) => String(s == null ? '' : s).replace(ANSI_RE, '').replace(/\s+$/, '');
  // The agent's final summary is markdown; escaped first, then only the two
  // inline marks it actually uses are turned into tags.
  const inlineMd = (s) => esc(cleanText(s))
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  function outcomeOf(step) {
    // The detail is "<command echo>\n<output>\n[exit n]" — keep the last
    // meaningful line so the report reads like a result, not a log dump.
    const lines = cleanText(step.detail).split('\n').map((l) => l.trim()).filter(Boolean);
    const last = lines[lines.length - 1] || '';
    const marked = last.match(/^\[exit (-?\d+)\]$/);
    const body = marked ? lines.slice(0, -1).pop() || '' : last;
    return { text: cleanText(body).slice(0, 160), exit: marked ? marked[1] : null };
  }
  function durationOf(task) {
    if (!task.started_at || !task.finished_at) return '';
    const secs = Math.max(0, Math.round((task.finished_at - task.started_at) / 1000));
    return secs < 60 ? secs + 's' : Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
  }
  function reportHtml(task, running) {
    const steps = task.steps || [];
    const done = steps.filter((s) => s.status !== 'error').length;
    const failed = steps.filter((s) => s.status === 'error');
    const headline = running
      ? 'Working \u00b7 step ' + Math.max(done, 1) + ' of ' + (task.max_steps || 8)
      : (task.status === 'completed'
        ? 'Task complete'
        : (task.status === 'cancelled' ? 'Task cancelled' : 'Task failed'));
    const tone = running ? 'text-sky-300' : (task.status === 'completed' ? 'text-emerald-400' : 'text-rose-300');
    const rows = steps.map((s) => {
      const bad = s.status === 'error';
      const res = outcomeOf(s);
      const bits = [];
      if (res.exit !== null) bits.push('exit ' + res.exit);
      if (s.latency_ms) bits.push(Math.round(s.latency_ms / 100) / 10 + 's');
      return '<li class="border-l-2 ' + (bad ? 'border-rose-500/50' : 'border-emerald-500/40') + ' pl-2">' +
        '<div class="flex items-baseline justify-between gap-2">' +
        '<span class="' + (bad ? 'text-rose-200' : 'text-slate-200') + '">' + esc(s.title || s.action) + '</span>' +
        '<span class="shrink-0 font-mono text-[10px] text-slate-600">' + esc(bits.join(' \u00b7 ')) + '</span></div>' +
        (res.text && !bad ? '<div class="truncate font-mono text-[10px] text-slate-500">' + esc(res.text) + '</div>' : '') +
        (bad && res.text ? '<div class="truncate font-mono text-[10px] text-rose-300/80">' + esc(res.text) + '</div>' : '') +
        '</li>';
    }).join('');
    const why = task.error ? '<div class="mt-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">' + esc(cleanText(task.error)) + '</div>' : '';
    const summary = !running && task.summary
      ? '<div class="md-body mt-2 whitespace-pre-wrap border-t border-slate-800 pt-2 text-slate-300">' + inlineMd(cleanText(task.summary).slice(0, 1200)) + '</div>'
      : '';
    const meta = '<div class="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-slate-600">' +
      (steps.length ? '<span>' + done + '/' + steps.length + ' steps ok</span>' : '') +
      (failed.length ? '<span class="text-rose-400">' + failed.length + ' failed</span>' : '') +
      (durationOf(task) ? '<span>' + esc(durationOf(task)) + '</span>' : '') +
      '<button type="button" data-watch-agent class="ml-auto inline-flex items-center gap-1 rounded border border-purple-500/40 px-1.5 py-0.5 text-purple-200 hover:bg-purple-500/10">' +
      '<i data-lucide="terminal" class="size-3"></i>watch</button></div>';
    return '<div class="mb-1 flex items-center gap-1.5 font-semibold uppercase tracking-wider ' + tone + '">' +
      '<i data-lucide="' + (running ? 'loader-2' : (task.status === 'completed' ? 'check-circle-2' : 'circle-alert')) +
      '" class="size-3' + (running ? ' animate-spin' : '') + '"></i>' + esc(headline) + '</div>' +
      (rows ? '<ol class="space-y-1.5">' + rows + '</ol>' : '<div class="text-slate-500">queued\u2026</div>') +
      meta + why + summary;
  }

  async function sideAgent(goal) {
    agentTabId = null;
    const report = bubble('rounded-xl border border-purple-500/30 bg-purple-500/10 px-3 py-2 text-[12px]',
      '<div class="mb-1 flex items-center gap-1.5 font-semibold uppercase tracking-wider text-sky-300">' +
      '<i data-lucide="loader-2" class="size-3 animate-spin"></i>Starting</div>' +
      '<div class="text-slate-300">' + esc(goal) + '</div>');
    const render = (task, running) => {
      report.innerHTML = '<div class="mb-1 text-[11px] text-slate-400">' + esc(goal) + '</div>' + reportHtml(task, running);
      icons();
    };
    report.addEventListener('click', (e) => {
      if (e.target.closest('[data-watch-agent]')) showAgentTab();
    });
    try {
      const res = await fetch('/api/agent/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal, max_steps: 8 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.error) || 'HTTP ' + res.status);
      await watchAgentTerminal();
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const poll = await fetch('/api/agent/tasks/' + encodeURIComponent(data.id));
        if (!poll.ok) continue;
        const task = await poll.json();
        const running = task.status === 'queued' || task.status === 'running';
        render(task, running);
        if (!running) return;
        // The tab appears with the task's first shell step, not at submit time.
        await watchAgentTerminal();
      }
    } catch (err) {
      report.innerHTML = '<div class="mb-1 font-semibold uppercase tracking-wider text-rose-300">Agent could not finish</div>' +
        '<div class="text-rose-200">' + esc((err && err.message) || 'unknown error') + '</div>';
      icons();
    }
  }

  /* ------------------- chat target pickers ------------------- */
  const modelSel = $('ws-model');
  const effortSel = $('ws-effort');

  async function loadPickers() {
    if (!modelSel) return;
    try {
      const res = await fetch('/api/build/models');
      if (!res.ok) return;
      const data = await res.json();
      const chosen = modelSel.value || 'auto';
      const parts = ['<option value="auto">model: auto — gateway routes</option>'];
      for (const g of data.groups || []) {
        parts.push('<optgroup label="' + esc(g.provider_name) + '">');
        for (const m of g.models || []) {
          const tag = m.is_free ? ' · FREE' : (m.reasoning ? ' · reasoning' : '');
          parts.push('<option value="' + esc(m.exposed_id) + '">' + esc(m.display_name) + tag + '</option>');
        }
        parts.push('</optgroup>');
      }
      modelSel.innerHTML = parts.join('');
      // keep the user's previous choice when it still exists
      if ([...modelSel.options].some((o) => o.value === chosen)) modelSel.value = chosen;
    } catch (e) { /* picker stays on auto */ }
  }
  if (modelSel) loadPickers();
  $('ws-pickers-refresh')?.addEventListener('click', () => {
    loadPickers();
    toast('Model list reloaded', 'info');
  });

  async function sideChat(text, context) {
    const holder = bubble('rounded-xl border border-slate-800 bg-[#0d1322] px-3 py-2 text-[12px] text-slate-200',
      '<div class="md-body whitespace-pre-wrap"><span class="nova-caret text-emerald-400">▌</span></div>');
    const body = holder.querySelector('.md-body');
    const payload = {
      model: (modelSel && modelSel.value) || 'auto',
      stream: true,
      messages: context.concat([{ role: 'user', content: text }]),
    };
    const effort = effortSel && effortSel.value;
    if (effort) payload.reasoning_effort = effort;   // passes through the gateway to the upstream
    try {
      const res = await fetch('/api/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
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

  /* --------------------- soft keyboard inset (phones) --------------------- */
  /* A phone keyboard never moves a position:fixed sheet — it just paints over
   * the bottom of the viewport, which is exactly where the helper-key row and
   * the status footer live. So they ended up underneath the keyboard with no
   * way to reach them. Publish the covered height as --kb and let the
   * stylesheet lift the sheet and the corner FABs by that much.
   *
   * The value self-normalises across engines: with
   * `interactive-widget=resizes-content` (Chrome) the layout viewport already
   * shrinks, so the measured inset is 0; on iOS, which ignores that flag, the
   * inset is the real keyboard height. Either way the key row ends up sitting
   * just above the keyboard. */
  const vv = window.visualViewport;
  function syncKeyboardInset() {
    const inset = vv
      ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop))
      : 0;
    document.documentElement.style.setProperty('--kb', inset + 'px');
  }
  if (vv) {
    vv.addEventListener('resize', syncKeyboardInset);
    vv.addEventListener('scroll', syncKeyboardInset);
    window.addEventListener('orientationchange', onOrientationChange);
  }
  function onOrientationChange() { setTimeout(syncKeyboardInset, 250); }
  syncKeyboardInset();

  function cleanup() {
    detach();
    dropQueuedInput();
    clearTimeout(resizeTimer);
    if (mqMobile.removeEventListener) mqMobile.removeEventListener('change', applySheets);
    if (vv) {
      vv.removeEventListener('resize', syncKeyboardInset);
      vv.removeEventListener('scroll', syncKeyboardInset);
    }
    window.removeEventListener('orientationchange', onOrientationChange);
    document.documentElement.style.removeProperty('--kb');
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
