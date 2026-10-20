/**
 * AI Browser Hub — frontend.
 *
 * Transport:
 *  - /api/stream  : length-prefixed JPEG frames pushed by the server only when the page repaints.
 *  - /api/events  : Server-Sent Events (tabs, url, scroll, logs, chat messages, AI status, AI pointer).
 *  - /api/input   : ordered batches of raw mouse / wheel / keyboard events (one request in flight).
 */
'use strict';

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- state

const S = {
  viewport: { width: 1280, height: 800 },
  tabs: [],
  url: '',
  loading: false,
  scroll: null,
  pointerMode: 'control',
  aiRunning: false,
  config: null,
  lastSelection: '',
  frames: 0,
  connLive: false,
};
window.__aib = S;  // read-only diagnostics for tools/bench_smoothness.py

// ---------------------------------------------------------------- utils

async function api(path, body) {
  const opts = body === undefined
    ? { cache: 'no-store' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  return res.json();
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** Tiny, safe markdown: escape first, then **bold**, *italic*, `code`, links, "- " lists, newlines. */
function md(text) {
  let h = escapeHtml(text);
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<i>$2</i>')
    .replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)])/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  const lines = h.split('\n');
  let out = '', inList = false;
  for (const line of lines) {
    const m = line.match(/^\s*[-•]\s+(.*)$/);
    if (m) {
      if (!inList) { out += '<ul>'; inList = true; }
      out += `<li>${m[1]}</li>`;
    } else {
      if (inList) { out += '</ul>'; inList = false; }
      out += line + '<br>';
    }
  }
  if (inList) out += '</ul>';
  return out.replace(/(<br>)+$/, '');
}

const TOAST = { success: ['Toast--success', 'check'], error: ['Toast--error', 'x-circle-fill'], info: ['', 'info'] };
function toast(text, type = 'info', ms = 2600) {
  const box = $('toasts');
  const [cls, icon] = TOAST[type] || TOAST.info;
  const el = document.createElement('div');
  el.className = `Toast ${cls}`;
  el.innerHTML = `<span class="Toast-icon">${oi(icon)}</span><span class="Toast-content"></span>`;
  el.querySelector('.Toast-content').textContent = text;
  box.appendChild(el);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, ms);
}

function setSegmented(groupId, value) {
  document.querySelectorAll(`#${groupId} [data-mode]`).forEach((b) => {
    const on = b.dataset.mode === value;
    b.classList.toggle('selected', on);
    b.setAttribute('aria-checked', on ? 'true' : 'false');
  });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  }
}

// ---------------------------------------------------------------- Octicons (Primer)

/** Returns an inline Octicon <svg> string (16px). Icons come from vendor/octicons.js. */
function oi(name, cls = '') {
  const ic = (window.OCTICONS || {})[name];
  if (!ic) return '';
  return `<svg class="octicon ${cls}" width="${ic.w}" height="16" viewBox="0 0 ${ic.w} 16" aria-hidden="true">${ic.path}</svg>`;
}

function setIcon(svg, name) {
  const ic = (window.OCTICONS || {})[name];
  if (!svg || !ic) return;
  svg.setAttribute('viewBox', `0 0 ${ic.w} 16`);
  svg.setAttribute('width', ic.w);
  svg.setAttribute('height', 16);
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('octicon');
  svg.innerHTML = ic.path;
  svg.dataset.oi = name;
}

function hydrateIcons(root = document) {
  root.querySelectorAll('svg[data-oi]').forEach((s) => { if (!s.innerHTML) setIcon(s, s.dataset.oi); });
}
hydrateIcons();

// ---------------------------------------------------------------- color mode (Primer data-color-mode)

const THEMES = ['auto', 'light', 'dark'];
const THEME_META = { auto: ['device-desktop', 'theo hệ thống'], light: ['sun', 'sáng'], dark: ['moon', 'tối'] };
function applyTheme(mode) {
  document.documentElement.setAttribute('data-color-mode', mode);
  setIcon($('themeIcon'), THEME_META[mode][0]);
  $('btnTheme').title = `Giao diện: ${THEME_META[mode][1]} (bấm để đổi)`;
  try { localStorage.setItem('aib.theme', mode); } catch { /* ignore */ }
}
$('btnTheme').addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-color-mode') || 'auto';
  applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length]);
});
try { applyTheme(localStorage.getItem('aib.theme') || 'auto'); } catch { applyTheme('auto'); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const preview = (t, n = 50) => (t.length > n ? t.slice(0, n) + '…' : t);

// ---------------------------------------------------------------- screen: fit & render

const stage = $('stage');
const screen = $('screen');
const canvas = $('screenCanvas');
const ctx = canvas.getContext('2d', { alpha: false });
const overlay = $('screenOverlay');

function fitScreen() {
  const { width: vw, height: vh } = S.viewport;
  const pad = 20;
  const availW = Math.max(100, stage.clientWidth - pad);
  const availH = Math.max(100, stage.clientHeight - pad);
  const scale = Math.min(availW / vw, availH / vh);
  screen.style.width = Math.floor(vw * scale) + 'px';
  screen.style.height = Math.floor(vh * scale) + 'px';
}
new ResizeObserver(fitScreen).observe(stage);

let pendingFrame = null;
let decoding = false;

function queueFrame(bytes) {
  pendingFrame = bytes;  // keep only the newest frame; drop stale ones under load
  if (!decoding) decodeLoop();
}

async function decodeLoop() {
  decoding = true;
  while (pendingFrame) {
    const bytes = pendingFrame;
    pendingFrame = null;
    try {
      const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }));
      if (canvas.width !== bmp.width || canvas.height !== bmp.height) {
        canvas.width = bmp.width;
        canvas.height = bmp.height;
      }
      ctx.drawImage(bmp, 0, 0);
      bmp.close();
      S.frames++;
      S.totalFrames = (S.totalFrames || 0) + 1;
      overlay.hidden = true;
    } catch { /* corrupt frame: skip */ }
  }
  decoding = false;
}

