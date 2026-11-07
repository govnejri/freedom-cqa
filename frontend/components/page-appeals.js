/* <cqa-page-appeals> — список апелляций #/appeals?status=open|decided|all */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  var STATUSES = ['open', 'decided', 'all'];

  class CqaPageAppeals extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.appeals') + ' · CQA';
      this.status = STATUSES.indexOf((this.query || {}).status) >= 0 ? this.query.status : 'open';
      var self = this;
      this.seg = h('div', { class: 'segmented', role: 'radiogroup', 'aria-label': t('appeals.filter') });
      STATUSES.forEach(function (s) {
        var b = h('button', { type: 'button', role: 'radio', 'data-status': s }, t('appeals.f.' + s));
        b.addEventListener('click', function () { self.setStatus(s, true); });
        self.seg.appendChild(b);
      });
      this.seg.addEventListener('keydown', function (e) {
        var i = STATUSES.indexOf(self.status);
        if (e.key === 'ArrowRight') i = (i + 1) % STATUSES.length;
        else if (e.key === 'ArrowLeft') i = (i + STATUSES.length - 1) % STATUSES.length;
        else return;
        e.preventDefault();
        self.setStatus(STATUSES[i], true);
        self.seg.querySelector('[data-status="' + STATUSES[i] + '"]').focus();
      });
      this.box = h('div');
      this.textContent = '';
      this.appendChild(h('div', { class: 'page' },
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('nav.appeals')), h('div', { class: 'page-header__sub' }, t('appeals.subtitle'))),
          this.seg),
        this.box));
      this.setStatus(this.status, false);
    }

    update(params, query) {
      this.setStatus(STATUSES.indexOf(query.status) >= 0 ? query.status : 'open', false);
      return true;
    }

    setStatus(s, url) {
      this.status = s;
      this.seg.querySelectorAll('button').forEach(function (b) {
        var on = b.getAttribute('data-status') === s;
        b.setAttribute('aria-checked', String(on));
        b.tabIndex = on ? 0 : -1;
      });
      if (url) CQA.router.setQuery(s === 'open' ? {} : { status: s });
      this.load();
    }

    load() {
      var self = this, status = this.status;
      ui.load(this.box, ui.skeleton('table', 4), function () { return CQA.api.appeals(status); }, function (list) {
        if (status !== self.status) return;
        if (!list.length) {
          self.box.appendChild(ui.empty(t('appeals.empty.' + status), t('appeals.emptyText'), h('a', { class: 'btn', href: '#/conversations' }, t('nav.conversations')), 'scale'));
          return;
        }
        var tbody = h('tbody');
        list.forEach(function (a) {
          var o = a.original || {};
          var d = a.decision;
          var href = '#/appeals/' + a.id;
          var tr = h('tr', { class: 'is-clickable' },
            h('td', null, h('a', { href: href }, '#' + a.id)),
            h('td', null, h('span', { class: 'mono' }, fmt.externalId(a.uid))),
            h('td', null, h('span', { class: 'crit-ref' }, h('b', null, a.criterion_id), ' ', a.criterion_title)),
            h('td', null, h('span', { class: 'row row--nowrap' },
              CQA.verdict(o.verdict, o.type),
              d && d.action === 'change' ? [icon('arrowRight', 'icon--16 muted'), CQA.verdict(d.new_verdict, o.type)] : null)),
            h('td', null, ui.badge(CQA.i18n.tx('appealStatus.' + a.status, a.status), a.status === 'decided' ? 'success' : 'warning'),
              d ? h('span', { class: 'muted t-caption1' }, ' ', d.action === 'change' ? t('decision.changed') : t('decision.confirmed')) : null),
            h('td', { class: 'num' }, a.new_final_total != null ? CQA.score(a.new_final_total, a.prev_final_total) : h('span', { class: 'muted' }, '—')),
            h('td', { class: 'muted nowrap col-operator' }, fmt.date(a.created_at)));
          tr.addEventListener('click', function (e) { if (!e.target.closest('a')) location.hash = href; });
          tbody.appendChild(tr);
        });
        self.box.appendChild(h('div', { class: 'table-wrap' }, h('table', { class: 'table', 'aria-label': t('nav.appeals') },
          h('thead', null, h('tr', null,
            h('th', { scope: 'col' }, '№'), h('th', { scope: 'col' }, t('appeals.colConversation')), h('th', { scope: 'col' }, t('appeals.colCriterion')),
            h('th', { scope: 'col' }, t('appeals.colVerdict')), h('th', { scope: 'col' }, t('appeals.colStatus')),
            h('th', { scope: 'col', class: 'num' }, t('appeals.colTotal')), h('th', { scope: 'col', class: 'col-operator' }, t('appeals.colCreated')))),
          tbody)));
      });
    }
  }

  customElements.define('cqa-page-appeals', CqaPageAppeals);
})(window.CQA = window.CQA || {});
