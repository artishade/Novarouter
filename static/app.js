/* NovaRouter Python UI — shell behaviour + shared helpers.
 * Vanilla JS over HTMX; Alpine is available to tab modules for local state.
 */
(() => {
  'use strict';

  const store = {
    tab: 'overview',
    expanded: true,
    autoRefresh: false,
    base_url: null,
  };

  try { store.expanded = localStorage.getItem('nova.sidebar.expanded') !== '0'; } catch {}
  /* Auto-refresh is OFF by default (opt-in via the header toggle): periodic
   * live swaps used to close the on-screen keyboard while typing on mobile. */
  try { store.autoRefresh = localStorage.getItem('nova.autorefresh') === '1'; } catch {}

  /* ----------------------------- Sidebar ----------------------------- */

  function applySidebar() {
    document.body.classList.toggle('sb-expanded', store.expanded);
    document.body.classList.toggle('sb-collapsed', !store.expanded);
    document.querySelectorAll('#sidebar .nav-item').forEach((btn) => {
      const active = btn.dataset.tab === store.tab;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-current', active ? 'page' : 'false');
    });
    const activeBtn = document.querySelector(`#sidebar .nav-item[data-tab="${store.tab}"]`);
    if (activeBtn) {
      const t = document.getElementById('tab-title');
      const s = document.getElementById('tab-subtitle');
      if (t) t.textContent = activeBtn.dataset.label || activeBtn.dataset.tab;
      if (s) s.textContent = activeBtn.dataset.subtitle || '';
    }
  }

  function nav(tab) {
    if (!tab) return;
    store.tab = tab;
    applySidebar();
    const target = document.getElementById('tab-content');
    if (window.htmx && target) window.htmx.ajax('GET', '/partials/tab/' + tab, { target: '#tab-content', swap: 'innerHTML' });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /* ----------------------------- Clocks ----------------------------- */

  function tick() {
    const now = new Date();
    const c = document.getElementById('clock');
    if (c) c.textContent = now.toLocaleTimeString('en-US', { hour12: false });
    const d = document.getElementById('dhaka-clock');
    if (d) d.textContent = now.toLocaleTimeString('en-US', { hour12: false, timeZone: 'Asia/Dhaka' });
  }

  /* ----------------------------- Toasts ----------------------------- */

  function toast(message, type = 'info') {
    const wrap = document.getElementById('toaster');
    if (!wrap || !message) return;
    const el = document.createElement('div');
    el.className = `nova-toast nova-toast-${type}`;
    el.setAttribute('role', 'status');
    el.innerHTML =
      '<span class="nova-toast-dot mt-1 inline-block size-1.5 shrink-0 rounded-full"></span>' +
      '<span class="min-w-0 flex-1 break-words"></span>';
    el.querySelector('span:nth-child(2)').textContent = String(message);
    const dismiss = () => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 160);
    };
    el.addEventListener('click', dismiss);
    wrap.appendChild(el);
    while (wrap.children.length > 5) wrap.firstElementChild.remove();
    setTimeout(dismiss, 4600);
  }

  /* ----------------------------- Clipboard / refresh / downloads ----------------------------- */

  async function copy(text, message = 'Copied to clipboard') {
    try {
      await navigator.clipboard.writeText(text);
      toast(message, 'success');
    } catch {
      toast('Clipboard is not available', 'error');
    }
  }

  function refresh() {
    if (window.htmx) window.htmx.trigger(document.body, 'nova:refresh');
  }

  async function downloadFile(id, name) {
    try {
      const res = await fetch('/api/admin/storage/files/' + id);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      const bin = atob(json.file.data_base64);
      const bytes = Uint8Array.from(bin, (ch) => ch.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: json.file.mime || 'application/octet-stream' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = name || json.file.name || 'download';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      toast('Download failed', 'error');
    }
  }

  function copyFromSelector(sel) {
    const el = document.querySelector(sel);
    if (el && el.textContent) copy(el.textContent.trim(), 'Base URL copied to clipboard');
  }

  window.NovaUI = { nav, toast, copy, refresh, downloadFile, copyFromSelector, store };

  /* ----------------------------- HTMX wiring ----------------------------- */

  let inflight = 0;
  document.addEventListener('htmx:beforeRequest', () => {
    inflight += 1;
    document.getElementById('refresh-btn')?.classList.add('nova-spin');
  });
  const settle = () => {
    inflight = Math.max(0, inflight - 1);
    if (inflight === 0) document.getElementById('refresh-btn')?.classList.remove('nova-spin');
  };
  document.addEventListener('htmx:afterRequest', settle);
  document.addEventListener('htmx:timeout', settle);
  document.addEventListener('htmx:sendError', settle);

  document.addEventListener('htmx:afterSwap', (e) => {
    if (e.target && e.target.id === 'tab-content') window.scrollTo({ top: 0 });
    if (window.lucide) window.lucide.createIcons();
    applySidebar();
  });

  document.addEventListener('nova:toast', (e) => {
    const d = e.detail || {};
    if (typeof d === 'string') return toast(d, 'info');
    toast(d.message || 'Done', d.type || 'info');
  });

  /* ----------------------------- Global listeners ----------------------------- */

  document.addEventListener('click', (e) => {
    const navBtn = e.target.closest('#sidebar .nav-item');
    if (navBtn) { e.preventDefault(); nav(navBtn.dataset.tab); return; }
    const copyBtn = e.target.closest('[data-copy-selector]');
    if (copyBtn) { e.preventDefault(); copyFromSelector(copyBtn.dataset.copySelector); }
  });

  document.getElementById('sidebar-toggle')?.addEventListener('click', () => {
    store.expanded = !store.expanded;
    try { localStorage.setItem('nova.sidebar.expanded', store.expanded ? '1' : '0'); } catch {}
    applySidebar();
  });
  document.getElementById('sb-expand-bottom')?.addEventListener('click', () => {
    store.expanded = true;
    try { localStorage.setItem('nova.sidebar.expanded', '1'); } catch {}
    applySidebar();
  });

  const ar = document.getElementById('auto-refresh');
  function applyAr() {
    const on = ar?.checked ?? store.autoRefresh;
    const dot = document.getElementById('ar-dot');
    const ping = document.getElementById('ar-ping');
    if (dot) { dot.classList.toggle('bg-emerald-400', on); dot.classList.toggle('bg-slate-600', !on); }
    if (ping) ping.classList.toggle('hidden', !on);
  }
  if (ar) {
    ar.checked = store.autoRefresh;
    ar.addEventListener('change', () => {
      store.autoRefresh = ar.checked;
      try { localStorage.setItem('nova.autorefresh', ar.checked ? '1' : '0'); } catch {}
      applyAr();
    });
    applyAr();
  }

  document.getElementById('refresh-btn')?.addEventListener('click', () => {
    refresh();
  });

  let lastActivity = 0;
  const bumpActivity = () => { lastActivity = Date.now(); };
  ['keydown', 'input', 'pointerdown', 'touchstart'].forEach((evt) =>
    document.addEventListener(evt, bumpActivity, { capture: true, passive: true }));

  const isEditing = () => {
    const a = document.activeElement;
    if (!a) return false;
    const tag = (a.tagName || '').toUpperCase();
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || a.isContentEditable === true;
  };

  /* Live polling — 5s, only when auto-refresh is explicitly on, the tab has
   * live widgets, and the user is not typing/interacting. Every guard exists
   * to guarantee a background refresh can never steal focus or close the
   * mobile keyboard mid-sentence.
   *
   * Dispatches `nova:auto-refresh`, NOT `nova:refresh`: the Models and
   * Providers tabs never listen to the polling event — a background swap
   * used to abort a running Sync Catalogue / Health check sweep mid-flight.
   * Those two tabs refresh on user actions only (manual refresh button,
   * post-mutation HX-Trigger from the server). */
  const AUTO_REFRESH_EXEMPT_TABS = new Set(['models', 'providers']);
  setInterval(() => {
    if (!store.autoRefresh) return;
    if (document.hidden) return;
    if (AUTO_REFRESH_EXEMPT_TABS.has(store.tab)) return;
    if (inflight > 0) return;
    if (isEditing()) return;
    if (Date.now() - lastActivity < 8000) return;
    if (!document.querySelector('#tab-content [data-live]')) return;
    if (window.htmx) window.htmx.trigger(document.body, 'nova:auto-refresh');
  }, 5000);

  /* Base URL chip (meta descriptor) */
  fetch('/api/admin/meta')
    .then((r) => (r.ok ? r.json() : null))
    .then((meta) => {
      if (meta && meta.base_url) {
        store.base_url = meta.base_url;
        const span = document.getElementById('base-url');
        /* The chip itself stays `hidden md:flex` (template) — removing the
           hidden class un-hid it on phones too and caused horizontal
           overflow at 390px. Only the text needs populating. */
        if (span) span.textContent = meta.base_url;
      }
    })
    .catch(() => {});

  /* Init */
  document.addEventListener('DOMContentLoaded', () => {
    applySidebar();
    if (window.lucide) window.lucide.createIcons();
    tick();
    setInterval(tick, 1000);
  });
})();
