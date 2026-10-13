/* MusabAI workspace bar (phone-first, Cursor-style): Files, Terminal and a live Preview of the app the agent runs
   (a dev server on this phone), next to the Agent/Plan/Ask modes and VS Code. */
(function () {
  "use strict";
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ar = () => (document.documentElement.lang || "").startsWith("ar") || (window.NCi18n && NCi18n.active && NCi18n.active());
  const t = (en, a) => (ar() ? a : en);
  async function call(path) {
    const r = await fetch(path);
    const d = await r.json().catch(() => ({}));
    if (!r.ok || (d && d.error)) throw new Error((d && d.error) || "HTTP " + r.status);
    return d;
  }
  const sid = () => (window.NewAlWorkspaceSession ? window.NewAlWorkspaceSession() : "");
  async function root() {
    const id = sid();
    if (!id) return "";
    try { const d = await call("/api/sessions/" + encodeURIComponent(id)); return (d.meta && d.meta.root) || ""; } catch (_) { return ""; }
  }

  function sheet(id, title) {
    let o = document.getElementById(id);
    if (!o) {
      o = document.createElement("div");
      o.id = id;
      o.className = "musab-preview";
      o.innerHTML = '<div class="mp-card"><header><span class="mp-title"></span><span class="mp-tabs"></span>' +
        '<button type="button" class="mp-x" title="Close">×</button></header><div class="mp-body"></div></div>';
      document.body.appendChild(o);
      o.querySelector(".mp-x").onclick = () => { o.hidden = true; o.querySelector(".mp-body").innerHTML = ""; };
    }
    o.querySelector(".mp-title").textContent = title;
    o.querySelector(".mp-tabs").innerHTML = "";
    o.hidden = false;
    return o;
  }

  // ---------------------------------------------------------------- Files
  async function openFiles() {
    const r = await root();
    const o = sheet("musab-files", t("Files", "الملفات"));
    const body = o.querySelector(".mp-body");
    body.innerHTML = '<div class="mt-search"><input type="search" placeholder="' + t("Search files", "ابحث في الملفات") + '"></div><div class="mt-list"></div>';
    const list = body.querySelector(".mt-list");
    if (!r) { list.innerHTML = '<p class="mp-muted">' + t("Open a project first.", "افتح مشروعاً أولاً.") + "</p>"; return; }
    const load = async q => {
      try {
        const files = await call("/api/files?root=" + encodeURIComponent(r) + "&q=" + encodeURIComponent(q || ""));
        list.innerHTML = files.length ? "" : '<p class="mp-muted">' + t("No files match.", "لا ملفات مطابقة.") + "</p>";
        files.forEach(f => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "mt-file";
          b.innerHTML = '<span class="mt-name">' + esc(f.split("/").pop()) + '</span><span class="mt-dir">' + esc(f) + "</span>";
          b.onclick = () => { o.hidden = true; if (window.MusabCursor) window.MusabCursor.preview(f, r); };
          list.appendChild(b);
        });
      } catch (e) { list.innerHTML = '<p class="mp-muted">' + esc(e.message) + "</p>"; }
    };
    let timer = 0;
    body.querySelector("input").oninput = ev => { clearTimeout(timer); timer = setTimeout(() => load(ev.target.value), 250); };
    load("");
  }

  // ---------------------------------------------------------------- live Preview of a dev server
  async function openPreview() {
    const o = sheet("musab-live", t("Preview", "معاينة"));
    const body = o.querySelector(".mp-body");
    const tabs = o.querySelector(".mp-tabs");
    body.innerHTML = '<p class="mp-muted">' + t("Looking for a running app…", "أبحث عن تطبيق يعمل…") + "</p>";
    let ports = [];
    try { ports = (await call("/api/ports")).ports || []; } catch (_) { /* engine without the route */ }
    if (!ports.length) {
      body.innerHTML = '<p class="mp-muted">' + t("No app is running yet. Ask the agent, e.g. “run the site and show it”, then open Preview again.",
        "لا يوجد تطبيق يعمل بعد. اطلب من الوكيل مثلاً «شغّل الموقع واعرضه» ثم افتح المعاينة مجدداً.") + "</p>";
      return;
    }
    const show = port => {
      body.innerHTML = "";
      const f = document.createElement("iframe");
      f.className = "mp-frame";
      f.src = "http://127.0.0.1:" + port + "/";
      body.appendChild(f);
      tabs.querySelectorAll("button[data-port]").forEach(b => b.classList.toggle("on", b.dataset.port === String(port)));
    };
    tabs.innerHTML = ports.map(p => '<button type="button" data-port="' + p + '">:' + p + "</button>").join("") +
      '<button type="button" class="mt-reload" title="' + t("Reload", "تحديث") + '">⟳</button>';
    tabs.querySelectorAll("button[data-port]").forEach(b => b.onclick = () => show(b.dataset.port));
    tabs.querySelector(".mt-reload").onclick = () => { const f = body.querySelector("iframe"); if (f) f.src = f.src; };
    show(ports[0]);
  }

  function addButtons() {
    const anchor = $("#musab-vscode") || $("#musab-modes");
    if (!anchor || $("#musab-files-btn")) return;
    const items = [
      ["musab-files-btn", "📁", t("Files", "الملفات"), openFiles],
      ["musab-term-btn", "⌨", t("Terminal", "الطرفية"), () => { const b = $("#toggle-terminal"); if (b) b.click(); }],
      ["musab-live-btn", "🌐", t("Preview", "معاينة"), openPreview],
    ];
    let after = anchor;
    items.forEach(([id, icon, label, fn]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.id = id;
      b.className = "musab-vscode";
      b.title = label;
      b.textContent = icon + " " + label;
      b.onclick = fn;
      after.after(b);
      after = b;
    });
  }
  window.MusabTabs = { openFiles, openPreview };
  const boot = () => addButtons();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  setTimeout(boot, 1700);
  setTimeout(boot, 4000);
})();