async function runStream() {
  for (;;) {
    try {
      const res = await fetch('/api/stream', { cache: 'no-store' });
      if (!res.ok || !res.body) throw new Error('stream unavailable');
      const reader = res.body.getReader();
      let buf = new Uint8Array(0);
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const merged = new Uint8Array(buf.length + value.length);
        merged.set(buf);
        merged.set(value, buf.length);
        buf = merged;
        let off = 0;
        while (buf.length - off >= 8) {
          const dv = new DataView(buf.buffer, buf.byteOffset + off, 8);
          const len = dv.getUint32(0);
          if (buf.length - off < 8 + len) break;
          if (len > 0) queueFrame(buf.slice(off + 8, off + 8 + len));
          off += 8 + len;
        }
        if (off > 0) buf = buf.slice(off);
      }
    } catch { /* reconnect below */ }
    showOverlay('Mất luồng hình, đang kết nối lại...');
    await sleep(1000);
  }
}

function showOverlay(text) {
  $('overlayText').textContent = text;
  overlay.hidden = false;
}

setInterval(() => {
  $('sbFps').textContent = S.frames ? `${S.frames} FPS` : '0 FPS · trang tĩnh';
  S.frames = 0;
}, 1000);

// ---------------------------------------------------------------- input queue

/** Ordered input pipeline: coalesces moves/wheel/text, keeps exactly one request in flight. */
const inputQ = {
  events: [],
  inflight: false,
  timer: null,
  idleWaiters: [],

  push(ev) {
    const last = this.events[this.events.length - 1];
    if (last && ev.t === 'move' && last.t === 'move') this.events[this.events.length - 1] = ev;
    else if (last && ev.t === 'wheel' && last.t === 'wheel') { last.dx += ev.dx; last.dy += ev.dy; last.x = ev.x; last.y = ev.y; }
    else if (last && ev.t === 'text' && last.t === 'text') last.text += ev.text;
    else this.events.push(ev);
    if (ev.t === 'move') {
      // Coalesce bursts of mousemove into one send per ~frame; other events go out immediately.
      if (!this.timer) this.timer = setTimeout(() => { this.timer = null; this.flush(); }, 8);
    } else {
      this.flush();
    }
  },

  async flush() {
    if (this.inflight || !this.events.length) {
      if (!this.inflight && !this.events.length) this._resolveIdle();
      return;
    }
    this.inflight = true;
    const batch = this.events;
    this.events = [];
    try {
      const res = await api('/api/input', { events: batch });
      if (res && res.status === 'error') console.warn('input error', res.message);
    } catch { /* server restarting */ }
    this.inflight = false;
    if (this.events.length) this.flush();
    else this._resolveIdle();
  },

  whenIdle() {
    if (!this.inflight && !this.events.length) return Promise.resolve();
    return new Promise((r) => this.idleWaiters.push(r));
  },

  _resolveIdle() {
    const w = this.idleWaiters;
    this.idleWaiters = [];
    w.forEach((r) => r());
  },
};

function toRemote(e) {
  const r = canvas.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width) * S.viewport.width;
  const y = ((e.clientY - r.top) / r.height) * S.viewport.height;
  return {
    x: Math.round(Math.max(0, Math.min(S.viewport.width - 1, x))),
    y: Math.round(Math.max(0, Math.min(S.viewport.height - 1, y))),
  };
}

// ---------------------------------------------------------------- mouse

const BUTTONS = ['left', 'middle', 'right'];
let drag = null;

canvas.addEventListener('mousedown', (e) => {
  e.preventDefault();
  focusKeyboard();
  const p = toRemote(e);
  drag = { button: BUTTONS[e.button] || 'left', sx: e.clientX, sy: e.clientY, moved: false, count: e.detail || 1 };
  inputQ.push({ t: 'down', ...p, button: drag.button, count: Math.min(3, e.detail || 1) });
});

canvas.addEventListener('mousemove', (e) => {
  const p = toRemote(e);
  $('sbCoords').textContent = `x: ${p.x}, y: ${p.y}`;
  if (!drag) inputQ.push({ t: 'move', ...p });
});

window.addEventListener('mousemove', (e) => {
  if (!drag) return;
  if (Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > 4) drag.moved = true;
  inputQ.push({ t: 'move', ...toRemote(e) });
});

window.addEventListener('mouseup', (e) => {
  if (!drag) return;
  const d = drag;
  drag = null;
  inputQ.push({ t: 'up', ...toRemote(e), button: d.button, count: Math.min(3, d.count) });
  if (d.button === 'left' && (d.moved || d.count >= 2)) afterSelection();
});

canvas.addEventListener('contextmenu', (e) => e.preventDefault());

screen.addEventListener('wheel', (e) => {
  e.preventDefault();
  let dy = e.deltaY, dx = e.deltaX;
  if (e.deltaMode === 1) { dy *= 40; dx *= 40; }
  else if (e.deltaMode === 2) { dy *= S.viewport.height * 0.9; dx *= S.viewport.width * 0.9; }
  const p = toRemote(e);
  inputQ.push({ t: 'wheel', ...p, dx, dy });
}, { passive: false });

