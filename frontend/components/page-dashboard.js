/* <cqa-page-dashboard> — сводка руководителя #/?group=DEV: счётчики, радар рисков, вопросы без ответа по темам,
   причины обращения, действия (загрузить набор, оценить всё, посчитать сигналы), последние прогоны. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  function kpi(label, value, opts) {
    opts = opts || {};
    var tag = opts.href ? 'a' : 'div';
    return h(tag, { class: 'kpi' + (opts.tone ? ' kpi--' + opts.tone : ''), href: opts.href || null },
      h('span', { class: 'kpi__label' }, icon(opts.icon || 'info'), label),
      h('span', { class: 'kpi__value' }, value),
      opts.note ? h('span', { class: 'kpi__note' }, opts.note) : null);
  }

  CQA.runStatusTone = function (s) {
    return { done: 'success', running: 'brand', queued: 'neutral', failed: 'danger', cancelled: 'neutral' }[s] || 'neutral';
  };

  /** Набор по умолчанию: последний загруженный с оценками, не автотесты. */
  CQA.defaultGroup = function (datasets) {
    var list = (datasets || []).filter(function (d) { return d.source !== 'autotest' && d.group_id !== 'AUTOTEST'; });
    list.sort(function (a, b) { return String(b.fetched_at || '').localeCompare(String(a.fetched_at || '')); });
    var withEval = list.filter(function (d) { return d.n_evaluated == null || d.n_evaluated > 0; });
    return (withEval[0] || list[0] || {}).group_id || null;
  };

  class CqaPageDashboard extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.dashboard') + ' · CQA';
      var self = this;
      this.group = (this.query && this.query.group) || null;
      this.groupSel = h('select', { class: 'select select--small dash-group', id: 'dash-group-sel', 'aria-label': t('dash.group') });
      this.groupSel.addEventListener('change', function () {
        self.group = self.groupSel.value || null;
        CQA.router.setQuery({ group: self.group || 'all' });
        self.loadInsights();
      });
      this.kpis = h('div', null, ui.skeleton('tiles', 7));
      this.radar = h('div', null, ui.skeleton('card', 4));
      this.radarSub = h('span', { class: 'muted t-caption1' });
      this.unansBox = h('div', null, ui.skeleton('text', 5));
      this.unansSub = h('span', { class: 'muted t-caption1' });
      this.reasonsBox = h('div', null, ui.skeleton('table', 5));
      this.reasonsSub = h('span', { class: 'muted t-caption1' });
      this.runsBox = h('div', null, ui.skeleton('table', 3));
      this.status = h('div', { class: 'dash-status', 'aria-live': 'polite' });
      this.signalsBtn = h('button', { class: 'btn', type: 'button', onClick: function () { self.runSignals(); } }, icon('sparkle'), t('dash.signals'));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page dash-page' },
        h('div', { class: 'page-header dash-header' },
          h('div', null,
            h('h1', null, t('dash.title')),
            h('div', { class: 'page-header__sub row' }, t('dash.subtitle'),
              h('label', { class: 'dash-group-label', for: 'dash-group-sel' }, t('dash.group')), this.groupSel)),
          h('div', { class: 'row dash-header__actions' },
            h('button', { class: 'btn', type: 'button', onClick: function () { self.openLoad(); } }, icon('download'), t('dash.loadDataset')),
            this.signalsBtn,
            h('button', { class: 'btn btn--primary', type: 'button', onClick: function () { self.openEvaluate(); } }, icon('flash'), t('dash.evaluateAll')))),
        h('div', { class: 'stack stack--xl' },
          this.status,
          this.kpis,
          h('section', { class: 'card radar-card', 'aria-labelledby': 'dash-radar' },
            h('div', { class: 'card__header' },
              h('div', null,
                h('h2', { class: 'card__title radar-card__title', id: 'dash-radar' }, icon('shield'), t('risk.title')),
                this.radarSub),
              h('a', { class: 'btn btn--small', href: '#/risks' }, t('risk.all'), icon('chevronRight'))),
            this.radar),
          h('div', { class: 'dash-insights' },
            h('section', { class: 'card', 'aria-labelledby': 'dash-unans' },
              h('div', { class: 'card__header' },
                h('div', null, h('h2', { class: 'card__title', id: 'dash-unans' }, t('unans.title')), this.unansSub)),
              this.unansBox),
            h('section', { class: 'card', 'aria-labelledby': 'dash-reasons' },
              h('div', { class: 'card__header' },
                h('div', null, h('h2', { class: 'card__title', id: 'dash-reasons' }, t('reasons.title')), this.reasonsSub)),
              this.reasonsBox)),
          h('section', { class: 'card', 'aria-labelledby': 'dash-runs' },
            h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'dash-runs' }, t('dash.runsTitle'))),
            this.runsBox))));
      this.addEventListener('cqa-compliance-changed', function () { self.loadSummary(); self.renderRadarSub(); });
      this.init();
      this.loadRuns();
    }

    disconnectedCallback() { clearTimeout(this.sigTimer); this.stopped = true; }

    update(params, query) {
      // смена ?group= из адресной строки
      var q = (query && query.group) || null;
      var g = q === 'all' ? null : q || this.group;
      if (g !== this.group && this.datasets) { this.group = g; this.fillGroups(); this.loadInsights(); }
      this.query = query;
      return true;
    }

    init() {
      var self = this;
      CQA.api.datasets().catch(function () { return []; }).then(function (list) {
        self.datasets = list || [];
        if (self.query && self.query.group === 'all') self.group = null;
        else if (!self.group) self.group = CQA.defaultGroup(self.datasets) || (CQA.api.launchData && CQA.api.launchData.group) || null;
        self.fillGroups();
        self.loadInsights();
      });
    }

    fillGroups() {
      var sel = this.groupSel;
      CQA.dom.clear(sel);
      sel.appendChild(h('option', { value: '' }, t('dash.allGroups')));
      var self = this;
      (this.datasets || []).forEach(function (d) {
        sel.appendChild(h('option', { value: d.group_id, selected: d.group_id === self.group }, d.group_id + ' · ' + t('dash.records', { n: d.n_conversations != null ? d.n_conversations : d.total_records })));
      });
      sel.value = this.group || '';
    }

    loadInsights() {
      this.loadSummary();
      this.loadRisks();
      this.loadUnanswered();
      this.loadReasons();
    }

    loadSummary() {
      var self = this, g = this.group;
      ui.load(this.kpis, ui.skeleton('tiles', 7), function () { return CQA.api.insightsSummary(g); }, function (s) {
        self.summary = s;
        var top = (s.top_unanswered_topics || [])[0];
        self.kpis.appendChild(h('div', { class: 'kpis kpis--dash' },
          kpi(t('dash.kEvaluated'), fmt.num(s.n_evaluated, 0), { icon: 'checkCircle', href: '#/conversations' + (g ? '?group=' + encodeURIComponent(g) : ''),
            note: t('dash.kEvaluatedOf', { n: s.n_conversations }) }),
          kpi(t('dash.kAverage'), s.avg_total == null ? '—' : fmt.score(s.avg_total), { icon: 'checklist', tone: 'brand', note: t('dash.kAverageNote') }),
          kpi(t('dash.kGross'), fmt.num(s.gross_count, 0), { icon: 'shield', tone: s.gross_count ? 'danger' : null, href: '#/risks',
            note: s.risks_open_compliance ? t('dash.kGrossSent', { n: s.risks_open_compliance }) : t('dash.kGrossNote') }),
          kpi(t('dash.kUnanswered'), fmt.num(s.unanswered_questions, 0), { icon: 'question', tone: s.unanswered_questions ? 'warning' : null,
            note: top ? t('dash.kUnansweredTop', { topic: top.topic, n: top.n_questions }) : t('dash.kSignals', { n: s.n_with_signals, total: s.n_evaluated }) }),
          kpi(t('dash.kReview'), fmt.num(s.needs_review, 0), { icon: 'eye', tone: s.needs_review ? 'warning' : null, href: '#/conversations?review=1', note: t('dash.kReviewNote') }),
          kpi(t('dash.kAppeals'), fmt.num(s.open_appeals, 0), { icon: 'scale', tone: s.open_appeals ? 'warning' : null, href: '#/appeals?status=open', note: t('dash.kAppealsNote') })));
        self.signalsBtn.title = t('dash.signalsTitle', { n: s.n_with_signals, total: s.n_evaluated });
      });
    }

    loadRisks() {
      var self = this, g = this.group;
      ui.load(this.radar, ui.skeleton('card', 4), function () { return CQA.api.risks(g); }, function (r) {
        self.risksData = r;
        self.renderRadarSub();
        var el = document.createElement('cqa-risk-radar');
        el.data = { risks: r, limit: 3 };
        self.radar.appendChild(el);
      });
    }

    /** «N грубых ошибок в M диалогах · передано в комплаенс: K» — элементы радара меняют compliance на месте. */
    renderRadarSub() {
      var r = this.risksData;
      if (!r) return;
      var sent = r.items.filter(function (it) { return it.compliance; }).length;
      this.radarSub.textContent = r.total ? t('risk.sub', { n: r.total, c: r.n_conversations, s: sent }) : '';
    }

    loadUnanswered() {
      var self = this, g = this.group;
      ui.load(this.unansBox, ui.skeleton('text', 5), function () { return CQA.api.unanswered(g); }, function (d) {
        self.unansSub.textContent = d.n_with_signals ? t('unans.sub', { q: d.total_questions, c: d.n_conversations, s: d.n_with_signals, e: d.n_evaluated }) : '';
        var el = document.createElement('cqa-unanswered');
        el.data = d;
        self.unansBox.appendChild(el);
      });
    }

    loadReasons() {
      var self = this, g = this.group;
      ui.load(this.reasonsBox, ui.skeleton('table', 5), function () { return CQA.api.reasons(g); }, function (d) {
        self.reasonsSub.textContent = d.n_with_signals ? t('reasons.sub', { n: d.n_with_signals }) : '';
        var el = document.createElement('cqa-reasons');
        el.data = d;
        self.reasonsBox.appendChild(el);
      });
    }

    // ------------------------------------------------------------ сигналы
    runSignals() {
      var self = this, btn = this.signalsBtn;
      btn.disabled = true;
      CQA.dom.replace(btn, ui.spinner(), t('dash.signalsStarting'));
      CQA.api.runSignals({ group: this.group }).then(function (r) {
        if (r.status === 'nothing_to_do') {
          CQA.dom.replace(self.status, ui.msgbar('info', t('dash.signalsNothing', { n: r.already })));
          self.signalsDone();
          return;
        }
        CQA.dom.replace(self.status, ui.msgbar('info', h('span', { class: 'row' }, ui.spinner(), t('dash.signalsRunning', { n: r.queued, p: r.profile }))));
        self.pollSignals(Date.now(), r.queued, (self.summary && self.summary.n_with_signals) || 0);
      }, function (err) {
        CQA.dom.replace(self.status, ui.msgbar('danger', t('dash.signalsFailed'), err.message));
        self.signalsDone();
      });
    }

    signalsDone() {
      this.signalsBtn.disabled = false;
      CQA.dom.replace(this.signalsBtn, icon('sparkle'), t('dash.signals'));
    }

    pollSignals(t0, queued, before) {
      var self = this;
      clearTimeout(this.sigTimer);
      this.sigTimer = setTimeout(function () {
        if (self.stopped) return;
        CQA.api.insightsSummary(self.group).then(function (s) {
          var done = s.n_with_signals - before;
          var finished = done >= queued || s.n_with_signals >= s.n_evaluated || Date.now() - t0 > 180000;
          if (finished) {
            CQA.dom.replace(self.status, ui.msgbar('success', t('dash.signalsReady', { n: Math.max(0, done) })));
            self.signalsDone();
            self.loadInsights();
          } else {
            CQA.dom.replace(self.status, ui.msgbar('info', h('span', { class: 'row' }, ui.spinner(), t('dash.signalsProgress', { d: Math.max(0, done), n: queued }))));
            self.pollSignals(t0, queued, before);
          }
        }, function () { self.pollSignals(t0, queued, before); });
      }, 3000);
    }

    // ------------------------------------------------------------ загрузка набора и оценка
    openLoad() {
      var self = this;
      var groupInput = h('input', { class: 'input', id: 'dl-group', value: (CQA.api.launchData && CQA.api.launchData.group) || 'DEV', autocomplete: 'off', spellcheck: 'false' });
      var fileInput = h('input', { type: 'file', accept: '.json,application/json', class: 'sr-only', id: 'dl-file' });
      var status = h('div', { 'aria-live': 'polite' });
      var body = h('div', { class: 'stack stack--l' },
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: 'dl-group' }, t('dash.groupId')),
          groupInput,
          h('div', { class: 'field__hint' }, t('dash.groupHint'))),
        h('div', { class: 'field__hint' },
          h('label', { for: 'dl-file', class: 'link-label', tabindex: '0', onKeydown: function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } } }, t('dash.uploadFile')), fileInput),
        status);
      var done = function (res, g) {
        CQA.dom.replace(status, ui.msgbar('success', t('dash.loaded', { g: (res && res.group_id) || g, n: res && res.total_records != null ? res.total_records : '?' }), res && res.note));
        CQA.api.datasets().then(function (list) { self.datasets = list || []; self.group = (res && res.group_id) || self.group; self.fillGroups(); self.loadInsights(); });
      };
      fileInput.addEventListener('change', function () {
        var file = fileInput.files[0];
        if (!file) return;
        CQA.dom.replace(status, h('div', { class: 'row' }, ui.spinner(), t('dash.uploading', { name: file.name })));
        CQA.api.uploadDataset(file).then(function (res) { done(res, file.name); }, function (err) {
          CQA.dom.replace(status, ui.msgbar('danger', t('dash.loadFailed'), err.message));
        }).finally(function () { fileInput.value = ''; });
      });
      ui.modal({
        title: t('dash.loadDataset'),
        body: body,
        footer: function (close) {
          var go = h('button', { class: 'btn btn--primary', type: 'button' }, icon('download'), t('dash.loadDataset'));
          go.addEventListener('click', function () {
            var g = groupInput.value.trim();
            if (!g) { groupInput.setAttribute('aria-invalid', 'true'); groupInput.focus(); return; }
            groupInput.removeAttribute('aria-invalid');
            go.disabled = true;
            CQA.dom.replace(go, ui.spinner(), t('dash.loading'));
            CQA.api.fetchDataset(g).then(function (res) { done(res, g); }, function (err) {
              CQA.dom.replace(status, ui.msgbar('danger', t('dash.loadFailed'), err.message));
            }).finally(function () { go.disabled = false; CQA.dom.replace(go, icon('download'), t('dash.loadDataset')); });
          });
          return [h('button', { class: 'btn', type: 'button', onClick: function () { close(); } }, t('action.close')), go];
        }
      });
    }

    openEvaluate() {
      var self = this;
      var sel = h('select', { class: 'select', id: 'ev-group' }, h('option', { value: '' }, t('dash.allGroups')));
      (this.datasets || []).forEach(function (d) { sel.appendChild(h('option', { value: d.group_id }, d.group_id + ' · ' + t('dash.records', { n: d.total_records }))); });
      sel.value = this.group || '';
      var force = h('input', { type: 'checkbox' });
      var status = h('div', { 'aria-live': 'polite' });
      ui.modal({
        title: t('dash.evaluateTitle'),
        body: h('div', { class: 'stack stack--l' },
          h('p', { class: 'muted' }, t('dash.evaluateIntro')),
          h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'ev-group' }, t('apply.group')), sel),
          h('label', { class: 'check check--small' }, force, t('dash.force')),
          status),
        footer: function (close) {
          var go = h('button', { class: 'btn btn--primary', type: 'button' }, icon('flash'), t('dash.evaluateAll'));
          go.addEventListener('click', function () {
            go.disabled = true;
            CQA.dom.replace(go, ui.spinner(), t('dash.starting'));
            CQA.api.createRun({ group: sel.value || null, force: force.checked, kind: 'batch' }).then(function (run) {
              close();
              CQA.router.go('/runs/' + run.id);
            }, function (err) {
              go.disabled = false;
              CQA.dom.replace(go, icon('flash'), t('dash.evaluateAll'));
              CQA.dom.replace(status, ui.msgbar('danger', t('run.createFailed'), err.message));
            });
          });
          return [h('button', { class: 'btn', type: 'button', onClick: function () { close(); } }, t('action.cancel')), go];
        }
      });
    }

    loadRuns() {
      var self = this;
      ui.load(this.runsBox, ui.skeleton('table', 3), function () { return CQA.api.runs(); }, function (runs) {
        if (!runs.length) { self.runsBox.appendChild(h('p', { class: 'muted' }, t('dash.noRuns'))); return; }
        var tbody = h('tbody');
        runs.slice(0, 6).forEach(function (r) {
          var processed = r.units_done + r.units_failed;
          var pct = r.units_total ? processed / r.units_total : 0;
          var href = r.kind === 'dry_run' ? '#/runs/' + r.id + '/diff' : '#/runs/' + r.id;
          var tr = h('tr', { class: 'is-clickable' },
            h('td', null, h('a', { href: href }, '#' + r.id)),
            h('td', null, CQA.i18n.tx('runKind.' + r.kind, r.kind)),
            h('td', null, ui.badge(CQA.i18n.tx('runStatus.' + r.status, r.status), CQA.runStatusTone(r.status))),
            h('td', { class: 'run-progress-cell' },
              h('div', { class: 'row row--nowrap' },
                h('div', { class: 'progress grow' }, h('div', { class: 'progress__bar', style: { width: Math.round(pct * 100) + '%' } })),
                h('span', { class: 'num t-caption1 muted nowrap' }, processed + '/' + r.units_total)),
              r.units_failed ? h('span', { class: 't-caption1', style: { color: 'var(--colorStatusDangerForeground1)' } }, t('run.failedN', { n: r.units_failed })) : null),
            h('td', { class: 'col-operator' }, r.model),
            h('td', { class: 'num' }, fmt.money(r.cost_usd)),
            h('td', { class: 'nowrap muted' }, fmt.date(r.created_at)));
          tr.addEventListener('click', function (e) { if (!e.target.closest('a')) location.hash = href; });
          tbody.appendChild(tr);
        });
        self.runsBox.appendChild(h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact' },
          h('thead', null, h('tr', null,
            h('th', { scope: 'col' }, '№'), h('th', { scope: 'col' }, t('run.kind')), h('th', { scope: 'col' }, t('run.status')),
            h('th', { scope: 'col' }, t('run.progress')), h('th', { scope: 'col', class: 'col-operator' }, t('run.model')),
            h('th', { scope: 'col', class: 'num' }, t('run.cost')), h('th', { scope: 'col' }, t('run.created')))),
          tbody)));
      });
    }
  }

  customElements.define('cqa-page-dashboard', CqaPageDashboard);
})(window.CQA = window.CQA || {});
