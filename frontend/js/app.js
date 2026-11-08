/* Запуск: язык → каркас → режим API/мок → роутер. Подключается последним. */
(function (CQA) {
  'use strict';

  CQA.config = CQA.config || { checklistId: 'freedom-cqa' };

  /** Тема: light | dark | auto (как в системе). Светлая — по умолчанию. */
  CQA.setTheme = function (theme) {
    if (['light', 'dark', 'auto'].indexOf(theme) < 0) theme = 'light';
    document.documentElement.setAttribute('data-theme', theme);
    var meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute('content', theme === 'light' ? 'light' : theme === 'dark' ? 'dark' : 'light dark');
    // цвет панели мобильного браузера — под шапку: белая или почти чёрная (ecosystem)
    var dark = theme === 'dark' || (theme === 'auto' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    var tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.setAttribute('content', dark ? '#0B0C0E' : '#FFFFFF');
    CQA.store.set('cqa.theme', theme === 'light' ? null : theme);
  };
  CQA.theme = function () { return document.documentElement.getAttribute('data-theme') || 'light'; };

  CQA.boot = function () {
    CQA.i18n.init();
    document.documentElement.lang = CQA.i18n.lang();
    CQA.setTheme(CQA.store.get('cqa.theme', 'light'));
    var root = document.getElementById('app-root') || document.body;
    var shell = document.createElement('cqa-app');
    root.textContent = '';
    root.appendChild(shell);
    return CQA.api.init().then(function () {
      var d = CQA.api.launchData || {};
      if (d.checklistId) CQA.config.checklistId = d.checklistId;
      if (d.page && !location.hash) location.hash = d.page.charAt(0) === '#' ? d.page : '#' + d.page;
      shell.ready();
      CQA.router.start(shell.outlet);
      CQA.guide.maybeFirst();
    }).catch(function (err) {
      console.error('[CQA] boot', err);
      shell.outlet.textContent = '';
      shell.outlet.appendChild(CQA.h('div', { class: 'page' }, CQA.ui.error(err, function () { location.reload(); }, CQA.t('app.bootFailed'))));
    });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', CQA.boot);
  else CQA.boot();
})(window.CQA = window.CQA || {});