async function afterSelection() {
  await inputQ.whenIdle();
  let text = '';
  try { text = ((await api('/api/selection')).text || '').trim(); } catch { return; }
  S.lastSelection = text;
  if (!text) {
    if (S.pointerMode === 'select') toast('Không có chữ nào được bôi đen trong vùng kéo', 'info', 1800);
    return;
  }
  if (S.pointerMode === 'select') {
    if (await copyText(text)) toast(`📋 Đã sao chép ${text.length} ký tự: "${preview(text)}"`, 'success');
  } else {
    toast(`Đã chọn ${text.length} ký tự · Ctrl+C để sao chép`, 'info', 1600);
  }
}

async function copyRemoteSelection() {
  await inputQ.whenIdle();
  let text = '';
  try { text = ((await api('/api/selection')).text || '').trim(); } catch { /* ignore */ }
  text = text || S.lastSelection;
  if (!text) { toast('Chưa bôi đen chữ nào trên trang', 'info', 1600); return; }
  if (await copyText(text)) toast(`📋 Đã sao chép: "${preview(text)}"`, 'success');
}

// Pointer mode (control vs select+auto copy). Native selection works in both; select mode auto-copies.
document.querySelectorAll('#pointerMode button').forEach((b) => b.addEventListener('click', () => setPointerMode(b.dataset.mode)));
function setPointerMode(mode) {
  S.pointerMode = mode;
  setSegmented('pointerMode', mode);
  screen.classList.toggle('select-mode', mode === 'select');
  toast(mode === 'select' ? '✏️ Kéo chuột qua chữ để bôi đen — tự động sao chép' : '🖱️ Chế độ chuột: click, kéo thả, cuộn như trình duyệt thật', 'info', 2000);
}

// ---------------------------------------------------------------- keyboard

const kbSink = $('kbSink');
const NAMED_KEYS = new Set([
  'Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown',
  'Home', 'End', 'PageUp', 'PageDown', 'Insert',
  'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
]);

function focusKeyboard() {
  if (document.activeElement !== kbSink) kbSink.focus({ preventScroll: true });
}

kbSink.addEventListener('focus', () => {
  screen.classList.add('kb-active');
  const hint = $('kbHint');
  hint.classList.remove('faded');
  clearTimeout(hint._t);
  hint._t = setTimeout(() => hint.classList.add('faded'), 2500);
});
kbSink.addEventListener('blur', () => screen.classList.remove('kb-active'));

/** Converts a DOM KeyboardEvent to a Playwright key combo, or null if it should go through `input`. */
function toPlaywrightKey(e) {
  const mods = [];
  if (e.ctrlKey) mods.push('Control');
  if (e.altKey) mods.push('Alt');
  if (e.metaKey) mods.push('Meta');
  let key = e.key;
  if (key === ' ') key = 'Space';
  if (NAMED_KEYS.has(key) || key === 'Space') {
    if (e.shiftKey) mods.push('Shift');
    if (key === 'Space' && !mods.length) return null;  // plain space goes through `input`
    return [...mods, key].join('+');
  }
  if (!mods.length) return null;
  if (/^Key[A-Z]$|^Digit\d$/.test(e.code)) key = e.code;
  else if (key.length !== 1) return null;
  if (e.shiftKey) mods.push('Shift');
  return [...mods, key].join('+');
}

kbSink.addEventListener('keydown', (e) => {
  if (e.isComposing || e.key === 'Process' || e.keyCode === 229) return;
  const ctrl = e.ctrlKey || e.metaKey;
  const lower = e.key.toLowerCase();
  if (ctrl && !e.altKey && lower === 'c') { e.preventDefault(); copyRemoteSelection(); return; }
  if (ctrl && !e.altKey && lower === 'v') return;          // handled by the paste event
  if (ctrl && !e.altKey && lower === 'x') copyRemoteSelection();
  const key = toPlaywrightKey(e);
  if (!key) return;                                         // printable: arrives via `input`
  e.preventDefault();
  inputQ.push({ t: 'key', key });
});

kbSink.addEventListener('input', (e) => {
  if (e.isComposing) return;
  const v = kbSink.value;
  kbSink.value = '';
  if (v) inputQ.push({ t: 'text', text: v });
});

kbSink.addEventListener('compositionend', (e) => {
  const v = kbSink.value || e.data || '';
  kbSink.value = '';
  if (v) inputQ.push({ t: 'text', text: v });
});

kbSink.addEventListener('paste', (e) => {
  e.preventDefault();
  const text = e.clipboardData ? e.clipboardData.getData('text') : '';
  if (text) {
    inputQ.push({ t: 'text', text });
    toast(`📥 Đã dán ${text.length} ký tự vào trang`, 'info', 1500);
  }
});

document.querySelectorAll('#specialKeys button').forEach((b) => b.addEventListener('click', () => {
  inputQ.push({ t: 'key', key: b.dataset.key });
  b.classList.add('pressed');
  setTimeout(() => b.classList.remove('pressed'), 150);
}));

$('sendTextForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const ta = $('sendTextInput');
  if (!ta.value) return;
  inputQ.push({ t: 'text', text: ta.value });
  toast(`⌨️ Đã gửi ${ta.value.length} ký tự vào ô đang chọn`, 'success', 1600);
  ta.value = '';
});

$('btnPasteClipboard').addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!text) { toast('Clipboard trống', 'info'); return; }
    inputQ.push({ t: 'text', text });
    toast(`📥 Đã dán ${text.length} ký tự vào trang`, 'success', 1600);
  } catch {
    toast('Trình duyệt chặn đọc clipboard — click vào màn hình rồi bấm Ctrl+V', 'error');
  }
});

// ---------------------------------------------------------------- scrollbar overlay

const vscroll = $('vscroll');
const vthumb = $('vscrollThumb');
let thumbDrag = null;

