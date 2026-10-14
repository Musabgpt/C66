/* MusabAI Cursor-style layer: Agent / Plan / Ask modes, file previews inside the chat, VS Code (code-server in
   Termux). Loaded after app.js; it only adds to the page and talks to the engine's own API (same origin, the key is
   in the session cookie). */
(function () {
  "use strict";
  const VSCODE_PORT = 8780;
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ar = () => (document.documentElement.lang || "").startsWith("ar") || (window.NCi18n && NCi18n.active && NCi18n.active());
  const t = (en, a) => (ar() ? a : en);
  const store = {
    get(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
  };

  async function call(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.error) throw new Error(data.error || "HTTP " + r.status);
    return data;
  }
  function note(text) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(note.timer);
    note.timer = setTimeout(() => { el.hidden = true; }, 3500);
  }
  const sid = () => (window.NewAlWorkspaceSession ? window.NewAlWorkspaceSession() : "");
  async function sessionRoot() {
    const id = sid();
    if (!id) return "";
    try {
      const d = await call("/api/sessions/" + encodeURIComponent(id));
      return (d.meta && d.meta.root) || d.root || "";
    } catch (_) { return ""; }
  }

  // ------------------------------------------------------------------ modes (Cursor: Agent / Plan / Ask)
  const MODES = [
    ["agent", "Agent", "وكيل", "Edits files and runs commands"],
    ["plan", "Plan", "خطة", "Explores, then proposes a plan before changing anything"],
    ["ask", "Ask", "سؤال", "Answers and reads code; changes nothing"],
  ];
  let mode = store.get("musab.mode", "agent");
  async function setSessionMode(value) {
    const id = sid();
    if (!id) return;
    try { await call("/api/sessions/" + encodeURIComponent(id) + "/send", { text: "/mode " + value }); } catch (_) { /* no thread yet */ }
  }
  // Ask = read-only for the thread; leaving Ask gives back the thread's own mode (Full access stays Full access).
  async function rememberMode() {
    const id = sid();
    if (!id) return;
    try {
      const d = await call("/api/sessions/" + encodeURIComponent(id));
      const m = d.meta && d.meta.mode;
      if (m && m !== "read-only") store.set("musab.agentMode", m);
    } catch (_) { /* keep the last one */ }
  }
  // A thread opened or created while Ask is on is read-only too (the pill and the thread never disagree).
  let lastSid = "";
  setInterval(() => {
    const id = sid();
    if (id && id !== lastSid) {
      lastSid = id;
      if (mode === "ask") rememberMode().then(() => setSessionMode("read-only"));
    }
  }, 1200);
  function renderModes() {
    let bar = $("#musab-modes");
    const foot = $(".composer-foot");
    if (!foot) return;
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "musab-modes";
      bar.className = "musab-modes";
      foot.insertBefore(bar, foot.firstChild);
    }
    bar.innerHTML = MODES.map(m => '<button type="button" data-mode="' + m[0] + '" title="' + esc(m[3]) + '" class="' +
      (m[0] === mode ? "on" : "") + '">' + esc(m[1]) + "</button>").join("");   // i18n.js translates it as drawn
    bar.querySelectorAll("button").forEach(b => b.onclick = async () => {
      const prev = mode;
      mode = b.dataset.mode;
      store.set("musab.mode", mode);
      renderModes();
      if (mode === "ask") { await rememberMode(); await setSessionMode("read-only"); }
      else if (prev === "ask") await setSessionMode(store.get("musab.agentMode", "auto-edit"));
      note(t(MODES.find(m => m[0] === mode)[3], mode === "agent" ? "يعدّل الملفات ويشغّل الأوامر" : mode === "plan" ? "يستكشف ثم يقترح خطة قبل أي تغيير" : "يجيب ويقرأ الكود دون تغيير"));
    });
  }
  // app.js calls this on every message it sends.
  function transform(text) {
    if (!text || text.startsWith("/") || text.startsWith("!") || text.startsWith("#")) return text;
    if (mode === "plan") return "/plan " + text;
    return text;
  }

  // ------------------------------------------------------------------ file previews
  const KIND = [
    [/\.(png|jpe?g|gif|webp|bmp|ico|svg)$/i, "image"],
    [/\.(html?|xhtml)$/i, "html"],
    [/\.(md|markdown)$/i, "markdown"],
    [/\.(csv|tsv)$/i, "table"],
    [/\.json$/i, "json"],
    [/\.pdf$/i, "pdf"],
    [/\.(mp4|webm|mov)$/i, "video"],
    [/\.(mp3|wav|ogg|m4a)$/i, "audio"],
  ];
  const kindOf = p => (KIND.find(k => k[0].test(p)) || [0, "code"])[1];
  const rawUrl = (root, path) => "/api/file/raw?root=" + encodeURIComponent(root) + "&path=" + encodeURIComponent(path);

  function sheet(title) {
    let o = $("#musab-preview");
    if (!o) {
      o = document.createElement("div");
      o.id = "musab-preview";
      o.className = "musab-preview";
      o.innerHTML = '<div class="mp-card"><header><span class="mp-title"></span><span class="mp-tabs"></span>' +
        '<button type="button" class="mp-x" title="Close">×</button></header><div class="mp-body"></div></div>';
      document.body.appendChild(o);
      o.querySelector(".mp-x").onclick = () => { o.hidden = true; o.querySelector(".mp-body").innerHTML = ""; };
      o.addEventListener("click", ev => { if (ev.target === o) o.querySelector(".mp-x").click(); });
    }
    o.querySelector(".mp-title").textContent = title;
    o.querySelector(".mp-tabs").innerHTML = "";
    o.hidden = false;
    return o;
  }
  function csvTable(text, sep) {
    const rows = text.split(/\r?\n/).filter(Boolean).slice(0, 1000).map(line => {
      const out = []; let cur = "", q = false;
      for (let i = 0; i < line.length; i++) {
        const c = line[i];
        if (c === '"') { if (q && line[i + 1] === '"') { cur += '"'; i++; } else q = !q; }
        else if (c === sep && !q) { out.push(cur); cur = ""; }
        else cur += c;
      }
      out.push(cur);
      return out;
    });
    if (!rows.length) return "<p>(empty)</p>";
    return '<div class="mp-table"><table><thead><tr>' + rows[0].map(c => "<th>" + esc(c) + "</th>").join("") + "</tr></thead><tbody>" +
      rows.slice(1).map(r => "<tr>" + r.map(c => "<td>" + esc(c) + "</td>").join("") + "</tr>").join("") + "</tbody></table></div>";
  }
  function codeView(text) {
    const lines = text.split("\n");
    return '<pre class="mp-code">' + lines.map((l, i) => '<span class="ln">' + (i + 1) + "</span>" + esc(l)).join("\n") + "</pre>";
  }
  async function preview(path, root) {
    root = root || await sessionRoot();
    const o = sheet(path);
    const body = o.querySelector(".mp-body");
    const tabs = o.querySelector(".mp-tabs");
    const kind = kindOf(path);
    const raw = rawUrl(root, path);
    body.innerHTML = '<p class="mp-muted">' + t("Loading…", "جارٍ التحميل…") + "</p>";
    try {
      if (kind === "image") { body.innerHTML = '<img class="mp-img" alt="" src="' + esc(raw) + '">'; return; }
      if (kind === "video") { body.innerHTML = '<video controls class="mp-img" src="' + esc(raw) + '"></video>'; return; }
      if (kind === "audio") { body.innerHTML = '<audio controls src="' + esc(raw) + '"></audio>'; return; }
      if (kind === "pdf") {
        body.innerHTML = '<p>' + t("PDF file.", "ملف PDF.") + ' <button type="button" class="btn">' + t("Open", "فتح") + "</button></p>";
        body.querySelector("button").onclick = () => {
          if (window.NewAlPhone && NewAlPhone.saveDocument) NewAlPhone.saveDocument(raw, path.split("/").pop(), "application/pdf");
          else window.open(raw, "_blank");
        };
        return;
      }
      const d = await call("/api/file?root=" + encodeURIComponent(root) + "&path=" + encodeURIComponent(path));
      const text = d.text || "";
      const views = { code: () => codeView(text) };
      if (kind === "html") views.preview = () => {
        const f = document.createElement("iframe");
        f.className = "mp-frame";
        f.setAttribute("sandbox", "allow-scripts allow-forms allow-modals");
        f.srcdoc = text;
        return f;
      };
      if (kind === "markdown" && window.renderMarkdown) views.preview = () => '<div class="msg-assistant">' + window.renderMarkdown(text) + "</div>";
      if (kind === "table") views.preview = () => csvTable(text, /\.tsv$/i.test(path) ? "\t" : ",");
      if (kind === "json") views.preview = () => { try { return codeView(JSON.stringify(JSON.parse(text), null, 2)); } catch (_) { return codeView(text); } };
      const show = name => {
        const v = views[name]();
        body.innerHTML = "";
        if (typeof v === "string") body.innerHTML = v; else body.appendChild(v);
        tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === name));
      };
      const names = Object.keys(views).sort(a => (a === "preview" ? -1 : 1));
      if (names.length > 1) {
        tabs.innerHTML = names.map(n => '<button type="button" data-v="' + n + '">' + (n === "preview" ? t("Preview", "معاينة") : t("Code", "الكود")) + "</button>").join("");
        tabs.querySelectorAll("button").forEach(b => b.onclick = () => show(b.dataset.v));
      }
      show(names[0]);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "mp-copy";
      copy.textContent = t("Copy", "نسخ");
      copy.onclick = () => { navigator.clipboard ? navigator.clipboard.writeText(text) : (window.NewAlPhone && NewAlPhone.setClipboard(text)); note(t("Copied", "تم النسخ")); };
      tabs.appendChild(copy);
    } catch (e) {
      body.innerHTML = '<p class="mp-muted">' + esc(e.message) + "</p>";
    }
  }

  // A preview button on every tool card that names a file (read, edit, write, patch).
  function decorate(el, ev) {
    try {
      shotCard(el, ev);
      const m = (ev && ev.meta) || {};
      const a = (ev && ev.args) || {};
      const path = m.path || a.path || a.file_path || (m.files && m.files[0]) || "";
      if (!el || !path || el.querySelector(".mp-open")) return;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "mp-open";
      b.title = t("Preview", "معاينة");
      b.textContent = "👁";
      b.onclick = ev2 => { ev2.stopPropagation(); preview(String(path)); };
      const head = el.querySelector(".item-head") || el.querySelector(".meta") || el;
      head.appendChild(b);
    } catch (_) { /* decoration never breaks the chat */ }
  }
  // File paths written as `code` in answers open a preview when tapped.
  const PATHLIKE = /^[\w./-]+\.[A-Za-z0-9]{1,8}$/;
  document.addEventListener("click", ev => {
    const c = ev.target.closest && ev.target.closest(".msg-assistant code");
    if (!c || c.closest("pre")) return;
    const p = c.textContent.trim();
    if (PATHLIKE.test(p) && !/^\d+(\.\d+)+$/.test(p)) preview(p.replace(/^\.\//, ""));
  });

  // ------------------------------------------------------------------ pictures the agent looked at
  // A phone screenshot (or an image in the project that the agent looked at) shows in its tool card; a tap enlarges it.
  // The engine keeps a copy with the session and serves it from /api/shot (raster files only).
  const SHOT_SID = /^[A-Za-z0-9_\-]{1,80}$/, SHOT_NAME = /^[0-9a-f]{16}\.(jpg|png|webp)$/;
  function showShot(url) {
    const o = sheet(t("Screenshot", "لقطة شاشة"));
    o.querySelector(".mp-body").innerHTML = '<img class="mp-img" alt="" src="' + esc(url) + '">';
  }
  function shotCard(el, ev) {
    const shot = ev && ev.meta && ev.meta.shot;
    // typeof first: a missing field would otherwise test as the text "undefined" and pass the pattern
    const valid = !!shot && typeof shot.sid === "string" && typeof shot.name === "string" &&
      SHOT_SID.test(shot.sid) && SHOT_NAME.test(shot.name);
    if (!el || !valid || el.querySelector(".mp-shot")) return;
    const url = "/api/shot/" + shot.sid + "/" + shot.name;
    const img = document.createElement("img");
    img.className = "mp-shot";
    img.alt = t("Screenshot", "لقطة شاشة");
    img.loading = "lazy";
    img.src = url;
    img.onclick = ev2 => { ev2.stopPropagation(); showShot(url); };
    (el.querySelector(".item-body") || el).prepend(img);
    el.classList.add("open");   // the picture is the point of the card: it shows without a tap
  }

  // ------------------------------------------------------------------ copy a code block in an answer
  function copyText(text) {
    if (window.NewAlPhone && NewAlPhone.setClipboard) return Promise.resolve().then(() => NewAlPhone.setClipboard(text));
    return navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject(new Error("no clipboard"));
  }
  function addCopy(pre) {
    if (pre.dataset.copy || !pre.closest(".msg-assistant")) return;   // tool output is not an answer's code
    pre.dataset.copy = "1";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "code-copy";
    b.textContent = t("Copy", "نسخ");
    b.onclick = ev => {
      ev.stopPropagation();
      const code = pre.querySelector("code");
      // the code only: the button's own label is not part of it
      const text = code ? code.textContent : [...pre.childNodes].filter(n => n !== b).map(n => n.textContent).join("");
      copyText(text).then(() => {
        b.textContent = t("Copied", "تم النسخ");
        setTimeout(() => { b.textContent = t("Copy", "نسخ"); }, 1500);
      }, () => note(t("Copy failed", "تعذّر النسخ")));
    };
    pre.appendChild(b);
  }
  let copyWatching = false;
  function watchCode() {
    const box = $("#messages");
    if (!box || copyWatching || !window.MutationObserver) return;
    copyWatching = true;
    new MutationObserver(list => {
      for (const m of list) for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.tagName === "PRE") addCopy(n);
        else if (n.querySelectorAll) n.querySelectorAll("pre").forEach(addCopy);
      }
    }).observe(box, { childList: true, subtree: true });
    box.querySelectorAll("pre").forEach(addCopy);
  }

  // ------------------------------------------------------------------ VS Code (code-server in Termux)
  async function openVSCode() {
    let st = {};
    try { st = await call("/api/vscode/status"); } catch (_) { /* engine without the route */ }
    const root = await sessionRoot();
    const url = "http://127.0.0.1:" + VSCODE_PORT + "/" + (root && root.indexOf("com.termux") >= 0 ? "?folder=" + encodeURIComponent(root) : "");
    if (st.up) { location.href = url; return; }
    if (!(window.NewAlPhone && NewAlPhone.termuxRunVisible)) { note(t("VS Code needs Termux on the phone", "يحتاج VS Code إلى Termux على الهاتف")); return; }
    let command = "";
    try { command = (await call("/api/vscode/link")).command; } catch (e) { note(e.message); return; }
    const ph = JSON.parse(NewAlPhone.status() || "{}").termux || {};
    if (!ph.installed) { NewAlPhone.termuxSetup(""); return; }
    if (!ph.allowed && NewAlPhone.termuxAllow) {
      NewAlPhone.termuxAllow();
      for (let i = 0; i < 20 && !(JSON.parse(NewAlPhone.status() || "{}").termux || {}).allowed; i++) await new Promise(r => setTimeout(r, 1000));
    }
    if (NewAlPhone.termuxRunVisible(command) !== "started") {
      // Termux does not take commands from apps yet: one paste (it also turns that on for next time).
      NewAlPhone.termuxSetup(command);
      note(t("In Termux: paste and press Enter", "في Termux: الصق واضغط Enter"));
    } else note(t("Starting VS Code in Termux… the first time installs it (a few minutes)", "يتم تشغيل VS Code في Termux… أول مرة يتم تثبيته (بضع دقائق)"));
    const started = Date.now();
    const timer = setInterval(async () => {
      try {
        const s = await call("/api/vscode/status");
        if (s.up) { clearInterval(timer); location.href = url; }
      } catch (_) { /* keep waiting */ }
      if (Date.now() - started > 900000) clearInterval(timer);
    }, 4000);
  }
  function addVSCodeButton() {
    if (!window.NewAlPhone || $("#musab-vscode")) return;
    const bar = $("#musab-modes");
    if (!bar) return;
    const b = document.createElement("button");
    b.type = "button";
    b.id = "musab-vscode";
    b.className = "musab-vscode";
    b.title = "VS Code";
    b.setAttribute("aria-label", "VS Code");
    b.innerHTML = '<span class="mb-ic" aria-hidden="true">⌘</span><span class="mb-tx">VS Code</span>';
    b.onclick = openVSCode;
    bar.after(b);
  }

  function boot() {
    renderModes();
    addVSCodeButton();
    watchCode();
  }
  window.MusabCursor = { transform, decorate, preview, openVSCode, mode: () => mode };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  setTimeout(boot, 1500);
})();
