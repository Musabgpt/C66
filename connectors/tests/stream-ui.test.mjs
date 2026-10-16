import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const code = readFileSync(new URL('../stream_ui.js', import.meta.url), 'utf8');

test('recovery removes only the failed draft and keeps completed tool results', t => {
  const dom = new JSDOM('<body><p id="answer">Earlier reply</p><p id="reason">Earlier reasoning</p><pre id="tool">File written</pre></body>', {runScripts:'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document;
  w.eval(code);
  const view = {text:d.querySelector('#answer'), reasoning:d.querySelector('#reason')};
  w.MusabStreamView.begin(view);
  for (const key of ['text', 'reasoning']) {
    view[key] = d.createElement('p');
    view[key].textContent = 'Failed draft';
    d.body.append(view[key]);
  }
  w.MusabStreamView.reset(view);
  assert(!d.body.textContent.includes('Failed draft'));
  assert(d.querySelector('#answer'));
  assert(d.querySelector('#reason'));
  assert.equal(d.querySelector('#tool').textContent, 'File written');
  assert.equal(view.text, null);
  assert.equal(view.reasoning, null);
  w.MusabStreamView.reset(view); // repeated/replayed resets remain harmless
  assert(d.querySelector('#tool'));
});

test('a subtask reset cannot remove another request draft', t => {
  const dom = new JSDOM('<body><p id="main">Main draft</p><p id="sub">Subtask draft</p></body>', {runScripts:'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document;
  w.eval(code);
  w.MusabStreamView.reset({text:d.querySelector('#sub')});
  assert.equal(d.querySelector('#main').textContent, 'Main draft');
  assert.equal(d.querySelector('#sub'), null);
});