function renderScroll(sc) {
  S.scroll = sc;
  if (!sc || !sc.maxY) {
    vscroll.classList.remove('has-scroll');
    $('sbScroll').textContent = 'Cuộn: vừa trang';
    return;
  }
  vscroll.classList.add('has-scroll');
  $('sbScroll').textContent = `Cuộn: ${Math.round(sc.ratio * 100)}%`;
  if (thumbDrag) return;
  const track = vscroll.clientHeight;
  const viewH = sc.viewH || S.viewport.height;
  const thumbH = Math.max(24, Math.round(track * Math.min(1, viewH / (viewH + sc.maxY))));
  vthumb.style.height = thumbH + 'px';
  vthumb.style.top = Math.round(sc.ratio * (track - thumbH)) + 'px';
}

let ratioTimer = null;
let ratioPending = null;
function sendRatio(r) {
  ratioPending = r;
  if (ratioTimer) return;
  ratioTimer = setTimeout(async () => {
    ratioTimer = null;
    const val = ratioPending;
    try {
      const res = await api('/api/scroll_ratio', { ratio: val });
      if (res.scroll && !thumbDrag) renderScroll(res.scroll);
    } catch { /* ignore */ }
  }, 50);
}

vthumb.addEventListener('mousedown', (e) => {
  e.preventDefault();
  e.stopPropagation();
  thumbDrag = { y: e.clientY, top: parseFloat(vthumb.style.top) || 0 };
  vscroll.classList.add('dragging');
});
window.addEventListener('mousemove', (e) => {
  if (!thumbDrag) return;
  const max = vscroll.clientHeight - vthumb.offsetHeight;
  const top = Math.max(0, Math.min(max, thumbDrag.top + e.clientY - thumbDrag.y));
  vthumb.style.top = top + 'px';
  sendRatio(max > 0 ? top / max : 0);
});
window.addEventListener('mouseup', () => {
  if (!thumbDrag) return;
  thumbDrag = null;
  vscroll.classList.remove('dragging');
});
vscroll.addEventListener('mousedown', (e) => {
  if (e.target !== vscroll) return;
  e.preventDefault();
  e.stopPropagation();
  const above = e.offsetY < (parseFloat(vthumb.style.top) || 0);
  inputQ.push({ t: 'wheel', x: S.viewport.width / 2, y: S.viewport.height / 2, dx: 0, dy: (above ? -1 : 1) * S.viewport.height * 0.85 });
});

// ---------------------------------------------------------------- tabs & navigation

function renderTabs(tabs) {
  const bar = $('tabBar');
  const sig = JSON.stringify(tabs);
  if (bar._sig === sig) return;
  bar._sig = sig;
  bar.innerHTML = tabs.map((t, i) => `
    <div class="tab ${t.active ? 'active' : ''}" data-index="${i}" title="${escapeHtml(t.title)}\n${escapeHtml(t.url)}">
      ${oi(t.url && t.url.startsWith('https://') ? 'lock' : 'globe')}
      <span class="tab-title">${escapeHtml(t.title || 'Tab mới')}</span>
      ${tabs.length > 1 ? `<button class="tab-close" data-close="${i}" title="Đóng tab" aria-label="Đóng tab">${oi('x')}</button>` : ''}
    </div>`).join('') + `<button class="tab-new" id="btnNewTab" title="Tab mới" aria-label="Tab mới">${oi('plus')}</button>`;
}

$('tabBar').addEventListener('click', (e) => {
  const close = e.target.closest('[data-close]');
  if (close) { api('/api/tab/close', { index: +close.dataset.close }); return; }
  if (e.target.closest('#btnNewTab')) { api('/api/tab/new', { url: 'https://www.google.com' }); return; }
  const tab = e.target.closest('.tab');
  if (tab && !tab.classList.contains('active')) api('/api/tab/switch', { index: +tab.dataset.index });
});
$('tabBar').addEventListener('auxclick', (e) => {
  const tab = e.target.closest('.tab');
  if (e.button === 1 && tab && S.tabs.length > 1) api('/api/tab/close', { index: +tab.dataset.index });
});

const urlInput = $('urlInput');
$('urlForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;
  urlInput.blur();
  api('/api/goto', { url }).then((r) => { if (r.status === 'error') toast(`Không mở được trang: ${r.message}`, 'error', 4000); });
});
urlInput.addEventListener('focus', () => setTimeout(() => urlInput.select(), 0));
urlInput.addEventListener('keydown', (e) => { if (e.key === 'Escape') { urlInput.value = S.url; urlInput.blur(); } });

document.querySelectorAll('[data-nav]').forEach((b) => b.addEventListener('click', () => api('/api/action', { action: b.dataset.nav })));
$('btnReload').addEventListener('click', () => api('/api/action', { action: S.loading ? 'stop_loading' : 'reload' }));

const SITES = {
  cambridge: 'https://www.cambridgeone.org/nlp/learner/org_cup_P5P89k5Xlm9dZYgWcNtWU/class/0dbf774e-59f2-4477-bc4e-336e02cddf5b/product/empbe2ednl3dw/assignment/64fffda9-288b-4dc4-81a4-a2e2333d07f6/view/item/1626243307374%2F1626245741894%2F1626258839518',
  rikkei: 'https://lms-student.rikkei.edu.vn',
};
document.querySelectorAll('[data-site]').forEach((b) => b.addEventListener('click', () => api('/api/goto', { url: SITES[b.dataset.site] })));

function setSidebar(open) {
  $('workspace').classList.toggle('no-sidebar', !open);
  setIcon($('btnSidebar').querySelector('svg'), open ? 'sidebar-expand' : 'sidebar-collapse');
  try { localStorage.setItem('aib.sidebar', open ? '1' : '0'); } catch { /* ignore */ }
}
$('btnSidebar').addEventListener('click', () => setSidebar($('workspace').classList.contains('no-sidebar')));
try { if (localStorage.getItem('aib.sidebar') === '0') setSidebar(false); } catch { /* ignore */ }

