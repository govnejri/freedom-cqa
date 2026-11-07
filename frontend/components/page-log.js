/* <cqa-page-log> — журнал оценки #/log/{evaluation_id}?call=720: все вызовы модели по оценке (цель, критерий, модель,
   кэш, статус, токены, стоимость, задержка), итог по стоимости; по клику — запрос (system, user) и сырой ответ модели. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  var PURPOSE_TONE = { judge: 'brand', retry: 'warning', cross_check: 'mid', signals: 'success', second_opinion: 'brand', ping: 'neutral' };
  var GROUPS = [
    { key: 'all', match: null },
    { key: 'judge', match: { judge: 1, retry: 1 } },
    { key: 'cross_check', match: { cross_check: 1 } },
    { key: 'signals', match: { signals: 1 } },
    { key: 'second_opinion', match: { second_opinion: 1 } }
  ];

  function purposeLabel(p) { return CQA.i18n.tx('purpose.' + p, p); }
  function sum(list, key) { return list.reduce(function (a, c) { return a + (Number(c[key]) || 0); }, 0); }
  /** Потрачено: вызовы из кэша не оплачиваются (их цена — исходного вызова). */
  function spent(list) { return list.reduce(function (a, c) { return a + (c.cache_hit ? 0 : Number(c.cost_usd) || 0); }, 0); }

  class CqaPageLog extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.eid = Number(this.params.id);
      this.filter = 'all';
      document.title = t('log.title', { id: this.eid }) + ' · CQA';
      this.root = h('div', { class: 'page log-page' }, ui.skeleton('card', 6));
      this.textContent = '';
      this.appendChild(this.root);
      this.load();
    }

    disconnectedCallback() {
      // окно вызова живёт в body — закрываем его при уходе со страницы
      if (this.modal) { var m = this.modal; this.modal = null; m.close('replace'); }
    }

    update(params, query) {
      if (Number(params.id) !== this.eid) return false;
      this.query = query;
      if (this.calls && query.call && (!this.modal || this.openCall !== Number(query.call))) this.showCall(Number(query.call));
      return true;
    }

    load() {
      var self = this;
      CQA.dom.replace(this.root, ui.skeleton('card', 6));
      Promise.all([CQA.api.evaluationById(this.eid), CQA.api.evaluationCalls(this.eid)]).then(function (res) {
        self.ev = res[0];
        self.calls = res[1] || [];
        return CQA.api.evaluations(self.ev.uid).catch(function () { return []; });
      }).then(function (history) {
        self.history = history || [];
        self.render();
        if (self.query && self.query.call) self.showCall(Number(self.query.call));
      }, function (err) {
        CQA.dom.replace(self.root,
          h('a', { class: 'back-link', href: '#/conversations' }, icon('arrowLeft', 'icon--16'), t('nav.conversations')),
          ui.error(err, function () { self.load(); }, err.status === 404 ? t('log.notFound') : null));
      });
    }

    render() {
      var self = this, ev = this.ev, calls = this.calls;
      var ext = CQA.fmt.externalId(ev.uid);
      var cached = calls.filter(function (c) { return c.cache_hit; });
      var paid = calls.filter(function (c) { return !c.cache_hit; });
      var cost = spent(calls);

      // разбивка по цели
      var byPurpose = {};
      calls.forEach(function (c) {
        var b = byPurpose[c.purpose] || (byPurpose[c.purpose] = { n: 0, cost: 0, cached: 0 });
        b.n += 1; if (c.cache_hit) b.cached += 1; else b.cost += Number(c.cost_usd) || 0;
      });
      var purposeChips = h('div', { class: 'log-purposes' }, Object.keys(byPurpose).map(function (p) {
        var b = byPurpose[p];
        return h('span', { class: 'log-purpose' }, ui.badge(purposeLabel(p), PURPOSE_TONE[p] || 'neutral'),
          h('span', { class: 'num' }, t('log.purposeLine', { n: b.n, c: fmt.money(b.cost) })),
          b.cached ? h('span', { class: 'muted t-caption1' }, t('log.purposeCached', { n: b.cached })) : null);
      }));

      // критерии, у которых нет вызова судьи в этой оценке: вердикт перенесён из прошлой оценки
      var judged = {};
      calls.forEach(function (c) { if ((c.purpose === 'judge' || c.purpose === 'retry') && c.criterion_id) judged[c.criterion_id] = 1; });
      var carried = ev.criteria.filter(function (c) { return c.status === 'done' && !judged[c.criterion_id]; }).map(function (c) { return c.criterion_id; });
      var others = this.history.filter(function (e) { return e.id !== ev.id; });
      var carriedNote = carried.length ? ui.msgbar('info', t('log.carriedTitle', { list: carried.join(', ') }),
        h('span', null, t('log.carriedText'), others.length ? [' ', t('log.otherEvals'), ' ', others.slice(0, 6).map(function (e, i) {
          return [i ? ', ' : '', h('a', { href: '#/log/' + e.id }, '#' + e.id), h('span', { class: 'muted' }, ' (v' + e.checklist_version + ', ' + fmt.date(e.created_at) + ')')];
        })] : null)) : null;

      this.tableBox = h('div');
      this.filterBar = h('div', { class: 'segmented', role: 'group', 'aria-label': t('log.filter') });
      GROUPS.forEach(function (g) {
        var n = g.match ? calls.filter(function (c) { return g.match[c.purpose]; }).length : calls.length;
        if (g.match && !n) return;
        self.filterBar.appendChild(h('button', { type: 'button', 'aria-pressed': String(self.filter === g.key), 'data-key': g.key,
          onClick: function () { self.filter = g.key; self.renderTable(); } }, t('log.f.' + g.key) + ' · ' + n));
      });

      var stat = function (label, value, note) {
        return h('div', { class: 'run-stat' }, h('div', { class: 'run-stat__label' }, label), h('div', { class: 'run-stat__value num' }, value),
          note ? h('div', { class: 'muted t-caption1' }, note) : null);
      };
      CQA.dom.replace(this.root,
        h('a', { class: 'back-link', href: '#/c/' + encodeURIComponent(ev.uid) }, icon('arrowLeft', 'icon--16'), t('log.back', { id: ext })),
        h('div', { class: 'page-header' },
          h('div', null,
            h('h1', null, t('log.title', { id: ev.id })),
            h('div', { class: 'page-header__sub' },
              h('a', { href: '#/c/' + encodeURIComponent(ev.uid) }, ext), ' · ', t('eval.checklistVersion', { v: ev.checklist_version }), ' · ',
              ev.model, ' · ', t('log.prompt', { v: ev.prompt_version }), ' · ', fmt.date(ev.created_at)))),
        h('div', { class: 'stack stack--l' },
          ui.msgbar('info', t('log.introTitle'), t('log.intro')),
          CQA.api.mode === 'mock' ? ui.msgbar('warning', null, t('mock.logNote')) : null,
          h('section', { class: 'card' },
            h('div', { class: 'run-stats' },
              stat(t('log.sCalls'), fmt.int(calls.length), cached.length ? t('log.sCached', { n: cached.length }) : null),
              stat(t('log.sCost'), fmt.money(cost), t('log.sCostNote')),
              stat(t('log.sTokensIn'), fmt.int(sum(paid, 'prompt_tokens')), t('log.sTokensCached', { n: fmt.int(sum(paid, 'cached_tokens')) })),
              stat(t('log.sTokensOut'), fmt.int(sum(paid, 'completion_tokens')), t('log.sReasoning', { n: fmt.int(sum(paid, 'reasoning_tokens')) })),
              stat(t('log.sLatency'), fmt.ms(sum(paid, 'latency_ms')), t('log.sLatencyNote'))),
            h('div', { class: 'log-purposes-wrap' }, purposeChips)),
          carriedNote,
          h('section', { class: 'card' },
            h('div', { class: 'card__header' },
              h('h2', { class: 'card__title' }, t('log.callsTitle')),
              h('div', { class: 'segmented-scroll' }, this.filterBar)),
            this.tableBox)));
      this.renderTable();
    }

    renderTable() {
      var self = this;
      var g = GROUPS.filter(function (x) { return x.key === self.filter; })[0] || GROUPS[0];
      var list = this.calls.filter(function (c) { return !g.match || g.match[c.purpose]; });
      Array.prototype.forEach.call(this.filterBar.children, function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-key') === self.filter)); });
      if (!list.length) { CQA.dom.replace(this.tableBox, h('p', { class: 'muted' }, t('log.empty'))); return; }
      var tbody = h('tbody');
      list.forEach(function (c) {
        var ok = c.status === 'ok';
        var tr = h('tr', { class: 'is-clickable' + (c.cache_hit ? ' is-cached' : ''), tabindex: '0', 'aria-label': t('log.openCall', { id: c.id }) },
          h('td', { class: 'nowrap num' }, fmt.time(c.ts)),
          h('td', null, ui.badge(purposeLabel(c.purpose), PURPOSE_TONE[c.purpose] || 'neutral'), c.attempt > 1 ? h('span', { class: 'muted t-caption1' }, ' ' + t('log.attempt', { n: c.attempt })) : null),
          h('td', { class: 'mono' }, c.criterion_id || '—'),
          h('td', { class: 'nowrap' }, c.model, h('div', { class: 'muted t-caption1' }, c.profile)),
          h('td', null, c.cache_hit ? ui.badge(t('log.cacheYes'), 'neutral', 'database') : h('span', { class: 'muted' }, t('log.cacheNo'))),
          h('td', null, ui.badge(CQA.i18n.tx('callStatus.' + c.status, c.status), ok ? 'success' : 'danger')),
          h('td', { class: 'num' }, fmt.int(c.prompt_tokens)),
          h('td', { class: 'num muted' }, fmt.int(c.cached_tokens)),
          h('td', { class: 'num' }, fmt.int(c.completion_tokens)),
          h('td', { class: 'num muted' }, fmt.int(c.reasoning_tokens)),
          h('td', { class: 'num nowrap' }, c.cache_hit ? h('s', { class: 'muted', title: t('log.cachedCostTitle') }, fmt.money(c.cost_usd)) : fmt.money(c.cost_usd)),
          h('td', { class: 'num nowrap' }, c.cache_hit ? h('span', { class: 'muted' }, '—') : fmt.ms(c.latency_ms)));
        var open = function () { self.showCall(c.id); };
        tr.addEventListener('click', open);
        tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
        tbody.appendChild(tr);
      });
      var paid = list.filter(function (c) { return !c.cache_hit; });
      CQA.dom.replace(this.tableBox, h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact log-table' },
        h('thead', null,
          h('tr', null,
            h('th', { scope: 'col', rowspan: '2' }, t('log.cTime')), h('th', { scope: 'col', rowspan: '2' }, t('log.cPurpose')),
            h('th', { scope: 'col', rowspan: '2' }, t('log.cCriterion')), h('th', { scope: 'col', rowspan: '2' }, t('log.cModel')),
            h('th', { scope: 'col', rowspan: '2' }, t('log.cCache')), h('th', { scope: 'col', rowspan: '2' }, t('log.cStatus')),
            h('th', { scope: 'colgroup', colspan: '4', class: 'log-th-group' }, t('log.cTokens')),
            h('th', { scope: 'col', rowspan: '2', class: 'num' }, t('log.cCost')), h('th', { scope: 'col', rowspan: '2', class: 'num' }, t('log.cLatency'))),
          h('tr', null,
            h('th', { scope: 'col', class: 'num' }, t('log.tIn')), h('th', { scope: 'col', class: 'num' }, t('log.tCached')),
            h('th', { scope: 'col', class: 'num' }, t('log.tOut')), h('th', { scope: 'col', class: 'num' }, t('log.tReasoning')))),
        tbody,
        h('tfoot', null, h('tr', null,
          h('td', { colspan: '6' }, h('b', null, t('log.total', { n: list.length })), list.length !== paid.length ? h('span', { class: 'muted' }, ' · ' + t('log.totalPaid', { n: paid.length })) : null),
          h('td', { class: 'num' }, fmt.int(sum(paid, 'prompt_tokens'))),
          h('td', { class: 'num muted' }, fmt.int(sum(paid, 'cached_tokens'))),
          h('td', { class: 'num' }, fmt.int(sum(paid, 'completion_tokens'))),
          h('td', { class: 'num muted' }, fmt.int(sum(paid, 'reasoning_tokens'))),
          h('td', { class: 'num nowrap' }, h('b', null, fmt.money(spent(list)))),
          h('td', { class: 'num nowrap' }, fmt.ms(sum(paid, 'latency_ms'))))))));
    }

    // ------------------------------------------------------------ детали вызова
    showCall(id) {
      var self = this;
      var meta = this.calls.filter(function (c) { return c.id === id; })[0];
      if (this.modal) this.modal.close('replace');
      var body = h('div', { class: 'stack stack--l log-call' }, ui.skeleton('card', 6));
      this.openCall = id;
      CQA.router.setQuery(Object.assign({}, this.query || {}, { call: id }));
      this.modal = ui.modal({
        title: t('log.callTitle', { id: id, p: meta ? purposeLabel(meta.purpose) : '' }) + (meta && meta.criterion_id ? ' · ' + t('log.criterionN', { id: meta.criterion_id }) : ''),
        wide: true,
        noAutofocus: true,
        body: body,
        onClose: function (reason) {
          if (reason === 'replace') return;
          self.modal = null;
          self.openCall = null;
          var q = Object.assign({}, self.query || {});
          delete q.call;
          self.query = q;
          CQA.router.setQuery(q);
        },
        footer: function (close) { return [h('button', { class: 'btn', type: 'button', onClick: function () { close(); } }, t('action.close'))]; }
      });
      CQA.api.llmCall(id).then(function (c) { self.renderCall(body, c); }, function (err) {
        CQA.dom.replace(body, ui.error(err, function () { self.showCall(id); }));
      });
    }

    renderCall(body, c) {
      var req = c.request && typeof c.request === 'object' ? c.request : null;
      var msgs = req && Array.isArray(req.messages) ? req.messages : [];
      var params = [];
      if (req) ['thinking_level', 'temperature', 'max_output_tokens'].forEach(function (k) { if (req[k] != null) params.push(h('span', { class: 'tag' }, k + ': ' + req[k])); });
      var parsed = null;
      if (c.response_text) { try { parsed = JSON.parse(c.response_text); } catch (e) { parsed = null; } }
      var lastUser = -1;
      msgs.forEach(function (m, i) { if (m.role === 'user') lastUser = i; });
      CQA.dom.replace(body,
        h('dl', { class: 'kv log-kv' },
          h('dt', null, t('log.cTime')), h('dd', null, fmt.date(c.ts) + ' · ' + fmt.time(c.ts)),
          h('dt', null, t('log.cModel')), h('dd', null, c.model + ' · ' + c.profile + ' · ' + c.provider),
          h('dt', null, t('log.cStatus')), h('dd', null, ui.badge(CQA.i18n.tx('callStatus.' + c.status, c.status), c.status === 'ok' ? 'success' : 'danger'),
            c.cache_hit ? [' ', ui.badge(t('log.cacheYes'), 'neutral', 'database')] : null, c.attempt > 1 ? ' ' + t('log.attempt', { n: c.attempt }) : null),
          h('dt', null, t('log.cTokens')), h('dd', { class: 'num' }, t('log.tokensLine', {
            i: fmt.int(c.prompt_tokens), c: fmt.int(c.cached_tokens), o: fmt.int(c.completion_tokens), r: fmt.int(c.reasoning_tokens) })),
          h('dt', null, t('log.cCost')), h('dd', null, fmt.money(c.cost_usd) + (c.cache_hit ? ' · ' + t('log.cachedCostTitle') : '') + ' · ' + fmt.ms(c.latency_ms))),
        h('section', { class: 'stack' },
          h('h3', { class: 'log-h3' }, t('log.request')),
          params.length ? h('div', { class: 'row' }, params) : null,
          msgs.length ? msgs.map(function (m, i) {
            var text = typeof m.content === 'string' ? m.content : JSON.stringify(m.content, null, 2);
            return h('details', { class: 'log-msg', open: i === lastUser },
              h('summary', null, icon('chevronRight', 'icon--16'), h('span', { class: 'log-msg__role log-msg__role--' + m.role }, m.role),
                h('span', { class: 'muted t-caption1' }, t('log.chars', { n: fmt.int(text.length) })),
                h('span', { class: 'log-msg__peek muted t-caption1' }, text.slice(0, 120).replace(/\s+/g, ' '))),
              h('pre', { class: 'code-block' }, text));
          }) : h('p', { class: 'muted' }, t('log.noRequest')),
          req && req.json_schema ? h('details', { class: 'log-msg' },
            h('summary', null, icon('chevronRight', 'icon--16'), h('span', { class: 'log-msg__role' }, 'json_schema'), h('span', { class: 'muted t-caption1' }, t('log.schemaHint'))),
            h('pre', { class: 'code-block' }, JSON.stringify(req.json_schema, null, 2))) : null),
        h('section', { class: 'stack' },
          h('h3', { class: 'log-h3' }, t('log.response')),
          c.response_text ? h('pre', { class: 'code-block code-block--response' }, parsed ? JSON.stringify(parsed, null, 2) : c.response_text)
            : h('p', { class: 'muted' }, t('log.noResponse'))));
    }
  }

  customElements.define('cqa-page-log', CqaPageLog);
})(window.CQA = window.CQA || {});
