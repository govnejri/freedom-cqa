/* Запуск: язык → каркас → режим API/мок → роутер. Подключается последним. */
(function (CQA) {
  'use strict';

  CQA.config = CQA.config || { checklistId: 'freedom-cqa' };

  CQA.boot = function () {
    CQA.i18n.init();
    document.documentElement.lang = CQA.i18n.lang();
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
    }).catch(function (err) {
      console.error('[CQA] boot', err);
      shell.outlet.textContent = '';
      shell.outlet.appendChild(CQA.h('div', { class: 'page' }, CQA.ui.error(err, function () { location.reload(); }, CQA.t('app.bootFailed'))));
    });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', CQA.boot);
  else CQA.boot();
})(window.CQA = window.CQA || {});
