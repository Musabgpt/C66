/* Scheduled tasks: create, review, pause, resume and delete without leaving MusabAI.
   Runs entirely through the authenticated local API, with no external web service. */
(function () {
  "use strict";
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, ch =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  async function request(path, data) {
    const response = await fetch(path, data === undefined
      ? { credentials: "same-origin" }
      : { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data) });
    const json = await response.json();
    if (!response.ok || json.error) throw new Error(json.error || "HTTP " + response.status);
    return json;
  }
  function prettyTime(seconds) {
    if (!seconds) return "—";
    return new Date(Number(seconds) * 1000).toLocaleString(navigator.language || "ar");
  }
  const status = { active: "نشطة", running: "قيد التنفيذ", paused: "موقوفة",
                   done: "اكتملت" };
  const types = { reminder: "تذكير", agent: "فحص ذكي", watch: "مراقبة شرط" };
  let panel, list;
  function close() { if (panel) panel.hidden = true; }
  function showError(err) {
    const node = $("#scheduled-error", panel);
    if (node) { node.textContent = err.message || String(err); node.hidden = false; }
  }
  async function action(id, command) {
    if (command === "delete" && !confirm("حذف المهمة نهائياً؟")) return;
    try {
      await request("/api/scheduled-tasks/" + id + "/" + command, {});
      await refresh();
    } catch (err) { showError(err); }
  }
  async function refresh() {
    const data = await request("/api/scheduled-tasks");
    const banner = $("#scheduled-status", panel);
    banner.textContent = data.scheduler
      ? "المهام محفوظة على الهاتف. التنفيذ يعتمد على استمرار خدمة MusabAI؛ قد يؤخر أندرويد التشغيل بالخلفية."
      : "المهام محفوظة لكن المنفّذ التلقائي غير نشط في هذا التشغيل.";
    list.replaceChildren();
    if (!data.tasks.length) {
      const empty = document.createElement("p");
      empty.className = "s-task-empty";
      empty.textContent = "لا توجد مهام بعد. أنشئ أول مهمة من النموذج أو اطلبها في محادثة MusabAI.";
      list.appendChild(empty);
      return;
    }
    for (const task of data.tasks) {
      const el = document.createElement("article");
      el.className = "s-task-row";
      el.innerHTML = '<div class="s-task-heading"><strong>' + esc(task.title) +
        '</strong><span>' + esc(types[task.kind] || task.kind) + " · " +
        esc(status[task.status] || task.status) + '</span></div>' +
        '<p class="s-task-text">' + esc(task.prompt) + '</p>' +
        '<p class="s-task-meta">التشغيل التالي: ' +
        esc(task.status === "done" ? "اكتملت" : prettyTime(task.next_run)) +
        (task.repeat_seconds ? " · تتكرر كل " + Math.round(task.repeat_seconds / 60) + " دقيقة" : "") +
        (task.last_run ? "<span> · آخر تشغيل: " + esc(prettyTime(task.last_run)) + "</span>" : "") +
        '</p>' + (task.last_result ? '<p class="s-task-last">النتيجة: ' +
        esc(task.last_result.slice(0, 280)) + '</p>' : "") +
        '<div class="s-task-actions"></div>';
      const actions = $(".s-task-actions", el);
      const addButton = (text, command) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = text;
        btn.onclick = () => action(task.id, command);
        actions.appendChild(btn);
      };
      if (task.status === "active") addButton("إيقاف", "pause");
      if (task.status === "paused") addButton("تفعيل", "resume");
      if (task.status !== "running") addButton("تشغيل الآن", "run");
      if (task.status !== "running") addButton("حذف", "delete");
      list.appendChild(el);
    }
  }
  function init() {
    const nav = $("#open-cloud") || $("#open-settings");
    if (!nav || $("#open-scheduled-tasks")) return;
    const button = document.createElement("button");
    button.id = "open-scheduled-tasks";
    button.className = "side-item";
    button.innerHTML = '<span aria-hidden="true">⏰</span><span>المهام المجدولة</span>';
    button.title = "إنشاء تذكيرات ومهام متكررة";
    nav.after(button);
    panel = document.createElement("section");
    panel.id = "scheduled-panel";
    panel.dir = "rtl";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "المهام المجدولة");
    panel.innerHTML = '<div class="s-task-window">' +
      '<header class="s-task-header"><h2>المهام المجدولة</h2><button type="button" id="scheduled-close" aria-label="إغلاق">✕</button></header>' +
      '<div class="s-task-content">' +
      '<p id="scheduled-status"></p><p id="scheduled-error" role="alert" hidden></p>' +
      '<form id="scheduled-form">' +
      '<label>عنوان المهمة<input name="title" maxlength="140" required placeholder="مثلاً: تفقد بناء APK"></label>' +
      '<label>ماذا تريد؟<textarea name="prompt" maxlength="4000" rows="3" required placeholder="ذكّرني أو افحص شيئاً وأخبرني بالنتيجة"></textarea></label>' +
      '<div class="s-task-fields"><label>نوع المهمة<select name="kind">' +
      '<option value="reminder">تذكير فقط</option><option value="agent">فحص ذكي بالوكيل (قراءة فقط)</option>' +
      '<option value="watch">مراقبة شرط وإشعار عند تحققه</option></select></label>' +
      '<label>التكرار<select name="repeat">' +
      '<option value="0">مرة واحدة</option><option value="60">كل ساعة</option>' +
      '<option value="1440">كل يوم</option><option value="10080">كل أسبوع</option></select></label></div>' +
      '<label>أول تشغيل (بتوقيت هاتفك)<input type="datetime-local" name="when"></label>' +
      '<small>في المهام المتكررة، اترك الوقت فارغاً ليبدأ بعد أول فترة تكرار. الفحص الذكي يستخدم النموذج المتوفر وقت التنفيذ.</small>' +
      '<button type="submit" class="s-task-save">إنشاء المهمة</button></form>' +
      '<h3>مهامي</h3><div id="scheduled-list"></div></div></div>';
    document.body.appendChild(panel);
    list = $("#scheduled-list", panel);
    $("#scheduled-close", panel).onclick = close;
    panel.addEventListener("click", ev => { if (ev.target === panel) close(); });
    button.onclick = async () => {
      panel.hidden = false;
      $("#scheduled-error", panel).hidden = true;
      try { await refresh(); } catch (err) { showError(err); }
    };
    const form = $("#scheduled-form", panel);
    form.onsubmit = async event => {
      event.preventDefault();
      $("#scheduled-error", panel).hidden = true;
      const d = new FormData(form);
      const frequency = Number(d.get("repeat") || 0);
      const local = String(d.get("when") || "");
      if (!local && !frequency) return showError(new Error("حدد وقت التشغيل أو اختر تكراراً"));
      if (local && isNaN(new Date(local).getTime()))
        return showError(new Error("صيغة التاريخ غير صحيحة"));
      const requestBody = {
        title: String(d.get("title") || "").trim(),
        prompt: String(d.get("prompt") || "").trim(),
        kind: String(d.get("kind") || "reminder"),
        repeat_minutes: frequency,
        when: local ? new Date(local).toISOString() : "",
      };
      if (requestBody.kind === "watch" && !frequency)
        return showError(new Error("مراقبة الشرط تحتاج تكراراً كل ساعة أو أكثر"));
      const save = $(".s-task-save", form);
      save.disabled = true;
      try {
        await request("/api/scheduled-tasks", requestBody);
        form.reset();
        await refresh();
      } catch (err) { showError(err); }
      finally { save.disabled = false; }
    };
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
