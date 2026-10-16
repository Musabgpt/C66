/* MusabAI review (Cursor-style): per-hunk Keep / Undo in the review pane and a restore (checkpoint) button on every
   message. Loaded after cursor_ui.js; app.js calls MusabReview.hunkView and MusabReview.onTurn. */
(function () {
  "use strict";
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
    const el = document.querySelector("#toast");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(note.timer);
    note.timer = setTimeout(() => { el.hidden = true; }, 3500);
  }
  const sid = () => (window.NewAlWorkspaceSession ? window.NewAlWorkspaceSession() : "");

  // ------------------------------------------------------------------ review: per-hunk Keep / Undo, checkpoints
  function hunkView(sessionId, change, refresh) {
    const box = document.createElement("div");
    box.className = "mh-box";
    box.innerHTML = '<p class="mp-muted">' + t("Loading…", "جارٍ التحميل…") + "</p>";
    const load = async () => {
      let d;
      try { d = await call("/api/sessions/" + encodeURIComponent(sessionId) + "/hunks", { path: change.path }); }
      catch (e) { box.innerHTML = '<p class="mp-muted">' + esc(e.message) + "</p>"; return; }
      if (!d.hunks.length) { box.innerHTML = '<p class="mp-muted">' + t("No changes left in this file.", "لا تغييرات متبقية في هذا الملف.") + "</p>"; if (refresh) refresh(); return; }
      box.innerHTML = "";
      d.hunks.forEach(hk => {
        const card = document.createElement("div");
        card.className = "mh-hunk";
        const kept = store.get("musab.kept." + sessionId + "." + change.path + "." + d.sig + "." + hk.i, "");
        if (kept) card.classList.add("kept");
        card.innerHTML = '<div class="mh-head"><span>' + t("Line", "السطر") + " " + hk.new_start + '</span><span class="mh-actions">' +
          '<button type="button" class="mh-keep" title="' + t("Keep", "إبقاء") + '">✓</button>' +
          '<button type="button" class="mh-undo" title="' + t("Undo this change", "تراجع عن هذا التغيير") + '">✗</button></span></div>' +
          '<pre class="mh-lines">' + hk.removed.map(l => '<span class="del">- ' + esc(l) + "</span>").join("\n") +
          (hk.removed.length && hk.added.length ? "\n" : "") + hk.added.map(l => '<span class="add">+ ' + esc(l) + "</span>").join("\n") + "</pre>";
        card.querySelector(".mh-keep").onclick = () => {
          card.classList.add("kept");
          store.set("musab.kept." + sessionId + "." + change.path + "." + d.sig + "." + hk.i, "1");
        };
        card.querySelector(".mh-undo").onclick = async () => {
          try {
            await call("/api/sessions/" + encodeURIComponent(sessionId) + "/revert_hunk", { path: change.path, hunk: hk.i, sig: d.sig });
            note(t("Change undone", "تم التراجع عن التغيير"));
            await load();
            if (refresh) refresh();
          } catch (e) { note(e.message); }
        };
        box.appendChild(card);
      });
    };
    load();
    return box;
  }

  // A restore button on every message: the project goes back to how it was before it (Cursor's checkpoints).
  function onTurn(bubble, ev, replay) {
    if (!bubble || !ev || !ev.turn || bubble.querySelector(".mh-restore")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mh-restore";
    b.title = t("Restore the files to before this message", "إرجاع الملفات إلى ما قبل هذه الرسالة");
    b.textContent = "↺";
    b.onclick = async () => {
      if (!confirm(t("Restore all files to how they were before this message? Later changes are undone.",
        "إرجاع كل الملفات إلى ما كانت عليه قبل هذه الرسالة؟ سيتم التراجع عن كل التغييرات بعدها."))) return;
      try {
        const r = await call("/api/sessions/" + encodeURIComponent(sid()) + "/restore", { turn: ev.turn });
        note(r.reverted.length ? t("Restored: ", "تمت الاستعادة: ") + r.reverted.join(", ") : t("Nothing to restore", "لا شيء للاستعادة"));
      } catch (e) { note(e.message); }
    };
    bubble.appendChild(b);
  }

  window.MusabReview = { hunkView, onTurn };
})();
