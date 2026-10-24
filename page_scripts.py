"""
JavaScript snippets evaluated inside page frames by BrowserManager.
Kept separate so the Python worker logic stays readable.
"""

# Finds interactive elements in ONE frame, tags them with data-aid="<id>" and returns a compact
# description. Covers the WHOLE document: elements scrolled out of view (or inside a scrolled box)
# are kept when they matter for answering (fields, options, drop zones, buttons) and carry
# offscreen: "above" | "below" | "side". Coordinates are relative to the frame's own viewport.
ANNOTATE_ELEMENTS = r"""
({start, limit}) => {
  document.querySelectorAll('[data-aid]').forEach(e => e.removeAttribute('data-aid'));
  const SEL = [
    'a[href]', 'button', 'input:not([type=hidden])', 'textarea', 'select', 'summary', 'label',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]', '[role=option]', '[role=tab]',
    '[role=menuitem]', '[role=switch]', '[role=combobox]', '[role=textbox]', '[role=listbox]',
    '[role=slider]', '[role=treeitem]', '[contenteditable=""]', '[contenteditable=true]',
    '[onclick]', '[draggable=true]', '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const DROP = '[ondrop], [ondragover], [aria-dropeffect], [data-droppable], [class*=droppable], [class*=dropzone], [class*=drop-zone], [class*=drop-target], [class*=droptarget]';
  // Gap/slot class names used by gap-fill players; excludes utility classes such as Tailwind "gap-4".
  const DROP_RE = /(^|[\s_-])(drop|blank|slot|gap|answer-?box|target|placeholder-box)(?![-_]?\d)([\s_-]|$)/i;
  const isDropEl = el => el.matches(DROP) || (typeof el.className === 'string' && DROP_RE.test(el.className));
  const W = window.innerWidth, H = window.innerHeight;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  const shown = (el, r) => {
    if (r.width < 3 || r.height < 3) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) return false;
    return true;
  };
  const where = (top, bottom, left, right, T, B, L, R) =>
    bottom <= T ? 'above' : top >= B ? 'below' : (right <= L || left >= R) ? 'side' : null;
  // Off-screen relative to the window, or hidden inside a scrolled container (overflow box).
  const offscreenOf = (el, r) => {
    const w = where(r.top, r.bottom, r.left, r.right, 0, H, 0, W);
    if (w) return w;
    let p = el.parentElement, n = 0;
    while (p && p !== document.body && p !== document.documentElement && n++ < 30) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden)/.test(s.overflowY + ' ' + s.overflowX)) {
        const pr = p.getBoundingClientRect();
        const c = where(r.top, r.bottom, r.left, r.right, pr.top, pr.bottom, pr.left, pr.right);
        if (c) return c;
      }
      p = p.parentElement;
    }
    return null;
  };
  const uncovered = (el, r) => {
    const cx = Math.min(W - 1, Math.max(0, r.left + r.width / 2));
    const cy = Math.min(H - 1, Math.max(0, r.top + r.height / 2));
    const top = document.elementFromPoint(cx, cy);
    if (!top) return true;
    if (top === el || el.contains(top) || top.contains(el)) return true;
    // Custom-styled radios/checkboxes often hide the input under its label.
    const lab = el.labels && el.labels[0];
    if (lab && (lab === top || lab.contains(top))) return true;
    return false;
  };
  const blockOf = el => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const d = getComputedStyle(p).display;
      if (!d.startsWith('inline') && d !== 'contents') return p;
      p = p.parentElement;
    }
    return document.body;
  };
  // Only text the student can see: hidden nodes may hold the answer key (British Council keeps the correct
  // chip in a "model-wrapper hidden" box next to each gap), and a Range or innerText of a display:none box
  // returns that text too, which labelled every gap with an answer instead of its prompt.
  const rendered = n => { const p = n.nodeType === 1 ? n : n.parentElement; return !!p && p.getClientRects().length > 0
    && getComputedStyle(p).visibility !== 'hidden'; };
  const around = el => {
    const b = blockOf(el);
    let before = '', after = '';
    const w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const n = w.currentNode;
      if (el.contains(n) || !n.nodeValue.trim() || !rendered(n)) continue;
      if (el.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_PRECEDING) before = (before + ' ' + n.nodeValue).slice(-400);
      else { after += ' ' + n.nodeValue; if (after.length > 300) break; }
    }
    return [clean(before).slice(-90), clean(after).slice(0, 70)];
  };

  // A gap in its own box with the prompt in a neighbouring column (British Council: <span class="text-question">
  // visit</span> | <ul class="gapfield-list">): the nearest visible text before it, a few levels up.
  const labelBefore = el => {
    let node = el;
    for (let depth = 0; depth < 5 && node && node !== document.body; depth++, node = node.parentElement) {
      for (let s = node.previousElementSibling; s; s = s.previousElementSibling) {
        if (!rendered(s)) continue;
        const t = clean(s.innerText);
        if (t) return t.slice(-90);
      }
    }
    return '';
  };
  const CONTROL_ROLES =/^(button|link|checkbox|radio|option|tab|menuitem|menuitemradio|menuitemcheckbox|switch|treeitem|combobox|slider)$/;
  const isControl = e => ['a', 'button', 'select', 'textarea', 'summary', 'label', 'input'].includes(e.tagName.toLowerCase())
    || CONTROL_ROLES.test(e.getAttribute('role') || '');

  let cands = Array.from(document.querySelectorAll(SEL + ',' + DROP));
  // Clickable widgets built from plain divs/spans (cursor:pointer) — common in LMS players.
  let scanned = 0;
  // ul/ol too: British Council's select-and-drop games use an empty <ul class="gapfield-list ui-select-and-drop">
  // as each gap, and without it the AI saw only the answer chips and dragged them onto each other.
  for (const el of document.querySelectorAll('div, span, li, img, p, td, svg, ul, ol')) {
    if (++scanned > 8000) break;
    const s = getComputedStyle(el);
    if (s.cursor === 'pointer' && !(el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer')) cands.push(el);
    else if (s.cursor === 'grab' || s.cursor === 'move' || (typeof el.className === 'string' && DROP_RE.test(el.className))) cands.push(el);
  }
  const candSet = new Set(cands);
  cands = Array.from(candSet);  // an element can match several rules; annotate it once
  const out = [];
  const deferredLinks = [];
  let id = start;
  for (const el of cands) {
    if (out.length >= limit) break;
    if (el.closest('[data-ai-overlay]')) continue;
    const r = el.getBoundingClientRect();
    if (!shown(el, r)) continue;
    const tag = el.tagName.toLowerCase();
    const inType = (el.type || '').toLowerCase();
    const isToggle = tag === 'input' && (inType === 'radio' || inType === 'checkbox');
    const isField = (tag === 'input' && !isToggle && !['button', 'submit', 'reset', 'image', 'file', 'range', 'color'].includes(inType))
      || tag === 'textarea' || tag === 'select' || el.isContentEditable;
    const isDrop = isDropEl(el);
    const offscreen = offscreenOf(el, r);
    // Off-screen plain links come last (after questions and buttons), so they never crowd answers out.
    if (offscreen && tag === 'a' && !el.getAttribute('role') && !isDrop) { deferredLinks.push([el, r, offscreen]); continue; }
    // Skip children of an already-listed clickable (span inside button etc.), but keep fields and drop zones,
    // and keep real controls inside a merely focusable container: OneTrust's cookie banner is a div with
    // tabindex, and its "Accept All Cookies" button was dropped, so the AI kept clicking the banner text.
    if (!isField && !isDrop) {
      let p = el.parentElement, nested = false;
      while (p) {
        if (candSet.has(p) && p.getAttribute('data-aid') && (isControl(p) || !isControl(el))) { nested = true; break; }
        p = p.parentElement;
      }
      if (nested) continue;
    }
    if (!offscreen && !uncovered(el, r) && !isToggle) continue;

    el.setAttribute('data-aid', String(id));
    const info = {
      id, tag,
      type: (el.getAttribute('type') || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      text: clean(el.innerText || (tag === 'input' && ['button', 'submit'].includes(el.type) ? el.value : '') || el.getAttribute('alt') || '').slice(0, 120),
      label: clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || '').slice(0, 80),
      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
      w: Math.round(r.width), h: Math.round(r.height),
      left: Math.round(r.left), top: Math.round(r.top)
    };
    if (offscreen) info.offscreen = offscreen;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') info.disabled = true;
    let toggle = isToggle ? el : null;
    if (tag === 'label') toggle = el.control || el.querySelector('input[type=radio], input[type=checkbox]');
    if ((toggle && toggle.checked) || el.getAttribute('aria-checked') === 'true') info.checked = true;
    if (toggle) info.toggle = toggle.type;
    if (el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-pressed') === 'true') info.selected = true;
    const cur = getComputedStyle(el).cursor;
    if (el.getAttribute('draggable') === 'true' || cur === 'grab' || cur === 'move'
        || /(^|[\s_-])(drag|draggable|sortable|word-?chip|token)([\s_-]|$)/i.test(typeof el.className === 'string' ? el.className : '')) info.draggable = true;
    if (isDrop) {
      info.dropzone = true;
      const [before, after] = around(el);
      info.before = before; info.after = after;
      if (!before && !after) info.before = labelBefore(el);
    }
    if (isField) {
      info.editable = el.isContentEditable ? 'content' : tag;
      info.value = clean(el.isContentEditable ? el.innerText : el.value).slice(0, 160);
      if (el.isContentEditable) info.text = '';
      const [before, after] = around(el);
      info.before = before; info.after = after;
      if (el.labels && el.labels[0]) info.label = info.label || clean(el.labels[0].innerText).slice(0, 80);
    }
    if (tag === 'select') {
      info.options = Array.from(el.options).slice(0, 25).map(o => clean(o.text));
      info.value = clean(el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '');
    }
    out.push(info);
    id++;
  }
  // Second pass: off-screen links (menus, course outlines, lesson lists) with whatever budget is left.
  for (const [el, r, offscreen] of deferredLinks) {
    if (out.length >= limit) break;
    if (el.parentElement && el.parentElement.closest('[data-aid]')) continue;
    const text = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 120);
    if (!text) continue;
    el.setAttribute('data-aid', String(id));
    out.push({ id, tag: 'a', type: '', role: '', text, label: '', offscreen,
               x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
               w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), top: Math.round(r.top) });
    id++;
  }
  const doc = document.scrollingElement || document.documentElement;
  return {
    elements: out,
    next: id,
    scroll: { y: Math.round(doc ? doc.scrollTop : 0), maxY: Math.round(doc ? doc.scrollHeight - doc.clientHeight : 0) }
  };
}
"""

