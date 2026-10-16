import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
const code = readFileSync(new URL('../ui.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function panel(t, item, bundle = null, session = 'project') {
  const dom = new JSDOM('<html lang="ar"><div class="side-top"></div><button id="open-github"></button></html>', {pretendToBeVisual: true, runScripts: 'outside-only', url: 'http://localhost/'});
  t.after(() => dom.window.close());
  const w = dom.window;
  const state = {item, bundle, calls: [], posts: []};
  w.NewAlWorkspaceSession = () => session;
  w.fetch = async (url, options = {}) => {
    state.calls.push(url);
    const body = options.body ? JSON.parse(options.body) : null;
    if (options.method === 'POST') {
      state.posts.push({url, body});
      return {ok: true, json: async () => ({ok: true, tools: 3, running: String(url).endsWith('/start') || String(url).endsWith('/reconnect')})};
    }
    if (String(url).startsWith('/api/mcp-bundles')) {
      return {ok: true, json: async () => ({bundles: state.bundle ? [state.bundle] : []})};
    }
    if (String(url).startsWith('/api/extensions')) {
      return {ok: true, json: async () => ({extensions: []})};
    }
    return {ok: true, json: async () => ({connectors: [state.item]})};
  };
  w.eval(code);
  await tick();
  w.openMusabConnectors();
  await tick();
  state.dom = w.document;
  state.refresh = async () => { w.dispatchEvent(new w.Event('focus')); await tick(); };
  state.setBundle = async value => { state.bundle = value; await state.refresh(); };
  return state;
}
test('unchanged refresh preserves focused button and scrolled content', async t => {
  const state = await panel(t, {id: 'github', name: 'GitHub', configured: true, status: 'disconnected'});
  const button = state.dom.querySelector('[data-action="connect:github"]');
  button.focus();
  state.dom.querySelector('.connector-panel').scrollTop = 180;
  await state.refresh();
  assert.equal(state.dom.querySelector('[data-action="connect:github"]'), button);
  assert.equal(state.dom.activeElement, button);
  assert.equal(state.dom.querySelector('.connector-panel').scrollTop, 180);
});
test('failed browser flow offers reconnect without an unusable account test', async t => {
  const state = await panel(t, {id: 'github', name: 'GitHub', configured: true, status: 'error', has_credentials: false});
  assert.equal(state.dom.querySelector('[data-action="connect:github"]').disabled, false);
  assert.equal(state.dom.querySelector('[data-action="test:github"]'), null);
});
test('unchanged response after an action restores enabled controls', async t => {
  const state = await panel(t, {id: 'github', name: 'GitHub', configured: true, status: 'error', has_credentials: false});
  state.dom.querySelector('[data-action="disconnect:github"]').click();
  await tick();
  assert(state.calls.includes('/api/connectors/disconnect'));
  assert.equal(state.dom.querySelector('[data-action="connect:github"]').disabled, false);
  assert.equal(state.dom.querySelector('[data-action="disconnect:github"]').disabled, false);
});
test('unconfigured provider has an Arabic explanation and disabled connect', async t => {
  const state = await panel(t, {id: 'gmail', name: 'Gmail', configured: false, status: 'not_configured', error: 'OAuth application deployment required'});
  assert.equal(state.dom.querySelector('[data-action="connect:gmail"]').disabled, true);
  assert(state.dom.querySelector('#connector-list').textContent.includes('صاحب التطبيق'));
  assert(!state.dom.querySelector('#connector-list').textContent.includes('OAuth application deployment required'));
});

test('Termux retains its callback test without OAuth credentials', async t => {
  const state = await panel(t, {id: 'termux', name: 'Termux', configured: true, status: 'connected'});
  assert(state.dom.querySelector('[data-action="test:termux"]'));
});



test('MCP install explains missing project session instead of silently disabling the button', async t => {
  const state = await panel(t,
    {id: 'termux', name: 'Termux', configured: true, status: 'connected'},
    {id:'playwright', name:'Playwright MCP', installed:false, running:false, available:true,
      status:'ready', tools:0, missing:[]},
    ''
  );
  const button = state.dom.querySelector('[data-action="mcp:enable:playwright"]');
  assert(button);
  assert.equal(button.disabled, false);
  button.click();
  await tick();
  assert(state.dom.querySelector('#connector-message').textContent.includes('افتح محادثة أو مشروعًا أولًا'));
  assert.equal(state.posts.length, 0);
});

test('Termux callback test shows explicit success feedback', async t => {
  const state = await panel(t, {id: 'termux', name: 'Termux', configured: true, status: 'connected'});
  state.dom.querySelector('[data-action="test:termux"]').click();
  await tick(); await tick();
  assert(state.posts.some(x => x.url === '/api/connectors/connect'));
  assert(state.dom.querySelector('#connector-message').textContent.includes('نجح اختبار Termux'));
});

test('Musab Hub renders stopped MCP separately from installed state and exposes real lifecycle controls', async t => {
  const state = await panel(t,
    {id: 'github', name: 'GitHub', configured: true, status: 'disconnected'},
    {id:'playwright', name:'Playwright MCP', description:'Browser tools', installed:true, running:false,
      can_start:true, status:'server_stopped', tools:5, runtime_reason:'Termux bridge verified', missing:[]}
  );
  const row = state.dom.querySelector('.mcp-bundle');
  assert(row);
  assert(row.textContent.includes('الخادم متوقف'));
  assert(row.textContent.includes('5'));
  assert(row.querySelector('[data-action="mcp:start:playwright"]'));
  assert(row.querySelector('[data-action="mcp:reconnect:playwright"]'));
  assert(!row.querySelector('[data-action="mcp:stop:playwright"]'));
});

test('MCP Start posts to the lifecycle endpoint with the current project session', async t => {
  const state = await panel(t,
    {id: 'github', name: 'GitHub', configured: true, status: 'disconnected'},
    {id:'playwright', name:'Playwright MCP', installed:true, running:false, can_start:true,
      status:'server_stopped', tools:5, missing:[]}
  );
  state.dom.querySelector('[data-action="mcp:start:playwright"]').click();
  await tick(); await tick();
  const post = state.posts.find(x => x.url === '/api/mcp-bundles/start');
  assert(post);
  assert.deepEqual(post.body, {session:'project', id:'playwright'});
});

test('live refresh changes MCP controls from Start to Stop without reopening Musab Hub', async t => {
  const state = await panel(t,
    {id: 'github', name: 'GitHub', configured: true, status: 'disconnected'},
    {id:'memory', name:'Memory MCP', installed:true, running:false, can_start:true,
      status:'server_stopped', tools:2, missing:[]}
  );
  assert(state.dom.querySelector('[data-action="mcp:start:memory"]'));
  await state.setBundle({id:'memory', name:'Memory MCP', installed:true, running:true, can_start:false,
    status:'server_running', tools:2, missing:[]});
  assert(state.dom.querySelector('[data-action="mcp:stop:memory"]'));
  assert(state.dom.querySelector('.mcp-bundle').textContent.includes('الخادم يعمل'));
});

test('Termux preparation renders progress, exact failure, and verified versions independently of chat', async t => {
  const state = await panel(t, {id:'termux', name:'Termux', configured:true, status:'connected', dependencies:{state:'installing'}});
  const text = () => state.dom.querySelector('#connector-list').textContent;
  assert(text().includes('جارٍ التثبيت والتحقق'));
  assert(!text().includes('جاهزان'));
  state.item.dependencies = {state:'failed', error:'pkg: repository unavailable'};
  await state.refresh();
  assert(text().includes('pkg: repository unavailable'));
  assert(!text().includes('جاهزان'));
  state.item.dependencies = {state:'ready', output:'uv 0.12.23\nuvx 0.12.23'};
  await state.refresh();
  assert(text().includes('جاهزان وتم التحقق داخل Termux'));
  assert(text().includes('uvx 0.12.23'));
});

test('unconfigured Google explains OAuth setup without pretending account access exists', async t => {
  const state = await panel(t, {id:'gmail',name:'Gmail',configured:false,status:'not_configured'});
  assert(state.dom.querySelector('#connector-list').textContent.includes('مفتاح API وحده'));
  assert.equal(state.dom.querySelector('a[href="https://console.cloud.google.com/apis/credentials"]').target, '_blank');
  assert.equal(state.dom.querySelector('[data-action="connect:gmail"]').disabled, true);
});

test('hidden Hub stops polling and refreshes once visible', async t => {
  const state = await panel(t, {id:'termux',name:'Termux',configured:true,status:'connected'});
  const before = state.calls.length;
  Object.defineProperty(state.dom, 'hidden', {configurable:true,value:true});
  await state.refresh();
  assert.equal(state.calls.length, before);
  Object.defineProperty(state.dom, 'hidden', {configurable:true,value:false});
  await state.refresh();
  assert.equal(state.calls.length, before + 3);
});

test('one failing Hub section does not hide working accounts and MCP cards', async t => {
  const state = await panel(t, {id:'github',name:'GitHub',configured:true,status:'connected'},
    {id:'memory',name:'Memory',installed:true,running:true,status:'server_running'});
  const w = state.dom.defaultView, fetch = w.fetch;
  w.fetch = async (url, options) => {
    if (String(url).startsWith('/api/extensions')) throw new Error('Extension catalog offline');
    return fetch(url, options);
  };
  state.item = {...state.item, account:'Musabgpt'};
  await state.refresh();
  assert(state.dom.querySelector('#connector-list').textContent.includes('Musabgpt'));
  assert(state.dom.querySelector('[data-action="mcp:stop:memory"]'));
  assert(state.dom.querySelector('#extension-list').textContent.includes('Extension catalog offline'));
});

test('operation feedback stays beside its MCP card', async t => {
  const state = await panel(t, {id:'termux',name:'Termux',configured:true,status:'connected'},
    {id:'memory',name:'Memory',installed:true,running:false,can_start:true,status:'server_stopped'});
  state.dom.querySelector('[data-action="mcp:start:memory"]').click();
  await tick(); await tick();
  assert(state.dom.querySelector('.mcp-bundle [role="status"]').textContent.includes('تم تشغيل الخادم'));
});