function applyState(st) {
  if (!st) return;
  if (st.mode) applyMode(st.mode);
  if (st.viewport && (st.viewport.width !== S.viewport.width || st.viewport.height !== S.viewport.height)) {
    S.viewport = st.viewport;
    fitScreen();
  }
  $('sbViewport').textContent = `${S.viewport.width}×${S.viewport.height}`;
  if (st.tabs) { S.tabs = st.tabs; renderTabs(st.tabs); }
  if (typeof st.active_url === 'string') {
    S.url = st.active_url;
    if (document.activeElement !== urlInput) urlInput.value = st.active_url;
    const secure = st.active_url.startsWith('https://');
    setIcon($('urlIcon'), secure ? 'lock' : 'globe');
    $('urlIcon').classList.toggle('secure', secure);
  }
  S.loading = !!st.loading;
  $('urlForm').classList.toggle('loading', S.loading);
  const reloadIcon = S.loading ? 'x' : 'sync';
  if ($('btnReload').querySelector('svg').dataset.oi !== reloadIcon) setIcon($('btnReload').querySelector('svg'), reloadIcon);
  $('btnReload').title = S.loading ? 'Dừng tải' : 'Tải lại';
  // The desktop panel keeps a fixed window title; the web view shows the page being browsed.
  document.title = document.body.classList.contains('panel-mode') || !st.title ? 'Auto Browser' : `${st.title} · Auto Browser`;
  $('sbMode').textContent = st.screencast ? 'Screencast trực tiếp' : 'Ảnh chụp dự phòng';
  renderScroll(st.scroll);
  if (st.ready === false && st.mode !== 'window') showOverlay('Trình duyệt đang khởi động...');
}

// ---------------------------------------------------------------- AI pointer animation

const aiCursor = $('aiCursor');
let aiCursorTimer = null;
function showAiPointer(p) {
  const left = (p.x / S.viewport.width) * 100;
  const top = (p.y / S.viewport.height) * 100;
  aiCursor.style.left = `calc(${left}% - 4px)`;
  aiCursor.style.top = `calc(${top}% - 2px)`;
  aiCursor.classList.add('show');
  const rip = document.createElement('div');
  rip.className = 'ripple';
  rip.style.left = left + '%';
  rip.style.top = top + '%';
  setTimeout(() => { screen.appendChild(rip); setTimeout(() => rip.remove(), 650); }, 300);
  clearTimeout(aiCursorTimer);
  aiCursorTimer = setTimeout(() => aiCursor.classList.remove('show'), S.aiRunning ? 6000 : 2000);
}

// ---------------------------------------------------------------- chat

const chat = $('chat');
const renderedIds = new Set();

function nearBottom() {
  return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
}

function messageHtml(m) {
  const status = m.status && m.status !== 'ok' ? m.status : '';
  if (m.kind === 'answers') {
    const items = (m.data && m.data.answers) || [];
    return `<div class="msg ai answers"><div class="card">
      <div class="card-head"><span>${oi('checklist')} Đáp án <span class="Counter">${items.length}</span></span><button class="btn-link" data-copy-answers="${m.id}">${oi('copy')} Sao chép</button></div>
      <ol>${items.map((a) => `<li>${a.q ? `<div class="q">${escapeHtml(a.q)}</div>` : ''}<div class="a">${escapeHtml(a.a)}</div>${a.why ? `<div class="why">${escapeHtml(a.why)}</div>` : ''}</li>`).join('')}</ol>
    </div></div>`;
  }
  if (m.kind === 'thought') {
    const d = m.data || {};
    const step = d.step ? `<span class="step-tag" title="${escapeHtml(d.model || '')}">Bước ${d.step}${d.secs ? ` · ${d.secs}s` : ''}</span>` : '';
    return `<div class="msg ai thought"><div class="bubble">${step}${md(m.text)}</div></div>`;
  }
  if (m.kind === 'transcript') {
    const d = m.data || {};
    return `<div class="msg ai transcript"><div class="card"><details>
      <summary class="card-head"><span>${oi('unmute')} Lời audio «${escapeHtml(d.name || 'audio')}»</span><span class="color-fg-muted text-normal">bấm để xem</span></summary>
      <pre>${escapeHtml(m.text)}</pre></details></div></div>`;
  }
  const kind = ['action', 'summary', 'status'].includes(m.kind) ? m.kind : '';
  const statusCls = m.kind === 'success' ? 'success' : m.kind === 'warning' ? 'warning' : m.kind === 'error' ? 'error' : status;
  const badge = m.kind === 'action' ? `<span class="badge">${oi(status === 'warning' ? 'alert' : 'dot-fill')}</span>` : '';
  return `<div class="msg ${m.role === 'user' ? 'user' : 'ai'} ${kind} ${statusCls}">${badge}
      <div class="bubble">${m.role === 'user' ? escapeHtml(m.text).replace(/\n/g, '<br>') : md(m.text)}</div>
      <div class="meta">${escapeHtml(m.timestamp || '')}</div>
    </div>`;
}

const answerStore = new Map();
function appendMessage(m, forceScroll = false) {
  if (renderedIds.has(m.id)) return;
  renderedIds.add(m.id);
  if (m.kind === 'answers') answerStore.set(String(m.id), (m.data && m.data.answers) || []);
  const stick = forceScroll || nearBottom();
  chat.insertAdjacentHTML('beforeend', messageHtml(m));
  while (chat.children.length > 400) chat.firstElementChild.remove();
  if (stick) chat.scrollTop = chat.scrollHeight;
}