# Draws numbered boxes over annotated elements (Set-of-Marks) so the vision model can refer to ids.
DRAW_MARKS = r"""
() => {
  const old = document.querySelector('[data-ai-overlay]'); if (old) old.remove();
  const root = document.createElement('div');
  root.setAttribute('data-ai-overlay', '1');
  root.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;';
  const palette = ['#ff3b30', '#ff9500', '#34c759', '#007aff', '#af52de', '#ff2d55', '#5ac8fa', '#d4a017'];
  document.querySelectorAll('[data-aid]').forEach(el => {
    const r = el.getBoundingClientRect();
    const id = el.getAttribute('data-aid');
    const c = palette[Number(id) % palette.length];
    const box = document.createElement('div');
    box.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;outline:2px solid ${c};outline-offset:-1px;border-radius:2px;`;
    const tag = document.createElement('div');
    tag.textContent = id;
    const ty = r.top >= 14 ? r.top - 14 : r.top;
    tag.style.cssText = `position:fixed;left:${Math.max(0, r.left)}px;top:${ty}px;background:${c};color:#fff;font:bold 11px/14px Arial,sans-serif;padding:0 3px;border-radius:2px;`;
    root.appendChild(box); root.appendChild(tag);
  });
  document.documentElement.appendChild(root);
  return true;
}
"""

