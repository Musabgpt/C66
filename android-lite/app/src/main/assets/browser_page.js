// The page's side of the assistant's own browser (InAppBrowser.java runs it inside each page it works on).
// It reads a page the way a person scans it (its text, then what can be used, numbered), finds an element by its
// number or its name, types the way a keyboard does (an editing command, never by rewriting the page: rich editors
// such as the composers of social sites break when their text is replaced under them), and clears what stands in
// the way (cookie notices, covering dialogs). Plain ES2017: old WebViews run it too.
// Entry: run(op, args) returns a plain object. The file is wrapped in a function by its caller.

var TEXT_MAX = 6000, ITEMS_MAX = 80, SEEN_MAX = 2500;
var SEL = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],[role=tab],[role=menuitem],'
  + '[role=menuitemcheckbox],[role=menuitemradio],[role=checkbox],[role=radio],[role=switch],[role=option],'
  + '[role=combobox],[role=textbox],[role=searchbox],[onclick],[contenteditable=""],[contenteditable=true],'
  + '[contenteditable=plaintext-only]';
var M = window.__musab || (window.__musab = { els: {}, tapped: false });

// ---------------------------------------------------------------------------------------------- looking

function win(e) { return (e.ownerDocument && e.ownerDocument.defaultView) || window; }

/** The document, the open shadow trees inside it and the frames of the same site: where elements can be. */
function roots(root, out) {
  out.push(root);
  var all = root.querySelectorAll('*');
  for (var i = 0; i < all.length && out.length < 40; i++) {
    var e = all[i];
    if (e.shadowRoot) roots(e.shadowRoot, out);
    else if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
      try { var d = e.contentDocument; if (d && d.body && d.body.childElementCount) roots(d, out); } catch (_) { /* another site */ }
    }
  }
  return out;
}

function all(selector) {
  var out = [], rs = roots(document, []);
  for (var i = 0; i < rs.length; i++) {
    var found = rs[i].querySelectorAll(selector);
    for (var j = 0; j < found.length && out.length < SEEN_MAX; j++) out.push(found[j]);
  }
  return out;
}

function first(selector) {
  var rs = roots(document, []);
  for (var i = 0; i < rs.length; i++) {
    var e = null;
    try { e = rs[i].querySelector(selector); } catch (_) { return null; }
    if (e) return e;
  }
  return null;
}

function vis(e) {
  var r = e.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  var s = win(e).getComputedStyle(e);
  return s.visibility !== 'hidden' && s.display !== 'none';
}

function clean(t) { return String(t || '').replace(/\s+/g, ' ').trim(); }
function words(e) { return clean(e.innerText || e.textContent); }

function texty(e) {
  if (e.tagName !== 'INPUT') return false;
  var type = (e.getAttribute('type') || 'text').toLowerCase();
  return !/^(checkbox|radio|submit|button|reset|image|file|hidden|range|color)$/.test(type);
}
/** An editor is its outermost editable element; what is inside it (its links, its words) is its content. */
function host(e) { return !!e.isContentEditable && !(e.parentElement && e.parentElement.isContentEditable); }
function editable(e) { return e.tagName === 'TEXTAREA' || texty(e) || host(e); }

function kind(e) {
  var tag = e.tagName.toLowerCase(), type = (e.getAttribute('type') || '').toLowerCase();
  var role = (e.getAttribute('role') || '').toLowerCase();
  if (tag === 'select') return 'select';
  if (tag === 'input') {
    if (type === 'checkbox' || type === 'radio') return type;
    if (type === 'file') return 'file';
    if (type === 'submit' || type === 'button' || type === 'reset' || type === 'image') return 'button';
    return 'field';
  }
  if (editable(e)) return 'field';
  if (role === 'checkbox' || role === 'switch' || role === 'menuitemcheckbox') return 'checkbox';
  if (role === 'radio' || role === 'menuitemradio') return 'radio';
  if (tag === 'a' || role === 'link') return 'link';
  return 'button';
}

/** A label's own words, without the words of the control it wraps (a list's choices, a button's text). */
function caption(l) {
  if (!l.querySelector('select,textarea,button')) return words(l);
  var copy = l.cloneNode(true), inner = copy.querySelectorAll('select,textarea,button');
  for (var i = 0; i < inner.length; i++) inner[i].parentNode.removeChild(inner[i]);
  return clean(copy.textContent);
}

