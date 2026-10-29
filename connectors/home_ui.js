// MusabAI on the phone: a personal-assistant home in place of the empty chat, and instant answers for one-step
// requests (an alarm, a timer, a reminder, a call, a message, directions, a search, the torch...). Those are done by
// the phone itself through NewAlPhone.quick, in milliseconds and with no model; anything else goes to the agent as
// before. On a computer (no NewAlPhone) nothing here runs.
(function () {
  'use strict';
  const phone = window.NewAlPhone;
  if (!phone || typeof phone.quick !== 'function') return;

  const ar = () => (document.documentElement.lang || '').startsWith('ar') ||
    (window.NCi18n ? NCi18n.wanted(localStorage.getItem('nc.pref.lang') || '') === 'ar' : /^ar\b/i.test(navigator.language || ''));
  const tr = (en, arabic) => ar() ? arabic : en;
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const RECENT_KEY = 'mh.recent';

  // ------------------------------------------------------------------ the fast path
  /** Only short requests in plain words: code, paths and links always go to the agent. */
  function quickable(text) {
    const t = (text || '').trim();
    if (!t || t.length > 140 || t.startsWith('/') || t.startsWith('@')) return false;
    if (t.split(/\s+/).length > 16) return false;
    if (/[`{}<>=\\]|https?:|\w\.\w{1,4}\b|\n/.test(t)) return false;
    return true;
  }

  function tryQuick(text) {
    if (!quickable(text)) return null;
    try {
      const r = JSON.parse(phone.quick(text.trim()) || '{}');
      return r && r.handled ? r : null;
    } catch (_e) {
      return null;
    }
  }

  function remember(text, message) {
    try {
      const list = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]').filter(x => x.text !== text);
      list.unshift({ text, message, at: Date.now() });
      localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 6)));
    } catch (_e) {}
    renderRecent();
  }

  let toastTimer = 0;
  function showResult(message) {
    let box = document.getElementById('mh-toast');
    if (!box) {
      box = el('div', 'mh-toast');
      box.id = 'mh-toast';
      box.setAttribute('role', 'status');
      box.onclick = () => box.classList.remove('show');
      document.body.append(box);
    }
    box.textContent = message;
    box.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove('show'), 4200);
  }

  /** The person's words, done on the phone when they can be; true when they were. */
  function handle(text) {
    const r = tryQuick(text);
    if (!r) return false;
    showResult(r.message || tr('Done.', 'تم.'));
    remember(text.trim(), r.message || '');
    const input = document.getElementById('input');
    if (input) {
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    return true;
  }

  function busy() {
    const send = document.getElementById('send');
    return send && send.classList.contains('stop');
  }

  // Before app.js sees Enter or the send button: a request the phone can do itself never waits for a model.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing || e.target.id !== 'input') return;
    const popup = document.getElementById('popup');
    if (popup && !popup.hidden) return;
    if (busy()) return;
    if (handle(e.target.value)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('submit', e => {
    if (!e.target || e.target.id !== 'composer' || busy()) return;
    const input = document.getElementById('input');
    if (input && handle(input.value)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  // A spoken request from the home's microphone runs at once (the composer's own microphone only fills the box).
  let speakToRun = false;
  const voiceBefore = window.onPhoneVoice;
  window.onPhoneVoice = text => {
    if (speakToRun) {
      speakToRun = false;
      document.querySelector('.mh-mic')?.classList.remove('listening');
      if (text && handle(text)) return;
      if (typeof voiceBefore === 'function') voiceBefore(text);
      if (text) setTimeout(() => document.getElementById('send')?.click(), 60);
      return;
    }
    if (typeof voiceBefore === 'function') voiceBefore(text);
  };

  // ------------------------------------------------------------------ the home
  function greeting(now) {
    const h = now.getHours();
    if (h < 5) return tr('Good night', 'تصبح على خير');
    if (h < 12) return tr('Good morning', 'صباح الخير');
    if (h < 18) return tr('Good afternoon', 'مساء النور');
    return tr('Good evening', 'مساء الخير');
  }

  function dateLine(now) {
    try {
      const loc = ar() ? 'ar' : undefined;
      const day = now.toLocaleDateString(loc, { weekday: 'long', day: 'numeric', month: 'long' });
      const time = now.toLocaleTimeString(loc, { hour: 'numeric', minute: '2-digit' });
      return day + ' · ' + time;
    } catch (_e) {
      return now.toDateString();
    }
  }

  // [icon, English, Arabic, what it does]: run = done by the phone now, fill = the start of a request to finish,
  // send = a question asked to the agent at once, call = something of the app's own
  const GROUPS = [
    ['Organise', 'التنظيم', [
      ['⏰', 'Alarm', 'منبه', { fill: ['Wake me up at ', 'صحيني الساعة '] }],
      ['⏳', 'Timer', 'مؤقت', { fill: ['Timer ', 'مؤقت '] }],
      ['🔔', 'Remind me', 'ذكرني', { fill: ['Remind me ', 'ذكرني '] }],
      ['🕒', 'Time', 'الوقت', { run: ['What time is it', 'كم الساعة'] }],
    ]],
    ['People', 'التواصل', [
      ['📞', 'Call', 'اتصال', { fill: ['Call ', 'اتصل ب'] }],
      ['💬', 'Message', 'رسالة', { fill: ['Send a message to ', 'ابعث رسالة ل'] }],
      ['🟢', 'WhatsApp', 'واتساب', { run: ['Open WhatsApp', 'افتح واتساب'] }],
      ['🗺️', 'Directions', 'الطريق', { fill: ['Take me to ', 'خذني على '] }],
    ]],
    ['Phone', 'الهاتف', [
      ['🔦', 'Torch', 'الفلاش', { run: ['Turn on the torch', 'شغل الفلاش'] }],
      ['🔋', 'Battery', 'البطارية', { run: ['Battery', 'كم البطارية'] }],
      ['📸', 'Screenshot', 'لقطة شاشة', { run: ['Screenshot', 'خذ لقطة شاشة'] }],
      ['🔇', 'Silent', 'صامت', { run: ['Mute', 'اكتم الصوت'] }],
    ]],
    ['Ask', 'اسأل', [
      ['🌤️', 'Weather', 'الطقس', { send: ['What is the weather today where I am?', 'شو الطقس اليوم عندي؟'] }],
      ['📰', 'News', 'الأخبار', { send: ['What are the main news headlines today?', 'شو أهم الأخبار اليوم؟'] }],
      ['🗓️', 'Plan my day', 'رتّب يومي', { fill: ['Plan my day: ', 'رتبلي يومي: '] }],
      ['🌐', 'Translate', 'ترجمة', { fill: ['Translate to English: ', 'ترجم للعربي: '] }],
    ]],
    ['Discover', 'اكتشف', [
      ['🔎', 'Search', 'بحث', { fill: ['Search for ', 'ابحث عن '] }],
      // the caret goes between the verb and "on YouTube", where the song or video is said
      ['▶️', 'YouTube', 'يوتيوب', { fill: ['Play  on YouTube', 'شغل  على يوتيوب'], back: [11, 11] }],
      ['🫧', 'Bubble', 'الفقاعة', { call: () => phone.floatingAssistant && phone.floatingAssistant(true, true) }],
      ['✨', 'Ask anything', 'اسأل أي شي', { fill: ['', ''] }],
    ]],
  ];

  function fillInput(text, caretFromEnd) {
    const input = document.getElementById('input');
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    const at = Math.max(0, text.length - (caretFromEnd || 0));
    try { input.setSelectionRange(at, at); } catch (_e) {}
  }

  function tile(icon, en, arabic, act) {
    const b = el('button', 'mh-tile');
    b.type = 'button';
    b.append(el('span', 'mh-tile-icon', icon), el('span', 'mh-tile-label', tr(en, arabic)));
    b.onclick = () => {
      if (act.run) {
        // the phone's own commands are matched in Arabic, so the Arabic words run in either language
        if (!handle(act.run[1])) fillInput(tr(act.run[0], act.run[1]));
      } else if (act.fill) {
        fillInput(tr(act.fill[0], act.fill[1]), act.back ? tr(act.back[0], act.back[1]) : 0);
      } else if (act.send) {
        // a question for the agent (live facts, thinking): asked at once
        fillInput(tr(act.send[0], act.send[1]));
        if (!busy()) setTimeout(() => document.getElementById('send')?.click(), 30);
      } else if (act.call) {
        act.call();
      }
    };
    return b;
  }

  function setupNotes(card) {
    card.replaceChildren();
    let st = {};
    try { st = JSON.parse(phone.status() || '{}'); } catch (_e) {}
    const missing = [];
    if (!st.accessibility) missing.push([tr('Let MusabAI control the screen', 'اسمح لـ MusabAI بالتحكم بالشاشة'),
      () => phone.openAccessibilitySettings && phone.openAccessibilitySettings()]);
    if (st.contacts === false) missing.push([tr('Call and message your contacts by name', 'اتصل وراسل جهات الاتصال بالاسم'),
      () => phone.allowContacts && phone.allowContacts()]);
    if (st.microphone === false) missing.push([tr('Turn on the microphone', 'فعّل الميكروفون'),
      () => phone.setupAll && phone.setupAll()]);
    card.hidden = !missing.length;
    for (const [text, run] of missing) {
      const b = el('button', 'mh-setup-item', text);
      b.type = 'button';
      b.onclick = () => { run(); setTimeout(() => setupNotes(card), 1500); };
      card.append(b);
    }
  }

  function renderRecent() {
    const box = document.getElementById('mh-recent');
    if (!box) return;
    let list = [];
    try { list = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch (_e) {}
    box.replaceChildren();
    box.hidden = !list.length;
    if (!list.length) return;
    box.append(el('h3', 'mh-section-title', tr('Again', 'مرة ثانية')));
    const row = el('div', 'mh-chips');
    for (const item of list) {
      const c = el('button', 'mh-chip', item.text);
      c.type = 'button';
      c.title = item.message || '';
      c.onclick = () => { if (!handle(item.text)) fillInput(item.text); };
      row.append(c);
    }
    box.append(row);
  }

  /**
   * The chat jumps to its end when a thread loads (app.js scrollDown); with the home showing that hides the greeting
   * and the microphone. A scroll the person did not make (no touch, wheel or key in the last moments) goes back to
   * the top while the home is shown, and a new chat starts from the top.
   */
  function keepHomeAtTop() {
    const thread = document.getElementById('thread');
    const empty = document.getElementById('empty');
    if (!thread || !empty) return;
    let touched = 0;
    const mark = () => { touched = Date.now(); };
    for (const ev of ['touchstart', 'touchmove', 'wheel', 'pointerdown', 'keydown']) {
      thread.addEventListener(ev, mark, { passive: true });
    }
    const homeShown = () => {
      const messages = document.getElementById('messages');
      return !empty.hidden && (!messages || !messages.children.length);
    };
    thread.addEventListener('scroll', () => {
      // 2.5 s: a fling keeps scrolling for a while after the finger leaves the screen
      if (thread.scrollTop > 0 && homeShown() && Date.now() - touched > 2500) thread.scrollTop = 0;
    }, { passive: true });
    new MutationObserver(() => { if (homeShown()) thread.scrollTop = 0; })
      .observe(empty, { attributes: true, attributeFilter: ['hidden'] });
    thread.scrollTop = 0;
  }

  function build() {
    const empty = document.getElementById('empty');
    if (!empty || document.getElementById('mh-home')) return;
    document.documentElement.classList.add('mh-phone');
    const home = el('div', 'mh-home');
    home.id = 'mh-home';

    const hero = el('header', 'mh-hero');
    const greet = el('div', 'mh-greet');
    const date = el('div', 'mh-date');
    const tick = () => { const now = new Date(); greet.textContent = greeting(now); date.textContent = dateLine(now); };
    tick();
    setInterval(tick, 30000);
    hero.append(greet, date);

    const mic = el('button', 'mh-mic');
    mic.type = 'button';
    mic.setAttribute('aria-label', tr('Speak', 'احكي'));
    mic.append(el('span', 'mh-mic-ring'), el('span', 'mh-mic-icon', '🎙'));
    mic.onclick = () => {
      speakToRun = true;
      mic.classList.add('listening');
      setTimeout(() => mic.classList.remove('listening'), 9000);
      phone.listen('');
    };
    const hint = el('p', 'mh-hint', tr('Try: “Wake me up at 7” · “Remind me in 10 minutes” · “Take me home”',
      'جرّب: «صحيني الساعة 7» · «ذكرني بعد 10 دقايق» · «خذني على البيت»'));

    const setup = el('div', 'mh-setup');
    setupNotes(setup);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) setupNotes(setup); });

    const grid = el('div', 'mh-groups');
    for (const [en, arabic, tiles] of GROUPS) {
      const sec = el('section', 'mh-group');
      sec.append(el('h3', 'mh-section-title', tr(en, arabic)));
      const row = el('div', 'mh-tiles');
      for (const [icon, ten, tar, act] of tiles) row.append(tile(icon, ten, tar, act));
      sec.append(row);
      grid.append(sec);
    }
    const recent = el('section', 'mh-recent');
    recent.id = 'mh-recent';

    home.append(hero, mic, hint, setup, recent, grid);
    empty.prepend(home);
    renderRecent();
    keepHomeAtTop();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
