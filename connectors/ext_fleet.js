/* MusabAI: background agents and GitHub in the workspace bar.
   - 🤖 Agents: start a background agent (its own worktree), see them run, open one, apply its changes, stop it.
   - 🐙 GitHub: Actions runs and open pull requests (with checks) of the project's repo or any other; watch a run
     in the background, read a failed run's log, rerun it, merge a pull request.
   - What a background thread asks approval for shows up wherever you are; when one finishes you are told. */
(function () {
  "use strict";
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ar = () => (document.documentElement.lang || "").startsWith("ar") || (window.NCi18n && NCi18n.active && NCi18n.active());
  const t = (en, a) => (ar() ? a : en);
  const sid = () => (window.NewAlWorkspaceSession ? window.NewAlWorkspaceSession() : "");
  const openThread = id => { if (window.NewAlOpenSession) window.NewAlOpenSession(id); };
  async function get(path) {
    const r = await fetch(path);
    const d = await r.json().catch(() => ({}));
    if (!r.ok || (d && d.error)) throw new Error((d && d.error) || "HTTP " + r.status);
    return d;
  }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || (d && d.error)) throw new Error((d && d.error) || "HTTP " + r.status);
    return d;
  }

  // ---------------------------------------------------------------- toasts (with an action)
  function toast(html, action, label, ms) {
    let box = $("#mf-toasts");
    if (!box) { box = document.createElement("div"); box.id = "mf-toasts"; document.body.appendChild(box); }
    const el = document.createElement("div");
    el.className = "mf-toast";
    el.innerHTML = '<div class="mf-tt">' + html + "</div>" + (action ? '<button type="button" class="mf-go">' + esc(label) + "</button>" : "") +
      '<button type="button" class="mf-x" aria-label="close">×</button>';
    if (action) el.querySelector(".mf-go").onclick = () => { el.remove(); action(); };
    el.querySelector(".mf-x").onclick = () => el.remove();
    box.appendChild(el);
    setTimeout(() => el.remove(), ms || 12000);
    return el;
  }

  // ---------------------------------------------------------------- the background agents
  let fleet = [];
  const fleetIds = () => new Set(fleet.map(a => a.id));
  async function loadFleet() { try { fleet = (await get("/api/fleet")).agents || []; } catch (_) { fleet = []; } return fleet; }

  function sheet(id, title) {
    let o = document.getElementById(id);
    if (!o) {
      o = document.createElement("div");
      o.id = id;
      o.className = "musab-preview";
      o.innerHTML = '<div class="mp-card"><header><span class="mp-title"></span><span class="mp-tabs"></span>' +
        '<button type="button" class="mp-x" title="Close">×</button></header><div class="mp-body mf-body"></div></div>';
      document.body.appendChild(o);
      o.querySelector(".mp-x").onclick = () => { o.hidden = true; clearInterval(o._timer); };
    }
    o.querySelector(".mp-title").textContent = title;
    o.hidden = false;
    return o;
  }

  const stateWord = s => ({ running: "⏳ " + t("running", "يعمل"), done: "✓ " + t("done", "انتهى"), stopped: "■ " + t("stopped", "متوقف"), gone: "?" }[s] || s);

  async function openAgents() {
    const o = sheet("musab-agents", t("Background agents", "الوكلاء في الخلفية"));
    const body = o.querySelector(".mp-body");
    body.innerHTML = '<form class="mf-new"><textarea rows="3" placeholder="' + esc(t("A task for a background agent… (it works in its own copy of the project)",
      "مهمة لوكيل في الخلفية… (يعمل على نسخة منفصلة من المشروع)")) + '"></textarea><button type="submit" class="mf-btn primary">' +
      t("Start", "ابدأ") + '</button></form><div class="mf-list"></div>';
    const list = body.querySelector(".mf-list");
    body.querySelector("form").onsubmit = async ev => {
      ev.preventDefault();
      const ta = body.querySelector("textarea");
      const prompt = ta.value.trim();
      if (!prompt) return;
      if (!sid()) { toast(esc(t("Open a thread in the project first.", "افتح محادثة في المشروع أولاً."))); return; }
      try {
        const d = await post("/api/fleet/start", { session: sid(), prompt });
        ta.value = "";
        toast("⚙ " + esc(d.name) + " — " + esc(t("started", "بدأ")), () => openThread(d.id), t("Open", "افتح"));
        render();
      } catch (e) { toast(esc(e.message)); }
    };
    const render = async () => {
      await loadFleet();
      if (!fleet.length) {
        list.innerHTML = '<p class="mp-muted">' + t("No background agents yet. Give one a task above, or type /bg <task> in the chat.",
          "لا يوجد وكلاء بعد. أعطِ واحداً مهمة في الأعلى، أو اكتب ‎/bg <المهمة>‎ في المحادثة.") + "</p>";
        return;
      }
      list.innerHTML = "";
      fleet.forEach(a => {
        const row = document.createElement("div");
        row.className = "mf-row";
        const changed = (a.changes || []).length;
        row.innerHTML = '<div class="mf-head"><b>⚙ ' + esc(a.name) + '</b><span class="mf-state ' + esc(a.state) + '">' + stateWord(a.state) + "</span></div>" +
          '<div class="mf-meta">' + (a.worktree ? "⑂ " + t("own worktree", "نسخة منفصلة") + " · " : "") + changed + " " + t("files changed", "ملفات معدّلة") + "</div>" +
          (a.answer ? '<div class="mf-answer">' + esc(a.answer) + "</div>" : "") +
          '<div class="mf-acts"><button type="button" class="mf-btn" data-a="open">' + t("Open", "افتح") + "</button>" +
          (a.state === "running" ? '<button type="button" class="mf-btn danger" data-a="stop">' + t("Stop", "أوقف") + "</button>" : "") +
          (a.state !== "running" && a.worktree && changed ? '<button type="button" class="mf-btn primary" data-a="apply">' + t("Apply changes", "طبّق التغييرات") + "</button>" : "") +
          "</div>";
        row.querySelectorAll("button").forEach(b => b.onclick = async () => {
          try {
            if (b.dataset.a === "open") { o.hidden = true; openThread(a.id); return; }
            if (b.dataset.a === "stop") await post("/api/sessions/" + encodeURIComponent(a.id) + "/interrupt", {});
            if (b.dataset.a === "apply") {
              const d = await post("/api/sessions/" + encodeURIComponent(a.id) + "/apply", {});
              toast("✓ " + esc(t("Applied", "طُبّق") + ": " + (d.applied || []).join(", ")));
            }
            render();
          } catch (e) { toast(esc(e.message)); }
        });
        list.appendChild(row);
      });
    };
    clearInterval(o._timer);
    o._timer = setInterval(() => { if (!o.hidden) render(); }, 5000);
    render();
  }

  // ---------------------------------------------------------------- GitHub
  async function openGitHub() {
    const o = sheet("musab-gh", "GitHub");
    const body = o.querySelector(".mp-body");
    const saved = (() => { try { return localStorage.getItem("musab.gh.repo") || ""; } catch (_) { return ""; } })();
    body.innerHTML = '<form class="mf-repo"><input type="text" placeholder="' + esc(t("owner/repo (empty: this project)", "المالك/المستودع (فارغ: هذا المشروع)")) +
      '" value="' + esc(saved) + '"><button type="submit" class="mf-btn">⟳</button></form><div class="mf-gh"><p class="mp-muted">…</p></div><pre class="mf-log" hidden></pre>';
    const out = body.querySelector(".mf-gh");
    const log = body.querySelector(".mf-log");
    const repo = () => body.querySelector("input").value.trim();
    const act = async (action, number, extra) => {
      const d = await post("/api/gh/action", Object.assign({ session: sid(), repo: repo(), action, number: String(number || "") }, extra || {}));
      return d.text || "";
    };
    const render = async () => {
      try { localStorage.setItem("musab.gh.repo", repo()); } catch (_) { /* private mode */ }
      try {
        const d = await get("/api/gh/overview?session=" + encodeURIComponent(sid()) + "&repo=" + encodeURIComponent(repo()));
        const cls = s => /✓/.test(s) ? "ok" : /✗/.test(s) ? "bad" : /…/.test(s) ? "run" : "";
        out.innerHTML = '<h4>' + esc(d.repo) + " · " + t("Actions runs", "بنيات Actions") + "</h4>" +
          (d.runs.length ? d.runs.map(r => '<div class="mf-run"><span class="mf-st ' + cls(r.state) + '">' + esc(r.state) + "</span> <b>#" + esc(r.number) + "</b> " +
            esc(r.name) + ' <span class="mp-muted">' + esc(r.branch || "") + " · " + esc((r.title || "").slice(0, 60)) + (r.took ? " · " + esc(r.took) : "") + "</span>" +
            '<span class="mf-acts">' + (r.status !== "completed" ? '<button type="button" class="mf-btn" data-w="' + r.id + '">👀 ' + t("Watch", "راقب") + "</button>" : "") +
            (/✗/.test(r.state) ? '<button type="button" class="mf-btn" data-l="' + r.id + '">' + t("Log", "السجل") + '</button><button type="button" class="mf-btn" data-r="' + r.id + '">' + t("Rerun", "أعد") + "</button>" : "") +
            '<a class="mf-btn" target="_blank" rel="noopener" href="' + esc(r.url) + '">↗</a></span></div>').join("") : '<p class="mp-muted">' + t("No runs.", "لا توجد بنيات.") + "</p>") +
          "<h4>" + t("Open pull requests", "طلبات الدمج المفتوحة") + "</h4>" +
          (d.prs.length ? d.prs.map(p => '<div class="mf-run"><b>#' + esc(p.number) + "</b> " + esc(p.title) + ' <span class="mp-muted">' + esc(p.head) + " → " + esc(p.base) +
            (p.checks ? " · " + esc(p.checks) : "") + '</span><span class="mf-acts"><button type="button" class="mf-btn" data-c="' + p.number + '">' + t("Checks", "الفحوص") + "</button>" +
            (p.draft ? "" : '<button type="button" class="mf-btn primary" data-m="' + p.number + '">' + t("Merge", "ادمج") + "</button>") +
            '<a class="mf-btn" target="_blank" rel="noopener" href="' + esc(p.url) + '">↗</a></span></div>').join("") : '<p class="mp-muted">' + t("None.", "لا يوجد.") + "</p>");
        const show = text => { log.hidden = false; log.textContent = text; log.scrollIntoView({ block: "nearest" }); };
        out.querySelectorAll("[data-w]").forEach(b => b.onclick = async () => { try { toast("👀 " + esc(await act("watch", b.dataset.w))); } catch (e) { toast(esc(e.message)); } });
        out.querySelectorAll("[data-l]").forEach(b => b.onclick = async () => { show("…"); try { show(await act("logs", b.dataset.l)); } catch (e) { show(e.message); } });
        out.querySelectorAll("[data-r]").forEach(b => b.onclick = async () => { try { toast(esc(await act("rerun", b.dataset.r))); render(); } catch (e) { toast(esc(e.message)); } });
        out.querySelectorAll("[data-c]").forEach(b => b.onclick = async () => { show("…"); try { show(await act("checks", b.dataset.c)); } catch (e) { show(e.message); } });
        out.querySelectorAll("[data-m]").forEach(b => b.onclick = async () => {
          if (!confirm(t("Merge pull request #", "دمج طلب الدمج #") + b.dataset.m + "?")) return;
          try { toast("✓ " + esc(await act("merge", b.dataset.m, { method: "merge" }))); render(); } catch (e) { toast(esc(e.message)); }
        });
      } catch (e) { out.innerHTML = '<p class="mp-muted">' + esc(e.message) + "</p>"; }
    };
    body.querySelector("form").onsubmit = ev => { ev.preventDefault(); render(); };
    clearInterval(o._timer);
    o._timer = setInterval(() => { if (!o.hidden) render(); }, 20000);
    render();
  }

  // ---------------------------------------------------------------- events from the other threads
  const approvalCards = {};
  function showApproval(ev) {
    if (approvalCards[ev.id]) return;
    const a = fleet.find(x => x.id === ev.session);
    const args = ev.args || {};
    const what = args.command || args.url || args.path || JSON.stringify(args).slice(0, 300);
    const el = toast("<b>" + esc(a ? "⚙ " + a.name : t("Another thread", "محادثة أخرى")) + "</b> " + esc(t("asks to use", "يطلب استخدام")) + " <code>" + esc(ev.tool) +
      "</code><pre>" + esc(String(what).slice(0, 600)) + '</pre><div class="mf-acts"><button type="button" class="mf-btn primary" data-a="once">' + t("Allow", "اسمح") +
      '</button><button type="button" class="mf-btn" data-a="always">' + t("Always", "دائماً") + '</button><button type="button" class="mf-btn danger" data-a="deny">' + t("Deny", "ارفض") + "</button></div>",
      () => openThread(ev.session), t("Open", "افتح"), 10 * 60 * 1000);
    el.classList.add("mf-approval");
    approvalCards[ev.id] = el;
    el.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => {
      el.remove();
      delete approvalCards[ev.id];
      try { await post("/api/approvals/" + encodeURIComponent(ev.id), { answer: b.dataset.a }); } catch (e) { toast(esc(e.message)); }
    });
  }

  function onEvent(ev) {
    if (!ev || !ev.type) return;
    const here = ev.session === sid();
    if (ev.type === "fleet") { loadFleet(); return; }
    if (ev.type === "approval" && !here) { loadFleet().then(() => showApproval(ev)); return; }
    if (ev.type === "approval_result" && approvalCards[ev.id]) { approvalCards[ev.id].remove(); delete approvalCards[ev.id]; return; }
    if (ev.type === "turn_end" && !ev.sub && !here && fleetIds().has(ev.session)) {
      const a = fleet.find(x => x.id === ev.session);
      toast((ev.error && ev.error !== "interrupted" ? "⚠ " : "✓ ") + "<b>⚙ " + esc(a ? a.name : "") + "</b> " + esc(t("finished", "انتهى")) +
        (ev.answer ? '<div class="mf-answer">' + esc(String(ev.answer).slice(0, 220)) + "</div>" : ""), () => openThread(ev.session), t("Open", "افتح"), 20000);
      loadFleet();
      return;
    }
    if (ev.type === "gh_watch" && ev.state === "end") {
      toast(esc(ev.title || ""), here ? null : () => openThread(ev.session), t("Open", "افتح"), 30000);
    }
  }

  function connect() {
    try {
      const es = new EventSource("/api/events");
      es.onmessage = e => { try { onEvent(JSON.parse(e.data)); } catch (_) { /* not ours */ } };
    } catch (_) { /* no events in this browser */ }
  }

  function addButtons() {
    const anchor = $("#musab-live-btn") || $("#musab-vscode") || $("#musab-modes");
    if (!anchor || $("#musab-agents-btn")) return;
    const items = [["musab-agents-btn", "🤖", t("Agents", "الوكلاء"), openAgents], ["musab-gh-btn", "🐙", "GitHub", openGitHub]];
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

  window.MusabFleet = { openAgents, openGitHub };
  const boot = () => addButtons();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  setTimeout(boot, 1800);
  setTimeout(boot, 4200);
  loadFleet();
  connect();
})();