REMOVE_MARKS = r"""
() => { document.querySelectorAll('[data-ai-overlay]').forEach(e => e.remove()); return true; }
"""

# Visible readable text of a frame (for the AI prompt and the text drawer).
FRAME_TEXT = r"""
(maxLen) => {
  const clean = s => (s || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  return clean(document.body ? document.body.innerText : '').slice(0, maxLen);
}
"""

FRAME_TEXT_BLOCKS = r"""
() => {
  // A "block" = a non-inline element that owns text directly (question rows are often plain divs).
  const taken = new Set();
  const blocks = [];
  let n = 0;
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (++n > 8000 || blocks.length >= 250) break;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'TEMPLATE'].includes(el.tagName)) continue;
    const ownText = Array.from(el.childNodes).some(c => c.nodeType === 3 && c.textContent.trim().length > 1);
    if (!ownText) continue;
    const s = getComputedStyle(el);
    if (s.display.startsWith('inline') || s.display === 'none' || s.visibility === 'hidden') continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length < 3) continue;
    let p = el.parentElement, dup = false;
    while (p) { if (taken.has(p) && p._aibText.includes(t)) { dup = true; break; } p = p.parentElement; }
    if (dup) continue;
    el._aibText = t;
    taken.add(el);
    blocks.push(t);
  }
  return Array.from(new Set(blocks));
}
"""

