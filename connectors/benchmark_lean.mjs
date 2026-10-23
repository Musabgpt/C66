// Deterministic work-count benchmark; no cloud requests or timing claims.
// Usage: node connectors/benchmark_lean.mjs [path/to/lean_ui.js]
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const code = readFileSync(process.argv[2] || new URL('./lean_ui.js', import.meta.url), 'utf8');
const dom = new JSDOM('<body><button id="open-connectors"></button><div id="messages"></div><div id="connector-dialog"><h2 id="connector-heading"></h2><div id="connector-list"></div></div></body>', {url:'http://localhost', runScripts:'outside-only'});
const w = dom.window;
const frames = [];
let scheduled = 0;
w.requestAnimationFrame = fn => { scheduled++; frames.push(fn); };
const tick = () => new Promise(resolve => setImmediate(resolve));
async function flush() {
  await tick();
  for (let i = 0; frames.length && i < 20; i++) {
    frames.splice(0).forEach(fn => fn());
    await tick();
  }
  if (frames.length) throw new Error('Maintenance did not settle');
}
try {
  w.eval(code);
  await flush();
  scheduled = 0;
  const messages = w.document.querySelector('#messages');
  for (let i = 0; i < 100; i++) {
    messages.append(w.document.createTextNode('token '));
    await flush();
  }
  console.log(JSON.stringify({chatUpdates:100, connectorMaintenanceFrames:scheduled}));
} finally { w.close(); }
