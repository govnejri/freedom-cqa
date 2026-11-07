/* Сравнение двух версий чек-листа: какие поля каких критериев изменились.
   CQA.checklistDiff(a, b) → {top: [{field, before, after}], criteria: [{id, title, status, fields}]}
   <cqa-checklist-diff> .data = {a: ChecklistVersionDTO, b: ChecklistVersionDTO} */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, U = CQA.util;

  var TOP_FIELDS = ['title', 'version_note', 'general_rules', 'principles', 'channel_rules', 'scoring', 'sections', 'signals'];
  var CRIT_ORDER = ['enabled', 'title', 'section', 'type', 'weight', 'na_allowed', 'na_condition', 'scale', 'definition', 'check_steps', 'interpretations', 'examples', 'evidence_roles', 'channels', 'flag_categories', 'cross_check'];

  function same(a, b) { return JSON.stringify(a == null ? null : a) === JSON.stringify(b == null ? null : b); }

  function diff(a, b) {
    a = a || { criteria: [] }; b = b || { criteria: [] };
    var top = TOP_FIELDS.filter(function (k) { return !same(a[k], b[k]); }).map(function (k) { return { field: k, before: a[k], after: b[k] }; });
    var ai = {}, bi = {};
    (a.criteria || []).forEach(function (c) { ai[c.id] = c; });
    (b.criteria || []).forEach(function (c) { bi[c.id] = c; });
    var ids = (b.criteria || []).map(function (c) { return c.id; });
    (a.criteria || []).forEach(function (c) { if (!bi[c.id]) ids.push(c.id); });
    var criteria = [];
    ids.forEach(function (id) {
      var x = ai[id], y = bi[id];
      if (!x) { criteria.push({ id: id, title: y.title, status: 'added', fields: [] }); return; }
      if (!y) { criteria.push({ id: id, title: x.title, status: 'removed', fields: [] }); return; }
      var keys = CRIT_ORDER.slice();
      Object.keys(x).concat(Object.keys(y)).forEach(function (k) { if (keys.indexOf(k) < 0 && k !== 'id') keys.push(k); });
      var fields = keys.filter(function (k) { return !same(x[k], y[k]); }).map(function (k) { return { field: k, before: x[k], after: y[k] }; });
      if (fields.length) criteria.push({ id: id, title: y.title, status: 'changed', fields: fields });
    });
    return { top: top, criteria: criteria };
  }

  /** id критериев, которые нужно переоценить при применении b вместо a (изменённые, добавленные и включённые). */
  function changedIds(a, b) {
    return diff(a, b).criteria.filter(function (c) { return c.status !== 'removed'; }).map(function (c) { return c.id; });
  }

  // ------------------------------------------------------------ отображение значений
  function itemText(field, v) {
    if (v == null) return '—';
    if (field === 'scale') return v.value + ' — ' + (v.text || '');
    if (field === 'examples') return U.verdictLabel(v.verdict) + ' · ' + (v.fragment || '') + (v.why ? ' — ' + v.why : '');
    if (field === 'sections') return v.id + ': ' + v.title;
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  function scalarText(field, v) {
    if (v == null || v === '') return h('span', { class: 'muted' }, '—');
    if (typeof v === 'boolean') return v ? t('common.yes') : t('common.no');
    if (field === 'type') return t('ctype.' + v);
    if (field === 'channels') return v.map(U.channelLabel).join(', ');
    if (field === 'evidence_roles') return v.map(function (r) { return t('role.' + r); }).join(', ');
    if (typeof v === 'object' && !Array.isArray(v)) {
      return h('div', { class: 'stack' }, Object.keys(v).map(function (k) { return h('div', null, h('b', null, k + ': '), h('span', { class: 'pre-wrap' }, typeof v[k] === 'object' ? JSON.stringify(v[k]) : String(v[k]))); }));
    }
    return h('span', { class: 'pre-wrap' }, String(v));
  }

  function renderChange(f) {
    var label = CQA.i18n.tx('field.' + f.field, f.field);
    var isList = Array.isArray(f.before) || Array.isArray(f.after);
    if (isList && ['channels', 'evidence_roles'].indexOf(f.field) < 0) {
      var before = (f.before || []).map(function (x) { return itemText(f.field, x); });
      var after = (f.after || []).map(function (x) { return itemText(f.field, x); });
      var removed = before.filter(function (x) { return after.indexOf(x) < 0; });
      var added = after.filter(function (x) { return before.indexOf(x) < 0; });
      var kept = after.length - added.length;
      return h('div', { class: 'diff-field' },
        h('div', { class: 'diff-field__name' }, label),
        h('ul', { class: 'diff-list' },
          removed.map(function (x) { return h('li', { class: 'diff-del' }, h('span', { class: 'diff-sign', 'aria-label': t('diff.removed') }, '−'), h('span', null, x)); }),
          added.map(function (x) { return h('li', { class: 'diff-add' }, h('span', { class: 'diff-sign', 'aria-label': t('diff.added') }, '+'), h('span', null, x)); }),
          !removed.length && !added.length ? h('li', { class: 'muted' }, t('diff.reordered')) : null),
        kept > 0 ? h('div', { class: 'muted t-caption1' }, t('diff.unchangedItems', { n: kept })) : null);
    }
    return h('div', { class: 'diff-field' },
      h('div', { class: 'diff-field__name' }, label),
      h('div', { class: 'diff-pair' },
        h('div', { class: 'diff-before' }, h('span', { class: 'diff-tag' }, t('diff.before')), scalarText(f.field, f.before)),
        h('div', { class: 'diff-after' }, h('span', { class: 'diff-tag' }, t('diff.after')), scalarText(f.field, f.after))));
  }

  class CqaChecklistDiff extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d;
      this.textContent = '';
      this.classList.add('cl-diff');
      var res = diff(d.a.config, d.b.config);
      var counts = { added: 0, removed: 0, changed: 0 };
      res.criteria.forEach(function (c) { counts[c.status] += 1; });
      this.appendChild(h('div', { class: 'row diff-summary' },
        h('span', { class: 't-body1-strong' }, t('diff.summary', { a: d.a.version, b: d.b.version })),
        counts.changed ? CQA.ui.badge(t('diff.nChanged', { n: counts.changed }), 'warning') : null,
        counts.added ? CQA.ui.badge(t('diff.nAdded', { n: counts.added }), 'success') : null,
        counts.removed ? CQA.ui.badge(t('diff.nRemoved', { n: counts.removed }), 'danger') : null,
        res.top.length ? CQA.ui.badge(t('diff.nTop', { n: res.top.length }), 'brand') : null));
      if (!res.top.length && !res.criteria.length) {
        this.appendChild(CQA.ui.empty(t('diff.same'), null, null, 'checkCircle'));
        return;
      }
      var self = this;
      if (res.top.length) {
        this.appendChild(h('section', { class: 'card diff-card' },
          h('h3', { class: 'card__title' }, t('diff.general')),
          res.top.map(renderChange)));
      }
      res.criteria.forEach(function (c) {
        self.appendChild(h('section', { class: 'card diff-card diff-card--' + c.status },
          h('div', { class: 'row' },
            h('span', { class: 'ced__id mono' }, c.id),
            h('h3', { class: 'card__title' }, c.title),
            CQA.ui.badge(t('diff.status.' + c.status), c.status === 'added' ? 'success' : c.status === 'removed' ? 'danger' : 'warning'),
            c.status === 'changed' ? h('span', { class: 'muted t-caption1' }, c.fields.map(function (f) { return CQA.i18n.tx('field.' + f.field, f.field); }).join(', ')) : null),
          c.fields.map(renderChange)));
      });
    }
  }

  CQA.checklistDiff = diff;
  CQA.checklistChangedIds = changedIds;
  customElements.define('cqa-checklist-diff', CqaChecklistDiff);
})(window.CQA = window.CQA || {});