FRAME_SELECTION = r"""
() => {
  const a = document.activeElement;
  if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA') && typeof a.selectionStart === 'number' && a.selectionEnd > a.selectionStart) {
    return a.value.substring(a.selectionStart, a.selectionEnd);
  }
  const s = window.getSelection();
  return s ? s.toString() : '';
}
"""

# Right-click "Phân tích AI": the selection and the block around it (a highlighted question rarely
# includes its options). Climbs from the selection until the block holds clearly more than the selection.
SELECTION_CONTEXT = r"""
() => {
  let text = '', node = null;
  const a = document.activeElement;
  if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA') && typeof a.selectionStart === 'number' && a.selectionEnd > a.selectionStart) {
    text = a.value.substring(a.selectionStart, a.selectionEnd);
    node = a;
  } else {
    const s = window.getSelection();
    if (s && s.rangeCount) { text = s.toString(); node = s.getRangeAt(0).commonAncestorContainer; }
  }
  if (!text.trim()) return { text: '', context: '' };
  let el = node && node.nodeType === 1 ? node : node && node.parentElement;
  let ctx = (el && el.innerText) || text;
  while (el && el.parentElement && el !== document.body && ctx.length < text.length + 150) {
    const up = el.parentElement.innerText || '';
    if (up.length > 1500) break;
    el = el.parentElement;
    ctx = up;
  }
  return { text, context: ctx.slice(0, 1500) };
}
"""

# The answer box of "Phân tích AI": small, frosted, bottom-left, above the page (top frame only).
# Lives in a shadow root so the page's CSS cannot restyle it and the agent's observation skips it.
ANSWER_OVERLAY = r"""
(o) => {
  const ID = '__aib_answer';
  let host = document.getElementById(ID);
  if (o.state === 'hide') { if (host) host.remove(); return true; }
  if (!host) {
    host = document.createElement('div');
    host.id = ID;
    host.style.cssText = 'all:initial;position:fixed;left:12px;bottom:12px;z-index:2147483647;transition:opacity .15s';
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>
      .box { box-sizing: border-box; width: max-content; min-width: 160px; max-width: min(300px, calc(100vw - 24px));
        max-height: 170px; display: flex; flex-direction: column; border-radius: 10px; overflow: hidden;
        background: rgba(22, 27, 34, .72); color: #f0f6fc; border: 1px solid rgba(240, 246, 252, .14);
        box-shadow: 0 6px 24px rgba(1, 4, 9, .35); backdrop-filter: blur(14px) saturate(1.3);
        -webkit-backdrop-filter: blur(14px) saturate(1.3);
        font: 13px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }
      .head { display: flex; align-items: center; gap: 6px; padding: 5px 6px 5px 10px; font-size: 11px;
        font-weight: 600; letter-spacing: .02em; color: rgba(240, 246, 252, .7); }
      .head span { flex: 1; }
      .x { all: unset; cursor: pointer; width: 18px; height: 18px; border-radius: 5px; text-align: center;
        line-height: 18px; font-size: 14px; color: rgba(240, 246, 252, .7); }
      .x:hover { background: rgba(240, 246, 252, .14); color: #fff; }
      .body { padding: 0 10px 8px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
      .ans { font-weight: 600; color: #fff; }
      .error .body { color: #ffa198; }
      .loading .body { color: rgba(240, 246, 252, .75); }
      .loading .body::after { content: ''; display: inline-block; width: 1.2em; text-align: left;
        animation: dots 1.2s steps(4) infinite; }
      @keyframes dots { 0% { content: ''; } 25% { content: '.'; } 50% { content: '..'; } 75% { content: '...'; } }
    </style><div class="box"><div class="head"><span>✨ Phân tích AI</span><button class="x" title="Đóng (Esc)">×</button></div><div class="body"></div></div>`;
    root.querySelector('.x').addEventListener('click', () => host.remove());
    (document.body || document.documentElement).appendChild(host);
  }
  if (!window.__aibAnswerEsc) {
    window.__aibAnswerEsc = true;
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { const h = document.getElementById(ID); if (h) h.remove(); }
    }, true);
  }
  const box = host.shadowRoot.querySelector('.box'), body = host.shadowRoot.querySelector('.body');
  box.className = 'box ' + o.state;
  host.style.opacity = String(o.opacity);
  body.textContent = '';
  const text = String(o.body || '').replace(/\*\*/g, '').trim();
  if (o.state === 'answer') {
    const nl = text.indexOf('\n');
    const first = document.createElement('div');
    first.className = 'ans';
    first.textContent = nl < 0 ? text : text.slice(0, nl);
    body.appendChild(first);
    if (nl >= 0) body.appendChild(document.createTextNode(text.slice(nl + 1).trim()));
  } else {
    body.textContent = text;
  }
  body.scrollTop = 0;
  return true;
}
"""

