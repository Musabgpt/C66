import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const code = readFileSync(new URL('../mcp_ui.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

async function setup(t, fail = false) {
  const dom = new JSDOM(
    '<html lang="ar"><div class="connector-panel"><div id="connector-list"></div></div></html>',
    {runScripts:'outside-only',url:'http://localhost/'}
  );
  t.after(() => dom.window.close());
  const w = dom.window, calls = []; let sid = 'project';
  w.NewAlWorkspaceSession = () => sid;
  w.fetch = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body);
    calls.push({path, method: options.method || 'GET', body});
    const error = fail && options.method === 'POST';
    if (error) return {ok:false,json:async () => ({error:'authentication required'})};
    if (String(path).startsWith('/api/free-providers')) {
      if (options.method === 'POST') return {ok:true,json:async () => (
        String(path).endsWith('/test') ? {ok:true,connected:true} : {ok:true,configured:true}
      )};
      return {ok:true,json:async () => ({free_only:true,providers:[
        {id:'groq',name:'Groq Free',configured:false},
        {id:'nvidia',name:'NVIDIA Free',configured:true}
      ]})};
    }
    if (String(path).startsWith('/api/mcp-bundles')) {
      if (options.method === 'POST') return {ok:true,json:async () => ({ok:true,tools:7,enabled:true})};
      return {ok:true,json:async () => ({bundles:[
        {id:'playwright',name:'Microsoft Playwright MCP',description:'Browser automation',status:'available',available:true,enabled:false,missing:[]},
        {id:'github',name:'GitHub MCP Server',description:'GitHub tools',status:'runtime_missing',available:false,enabled:false,missing:['github-mcp-server']}
      ]})};
    }
    if (String(path).startsWith('/api/browser-router')) {
      return {ok:true,json:async () => ({
        route:'playwright',
        label:'Microsoft Playwright MCP',
        reason:'Playwright is the preferred structured browser backend.',
        availability:{api:false,service_mcp:false,playwright:true,browser_use:false,open_browser_use:false,android_session:true}
      })};
    }
    if (String(path).startsWith('/api/document-engine')) {
      return {ok:true,json:async () => ({
        route:'docling',
        label:'Docling MCP',
        reason:'A verified Docling MCP server is exposed and this task benefits from structured document understanding.',
        availability:{docling:true,native:true}
      })};
    }
    if (String(path).startsWith('/api/search-layer')) {
      if (options.method === 'POST') {
        if (String(path).endsWith('/test')) {
          return {ok:true,json:async () => body?.provider === 'searxng'
            ? ({ok:true,results:1}) : ({ok:true,version:'0.8.6'})};
        }
        return {ok:true,json:async () => ({ok:true})};
      }
      return {ok:true,json:async () => ({
        searxng:'https://search.example',
        crawl4ai:'https://crawl.example',
        crawl4ai_token:false,
        order:['searxng','crawl4ai']
      })};
    }
    if (String(path) === '/api/connectors') {
      return {ok:true,json:async () => ({ok:true,connectors:[
        {id:'activepieces',name:'Activepieces Automation Hub',status:'disconnected',configured:true,has_credentials:false,tool_count:0}
      ]})};
    }
    if (String(path).startsWith('/api/connectors/')) {
      return {ok:true,json:async () => ({ok:true,status:'connected',tool_count:11})};
    }
    if (String(path).endsWith('/save')) return {ok:true,json:async () => ({ok:true,tools:2})};
    return {ok:true,json:async () => ({servers:[{name:'fixture',url:'https://example.com/mcp',tools:2,bundle:''}]})};
  };
  w.eval(code); await tick();
  w.document.querySelector('#mcp-name').value = 'fixture';
  w.document.querySelector('#mcp-url').value = 'https://example.com/mcp';
  w.document.querySelector('#mcp-token').value = 'private-token';
  return {w,calls,setSession:s => sid=s};
}

