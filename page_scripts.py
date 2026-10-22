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
  const around = el => {
    const b = blockOf(el);
    try {
      const r1 = document.createRange(); r1.setStart(b, 0); r1.setEndBefore(el);
      const r2 = document.createRange(); r2.setStartAfter(el); r2.setEnd(b, b.childNodes.length);
      return [clean(r1.toString()).slice(-90), clean(r2.toString()).slice(0, 70)];
    } catch (e) { return ['', '']; }
  };

  let cands = Array.from(document.querySelectorAll(SEL + ',' + DROP));
  // Clickable widgets built from plain divs/spans (cursor:pointer) — common in LMS players.
  let scanned = 0;
  for (const el of document.querySelectorAll('div, span, li, img, p, td, svg')) {
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
    // Skip children of an already-listed clickable (span inside button etc.), but keep fields and drop zones.
    if (!isField && !isDrop) {
      let p = el.parentElement, nested = false;
      while (p) { if (candSet.has(p) && p.getAttribute('data-aid')) { nested = true; break; } p = p.parentElement; }
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
  for (const d of document.querySelectorAll(DROPS)) {
    if (!shown(d) || ignored(d) || d.querySelector(DROPS)) continue;
    total++;
    const said = d.getAttribute('aria-label') || '';
    const empty = /\b(is empty|empty)\b|trống/i.test(said)
      || (!/\bcontains?\b|chứa/i.test(said) && !clean(d.innerText) && !d.querySelector('img, [draggable=true], [role=button]'));
    if (empty) push(d, 'drop');
  }
  return { total, unanswered: items.length, items: items.slice(0, 15) };
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
