/* <cqa-page-slices> — срезы руководителя #/slices?by=language|channel|operator|reason&group=&g=<группа>.
   Таблица-тепловая карта: группа, диалогов, средний балл, грубых ошибок и по каждому критерию доля «Да»
   (шкала — средняя ступень, флаг — доля нарушений). Клик по группе — её диалоги ниже таблицы. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var BY = ['language', 'channel', 'operator', 'reason'];
  var BY_ICON = { language: 'globe', channel: 'phone', operator: 'person', reason: 'question' };
  var LOW_N = 3;

  /** Цвет клетки по качеству 0..1 (для флага — доля диалогов без нарушения). */
  function heat(q) {
    if (q == null) return 'none';
    if (q >= 0.95) return 'h5';
    if (q >= 0.85) return 'h4';
    if (q >= 0.7) return 'h3';
    if (q >= 0.5) return 'h2';
    return 'h1';
  }

  function groupLabel(by, key) {
    if (by === 'channel') return U.channelLabel(key);
    if (by === 'operator' && key === 'не назначен') return t('sl.noOperator');
    if (by === 'reason' && key === 'нет сигналов') return t('sl.noSignals');
    key = String(key == null ? '' : key);
    return by === 'reason' ? key.charAt(0).toUpperCase() + key.slice(1) : key;
  }

  class CqaPageSlices extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.slices') + ' · CQA';
      var q = this.query || {};
      this.by = BY.indexOf(q.by) >= 0 ? q.by : 'language';
      this.group = q.group && q.group !== 'all' ? q.group : null;
      this.sel = q.g || null;
      this.sort = '-n_dialogs';
      var self = this;
      this.seg = h('div', { class: 'segmented segmented--l', role: 'radiogroup', 'aria-label': t('sl.by') });
      this.segBtns = {};
      BY.forEach(function (b) {
        var btn = h('button', { type: 'button', role: 'radio', 'aria-checked': 'false', onClick: function () { self.setBy(b); } }, icon(BY_ICON[b], 'icon--16'), t('sl.by.' + b));
        self.segBtns[b] = btn;
        self.seg.appendChild(btn);
      });
      this.groupSel = h('select', { class: 'select select--small', 'aria-label': t('dash.group') }, h('option', { value: '' }, t('dash.allGroups')));
      this.groupSel.addEventListener('change', function () { self.group = self.groupSel.value || null; self.sel = null; self.syncUrl(); self.load(); });
      this.box = h('div', { class: 'stack stack--l' }, ui.skeleton('table', 8));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page sl-page' },
        CQA.subnav('team', 'slices'),
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('team.slicesTitle')), h('div', { class: 'page-header__sub' }, t('sl.sub'))),
          h('div', { class: 'row' }, h('label', { class: 'muted t-caption1' }, t('dash.group')), this.groupSel)),
        h('div', { class: 'row sl-controls' }, h('div', { class: 'segmented-scroll' }, this.seg)),
        this.box));
      this.syncSeg();
      CQA.api.datasets().then(function (list) {
        (list || []).filter(function (d) { return d.group_id !== 'AUTOTEST' && d.group_id !== 'TRAINER'; }).forEach(function (d) {
          self.groupSel.appendChild(h('option', { value: d.group_id }, d.group_id));
        });
        self.groupSel.value = self.group || '';
      }).catch(function () { /* без списка наборов */ });
      this.load();
    }

    update(params, query) {
      var by = BY.indexOf(query.by) >= 0 ? query.by : 'language';
      var g = query.group && query.group !== 'all' ? query.group : null;
      var reload = by !== this.by || g !== this.group;
      this.by = by; this.group = g; this.sel = query.g || null;
      this.syncSeg();
      if (reload) this.load(); else this.render();
      return true;
    }

    setBy(b) {
      if (b === this.by) return;
      this.by = b;
      this.sel = null;
      this.syncSeg();
      this.syncUrl();
      this.load();
    }

    syncSeg() {
      var self = this;
      BY.forEach(function (b) { self.segBtns[b].setAttribute('aria-checked', String(b === self.by)); });
    }

    syncUrl() { CQA.router.setQuery({ by: this.by !== 'language' ? this.by : null, group: this.group, g: this.sel }); }

    load() {
      var self = this, by = this.by, seq = (this._seq = (this._seq || 0) + 1);
      if (!this.data) CQA.dom.replace(this.box, ui.skeleton('table', 8));
      else this.box.classList.add('is-loading');
      Promise.all([CQA.api.slices(by, this.group), this.convs ? Promise.resolve(this.convs) : CQA.api.conversations({}).catch(function () { return []; })]).then(function (res) {
        if (seq !== self._seq) return;
        self.box.classList.remove('is-loading');
        self.data = res[0];
        self.convs = res[1];
        self.render();
      }, function (err) {
        if (seq !== self._seq) return;
        self.box.classList.remove('is-loading');
        CQA.dom.replace(self.box, ui.error(err, function () { self.load(); }));
      });
    }

    rows() {
      var key = this.sort.replace(/^-/, ''), dir = this.sort.charAt(0) === '-' ? -1 : 1, by = this.by;
      return this.data.slices.slice().sort(function (a, b) {
        var x = key === 'key' ? groupLabel(by, a.key) : a[key], y = key === 'key' ? groupLabel(by, b.key) : b[key];
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        if (typeof x === 'number') return (x - y) * dir || b.n_dialogs - a.n_dialogs;
        return String(x).localeCompare(String(y), 'ru') * dir;
      });
    }

    render() {
      var self = this, d = this.data, by = d.by;
      var crits = d.criteria;
      var sortTh = function (key, label, num) {
        var k = self.sort.replace(/^-/, ''), desc = self.sort.charAt(0) === '-';
        var state = k === key ? (desc ? 'descending' : 'ascending') : 'none';
        return h('th', { scope: 'col', class: num ? 'num' : null, 'aria-sort': state },
          h('button', { class: 'sort-btn', type: 'button', onClick: function () {
            self.sort = k === key ? (desc ? key : '-' + key) : (num ? '-' + key : key);
            self.render();
          } }, label, icon(state === 'descending' ? 'arrowDown' : state === 'ascending' ? 'arrowUp' : 'arrowSort')));
      };
      var thead = h('tr', null,
        sortTh('key', t('sl.by.' + by)),
        sortTh('n_dialogs', t('sl.cDialogs'), true),
        sortTh('avg_total', t('sl.cAvg'), true),
        sortTh('gross_count', t('sl.cGross'), true),
        crits.map(function (c) {
          return h('th', { scope: 'col', class: 'sl-crit-th', title: c.id + ' ' + c.title + ' · ' + t('ctype.' + c.type) },
            h('span', { class: 'sl-crit-th__id mono' }, c.id), h('span', { class: 'sl-crit-th__title' }, c.title));
        }));
      var tbody = h('tbody');
      tbody.appendChild(this.row(d.overall, crits, true));
      this.rows().forEach(function (r) { tbody.appendChild(self.row(r, crits, false)); });

      var detail = h('div');
      CQA.dom.replace(this.box,
        h('div', { class: 'sl-legend t-caption1' },
          h('span', { class: 'muted' }, t('sl.legend')),
          h('span', { class: 'sl-scale', 'aria-hidden': 'true' }, ['h1', 'h2', 'h3', 'h4', 'h5'].map(function (x) { return h('i', { class: 'heat--' + x }); })),
          h('span', { class: 'muted' }, t('sl.legendScale')),
          h('span', { class: 'sl-legend__low' }, h('i', { class: 'heat--h4 is-low' }), t('sl.legendLow', { n: LOW_N }))),
        h('div', { class: 'table-wrap sl-wrap' }, h('table', { class: 'table table--compact sl-table', 'aria-label': t('sl.tableAria', { by: t('sl.by.' + by) }) },
          h('thead', null, thead), tbody)),
        h('p', { class: 'muted t-caption1' }, t('sl.foot')),
        detail);
      this.detail = detail;
      if (this.sel) this.renderDetail();
    }

    row(r, crits, overall) {
      var self = this, by = this.data.by;
      var selected = !overall && this.sel === r.key;
      var label = overall ? t('sl.overall') : groupLabel(by, r.key);
      var cells = crits.map(function (c) {
        var st = r.criteria[c.id] || {};
        if (!st.n_applicable) return h('td', { class: 'sl-cell heat--none', title: t('sl.cellNa', { n: st.n || 0 }) }, '—');
        var val, tip;
        if (c.type === 'scale') { val = fmt.num(st.mean_step, 1); tip = t('sl.tipScale', { v: fmt.num(st.mean_step, 2), top: c.top, n: st.n_applicable }); }
        else if (c.type === 'flag') { val = st.n_yes ? String(st.n_yes) : '0'; tip = t('sl.tipFlag', { v: st.n_yes, n: st.n_applicable }); }
        else { val = fmt.pct(st.yes_share); tip = t('sl.tipYes', { v: st.n_yes, n: st.n_applicable }); }
        if (st.n_na) tip += ' · ' + t('op.naN', { n: st.n_na });
        var low = st.n_applicable < LOW_N;
        return h('td', { class: 'sl-cell heat--' + heat(st.quality) + (low ? ' is-low' : '') + (c.type === 'flag' && st.n_yes ? ' is-flag' : ''), title: tip }, val);
      });
      var gross = r.gross_count ? h('span', { class: 'reasons__gross' }, String(r.gross_count)) : h('span', { class: 'muted' }, '0');
      var tr = h('tr', { class: (overall ? 'sl-overall' : 'is-clickable') + (selected ? ' is-selected' : ''), tabindex: overall ? null : '0',
        'aria-expanded': overall ? null : String(selected) },
        h('th', { scope: 'row', class: 'sl-group' }, overall ? h('b', null, label) : h('span', { class: 'row row--nowrap' },
          icon(selected ? 'chevronDown' : 'chevronRight', 'icon--16 muted'), h('span', { class: 'sl-group__name' }, label))),
        h('td', { class: 'num' }, String(r.n_dialogs)),
        h('td', { class: 'num' }, CQA.score(r.avg_total)),
        h('td', { class: 'num' }, gross),
        cells);
      if (!overall) {
        var pick = function () {
          self.sel = selected ? null : r.key;
          self.syncUrl();
          self.render();
          if (self.sel) CQA.dom.scrollIntoPane(self.detail, { block: 'start' });
        };
        tr.addEventListener('click', pick);
        tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
      }
      return tr;
    }

    /** Диалоги выбранной группы + переход в список диалогов с тем же фильтром, где он есть. */
    renderDetail() {
      var self = this, by = this.data.by;
      var r = this.data.slices.filter(function (x) { return x.key === self.sel; })[0];
      if (!r) { CQA.dom.clear(this.detail); return; }
      var byUid = {};
      (this.convs || []).forEach(function (s) { byUid[s.uid] = s; });
      var link = null;
      if (by === 'language') link = CQA.router.href('/conversations', { lang: r.key, group: this.group });
      else if (by === 'channel') link = CQA.router.href('/conversations', { channel: r.key, group: this.group });
      else if (by === 'operator' && r.key !== 'не назначен') link = CQA.operatorHref(r.key);
      var scoreOf = function (s) { return s.final_total != null ? s.final_total : s.total; };
      var items = r.uids.map(function (uid) { return byUid[uid] || { uid: uid, external_id: fmt.externalId(uid) }; });
      items.sort(function (a, b) { var x = scoreOf(a), y = scoreOf(b); return (x == null ? 101 : x) - (y == null ? 101 : y); });
      var rows = items.map(function (s) {
        var uid = s.uid;
        var score = scoreOf(s);
        var m = [];
        if (s.gross) m.push(ui.badge(t('badge.gross'), 'danger-filled', 'warning'));
        if (s.needs_review) m.push(ui.badge(t('badge.review'), 'warning', 'eye'));
        if (s.open_appeals) m.push(ui.badge(t('badge.appeal', { n: s.open_appeals }), 'brand', 'scale'));
        var href = CQA.convHref(uid);
        var tr = h('tr', { class: 'is-clickable' },
          h('td', null, h('a', { class: 'row-link mono', href: href }, s.external_id)),
          h('td', null, s.language ? ui.badge(s.language, 'neutral') : null),
          h('td', null, s.channel ? h('span', { class: 'row row--nowrap' }, icon(U.channelIcon(s.channel), 'icon--16 muted'), U.channelLabel(s.channel)) : null),
          h('td', { class: 'col-operator' }, s.operator_name ? h('a', { href: CQA.operatorHref(s.operator_name) }, s.operator_name) : h('span', { class: 'muted' }, '—')),
          h('td', { class: 'num' }, score != null ? CQA.score(score) : '—'),
          h('td', null, h('div', { class: 'row marks' }, m)));
        tr.addEventListener('click', function (e) { if (!e.target.closest('a')) location.hash = href; });
        return tr;
      });
      CQA.dom.replace(this.detail, h('section', { class: 'card sl-detail', 'aria-labelledby': 'sl-detail-title' },
        h('div', { class: 'card__header' },
          h('div', null,
            h('h2', { class: 'card__title', id: 'sl-detail-title' }, t('sl.detailTitle', { g: groupLabel(by, r.key), n: r.n_dialogs }) ),
            h('div', { class: 'muted t-caption1' }, t('sl.detailSub', { a: fmt.score(r.avg_total), g: r.gross_count, rv: r.needs_review, ap: r.n_appeals }))),
          h('div', { class: 'row' },
            link ? h('a', { class: 'btn btn--small', href: link }, icon(by === 'operator' ? 'person' : 'filter'), by === 'operator' ? t('sl.openProfile') : t('sl.openList')) : null,
            h('button', { class: 'btn btn--small btn--subtle', type: 'button', onClick: function () { self.sel = null; self.syncUrl(); self.render(); } }, icon('close', 'icon--16'), t('action.close')))),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact' },
          h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('list.colId')), h('th', { scope: 'col' }, t('list.colLang')),
            h('th', { scope: 'col' }, t('list.colChannel')), h('th', { scope: 'col', class: 'col-operator' }, t('list.colOperator')),
            h('th', { scope: 'col', class: 'num' }, t('list.colTotal')), h('th', { scope: 'col' }, t('list.colMarks')))),
          h('tbody', null, rows)))));
    }
  }

  customElements.define('cqa-page-slices', CqaPageSlices);
})(window.CQA = window.CQA || {});
