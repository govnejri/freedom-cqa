/* <cqa-page-conversations> — список #/conversations?lang=&channel=&score_min=&score_max=&gross=1&review=1&appeal=1&q=&sort=-total
   Фильтры уходят в API (GET /conversations), сортировка — на клиенте (мгновенно), состояние — в URL. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var FILTER_KEYS = ['group', 'lang', 'channel', 'score_min', 'score_max', 'gross', 'review', 'appeal', 'q'];
  var COLUMNS = [
    { key: 'external_id', label: 'list.colId' },
    { key: 'language', label: 'list.colLang' },
    { key: 'channel', label: 'list.colChannel' },
    { key: 'operator_name', label: 'list.colOperator', cls: 'col-operator' },
    { key: 'score', label: 'list.colTotal', num: true },
    { key: null, label: 'list.colMarks' }
  ];

  function scoreOf(s) { return s.final_total != null ? s.final_total : s.total; }

  class CqaPageConversations extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.conversations') + ' · CQA';
      this.filters = {};
      var q = this.query || {};
      FILTER_KEYS.forEach(function (k) { if (q[k]) this.filters[k] = q[k]; }, this);
      this.sort = q.sort || 'external_id';
      this.build();
      this.loadDatasets();
      this.fetch();
    }

    update(params, query) {
      // смена query извне (назад/вперёд в истории) — перечитать фильтры
      var self = this;
      this.filters = {};
      FILTER_KEYS.forEach(function (k) { if (query[k]) self.filters[k] = query[k]; });
      this.sort = query.sort || 'external_id';
      this.syncControls();
      this.fetch();
      return true;
    }

    build() {
      var self = this;
      this.count = h('span', { class: 'muted' });
      this.exportMenu = document.createElement('cqa-export-menu');
      var header = h('div', { class: 'page-header' },
        h('div', null, h('h1', null, t('nav.conversations')), h('div', { class: 'page-header__sub' }, this.count)),
        h('div', { class: 'row' }, this.exportMenu));

      // --- фильтры
      this.ctl = {};
      var c = this.ctl;
      c.q = h('input', { class: 'input', type: 'search', placeholder: t('list.searchPlaceholder'), 'aria-label': t('list.search') });
      c.group = h('select', { class: 'select', 'aria-label': t('list.group'), hidden: true }, h('option', { value: '' }, t('list.allGroups')));
      c.lang = h('select', { class: 'select', 'aria-label': t('list.lang') },
        h('option', { value: '' }, t('list.allLangs')), ['RU', 'KZ', 'EN'].map(function (l) { return h('option', { value: l }, l); }));
      c.channel = h('select', { class: 'select', 'aria-label': t('list.channel') },
        h('option', { value: '' }, t('list.allChannels')),
        h('option', { value: 'agent chat' }, t('channel.chat')),
        h('option', { value: 'voice call' }, t('channel.call')));
      c.score_min = h('input', { class: 'input input--num', type: 'number', min: '0', max: '100', step: '1', placeholder: t('list.from'), 'aria-label': t('list.scoreMin') });
      c.score_max = h('input', { class: 'input input--num', type: 'number', min: '0', max: '100', step: '1', placeholder: t('list.to'), 'aria-label': t('list.scoreMax') });
      c.gross = h('button', { class: 'toggle', type: 'button', 'aria-pressed': 'false' }, icon('warning'), t('list.fGross'));
      c.review = h('button', { class: 'toggle', type: 'button', 'aria-pressed': 'false' }, icon('eye'), t('list.fReview'));
      c.appeal = h('button', { class: 'toggle', type: 'button', 'aria-pressed': 'false' }, icon('scale'), t('list.fAppeal'));
      this.resetBtn = h('button', { class: 'btn btn--subtle', type: 'button', onClick: function () { self.filters = {}; self.syncControls(); self.changed(); } }, icon('close', 'icon--16'), t('list.reset'));

      var debounced = U.debounce(function () { self.changed(); }, 250);
      c.q.addEventListener('input', function () { self.filters.q = c.q.value.trim(); debounced(); });
      ['group', 'lang', 'channel'].forEach(function (k) {
        c[k].addEventListener('change', function () { self.filters[k] = c[k].value; self.changed(); });
      });
      ['score_min', 'score_max'].forEach(function (k) {
        c[k].addEventListener('input', function () { self.filters[k] = c[k].value; debounced(); });
      });
      ['gross', 'review', 'appeal'].forEach(function (k) {
        c[k].addEventListener('click', function () { self.filters[k] = self.filters[k] === '1' ? '' : '1'; self.syncControls(); self.changed(); });
      });

      var filters = h('div', { class: 'filters card', role: 'search', 'aria-label': t('list.filters') },
        h('div', { class: 'input-with-icon filters__search' }, icon('search'), c.q),
        c.group, c.lang, c.channel,
        h('div', { class: 'row row--nowrap filters__range' }, h('span', { class: 'muted' }, t('list.score')), c.score_min, h('span', { class: 'muted' }, '–'), c.score_max),
        c.gross, c.review, c.appeal, this.resetBtn);

      this.tableBox = h('div', { class: 'list-box' });
      this.textContent = '';
      this.appendChild(h('div', { class: 'page' }, header, filters, this.tableBox));
      this.syncControls();
    }

    syncControls() {
      var c = this.ctl, f = this.filters;
      ['q', 'group', 'lang', 'channel', 'score_min', 'score_max'].forEach(function (k) { if (document.activeElement !== c[k]) c[k].value = f[k] || ''; });
      ['gross', 'review', 'appeal'].forEach(function (k) { c[k].setAttribute('aria-pressed', String(f[k] === '1')); });
      var active = FILTER_KEYS.some(function (k) { return f[k]; });
      this.resetBtn.hidden = !active;
    }

    changed() {
      this.syncUrl();
      this.syncControls();
      this.fetch();
    }

    syncUrl() {
      var q = {};
      FILTER_KEYS.forEach(function (k) { if (this.filters[k]) q[k] = this.filters[k]; }, this);
      if (this.sort !== 'external_id') q.sort = this.sort;
      CQA.router.setQuery(q);
    }

    loadDatasets() {
      var self = this;
      CQA.api.datasets().then(function (list) {
        if (!list || list.length < 2) return;
        list.forEach(function (d) { self.ctl.group.appendChild(h('option', { value: d.group_id }, d.group_id + ' (' + d.total_records + ')')); });
        self.ctl.group.hidden = false;
        self.ctl.group.value = self.filters.group || '';
      }).catch(function () { /* фильтр по набору не обязателен */ });
      this.exportMenu.group = this.filters.group || null;
    }

    fetch() {
      var self = this;
      var seq = (this._seq = (this._seq || 0) + 1);
      var q = {};
      FILTER_KEYS.forEach(function (k) { if (self.filters[k]) q[k] = self.filters[k]; });
      // сортируем на клиенте; серверу передаём тот же ключ (score → total) на случай пагинации
      q.sort = this.sort.replace('score', 'total');
      this.exportMenu.group = this.filters.group || null;
      if (!this.rows) CQA.dom.replace(this.tableBox, ui.skeleton('table', 8));
      else this.tableBox.classList.add('is-loading');
      CQA.api.conversations(q).then(function (rows) {
        if (seq !== self._seq) return;
        self.rows = rows;
        self.tableBox.classList.remove('is-loading');
        self.renderTable();
      }, function (err) {
        if (seq !== self._seq) return;
        self.rows = null;
        self.tableBox.classList.remove('is-loading');
        CQA.dom.replace(self.tableBox, ui.error(err, function () { self.fetch(); }));
      });
    }

    sorted() {
      var key = this.sort.replace(/^-/, ''), dir = this.sort.charAt(0) === '-' ? -1 : 1;
      return this.rows.slice().sort(function (a, b) {
        var x = key === 'score' ? scoreOf(a) : a[key], y = key === 'score' ? scoreOf(b) : b[key];
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        if (typeof x === 'number') return (x - y) * dir;
        return String(x).localeCompare(String(y), 'ru', { numeric: true }) * dir;
      });
    }

    renderTable() {
      var self = this;
      var rows = this.rows;
      var total = rows.length;
      this.count.textContent = t('list.count', { n: total });
      if (!total) {
        var anyFilter = FILTER_KEYS.some(function (k) { return self.filters[k]; });
        CQA.dom.replace(this.tableBox, anyFilter
          ? ui.empty(t('list.emptyFiltered'), t('list.emptyFilteredText'), h('button', { class: 'btn', type: 'button', onClick: function () { self.filters = {}; self.syncControls(); self.changed(); } }, t('list.reset')), 'filter')
          : ui.empty(t('list.empty'), t('list.emptyText'), h('a', { class: 'btn btn--primary', href: '#/' }, icon('upload'), t('dash.loadDataset')), 'chat'));
        return;
      }
      var key = this.sort.replace(/^-/, ''), desc = this.sort.charAt(0) === '-';
      var thead = h('tr', null, COLUMNS.map(function (col) {
        if (!col.key) return h('th', { scope: 'col' }, t(col.label));
        var state = key === col.key ? (desc ? 'descending' : 'ascending') : 'none';
        return h('th', { scope: 'col', class: [col.num && 'num', col.cls], 'aria-sort': state },
          h('button', { class: 'sort-btn', type: 'button', onClick: function () {
            self.sort = key === col.key && !desc ? '-' + col.key : (key === col.key ? col.key : (col.num ? '-' + col.key : col.key));
            self.syncUrl();
            self.renderTable();
          } }, t(col.label), icon(state === 'descending' ? 'arrowDown' : state === 'ascending' ? 'arrowUp' : 'arrowSort')));
      }));
      var tbody = h('tbody');
      this.sorted().forEach(function (s) {
        var href = '#/c/' + encodeURIComponent(s.uid);
        var marks = [];
        if (s.gross) marks.push(ui.badge(s.gross_categories && s.gross_categories.length ? t('badge.grossShort', { c: s.gross_categories.join(', ') }) : t('badge.gross'), 'danger-filled', 'warning', { title: t('badge.gross') }));
        if (s.needs_review) marks.push(ui.badge(t('badge.review'), 'warning', 'eye'));
        if (s.open_appeals) marks.push(ui.badge(t('badge.appeal', { n: s.open_appeals }), 'brand', 'scale'));
        if (s.status && s.status !== 'done') marks.push(ui.badge(CQA.i18n.tx('evalStatus.' + s.status, s.status), s.status === 'failed' ? 'danger' : 'neutral'));
        var tr = h('tr', { class: 'is-clickable' },
          h('td', null, h('a', { class: 'row-link', href: href }, s.external_id)),
          h('td', null, ui.badge(s.language, 'neutral')),
          h('td', null, h('span', { class: 'row row--nowrap' }, icon(U.channelIcon(s.channel), 'icon--16 muted'), U.channelLabel(s.channel))),
          h('td', { class: 'col-operator' }, s.operator_name || h('span', { class: 'muted' }, '—')),
          h('td', { class: 'num' }, s.total == null && s.final_total == null
            ? h('span', { class: 'muted t-caption1' }, s.evaluation_id ? t('list.noTotal') : t('list.notEvaluated'))
            : CQA.score(scoreOf(s), s.final_total != null && s.total != null && s.final_total !== s.total ? s.total : null)),
          h('td', null, h('div', { class: 'row marks' }, marks)));
        tr.addEventListener('click', function (e) { if (!e.target.closest('a')) location.hash = href; });
        tbody.appendChild(tr);
      });
      CQA.dom.replace(this.tableBox, h('div', { class: 'table-wrap' },
        h('table', { class: 'table conv-table', 'aria-label': t('list.tableAria') }, h('thead', null, thead), tbody)));
    }
  }

  customElements.define('cqa-page-conversations', CqaPageConversations);
})(window.CQA = window.CQA || {});
