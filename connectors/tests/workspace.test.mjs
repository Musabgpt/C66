import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const code = readFileSync(new URL('../workspace.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const memory = () => ({enabled: true, counts: {lessons: 1, episodes: 2}, lessons: [
  {id: 1, topic: 'Build checks', lesson: 'Run the compiler first.', evidence: ['Build failed: missing dependency.']},
], recent_failures: [{task: 'Compile package', error: 'Missing dependency', evidence: 'Compiler exit 1'}]});
const button = (root, label) => [...root.querySelectorAll('button')].find(n => n.textContent === label || n.getAttribute('aria-label') === label);

async function app(t, {lang = 'en', sid = 'project-one', phone, handler} = {}) {
  const dom = new JSDOM(`<html lang="${lang}"><body><div class="side-top"></div><textarea id="input"></textarea></body></html>`, {runScripts: 'outside-only', url: 'http://localhost/'});
  t.after(() => dom.window.close());
  const w = dom.window, calls = [];
  w.NewAlWorkspaceSession = () => sid;
  if (phone) w.NewAlPhone = phone;
  w.fetch = async (url, options) => {
    const call = {url, method: options.method, body: options.body ? JSON.parse(options.body) : undefined};
    calls.push(call);
    const value = handler ? await handler(call) : undefined;
    if (value instanceof Error) throw value;
    const data = value ?? (url.startsWith('/api/memory?') ? memory() : url === '/api/evolution' ? {candidates: []} : {files: []});
    return {ok: !data.error, json: async () => data};
  };
  w.eval(code);
  await tick();
  const entry = button(w.document, lang === 'ar' ? 'مساحة العمل' : 'Workspace');
  assert(entry, 'the sidebar exposes one Workspace entry');
  entry.focus(); entry.click(); await tick();
  return {w, doc: w.document, calls, entry,
    async tab(label) { button(w.document.querySelector('[role="tablist"]'), label).click(); await tick(); },
  };
}

test('one workspace entry contains accessible tabs and restores focus on Escape', async t => {
  const {w, doc, entry} = await app(t);
  assert.equal(doc.querySelectorAll('.side-top button').length, 1);
  const modal = doc.querySelector('[role="dialog"]');
  assert.equal(modal.getAttribute('aria-modal'), 'true');
  assert.equal(doc.getElementById(modal.getAttribute('aria-labelledby')).textContent, 'Workspace');
  const tabs = [...doc.querySelectorAll('[role="tab"]')];
  assert.deepEqual(tabs.map(n => n.textContent), ['Files', 'Memory', 'Improve', 'Tasks']);
  assert.equal(tabs[0].getAttribute('aria-selected'), 'true');
  assert.deepEqual(tabs.map(n => n.tabIndex), [0, -1, -1, -1]);
  tabs[0].focus(); tabs[0].dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
  await tick();
  assert.equal(doc.activeElement, tabs[1]);
  assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
  assert.deepEqual(tabs.map(n => n.tabIndex), [-1, 0, -1, -1]);
  doc.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert.equal(doc.querySelector('[role="dialog"]'), null);
  assert.equal(doc.activeElement, entry);
});

test('workspace traps focus at its boundaries and tabs support Home and End', async t => {
  const {w, doc} = await app(t);
  const modal = doc.querySelector('[role="dialog"]');
  const close = button(modal, 'Close');
  const create = button(modal, 'Create a document');
  close.focus();
  close.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true}));
  assert.equal(doc.activeElement, create);
  create.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true}));
  assert.equal(doc.activeElement, close);
  const tabs = [...doc.querySelectorAll('[role="tab"]')];
  tabs[0].focus();
  tabs[0].dispatchEvent(new w.KeyboardEvent('keydown', {key: 'End', bubbles: true}));
  await tick();
  assert.equal(doc.activeElement, tabs.at(-1));
  assert.equal(tabs.at(-1).getAttribute('aria-selected'), 'true');
  tabs.at(-1).dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Home', bubbles: true}));
  await tick();
  assert.equal(doc.activeElement, tabs[0]);
  assert.equal(tabs[0].getAttribute('aria-selected'), 'true');
});