function label(e) {
  var t = e.getAttribute('aria-label') || '', by = e.getAttribute('aria-labelledby');
  if (!t && by) {
    var ids = by.split(/\s+/), parts = [];
    for (var i = 0; i < ids.length; i++) { var n = e.ownerDocument.getElementById(ids[i]); if (n) parts.push(words(n)); }
    t = parts.join(' ');
  }
  if (!t && e.labels && e.labels.length) t = caption(e.labels[0]);
  if (!t && e.closest) { var p = e.closest('label'); if (p) t = caption(p); }
  return clean(t || e.getAttribute('placeholder') || e.getAttribute('aria-placeholder') || e.getAttribute('title')
    || e.getAttribute('alt') || e.getAttribute('name') || '');
}

function name(e) {
  var k = kind(e), t;
  if (k === 'field' || k === 'select' || k === 'checkbox' || k === 'radio' || k === 'file') t = label(e);
  else t = e.getAttribute('aria-label') || words(e) || e.value || label(e);
  if (!t && e.querySelector) {
    var picture = e.querySelector('img[alt],svg[aria-label],[title]');
    if (picture) t = picture.getAttribute('alt') || picture.getAttribute('aria-label') || picture.getAttribute('title');
  }
  return clean(t).slice(0, 80);
}

/** What a tap on this element should land on: a hidden checkbox or radio is used through its label. */
function face(e) {
  if (vis(e)) return e;
  if (e.labels) for (var i = 0; i < e.labels.length; i++) if (vis(e.labels[i])) return e.labels[i];
  return null;
}

/** The dialog that covers the page now, if one does. */
function dialog() {
  var found = all('dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]'), top = null;
  for (var i = 0; i < found.length; i++) if (vis(found[i])) top = found[i];
  return top;
}

function inside(e, box) {
  for (var n = e; n; n = n.parentNode || n.host) if (n === box) return true;
  return false;
}

function checked(e) {
  if (e.tagName === 'INPUT') return !!e.checked;
  var a = e.getAttribute('aria-checked') || e.getAttribute('aria-pressed') || e.getAttribute('aria-selected');
  return a === 'true';
}

function describe(e, n) {
  var k = kind(e), it = { i: n, kind: k, name: name(e) };
  var type = (e.getAttribute('type') || '').toLowerCase();
  if (k === 'field') {
    if (type && type !== 'text') it.type = type;
    var v = e.isContentEditable ? words(e) : String(e.value || '');
    if (v) it.value = type === 'password' ? '(hidden)' : v.slice(0, 60);
  } else if (k === 'select') {
    var chosen = e.options && e.selectedIndex >= 0 ? e.options[e.selectedIndex] : null;
    if (chosen) it.value = clean(chosen.text).slice(0, 60);
    var options = [];
    for (var i = 0; e.options && i < e.options.length && options.length < 12; i++) {
      var o = clean(e.options[i].text);
      if (o) options.push(o.slice(0, 40));
    }
    if (options.length) it.options = options;
    if (e.options && e.options.length > options.length) it.more_options = e.options.length - options.length;
  } else if (k === 'checkbox' || k === 'radio') {
    if (checked(e)) it.value = 'checked';
  } else if (k === 'link' && e.href) {
    it.href = String(e.href).slice(0, 160);
  }
  if (e.required || e.getAttribute('aria-required') === 'true') it.required = true;
  if (e.disabled || e.getAttribute('aria-disabled') === 'true') it.disabled = true;
  return it;
}