test('successful custom addition discovers tools and clears the password field', async t => {
  const {w,calls} = await setup(t);
  w.document.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true})); await tick();
  const request = calls.find(c => c.path === '/api/mcp-servers/save');
  assert.equal(request.body.session,'project');
  assert.equal(request.body.token,'private-token');
  assert.equal(w.document.querySelector('#mcp-token').value,'');
  assert.equal(w.document.body.textContent.includes('private-token'),false);
});

test('failed authentication never shows a saved custom connection', async t => {
  const {w,calls} = await setup(t,true);
  const before = calls.length;
  w.document.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true})); await tick();
  assert.equal(calls.length,before+1);
  assert.match(w.document.querySelector('.custom-mcp [role="status"]').textContent,/authentication required/);
  assert.equal(w.document.querySelector('button[type="submit"]').disabled,false);
});

test('no project means no custom MCP connection request', async t => {
  const {w,calls,setSession} = await setup(t); setSession(null);
  const before = calls.filter(c => c.path === '/api/mcp-servers/save').length;
  w.document.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true})); await tick();
  assert.equal(calls.filter(c => c.path === '/api/mcp-servers/save').length,before);
  assert.match(w.document.querySelector('.custom-mcp [role="status"]').textContent,/مشروع/);
});

test('Activepieces card sends only the server URL and opens the native OAuth flow', async t => {
  const {w,calls} = await setup(t);
  const input = w.document.querySelector('#activepieces-mcp-url');
  assert(input);
  input.value = 'https://automation.example/mcp';
  const connect = [...w.document.querySelectorAll('.activepieces-hub button')]
    .find(button => button.textContent.includes('ربط'));
  assert(connect);
  connect.click(); await tick(); await tick();
  const request = calls.find(c => c.path === '/api/connectors/connect' && c.body?.provider === 'activepieces');
  assert(request);
  assert.equal(request.body.url, 'https://automation.example/mcp');
  assert.equal(w.document.body.textContent.includes('access_token'), false);
});

test('browser router sends live task requirements and renders the selected backend', async t => {
  const {w,calls} = await setup(t);
  const panel = w.document.querySelector('.browser-router');
  assert(panel);
  const inputs = panel.querySelectorAll('input');
  const url = [...inputs].find(x => x.type === 'url');
  const service = [...inputs].find(x => x.type === 'text');
  const checks = [...inputs].filter(x => x.type === 'checkbox');
  url.value = 'https://example.com';
  service.value = 'shop';
  checks[0].checked = true;
  checks[1].checked = true;
  const button = [...panel.querySelectorAll('button')].find(x => x.textContent.includes('اختيار'));
  button.click(); await tick(); await tick();
  const request = calls.find(c => String(c.path).startsWith('/api/browser-router?'));
  assert(request);
  assert.match(request.path, /session=project/);
  assert.match(request.path, /needs_session=1/);
  assert.match(request.path, /complex_ui=1/);
  assert.match(panel.querySelector('[role="status"]').textContent, /Playwright/);
});

test('search layer saves URLs and clears Crawl4AI secret without rendering it', async t => {
  const {w,calls} = await setup(t);
  const panel = w.document.querySelector('.search-layer');
  assert(panel);
  const searx = panel.querySelector('#searxng-url');
  const crawl = panel.querySelector('#crawl4ai-url');
  const token = panel.querySelector('#crawl4ai-token');
  assert.equal(searx.value, 'https://search.example');
  assert.equal(crawl.value, 'https://crawl.example');
  token.value = 'crawl4ai-private-token';
  const save = [...panel.querySelectorAll('button')].find(button => button.textContent.includes('حفظ'));
  save.click(); await tick(); await tick();
  const request = calls.find(c => c.path === '/api/search-layer/save');
  assert(request);
  assert.equal(request.body.searxng, 'https://search.example');
  assert.equal(request.body.crawl4ai, 'https://crawl.example');
  assert.equal(request.body.crawl4ai_token, 'crawl4ai-private-token');
  assert.equal(token.value, '');
  assert.equal(w.document.body.textContent.includes('crawl4ai-private-token'), false);
});

