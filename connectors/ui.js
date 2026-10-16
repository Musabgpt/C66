// Additive connector panel. Original app.js, plugins, skills, terminal and review remain in place.
(() => {
  "use strict";
  const ar = () => document.documentElement.lang.startsWith("ar");
  const tr = (en, arabic) => ar() ? arabic : en;
  const current = () => window.NewAlWorkspaceSession?.();
  const labels = {
    connected: ["Connected", "متصل"], authorizing: ["Waiting for authorization", "بانتظار التفويض"],
    testing: ["Testing connection", "جارٍ اختبار الاتصال"], disconnected: ["Disconnected", "غير متصل"],
    not_configured: ["Deployment required", "يحتاج تفعيل الخدمة"], error: ["Connection failed", "فشل الاتصال"],
    reauthorize: ["Reconnect required", "يحتاج إعادة تفويض"], permission_required: ["Permission required", "يحتاج صلاحية"],
    unavailable: ["Unavailable on this host", "غير متاح على هذا الجهاز"],
    ready: ["Ready", "جاهز"], termux_disconnected: ["Termux not connected", "Termux غير متصل"],
    tool_missing: ["Runtime tool not installed", "الأداة غير مثبتة"], needs_setup: ["Setup required", "يحتاج إعداد"],
    server_stopped: ["Server stopped", "الخادم متوقف"], server_running: ["Server running", "الخادم يعمل"],
    health_failed: ["Health check failed", "فشل health check"], runtime_unavailable: ["Runtime unavailable", "بيئة التشغيل غير متاحة"]
  };
  let timer, active = false, pending = false, requesting = false, lastView = "";
  const feedback = new Map();
  const errors = {
    "OAuth application deployment required": ["The app owner must activate this service before accounts can connect.", "هذه الخدمة تحتاج تفعيلًا من صاحب التطبيق قبل ربط الحسابات."],
    "Connector server is unavailable; retry when online": ["Connection server unavailable. Check your network and retry.", "خادم الاتصال غير متاح. تحقّق من الإنترنت وأعد المحاولة."],
    "Waiting for network; authorization will resume automatically": ["Waiting for network; authorization will resume automatically", "بانتظار الإنترنت؛ سيُستأنف التفويض تلقائيًا."],
    "Authorization session is no longer valid; connect again": ["Authorization session expired or was lost. Connect again.", "انتهت جلسة التفويض أو فُقدت. اضغط اتصال مجددًا."],
    "Authorization expired; connect again": ["Authorization expired; connect again", "انتهت مهلة التفويض. اضغط اتصال مجددًا."],
    "Authorization was declined or failed; connect again": ["Authorization was declined or failed; connect again", "رُفض التفويض أو فشل. اضغط اتصال مجددًا."],
    "Authorization did not complete; connect again": ["Authorization did not complete; connect again", "لم يكتمل التفويض. اضغط اتصال مجددًا."],
    "Account verification failed; test again or reconnect": ["Account verification failed; test again or reconnect", "تعذّر التحقّق من الحساب. أعد الاختبار أو الاتصال."],
  };
  const errorText = text => errors[text] ? tr(...errors[text]) : text;
  async function api(path, body) {
    const response = await fetch(path, { method: body ? "POST" : "GET", credentials: "same-origin",
      headers: body ? {"Content-Type": "application/json"} : {}, body: body ? JSON.stringify(body) : undefined });
    const result = await response.json();
    if (!response.ok || result.error || result.ok === false) throw new Error(result.error || "Connection failed");
    return result;
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function feedbackNode(key) {
    const status = element("p", feedback.get(key) || "", "connector-feedback");
    status.dataset.feedback = key; status.setAttribute("role", "status");
    return status;
  }
  function showFeedback(key, text) {
    feedback.set(key, text);
    const message = document.querySelector("#connector-message");
    if (message) message.textContent = text;
    const local = [...document.querySelectorAll("[data-feedback]")].find(node => node.dataset.feedback === key);
    if (local) local.textContent = text;
  }
  async function refresh() {
    if (!active || document.hidden || requesting || pending) return;
    requesting = true;
    const list = document.querySelector("#connector-list"), message = document.querySelector("#connector-message");
    try {
      const sid = current();
      const bundlePath = "/api/mcp-bundles" + (sid ? "?session=" + encodeURIComponent(sid) : "");
      const responses = await Promise.allSettled([
        api("/api/connectors"), api("/api/extensions?directory=1"), api(bundlePath)
      ]);
      if (!active || current() !== sid) return;
      const [result, extensionResult, bundleResult] = responses.map(row => row.status === "fulfilled" ? row.value : {});
      result.connectors ||= []; extensionResult.extensions ||= []; bundleResult.bundles ||= [];
      const failures = responses.map(row => row.status === "rejected" ? errorText(row.reason?.message || String(row.reason)) : "");
      document.querySelector("#connector-heading").textContent = tr("Musab Hub — live integrations", "Musab Hub — الاتصالات الحية");
      document.querySelector("#connector-intro").textContent = tr(
        "One status manager now shows account connections and MCP runtime state separately: installed, running, stopped, permission/setup, and health.",
        "مدير حالة واحد يعرض الآن اتصالات الحسابات وحالة MCP الفعلية بشكل منفصل: مثبّت، يعمل، متوقف، يحتاج صلاحية/إعداد، وحالة الفحص."
      );
      document.querySelector("#open-connectors").textContent = tr("Musab Hub", "Musab Hub");
      const view = JSON.stringify([ar(), sid, result.connectors, bundleResult.bundles, extensionResult.extensions, failures, [...feedback]]);
      if (lastView === view && !pending) {
        list.querySelectorAll("button").forEach(button => { button.disabled = button.dataset.disabled === "true"; });
        return;
      }
      lastView = view;
      const panel = document.querySelector(".connector-panel"), scroll = panel.scrollTop;
      const focused = document.activeElement?.dataset.action;
      list.replaceChildren();
      if (failures[0]) list.append(element("p", failures[0], "connector-feedback"));
      for (const item of result.connectors) {
        if (item.id === "activepieces") continue; // configured in the dedicated Automation Hub card
        const row = element("section", "", "connector-card"), detail = element("div");
        detail.append(element("strong", item.name));
        const label = labels[item.status] || [item.status, item.status];
        detail.append(element("div", tr(...label), "connector-state " + (item.status === "connected" ? "connected" : "")));
        if (item.id === "termux" && item.dependencies) {
          const dep = item.dependencies;
          const states = {
            waiting_termux: ["uv/uvx: waiting for preparation", "uv/uvx: بانتظار التجهيز"],
            installing: ["uv/uvx: installing and verifying automatically…", "uv/uvx: جارٍ التثبيت والتحقق تلقائيًا…"],
            ready: ["uv/uvx: verified in Termux", "uv/uvx: جاهزان وتم التحقق داخل Termux"],
            failed: ["uv/uvx: preparation failed", "uv/uvx: تعذّر التجهيز"]
          };
          detail.append(element("div", tr(...(states[dep.state] || states.waiting_termux)), "muted"));
          if (dep.error) detail.append(element("div", dep.error, "muted"));
          if (dep.state === "ready" && dep.output) detail.append(element("small", dep.output, "muted"));
          if (dep.state === "failed") detail.append(element("small", tr("Use Test to retry preparation.", "اضغط اختبار لإعادة محاولة التجهيز."), "muted"));
        }
        if (item.account) detail.append(element("div", item.account, "muted"));
        if (item.transport === "mcp" && item.status === "connected")
          detail.append(element("div", tr("Available tools: ", "الأدوات المتاحة: ") + item.tool_count, "muted"));
        if (item.scopes) { const scopes = element("details"); scopes.append(element("summary", tr("Granted scopes", "الصلاحيات الممنوحة")), element("small", item.scopes)); detail.append(scopes); }
        if (item.error) detail.append(element("div", errorText(item.error), "muted"));
        if (['drive','gmail','calendar','docs','sheets'].includes(item.id) && !item.configured) {
          const setup = element("details"), title = element("summary", tr("Google service setup", "إعداد خدمات Google"));
          setup.append(title, element("p", tr(
            "Enable the Google APIs, register a Web OAuth client and configure the app's connection server. Then connect here to approve account access. An API key alone does not grant mailbox or Drive access.",
            "فعّل واجهات Google وسجّل عميل OAuth من نوع Web واضبطه في خادم اتصال التطبيق. بعدها اضغط اتصال لتفويض حسابك. مفتاح API وحده لا يمنح الوصول إلى Gmail أو Drive."
          )));
          const link = element("a", tr("Open Google Cloud credentials", "فتح إعداد بيانات Google Cloud"));
          link.href = "https://console.cloud.google.com/apis/credentials"; link.target = "_blank"; link.rel = "noopener noreferrer";
          setup.append(link); detail.append(setup);
        }
        row.append(detail);
        const actions = element("div", "", "connector-actions");
        {
          const connect = element("button", item.configured || item.id === "termux" ? tr("Connect", "اتصال") : tr("Setup required", "الإعداد مطلوب"), "btn primary");
          connect.disabled = !item.configured || ["connected", "authorizing", "testing", "unavailable"].includes(item.status);
          connect.dataset.disabled = String(connect.disabled);
          connect.dataset.action = "connect:" + item.id;
          connect.onclick = async () => {
            if (item.id === "termux" && window.NewAlPhone) {
              const phone = JSON.parse(NewAlPhone.status());
              if (!phone.termux.installed) { NewAlPhone.openUrl("https://f-droid.org/packages/com.termux/"); return; }
              if (!phone.termux.allowed) { NewAlPhone.termuxAllow(); return; }
            }
            await action("connect", item.id);
          };
          actions.append(connect);
        }
        if ((item.id === "termux" || item.has_credentials) && ["connected", "reauthorize", "error"].includes(item.status)) {
          const test = element("button", tr("Test", "اختبار"), "btn");
          test.dataset.action = "test:" + item.id;
          test.onclick = () => action(item.id === "termux" ? "connect" : "test", item.id);
          actions.append(test);
        }
        if (["connected", "authorizing", "testing", "reauthorize", "error"].includes(item.status)) {
          const disconnect = element("button", tr("Disconnect", "فصل"), "btn");
          disconnect.dataset.action = "disconnect:" + item.id;
          disconnect.onclick = () => action("disconnect", item.id);
          actions.append(disconnect);
        }
        detail.append(feedbackNode("account:" + item.id));
        row.append(actions); list.append(row);
      }

      const mcpHeading = element("h3", tr("MCP runtime", "تشغيل MCP"));
      mcpHeading.className = "integration-group-heading";
      list.append(mcpHeading);
      if (failures[2]) list.append(element("p", failures[2], "connector-feedback"));
      for (const item of bundleResult.bundles || []) {
        const row = element("section", "", "connector-card mcp-bundle"), detail = element("div");
        detail.append(element("strong", item.name));
        const label = labels[item.status] || [item.status, item.status];
        detail.append(element("div", tr(...label), "connector-state " + (item.running ? "connected" : "")));
        if (item.description) detail.append(element("div", item.description, "muted"));
        if (item.manual_setup) detail.append(element("div", item.manual_setup, "muted"));
        if (item.native_fallback) detail.append(element("div", item.native_fallback, "muted"));
        if (item.runtime_reason) detail.append(element("div", tr("Runtime: ", "بيئة التشغيل: ") + item.runtime_reason, "muted"));
        if (item.missing?.length) detail.append(element("div", tr("Missing: ", "الناقص: ") + item.missing.join(", "), "muted"));
        if (item.installed) detail.append(element("div", tr("Verified tools: ", "الأدوات المتحقق منها: ") + item.tools, "muted"));
        if (item.last_error) detail.append(element("div", item.last_error, "muted"));
        detail.append(feedbackNode("mcp:" + sid + ":" + item.id));
        row.append(detail);

        const actions = element("div", "", "connector-actions");
        const add = (op, title, primary, disabled = false) => {
          const button = element("button", title, "btn" + (primary ? " primary" : ""));
          button.dataset.action = "mcp:" + op + ":" + item.id;
          button.disabled = disabled;
          button.dataset.disabled = String(button.disabled);
          button.onclick = () => bundleAction(op, item.id);
          actions.append(button);
        };
        if (!item.installed) {
          add("enable", tr("Test & install", "اختبار وتثبيت"), true, !item.available);
        } else if (item.running) {
          add("stop", tr("Stop", "إيقاف"), true);
          add("test", tr("Health check", "فحص الصحة"), false);
          add("disable", tr("Remove", "إزالة"), false);
        } else {
          add("start", tr("Start", "تشغيل"), true, !item.can_start);
          add("reconnect", tr("Reconnect", "إعادة اتصال"), false,
            ["termux_disconnected", "tool_missing", "needs_setup", "permission_required", "runtime_unavailable"].includes(item.status));
          add("test", tr("Test", "اختبار"), false,
            ["termux_disconnected", "tool_missing", "needs_setup", "permission_required", "runtime_unavailable"].includes(item.status));
          add("disable", tr("Remove", "إزالة"), false);
        }
        row.append(actions);
        list.append(row);
      }

      const extensionList = document.querySelector("#extension-list");
      if (extensionList) {
        extensionList.replaceChildren();
        if (failures[1]) extensionList.append(element("p", failures[1], "connector-feedback"));
        for (const item of extensionResult.extensions || []) {
          const provider = ["notion", "gitlab"].includes(item.id) ? item.id + "mcp" : item.id;
          if (result.connectors.some(account => account.id === provider)) continue;
          const row = element("section", "", "connector-card extension-card"), detail = element("div");
          detail.append(element("strong", item.name));
          const ready = item.status === "ready";
          detail.append(element("div", ready ? tr("Ready — real built-in tool", "جاهزة — أداة حقيقية مدمجة") : tr("Not configured", "غير مهيأة"), "connector-state " + (ready ? "connected" : "")));
          detail.append(element("div", item.description || item.requires || "", "muted"));
          if (!ready && item.requires) detail.append(element("small", tr("Requires: ", "تحتاج: ") + item.requires));
          row.append(detail); extensionList.append(row);
        }
      }
      if (focused) [...list.querySelectorAll("button")].find(button => button.dataset.action === focused && !button.disabled)?.focus({preventScroll: true});
      panel.scrollTop = scroll;
    } catch (error) { if (message) message.textContent = errorText(error.message); }
    finally { requesting = false; }
  }
  async function bundleAction(op, id) {
    if (pending) return;
    const sid = current();
    if (!sid) {
      const message = document.querySelector("#connector-message");
      if (message) message.textContent = tr("Open a project conversation first.", "افتح محادثة أو مشروعًا أولًا.");
      return;
    }
    pending = true;
    document.querySelectorAll("#connector-list button").forEach(button => { button.disabled = true; });
    const message = document.querySelector("#connector-message");
    const feedbackKey = "mcp:" + sid + ":" + id;
    showFeedback(feedbackKey, op === "enable"
      ? tr("Preparing dependencies, then verifying MCP…", "جارٍ تجهيز الاعتماديات ثم التحقق من MCP…")
      : tr("Applying server operation…", "جارٍ تنفيذ عملية الخادم…"));
    try {
      const result = await api("/api/mcp-bundles/" + op, {session: sid, id});
      const words = {
        enable: tr("Verified and installed.", "تم التحقق والتثبيت."),
        start: tr("Server started.", "تم تشغيل الخادم."),
        stop: tr("Server stopped.", "تم إيقاف الخادم."),
        reconnect: tr("Server reconnected.", "تمت إعادة اتصال الخادم."),
        test: tr("Health check passed.", "نجح فحص الصحة."),
        disable: tr("MCP removed.", "تمت إزالة MCP.")
      };
      showFeedback(feedbackKey, (words[op] || "") + (result.tools ? tr(" Tools: ", " الأدوات: ") + result.tools : ""));
    } catch (error) { showFeedback(feedbackKey, errorText(error.message)); }
    finally { pending = false; lastView = ""; await refresh(); }
  }

  async function action(op, provider) {
    if (pending) return;
    pending = true;
    document.querySelectorAll("#connector-list button").forEach(button => { button.disabled = true; });
    const message = document.querySelector("#connector-message");
    const feedbackKey = "account:" + provider;
    showFeedback(feedbackKey, tr("Working…", "جارٍ التنفيذ…"));
    try {
      const result = await api("/api/connectors/" + op, {provider});
      showFeedback(feedbackKey, result.text
        || (provider === "termux" && op === "connect" ? tr("Termux test passed.", "نجح اختبار Termux.") : "")
        || (result.status === "authorizing" ? tr("Approve in the browser, then return here.", "وافق في المتصفح ثم ارجع للتطبيق.") : tr("Operation completed.", "اكتملت العملية.")));
    } catch (error) { showFeedback(feedbackKey, errorText(error.message)); }
    finally { pending = false; await refresh(); }
  }
  function close() {
    active = false;
    clearInterval(timer);
    document.querySelector("#connector-dialog").hidden = true;
    document.querySelector("#open-connectors").focus();
  }
  window.openMusabConnectors = () => {
    active = true;
    document.querySelector("#connector-dialog").hidden = false;
    document.querySelector("#connector-close").focus();
    refresh(); clearInterval(timer); timer = setInterval(refresh, 10000);
  };
  document.addEventListener("DOMContentLoaded", () => {
    const button = element("button", tr("Connections", "الاتصالات"), "side-item");
    button.id = "open-connectors";
    button.onclick = window.openMusabConnectors;
    document.querySelector(".side-top").append(button);
    // GitHub's entry point uses the same browser connection flow.
    document.querySelector("#open-github").addEventListener("click", event => {
      event.preventDefault(); event.stopImmediatePropagation(); window.openMusabConnectors();
    }, true);
    const dialog = element("div", "", "modal");
    dialog.id = "connector-dialog"; dialog.hidden = true;
    dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true"); dialog.setAttribute("aria-labelledby", "connector-heading");
    const card = element("div", "", "modal-card connector-panel"), head = element("header");
    const title = element("span", tr("MusabAI connections", "اتصالات MusabAI")); title.id = "connector-heading";
    const closeButton = element("button", "×", "icon-btn"); closeButton.id = "connector-close";
    closeButton.setAttribute("aria-label", tr("Close", "إغلاق")); closeButton.onclick = close;
    const refreshButton = element("button", tr("Refresh", "تحديث"), "btn");
    refreshButton.id = "connector-refresh"; refreshButton.onclick = refresh;
    head.append(title, refreshButton, closeButton); card.append(head);
    card.append(element("p", tr(
      "Account connectors and MCP runtimes share this live status manager. Saved configuration is never presented as a running process.",
      "تستخدم اتصالات الحسابات وMCP مدير الحالة الحي نفسه. لا تُعرض الإعدادات المحفوظة أبدًا على أنها عملية تعمل."
    ), "muted"));
    card.lastElementChild.id = "connector-intro";
    const message = element("p"); message.id = "connector-message"; message.setAttribute("role", "status"); card.append(message);
    const list = element("div"); list.id = "connector-list"; card.append(list); dialog.append(card); document.body.append(dialog);
    card.append(element("h3", tr("Real extensions", "الإضافات الحقيقية")));
    card.append(element("p", tr("Connect services above to add their available tools. Built-in tools below need no account.", "اربط الخدمات بالأعلى لإضافة أدواتها المتاحة. الأدوات المدمجة أدناه لا تحتاج حسابًا."), "muted"));
    const extensionList = element("div"); extensionList.id = "extension-list"; card.append(extensionList);
    dialog.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.stopPropagation(); close(); }
      if (event.key === "Tab") {
        const focusable = [...dialog.querySelectorAll("button:not(:disabled), input:not(:disabled), summary")];
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    dialog.onclick = event => { if (event.target === dialog) close(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  });
})();
