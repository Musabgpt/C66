/* MusabAI on the phone, focused: the interface shows Termux, GitHub and Chrome (Preview), and the phone's chat;
   VS Code, extensions and skills, repository (cloud) tasks, the workspace sheet, background agents, the cloud/worktree
   picker, MCP runtimes and other services' cards are not shown (lean.py does the same for the agent's tools). */
(() => {
  'use strict';
  document.documentElement.classList.add('musab-lean');
  try {
    const env = localStorage.getItem('nc.pref.env');
    if (env && env !== 'local') localStorage.setItem('nc.pref.env', 'local');   // the cloud picker is gone
  } catch (e) { /* storage blocked: the default is local */ }

  const ar = () => (document.documentElement.lang || navigator.language || '').startsWith('ar') ||
    (window.NCi18n ? NCi18n.wanted(localStorage.getItem('nc.pref.lang') || '') === 'ar' : false);
  const KEEP = /^(?:connect|test|disconnect):(?:github|termux)$/;
  const set = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };
  const hide = el => { if (el && el.style.display !== 'none') el.style.display = 'none'; };

  function closeTopOverlay() {
    const openPicker = document.querySelector('.picker.open');
    if (openPicker) { openPicker.classList.remove('open'); return true; }
    const popup = document.querySelector('#popup');
    if (popup && !popup.hidden) { popup.hidden = true; return true; }
    const modal = document.querySelector('#modal');
    if (modal && !modal.hidden) {
      const btn = document.querySelector('#modal-close');
      if (btn) btn.click(); else modal.hidden = true;
      return true;
    }
    const conn = document.querySelector('#connector-dialog');
    if (conn && !conn.hidden) {
      const btn = document.querySelector('#connector-close');
      if (btn) btn.click(); else conn.hidden = true;
      return true;
    }
    const sched = document.querySelector('#scheduled-panel');
    if (sched && !sched.hidden) {
      const btn = document.querySelector('#scheduled-close');
      if (btn) btn.click(); else sched.hidden = true;
      return true;
    }
    const drawer = document.querySelector('#musab-drawer');
    if (drawer && !drawer.hidden) {
      const btn = drawer.querySelector('.md-x, [aria-label="Close"], button');
      if (btn) btn.click(); else drawer.hidden = true;
      return true;
    }
    const rev = document.querySelector('#review');
    if (rev && !rev.hidden) {
      const btn = document.querySelector('#review-close');
      if (btn) btn.click(); else rev.hidden = true;
      return true;
    }
    const term = document.querySelector('#terminal');
    if (term && !term.hidden) {
      const btn = document.querySelector('#term-close');
      if (btn) btn.click(); else term.hidden = true;
      return true;
    }
    const app = document.querySelector('#app');
    if (app && window.innerWidth < 700 && !app.classList.contains('no-sidebar')) {
      app.classList.add('no-sidebar');
      return true;
    }
    return false;
  }

  function installBackGuard() {
    try {
      if (!history.state || !history.state.musabGuard) {
        history.pushState({ musabGuard: 1 }, '', location.href);
      }
      window.addEventListener('popstate', () => {
        closeTopOverlay();
        try { history.pushState({ musabGuard: 1 }, '', location.href); } catch (_e) {}
      });
    } catch (_e) {}
  }

  function tidy() {
    const list = document.querySelector('#connector-list');
    if (list) {
      list.querySelectorAll('.connector-card').forEach(card => {
        if (card.closest('.free-providers')) return;
        const action = card.querySelector('[data-action]');
        if (!action || !KEEP.test(action.dataset.action)) hide(card);
      });
      list.querySelectorAll('.integration-group-heading').forEach(hide);
    }
    const ext = document.querySelector('#extension-list');
    if (ext) {
      hide(ext);
      let prev = ext.previousElementSibling;
      for (let i = 0; i < 2 && prev; i++, prev = prev.previousElementSibling) if (/^(P|H3)$/.test(prev.tagName)) hide(prev);
    }
    // GitHub and Termux first, the model keys after them
    const keys = document.querySelector('#connector-dialog .free-providers');
    if (keys && keys.parentElement && keys.parentElement.lastElementChild !== keys) keys.parentElement.append(keys);
    set(document.querySelector('#connector-heading'), ar() ? 'الاتصالات' : 'Connections');
    set(document.querySelector('#connector-intro'), ar()
      ? 'Termux يشغّل الأوامر وGitHub يحفظ المشاريع ويبنيها. مفاتيح النماذج المجانية تحتهما.'
      : 'Termux runs the commands and GitHub keeps and builds the projects. Free model keys are below.');
    set(document.querySelector('#open-connectors'), ar() ? 'الاتصالات' : 'Connections');
    set(document.querySelector('#connector-refresh'), ar() ? 'تحديث' : 'Refresh');
  }

  let queued = false;
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; tidy(); }); } };
  document.addEventListener('DOMContentLoaded', () => {
    installBackGuard();
    tidy();
    const relevant = '#connector-dialog, #connector-list, #extension-list, #open-connectors';
    new MutationObserver(records => {
      for (const record of records) {
        const target = record.target;
        // Streaming text and tool output never change the connections panel.
        if (target.nodeType === 1 && target.closest('#messages')) continue;
        if (target.nodeType === 1 && target.closest(relevant)) { later(); return; }
        for (const node of record.addedNodes) {
          if (node.nodeType === 1 && !node.matches('#messages') &&
              (node.matches(relevant) || node.querySelector(relevant))) { later(); return; }
        }
      }
    }).observe(document.body, {childList: true, subtree: true});
    new MutationObserver(later).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
  });
})();
