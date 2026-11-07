/* <cqa-page-risks> — радар рисков #/risks?category=&group=&open=1: все грубые ошибки по категориям,
   цитаты-доказательства, «В комплаенс» и статус дела. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui;

  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  class CqaPageRisks extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('risk.title') + ' · CQA';
      var self = this;
      this.groupSel = h('select', { class: 'select select--small', id: 'risk-group', 'aria-label': t('dash.group') });
      this.groupSel.addEventListener('change', function () { self.group = self.groupSel.value || null; self.sync(); self.load(); });
      this.filters = h('div', { class: 'row risk-filters' });
      this.box = h('div', null, ui.skeleton('card', 6));
      this.sub = h('div', { class: 'page-header__sub' });
      this.textContent = '';
      this.appendChild(h('div', { class: 'page' },
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('risk.title')), this.sub),
          h('div', { class: 'row' }, h('label', { class: 'muted', for: 'risk-group' }, t('dash.group')), this.groupSel)),
        h('div', { class: 'stack stack--l' },
          ui.msgbar('info', null, t('risk.intro')),
          this.filters,
          this.box)));
      this.addEventListener('cqa-compliance-changed', function () {
        var r = self.risks;
        self.sub.textContent = t('risk.sub', { n: r.total, c: r.n_conversations, s: r.items.filter(function (it) { return it.compliance; }).length });
        self.renderFilters();
      });
      CQA.api.datasets().catch(function () { return []; }).then(function (list) {
        var q = self.query || {};
        self.group = q.group === 'all' ? null : q.group || CQA.defaultGroup(list);
        CQA.dom.clear(self.groupSel);
        self.groupSel.appendChild(h('option', { value: '' }, t('dash.allGroups')));
        (list || []).forEach(function (d) { self.groupSel.appendChild(h('option', { value: d.group_id }, d.group_id)); });
        self.groupSel.value = self.group || '';
        self.load();
      });
    }

    update(params, query) {
      this.query = query;
      if (this.risks) this.render();
      return true;
    }

    sync() {
      var q = this.query || {};
      CQA.router.setQuery({ group: this.group || 'all', category: q.category || null, open: q.open || null });
    }

    load() {
      var self = this;
      ui.load(this.box, ui.skeleton('card', 6), function () { return CQA.api.risks(self.group); }, function (r) {
        self.risks = r;
        self.render();
      });
    }

    render() {
      var r = this.risks, q = this.query || {};
      var sent = r.items.filter(function (it) { return it.compliance; }).length;
      this.sub.textContent = r.total ? t('risk.sub', { n: r.total, c: r.n_conversations, s: sent }) : t('risk.none');
      this.renderFilters();
      var view = r;
      if (q.open === '1') {
        var items = r.items.filter(function (it) { return !it.compliance; });
        var ids = {};
        items.forEach(function (it) { ids[it.criterion_result_id] = 1; });
        view = {
          total: items.length, n_conversations: r.n_conversations, items: items,
          by_category: r.by_category.map(function (c) {
            var list = c.criterion_result_ids.filter(function (id) { return ids[id]; });
            return { category: c.category, count: list.length, criterion_result_ids: list };
          }).filter(function (c) { return c.count; })
        };
      }
      CQA.dom.clear(this.box);
      if (!view.total) {
        this.box.appendChild(ui.empty(r.total ? t('risk.allSent') : t('risk.none'), r.total ? null : t('risk.noneText'), null, 'checkCircle'));
        return;
      }
      var el = document.createElement('cqa-risk-radar');
      el.data = { risks: view, limit: null, category: q.category || null, detailed: true };
      this.box.appendChild(el);
    }

    renderFilters() {
      var self = this, r = this.risks, q = this.query || {};
      if (!r) return;
      var go = function (patch) {
        var nq = Object.assign({}, self.query || {}, patch);
        CQA.router.go('/risks', { group: self.group || 'all', category: nq.category || null, open: nq.open || null });
      };
      var seg = h('div', { class: 'segmented', role: 'group', 'aria-label': t('risk.category') },
        h('button', { type: 'button', 'aria-pressed': String(!q.category), onClick: function () { go({ category: null }); } }, t('risk.allCats', { n: r.total })),
        r.by_category.map(function (c) {
          return h('button', { type: 'button', 'aria-pressed': String(q.category === c.category), onClick: function () { go({ category: c.category }); } },
            cap(c.category) + ' · ' + c.count);
        }));
      var pending = r.items.filter(function (it) { return !it.compliance; }).length;
      var openBtn = h('button', { class: 'toggle', type: 'button', 'aria-pressed': String(q.open === '1'), onClick: function () { go({ open: q.open === '1' ? null : '1' }); } },
        icon('filter'), t('risk.onlyOpen', { n: pending }));
      CQA.dom.replace(this.filters, h('div', { class: 'segmented-scroll' }, seg), openBtn);
    }
  }

  customElements.define('cqa-page-risks', CqaPageRisks);
})(window.CQA = window.CQA || {});