SCROLL_INFO = r"""
() => {
  const doc = document.scrollingElement || document.documentElement;
  let el = doc, maxY = doc ? doc.scrollHeight - doc.clientHeight : 0;
  if (maxY < 10) {
    // Many SPA players scroll an inner container instead of the window.
    let best = null, bestMax = 0, n = 0;
    for (const e of document.querySelectorAll('div, main, section, article')) {
      if (++n > 3000) break;
      const m = e.scrollHeight - e.clientHeight;
      if (m > bestMax && e.clientHeight > 200) {
        const oy = getComputedStyle(e).overflowY;
        if (oy === 'auto' || oy === 'scroll') { best = e; bestMax = m; }
      }
    }
    if (best) { el = best; maxY = bestMax; }
  }
  const y = el ? el.scrollTop : 0;
  const viewH = el ? el.clientHeight : window.innerHeight;
  return {
    currentY: Math.round(y), maxY: Math.max(0, Math.round(maxY)), viewH: Math.round(viewH),
    ratio: maxY > 0 ? Math.min(1, Math.max(0, y / maxY)) : 0,
    canScrollUp: y > 2, canScrollDown: y < maxY - 2
  };
}
"""

SCROLL_TO_RATIO = r"""
(ratio) => {
  const doc = document.scrollingElement || document.documentElement;
  let el = doc, maxY = doc ? doc.scrollHeight - doc.clientHeight : 0;
  if (maxY < 10) {
    let best = null, bestMax = 0, n = 0;
    for (const e of document.querySelectorAll('div, main, section, article')) {
      if (++n > 3000) break;
      const m = e.scrollHeight - e.clientHeight;
      if (m > bestMax && e.clientHeight > 200) {
        const oy = getComputedStyle(e).overflowY;
        if (oy === 'auto' || oy === 'scroll') { best = e; bestMax = m; }
      }
    }
    if (best) { el = best; maxY = bestMax; }
  }
  if (el) el.scrollTop = Math.round(ratio * maxY);
  return true;
}
"""

# "Go to section X" without the AI: ranks every clickable item and heading of the WHOLE page (incl.
# off-screen ones) by how well its text matches the query, ignoring Vietnamese diacritics and case.
# Tags candidates with data-find="<n>" so the Python side can click the chosen one.
FIND_TARGETS = r"""
(query) => {
  const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D')
    .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const q = norm(query);
  if (!q) return [];
  const qWords = q.split(' ');
  document.querySelectorAll('[data-find]').forEach(e => e.removeAttribute('data-find'));
  const sel = 'a, button, summary, label, [role=button], [role=link], [role=tab], [role=menuitem], [role=treeitem], [role=option], li, h1, h2, h3, h4, h5, h6, [onclick], [tabindex]:not([tabindex="-1"])';
  const out = [];
  let n = 0;
  const H = window.innerHeight;
  for (const el of document.querySelectorAll(sel)) {
    if (++n > 6000) break;
    if (el.closest('[data-ai-overlay]')) continue;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || !el.getClientRects().length) continue;
    const raw = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
    if (!raw || raw.length > 160) continue;
    const t = norm(raw);
    let score = 0;
    if (t === q) score = 100;
    else if (t.startsWith(q + ' ')) score = 88;   // word boundary: "lesson 1" must not match "lesson 10" here
    else if ((' ' + t + ' ').includes(' ' + q + ' ')) score = 78;
    else if (t.includes(q)) score = 68;
    else {
      const tWords = new Set(t.split(' '));
      const hit = qWords.filter(w => tWords.has(w)).length;
      if (hit) score = Math.round(55 * hit / qWords.length);
    }
    if (score < 40) continue;
    const tag = el.tagName.toLowerCase();
    const heading = /^h[1-6]$/.test(tag);
    const clickable = !heading && (tag !== 'li' || s.cursor === 'pointer');
    if (!heading && !clickable) continue;
    score += clickable ? 4 : 0;               // prefer something you can open
    score -= Math.min(10, Math.floor(Math.max(0, t.length - q.length) / 12));  // prefer tight matches
    const r = el.getBoundingClientRect();
    out.push({ el, text: raw.slice(0, 90), score, kind: heading ? 'heading' : 'link',
               where: r.bottom <= 0 ? 'above' : r.top >= H ? 'below' : 'visible' });
  }
  out.sort((a, b) => b.score - a.score);
  // Nested duplicates (a <li> around an <a> with the same text): keep the innermost clickable.
  const picked = [];
  for (const c of out) {
    const outer = picked.findIndex(p => p.el.contains(c.el));
    if (outer >= 0) {
      if (c.score >= picked[outer].score - 6) picked[outer] = c;  // same item, deeper element
      continue;
    }
    if (picked.some(p => c.el.contains(p.el))) continue;
    picked.push(c);
    if (picked.length >= 8) break;
  }
  return picked.map((c, i) => { c.el.setAttribute('data-find', String(i)); return { idx: i, text: c.text, score: c.score, kind: c.kind, where: c.where }; });
}
"""

