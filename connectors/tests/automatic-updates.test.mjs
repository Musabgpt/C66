import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const source = new URL('../automatic_updates.js', import.meta.url);

test('verified automatic activation requests Android restart once and ignores unready state', async t => {
  assert(existsSync(source), 'automatic application of verified updates is missing');
  const dom=new JSDOM('<html lang="ar"><body></body></html>',{pretendToBeVisual:true,runScripts:'outside-only',url:'http://localhost/'});
  t.after(()=>dom.window.close());
  let restarts=0, ready=false;
  dom.window.NewAlPhone={restartEngine(){restarts++;}};
  dom.window.fetch=async()=>({ok:true,json:async()=>({state:'activating',candidate:'verified-revision',restart_ready:ready})});
  dom.window.eval(readFileSync(source,'utf8'));
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  dom.window.dispatchEvent(new dom.window.Event('focus')); await tick();
  assert.equal(restarts,0);
  ready=true;
  dom.window.dispatchEvent(new dom.window.Event('focus')); await tick();
  assert.equal(restarts,1);
  dom.window.dispatchEvent(new dom.window.Event('focus')); await tick();
  assert.equal(restarts,1);
});