function renderAllMessages(list) {
  chat.innerHTML = '';
  renderedIds.clear();
  (list || []).forEach((m) => appendMessage(m, true));
}

chat.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-copy-answers]');
  if (!btn) return;
  const items = answerStore.get(btn.dataset.copyAnswers) || [];
  const text = items.map((a, i) => `${i + 1}. ${a.q ? a.q + ' → ' : ''}${a.a}${a.why ? ` (${a.why})` : ''}`).join('\n');
  if (await copyText(text)) toast(`📋 Đã sao chép ${items.length} đáp án`, 'success');
});

function applyAiStatus(st) {
  if (!st) return;
  S.aiRunning = !!st.is_running;
  const phase = st.phase || 'idle';
  const badge = $('aiBadge');
  const stopping = phase === 'stopping';
  badge.className = 'Label ' + (stopping ? 'Label--attention' : S.aiRunning ? 'Label--accent' : 'Label--success');
  $('aiBadgeText').textContent = stopping ? 'Đang dừng' : S.aiRunning ? 'Đang chạy' : 'Sẵn sàng';
  $('btnRun').disabled = S.aiRunning;
  $('btnStop').disabled = !S.aiRunning;

  const PHASES = { observing: '👀 Quan sát', listening: '🎧 Nghe audio', thinking: '🧠 Suy luận', acting: '🖱️ Thao tác', starting: 'Khởi động', stopping: 'Đang dừng' };
  const prog = $('aiProgress');
  prog.hidden = !S.aiRunning;
  if (S.aiRunning) {
    const step = st.step || 0, max = st.max_steps || 0;
    $('aiStepText').textContent = max ? `Bước ${step}/${max}` : 'Đang xử lý';
    $('aiPhaseText').textContent = PHASES[phase] || '';
    $('aiProgressFill').style.width = max ? `${Math.min(100, (step / max) * 100)}%` : '8%';
    $('aiDetailText').textContent = st.detail || '';
  }
  screen.classList.toggle('ai-active', S.aiRunning);
  const hud = $('hud');
  if (S.aiRunning) {
    hud.className = 'hud ai';
    $('hudText').textContent = `🤖 ${st.step ? `Bước ${st.step} · ` : ''}${st.detail || PHASES[phase] || ''}`;
  } else {
    hud.className = 'hud idle';
    $('hudText').textContent = 'Trực tiếp';
  }
}

async function runAi(command, overrides) {
  const res = await api('/api/ai/command', { command, overrides });
  if (res.status === 'busy') toast(res.message, 'info');
}

$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  const ta = $('aiInput');
  const cmd = ta.value.trim();
  if (!cmd) return;
  ta.value = '';
  autoGrow(ta);
  runAi(cmd);
});
$('aiInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('composer').requestSubmit(); }
});
function autoGrow(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(120, ta.scrollHeight) + 'px'; }
$('aiInput').addEventListener('input', (e) => autoGrow(e.target));

$('btnRun').addEventListener('click', () => {
  const ta = $('aiInput');
  const cmd = ta.value.trim();
  ta.value = '';
  autoGrow(ta);
  runAi(cmd || 'Tiếp tục giải bài tập trên trang hiện tại');
});
$('btnStop').addEventListener('click', () => api('/api/ai/stop', {}));

$('chips').addEventListener('click', (e) => {
  const chip = e.target.closest('.chip');
  if (!chip) return;
  if (chip.dataset.clear) { api('/api/ai/clear', {}); return; }
  const overrides = chip.dataset.continuous ? { auto_submit: true, auto_next: true, mode: 'auto' } : undefined;
  runAi(chip.dataset.cmd, overrides);
});

// Quick options in the sidebar save immediately
document.querySelectorAll('#aiMode button').forEach((b) => b.addEventListener('click', () => saveConfig({ mode: b.dataset.mode })));
$('optSubmit').addEventListener('change', (e) => saveConfig({ auto_submit: e.target.checked }));
$('optNext').addEventListener('change', (e) => saveConfig({ auto_next: e.target.checked }));

// ---------------------------------------------------------------- config / settings

function applyConfig(cfg) {
  if (!cfg) return;
  S.config = cfg;
  const dot = $('keyDot');
  dot.className = 'status-dot ' + (cfg.has_key ? 'success' : 'danger');
  const brand = cfg.provider === 'claude' ? 'Claude' : 'Gemini';
  $('settingsLabel').textContent = cfg.has_key
    ? (cfg.active_model || brand).replace(/^gemini-/, 'Gemini ').replace(/^claude-/, 'Claude ')
    : 'Cài đặt';
  setSegmented('aiMode', cfg.mode);
  $('optSubmit').checked = !!cfg.auto_submit;
  $('optNext').checked = !!cfg.auto_next;
  $('optSubmit').disabled = $('optNext').disabled = cfg.mode === 'suggest';
  if (cfg.usage) $('sbUsage').textContent = cfg.usage.calls ? `AI: ${cfg.usage.calls} lượt` : '';
}

async function saveConfig(patch) {
  try {
    const res = await api('/api/ai/config', patch);
    applyConfig(res.config);
    return res;
  } catch {
    toast('Không lưu được cài đặt', 'error');
    return null;
  }
}

