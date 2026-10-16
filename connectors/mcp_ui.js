// Musab Hub MCP integrations: vetted local bundles + custom remote HTTP MCP.
(() => {
  'use strict';
  const tr = (en, ar) => document.documentElement.lang.startsWith('ar') ? ar : en;
  const node = (tag, text = '') => { const e = document.createElement(tag); e.textContent = text; return e; };
  const current = () => window.NewAlWorkspaceSession?.();

  async function api(path, body) {
    const r = await fetch(path, {
      method: body ? 'POST' : 'GET',
      credentials: 'same-origin',
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined
    });
    const data = await r.json();
    if (!r.ok || data.error || data.ok === false) throw new Error(data.error || 'MCP request failed');
    return data;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const panel = document.querySelector('.connector-panel');
    if (!panel) return;

    // ---------------------------------------------------------------- Free AI provider credentials
    const providersSection = node('section');
    providersSection.className = 'connector-card free-providers';
    providersSection.append(node('h3', tr('Free AI providers', 'مزودو الذكاء المجانيون')));
    providersSection.append(node('p', tr(
      'Keys are stored in Android Keystore. MusabAI only uses the free pool: Groq → Gemini → OpenRouter Free → NVIDIA.',
      'تُحفظ المفاتيح داخل Android Keystore. يستخدم MusabAI المسار المجاني فقط: Groq ← Gemini ← OpenRouter Free ← NVIDIA.'
    )));
    const providerMessage = node('p');
    providerMessage.setAttribute('role', 'status');
    const providerList = node('div');
    const providerRefresh = node('button', tr('Refresh provider status', 'تحديث حالة المزودين'));
    providerRefresh.type = 'button'; providerRefresh.className = 'btn';
    providersSection.append(providerRefresh, providerMessage, providerList);
    panel.insertBefore(providersSection, document.querySelector('#connector-list'));

    let providerBusy = false;
    async function withProviderBusy(fn) {
      if (providerBusy) return;
      providerBusy = true; providerRefresh.disabled = true;
      try { await fn(); }
      catch (error) { providerMessage.textContent = error.message; }
      finally { providerBusy = false; providerRefresh.disabled = false; }
    }

    async function loadProviders() {
      const data = await api('/api/free-providers');
      const drafts = new Map([...providerList.querySelectorAll('input[data-provider]')].map(input => [input.dataset.provider, input.value]));
      providerList.replaceChildren();
      for (const provider of data.providers || []) {
        const row = node('section'); row.className = 'connector-card free-provider';
        row.append(node('strong', provider.name));
        row.append(node('p', provider.configured
          ? tr('Key saved securely', 'المفتاح محفوظ بأمان')
          : tr('Key not configured', 'المفتاح غير مضاف')));
        const health = provider.health || {};
        const healthLabels = {
          available: tr('Endpoint verified', 'تم التحقق من الخدمة'),
          untested: tr('Not tested yet', 'لم يُختبر بعد'),
          rate_limited: tr('Usage limit reached', 'تم بلوغ حد الاستخدام'),
          auth_error: tr('Key or authorization rejected', 'رُفض المفتاح أو التفويض'),
          overloaded: tr('Provider busy', 'المزود مشغول'),
          timeout: tr('Provider timed out', 'انتهت مهلة المزود'),
          invalid_tool_response: tr('Model returned invalid tool arguments', 'أرسل النموذج معطيات أدوات غير صالحة'),
          retry_ready: tr('Ready to retry', 'يمكن إعادة المحاولة'),
          cooldown: tr('Waiting to retry', 'بانتظار إعادة المحاولة'),
          capability_mismatch: tr('Model unavailable for this request', 'النموذج غير متاح لهذا الطلب')
        };
        if (provider.configured) {
          let status = healthLabels[health.state] || healthLabels.untested;
          if (health.retry_after_seconds > 0) status += tr(' · retry in ', ' · إعادة المحاولة بعد ') + health.retry_after_seconds + tr(' seconds', ' ثانية');
          row.append(node('p', status));
        }
        const input = node('input');
        input.dataset.provider = provider.id;
        input.value = drafts.get(provider.id) || '';
        input.type = 'password'; input.autocomplete = 'off';
        input.placeholder = tr('API key', 'مفتاح API');
        input.setAttribute('aria-label', provider.name + ' API key');
        const save = node('button', tr('Save key', 'حفظ المفتاح'));
        save.type = 'button'; save.className = 'btn primary';
        save.onclick = () => withProviderBusy(async () => {
          const key = input.value.trim();
          if (!key) throw new Error(tr('Paste an API key first.', 'ألصق مفتاح API أولًا.'));
          await api('/api/free-providers/save', {provider: provider.id, key});
          input.value = '';
          providerMessage.textContent = tr('Saved in Android Keystore.', 'تم الحفظ داخل Android Keystore.');
          await loadProviders();
        });
        row.append(input, save);
        if (provider.configured) {
          const test = node('button', tr('Test', 'اختبار'));
          test.type = 'button'; test.className = 'btn';
          test.onclick = () => withProviderBusy(async () => {
            providerMessage.textContent = tr('Testing the real free endpoint…', 'جارٍ اختبار الخدمة المجانية فعليًا…');
            await api('/api/free-providers/test', {provider: provider.id});
            providerMessage.textContent = tr('Connected to the free endpoint.', 'تم الاتصال بالخدمة المجانية.');
            await loadProviders();
          });
          const remove = node('button', tr('Remove key', 'حذف المفتاح'));
          remove.type = 'button'; remove.className = 'btn';
          remove.onclick = () => withProviderBusy(async () => {
            await api('/api/free-providers/remove', {provider: provider.id});
            providerMessage.textContent = tr('Key removed.', 'تم حذف المفتاح.');
            await loadProviders();
          });
          row.append(test, remove);
        }
        providerList.append(row);
      }
    }
    providerRefresh.onclick = () => withProviderBusy(async () => { providerMessage.textContent = ''; await loadProviders(); });
    withProviderBusy(loadProviders);

    // ---------------------------------------------------------------- Activepieces Automation Hub
    const activepiecesSection = node('section');
    activepiecesSection.className = 'connector-card activepieces-hub';
    activepiecesSection.append(node('h3', tr('Activepieces Automation Hub', 'مركز أتمتة Activepieces')));
    activepiecesSection.append(node('p', tr(
      'Connect the MCP Server URL from Activepieces Settings. OAuth opens in your browser; tokens stay encrypted in Android Keystore and are never exposed to the WebView or Python.',
      'اربط رابط MCP Server من إعدادات Activepieces. يفتح OAuth في المتصفح، وتبقى الرموز مشفرة داخل Android Keystore ولا تظهر للـ WebView أو Python.'
    )));
    const activepiecesUrl = node('input');
    activepiecesUrl.type = 'url';
    activepiecesUrl.id = 'activepieces-mcp-url';
    activepiecesUrl.placeholder = 'https://your-instance.com/mcp';
    activepiecesUrl.autocomplete = 'off';
    activepiecesUrl.setAttribute('aria-label', tr('Activepieces MCP Server URL', 'رابط خادم Activepieces MCP'));
    const activepiecesMessage = node('p');
    activepiecesMessage.setAttribute('role', 'status');
    const activepiecesState = node('p');
    const activepiecesActions = node('div');
    activepiecesActions.className = 'connector-actions';
    const activepiecesConnect = node('button', tr('Connect Activepieces', 'ربط Activepieces'));
    activepiecesConnect.type = 'button'; activepiecesConnect.className = 'btn primary';
    const activepiecesTest = node('button', tr('Test', 'اختبار'));
    activepiecesTest.type = 'button'; activepiecesTest.className = 'btn';
    const activepiecesDisconnect = node('button', tr('Disconnect', 'فصل'));
    activepiecesDisconnect.type = 'button'; activepiecesDisconnect.className = 'btn';
    activepiecesActions.append(activepiecesConnect, activepiecesTest, activepiecesDisconnect);
    activepiecesSection.append(activepiecesUrl, activepiecesActions, activepiecesState, activepiecesMessage);
    panel.insertBefore(activepiecesSection, document.querySelector('#connector-list'));

    let activepiecesBusy = false, activepiecesPoll = 0;
    async function activepiecesAccount() {
      const data = await api('/api/connectors');
      return (data.connectors || []).find(item => item.id === 'activepieces') || null;
    }
    function renderActivepieces(item) {
      if (!item) {
        activepiecesState.textContent = tr('Available on Android builds.', 'متاح في نسخة Android.');
        activepiecesTest.disabled = activepiecesDisconnect.disabled = true;
        return;
      }
      if (item.server_url && !activepiecesUrl.value) activepiecesUrl.value = item.server_url;
      const labels = {
        connected: tr('Connected', 'متصل'),
        authorizing: tr('Waiting for browser authorization', 'بانتظار التفويض في المتصفح'),
        exchanging: tr('Finishing OAuth', 'جارٍ إكمال OAuth'),
        testing: tr('Testing MCP tools', 'جارٍ اختبار أدوات MCP'),
        reauthorize: tr('Reconnect required', 'يلزم إعادة الربط'),
        error: tr('Connection failed', 'فشل الاتصال'),
        disconnected: tr('Not connected', 'غير متصل')
      };
      activepiecesState.textContent = (labels[item.status] || item.status || tr('Not connected', 'غير متصل'))
        + (item.tool_count ? tr(' · Tools: ', ' · الأدوات: ') + item.tool_count : '')
        + (item.account ? ' · ' + item.account : '');
      if (item.error) activepiecesMessage.textContent = item.error;
      const connected = item.status === 'connected';
      activepiecesTest.disabled = !item.has_credentials;
      activepiecesDisconnect.disabled = !item.has_credentials && !['authorizing','exchanging','testing','error','reauthorize'].includes(item.status);
      activepiecesConnect.textContent = connected || item.status === 'reauthorize' || item.status === 'error'
        ? tr('Reconnect', 'إعادة الربط') : tr('Connect Activepieces', 'ربط Activepieces');
    }
    async function loadActivepieces() {
      try { renderActivepieces(await activepiecesAccount()); }
      catch (error) {
        activepiecesState.textContent = tr('Android connector host unavailable.', 'مضيف اتصالات Android غير متاح.');
        activepiecesTest.disabled = activepiecesDisconnect.disabled = true;
      }
    }
    async function activepiecesRun(fn) {
      if (activepiecesBusy) return;
      activepiecesBusy = true;
      activepiecesConnect.disabled = activepiecesTest.disabled = activepiecesDisconnect.disabled = true;
      try { await fn(); }
      catch (error) { activepiecesMessage.textContent = error.message; }
      finally {
        activepiecesBusy = false;
        activepiecesConnect.disabled = false;
        await loadActivepieces();
      }
    }
    async function pollActivepieces(token) {
      for (let attempt = 0; attempt < 300 && token === activepiecesPoll; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        if (token !== activepiecesPoll) return;
        let item;
        try { item = await activepiecesAccount(); }
        catch (_) { continue; }
        renderActivepieces(item);
        if (!item || !['authorizing','exchanging','testing'].includes(item.status)) {
          if (item?.status === 'connected') activepiecesMessage.textContent = tr(
            'Activepieces connected and MCP tools verified.',
            'تم ربط Activepieces والتحقق من أدوات MCP.'
          );
          return;
        }
      }
    }
    activepiecesConnect.onclick = () => activepiecesRun(async () => {
      const url = activepiecesUrl.value.trim();
      if (!url) throw new Error(tr('Paste the MCP Server URL first.', 'ألصق رابط MCP Server أولاً.'));
      activepiecesMessage.textContent = tr(
        'Discovering OAuth and opening your browser…',
        'جارٍ اكتشاف OAuth وفتح المتصفح…'
      );
      const result = await api('/api/connectors/connect', {provider:'activepieces', url});
      if (result.status === 'authorizing') {
        const token = ++activepiecesPoll;
        activepiecesMessage.textContent = tr(
          'Approve access in the browser. MusabAI will verify the tools automatically.',
          'وافق على الوصول في المتصفح. سيتحقق MusabAI من الأدوات تلقائياً.'
        );
        pollActivepieces(token);
      }
    });
    activepiecesTest.onclick = () => activepiecesRun(async () => {
      activepiecesMessage.textContent = tr('Testing the live MCP server…', 'جارٍ اختبار خادم MCP الفعلي…');
      const result = await api('/api/connectors/test', {provider:'activepieces'});
      activepiecesMessage.textContent = tr('Connection verified. Tools: ', 'تم التحقق من الاتصال. الأدوات: ') + result.tool_count;
    });
    activepiecesDisconnect.onclick = () => activepiecesRun(async () => {
      ++activepiecesPoll;
      await api('/api/connectors/disconnect', {provider:'activepieces'});
      activepiecesMessage.textContent = tr('Disconnected on this device.', 'تم الفصل على هذا الجهاز.');
    });
    loadActivepieces();

    // ---------------------------------------------------------------- Browser Tool Selector
    const browserSection = node('section');
    browserSection.className = 'connector-card browser-router';
    browserSection.append(node('h3', tr('Browser routing', 'توجيه المتصفح')));
    browserSection.append(node('p', tr(
      'MusabAI chooses a live route instead of guessing: service API → matching MCP → Playwright → Browser Use → open-browser-use. Existing logged-in sessions can prefer open-browser-use.',
      'يختار MusabAI مساراً متاحاً فعلياً بدل التخمين: API للخدمة ← MCP مطابق ← Playwright ← Browser Use ← open-browser-use. ويمكن تفضيل open-browser-use عند الحاجة إلى جلسة متصفح مسجلة الدخول.'
    )));
    const browserUrl = node('input');
    browserUrl.type = 'url'; browserUrl.placeholder = 'https://example.com';
    browserUrl.setAttribute('aria-label', tr('Target URL', 'الرابط المستهدف'));
    const browserService = node('input');
    browserService.type = 'text'; browserService.placeholder = tr('Service/MCP hint (optional)', 'اسم الخدمة/MCP (اختياري)');
    browserService.setAttribute('aria-label', tr('Service or MCP hint', 'اسم الخدمة أو MCP'));
    const sessionLabel = node('label');
    const browserSession = node('input'); browserSession.type = 'checkbox';
    sessionLabel.append(browserSession, document.createTextNode(tr(' Needs existing logged-in session', ' يحتاج جلسة مسجلة الدخول')));
    const complexLabel = node('label');
    const browserComplex = node('input'); browserComplex.type = 'checkbox';
    complexLabel.append(browserComplex, document.createTextNode(tr(' Complex/visual UI', ' واجهة معقدة/بصرية')));
    const browserCheck = node('button', tr('Choose browser route', 'اختيار مسار المتصفح'));
    browserCheck.type = 'button'; browserCheck.className = 'btn primary';
    const browserMessage = node('p'); browserMessage.setAttribute('role', 'status');
    browserSection.append(browserUrl, browserService, sessionLabel, complexLabel, browserCheck, browserMessage);
    panel.insertBefore(browserSection, document.querySelector('#connector-list'));

    async function checkBrowserRoute() {
      const sid = current();
      if (!sid) throw new Error(tr('Open a project conversation first.', 'افتح محادثة أو مشروعاً أولاً.'));
      const params = new URLSearchParams({session:sid});
      if (browserUrl.value.trim()) params.set('url', browserUrl.value.trim());
      if (browserService.value.trim()) params.set('service', browserService.value.trim());
      if (browserSession.checked) params.set('needs_session', '1');
      if (browserComplex.checked) params.set('complex_ui', '1');
      const result = await api('/api/browser-router?' + params.toString());
      const availability = result.availability || {};
      const ready = Object.entries(availability).filter(([, value]) => value).map(([key]) => key.replaceAll('_', ' '));
      browserMessage.textContent = tr('Selected: ', 'المسار المختار: ') + result.label
        + ' — ' + result.reason
        + (ready.length ? tr(' · Available: ', ' · المتاح: ') + ready.join(', ') : '');
    }
    browserCheck.onclick = async () => {
      browserCheck.disabled = true;
      browserMessage.textContent = tr('Checking live tools…', 'جارٍ فحص الأدوات المتاحة…');
      try { await checkBrowserRoute(); }
      catch (error) { browserMessage.textContent = error.message; }
      finally { browserCheck.disabled = false; }
    };

    // ---------------------------------------------------------------- Search & Crawl
    const searchSection = node('section');
    searchSection.className = 'connector-card search-layer';
    searchSection.append(node('h3', tr('Search & Crawl', 'البحث والزحف')));
    searchSection.append(node('p', tr(
      'SearXNG discovers sources; Crawl4AI deep-reads selected pages. Provider URLs stay private to MusabAI. An optional Crawl4AI token is stored in Android Keystore and is never read back into the page.',
      'يستخدم SearXNG لاكتشاف المصادر وCrawl4AI للقراءة العميقة للصفحات المختارة. تبقى روابط المزودات خاصة بـ MusabAI، ويُحفظ رمز Crawl4AI الاختياري داخل Android Keystore ولا يُعاد عرضه في الصفحة.'
    )));
    const searxUrl = node('input');
    searxUrl.type = 'url'; searxUrl.id = 'searxng-url'; searxUrl.autocomplete = 'off';
    searxUrl.placeholder = 'https://search.example.com';
    searxUrl.setAttribute('aria-label', 'SearXNG URL');
    const crawlUrl = node('input');
    crawlUrl.type = 'url'; crawlUrl.id = 'crawl4ai-url'; crawlUrl.autocomplete = 'off';
    crawlUrl.placeholder = 'https://crawl.example.com';
    crawlUrl.setAttribute('aria-label', 'Crawl4AI URL');
    const crawlToken = node('input');
    crawlToken.type = 'password'; crawlToken.id = 'crawl4ai-token'; crawlToken.autocomplete = 'off';
    crawlToken.placeholder = tr('Crawl4AI token (optional)', 'رمز Crawl4AI (اختياري)');
    crawlToken.setAttribute('aria-label', tr('Crawl4AI token', 'رمز Crawl4AI'));
    const searchActions = node('div'); searchActions.className = 'connector-actions';
    const searchSave = node('button', tr('Save search layer', 'حفظ طبقة البحث'));
    searchSave.type = 'button'; searchSave.className = 'btn primary';
    const searxTest = node('button', tr('Test SearXNG', 'اختبار SearXNG'));
    searxTest.type = 'button'; searxTest.className = 'btn';
    const crawlTest = node('button', tr('Test Crawl4AI', 'اختبار Crawl4AI'));
    crawlTest.type = 'button'; crawlTest.className = 'btn';
    const crawlRemove = node('button', tr('Remove Crawl4AI token', 'حذف رمز Crawl4AI'));
    crawlRemove.type = 'button'; crawlRemove.className = 'btn';
    searchActions.append(searchSave, searxTest, crawlTest, crawlRemove);
    const searchState = node('p');
    const searchMessage = node('p'); searchMessage.setAttribute('role', 'status');
    searchSection.append(searxUrl, crawlUrl, crawlToken, searchActions, searchState, searchMessage);
    panel.insertBefore(searchSection, document.querySelector('#connector-list'));

    let searchBusy = false;
    async function loadSearchLayer() {
      const data = await api('/api/search-layer');
      searxUrl.value = data.searxng || '';
      crawlUrl.value = data.crawl4ai || '';
      searchState.textContent =
        tr('SearXNG: ', 'SearXNG: ') + (data.searxng ? tr('configured', 'مهيأ') : tr('not configured', 'غير مهيأ')) +
        tr(' · Crawl4AI: ', ' · Crawl4AI: ') + (data.crawl4ai ? tr('configured', 'مهيأ') : tr('not configured', 'غير مهيأ')) +
        tr(' · token: ', ' · الرمز: ') + (data.crawl4ai_token ? tr('saved securely', 'محفوظ بأمان') : tr('not saved', 'غير محفوظ'));
      crawlRemove.disabled = !data.crawl4ai_token;
      return data;
    }
    async function searchRun(fn) {
      if (searchBusy) return;
      searchBusy = true;
      [searchSave, searxTest, crawlTest, crawlRemove].forEach(button => button.disabled = true);
      try { await fn(); }
      catch (error) { searchMessage.textContent = error.message; }
      finally {
        searchBusy = false;
        await loadSearchLayer().catch(error => { searchMessage.textContent = error.message; });
        searchSave.disabled = searxTest.disabled = crawlTest.disabled = false;
      }
    }
    searchSave.onclick = () => searchRun(async () => {
      const token = crawlToken.value.trim();
      const body = {searxng: searxUrl.value.trim(), crawl4ai: crawlUrl.value.trim()};
      if (token) body.crawl4ai_token = token;
      await api('/api/search-layer/save', body);
      crawlToken.value = '';
      searchMessage.textContent = tr(
        'Search layer saved. Test each live provider before relying on it.',
        'تم حفظ طبقة البحث. اختبر كل مزود فعلياً قبل الاعتماد عليه.'
      );
    });
    searxTest.onclick = () => searchRun(async () => {
      searchMessage.textContent = tr('Testing SearXNG JSON search…', 'جارٍ اختبار بحث SearXNG بصيغة JSON…');
      const result = await api('/api/search-layer/test', {provider:'searxng'});
      searchMessage.textContent = tr('SearXNG verified. Results: ', 'تم التحقق من SearXNG. النتائج: ') + result.results;
    });
    crawlTest.onclick = () => searchRun(async () => {
      searchMessage.textContent = tr('Testing Crawl4AI health endpoint…', 'جارٍ اختبار حالة Crawl4AI…');
      const result = await api('/api/search-layer/test', {provider:'crawl4ai'});
      searchMessage.textContent = tr('Crawl4AI verified', 'تم التحقق من Crawl4AI') + (result.version ? ' · ' + result.version : '');
    });
    crawlRemove.onclick = () => searchRun(async () => {
      await api('/api/search-layer/remove-token', {});
      crawlToken.value = '';
      searchMessage.textContent = tr('Crawl4AI token removed.', 'تم حذف رمز Crawl4AI.');
    });
    loadSearchLayer().catch(error => { searchMessage.textContent = error.message; });

    // ---------------------------------------------------------------- Document Engine
    const documentSection = node('section');
    documentSection.className = 'connector-card document-engine';
    documentSection.append(node('h3', tr('Document Engine', 'محرك المستندات')));
    documentSection.append(node('p', tr(
      'MusabAI keeps simple files on the lightweight native tools and routes OCR, layout, Office and structured conversion to verified Docling MCP when it is connected.',
      'يبقي MusabAI الملفات البسيطة على الأدوات المحلية الخفيفة، ويوجه OCR والتخطيط وملفات Office والتحويل المنظم إلى Docling MCP بعد التحقق من اتصاله.'
    )));
    const documentPath = node('input');
    documentPath.type = 'text'; documentPath.placeholder = tr('Document path or URL', 'مسار المستند أو الرابط');
    documentPath.setAttribute('aria-label', tr('Document path or URL', 'مسار المستند أو الرابط'));
    const documentTask = node('select');
    for (const value of ['read','convert','extract','create','edit','archive']) {
      const option = node('option', value); option.value = value; documentTask.append(option);
    }
    const ocrLabel = node('label'), documentOcr = node('input'); documentOcr.type = 'checkbox';
    ocrLabel.append(documentOcr, document.createTextNode(tr(' Needs OCR', ' يحتاج OCR')));
    const layoutLabel = node('label'), documentLayout = node('input'); documentLayout.type = 'checkbox';
    layoutLabel.append(documentLayout, document.createTextNode(tr(' Preserve layout/tables', ' الحفاظ على التخطيط/الجداول')));
    const structuredLabel = node('label'), documentStructured = node('input'); documentStructured.type = 'checkbox';
    structuredLabel.append(documentStructured, document.createTextNode(tr(' Structured conversion', ' تحويل منظم')));
    const documentCheck = node('button', tr('Choose document engine', 'اختيار محرك المستند'));
    documentCheck.type = 'button'; documentCheck.className = 'btn primary';
    const documentMessage = node('p'); documentMessage.setAttribute('role', 'status');
    documentSection.append(documentPath, documentTask, ocrLabel, layoutLabel, structuredLabel, documentCheck, documentMessage);
    panel.insertBefore(documentSection, document.querySelector('#connector-list'));

    documentCheck.onclick = async () => {
      const sid = current();
      if (!sid) {
        documentMessage.textContent = tr('Open a project conversation first.', 'افتح محادثة أو مشروعاً أولاً.');
        return;
      }
      const params = new URLSearchParams({session:sid, task:documentTask.value});
      if (documentPath.value.trim()) params.set('path', documentPath.value.trim());
      if (documentOcr.checked) params.set('needs_ocr', '1');
      if (documentLayout.checked) params.set('preserve_layout', '1');
      if (documentStructured.checked) params.set('structured', '1');
      documentCheck.disabled = true;
      documentMessage.textContent = tr('Checking live document tools…', 'جارٍ فحص أدوات المستندات المتاحة…');
      try {
        const result = await api('/api/document-engine?' + params.toString());
        documentMessage.textContent = tr('Selected: ', 'المحرك المختار: ') + result.label + ' — ' + result.reason;
      } catch (error) {
        documentMessage.textContent = error.message;
      } finally {
        documentCheck.disabled = false;
      }
    };

    // ---------------------------------------------------------------- Official MCP Registry
    const registrySection = node('section');
    registrySection.className = 'connector-card mcp-registry';
    registrySection.append(node('h3', tr('Official MCP Registry', 'سجل MCP الرسمي')));
    registrySection.append(node('p', tr(
      'Search the official registry. MusabAI inspects server.json and auto-installs only literal HTTPS Streamable HTTP endpoints after a real MCP handshake. Package commands are never executed automatically.',
      'ابحث في سجل MCP الرسمي. يفحص MusabAI ملف server.json ولا يثبت تلقائياً إلا نقاط Streamable HTTP حرفية عبر HTTPS بعد اختبار MCP حقيقي. أوامر الحزم لا تُشغّل تلقائياً أبداً.'
    )));
    const registryForm = node('div');
    const registryQuery = node('input');
    registryQuery.type = 'search';
    registryQuery.placeholder = tr('Search MCP servers', 'ابحث عن خوادم MCP');
    registryQuery.maxLength = 120;
    registryQuery.setAttribute('aria-label', tr('Registry search', 'بحث السجل'));
    const registrySearch = node('button', tr('Search', 'بحث'));
    registrySearch.type = 'button'; registrySearch.className = 'btn primary';
    registryForm.append(registryQuery, registrySearch);
    const registryMessage = node('p');
    registryMessage.setAttribute('role', 'status');
    const registryList = node('div');
    registrySection.append(registryForm, registryMessage, registryList);
    panel.insertBefore(registrySection, document.querySelector('#connector-list'));

    let registryBusy = false;
    async function registryRun(fn) {
      if (registryBusy) return;
      registryBusy = true; registrySearch.disabled = true;
      try { await fn(); }
      catch (error) { registryMessage.textContent = error.message; }
      finally { registryBusy = false; registrySearch.disabled = false; }
    }

    async function inspectRegistry(item, row) {
      const data = await api('/api/mcp-registry/inspect?name=' + encodeURIComponent(item.name));
      const details = node('div');
      if (data.remote) details.append(node('p', tr('Remote: ', 'الخادم البعيد: ') + data.remote));
      if (data.packages?.length) {
        const names = data.packages.map(p => [p.registryType, p.identifier, p.version].filter(Boolean).join(': '));
        details.append(node('p', tr('Packages (review only): ', 'الحزم (للمراجعة فقط): ') + names.join(', ')));
      }
      for (const reason of data.reasons || []) details.append(node('p', reason));
      if (data.installable) {
        const access = node('input');
        access.type = 'password'; access.autocomplete = 'off';
        access.placeholder = tr('Service access token (if required)', 'رمز وصول الخدمة إن كان مطلوبًا');
        access.setAttribute('aria-label', access.placeholder);
        details.append(access);
        const install = node('button', tr('Test and install', 'اختبار وتثبيت'));
        install.type = 'button'; install.className = 'btn primary';
        install.onclick = () => registryRun(async () => {
          const sid = current();
          if (!sid) throw new Error(tr('Open a project conversation first.', 'افتح محادثة أو مشروعاً أولاً.'));
          registryMessage.textContent = tr('Running a real MCP handshake before saving…', 'جارٍ تنفيذ اختبار MCP حقيقي قبل الحفظ…');
          const result = await api('/api/mcp-registry/install', {session: sid, name: item.name, ...(access.value.trim() ? {token: access.value.trim()} : {})});
          access.value = '';
          registryMessage.textContent = tr('Installed safely. Tools: ', 'تم التثبيت بأمان. الأدوات: ') + result.tools;
        });
        details.append(install);
      } else {
        details.append(node('p', tr(
          'Manual configuration required; MusabAI will not execute registry package commands.',
          'يلزم إعداد يدوي؛ لن يشغّل MusabAI أوامر الحزم القادمة من السجل.'
        )));
      }
      row.append(details);
    }

    async function loadRegistry(query) {
      registryMessage.textContent = tr('Searching official MCP Registry…', 'جارٍ البحث في سجل MCP الرسمي…');
      const data = await api('/api/mcp-registry?q=' + encodeURIComponent(query || '') + '&limit=20');
      registryList.replaceChildren();
      if (!(data.servers || []).length) registryList.append(node('p', tr('No matching servers.', 'لا توجد خوادم مطابقة.')));
      for (const item of data.servers || []) {
        const row = node('section'); row.className = 'connector-card mcp-registry-result';
        row.append(node('strong', item.title || item.name));
        row.append(node('p', item.name + (item.version ? ' · ' + item.version : '')));
        if (item.description) row.append(node('p', item.description));
        row.append(node('p', tr('Remote endpoints: ', 'نقاط الاتصال البعيدة: ') + item.remote_count +
          tr(' · Packages: ', ' · الحزم: ') + item.package_count));
        const inspect = node('button', tr('Inspect server.json', 'فحص server.json'));
        inspect.type = 'button'; inspect.className = 'btn';
        inspect.onclick = () => registryRun(async () => {
          inspect.disabled = true;
          try { await inspectRegistry(item, row); }
          finally { inspect.disabled = false; }
        });
        row.append(inspect);
        registryList.append(row);
      }
      registryMessage.textContent = tr('Results: ', 'النتائج: ') + (data.count || 0);
    }

    registrySearch.onclick = () => registryRun(() => loadRegistry(registryQuery.value.trim()));
    registryQuery.onkeydown = event => {
      if (event.key === 'Enter') { event.preventDefault(); registrySearch.click(); }
    };

    // ---------------------------------------------------------------- Custom remote HTTP MCP
    const section = node('section'); section.className = 'connector-card custom-mcp';
    const heading = node('h3', tr('Add MCP server', 'إضافة خادم MCP'));
    const intro = node('p', tr(
      'Remote Streamable HTTP. Tools become available in this project after a successful test. Browser OAuth services use the connection cards above.',
      'خادم بعيد ببروتوكول Streamable HTTP. تصبح أدواته متاحة في هذا المشروع بعد نجاح الاختبار. الخدمات التي تحتاج OAuth بالمتصفح تُربط من بطاقاتها بالأعلى.'
    ));
    const form = node('form'), message = node('p'), list = node('div');
    message.setAttribute('role', 'status');

    function field(label, type, id, placeholder) {
      const box = node('label', label), input = node('input');
      input.type = type; input.id = id; input.placeholder = placeholder; input.autocomplete = 'off';
      box.append(input); form.append(box); return input;
    }

    const name = field(tr('Name', 'الاسم'), 'text', 'mcp-name', 'my-server');
    name.required = true; name.maxLength = 24;
    const url = field(tr('MCP URL', 'رابط MCP'), 'url', 'mcp-url', 'https://example.com/mcp');
    url.required = true;
    const token = field(tr('Access token (optional)', 'رمز الوصول (اختياري)'), 'password', 'mcp-token', 'Bearer token');
    const submit = node('button', tr('Test and add', 'اختبار وإضافة'));
    submit.className = 'btn primary'; submit.type = 'submit'; form.append(submit);
    const refresh = node('button', tr('Show saved servers', 'عرض الخوادم المحفوظة'));
    refresh.type = 'button'; refresh.className = 'btn';
    section.append(heading, intro, form, message, refresh, list);
    panel.insertBefore(section, document.querySelector('#connector-list'));

    let busy = false;
    function requireSession(sid) {
      if (!sid || current() !== sid) throw new Error(tr('Open a project conversation first.', 'افتح محادثة أو مشروعًا أولًا.'));
    }

    async function load() {
      const sid = current(); requireSession(sid);
      const data = await api('/api/mcp-servers?session=' + encodeURIComponent(sid)); requireSession(sid);
      list.replaceChildren();
      const custom = (data.servers || []).filter(server => !server.bundle);
      if (!custom.length) list.append(node('p', tr('No custom MCP servers yet.', 'لا توجد خوادم MCP مضافة بعد.')));
      for (const server of custom) {
        const row = node('section'); row.className = 'connector-card';
        row.append(node('strong', server.name), node('p', server.url));
        if (server.registry) row.append(node('p', tr('Official Registry: ', 'السجل الرسمي: ') + server.registry + (server.registry_version ? ' · ' + server.registry_version : '')));
        row.append(node('p', tr('Last successful test: ', 'آخر اختبار ناجح: ') + server.tools + tr(' tools', ' أداة')));
        for (const op of ['test', 'remove']) {
          const button = node('button', op === 'test' ? tr('Test', 'اختبار') : tr('Remove', 'حذف'));
          button.className = 'btn';
          button.onclick = () => run(async () => {
            requireSession(sid);
            if (op === 'remove' && !window.confirm(tr('Remove this MCP server from the project?', 'حذف خادم MCP من هذا المشروع؟'))) return;
            const result = await api('/api/mcp-servers/' + op, {session: sid, name: server.name}); requireSession(sid);
            message.textContent = op === 'test'
              ? tr('Test passed. Tools: ', 'نجح الاختبار. الأدوات: ') + result.tools
              : tr('Removed.', 'تم الحذف.');
            await load();
          });
          row.append(button);
        }
        list.append(row);
      }
    }

    async function run(fn) {
      if (busy) return; busy = true; submit.disabled = refresh.disabled = true;
      message.textContent = tr('Testing connection…', 'جارٍ اختبار الاتصال…');
      try { await fn(); }
      catch (error) { message.textContent = error.message; }
      finally { busy = false; submit.disabled = refresh.disabled = false; }
    }

    form.onsubmit = event => {
      event.preventDefault();
      run(async () => {
        const sid = current(); requireSession(sid);
        const result = await api('/api/mcp-servers/save', {
          session: sid, name: name.value.trim(), url: url.value.trim(), token: token.value.trim()
        });
        token.value = ''; requireSession(sid);
        message.textContent = tr('Added and tested. Tools: ', 'تمت الإضافة والاختبار. الأدوات: ') + result.tools;
        await load();
      });
    };
    refresh.onclick = () => run(async () => { await load(); message.textContent = ''; });
  });
})();