test('Arabic workspace includes Arabic tab labels', async t => {
  const {doc} = await app(t, {lang: 'ar'});
  assert.deepEqual([...doc.querySelectorAll('[role="tab"]')].map(n => n.textContent), ['الملفات', 'الذاكرة', 'التطوير', 'المهام']);
});

test('memory and files require a conversation and never query the global project', async t => {
  const state = await app(t, {sid: ''});
  assert.equal(state.calls.length, 0);
  await state.tab('Memory');
  assert.match(state.doc.querySelector('[role="tabpanel"]').textContent, /Open a conversation or project first/);
  assert.equal(state.calls.length, 0);
  assert.equal(state.doc.querySelector('input[type="checkbox"]'), null);
});

test('memory displays stored lessons, evidence and failures as plain text and searches within the session', async t => {
  const state = await app(t, {handler: ({url}) => url.startsWith('/api/memory?') ? {
    ...memory(), lessons: [{id: 1, topic: '<img src=x onerror=alert(1)>', lesson: '<script>bad()</script>', evidence: {output: '<b>raw evidence</b>'}}],
  } : undefined});
  await state.tab('Memory');
  const panel = state.doc.querySelector('[role="tabpanel"]');
  assert.match(panel.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(panel.textContent, /<script>bad\(\)<\/script>/);
  assert.match(panel.textContent, /<b>raw evidence<\/b>/);
  assert.match(panel.textContent, /Missing dependency/);
  assert.match(panel.textContent, /1 lessons.*2 episodes/);
  assert.equal(panel.querySelector('img,script,b'), null);
  const input = panel.querySelector('input[type="search"]');
  assert(input.labels.length > 0);
  input.value = 'compiler & tests';
  button(panel, 'Search').click(); await tick();
  const query = new URL(state.calls.at(-1).url, state.w.location.href).searchParams;
  assert.equal(query.get('session'), 'project-one');
  assert.equal(query.get('query'), 'compiler & tests');
});

test('memory can be disabled and an unsuccessful settings change restores its previous value', async t => {
  let enabled = true, fail = false;
  const state = await app(t, {handler: call => {
    if (call.url.startsWith('/api/memory?')) return {...memory(), enabled};
    if (call.url === '/api/memory/settings') {
      if (fail) return {error: 'Storage is read-only'};
      enabled = call.body.enabled; return {ok: true, enabled};
    }
  }});
  await state.tab('Memory');
  let toggle = state.doc.querySelector('input[type="checkbox"]');
  assert.equal(toggle.checked, true);
  toggle.click(); await tick();
  assert.deepEqual(state.calls.find(c => c.url === '/api/memory/settings').body, {session: 'project-one', enabled: false});
  toggle = state.doc.querySelector('input[type="checkbox"]');
  assert.equal(toggle.checked, false);
  fail = true; toggle.click(); await tick();
  assert.equal(toggle.checked, false);
  assert.match(state.doc.querySelector('[role="alert"]').textContent, /Storage is read-only/);
});

test('deleting a lesson posts its session and id then refreshes visible records', async t => {
  let deleted = false;
  const state = await app(t, {handler: call => {
    if (call.url === '/api/memory/forget') {deleted = true; return {ok: true};}
    if (call.url.startsWith('/api/memory?')) return {...memory(), lessons: deleted ? [] : memory().lessons, counts: {lessons: deleted ? 0 : 1, episodes: 2}};
  }});
  await state.tab('Memory');
  button(state.doc, 'Delete lesson: Build checks').click(); await tick();
  assert.deepEqual(state.calls.find(c => c.url === '/api/memory/forget').body, {session: 'project-one', id: 1});
  assert.doesNotMatch(state.doc.querySelector('[role="tabpanel"]').textContent, /Run the compiler first/);
  assert.match(state.doc.querySelector('[role="tabpanel"]').textContent, /No stored lessons/);
});

test('clear requires explicit confirmation and Escape closes only the confirmation', async t => {
  const state = await app(t);
  await state.tab('Memory');
  const clear = button(state.doc, 'Clear memory'); clear.focus(); clear.click();
  assert.equal(state.doc.querySelectorAll('[role="dialog"]').length, 2);
  assert.equal(state.calls.filter(c => c.method === 'POST').length, 0);
  state.doc.activeElement.dispatchEvent(new state.w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert.equal(state.doc.querySelectorAll('[role="dialog"]').length, 1);
  assert.equal(state.doc.activeElement, clear);
  clear.click(); button(state.doc, 'Clear stored memory').click(); await tick();
  assert.deepEqual(state.calls.find(c => c.url === '/api/memory/clear').body, {session: 'project-one', confirm: true});
  assert.equal(state.doc.querySelectorAll('[role="dialog"]').length, 1);
});

test('failed memory reads show an actionable retry and recover', async t => {
  let fail = true;
  const state = await app(t, {handler: ({url}) => url.startsWith('/api/memory?') && fail ? new Error('Offline') : undefined});
  await state.tab('Memory');
  assert.match(state.doc.querySelector('[role="alert"]').textContent, /Offline/);
  fail = false; button(state.doc, 'Retry').click(); await tick();
  assert.match(state.doc.querySelector('[role="tabpanel"]').textContent, /Run the compiler first/);
});

test('memory reads show loading and late responses cannot replace another tab', async t => {
  let resolve;
  const pending = new Promise(r => {resolve = r;});
  const state = await app(t, {handler: ({url}) => url.startsWith('/api/memory?') ? pending : undefined});
  await state.tab('Memory');
  assert.match(state.doc.querySelector('[role="status"]').textContent, /Loading memory/);
  await state.tab('Improve');
  resolve(memory()); await tick();
  assert.match(state.doc.querySelector('[role="tabpanel"]').textContent, /Prepare improvement/);
  assert.doesNotMatch(state.doc.querySelector('[role="tabpanel"]').textContent, /Run the compiler first/);
});

test('Files preserves import, document reading, download and chat workflows', async t => {
  let imported = false;
  const file = {path: 'report.md', name: 'report.md', format: 'md', bytes: 20};
  const state = await app(t, {handler: call => {
    if (call.url.startsWith('/api/documents?')) return {files: imported ? [file, {...file, path: 'notes.txt', name: 'notes.txt', format: 'txt'}] : [file]};
    if (call.url === '/api/documents/import') {imported = true; return {ok: true};}
    if (call.url === '/api/documents/read') return {text: '# Read <b>literally</b>'};
  }});
  const save = [...state.doc.querySelectorAll('a')].find(n => n.textContent === 'Save');
  assert.equal(new URL(save.href).searchParams.get('session'), 'project-one');
  assert.equal(save.download, 'report.md');
  const read = button(state.doc, 'Read'); read.focus(); read.click(); await tick();
  assert.equal(state.doc.querySelector('pre').textContent, '# Read <b>literally</b>');
  state.doc.activeElement.dispatchEvent(new state.w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert.equal(state.doc.activeElement, read);
  const upload = state.doc.querySelector('input[type="file"]');
  Object.defineProperty(upload, 'files', {value: [new state.w.File(['hello'], 'notes.txt', {type: 'text/plain'})]});
  upload.dispatchEvent(new state.w.Event('change', {bubbles: true}));
  for (let i = 0; i < 20 && !state.calls.some(c => c.url === '/api/documents/import'); i++) await new Promise(r => setTimeout(r, 5));
  await tick();
  assert.deepEqual(state.calls.find(c => c.url === '/api/documents/import').body, {session: 'project-one', path: 'notes.txt', data: 'aGVsbG8='});
  button(state.doc, 'Work on it').click();
  assert.match(state.doc.querySelector('#input').value, /report.md/);
  assert.equal(state.doc.querySelector('[role="dialog"]'), null);
  assert.equal(state.doc.activeElement.id, 'input');
});

test('Improve preserves verified activation, rollback and preparing a chat request', async t => {
  const state = await app(t, {handler: call => call.url === '/api/evolution' ? {candidates: [
    {id: '1234567890abcdef12345678', goal: 'Faster startup', status: 'verified'}, {id: 'abcdef1234567890abcdef12', goal: 'Draft idea', status: 'prepared'},
  ]} : call.url === '/api/evolution/rollback' ? {ok: true, restart_required: true, text: 'Restored original engine'}
    : call.url === '/api/evolution/activate' ? {ok: true, restart_required: true, text: 'Saved. Restart the engine to use the verified candidate.'} : undefined});
  await state.tab('Improve');
  assert.equal([...state.doc.querySelectorAll('button')].filter(n => n.textContent === 'Activate').length, 1);
  button(state.doc, 'Activate').click(); await tick();
  assert.deepEqual(state.calls.find(c => c.url === '/api/evolution/activate').body, {candidate: '1234567890abcdef12345678'});
  button(state.doc, 'Restore original engine').click(); await tick();
  assert(state.calls.some(c => c.url === '/api/evolution/rollback' && c.method === 'POST'));
  state.doc.querySelector('[role="tabpanel"] textarea').value = 'Improve startup';
  button(state.doc, 'Prepare improvement').click();
  assert.match(state.doc.querySelector('#input').value, /self_evolve_verify.*Improve startup/);
  assert.equal(state.doc.querySelector('[role="dialog"]'), null);
});

for (const [label, path] of [['Activate', '/api/evolution/activate'], ['Restore original engine', '/api/evolution/rollback']]) {
  test(`${label} exposes native restart only after success and requires a separate click`, async t => {
    let restarts = 0;
    const state = await app(t, {phone: {restartEngine() {restarts++;}}, handler: call => {
      if (call.url === '/api/evolution') return {candidates: [{id: '1234567890abcdef12345678', goal: 'Faster startup', status: 'verified'}]};
      if (call.url === path) return {ok: true, restart_required: true, text: 'Restart the engine to apply this selection.'};
    }});
    await state.tab('Improve');
    assert.equal(button(state.doc, 'Restart engine'), undefined);
    assert.equal(restarts, 0);
    button(state.doc, label).click(); await tick();
    assert(state.calls.some(call => call.url === path && call.method === 'POST'));
    assert.equal(restarts, 0, 'selecting an engine must not restart it automatically');
    const restart = button(state.doc, 'Restart engine');
    assert(restart, 'successful selection offers an explicit native restart');
    restart.click(); await tick();
    assert.equal(restarts, 1);
  });

  test(`${label} does not offer native restart when selection fails`, async t => {
    let restarts = 0;
    const state = await app(t, {phone: {restartEngine() {restarts++;}}, handler: call => {
      if (call.url === '/api/evolution') return {candidates: [{id: '1234567890abcdef12345678', goal: 'Faster startup', status: 'verified'}]};
      if (call.url === path) return {error: 'Selection could not be saved'};
    }});
    await state.tab('Improve');
    button(state.doc, label).click(); await tick();
    assert.match(state.doc.querySelector('[role="alert"]').textContent, /Selection could not be saved/);
    assert.equal(button(state.doc, 'Restart engine'), undefined);
    assert.equal(restarts, 0);
  });
}


test('Improve shows truthful Phase 9 state and exposes a verified staged update', async t => {
  const state = await app(t, {handler: call => call.url === '/api/evolution' ? {
    current: {version_code: 304, revision: 'packaged'},
    update: {state: 'failed_verification', error: 'hash mismatch'},
    candidates: [
      {id: '999999999999999999999999', goal: 'Candidate 305', status: 'ready_to_activate'}
    ]
  } : undefined});
  await state.tab('Improve');
  const panel = state.doc.querySelector('[role="tabpanel"]');
  assert.match(panel.textContent, /failed_verification/);
  assert.match(panel.textContent, /v304/);
  assert(button(panel, 'Activate'), 'ready_to_activate is actionable only after verification');
  assert.doesNotMatch(panel.textContent, /\bupdated\b/i);
});

test('Improve exposes automatic update failure and requests verified automatic activation', async t => {
  const state = await app(t, {handler: call => call.url === '/api/evolution' ? {
    current: {version_code: 332, revision: 'packaged'}, update: {state: 'up_to_date'}, candidates: [],
    automatic: {state: 'failed_verification', error: 'Downloaded engine hash mismatch'}
  } : undefined});
  await state.tab('Improve');
  const panel = state.doc.querySelector('[role="tabpanel"]');
  assert.match(panel.textContent, /failed_verification/);
  assert.match(panel.textContent, /Downloaded engine hash mismatch/);
  panel.querySelector('textarea').value = 'Repair runtime';
  button(panel, 'Prepare improvement').click();
  assert.match(state.doc.querySelector('#input').value, /self_evolve_activate/);
  assert.doesNotMatch(state.doc.querySelector('#input').value, /do not activate/);
});

test('Improve does not hide a failed channel check before the first update is staged', async t => {
  const state = await app(t, {handler: call => call.url === '/api/evolution' ? {
    current: {version_code: 333, revision: 'packaged'}, update: {state: 'up_to_date'}, candidates: [],
    automatic: {enabled: true, error: 'Update channel network unavailable'}
  } : undefined});
  await state.tab('Improve');
  const panel = state.doc.querySelector('[role="tabpanel"]');
  assert.match(panel.textContent, /Update channel network unavailable/);
  assert.doesNotMatch(panel.textContent, /up_to_date/);
});

test('Tasks shows durable progress and prepares continuation without claiming execution', async t => {
  const state = await app(t, {handler: ({url}) => url.startsWith('/api/task-state?') ? {
    running:false, active_task:'saved-1', tasks:[{id:'saved-1',objective:'Build calculator',status:'active',
      checkpoint:{progress:'File written',next_step:'Test the page',blocker:'Provider unavailable'}}]
  } : undefined});
  await state.tab('Tasks');
  assert.match(state.doc.querySelector('[role="tabpanel"]').textContent, /File written/);
  assert.match(state.doc.querySelector('[role="tabpanel"]').textContent, /Provider unavailable/);
  assert(state.calls.at(-1).url.endsWith('session=project-one'));
  button(state.doc, 'Prepare continuation').click();
  assert.match(state.doc.querySelector('#input').value, /task_resume/);
  assert(!state.calls.some(call => call.method === 'POST'));
});

test('Tasks never offers a duplicate run while the conversation is active', async t => {
  const state = await app(t, {handler: ({url}) => url.startsWith('/api/task-state?') ? {
    running:true, active_task:'saved-1',tasks:[{id:'saved-1',objective:'Build',status:'active',checkpoint:{}}]
  } : undefined});
  await state.tab('Tasks');
  assert.equal(button(state.doc, 'Prepare continuation'), undefined);
});

test('Improve update switch uses saved state and can pause and enable the worker', async t => {
  const state = await app(t, {handler: call => call.url === '/api/evolution' ? {
    candidates: [], automatic: {enabled: false}
  } : call.url === '/api/evolution/automatic' ? {enabled: call.body.enabled} : undefined});
  await state.tab('Improve');
  button(state.doc, 'Enable automatic updates').click(); await tick();
  assert.deepEqual(state.calls.at(-1).body, {enabled: true});
  button(state.doc, 'Pause automatic updates').click(); await tick();
  assert.deepEqual(state.calls.at(-1).body, {enabled: false});
  assert(button(state.doc, 'Enable automatic updates'));
});