const modal = $('settingsModal');
function openSettings() {
  const c = S.config || {};
  document.querySelectorAll('input[name=provider]').forEach((r) => { r.checked = r.value === (c.provider || 'gemini'); });
  $('apiKeyInput').value = '';
  $('apiKeyInput').placeholder = c.masked_gemini_key ? `Đã lưu: ${c.masked_gemini_key}. Nhập khóa mới để thay` : 'Dán khóa API (AIza...)';
  $('claudeKeyInput').value = '';
  $('claudeKeyInput').placeholder = c.masked_claude_key ? `Đã lưu: ${c.masked_claude_key}. Nhập khóa mới để thay` : 'Dán khóa (sk-ant-...)';
  $('claudeModelInput').value = c.claude_model || 'claude-opus-5-5';
  $('claudeEffortSelect').value = c.claude_effort || 'medium';
  $('modelInput').value = c.model || '';
  $('fallbackInput').value = (c.fallback_models || []).join(', ');
  document.querySelectorAll('input[name=mode]').forEach((r) => { r.checked = r.value === (c.mode || 'auto'); });
  $('setSubmit').checked = !!c.auto_submit;
  $('setNext').checked = !!c.auto_next;
  $('setListen').checked = c.listen_audio !== false;
  $('maxStepsInput').value = c.max_steps || 30;
  const vp = `${S.viewport.width}x${S.viewport.height}`;
  const sel = $('viewportSelect');
  sel.value = [...sel.options].some((o) => o.value === vp) ? vp : 'fit';
  $('settingsStatus').hidden = true;
  modal.hidden = false;
  setTimeout(() => (c.provider === 'claude' ? $('claudeKeyInput') : $('apiKeyInput')).focus(), 50);
  if (c.has_gemini_key && !$('modelList').children.length) loadModels(true, 'gemini');
  if (c.has_claude_key && !$('claudeModelList').children.length) loadModels(true, 'claude');
}
function closeSettings() { modal.hidden = true; }

function settingsStatus(text, cls) {
  const el = $('settingsStatus');
  el.className = `flash ${{ ok: 'flash-success', err: 'flash-error', info: 'flash-info' }[cls] || 'flash-info'}`;
  el.textContent = text;
  el.hidden = false;
}

function collectSettings() {
  const patch = {
    model: $('modelInput').value.trim(),
    fallback_models: $('fallbackInput').value.split(',').map((s) => s.trim()).filter(Boolean),
    mode: (document.querySelector('input[name=mode]:checked') || {}).value || 'auto',
    auto_submit: $('setSubmit').checked,
    auto_next: $('setNext').checked,
    listen_audio: $('setListen').checked,
    max_steps: +$('maxStepsInput').value || 30,
    provider: (document.querySelector('input[name=provider]:checked') || {}).value || 'gemini',
    claude_model: $('claudeModelInput').value.trim(),
    claude_effort: $('claudeEffortSelect').value,
  };
  const key = $('apiKeyInput').value.trim();
  if (key) patch.api_key = key;
  const claudeKey = $('claudeKeyInput').value.trim();
  if (claudeKey) patch.claude_api_key = claudeKey;
  return patch;
}

async function applyViewportChoice() {
  const v = $('viewportSelect').value;
  let w, h;
  if (v === 'fit') {
    w = Math.round(stage.clientWidth - 20);
    h = Math.round(stage.clientHeight - 20);
  } else {
    [w, h] = v.split('x').map(Number);
  }
  if (w !== S.viewport.width || h !== S.viewport.height) await api('/api/viewport', { width: w, height: h });
}

async function submitSettings(test) {
  const btn = test ? $('btnTestSettings') : $('btnSaveSettings');
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `${oi('hourglass')} Đang lưu...`;
  try {
    const patch = collectSettings();
    if (test) patch.test = true;
    const res = await saveConfig(patch);
    await applyViewportChoice();
    if (!res) return;
    if (res.status === 'ok') {
      settingsStatus(`✅ ${res.message}`, 'ok');
      toast('Đã lưu cài đặt', 'success', 1600);
      if (test) loadModels(true, res.config && res.config.provider);
      setTimeout(closeSettings, test ? 1400 : 500);
    } else {
      settingsStatus(`❌ ${res.message}`, 'err');
    }
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
  }
}

async function loadModels(silent = false, provider = 'gemini') {
  const claude = provider === 'claude';
  const btn = claude ? $('btnLoadClaudeModels') : $('btnLoadModels');
  btn.disabled = true;
  try {
    const res = await api(`/api/ai/models?provider=${claude ? 'claude' : 'gemini'}`);
    if (res.status !== 'ok') { if (!silent) settingsStatus(`❌ ${res.message}`, 'err'); return; }
    $(claude ? 'claudeModelList' : 'modelList').innerHTML = res.models.map((m) => `<option value="${escapeHtml(m.name)}">${escapeHtml(m.display)}</option>`).join('');
    if (!claude) $('modelHint').textContent = `${res.models.length} mô hình khả dụng. Gõ để lọc; bản "flash" nhanh và rẻ, bản "pro" chính xác hơn.`;
    if (!silent) settingsStatus(`Đã tải ${res.models.length} mô hình.`, 'info');
  } catch {
    if (!silent) settingsStatus('❌ Không tải được danh sách mô hình', 'err');
  } finally {
    btn.disabled = false;
  }
}

$('btnSettings').addEventListener('click', openSettings);
modal.addEventListener('mousedown', (e) => { if (e.target === modal) closeSettings(); });
modal.querySelector('[data-close-modal]').addEventListener('click', closeSettings);
$('btnSaveSettings').addEventListener('click', () => submitSettings(false));
$('btnTestSettings').addEventListener('click', () => submitSettings(true));
$('btnLoadModels').addEventListener('click', () => loadModels(false, 'gemini'));
$('btnLoadClaudeModels').addEventListener('click', () => loadModels(false, 'claude'));
$('btnToggleKey').addEventListener('click', () => {
  const inp = $('apiKeyInput');
  inp.type = inp.type === 'password' ? 'text' : 'password';
  setIcon($('btnToggleKey').querySelector('svg'), inp.type === 'password' ? 'eye' : 'eye-closed');
});
$('btnClearKey').addEventListener('click', async () => {
  const provider = (document.querySelector('input[name=provider]:checked') || {}).value || 'gemini';
  const brand = provider === 'claude' ? 'Claude' : 'Gemini';
  if (!confirm(`Xóa ${brand} API Key đã lưu trên máy này?`)) return;
  await saveConfig({ clear_key: true, clear_provider: provider });
  settingsStatus('Đã xóa API Key.', 'info');
  openSettings();
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!modal.hidden) closeSettings();
  else if (!$('textDrawer').hidden) closeDrawer();
});