function pageText(box) {
  var body = box || document.body;
  return String(body ? body.innerText || '' : '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

/** The page as text and numbered things to use. Long pages are read from where the page is scrolled to. */
function read() {
  M.els = {};
  var open = dialog(), seen = all(SEL), picked = [];
  if (open) {                                  // a page can hold thousands of links: the dialog's own things are never missed
    var own = open.querySelectorAll(SEL), merged = [];
    for (var q = 0; q < own.length; q++) merged.push(own[q]);
    for (var z = 0; z < seen.length; z++) if (merged.indexOf(seen[z]) < 0) merged.push(seen[z]);
    seen = merged;
  }
  for (var i = 0; i < seen.length; i++) {
    var e = seen[i];
    if ((e.getAttribute('type') || '').toLowerCase() === 'hidden' || !face(e)) continue;
    if (e.isContentEditable && !host(e)) continue;      // a link or a button inside an editor is its text
    var k = kind(e);
    if (!name(e) && k !== 'field' && k !== 'select') continue;
    if (k === 'link' && /^(javascript:|#$)/i.test(e.getAttribute('href') || '') && !e.getAttribute('onclick') && !name(e)) continue;
    picked.push(e);
  }
  if (open) {                                  // what the dialog holds comes first: it is what can be used now
    var front = [], rest = [];
    for (var a = 0; a < picked.length; a++) (inside(picked[a], open) ? front : rest).push(picked[a]);
    picked = front.concat(rest);
  }
  var left = 0;
  if (picked.length > ITEMS_MAX) {             // too many: controls stay, the links farthest from here go
    var keep = [], links = [], mid = window.innerHeight / 2;
    for (var b = 0; b < picked.length; b++) (kind(picked[b]) === 'link' ? links : keep).push(picked[b]);
    keep = keep.slice(0, ITEMS_MAX);
    links.sort(function (x, y) {
      return Math.abs(x.getBoundingClientRect().top - mid) - Math.abs(y.getBoundingClientRect().top - mid);
    });
    var chosen = keep.concat(links.slice(0, Math.max(0, ITEMS_MAX - keep.length)));
    left = picked.length - chosen.length;
    var order = [];
    for (var c = 0; c < picked.length; c++) if (chosen.indexOf(picked[c]) >= 0) order.push(picked[c]);
    picked = order;
  }
  var items = [];
  for (var n = 0; n < picked.length; n++) {
    M.els[n + 1] = picked[n];
    try { picked[n].setAttribute('data-musab', String(n + 1)); } catch (_) { /* a frozen node */ }
    var it = describe(picked[n], n + 1);
    if (open && inside(picked[n], open)) it.dialog = true;
    items.push(it);
  }
  var text = pageText(), total = text.length, from = 0;
  var tall = document.documentElement.scrollHeight - window.innerHeight;
  if (total > TEXT_MAX && window.scrollY > 0 && tall > 0) {
    from = Math.min(total - TEXT_MAX, Math.floor(total * (window.scrollY / tall) * 0.92));
    var line = text.indexOf('\n', from);                // from a whole line, and never short of the page's end
    if (line >= 0 && line - from < 300 && from + TEXT_MAX < total) from = line + 1;
  }
  var page = {
    url: location.href, title: document.title, text: text.slice(from, from + TEXT_MAX), items: items,
    y: Math.round(window.scrollY), height: Math.round(document.documentElement.scrollHeight), view: window.innerHeight
  };
  if (total > TEXT_MAX) { page.text_from = from; page.text_total = total; }
  if (left) page.more_items = left;
  if (open) {
    page.dialog = clean(open.getAttribute('aria-label') || '') || words(open).slice(0, 80) || 'dialog';
    page.dialog_text = pageText(open).slice(0, 1500);
  }
  return page;
}

// ---------------------------------------------------------------------------------------------- finding

/** By the number read() gave it, else by its name: exact first, then its beginning, then a part of it. */
function find(spec) {
  spec = spec || {};
  var n = parseInt(spec.index, 10);
  if (n > 0) {
    var kept = M.els[n];
    if (kept && kept.isConnected !== false && kept.ownerDocument) return kept;
    var again = first('[data-musab="' + n + '"]');
    if (again) return again;
  }
  var raw = clean(spec.text);
  if (!raw) return null;
  if (/^css=/.test(raw)) return first(raw.slice(4));
  var want = raw.toLowerCase(), seen = all(SEL), starts = null, part = null;
  for (var i = 0; i < seen.length; i++) {
    var e = seen[i];
    if (!face(e)) continue;
    var have = name(e).toLowerCase();
    if (!have) continue;
    if (have === want) return e;
    if (!starts && have.indexOf(want) === 0) starts = e;
    if (!part && have.indexOf(want) > 0) part = e;
  }
  return starts || part;
}

function missing(spec) {
  return { ok: false, error: 'nothing on the page matches ' + JSON.stringify(clean((spec || {}).text) || (spec || {}).index)
    + ': extract the page again and use a number or a name from it' };
}

/** Brings it into view at once: a page that scrolls smoothly would still be moving when it is measured. */
function show(e) {
  try { e.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' }); } catch (_) { e.scrollIntoView(); }
}

/** Whether a click on it should take the page somewhere else. */
function leads(e) {
  var a = e.closest ? e.closest('a[href]') : null, h = a ? String(a.getAttribute('href') || '') : '';
  var type = String(e.getAttribute('type') || '').toLowerCase();
  return (!!h && h.charAt(0) !== '#' && !/^javascript:/i.test(h))
    || (!!e.form && (type === 'submit' || type === 'image' || (e.tagName === 'BUTTON' && type !== 'button' && type !== 'reset')));
}

/**
 * Where a finger should touch to use the element: a point inside the part of the browser the user would see, as a
 * share of its width and height. The browser itself then touches there, so the page gets a real touch.
 */
function point(spec) {
  var e = find(spec);
  if (!e) return missing(spec);
  var f = face(e);
  if (!f) return { ok: false, error: 'it is on the page but hidden: nothing to touch' };
  if (e.disabled || e.getAttribute('aria-disabled') === 'true') {
    return { ok: false, error: JSON.stringify(name(e)) + ' is disabled now: something before it is missing or not valid' };
  }
  show(f);
  var r = f.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
  for (var w = win(f); w !== window && w.frameElement; w = w.parent) {      // inside a frame: add where the frame is
    var fr = w.frameElement.getBoundingClientRect();
    x += fr.left + w.frameElement.clientLeft;
    y += fr.top + w.frameElement.clientTop;
  }
  var vv = window.visualViewport, vw = vv ? vv.width : window.innerWidth, vh = vv ? vv.height : window.innerHeight;
  var out = { ok: true, name: name(e), nav: leads(e), kind: kind(e) };
  if (kind(e) === 'file') out.file = true;
  if (x > 0 && y > 0 && x < vw && y < vh) {
    out.fx = x / vw;
    out.fy = y / vh;
    out.x = x;
    out.y = y;
    var hit = win(f) === window ? document.elementFromPoint(x, y) : null;
    for (var deep = hit; deep && deep.shadowRoot; ) {                       // look through shadow trees
      var inner = deep.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === deep) break;
      deep = hit = inner;
    }
    if (hit && !inside(hit, f) && !inside(f, hit) && !inside(hit, e)) {
      out.covered = clean(hit.getAttribute('aria-label') || '') || words(hit).slice(0, 60) || hit.tagName.toLowerCase();
    }
  }
  M.tapped = false;
  watch(win(f));
  return out;
}

/** A click made by the page itself, for when the browser's touch could not be used or did not arrive. */
function press(spec) {
  var e = find(spec);
  if (!e) return missing(spec);
  var f = face(e) || e;
  show(f);
  try { f.focus(); } catch (_) { /* not focusable */ }
  var r = f.getBoundingClientRect();
  var o = { bubbles: true, cancelable: true, composed: true, view: win(f), clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
  var W = win(f);
  if (W.PointerEvent) f.dispatchEvent(new W.PointerEvent('pointerdown', o));
  f.dispatchEvent(new W.MouseEvent('mousedown', o));
  if (W.PointerEvent) f.dispatchEvent(new W.PointerEvent('pointerup', o));
  f.dispatchEvent(new W.MouseEvent('mouseup', o));
  f.click();
  return { ok: true, name: name(e), nav: leads(e) };
}

// ---------------------------------------------------------------------------------------------- typing

function same(have, want) {
  var strip = function (t) { return String(t).replace(/[^0-9A-Za-zÀ-￿]+/g, '').toLowerCase(); };
  var a = strip(have), b = strip(want);
  if (!b) return !a;
  return !!a && (a.indexOf(b) >= 0 || b.indexOf(a) >= 0);
}

/** Sets a control's value the way the page's own code cannot tell from typing (for what has no editing command). */
function setValue(e, value) {
  var W = win(e), proto = e.tagName === 'TEXTAREA' ? W.HTMLTextAreaElement.prototype : W.HTMLInputElement.prototype;
  var d = Object.getOwnPropertyDescriptor(proto, 'value');
  if (d && d.set) d.set.call(e, value); else e.value = value;
  var ev;
  try { ev = new W.InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText', data: value }); }
  catch (_) { ev = new W.Event('input', { bubbles: true }); }
  e.dispatchEvent(ev);
  e.dispatchEvent(new W.Event('change', { bubbles: true }));
}

function choose(e, value) {
  var want = clean(value).toLowerCase(), hit = -1;
  for (var i = 0; i < e.options.length; i++) {
    var o = e.options[i], t = clean(o.text).toLowerCase();
    if (t === want || o.value === value) { hit = i; break; }
    if (hit < 0 && want && t.indexOf(want) >= 0) hit = i;
  }
  if (hit < 0) {
    var have = [];
    for (var j = 0; j < e.options.length && have.length < 15; j++) have.push(clean(e.options[j].text));
    return { ok: false, error: 'the list has no choice like ' + JSON.stringify(value) + '. It has: ' + have.join(' | ') };
  }
  e.selectedIndex = hit;
  var W = win(e);
  e.dispatchEvent(new W.Event('input', { bubbles: true }));
  e.dispatchEvent(new W.Event('change', { bubbles: true }));
  return { ok: true, name: name(e), value: clean(e.options[hit].text).slice(0, 80) };
}

function tick(e, value) {
  var want = !/^(|0|false|no|off|unchecked|لا)$/i.test(clean(value));
  if (checked(e) !== want) (face(e) || e).click();
  return { ok: true, name: name(e), value: checked(e) ? 'checked' : 'not checked' };
}

/** Whether the cursor is in this element (in its own document or shadow tree). */
function holds(e) {
  var root = e.getRootNode ? e.getRootNode() : e.ownerDocument;
  return (root && root.activeElement) === e || e.ownerDocument.activeElement === e;
}

/** A page that does not have the keyboard gives no focus events: editors wait for them, so they are given. */
function wake(e) {
  var W = win(e);
  try { e.focus(); } catch (_) { return; }
  if (e.ownerDocument.hasFocus && e.ownerDocument.hasFocus()) return;
  try {
    e.dispatchEvent(new W.FocusEvent('focus', { bubbles: false, composed: true }));
    e.dispatchEvent(new W.FocusEvent('focusin', { bubbles: true, composed: true }));
  } catch (_) { /* no focus events to make here */ }
}

/**
 * Text into a rich editor (a composer, a mail body) takes three moments, because such an editor keeps its own copy
 * of the text and follows the page's selection a beat later:
 *   1. compose(): the cursor goes in and everything in it is selected;
 *   2. typed({}): the text goes in over the selection. First as a paste, which editors take into their own copy
 *      (a paste event that an editor handles is cancelled by it, so we know); when nobody handles it, by the
 *      browser's own editing commands, as a keyboard would;
 *   3. typed({check: true}): what the editor now holds is compared with what was meant.
 * The text of an editor is never rewritten under it: that is what breaks composers ("something went wrong").
 */
function compose(e, value) {
  var doc = e.ownerDocument, W = win(e), sel = W.getSelection(), range = doc.createRange();
  wake(e);
  range.selectNodeContents(e);
  sel.removeAllRanges();
  sel.addRange(range);
  M.typing = { e: e, value: String(value), by: '' };
  return { ok: true, name: name(e), wait: 'selected' };
}

function typed(a) {
  var t = M.typing;
  if (!t || !t.e || t.e.isConnected === false) return { ok: false, error: 'the editor is gone: extract the page and type again' };
  var e = t.e, doc = e.ownerDocument, W = win(e), value = t.value;
  if (a && a.check) {
    var holds = words(e);
    M.typing = null;
    if (same(holds, value)) return { ok: true, name: name(e), value: holds.slice(0, 80), by: t.by };
    return { ok: false, error: 'the editor did not take the text (it holds ' + JSON.stringify(holds.slice(0, 60))
      + '): click the editor, then type again' };
  }
  if (value === '') {
    doc.execCommand('delete');
    t.by = 'keys';
    return { ok: true, wait: 'inserted' };
  }
  var handled = false;
  try {
    var data = new W.DataTransfer();
    data.setData('text/plain', value);
    handled = !e.dispatchEvent(new W.ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true, composed: true }));
  } catch (_) { /* no clipboard events here */ }
  var sel = W.getSelection(), inIt = !!(sel && sel.anchorNode && inside(sel.anchorNode, e));
  if (!handled && inIt) {                       // editing commands go where the selection is: only into this editor
    var lines = value.split('\n');
    for (var i = 0; i < lines.length; i++) {
      if (i) doc.execCommand('insertParagraph');
      if (lines[i]) doc.execCommand('insertText', false, lines[i]);
    }
  }
  t.by = handled ? 'paste' : 'keys';
  return { ok: true, wait: 'inserted' };
}

function enter(e) {
  var W = win(e), k = { bubbles: true, cancelable: true, composed: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 };
  var go = e.dispatchEvent(new W.KeyboardEvent('keydown', k));
  e.dispatchEvent(new W.KeyboardEvent('keypress', k));
  e.dispatchEvent(new W.KeyboardEvent('keyup', k));
  var f = e.form;
  if (go && f) {
    var b = f.querySelector('[type=submit],button:not([type=button]):not([type=reset])');
    if (f.requestSubmit) { try { f.requestSubmit(); } catch (_) { f.submit(); } }
    else if (b) b.click();
    else f.submit();
  }
}

function type(spec, value, submit) {
  var e = find(spec);
  if (!e) return missing(spec);
  value = String(value === undefined || value === null ? '' : value);
  var k = kind(e);
  if (e.disabled || e.readOnly) return { ok: false, error: JSON.stringify(name(e)) + ' cannot be changed now' };
  if (k === 'select') return choose(e, value);
  if (k === 'checkbox' || k === 'radio') return tick(e, value);
  if (k === 'file') return { ok: false, error: 'a file is chosen with op=upload (path of a file in this conversation)' };
  if (!editable(e)) {
    if (e.tagName === 'INPUT') { setValue(e, value); return { ok: true, name: name(e), value: String(e.value).slice(0, 80) }; }
    var innerField = e.querySelector ? e.querySelector('input:not([type=hidden]),textarea,[contenteditable=true],[contenteditable=""]') : null;
    if (!innerField) return { ok: false, error: JSON.stringify(name(e)) + ' is not a field: click it, then type into the field that opens' };
    e = innerField;
  }
  show(face(e) || e);
  if (e.isContentEditable) return compose(e, value);
  wake(e);
  var picker = e.tagName === 'INPUT' && /^(date|time|datetime-local|month|week)$/i.test(e.getAttribute('type') || '');
  if (!picker && holds(e)) {                    // an editing command goes to whatever has the cursor: it must be this field
    try { e.select(); } catch (_) { /* a kind of field without a selection */ }
    var doc = e.ownerDocument;
    try { if (value === '') doc.execCommand('delete'); else doc.execCommand('insertText', false, value); } catch (_) { /* below */ }
  }
  if (picker ? e.value !== value : !same(e.value, value)) setValue(e, value);
  if (submit) enter(e);
  return { ok: true, name: name(e), value: (e.getAttribute('type') || '') === 'password' ? '(hidden)' : String(e.value || '').slice(0, 80) };
}

function key(which) {
  var e = document.activeElement;
  while (e && e.shadowRoot && e.shadowRoot.activeElement) e = e.shadowRoot.activeElement;
  var name_ = String(which || 'Enter');
  var codes = { Enter: 13, Escape: 27, Tab: 9, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Backspace: 8, ' ': 32 };
  if (!(name_ in codes)) return { ok: false, error: 'keys here: Enter, Escape, Tab, ArrowDown, ArrowUp, ArrowLeft, ArrowRight, Backspace' };
  if (name_ === 'Enter') {
    if (!e || e === document.body) return { ok: false, error: 'no field has the cursor: type into one first' };
    enter(e);
    return { ok: true, nav: true };
  }
  var target = e && e !== document.body ? e : document.body, W = win(target);
  var k = { bubbles: true, cancelable: true, composed: true, key: name_, code: name_ === ' ' ? 'Space' : name_, keyCode: codes[name_], which: codes[name_] };
  target.dispatchEvent(new W.KeyboardEvent('keydown', k));
  target.dispatchEvent(new W.KeyboardEvent('keyup', k));
  return { ok: true };
}

// ---------------------------------------------------------------------------------------------- what stands in the way

var CONSENT = ['#onetrust-accept-btn-handler', '#accept-recommended-btn-handler', '.fc-cta-consent',
  '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll', '#CybotCookiebotDialogBodyButtonAccept',
  '#didomi-notice-agree-button', '.qc-cmp2-summary-buttons button[mode=primary]', '#truste-consent-button',
  '[data-testid=uc-accept-all-button]', '[data-cookiebanner=accept_button]',
  '[data-testid=cookie-policy-manage-dialog-accept-button]', '#L2AGLb', '#sp-cc-accept', '.cc-allow', '.cc-dismiss',
  '#cookie-accept', '#accept-cookies', '#acceptAllCookies', '.cmp-button_button--acceptAll', '#wt-cli-accept-all-btn',
  '.cky-btn-accept', '#cookie_action_close_header', '.js-accept-cookies', '[data-cky-tag=accept-button]',
  'button[data-action=accept]', '#gdpr-banner-accept', '.osano-cm-accept-all', '#hs-eu-confirmation-button',
  '.iubenda-cs-accept-btn', '#usercentrics-root'];
var ACCEPT = new RegExp('^(accept( all| all cookies| cookies)?( (&|and) (continue|close))?|allow( all)?( cookies)?|'
  + 'agree( (&|and) (continue|proceed|close))?|i (agree|accept|understand)|got it!?|ok(ay)?!?|yes,? i (agree|accept)|'
  + 'continue|understood|close and accept|'
  + 'موافق|أوافق|اوافق|قبول|قبول الكل|قبول الجميع|أقبل|اقبل|السماح|السماح للكل|السماح بالكل|حسنا|حسناً|فهمت|متابعة|'
  + 'السماح بجميع ملفات تعريف الارتباط|قبول جميع ملفات تعريف الارتباط|'
  + 'tout accepter|accepter( et continuer)?|j.accepte|alle akzeptieren|akzeptieren|zustimmen|einverstanden|'
  + 'aceptar( todo| todas| y continuar)?|acepto|accetta( tutto| tutti)?|accetto|aceitar( tudo| todos)?|concordo|'
  + 'kabul et|tümünü kabul et|kabul ediyorum|anladım|принять( все)?|согласен|accepteren|alles accepteren)$', 'i');
var COOKIEISH = /cookie|consent|gdpr|ccpa|privacy|cmp|onetrust|didomi|truste|iubenda|osano|cky-|cc-window|cc_banner/i;
var COOKIEWORDS = /cookie|ملفات تعريف الارتباط|كوكيز|الكوكيز|gdpr|datenschutz|çerez|политик[аи] конфиденциальности/i;

/** The button that accepts a cookie or consent notice, when the page shows one. */
function consentButton() {
  for (var i = 0; i < CONSENT.length; i++) {
    var known = first(CONSENT[i]);
    if (!known) continue;
    if (known.id === 'usercentrics-root' && known.shadowRoot) {
      known = known.shadowRoot.querySelector('[data-testid=uc-accept-all-button]');
      if (!known) continue;
    }
    if (vis(known) && known.id !== 'usercentrics-root') return known;
  }
  var buttons = all('button,[role=button],input[type=button],input[type=submit],a');
  for (var j = 0; j < buttons.length; j++) {
    var e = buttons[j];
    if (!vis(e) || !ACCEPT.test(name(e))) continue;
    var box = e, near = '';
    for (var d = 0; box && d < 7; d++) {
      var cls = typeof box.className === 'string' ? box.className : '';
      if (COOKIEISH.test((box.id || '') + ' ' + cls + ' ' + (box.getAttribute ? box.getAttribute('aria-label') || '' : ''))) return e;
      if (box.getAttribute && d >= 1 && !near) { var t = words(box); if (t.length > 40) near = t.slice(0, 700); }
      box = box.parentElement || (box.getRootNode && box.getRootNode().host) || null;
    }
    if (near && COOKIEWORDS.test(near)) return e;
  }
  return null;
}

/** Accepts the cookie notice if there is one. Run after a page opens: the page is then free to use. */
function clear() {
  var e = consentButton();
  if (!e) return { ok: true, accepted: '' };
  var label_ = name(e) || 'accept';
  try { e.click(); } catch (_) { return { ok: true, accepted: '' }; }
  return { ok: true, accepted: label_ };
}

var CLOSE = new RegExp('^(x|×|✕|✖|close|dismiss|no,? thanks?|not now|maybe later|later|skip|cancel|continue (in|with) (browser|the browser|mobile site)|'
  + 'stay (in|on) (browser|web)|use mobile site|إغلاق|اغلاق|أغلق|ليس الآن|لاحقاً|لاحقا|تخطي|تخطى|لا شكراً|لا شكرا|إلغاء|الغاء|'
  + 'المتابعة في المتصفح|fermer|plus tard|non merci|schließen|später|cerrar|ahora no|kapat|şimdi değil|закрыть|не сейчас)$', 'i');

/** Closes what covers the page: a cookie notice first, else the open dialog (its close button, else Escape). */
function dismiss() {
  var c = consentButton();
  if (c) { var n = name(c); c.click(); return { ok: true, did: 'accepted the cookie notice (' + n + ')' }; }
  var open = dialog();
  var scope = open ? open.querySelectorAll('button,[role=button],a,[aria-label]') : all('button,[role=button],[aria-label]');
  for (var i = 0; i < scope.length; i++) {
    var e = scope[i];
    if (!vis(e)) continue;
    var t = name(e), aria = clean(e.getAttribute('aria-label'));
    if (CLOSE.test(t) || CLOSE.test(aria) || /^(close|dismiss|إغلاق)\b/i.test(aria)) {
      if (!open && !/^(x|×|✕|✖)$/.test(t) && !/close|dismiss|إغلاق/i.test(aria)) continue;   // no dialog: only a clear close mark
      e.click();
      return { ok: true, did: 'closed it with ' + JSON.stringify(t || aria) };
    }
  }
  key('Escape');
  return { ok: true, did: open ? 'pressed Escape on the dialog' : 'nothing covers the page (pressed Escape)' };
}

// ---------------------------------------------------------------------------------------------- moving and searching

function scroll(to) {
  var h = window.innerHeight, t = String(to === undefined ? 'down' : to).toLowerCase();
  if (t === 'top') window.scrollTo(0, 0);
  else if (t === 'bottom') window.scrollTo(0, document.documentElement.scrollHeight);
  else if (t === 'up') window.scrollBy(0, -Math.round(h * 0.85));
  else window.scrollBy(0, Math.round(h * 0.85 * (parseFloat(t) || 1)));
  return { ok: true, y: Math.round(window.scrollY), height: Math.round(document.documentElement.scrollHeight), view: h };
}

/** Where the page says something: up to three places with the text around them; the first is scrolled to. */
function search(query) {
  var want = clean(query).toLowerCase();
  if (!want) return { ok: false, error: 'find needs text to look for' };
  var text = pageText(), low = text.toLowerCase(), places = [], at = low.indexOf(want), count = 0;
  while (at >= 0) {
    count++;
    if (places.length < 3) places.push(text.slice(Math.max(0, at - 220), at + want.length + 280).trim());
    at = low.indexOf(want, at + want.length);
  }
  if (count) {
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null), node;
    while ((node = walker.nextNode())) {
      if (node.nodeValue && node.nodeValue.toLowerCase().indexOf(want) >= 0 && node.parentElement && vis(node.parentElement)) {
        show(node.parentElement);
        break;
      }
    }
  }
  return { ok: true, count: count, places: places };
}

function has(text) {
  var want = clean(text).toLowerCase();
  return { ok: true, there: !want || pageText().toLowerCase().indexOf(want) >= 0, ready: document.readyState };
}

/** Whether the browser's touch reached the page (asked after it touched). */
function touched() { return { ok: true, tapped: !!M.tapped }; }

/** Notes a touch that reaches this window (the page, or a frame of the same site). */
function watch(w) {
  try {
    if (w.__musabWatch) return;
    w.__musabWatch = true;
    var mark = function () { M.tapped = true; };
    w.addEventListener('touchstart', mark, true);
    w.addEventListener('pointerdown', mark, true);
    w.addEventListener('mousedown', mark, true);
  } catch (_) { /* another site's frame */ }
}
watch(window);

function run(op, a) {
  a = a || {};
  switch (op) {
    case 'read': return read();
    case 'point': return point(a);
    case 'press': return press(a);
    case 'type': return type(a, a.value, !!a.submit);
    case 'typed': return typed(a);
    case 'key': return key(a.key);
    case 'clear': return clear();
    case 'dismiss': return dismiss();
    case 'scroll': return scroll(a.to);
    case 'find': return search(a.text);
    case 'has': return has(a.text);
    case 'touched': return touched();
    default: return { ok: false, error: 'unknown page op ' + op };
  }
}
