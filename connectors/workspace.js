(() => {
  'use strict';
  const ar = () => document.documentElement.lang.startsWith('ar');
  const tr = (en, arabic) => ar() ? arabic : en;
  const node = (tag, text = '', cls = '') => { const n = document.createElement(tag); n.textContent = text; n.className = cls; return n; };
  const button = (en, arabic, action, primary = false) => { const b = node('button', tr(en, arabic), 'btn' + (primary ? ' primary' : '')); b.type = 'button'; b.onclick = action; return b; };
  const session = () => window.NewAlWorkspaceSession?.() || '';
  const plain = value => typeof value === 'string' ? value : JSON.stringify(value ?? '');
  let nextId = 0;
  async function api(path, body) {
    const r = await fetch(path, {method: body ? 'POST' : 'GET', credentials: 'same-origin', headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
    const data = await r.json(); if (!r.ok || data.error || data.ok === false) throw new Error(data.error || tr('Request failed', 'فشل الطلب')); return data;
  }
  function status(message, text, error = false) {
    message.setAttribute('role', error ? 'alert' : 'status'); message.textContent = text;
  }
  function dialog(title) {
    const previous = document.activeElement, outer = node('div', '', 'modal'), card = node('div', '', 'modal-card connector-panel');
    const heading = node('span', title); heading.id = 'workspace-heading-' + ++nextId;
    const close = () => { outer.remove(); if (previous?.isConnected) previous.focus(); };
    const header = node('header'), closeButton = button('Close', 'إغلاق', close);
    closeButton.className = 'icon-btn'; closeButton.setAttribute('aria-label', closeButton.textContent); closeButton.textContent = '×';
    header.append(heading, closeButton); card.append(header); outer.append(card);
    outer.setAttribute('role', 'dialog'); outer.setAttribute('aria-modal', 'true'); outer.setAttribute('aria-labelledby', heading.id);
    outer.onclick = e => { if (e.target === outer) close(); };
    outer.onkeydown = e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
      if (e.key === 'Tab') {
        const controls = [...card.querySelectorAll('button,a[href],input,textarea,select,[tabindex]')].filter(n => !n.disabled && n.tabIndex >= 0 && !n.closest('[hidden]'));
        const first = controls[0], last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.body.append(outer); closeButton.focus(); return {card, close};
  }
  function prompt(text, path = '') {
    const input = document.querySelector('#input');
    input.value = text + (path ? '\n' + path : ''); input.dispatchEvent(new Event('input', {bubbles: true})); input.focus();
  }
  function requireSession(sid) {
    if (!sid || sid !== session()) throw new Error(tr('Open this conversation or project again before continuing.', 'افتح هذه المحادثة أو المشروع مجددًا للمتابعة.'));
  }
  function missingSession(panel) {
    panel.append(node('p', tr('Open a conversation or project first.', 'افتح محادثة أو مشروعًا أولًا.')));
  }
  function files(panel, current, close) {
    const sid = session(); if (!sid) { missingSession(panel); return; }
    panel.append(node('p', tr('Create documents by asking in chat. Import, read and save files here.', 'اطلب إنشاء الملفات بالمحادثة. من هون بتقدر تستوردها وتقرأها وتحفظها.')));
    const message = node('p', '', 'muted'), bar = node('div', '', 'connector-actions'), list = node('div'); panel.append(bar, message, list);
    const input = node('input'); input.type = 'file'; input.accept = '.html,.md,.pdf,.zip,.txt'; input.hidden = true;
    const importButton = button('Import a file', 'استيراد ملف', () => input.click(), true);
    let request = 0;
    const refresh = async () => {
      const version = ++request; status(message, tr('Loading files…', 'جارٍ تحميل الملفات…'));
      try {
        requireSession(sid);
        const data = await api('/api/documents?session=' + encodeURIComponent(sid));
        if (!current() || version !== request) return;
        list.replaceChildren(); status(message, '');
        if (!data.files.length) list.append(node('p', tr('No documents yet.', 'ما في ملفات بعد.')));
        for (const file of data.files) {
          const row = node('section', '', 'connector-card'), details = node('div');
          details.append(node('strong', file.path), node('small', `${file.format.toUpperCase()} · ${Math.ceil(file.bytes / 1024)} KB`, 'muted'));
          const actions = node('div', '', 'connector-actions');
          const read = button('Read', 'قراءة', async () => {
            read.disabled = true;
            try {
              requireSession(sid); const data = await api('/api/documents/read', {session: sid, path: file.path});
              if (current()) { read.disabled = false; const view = dialog(file.name); view.card.append(node('pre', data.text, 'workspace-evidence')); }
            } catch (e) { if (current()) status(message, e.message, true); }
            finally { read.disabled = false; }
          });
          const save = node('a', tr('Save', 'حفظ'), 'btn primary');
          save.href = '/api/documents/download?' + new URLSearchParams({session: sid, path: file.path}); save.download = file.name;
          save.onclick = e => {
            try {
              requireSession(sid);
              if (window.NewAlPhone?.saveDocument) {
                e.preventDefault(); window.NewAlPhone.saveDocument(save.href, file.name, ({pdf:'application/pdf',zip:'application/zip',html:'text/html',md:'text/markdown',txt:'text/plain'})[file.format]);
              }
            } catch (error) { e.preventDefault(); status(message, error.message, true); }
          };
          const use = button('Work on it', 'اشتغل عليه', () => {
            try { requireSession(sid); close(); prompt(tr('Read this file and help me improve it:', 'اقرأ هالملف وساعدني أحسّنه:'), file.path); }
            catch (e) { status(message, e.message, true); }
          });
          actions.append(read, save, use); row.append(details, actions); list.append(row);
        }
      } catch (e) {
        if (!current() || version !== request) return;
        status(message, e.message, true); list.replaceChildren(button('Retry', 'إعادة المحاولة', refresh));
      }
    };
    input.onchange = async () => {
      const file = input.files[0]; if (!file) return;
      if (file.size > 32 * 1024 * 1024) { status(message, tr('Maximum file size is 32 MB.', 'أقصى حجم للملف 32 ميغابايت.'), true); input.value = ''; return; }
      importButton.disabled = true; status(message, tr('Importing…', 'جارٍ الاستيراد…'));
      try {
        requireSession(sid);
        const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(new Error(tr('Could not read this file.', 'تعذرت قراءة هذا الملف.'))); reader.readAsDataURL(file); });
        requireSession(sid); await api('/api/documents/import', {session: sid, path: file.name, data}); if (current()) await refresh();
      } catch (e) { if (current()) status(message, e.message, true); }
      finally { importButton.disabled = false; input.value = ''; }
    };
    bar.append(importButton, input, button('Create a document', 'إنشاء ملف', () => { close(); prompt(tr('Create a document for me in ', 'أنشئ لي ملف بصيغة ')); }));
    refresh();
  }
  function memory(panel, current) {
    const sid = session(); if (!sid) { missingSession(panel); return; }
    panel.append(node('p', tr('Memory belongs to this project. Review learned lessons and the evidence behind them.', 'الذاكرة خاصة بهذا المشروع. راجع الدروس المحفوظة والأدلة التي تدعمها.')));
    const message = node('p', '', 'muted'), counts = node('p', '', 'muted'), list = node('div');
    const controls = node('div', '', 'connector-actions'), label = node('label', '', 'workspace-toggle'), toggle = node('input'); toggle.type = 'checkbox'; toggle.disabled = true;
    label.append(toggle, node('span', tr('Use project memory', 'استخدام ذاكرة المشروع')));
    const form = node('form', '', 'connector-actions'), search = node('input'), searchLabel = node('label', tr('Search lessons', 'البحث في الدروس'));
    search.type = 'search'; search.id = 'memory-search-' + ++nextId; searchLabel.htmlFor = search.id;
    const searchButton = button('Search', 'بحث', null); searchButton.type = 'submit'; form.append(searchLabel, search, searchButton);
    let request = 0, enabled = true, saving = false;
    const refresh = async () => {
      const version = ++request; status(message, tr('Loading memory…', 'جارٍ تحميل الذاكرة…'));
      try {
        requireSession(sid);
        const data = await api('/api/memory?' + new URLSearchParams({session: sid, query: search.value}));
        if (!current() || version !== request) return;
        enabled = data.enabled; toggle.checked = enabled; toggle.disabled = saving; status(message, '');
        counts.textContent = tr(`${data.counts.lessons} lessons · ${data.counts.episodes} episodes`, `${data.counts.lessons} دروس · ${data.counts.episodes} أحداث`);
        list.replaceChildren(); list.append(node('h3', tr('Stored lessons', 'الدروس المحفوظة')));
        if (!data.lessons.length) list.append(node('p', tr('No stored lessons.', 'لا توجد دروس محفوظة.')));
        for (const lesson of data.lessons) {
          const row = node('section', '', 'connector-card'), detail = node('div', '', 'workspace-detail');
          detail.append(node('strong', lesson.topic), node('p', lesson.lesson), node('pre', plain(lesson.evidence), 'workspace-evidence'));
          const forget = button('Delete lesson', 'حذف الدرس', async () => {
            forget.disabled = true;
            try {
              requireSession(sid); await api('/api/memory/forget', {session: sid, id: lesson.id});
              if (current()) { await refresh(); search.focus(); }
            } catch (e) { if (current()) status(message, e.message, true); }
            finally { forget.disabled = false; }
          });
          forget.setAttribute('aria-label', tr('Delete lesson: ', 'حذف الدرس: ') + lesson.topic); row.append(detail, forget); list.append(row);
        }
        list.append(node('h3', tr('Recent failures', 'الإخفاقات الأخيرة')));
        if (!data.recent_failures.length) list.append(node('p', tr('No recent failures.', 'لا توجد إخفاقات حديثة.')));
        for (const failure of data.recent_failures) {
          const row = node('section', '', 'workspace-failure'); row.append(node('strong', failure.task), node('p', failure.error), node('pre', plain(failure.evidence), 'workspace-evidence')); list.append(row);
        }
      } catch (e) {
        if (!current() || version !== request) return;
        status(message, e.message, true); list.replaceChildren(button('Retry', 'إعادة المحاولة', refresh));
      }
    };
    toggle.onchange = async () => {
      const previous = enabled, desired = toggle.checked; saving = true; toggle.disabled = true;
      try {
        requireSession(sid); const data = await api('/api/memory/settings', {session: sid, enabled: desired});
        enabled = data.enabled; toggle.checked = enabled; if (current()) status(message, tr('Memory setting saved.', 'تم حفظ إعداد الذاكرة.'));
      } catch (e) { toggle.checked = previous; if (current()) status(message, e.message, true); }
      finally { saving = false; toggle.disabled = false; }
    };
    const clear = button('Clear memory', 'مسح الذاكرة', () => {
      const confirmation = dialog(tr('Clear project memory?', 'مسح ذاكرة المشروع؟'));
      confirmation.card.append(node('p', tr('This permanently deletes this project’s stored lessons and history. This cannot be undone.', 'سيؤدي هذا إلى حذف دروس هذا المشروع وسجل ذاكرته نهائيًا. لا يمكن التراجع.')));
      const error = node('p'), confirm = button('Clear stored memory', 'مسح الذاكرة المحفوظة', async () => {
        confirm.disabled = true;
        try {
          requireSession(sid); await api('/api/memory/clear', {session: sid, confirm: true});
          confirmation.close(); if (current()) await refresh();
        } catch (e) { status(error, e.message, true); confirm.disabled = false; }
      }, true);
      const cancel = button('Cancel', 'إلغاء', confirmation.close); confirmation.card.append(cancel, confirm, error); cancel.focus();
    });
    controls.append(label, clear); panel.append(controls, form, message, counts, list);
    form.onsubmit = e => { e.preventDefault(); refresh(); }; refresh();
  }
  function improve(panel, current, close) {
    const message = node('p', '', 'muted'), list = node('div'), restart = button('Restart engine', 'إعادة تشغيل المحرّك', () => {
      try {
        window.NewAlPhone.restartEngine(); restart.disabled = true; status(message, tr('Restarting engine…', 'جارٍ إعادة تشغيل المحرّك…'));
      } catch (e) { status(message, e.message, true); }
    }, true);
    const restartRequired = () => {
      if (!restart.isConnected) actions.append(restart);
      restart.disabled = !window.NewAlPhone?.restartEngine;
      status(message, tr('Verified revision is pending startup health. Restart the engine to activate it.', 'النسخة الموثقة بانتظار فحص الإقلاع. أعد تشغيل المحرّك لتفعيلها.') + (restart.disabled ? tr(' Close and reopen the app to restart.', ' أغلق التطبيق وافتحه مجددًا لإعادة التشغيل.') : ''));
    };
    const applyResult = result => {
      if (!current()) return;
      if (result.restart_required) return restartRequired();
      if (result.hot_reloaded || result.state === 'rolled_back') {
        status(message, result.text || tr('Verified hot resources are active.', 'تم تفعيل الموارد الساخنة الموثقة.'));
        if (window.location?.reload) setTimeout(() => window.location.reload(), 0);
        return;
      }
      status(message, result.text || result.state || '');
    };
    panel.append(node('p', tr('Describe one improvement. The agent prepares a separate engine copy, edits it and tests it, then applies the verified version after active tasks finish. You can restore the original engine here.', 'حدد تحسينًا واحدًا. البرنامج بيجهّز نسخة منفصلة ويعدّلها ويختبرها، ثم يفعّل النسخة الناجحة تلقائيًا بعد انتهاء المهام. تقدر ترجع للمحرّك الأصلي من هون.')));
    const goal = node('textarea'), goalLabel = node('label', tr('What should improve?', 'شو بدك يتحسّن؟'));
    goal.id = 'improvement-goal-' + ++nextId; goalLabel.htmlFor = goal.id; goal.placeholder = goalLabel.textContent; goal.className = 'workspace-goal'; panel.append(goalLabel, goal);
    const ask = button('Prepare improvement', 'جهّز التحسين', () => {
      if (!goal.value.trim()) { goal.focus(); return; }
      close(); prompt(tr('Improve your Python engine for this goal. Use self_evolve, edit the isolated candidate, then self_evolve_verify. Only after successful verification use self_evolve_activate to apply it automatically after active tasks finish. Report the real test evidence and activation state. Goal: ', 'طوّر محرّكك لتحقيق هالهدف. استخدم self_evolve، عدّل النسخة المنفصلة، ثم self_evolve_verify. بعد نجاح الاختبارات فقط استخدم self_evolve_activate لتطبيقها تلقائيًا بعد انتهاء المهام. اعرض دليل الاختبار وحالة التفعيل الحقيقية. الهدف: ') + goal.value);
    }, true);
    const rollback = button('Restore original engine', 'رجوع للمحرّك الأصلي', async () => {
      rollback.disabled = true;
      try { const result = await api('/api/evolution/rollback', {}); applyResult(result); }
      catch (e) { if (current()) status(message, e.message, true); }
      finally { rollback.disabled = false; }
    });
    let updatesEnabled = true;
    const updateToggle = button('Pause automatic updates', 'إيقاف التحديثات التلقائية', async () => {
      updateToggle.disabled = true;
      try {
        const data = await api('/api/evolution/automatic', {enabled: !updatesEnabled});
        updatesEnabled = data.enabled;
        updateToggle.textContent = updatesEnabled ? tr('Pause automatic updates', 'إيقاف التحديثات التلقائية') : tr('Enable automatic updates', 'تشغيل التحديثات التلقائية');
        status(message, updatesEnabled ? tr('Verified GitHub updates will be checked while idle.', 'سيجري فحص تحديثات GitHub واختبارها وتفعيلها وقت الخمول.') : tr('Automatic updates paused.', 'أُوقفت التحديثات التلقائية.'));
      } catch (e) { if (current()) status(message, e.message, true); }
      finally { updateToggle.disabled = false; }
    });
    const actions = node('div', '', 'connector-actions'); actions.append(ask, rollback, updateToggle); panel.append(actions, message, list);
    const refresh = async () => {
      status(message, tr('Loading improvements…', 'جارٍ تحميل التحسينات…'));
      try {
        const data = await api('/api/evolution'); if (!current()) return;
        list.replaceChildren();
        updatesEnabled = data.automatic?.enabled !== false;
        updateToggle.textContent = updatesEnabled ? tr('Pause automatic updates', 'إيقاف التحديثات التلقائية') : tr('Enable automatic updates', 'تشغيل التحديثات التلقائية');
        const update = data.automatic?.state || data.automatic?.error ? data.automatic : data.update;
        const state = update?.state || (update?.error ? 'check_failed' : 'up_to_date');
        const currentVersion = data.current?.version_code ?? '';
        const revision = data.current?.revision || 'packaged';
        status(message, tr('Update state: ', 'حالة التحديث: ') + state + ' · v' + currentVersion + ' · ' + revision + (update?.error ? ' · ' + update.error : ''), Boolean(update?.error));
        if (!data.candidates.length) list.append(node('p', tr('No improvement candidates yet.', 'لا توجد نسخ محسّنة بعد.')));
        for (const item of data.candidates) {
          const row = node('section', '', 'connector-card'); row.append(node('span', item.goal + ' · ' + item.status));
          if (item.status === 'verified' || item.status === 'ready_to_activate') {
            const activate = button('Activate', 'تفعيل', async () => {
              activate.disabled = true;
              try { const result = await api('/api/evolution/activate', {candidate: item.id}); applyResult(result); }
              catch (e) { if (current()) status(message, e.message, true); }
              finally { activate.disabled = false; }
            }, true); row.append(activate);
          }
          list.append(row);
        }
      } catch (e) { if (current()) { status(message, e.message, true); list.replaceChildren(button('Retry', 'إعادة المحاولة', refresh)); } }
    };
    refresh();
  }
  function tasks(panel, current, close) {
    const sid = session();
    if (!sid) return missingSession(panel);
    const message = node('p'), list = node('div');
    const refresh = async () => {
      status(message, tr('Loading saved progress…', 'جارٍ تحميل التقدم المحفوظ…'));
      try {
        requireSession(sid);
        const data = await api('/api/task-state?session=' + encodeURIComponent(sid));
        if (!current() || session() !== sid) return;
        list.replaceChildren();
        status(message, data.running ? tr('This conversation is running.', 'هذه المحادثة قيد التنفيذ.')
          : tr('Saved progress for this conversation.', 'التقدم المحفوظ لهذه المحادثة.'));
        for (const task of data.tasks || []) {
          const row = node('section', '', 'workspace-failure'), checkpoint = task.checkpoint || {};
          row.append(node('strong', task.objective), node('p', task.status === 'complete'
            ? tr('Completed', 'مكتملة') : tr('Unfinished', 'غير مكتملة')));
          if (checkpoint.progress) row.append(node('p', checkpoint.progress, 'workspace-evidence'));
          if (checkpoint.blocker) row.append(node('p', checkpoint.blocker, 'workspace-evidence'));
          if (checkpoint.next_step && task.status !== 'complete') row.append(node('p', tr('Next: ', 'التالي: ') + checkpoint.next_step));
          if (task.id === data.active_task && !data.running) {
            row.append(button('Prepare continuation', 'تجهيز المتابعة', () => {
              if (!current()) return;
              try { requireSession(sid); }
              catch (error) { status(message, error.message, true); return; }
              close(); prompt(tr('Continue the unfinished task from its saved checkpoint. Check task_resume and keep completed operations intact.',
                'كمل المهمة غير المكتملة من نقطة الحفظ. اقرأ task_resume وحافظ على العمليات التي اكتملت.'));
            }, true));
          }
          list.append(row);
        }
        if (!data.tasks?.length) list.append(node('p', tr('No saved tasks in this conversation yet.', 'لا توجد مهام محفوظة في هذه المحادثة بعد.')));
      } catch (error) { if (current()) status(message, error.message, true); }
    };
    panel.append(button('Refresh', 'تحديث', refresh), message, list);
    refresh();
  }
  function openWorkspace() {
    const view = dialog(tr('Workspace', 'مساحة العمل')), tabs = node('div', '', 'workspace-tabs'), panel = node('section', '', 'workspace-panel');
    tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', tr('Workspace', 'مساحة العمل'));
    panel.setAttribute('role', 'tabpanel'); panel.id = 'workspace-panel-' + ++nextId;
    const definitions = [['Files', 'الملفات', files], ['Memory', 'الذاكرة', memory], ['Improve', 'التطوير', improve], ['Tasks', 'المهام', tasks]];
    let active = 0;
    const buttons = definitions.map(([en, arabic], index) => {
      const tab = button(en, arabic, () => select(index)); tab.id = panel.id + '-tab-' + index;
      tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', panel.id);
      tab.onkeydown = e => {
        let selected = index;
        if (e.key === 'ArrowRight') selected = (index + 1) % definitions.length;
        else if (e.key === 'ArrowLeft') selected = (index + definitions.length - 1) % definitions.length;
        else if (e.key === 'Home') selected = 0;
        else if (e.key === 'End') selected = definitions.length - 1;
        else return;
        e.preventDefault(); select(selected); buttons[selected].focus();
      };
      tabs.append(tab); return tab;
    });
    function select(index) {
      const version = ++active;
      buttons.forEach((tab, i) => { tab.setAttribute('aria-selected', String(i === index)); tab.tabIndex = i === index ? 0 : -1; });
      panel.setAttribute('aria-labelledby', buttons[index].id); panel.replaceChildren();
      definitions[index][2](panel, () => view.card.isConnected && active === version, view.close);
    }
    view.card.append(tabs, panel); select(0);
  }
  function setup() {
    const sidebar = document.querySelector('.side-top'); if (!sidebar || document.querySelector('#open-workspace')) return;
    const entry = button('Workspace', 'مساحة العمل', openWorkspace); entry.id = 'open-workspace'; entry.className = 'side-item'; sidebar.append(entry);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
})();
