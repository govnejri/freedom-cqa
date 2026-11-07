/* «Начало работы» — короткий путеводитель по задачам. Открывается из меню и сам при первом заходе.
   По ролям: 4 шага «что можно сделать», каждый — ссылка прямо в фичу. Закрытие запоминается в localStorage. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui;

  var LS_SEEN = 'cqa.guideSeen';
  var LS_ROLE = 'cqa.guideRole';
  var ROLES = ['lead', 'qa', 'operator'];

  /** Диалог для примеров: p1-ru-007 из демо, иначе самый низкий итог без грубого нарушения. */
  var demoPromise = null;
  function demoUid() {
    if (!demoPromise) {
      demoPromise = CQA.api.conversations({}).then(function (list) {
        list = (list || []).filter(function (s) { return !CQA.isTrainerUid(s.uid) && (s.final_total != null || s.total != null); });
        var known = list.filter(function (s) { return s.external_id === 'p1-ru-007'; })[0];
        if (known) return known.uid;
        list.sort(function (a, b) { return (a.final_total != null ? a.final_total : a.total) - (b.final_total != null ? b.final_total : b.total); });
        var pick = list.filter(function (s) { return !s.gross; })[0] || list[0];
        return pick ? pick.uid : null;
      }).catch(function () { return null; });
    }
    return demoPromise;
  }

  function steps(role, uid) {
    var card = uid ? CQA.convHref(uid) : '#/conversations';
    var checklist = CQA.navHref('checklist');
    return {
      lead: [
        { title: t('guide.lead1'), then: t('guide.lead1Then'), href: '#/' },
        { title: t('guide.lead2'), then: t('guide.lead2Then'), href: '#/risks' },
        { title: t('guide.lead3'), then: t('guide.lead3Then'), href: '#/operators' },
        { title: t('guide.lead4'), then: t('guide.lead4Then'), href: '#/assistant' }
      ],
      qa: [
        { title: t('guide.qa1'), then: t('guide.qa1Then'), href: card },
        { title: t('guide.qa2'), then: t('guide.qa2Then'), href: '#/appeals' },
        { title: t('guide.qa3'), then: t('guide.qa3Then'), href: checklist },
        { title: t('guide.qa4'), then: t('guide.qa4Then'), href: '#/quality' }
      ],
      operator: [
        { title: t('guide.op1'), then: t('guide.op1Then'), href: '#/me' },
        { title: t('guide.op2'), then: t('guide.op2Then'), href: card },
        { title: t('guide.op3'), then: t('guide.op3Then'), href: '#/trainer' },
        { title: t('guide.op4'), then: t('guide.op4Then'), href: '#/tutor' }
      ]
    }[role];
  }

  function open(opts) {
    opts = opts || {};
    var role = CQA.store.get(LS_ROLE, 'qa');
    if (ROLES.indexOf(role) < 0) role = 'qa';
    var seg = h('div', { class: 'segmented', role: 'group', 'aria-label': t('guide.whoAria') });
    var list = h('div', { class: 'guide__steps' });
    var drawer;
    var uid = null;

    function renderSteps() {
      CQA.dom.clear(list);
      steps(role, uid).forEach(function (s) {
        list.appendChild(h('a', { class: 'guide__step', href: s.href, onClick: function () { done(); } },
          h('span', { class: 'guide__text' }, h('span', { class: 'guide__title' }, s.title), h('span', { class: 'guide__then' }, s.then)),
          h('span', { class: 'guide__go' }, icon('arrowRight'))));
      });
      Array.prototype.forEach.call(seg.children, function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-role') === role)); });
    }
    ROLES.forEach(function (r) {
      seg.appendChild(h('button', { type: 'button', 'data-role': r, 'aria-pressed': 'false', onClick: function () {
        role = r; CQA.store.set(LS_ROLE, r); renderSteps();
      } }, t('roles.' + r)));
    });

    function done() {
      CQA.store.set(LS_SEEN, true);
      if (drawer) drawer.close();
    }

    var body = h('div', { class: 'stack stack--l' },
      h('p', { class: 'guide__intro' }, opts.first ? t('guide.introFirst') : t('guide.intro')),
      h('div', { class: 'stack' }, h('div', { class: 'field__label' }, t('guide.who')), seg),
      list,
      h('div', { class: 'drawer__section' },
        h('h3', null, icon('bulb'), t('guide.tipsTitle')),
        h('ul', { class: 'guide__tips' },
          h('li', null, icon('search'), h('span', null, h('kbd', null, 'Ctrl K'), ' ', t('guide.tipPalette'))),
          h('li', null, icon('info'), h('span', null, t('guide.tipTerms'))),
          h('li', null, icon('panel'), h('span', null, t('guide.tipDetails'))))),
      h('div', { class: 'row' }, h('button', { class: 'btn btn--primary', type: 'button', onClick: done }, t('guide.ok'))));
    renderSteps();
    drawer = ui.drawer({ title: t('guide.title'), sub: t('guide.sub'), body: body, onClose: function () { CQA.store.set(LS_SEEN, true); } });
    demoUid().then(function (u) { if (u) { uid = u; renderSteps(); } });
    return drawer;
  }

  CQA.guide = {
    open: open,
    /** Показать при первом заходе (после загрузки первой страницы). */
    maybeFirst: function () {
      if (CQA.store.get(LS_SEEN, false)) return;
      if (new URLSearchParams(location.search).get('guide') === '0') return;
      setTimeout(function () { open({ first: true }); }, 400);
    }
  };
})(window.CQA = window.CQA || {});
