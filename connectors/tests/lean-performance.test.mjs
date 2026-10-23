import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const code = readFileSync(new URL('../lean_ui.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function setup(t) {
  const dom = new JSDOM('<body><button id="open-connectors"></button><div id="messages"></div><div id="connector-dialog"><h2 id="connector-heading"></h2><div id="connector-list"></div></div></body>', {runScripts:'outside-only', url:'http://localhost'});
  t.after(() => dom.window.close());
  const w = dom.window, frames = [];
  w.requestAnimationFrame = fn => frames.push(fn);
  w.eval(code);
  await tick();
  const flush = async () => { for (let i = 0; frames.length && i < 10; i++) { frames.splice(0).forEach(fn => fn()); await tick(); } };
  await flush();
  return {w, frames, flush};
}
test('streaming chat does not schedule connector maintenance', async t => {
  const {w, frames} = await setup(t);
  for (let i = 0; i < 100; i++) {
    w.document.querySelector('#messages').append(w.document.createTextNode('token'));
    await tick();
  }
  assert.equal(frames.length, 0);
});
test('new connector cards are still filtered and language changes applied', async t => {
  const {w, flush} = await setup(t);
  const list = w.document.querySelector('#connector-list');
  list.innerHTML = '<div class="connector-card" id="keep"><button data-action="connect:github"></button></div><div class="connector-card" id="hide"><button data-action="connect:other"></button></div>';
  await tick(); await flush();
  assert.equal(w.document.querySelector('#hide').style.display, 'none');
  assert.notEqual(w.document.querySelector('#keep').style.display, 'none');
  w.document.documentElement.lang = 'ar';
  await tick(); await flush();
  assert.equal(w.document.querySelector('#connector-heading').textContent, 'الاتصالات');
});
test('connector dialog inserted after startup is maintained', async t => {
  const {w, flush} = await setup(t);
  w.document.querySelector('#connector-dialog').remove();
  const wrapper = w.document.createElement('section');
  wrapper.innerHTML = '<div id="connector-dialog"><h2 id="connector-heading">Old</h2></div>';
  w.document.body.append(wrapper);
  await tick(); await flush();
  assert.equal(w.document.querySelector('#connector-heading').textContent, 'Connections');
});