test('search layer tests SearXNG and Crawl4AI through real backend routes', async t => {
  const {w,calls} = await setup(t);
  const panel = w.document.querySelector('.search-layer');
  const buttons = [...panel.querySelectorAll('button')];
  buttons.find(button => button.textContent.includes('SearXNG')).click();
  await tick(); await tick();
  assert(calls.some(c => c.path === '/api/search-layer/test' && c.body?.provider === 'searxng'));
  buttons.find(button => button.textContent.includes('Crawl4AI') && button.textContent.includes('اختبار')).click();
  await tick(); await tick();
  assert(calls.some(c => c.path === '/api/search-layer/test' && c.body?.provider === 'crawl4ai'));
});

test('document engine sends OCR/layout requirements and renders verified Docling route', async t => {
  const {w,calls} = await setup(t);
  const panel = w.document.querySelector('.document-engine');
  assert(panel);
  panel.querySelector('input[type="text"]').value = 'scan.pdf';
  const checks = [...panel.querySelectorAll('input[type="checkbox"]')];
  checks[0].checked = true;
  checks[1].checked = true;
  const button = panel.querySelector('button');
  button.click(); await tick(); await tick();
  const request = calls.find(c => String(c.path).startsWith('/api/document-engine?'));
  assert(request);
  assert.match(request.path, /session=project/);
  assert.match(request.path, /path=scan.pdf/);
  assert.match(request.path, /needs_ocr=1/);
  assert.match(request.path, /preserve_layout=1/);
  assert.match(panel.querySelector('[role="status"]').textContent, /Docling/);
});

test('free provider key is cleared after save and never rendered back into the page', async t => {
  const {w,calls} = await setup(t);
  const row = [...w.document.querySelectorAll('.free-provider')].find(x => x.textContent.includes('Groq'));
  assert(row);
  const input = row.querySelector('input[type="password"]');
  input.value = 'groq-secret-example';
  const save = [...row.querySelectorAll('button')].find(b => b.textContent.includes('حفظ'));
  save.click(); await tick(); await tick();
  const request = calls.find(c => c.path === '/api/free-providers/save');
  assert.equal(request.body.provider, 'groq');
  assert.equal(request.body.key, 'groq-secret-example');
  assert.equal(input.value, '');
  assert.equal(w.document.body.textContent.includes('groq-secret-example'), false);
});

test('provider refresh preserves unsaved keys and displays the real cooldown', async t => {
  const {w} = await setup(t);
  const input = w.document.querySelector('input[data-provider="groq"]');
  input.value = 'draft-not-saved';
  const fetch = w.fetch;
  w.fetch = async (path, options = {}) => {
    if (path === '/api/free-providers' && options.method !== 'POST') return {ok:true,json:async()=>({providers:[
      {id:'groq',name:'Groq Free',configured:false},
      {id:'nvidia',name:'NVIDIA Free',configured:true,health:{state:'rate_limited',retry_after_seconds:77}}
    ]})};
    return fetch(path, options);
  };
  [...w.document.querySelectorAll('.free-providers button')].find(b=>b.textContent==='تحديث حالة المزودين').click();
  await tick(); await tick();
  assert.equal(w.document.querySelector('input[data-provider="groq"]').value, 'draft-not-saved');
  assert.match(w.document.querySelector('.free-providers').textContent, /77/);
  assert.match(w.document.querySelector('.free-providers').textContent, /تم بلوغ حد الاستخدام/);
});

test('provider argument failures are displayed as failures rather than untested', async t => {
  const {w} = await setup(t);
  const fetch = w.fetch;
  w.fetch = async (path, options = {}) => path === '/api/free-providers'
    ? {ok:true,json:async()=>({providers:[{id:'groq',name:'Groq Free',configured:true,
        health:{state:'invalid_tool_response',retry_after_seconds:180}}]})}
    : fetch(path, options);
  [...w.document.querySelectorAll('.free-providers button')].find(b=>b.textContent==='تحديث حالة المزودين').click();
  await tick(); await tick();
  assert.match(w.document.querySelector('.free-provider').textContent, /أرسل النموذج معطيات أدوات غير صالحة/);
  assert.doesNotMatch(w.document.querySelector('.free-provider').textContent, /لم يُختبر بعد/);
});
