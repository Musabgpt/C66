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

  const ar = () => (document.documentElement.lang || navigator.language || '').startsWith('ar');
  const KEEP = /^(?:connect|test|disconnect):(?:github|termux)$/;
  const set = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };
  const hide = el => { if (el && el.style.display !== 'none') el.style.display = 'none'; };

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
  }

  let queued = false;
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; tidy(); }); } };
  document.addEventListener('DOMContentLoaded', () => {
    tidy();
    new MutationObserver(later).observe(document.body, {childList: true, subtree: true});
  });
})();
