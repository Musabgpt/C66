// The chat's pictures and the Copy button on code in answers (cursor_ui.js). The page is shaped like the real chat: the
// message list, the composer footer the mode bar goes into, and the toast.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const code = readFileSync(new URL('../cursor_ui.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const SHOT = {sid: 'sess-1', name: '0123456789abcdef.jpg'};

async function chat(t, {lang = 'en', body = '', phone = null, clipboard = null} = {}) {
  const dom = new JSDOM(
    `<html lang="${lang}"><body><div id="messages">${body}</div><div class="composer-foot"></div>` +
    '<div id="toast" hidden></div></body></html>',
    {pretendToBeVisual: true, runScripts: 'outside-only', url: 'http://localhost/'});
  t.after(() => dom.window.close());
  const w = dom.window;
  if (clipboard) Object.defineProperty(w.navigator, 'clipboard', {configurable: true, value: clipboard});
  if (phone) w.NewAlPhone = phone;
  w.eval(code);
  // boot() draws the mode bar, then starts watching the chat: wait for that before the test acts
  for (let i = 0; i < 50 && !w.document.querySelector('#musab-modes'); i++) await tick();
  return {w, dom: w.document};
}

// a tool card as the chat draws it: a head, and a body that only shows when the card is open
function card(dom, body = '') {
  const el = dom.createElement('div');
  el.className = 'item';
  el.innerHTML = '<div class="item-head"><span class="meta"></span></div><div class="item-body">' + body + '</div>';
  dom.getElementById('messages').appendChild(el);
  return el;
}
// an answer as the chat draws it
function answer(dom, html) {
  const el = dom.createElement('div');
  el.className = 'msg-assistant';
  el.innerHTML = html;
  dom.getElementById('messages').appendChild(el);
  return el;
}

// ------------------------------------------------------------------ pictures the agent looked at

test('a picture the agent looked at comes first in its card, and the card opens', async t => {
  const {w, dom} = await chat(t);
  const el = card(dom, '<pre class="out">the screen shows a login form</pre>');
  w.MusabCursor.decorate(el, {ok: true, args: {}, meta: {vision: true, shot: SHOT}});
  const img = el.querySelector('.item-body img.mp-shot');
  assert(img, 'the picture is in the card body');
  assert.equal(img.getAttribute('src'), '/api/shot/sess-1/0123456789abcdef.jpg');
  assert.equal(el.querySelector('.item-body').firstElementChild, img, 'the picture comes before the description');
  assert.equal(el.classList.contains('open'), true, 'the card opens so the picture shows without a tap');
});

test('tapping the picture enlarges it', async t => {
  const {w, dom} = await chat(t);
  const el = card(dom);
  w.MusabCursor.decorate(el, {ok: true, args: {}, meta: {shot: SHOT}});
  el.querySelector('.mp-shot').click();
  const sheet = dom.getElementById('musab-preview');
  assert.equal(sheet.hidden, false);
  assert.equal(sheet.querySelector('.mp-title').textContent, 'Screenshot');
  assert.equal(sheet.querySelector('.mp-body img').getAttribute('src'), '/api/shot/sess-1/0123456789abcdef.jpg');
});

test('a reference that is not a picture the engine kept is not shown', async t => {
  const {w, dom} = await chat(t);
  const refused = [
    null,
    {name: SHOT.name},                                      // no session: must not become "undefined"
    {sid: '../etc', name: SHOT.name},
    {sid: SHOT.sid, name: '../../x.jpg'},
    {sid: SHOT.sid, name: 'passwd.jpg'},
    {sid: SHOT.sid, name: '0123456789ABCDEF.jpg'},          // the engine writes lower-case hex
    {sid: SHOT.sid, name: SHOT.name.replace('.jpg', '.svg')},
    {sid: SHOT.sid, name: SHOT.name.replace('.jpg', '.html')},
    {sid: 'x'.repeat(81), name: SHOT.name},
  ];
  for (const shot of refused) {
    const el = card(dom);
    w.MusabCursor.decorate(el, {ok: true, args: {}, meta: {shot}});
    assert.equal(el.querySelector('.mp-shot'), null, JSON.stringify(shot));
    assert.equal(el.classList.contains('open'), false, JSON.stringify(shot));
  }
});

