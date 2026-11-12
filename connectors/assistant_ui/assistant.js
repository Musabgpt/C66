// MusabAI, the personal assistant's one page: a conversation, six everyday tasks, a short settings sheet.
// It talks to the engine's own API (sessions, events, approvals) and to the app (window.NewAlPhone) when it is there.
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var phone = window.NewAlPhone || null;
  var S = { state: null, me: null, lang: "ar", sid: "", busy: false, live: null, lastFinal: "" };

  // ------------------------------------------------------------------ words
  var WORDS = {
    ar: {
      placeholder: "اكتب طلبك أو تحدّث…",
      morning: "صباح الخير", evening: "مساء الخير", hello_sub: "ماذا أنجز لك اليوم؟",
      setup: "فعّل التحكم بالهاتف ليعمل المساعد داخل تطبيقاتك.", enable: "تفعيل",
      mail: "البريد", mail_sub: "اقرأ الجديد ولخّص المهم", mail_text: "اقرأ بريدي الجديد ولخّص لي المهم وما يحتاج رداً.",
      messages: "الرسائل", messages_sub: "ما وصلك مع ردود مقترحة",
      messages_text: "ما الرسائل الجديدة التي وصلتني؟ لخّصها لكل شخص واقترح رداً مناسباً.",
      travel: "السفر", travel_sub: "رحلات وفنادق وحجوزات", travel_text: "ابحث لي عن رحلة من … إلى … بتاريخ … واعرض أفضل ثلاثة خيارات.",
      post: "النشر", post_sub: "اكتب منشوراً وانشره", post_text: "انشر على فيسبوك منشوراً يقول: ",
      form: "النماذج", form_sub: "املأ نموذجاً من معلوماتك", form_text: "املأ هذا النموذج من معلوماتي: ",
      remind: "التذكير", remind_sub: "مواعيد وتنبيهات", remind_text: "ذكّرني ",
      thinking: "يفكّر…", done_in: "تم في {s} ث", steps: "{n} خطوات", one_step: "خطوة واحدة", stopped: "أُوقف", failed: "لم يكتمل",
      yes: "نعم", no: "لا", send: "إرسال", allow: "سماح", deny: "رفض",
      ap_reply: "أرسل هذا الرد؟", ap_email: "أرسل هذا البريد؟", ap_text: "أرسل أو أنشر هذا النص؟", ok: "موافق", ap_other: "هل تسمح بهذا؟", to: "إلى", subject: "الموضوع",
      history: "المحادثات", new_chat: "محادثة جديدة", no_history: "لا محادثات بعد.", untitled: "محادثة", remove: "حذف",
      settings: "الإعدادات", g_ready: "الجاهزية", g_me: "معلوماتي", g_accounts: "الحسابات", g_behaviour: "السلوك",
      on: "مفعّل", ready: "جاهزة", set_up: "إعداد", connect: "ربط", disconnect: "فصل", connected: "مربوط", unavailable: "غير متاح",
      a11y: "التحكم بالهاتف", a11y_sub: "ليفتح التطبيقات ويكتب وينقر عنك",
      notif: "قراءة الإشعارات", notif_sub: "ليقرأ الرسائل الواردة ويرد عليها",
      mic: "الميكروفون", contacts: "جهات الاتصال", location: "الموقع",
      engines: "محركات المهام الطويلة", engines_sub: "وكيل الهاتف والمتصفح الخلفي، تعمل عبر Termux",
      setup_all: "إعداد كل شيء دفعة واحدة",
      me_note: "تبقى على هاتفك، ويقرؤها المساعد فقط عند تعبئة نموذج أو حجز.",
      f_name: "الاسم", f_email: "البريد الإلكتروني", f_phone: "رقم الهاتف", f_city: "المدينة", f_country: "الدولة",
      f_address: "العنوان", f_birth_date: "تاريخ الميلاد", f_nationality: "الجنسية", f_passport: "رقم الجواز",
      gmail: "Gmail", calendar: "تقويم Google", approve_browser: "وافق في المتصفح ثم ارجع إلى التطبيق.",
      confirm: "اسألني قبل الإرسال والنشر", confirm_sub: "مطفأ: المساعد يرسل وينشر لوحده",
      bubble: "الزر العائم", bubble_sub: "المساعد فوق أي تطبيق",
      language: "اللغة", lang_auto: "لغة الهاتف", skills: "المهام التي أتقنها",
      learned: "تعلّمت", lessons: "ما تعلّمته", lessons_sub: "دروس يحفظها من عمله ومن تصحيحاتك", no_lessons: "لم يتعلّم شيئاً بعد. يحفظ درساً بعد كل مهمة تعثّرت، وحين تصحّحه.",
      saved: "حُفظ", offline: "تعذّر الاتصال بالمساعد. أعد فتح التطبيق.",
      termux_wait: "يجري الإعداد في Termux… تابع الخطوات هناك.", termux_paste: "أول مرة فقط: في Termux الصق الأمر واضغط Enter.",
      shared: "أضفته إلى رسالتك: قل ما تريد أن أفعل به.",
      d_open: "فتح {x}", d_page: "فتح صفحة", d_screen: "قراءة الشاشة", d_tap: "نقر", d_type: "كتابة", d_scroll: "تمرير",
      d_notifs: "قراءة الإشعارات", d_reply: "إرسال الرد", d_contacts: "البحث في جهات الاتصال", d_cal: "التقويم",
      d_sms: "تجهيز رسالة", d_call: "تجهيز اتصال", d_where: "تحديد الموقع", d_phone: "العمل على الهاتف",
      d_steps: "تنفيذ خطوات على الهاتف", d_routine: "تنفيذ مهمة أتقنها", d_agent: "العمل داخل التطبيقات", d_look: "النظر إلى الشاشة",
      d_site: "فتح الموقع", d_read: "قراءة الصفحة", d_fill: "تعبئة حقل", d_web_agent: "العمل في الموقع",
      d_search: "البحث في الويب", d_fetch: "قراءة صفحة", d_weather: "حالة الطقس", d_info: "مراجعة معلوماتك", d_save_info: "حفظ معلومة", d_learn: "حفظ درس",
      d_remind: "حفظ التذكير", d_reminders: "التذكيرات", d_mail: "قراءة البريد", d_send_mail: "إرسال البريد", d_draft: "حفظ مسودة"
    },
    en: {
      placeholder: "Type or say what you need…",
      morning: "Good morning", evening: "Good evening", hello_sub: "What can I get done for you today?",
      setup: "Turn on phone control so the assistant can work inside your apps.", enable: "Turn on",
      mail: "Email", mail_sub: "Read what is new, summarised", mail_text: "Read my new email and summarise what matters and what needs a reply.",
      messages: "Messages", messages_sub: "What came in, with suggested replies",
      messages_text: "What new messages did I get? Summarise them per person and suggest a reply for each.",
      travel: "Travel", travel_sub: "Flights, hotels, bookings", travel_text: "Find me a flight from … to … on … and show the three best options.",
      post: "Posts", post_sub: "Write a post and publish it", post_text: "Publish a post on Facebook that says: ",
      form: "Forms", form_sub: "Fill a form from your details", form_text: "Fill this form from my details: ",
      remind: "Reminders", remind_sub: "Appointments and alerts", remind_text: "Remind me ",
      thinking: "Thinking…", done_in: "Done in {s} s", steps: "{n} steps", one_step: "1 step", stopped: "Stopped", failed: "Not completed",
      yes: "Yes", no: "No", send: "Send", allow: "Allow", deny: "Don't",
      ap_reply: "Send this reply?", ap_email: "Send this email?", ap_text: "Send or publish this text?", ok: "OK", ap_other: "Allow this?", to: "To", subject: "Subject",
      history: "Conversations", new_chat: "New conversation", no_history: "No conversations yet.", untitled: "Conversation", remove: "Delete",
      settings: "Settings", g_ready: "Readiness", g_me: "My details", g_accounts: "Accounts", g_behaviour: "Behaviour",
      on: "On", ready: "Ready", set_up: "Set up", connect: "Connect", disconnect: "Disconnect", connected: "Connected", unavailable: "Unavailable",
      a11y: "Phone control", a11y_sub: "To open apps, type and tap for you",
      notif: "Notification access", notif_sub: "To read incoming messages and answer them",
      mic: "Microphone", contacts: "Contacts", location: "Location",
      engines: "Engines for long tasks", engines_sub: "The phone agent and the background browser, through Termux",
      setup_all: "Set everything up at once",
      me_note: "They stay on your phone; the assistant reads them only to fill a form or a booking.",
      f_name: "Name", f_email: "Email", f_phone: "Phone", f_city: "City", f_country: "Country",
      f_address: "Address", f_birth_date: "Date of birth", f_nationality: "Nationality", f_passport: "Passport number",
      gmail: "Gmail", calendar: "Google Calendar", approve_browser: "Approve in the browser, then come back.",
      confirm: "Ask me before sending or publishing", confirm_sub: "Off: the assistant sends and publishes on its own",
      bubble: "Floating button", bubble_sub: "The assistant over any app",
      language: "Language", lang_auto: "The phone's", skills: "Tasks I know by heart",
      learned: "Learned", lessons: "What I have learned", lessons_sub: "Lessons it keeps from its own work and your corrections", no_lessons: "Nothing learned yet. It keeps a lesson after a task that stumbled, and when you correct it.",
      saved: "Saved", offline: "The assistant is not answering. Open the app again.",
      termux_wait: "Setting up in Termux… follow the steps there.", termux_paste: "First time only: paste the command in Termux and press Enter.",
      shared: "Added to your message: say what to do with it.",
      d_open: "Opening {x}", d_page: "Opening a page", d_screen: "Reading the screen", d_tap: "Tapping", d_type: "Typing", d_scroll: "Scrolling",
      d_notifs: "Reading notifications", d_reply: "Sending the reply", d_contacts: "Looking up contacts", d_cal: "Calendar",
      d_sms: "Preparing a message", d_call: "Preparing a call", d_where: "Finding the location", d_phone: "Working on the phone",
      d_steps: "Running steps on the phone", d_routine: "Running a task I know", d_agent: "Working inside the apps", d_look: "Looking at the screen",
      d_site: "Opening the site", d_read: "Reading the page", d_fill: "Filling a field", d_web_agent: "Working on the site",
      d_search: "Searching the web", d_fetch: "Reading a page", d_weather: "Weather", d_info: "Checking your details", d_save_info: "Saving a detail", d_learn: "Keeping a lesson",
      d_remind: "Saving the reminder", d_reminders: "Reminders", d_mail: "Reading email", d_send_mail: "Sending the email", d_draft: "Saving a draft"
    }
  };
  function t(key, vars) {
    var text = (WORDS[S.lang] || WORDS.en)[key];
    if (text === undefined) text = WORDS.en[key] !== undefined ? WORDS.en[key] : key;
    Object.keys(vars || {}).forEach(function (k) { text = text.replace("{" + k + "}", vars[k]); });
    return text;
  }

  // ------------------------------------------------------------------ icons (24px strokes)
  var ICONS = {
    menu: '<path d="M4 7h16M4 12h16M4 17h10"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-2.7-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.1-2.7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 2.7-1.1V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1 .3z"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M4 7.5l8 6 8-6"/>',
    messages: '<path d="M20 12a8 8 0 0 1-11.6 7.1L4 20l1-4.2A8 8 0 1 1 20 12z"/>',
    travel: '<path d="M3 13.5l7.5-1.5L16 4.5c.8-1 2.6-.4 2.2 1L16.5 12l3 1.2c.9.4.9 1.6 0 2L4.5 21 3 19.5l5-3-5-3z"/>',
    post: '<path d="M4 10v4a1 1 0 0 0 1 1h2l5 4V5L7 9H5a1 1 0 0 0-1 1zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    form: '<rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    remind: '<path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16zM10 21h4"/>',
    trash: '<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/>'
  };
  function icon(name) { return '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS[name] + "</svg>"; }

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function md(text) { return window.renderMarkdown ? window.renderMarkdown(text) : el("div", "", text).innerHTML; }
  function api(path, body) {
    var opt = body === undefined ? { method: "GET" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || data.error) throw new Error(data.error || ("HTTP " + r.status));
        return data;
      });
    });
  }
  var toastTimer = 0;
  function toast(text, ms) {
    var box = $("#toast");
    box.textContent = text;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.hidden = true; }, ms || 3500);
  }
  function phoneStatus() {
    try { return phone && phone.status ? JSON.parse(phone.status()) : null; } catch (_) { return null; }
  }
  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key) || "";
      if (value === "") localStorage.removeItem(key); else localStorage.setItem(key, value);
    } catch (_) { /* private mode */ }
    return value;
  }
  function nearBottom() { var box = $("#thread"); return box.scrollHeight - box.scrollTop - box.clientHeight < 140; }
  function scrollDown(force) { var box = $("#thread"); if (force || nearBottom()) box.scrollTop = box.scrollHeight; }

  // ------------------------------------------------------------------ what a tool call is, in the user's words
  function doing(name, args) {
    args = args || {};
    if (name === "phone") {
      var a = String(args.action || "");
      if (a === "open_app") return t("d_open", { x: args.name || "" }).trim();
      if (a === "open_url") return t("d_page");
      if (a === "screen" || a === "screen_tree") return t("d_screen");
      if (a === "tap" || a === "key") return t("d_tap");
      if (a === "type") return t("d_type");
      if (a === "swipe" || a === "scroll" || a === "gesture") return t("d_scroll");
      if (a === "notifications_read") return t("d_notifs");
      if (a === "notification_reply") return t("d_reply");
      if (a === "contacts") return t("d_contacts");
      if (a === "calendar" || a === "calendar_add") return t("d_cal");
      if (a === "sms") return t("d_sms");
      if (a === "call") return t("d_call");
      if (a === "location") return t("d_where");
      return t("d_phone");
    }
    if (name === "browser") {
      var op = String(args.op || "");
      return op === "open" ? t("d_site") : op === "extract" ? t("d_read") : op === "type" ? t("d_fill") : op === "click" ? t("d_tap") : t("d_read");
    }
    var plain = {
      phone_sequence: "d_steps", phone_routine: "d_routine", phone_agent: "d_agent", screen_look: "d_look",
      browser_agent: "d_web_agent", web_search: "d_search", web_fetch: "d_fetch", weather: "d_weather",
      scheduled_task_create: "d_remind", gmail_send: "d_send_mail", gmail_create_draft: "d_draft"
    };
    if (plain[name]) return t(plain[name]) + (name === "web_search" && args.query ? ": " + String(args.query).slice(0, 60) : "");
    if (name === "my_info") return t(args.action === "set" ? "d_save_info" : "d_info");
    if (name === "learn") return t("d_learn");
    if (name.indexOf("scheduled_task_") === 0) return t("d_reminders");
    if (name.indexOf("gmail_") === 0) return t("d_mail");
    if (name.indexOf("calendar_") === 0) return t("d_cal");
    return name;
  }

  // ------------------------------------------------------------------ the conversation
  function showHome() { $("#home").hidden = $("#messages").children.length > 0; }
  function newTurn(text) {
    var box = el("div", "turn");
    if (text) { var mine = el("div", "me", text); mine.dir = "auto"; box.appendChild(mine); }
    $("#messages").appendChild(box);
    S.live = { box: box, text: null, raw: "", pending: false, steps: [], byId: {}, doing: null };
    showHome();
    return S.live;
  }
  function live() { return S.live || newTurn(""); }
  function setDoing(text) {
    var L = live();
    if (!text) { if (L.doing) { L.doing.remove(); L.doing = null; } return; }
    if (!L.doing) {
      L.doing = el("div", "doing");
      L.doing.appendChild(el("span", "spin"));
      L.doing.appendChild(el("span", "label"));
    }
    L.doing.lastChild.textContent = text;
    L.box.appendChild(L.doing);                 // always last in the turn
    scrollDown();
  }
  function setBusy(on) {
    S.busy = on;
    var b = $("#send");
    b.classList.toggle("stop", on);
    b.innerHTML = icon(on ? "stop" : "send");
    $("#dot").classList.toggle("busy", on);
    if (on) hideReplies();
  }
  function notice(text, error) {
    var n = el("div", "notice" + (error ? " error" : ""), text);
    n.dir = "auto";
    live().box.appendChild(n);
    if (live().doing) live().box.appendChild(live().doing);
    scrollDown();
  }
  function finishSteps(L, ev) {
    var n = L.steps.length;
    var parts = [ev.error === "interrupted" ? t("stopped") : ev.error ? t("failed")
      : t("done_in", { s: Math.max(1, Math.round(ev.seconds || 0)) })];
    if (n) parts.push(n === 1 ? t("one_step") : t("steps", { n: n }));
    var d = el("details", "steps");
    d.appendChild(el("summary", "", parts.join(" · ")));
    if (n) {
      var list = el("ol");
      L.steps.forEach(function (s) { var li = el("li", s.ok === false ? "bad" : "", s.label); li.dir = "auto"; list.appendChild(li); });
      d.appendChild(list);
    }
    L.box.appendChild(d);
  }

  function apply(ev, replay) {
    if (ev.sub) return;                         // a helper's inner steps are not the user's concern
    var L;
    switch (ev.type) {
      case "turn_start":
        // The message just sent is already on the screen (send() shows it at once): the turn continues in it.
        if (!replay && S.live && S.live.waiting) S.live.waiting = false;
        else newTurn(ev.text || "");
        S.lastFinal = "";
        if (!replay) { setBusy(true); setDoing(t("thinking")); scrollDown(true); }
        break;
      case "text_delta":
        L = live();
        if (!L.text) { L.text = el("div", "ai"); L.text.dir = "auto"; L.raw = ""; L.box.appendChild(L.text); }
        L.raw += ev.text || "";
        if (!L.pending) {
          L.pending = true;
          requestAnimationFrame(function () { if (L.text) L.text.innerHTML = md(L.raw); L.pending = false; });
        }
        if (L.doing) L.box.appendChild(L.doing);
        scrollDown();
        break;
      case "stream_reset":
        L = live();
        if (L.text) { L.text.remove(); L.text = null; L.raw = ""; }
        break;
      case "assistant":
        L = live();
        if (!String(ev.text || "").trim()) { if (L.text) { L.text.remove(); L.text = null; } break; }
        if (!L.text) { L.text = el("div", "ai"); L.text.dir = "auto"; L.box.appendChild(L.text); }
        L.text.innerHTML = md(ev.text);
        L.text.classList.toggle("note", !ev.final);
        if (ev.final) S.lastFinal = ev.text;
        L.text = null;
        L.raw = "";
        if (L.doing) L.box.appendChild(L.doing);
        scrollDown();
        break;
      case "tool_start":
        L = live();
        if (L.text) { L.text.classList.add("note"); L.text = null; L.raw = ""; }
        var step = { label: doing(ev.name || "", ev.args), ok: null };
        L.steps.push(step);
        L.byId[ev.id] = step;
        if (!replay) setDoing(step.label + "…");
        break;
      case "tool_end":
        L = live();
        if (L.byId[ev.id]) L.byId[ev.id].ok = !!ev.ok && !ev.denied;
        if (!replay && S.busy) setDoing(t("thinking"));
        break;
      case "approval":
        if (!replay) showApproval(ev);
        break;
      case "approval_result":
        var card = document.getElementById("ap-" + String(ev.id).replace(/[^\w-]/g, "_"));
        if (card) card.remove();
        break;
      case "notice":
        if (ev.text) notice(ev.text, false);
        break;
      case "learned":                           // its own review of the turn kept a lesson
        if (ev.text) {
          var kept = el("div", "steps", t("learned") + ": " + ev.text);
          kept.dir = "auto";
          live().box.appendChild(kept);
          scrollDown();
        }
        break;
      case "error":
        notice(ev.message || "error", true);
        live().failed = true;
        break;
      case "turn_end":
        L = live();
        setDoing("");
        if (L.text) L.text = null;
        if (ev.error && ev.error !== "interrupted" && !L.failed) notice(ev.answer || ev.error, true);
        finishSteps(L, ev);
        if (!replay) {
          setBusy(false);
          $("#approvals").innerHTML = "";
          if (/[?؟]\s*$/.test(S.lastFinal)) showReplies();
          if (document.hidden && phone && phone.notifyDone) {
            try { phone.notifyDone("MusabAI", String(ev.answer || S.lastFinal || "").slice(0, 300)); } catch (_) { /* older app */ }
          }
          scrollDown(true);
        }
        break;
      default:
        break;
    }
  }

  function onEvent(ev) {
    if (!ev || ev.session !== S.sid || !S.sid) return;
    apply(ev, false);
  }
  function connect() {
    var es = new EventSource("/api/events");
    var dropped = false;
    es.onmessage = function (e) {
      var ev;
      try { ev = JSON.parse(e.data); } catch (_) { return; }
      onEvent(ev);
    };
    es.onerror = function () { dropped = true; };
    es.onopen = function () { if (dropped) { dropped = false; if (S.sid) openSession(S.sid); } };
  }

  // ------------------------------------------------------------------ approvals and quick answers
  function showApproval(ev) {
    var id = "ap-" + String(ev.id).replace(/[^\w-]/g, "_");
    if (document.getElementById(id)) return;
    var a = ev.args || {};
    var q = t("ap_other"), what = "", go = t("allow");
    if (ev.tool === "phone" && a.action === "notification_reply") {
      q = t("ap_reply"); go = t("send");
      what = "«" + String(a.text || "") + "»" + (a.target ? "\n" + t("to") + ": " + a.target : "");
    } else if (ev.tool === "phone_routine") {
      var values = a.values instanceof Array ? a.values : a.values ? [a.values] : [];
      q = t("ap_text"); go = t("ok");
      what = values.map(function (v) { return "«" + String(v) + "»"; }).join("\n");
    } else if (ev.tool === "gmail_send") {
      q = t("ap_email"); go = t("send");
      what = t("to") + ": " + (a.to || "") + "\n" + t("subject") + ": " + (a.subject || "") + "\n\n" + (a.text || "");
    } else {
      what = doing(ev.tool || "", a) + "\n" + Object.keys(a).map(function (k) { return k + ": " + String(a[k]).slice(0, 200); }).join("\n");
    }
    var card = el("div", "approval");
    card.id = id;
    card.appendChild(el("div", "q", q));
    var w = el("div", "what", what.slice(0, 2000));
    w.dir = "auto";
    card.appendChild(w);
    var acts = el("div", "acts");
    [["once", go, "btn go"], ["deny", t("deny"), "btn no"]].forEach(function (spec) {
      var b = el("button", spec[2], spec[1]);
      b.type = "button";
      b.onclick = function () {
        card.remove();
        api("/api/approvals/" + encodeURIComponent(ev.id), { answer: spec[0] }).catch(function (e) { toast(e.message); });
      };
      acts.appendChild(b);
    });
    card.appendChild(acts);
    $("#approvals").appendChild(card);
  }
  function hideReplies() { $("#replies").hidden = true; $("#replies").innerHTML = ""; }
  function showReplies() {
    var box = $("#replies");
    box.innerHTML = "";
    [["yes", "yes"], ["no", ""]].forEach(function (spec) {
      var b = el("button", spec[1], t(spec[0]));
      b.type = "button";
      b.onclick = function () { send(t(spec[0])); };
      box.appendChild(b);
    });
    box.hidden = false;
  }

  // ------------------------------------------------------------------ sessions
  function clearThread() {
    $("#messages").innerHTML = "";
    $("#approvals").innerHTML = "";
    hideReplies();
    S.live = null;
    S.lastFinal = "";
    setBusy(false);
    showHome();
  }
  function newChat() {
    S.sid = "";
    store("ma.session", "");
    clearThread();
  }
  function openSession(id) {
    return api("/api/sessions/" + encodeURIComponent(id)).then(function (d) {
      S.sid = id;
      store("ma.session", id);
      clearThread();
      (d.events || []).forEach(function (ev) { apply(ev, true); });
      (d.approvals || []).forEach(showApproval);
      if (d.busy) { setBusy(true); setDoing(t("thinking")); }
      else if (S.live && S.live.doing) setDoing("");
      showHome();
      scrollDown(true);
    }).catch(function () { newChat(); });
  }
  var creating = null;
  function ensureSession() {
    if (S.sid) return Promise.resolve(S.sid);
    if (!creating) {
      creating = api("/api/sessions", { root: (S.state && S.state.home) || "", mode: "full-auto" }).then(function (d) {
        S.sid = d.id;
        store("ma.session", d.id);
        creating = null;
        return d.id;
      }, function (e) { creating = null; throw e; });
    }
    return creating;
  }

  function send(text) {
    text = String(text || "").trim();
    if (!text || S.busy) return;
    hideReplies();
    var input = $("#input");
    input.value = "";
    grow();
    // One-step requests (an alarm, a call, the torch): the phone does them itself, at once, with no model.
    if (phone && phone.quick) {
      try {
        var r = JSON.parse(phone.quick(text) || "{}");
        if (r.handled) {
          var L = newTurn(text);
          var reply = el("div", "ai", r.message || "✓");
          reply.dir = "auto";
          L.box.appendChild(reply);
          S.live = null;
          scrollDown(true);
          return;
        }
      } catch (_) { /* an older app: the assistant handles it */ }
    }
    var shown = newTurn(text);                  // at once: the engine's turn_start then continues in it
    shown.waiting = true;
    setBusy(true);
    setDoing(t("thinking"));
    scrollDown(true);
    ensureSession().then(function (sid) {
      return api("/api/sessions/" + encodeURIComponent(sid) + "/send", { text: text, lang: S.lang });
    }).catch(function (e) {
      setBusy(false);
      S.live = shown;
      setDoing("");
      notice(e.message || t("offline"), true);
      S.live = null;
    });
  }
  function stop() {
    if (S.sid) api("/api/sessions/" + encodeURIComponent(S.sid) + "/interrupt", {}).catch(function () {});
  }

  // ------------------------------------------------------------------ home
  var TASKS = [["mail", true], ["messages", true], ["travel", false], ["post", false], ["form", false], ["remind", false]];
  function greet() {
    var name = S.me && S.me.profile && S.me.profile.name ? S.me.profile.name.split(" ")[0] : "";
    var hello = t(new Date().getHours() < 12 ? "morning" : "evening");
    $("#hello").textContent = name ? hello + (S.lang === "ar" ? " يا " : ", ") + name : hello;
    $("#hello-sub").textContent = t("hello_sub");
  }
  function renderHome() {
    greet();
    var box = $("#tasks");
    box.innerHTML = "";
    TASKS.forEach(function (task) {
      var b = el("button", "task");
      b.type = "button";
      var g = el("span", "glyph");
      g.innerHTML = icon(task[0]);
      b.appendChild(g);
      b.appendChild(el("b", "", t(task[0])));
      b.appendChild(el("small", "", t(task[0] + "_sub")));
      b.onclick = function () {
        if (task[1]) return send(t(task[0] + "_text"));
        var input = $("#input");
        input.value = t(task[0] + "_text");
        grow();
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      };
      box.appendChild(b);
    });
    renderSetup();
  }
  function renderSetup() {
    var st = phoneStatus();
    var need = !!(st && !st.accessibility);
    var box = $("#setup");
    box.hidden = !need;
    $("#dot").classList.toggle("setup", need);
    if (!need) return;
    box.innerHTML = "";
    box.appendChild(el("span", "", t("setup")));
    var b = el("button", "", t("enable"));
    b.type = "button";
    b.onclick = function () { phone.openAccessibilitySettings(); };
    box.appendChild(b);
  }

  // ------------------------------------------------------------------ sheets
  function openSheet(title) {
    $("#sheet-title").textContent = title;
    var body = $("#sheet-body");
    body.innerHTML = "";
    $("#sheet").hidden = false;
    return body;
  }
  function closeSheet() { $("#sheet").hidden = true; renderHome(); }

  function ago(seconds) {
    var s = Math.max(0, Date.now() / 1000 - (seconds || 0));
    var ar = S.lang === "ar";
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + (ar ? " د" : " min");
    if (s < 86400) return Math.round(s / 3600) + (ar ? " س" : " h");
    return Math.round(s / 86400) + (ar ? " يوم" : " d");
  }
  function openHistory() {
    var body = openSheet(t("history"));
    var fresh = el("button", "btn go wide", t("new_chat"));
    fresh.type = "button";
    fresh.onclick = function () { closeSheet(); newChat(); };
    api("/api/sessions").then(function (list) {
      if (!list.length) body.appendChild(el("div", "empty-note", t("no_history")));
      else {
        var card = el("div", "card");
        list.slice(0, 60).forEach(function (s) {
          var row = el("div", "history-item" + (s.id === S.sid ? " current" : ""));
          var open = el("button", "open");
          open.type = "button";
          var title = el("b", "", s.title || t("untitled"));
          title.dir = "auto";
          open.appendChild(title);
          open.appendChild(el("small", "", ago(s.updated)));
          open.onclick = function () { closeSheet(); openSession(s.id); };
          var del = el("button", "icon");
          del.type = "button";
          del.setAttribute("aria-label", t("remove"));
          del.innerHTML = icon("trash");
          del.onclick = function () {
            api("/api/sessions/delete", { id: s.id }).then(function () {
              row.remove();
              if (s.id === S.sid) newChat();
            }).catch(function (e) { toast(e.message); });
          };
          row.appendChild(open);
          row.appendChild(del);
          card.appendChild(row);
        });
        body.appendChild(card);
      }
      body.appendChild(fresh);
    }).catch(function (e) { body.appendChild(el("div", "empty-note", e.message)); body.appendChild(fresh); });
  }

  function row(title, sub) {
    var r = el("div", "row");
    var g = el("div", "grow");
    g.appendChild(el("b", "", title));
    if (sub) g.appendChild(el("small", "", sub));
    r.appendChild(g);
    return r;
  }
  function stateOrButton(r, on, onText, label, act) {
    if (on) { r.appendChild(el("span", "state on", onText)); return; }
    var b = el("button", "btn small go", label);
    b.type = "button";
    b.onclick = act;
    r.appendChild(b);
  }
  function toggle(r, checked, change) {
    var w = el("label", "switch");
    var box = el("input");
    box.type = "checkbox";
    box.checked = !!checked;
    box.onchange = function () { change(box.checked, box); };
    w.appendChild(box);
    w.appendChild(el("span"));
    r.appendChild(w);
  }

  function linkTermux() {
    var st = (phoneStatus() || {}).termux || {};
    if (!st.installed) { phone.termuxSetup(""); return; }
    var allowed = st.allowed ? Promise.resolve() : new Promise(function (done) {
      if (phone.termuxAllow) phone.termuxAllow();
      var tries = 0;
      var timer = setInterval(function () {
        if (((phoneStatus() || {}).termux || {}).allowed || ++tries > 20) { clearInterval(timer); done(); }
      }, 1000);
    });
    allowed.then(function () { return api("/api/termux/link", {}); }).then(function (d) {
      var ok = ((phoneStatus() || {}).termux || {}).allowed && phone.termuxRunVisible && phone.termuxRunVisible(d.command) === "started";
      if (ok) { toast(t("termux_wait"), 7000); return; }
      return api("/api/termux/link", {}).then(function (again) {       // the token is single use
        phone.termuxSetup(again.command);
        toast(t("termux_paste"), 9000);
      });
    }).catch(function (e) { toast(e.message); });
  }

  function openLessons() {
    var body = openSheet(t("lessons"));
    api("/api/assistant/lessons").then(function (d) {
      var list = d.lessons || [];
      if (!list.length) { body.appendChild(el("div", "empty-note", t("no_lessons"))); return; }
      var card = el("div", "card");
      list.forEach(function (item) {
        var r = el("div", "history-item");
        var text = el("div", "open");
        var line = el("b", "", item.lesson);
        line.dir = "auto";
        line.style.whiteSpace = "normal";
        text.appendChild(line);
        if (item.about) text.appendChild(el("small", "", item.about));
        var del = el("button", "icon");
        del.type = "button";
        del.setAttribute("aria-label", t("remove"));
        del.innerHTML = icon("trash");
        del.onclick = function () {
          api("/api/assistant/lessons/forget", { id: item.id }).then(function () { r.remove(); })
            .catch(function (e) { toast(e.message); });
        };
        r.appendChild(text);
        r.appendChild(del);
        card.appendChild(r);
      });
      body.appendChild(card);
    }).catch(function (e) { body.appendChild(el("div", "empty-note", e.message)); });
  }

  function openNotificationAccess() {
    api("/api/assistant/open", { page: "notification_access" }).catch(function (e) { toast(e.message); });
  }

  function openSettings() {
    var body = openSheet(t("settings"));
    var st = phoneStatus();

    // --- readiness: only on the phone
    if (st) {
      body.appendChild(el("div", "group", t("g_ready")));
      var ready = el("div", "card");
      var r = row(t("a11y"), t("a11y_sub"));
      stateOrButton(r, st.accessibility, t("on"), t("enable"), function () { phone.openAccessibilitySettings(); });
      ready.appendChild(r);
      var notif = row(t("notif"), t("notif_sub"));
      if (st.notification_access === undefined) notif.id = "row-notif";      // an older app: the engine tells
      else stateOrButton(notif, st.notification_access, t("on"), t("enable"), openNotificationAccess);
      ready.appendChild(notif);
      r = row(t("mic"));
      stateOrButton(r, st.microphone, t("on"), t("enable"), function () { phone.listen(S.lang === "ar" ? "ar" : ""); });
      ready.appendChild(r);
      r = row(t("contacts"));
      stateOrButton(r, st.contacts, t("on"), t("enable"), function () { phone.allowContacts(); });
      ready.appendChild(r);
      r = row(t("location"));
      stateOrButton(r, st.location, t("on"), t("enable"), function () { phone.allowLocation(); });
      ready.appendChild(r);
      r = row(t("engines"), t("engines_sub"));
      stateOrButton(r, st.termux && st.termux.up, t("ready"), t("set_up"), linkTermux);
      ready.appendChild(r);
      body.appendChild(ready);
      if (phone.setupAll) {
        var all = el("button", "btn wide", t("setup_all"));
        all.type = "button";
        all.onclick = function () { closeSheet(); phone.setupAll(); };
        body.appendChild(all);
      }
    }

    // --- my details
    body.appendChild(el("div", "group", t("g_me")));
    var mine = el("div", "card");
    body.appendChild(mine);
    var note = el("div", "about", t("me_note"));
    body.appendChild(note);

    // --- accounts
    var accountsTitle = el("div", "group", t("g_accounts"));
    var accounts = el("div", "card");
    accounts.id = "accounts";
    body.appendChild(accountsTitle);
    body.appendChild(accounts);

    // --- behaviour
    body.appendChild(el("div", "group", t("g_behaviour")));
    var how = el("div", "card");
    var confirmRow = row(t("confirm"), t("confirm_sub"));
    how.appendChild(confirmRow);
    if (phone && phone.floatingAssistant) {
      var bubble = row(t("bubble"), t("bubble_sub"));
      toggle(bubble, store("ma.bubble") === "1", function (on) {
        store("ma.bubble", on ? "1" : "");
        phone.floatingAssistant(on, on);
      });
      how.appendChild(bubble);
    }
    var langRow = row(t("language"));
    var pick = el("select");
    [["", t("lang_auto")], ["ar", "العربية"], ["en", "English"]].forEach(function (o) {
      var opt = el("option", "", o[1]);
      opt.value = o[0];
      pick.appendChild(opt);
    });
    pick.value = (S.state && S.state.settings && S.state.settings.lang) || "";
    pick.onchange = function () { api("/api/settings", { lang: pick.value }).then(function () { location.reload(); }); };
    langRow.appendChild(pick);
    how.appendChild(langRow);
    var learnt = row(t("lessons"), t("lessons_sub"));
    var more = el("button", "btn small", "›");
    more.type = "button";
    more.onclick = openLessons;
    learnt.appendChild(more);
    how.appendChild(learnt);
    if (phone && phone.openTaskMemory) {
      var skills = row(t("skills"));
      var go = el("button", "btn small", "›");
      go.type = "button";
      go.onclick = function () { phone.openTaskMemory(); };
      skills.appendChild(go);
      how.appendChild(skills);
    }
    body.appendChild(how);
    body.appendChild(el("div", "about", "MusabAI " + ((S.state && S.state.version) || "")));

    api("/api/assistant").then(function (d) {
      S.me = d;
      (d.fields || []).forEach(function (f) {
        var r2 = el("div", "row");
        var lab = el("label", "", t("f_" + f));
        var input = el("input");
        input.type = f === "email" ? "email" : f === "phone" ? "tel" : "text";
        input.dir = "auto";
        input.value = (d.profile || {})[f] || "";
        input.id = "f-" + f;
        lab.htmlFor = input.id;
        input.onchange = function () {
          var change = {};
          change[f] = input.value;
          api("/api/assistant", { profile: change }).then(function (now) { S.me = now; toast(t("saved"), 1200); })
            .catch(function (e) { toast(e.message); });
        };
        r2.appendChild(lab);
        r2.appendChild(input);
        mine.appendChild(r2);
      });
      toggle(confirmRow, d.confirm, function (on, box) {
        api("/api/assistant", { confirm: on }).then(function (now) { S.me = now; })
          .catch(function (e) { box.checked = !on; toast(e.message); });
      });
      var nrow = $("#row-notif");
      if (nrow) stateOrButton(nrow, !!(d.node || {}).notifications, t("on"), t("enable"), openNotificationAccess);
    }).catch(function (e) { mine.appendChild(el("div", "empty-note", e.message)); });

    api("/api/connectors").then(function (d) {
      var shown = 0;
      (d.connectors || []).forEach(function (c) {
        if (c.id !== "gmail" && c.id !== "calendar") return;
        shown++;
        var r3 = row(t(c.id));
        if (c.status === "unavailable") { r3.appendChild(el("span", "state", t("unavailable"))); accounts.appendChild(r3); return; }
        var linked = c.status === "connected";
        if (linked) r3.appendChild(el("span", "state on", t("connected")));
        var b = el("button", "btn small" + (linked ? "" : " go"), t(linked ? "disconnect" : "connect"));
        b.type = "button";
        b.onclick = function () {
          b.disabled = true;
          api("/api/connectors/" + (linked ? "disconnect" : "connect"), { provider: c.id }).then(function (res) {
            if (res.status === "authorizing") toast(t("approve_browser"), 6000);
            closeSheet();
          }).catch(function (e) { b.disabled = false; toast(e.message); });
        };
        r3.appendChild(b);
        accounts.appendChild(r3);
      });
      if (!shown) { accountsTitle.remove(); accounts.remove(); }
    }).catch(function () { accountsTitle.remove(); accounts.remove(); });
  }

  // ------------------------------------------------------------------ composer
  function grow() {
    var input = $("#input");
    input.style.height = "auto";
    input.style.height = Math.min(150, input.scrollHeight) + "px";
  }
  function wire() {
    $("#open-history").innerHTML = icon("menu");
    $("#new-chat").innerHTML = icon("plus");
    $("#open-settings").innerHTML = icon("gear");
    $("#sheet-close").innerHTML = icon("close");
    $("#mic").innerHTML = icon("mic");
    $("#send").innerHTML = icon("send");
    $("#input").placeholder = t("placeholder");
    $("#mic").hidden = !(phone && phone.listen);

    $("#open-history").onclick = openHistory;
    $("#new-chat").onclick = newChat;
    $("#open-settings").onclick = openSettings;
    $("#sheet-close").onclick = closeSheet;
    $("#sheet").onclick = function (e) { if (e.target === $("#sheet")) closeSheet(); };
    $("#mic").onclick = function () { phone.listen(S.lang === "ar" ? "ar" : ""); };
    $("#input").addEventListener("input", function () { grow(); if ($("#input").value) hideReplies(); });
    $("#input").addEventListener("keydown", function (e) {
      var touch = "ontouchstart" in window;
      if (e.key === "Enter" && !e.shiftKey && !touch) { e.preventDefault(); send($("#input").value); }
    });
    $("#composer").addEventListener("submit", function (e) {
      e.preventDefault();
      if (S.busy) stop(); else send($("#input").value);
    });
    // A link in an answer opens in the phone's browser, never inside this page.
    $("#messages").addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest("a[href]") : null;
      if (!a) return;
      if (phone && phone.openUrl) { e.preventDefault(); phone.openUrl(a.href); }
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) renderSetup(); });
  }

  // ------------------------------------------------------------------ the app speaks to the page
  window.onPhoneVoice = function (text) {
    var input = $("#input");
    if (input.value.trim()) { input.value = input.value.replace(/\s*$/, " ") + text; grow(); input.focus(); }
    else send(text);
  };
  function takeShared() {
    var d = null;
    try { d = phone && phone.takeShared ? JSON.parse(phone.takeShared() || "null") : null; } catch (_) { d = null; }
    if (!d || (!d.text && !(d.files || []).length)) return;
    var parts = [];
    if (d.text) parts.push(d.text);
    if ((d.files || []).length) parts.push(d.files.join(", "));
    var input = $("#input");
    input.value = parts.join("\n\n") + "\n\n";
    grow();
    input.focus();
    toast(t("shared"), 4500);
  }
  function takeAction() {
    var a = "";
    try { a = phone && phone.takeAction ? phone.takeAction() : ""; } catch (_) { a = ""; }
    if (!a) return;
    newChat();
    if (a === "voice" && phone.listen) setTimeout(function () { phone.listen(S.lang === "ar" ? "ar" : ""); }, 400);
  }
  window.onPhoneShared = function () { takeShared(); takeAction(); };
  window.onPhoneAccess = function () { renderSetup(); };
  window.openMusabConnectors = function () { openSettings(); };

  function paintBars() {
    try {
      if (!(phone && phone.theme)) return;
      var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      phone.theme(getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || (dark ? "#101214" : "#f6f6f3"), dark);
    } catch (_) { /* older app */ }
  }
  function startTermux() {
    // Once linked, the engines' host starts with the app.
    var st = (phoneStatus() || {}).termux;
    if (!st) return;
    if (st.up) { store("ma.termux", "1"); return; }
    if (st.installed && st.allowed && store("ma.termux") && phone.termuxStart) { try { phone.termuxStart(); } catch (_) { /* later */ } }
  }

  // ------------------------------------------------------------------ start
  function language(setting) {
    var lang = setting || (/^ar/i.test(navigator.language || "") ? "ar" : "en");
    S.lang = WORDS[lang] ? lang : "en";
    document.documentElement.lang = S.lang;
    document.documentElement.dir = S.lang === "ar" ? "rtl" : "ltr";
  }
  function start() {
    // The screen is there at once, in the phone's language; the engine's answer then fills in the rest.
    language(store("ma.lang"));
    wire();
    renderHome();
    paintBars();
    api("/api/state").then(function (state) {
      S.state = state;
      var before = S.lang;
      var chosen = (state.settings && state.settings.lang) || "";
      store("ma.lang", chosen);
      language(chosen);
      if (S.lang !== before) { $("#input").placeholder = t("placeholder"); renderHome(); }
      connect();
      startTermux();
      api("/api/assistant").then(function (d) { S.me = d; greet(); }).catch(function () {});
      var last = store("ma.session");
      var opened = last ? openSession(last) : Promise.resolve();
      opened.then(function () {
        takeShared();
        takeAction();
        if (/[?&]connections=1/.test(location.search)) openSettings();
      });
    }).catch(function () {
      $("#hello-sub").textContent = t("offline");
    });
  }
  window.MusabAssistant = { doing: doing, words: WORDS };
  start();
})();