# Unanswered questions on the WHOLE page of one frame: empty text fields, radio groups with no
# choice, selects still on a placeholder. Search boxes, header/nav fields and read-only fields are
# ignored. Items carry the data-aid of the field (or its label) when ANNOTATE_ELEMENTS tagged it.
FORM_STATUS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const H = window.innerHeight;
  const shown = el => {
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0;
  };
  const ignored = el => el.closest('header, nav, [role=search], [role=navigation], [data-ai-overlay]')
    || el.readOnly || el.disabled || el.getAttribute('aria-disabled') === 'true'
    || /search|tìm|tim kiem/i.test((el.type || '') + ' ' + (el.placeholder || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.name || ''));
  const pos = el => { const r = el.getBoundingClientRect(); return r.bottom <= 0 ? 'above' : r.top >= H ? 'below' : 'visible'; };
  const ctx = el => {
    let p = el.parentElement, n = 0;
    while (p && n++ < 6) { const t = clean(p.innerText); if (t.length > 8) return t.slice(0, 90); p = p.parentElement; }
    return '';
  };
  const items = [];
  let total = 0;
  const push = (el, kind) => items.push({ kind, aid: (el.closest('[data-aid]') || el).getAttribute('data-aid'), where: pos(el), context: ctx(el) });

  const textSel = 'input:not([type]), input[type=text], input[type=number], input[type=email], input[type=tel], input[type=url], textarea, [contenteditable=""], [contenteditable=true]';
  for (const el of document.querySelectorAll(textSel)) {
    if (!shown(el) || ignored(el)) continue;
    if (el.isContentEditable && el.parentElement && el.parentElement.closest('[contenteditable=""], [contenteditable=true]')) continue;
    total++;
    const v = clean(el.isContentEditable ? el.innerText : el.value);
    if (!v) push(el, 'blank');
  }
  const groups = new Map();
  for (const r of document.querySelectorAll('input[type=radio], [role=radio]')) {
    const vis = shown(r) || (r.labels && r.labels[0] && shown(r.labels[0])) || (r.parentElement && shown(r.parentElement));
    if (!vis || ignored(r)) continue;
    const key = r.name ? 'n:' + r.name : r.closest('[role=radiogroup], fieldset, ul, ol, form') || r.parentElement;
    const g = groups.get(key) || { checked: false, first: r };
    if (r.checked || r.getAttribute('aria-checked') === 'true') g.checked = true;
    groups.set(key, g);
  }
  for (const g of groups.values()) {
    total++;
    if (!g.checked) push((g.first.labels && g.first.labels[0]) || g.first, 'choice');
  }
  for (const s of document.querySelectorAll('select')) {
    if (!shown(s) || ignored(s)) continue;
    total++;
    const o = s.options[s.selectedIndex];
    const bare = o ? clean(o.text).replace(/[-–—_.…\s]+/g, ' ').trim() : '';
    const placeholder = o && s.selectedIndex === 0 && (o.value === '' || /^(|chọn.*|chon.*|select.*|choose.*|pick.*)$/i.test(bare));
    if (!o || s.selectedIndex < 0 || placeholder) push(s, 'dropdown');
  }
  // Only the innermost drop zones are blanks: players wrap them in containers that also look droppable
  // (H5P: .h5p-drag-droppable-words > .h5p-drag-dropzone-container > div.ui-droppable[aria-dropeffect]).
  const DROPS = '[ondrop], [aria-dropeffect], [data-droppable], [class*=droppable], [class*=dropzone], [class*=drop-zone]';
  // Some players leave a dropped item where it is in the DOM and only position it over the zone
  // (H5P Drag and Drop: .h5p-draggable.h5p-dropped stays outside .h5p-dropzone): such a zone is filled too.
  const items_ = [...document.querySelectorAll('[draggable=true], [aria-grabbed], [class*=draggable]')]
    .filter(el => shown(el) && !el.querySelector(DROPS)).map(el => el.getBoundingClientRect());
  const covered = d => {
    const z = d.getBoundingClientRect();
    return items_.some(r => { const x = r.left + r.width / 2, y = r.top + r.height / 2;
      return x >= z.left && x <= z.right && y >= z.top && y <= z.bottom; });
  };
  for (const d of document.querySelectorAll(DROPS)) {
    if (!shown(d) || ignored(d) || d.querySelector(DROPS)) continue;
    total++;
    const said = d.getAttribute('aria-label') || '';
    const empty = /\b(is empty|empty)\b|trống/i.test(said)
      || (!/\bcontains?\b|chứa/i.test(said) && !clean(d.innerText) && !d.querySelector('img, [draggable=true], [role=button]') && !covered(d));
    if (empty) push(d, 'drop');
  }
  // Players that show one question at a time hide the others, so their blanks are not counted above;
  // the page itself says how many are left (British Council / H5P question sets: "6 items remaining",
  // "Answered questions: 2 out of 8"). Seen live: Finish was pressed after 2 of 8.
  const page = document.body ? document.body.innerText : '';
  let remaining = 0;
  for (const m of page.matchAll(/(\d+)\s+(?:items?|questions?|câu(?:\s+hỏi)?)\s+(?:remaining|left|còn lại|chưa\s+(?:làm|trả lời))/gi))
    remaining = Math.max(remaining, +m[1]);
  for (const m of page.matchAll(/còn\s+(\d+)\s+câu/gi)) remaining = Math.max(remaining, +m[1]);
  for (const m of page.matchAll(/answered(?:\s+questions?)?\s*:?\s*(\d+)\s*(?:out of|of|\/)\s*(\d+)/gi))
    remaining = Math.max(remaining, +m[2] - +m[1]);
  return { total, unanswered: items.length, items: items.slice(0, 15), remaining };
}
"""

MEDIA_SOURCES = r"""
() => Array.from(document.querySelectorAll('audio, video')).map(m => ({
  src: m.currentSrc || m.src || (m.querySelector('source') || {}).src || '',
  kind: m.tagName.toLowerCase(),
  duration: isFinite(m.duration) ? Math.round(m.duration) : null,
  paused: m.paused
}))
"""

DND_SNAPSHOT = r"""
(sel) => {
  const el = document.querySelector(sel);
  if (!el || !el.isConnected) return { gone: true };
  const p = el.parentElement;
  return {
    gone: false,
    sig: [el.innerText, el.childElementCount, el.value === undefined ? '' : el.value, el.getAttribute('aria-label') || ''].join('|'),
    parentSig: p ? [p.childElementCount, (p.innerText || '').length].join('|') : '',
    index: p ? Array.prototype.indexOf.call(p.children, el) : -1
  };
}
"""

# Synthetic HTML5 drag sequence with a shared DataTransfer (for pages where CDP drag interception fails).
DND_HTML5_EVENTS = r"""
({src, tgt}) => {
  const s = document.querySelector(src), t = document.querySelector(tgt);
  if (!s || !t) throw new Error('source/target missing');
  const dt = new DataTransfer();
  const at = r => ({ bubbles: true, cancelable: true, composed: true, dataTransfer: dt,
                     clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
  const rs = s.getBoundingClientRect(), rt = t.getBoundingClientRect();
  s.dispatchEvent(new DragEvent('dragstart', at(rs)));
  s.dispatchEvent(new DragEvent('drag', at(rs)));
  t.dispatchEvent(new DragEvent('dragenter', at(rt)));
  t.dispatchEvent(new DragEvent('dragover', at(rt)));
  t.dispatchEvent(new DragEvent('drop', at(rt)));
  s.dispatchEvent(new DragEvent('dragend', at(rt)));
  return true;
}
"""

# Synthetic pointer + mouse sequence along a path (for libraries listening on document-level moves).
DND_POINTER_EVENTS = r"""
({src, tgt}) => new Promise(resolve => {
  const s = document.querySelector(src), t = document.querySelector(tgt);
  if (!s || !t) throw new Error('source/target missing');
  const rs = s.getBoundingClientRect(), rt = t.getBoundingClientRect();
  const sx = rs.left + rs.width / 2, sy = rs.top + rs.height / 2;
  const tx = rt.left + rt.width / 2, ty = rt.top + rt.height / 2;
  const fire = (el, type, x, y) => {
    const up = type.endsWith('up');
    const init = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, button: 0,
                   buttons: up ? 0 : 1, view: window, pointerId: 1, pointerType: 'mouse', isPrimary: true };
    el.dispatchEvent(type.startsWith('pointer') ? new PointerEvent(type, init) : new MouseEvent(type, init));
  };
  fire(s, 'pointerdown', sx, sy); fire(s, 'mousedown', sx, sy);
  const N = 20; let i = 0;
  const step = () => {
    i++;
    const x = sx + (tx - sx) * i / N, y = sy + (ty - sy) * i / N;
    const el = document.elementFromPoint(x, y) || document.body;
    fire(el, 'pointermove', x, y); fire(el, 'mousemove', x, y);
    if (i < N) { setTimeout(step, 12); return; }
    const end = document.elementFromPoint(tx, ty) || t;
    fire(end, 'pointerup', tx, ty); fire(end, 'mouseup', tx, ty);
    resolve(true);
  };
  setTimeout(step, 40);
})
"""

# Visual cue in the real browser window (window mode) where the AI clicks / drops.
AI_RIPPLE = r"""
({x, y}) => {
  const d = document.createElement('div');
  d.setAttribute('data-ai-overlay', 'ripple');
  d.style.cssText = `position:fixed;left:${x - 18}px;top:${y - 18}px;width:36px;height:36px;border-radius:50%;
    border:3px solid #22d3ee;box-shadow:0 0 14px rgba(34,211,238,.8);pointer-events:none;z-index:2147483647;
    transition:transform .5s ease-out,opacity .5s ease-out;transform:scale(.4);opacity:1;`;
  document.documentElement.appendChild(d);
  requestAnimationFrame(() => { d.style.transform = 'scale(1.6)'; d.style.opacity = '0'; });
  setTimeout(() => d.remove(), 650);
  return true;
}
"""

LOOKS_SELECTED = r"""
(el) => el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true'
  || el.getAttribute('aria-grabbed') === 'true'
  || /(^|[\s_-])(selected|picked|chosen|active|is-selected)([\s_-]|$)/i.test(typeof el.className === 'string' ? el.className : '')
"""

READ_FIELD_VALUE = r"""
(el) => el.isContentEditable ? el.innerText : (el.value !== undefined ? el.value : el.innerText)
"""

# Before typing with the keyboard: is the focused element the target (or a text field inside it or in its
# place, e.g. a gap that is replaced by an input when clicked)? Otherwise keys would land in whatever else
# has focus. MARK_TYPE_TARGET runs before the click and remembers where the target was.
MARK_TYPE_TARGET = r"""
(el) => { const r = el.getBoundingClientRect(); window.__aibTypeRect = {left: r.left, top: r.top, right: r.right, bottom: r.bottom}; }
"""

OWNS_TEXT_FOCUS = r"""
(aid) => {
  const el = document.querySelector('[data-aid="' + aid + '"]');
  let f = document.activeElement;
  while (f && f.shadowRoot && f.shadowRoot.activeElement) f = f.shadowRoot.activeElement;
  if (!f || f === document.body) return false;
  const a = el && el.isConnected ? el.getBoundingClientRect() : window.__aibTypeRect, b = f.getBoundingClientRect();
  const overlaps = !!a && a.right > a.left && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  if (!((el && (f === el || el.contains(f) || f.contains(el))) || overlaps)) return false;
  const tag = f.tagName.toLowerCase();
  return f.isContentEditable || tag === 'textarea'
    || (tag === 'input' && !/^(button|submit|reset|checkbox|radio|file|image|range|color|hidden)$/i.test(f.type));
}
"""