test('a card that already has its picture and preview button does not get another', async t => {
  const {w, dom} = await chat(t);
  const el = card(dom);
  const ev = {ok: true, args: {path: 'shots/home.png'}, meta: {shot: SHOT}};
  w.MusabCursor.decorate(el, ev);
  w.MusabCursor.decorate(el, ev);
  assert.equal(el.querySelectorAll('.mp-shot').length, 1);
  assert.equal(el.querySelectorAll('.mp-open').length, 1, 'the preview button is still there');
});

test('a card without a picture is left as it was', async t => {
  const {w, dom} = await chat(t);
  const el = card(dom, '<pre class="out">ok</pre>');
  w.MusabCursor.decorate(el, {ok: true, args: {}, meta: {vision: false}});
  assert.equal(el.querySelector('.mp-shot'), null);
  assert.equal(el.classList.contains('open'), false);
});

test('decorate never throws, even without a card or an event', async t => {
  const {w, dom} = await chat(t);
  assert.doesNotThrow(() => w.MusabCursor.decorate(null, {ok: true, meta: {shot: SHOT}}));
  assert.doesNotThrow(() => w.MusabCursor.decorate(card(dom), undefined));
});

// ------------------------------------------------------------------ Copy on code in an answer

test('an answer code block gets a Copy button that copies only the code', async t => {
  const copied = [];
  const {dom} = await chat(t, {clipboard: {writeText: async s => { copied.push(s); }}});
  const el = answer(dom, '<pre><code>print("hi")\nprint(2)</code></pre>');
  await tick();
  const button = el.querySelector('pre .code-copy');
  assert(button, 'the code block has a Copy button');
  button.click();
  await tick();
  assert.deepEqual(copied, ['print("hi")\nprint(2)']);
  assert.equal(button.textContent, 'Copied');
});

test('on the phone the code goes through the app clipboard', async t => {
  const phoneCopied = [], browserCopied = [];
  const {dom} = await chat(t, {
    phone: {setClipboard: s => phoneCopied.push(s)},
    clipboard: {writeText: async s => { browserCopied.push(s); }},
  });
  const el = answer(dom, '<pre><code>x = 1</code></pre>');
  await tick();
  el.querySelector('.code-copy').click();
  await tick();
  assert.deepEqual(phoneCopied, ['x = 1']);
  assert.deepEqual(browserCopied, []);
});

test('answers already on the page get the button when the chat starts', async t => {
  const {dom} = await chat(t, {body: '<div class="msg-assistant"><pre><code>ls</code></pre></div>'});
  assert(dom.querySelector('.msg-assistant pre .code-copy'));
});

test('an answer re-rendered while it streams gets its buttons again', async t => {
  const {dom} = await chat(t);
  const el = answer(dom, '');
  for (const line of ['first', 'second']) {
    el.innerHTML = '<p>step</p><pre><code>' + line + '</code></pre>';
    await tick();
    assert.equal(el.querySelectorAll('pre .code-copy').length, 1, line);
  }
});

test('tool output and the user\'s own messages get no Copy button', async t => {
  const {dom} = await chat(t);
  card(dom, '<pre class="out">total 0</pre>');
  const mine = dom.createElement('div');
  mine.className = 'msg-user';
  mine.innerHTML = '<pre><code>mine</code></pre>';
  dom.getElementById('messages').appendChild(mine);
  await tick();
  assert.equal(dom.querySelector('.code-copy'), null);
});

test('a copy that fails says so in the toast and leaves the button as it was', async t => {
  const {dom} = await chat(t);   // no clipboard at all, as in an old web view
  assert.equal(dom.defaultView.navigator.clipboard, undefined);
  const el = answer(dom, '<pre><code>x</code></pre>');
  await tick();
  const button = el.querySelector('.code-copy');
  button.click();
  await tick();
  const toast = dom.getElementById('toast');
  assert.equal(toast.hidden, false);
  assert.equal(toast.textContent, 'Copy failed');
  assert.equal(button.textContent, 'Copy');
});

test('an Arabic chat gets Arabic labels', async t => {
  const {dom} = await chat(t, {lang: 'ar', body: '<div class="msg-assistant"><pre><code>x</code></pre></div>'});
  assert.equal(dom.querySelector('.code-copy').textContent, 'نسخ');
});