// ---------------------------------------------------------------- text drawer

const drawer = $('textDrawer');
let textBlocks = [];

async function openDrawer() {
  drawer.hidden = false;
  $('textDrawerBody').innerHTML = `<div class="drawer-note">${oi('hourglass')} Đang đọc nội dung trang (kể cả iframe)...</div>`;
  try {
    const data = await api('/api/page/text');
    textBlocks = data.textBlocks || [];
  } catch {
    textBlocks = [];
  }
  renderDrawer();
}
function closeDrawer() { drawer.hidden = true; }

function renderDrawer() {
  const q = $('textSearch').value.trim().toLowerCase();
  const body = $('textDrawerBody');
  const list = textBlocks.map((t, i) => ({ t, i })).filter(({ t }) => !q || t.toLowerCase().includes(q));
  if (!list.length) {
    body.innerHTML = `<div class="drawer-note">${textBlocks.length ? 'Không có đoạn nào khớp bộ lọc.' : 'Không tìm thấy văn bản trên trang hiện tại.'}</div>`;
    return;
  }
  const hl = (t) => {
    const safe = escapeHtml(t);
    if (!q) return safe;
    const re = new RegExp(escapeHtml(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    return safe.replace(re, (m) => `<mark>${m}</mark>`);
  };
  body.innerHTML = `<div class="drawer-note" style="padding:0 0 4px;text-align:left">${list.length} đoạn</div>` +
    list.map(({ t, i }) => `<div class="text-block"><p>${hl(t)}</p><button class="btn-octicon copy" data-i="${i}" title="Sao chép đoạn này" aria-label="Sao chép đoạn này">${oi('copy')}</button></div>`).join('');
}

$('btnText').addEventListener('click', openDrawer);
$('btnCloseDrawer').addEventListener('click', closeDrawer);
$('btnRefreshText').addEventListener('click', openDrawer);
$('textSearch').addEventListener('input', renderDrawer);
$('textDrawerBody').addEventListener('click', async (e) => {
  const b = e.target.closest('.copy');
  if (!b) return;
  const t = textBlocks[+b.dataset.i] || '';
  if (await copyText(t)) toast(`📋 Đã sao chép: "${preview(t, 40)}"`, 'success');
});
$('btnCopyAll').addEventListener('click', async () => {
  if (!textBlocks.length) { toast('Chưa có nội dung để sao chép', 'info'); return; }
  if (await copyText(textBlocks.join('\n\n'))) toast(`📋 Đã sao chép ${textBlocks.length} đoạn văn bản`, 'success');
});

// ---------------------------------------------------------------- logs

const logBox = $('logBox');
function appendLog(line) {
  const stick = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 30;
  const div = document.createElement('div');
  div.textContent = line;
  logBox.appendChild(div);
  while (logBox.children.length > 200) logBox.firstChild.remove();
  if (stick) logBox.scrollTop = logBox.scrollHeight;
}

// ---------------------------------------------------------------- server events

function setConn(state) {
  const el = $('sbConn');
  el.querySelector('.status-dot').className = 'status-dot ' + (state === 'live' ? 'success' : state === 'down' ? 'danger' : 'attention');
  $('sbConnText').textContent = state === 'live' ? 'Đã kết nối' : state === 'down' ? 'Mất kết nối máy chủ' : 'Đang kết nối';
}

function connectEvents() {
  const isPanel = new URLSearchParams(location.search).get('view') === 'panel';
  const es = new EventSource('/api/events' + (isPanel ? '?view=panel' : ''));
  const on = (name, fn) => es.addEventListener(name, (e) => { try { fn(JSON.parse(e.data)); } catch (err) { console.error(name, err); } });

  on('hello', (d) => {
    setConn('live');
    applyState(d.state);
    logBox.innerHTML = '';
    (d.state.logs || []).forEach(appendLog);
    renderAllMessages(d.ai.messages);
    applyAiStatus(d.ai);
    applyConfig(d.ai.config);
  });
  on('state', applyState);
  on('log', appendLog);
  on('message', (m) => appendMessage(m));
  on('messages_cleared', () => renderAllMessages([]));
  on('ai_status', applyAiStatus);
  on('ai_pointer', showAiPointer);
  on('config', applyConfig);
  on('toast', (t) => toast(t.text, t.type || 'info', 3500));
  on('app_quit', () => { es.close(); if (isPanel) window.close(); else showOverlay('Ứng dụng đã thoát.'); });
  es.onopen = () => setConn('live');
  es.onerror = () => setConn(es.readyState === EventSource.CLOSED ? 'down' : 'connecting');
}

// ---------------------------------------------------------------- boot

/** "window" mode: the user works in a real Chromium window, this page is only the AI panel (no stream). */
let streamStarted = false;
function applyMode(mode) {
  const panel = mode === 'window';
  document.body.classList.toggle('panel-mode', panel);
  if (panel) {
    overlay.hidden = true;
  } else if (!streamStarted) {
    streamStarted = true;
    fitScreen();
    runStream();
  }
}

fitScreen();
connectEvents();
if (new URLSearchParams(location.search).get('view') === 'panel') applyMode('window');
