/* <cqa-page-quality> — качество оценки #/quality?run=&split=&by=: точность против эталона и потолка (согласие двух
   разметчиков), ловушки, разрез, стоимость, сравнение прогонов (моделей) и автотесты. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  var SPLITS = ['all', 'tune', 'holdout'];
  var BYS = ['language', 'channel', 'family'];

  function kappa(v) { return v == null || isNaN(v) ? '—' : fmt.num(v, 2); }
  function rateTone(v) { return v == null ? null : v >= 0.9 ? 'success' : v >= 0.75 ? 'mid' : 'danger'; }

  /** Плитка «метрика против потолка»: lowerBetter — для MAE. */
  function vsTile(label, value, ceil, opts) {
    opts = opts || {};
    var f = opts.fmt || fmt.pct1;
    var note = null, tone = null;
    if (value != null && ceil != null) {
      var diff = opts.lowerBetter ? ceil - value : value - ceil;
      var eps = opts.eps != null ? opts.eps : 0.005;
      if (diff >= -eps) { tone = 'success'; note = t('q.atCeiling', { c: f(ceil) }); }
      else { tone = 'warning'; note = t('q.belowCeiling', { c: f(ceil) }); }
    }
    return h('div', { class: 'kpi q-kpi' + (tone ? ' q-kpi--' + tone : '') },
      h('span', { class: 'kpi__label' }, label, opts.hint ? ui.hint(opts.hint) : null),
      h('span', { class: 'kpi__value' }, value == null ? '—' : f(value)),
      note ? h('span', { class: 'kpi__note q-kpi__note' }, icon(tone === 'success' ? 'checkCircle' : 'info', 'icon--16'), note) : null);
  }

  function plainTile(label, value, note, hint) {
    return h('div', { class: 'kpi q-kpi' },
      h('span', { class: 'kpi__label' }, label, hint ? ui.hint(hint) : null),
      h('span', { class: 'kpi__value' }, value),
      note ? h('span', { class: 'kpi__note' }, note) : null);
  }

  function sectionHead(id, title, hint, extra) {
    return h('div', { class: 'card__header' },
      h('div', null, h('h2', { class: 'card__title', id: id }, title), hint ? h('div', { class: 'muted t-caption1 q-hint' }, hint) : null),
      extra || null);
  }

  class CqaPageQuality extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('q.title') + ' · CQA';
      var q = this.query || {};
      this.state = { run: q.run ? Number(q.run) : null, split: SPLITS.indexOf(q.split) >= 0 ? q.split : 'all', by: BYS.indexOf(q.by) >= 0 ? q.by : 'language' };
      this.controls = h('div', { class: 'row q-controls' });
      this.box = h('div', { class: 'stack stack--l' }, ui.skeleton('tiles', 6), ui.skeleton('table', 8));
      this.runsBox = h('div', null, ui.skeleton('table', 4));
      this.autoBox = h('div', null, ui.skeleton('table', 6));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page q-page' },
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('q.title')), h('div', { class: 'page-header__sub' }, t('q.subtitle'))),
          this.controls),
        h('div', { class: 'stack stack--xl' },
          this.glossary(),
          this.box,
          h('section', { class: 'card', 'aria-labelledby': 'q-runs' },
            sectionHead('q-runs', t('q.runsTitle'), t('q.runsHint')),
            this.runsBox),
          h('section', { class: 'card', 'aria-labelledby': 'q-auto' },
            sectionHead('q-auto', t('q.autoTitle'), t('q.autoHint')),
            this.autoBox))));
      this.loadRuns();
      this.load();
      this.loadAutotests();
    }

    update(params, query) {
      this.query = query;
      return true;
    }

    glossary() {
      var items = ['exact', 'compatible', 'kappa', 'near', 'ceiling', 'mae', 'traps', 'split'];
      return h('details', { class: 'disclosure card q-glossary' },
        h('summary', null, icon('chevronRight'), h('b', null, t('q.glossaryTitle')), h('span', { class: 'muted' }, ' — ', t('q.glossaryLead'))),
        h('dl', { class: 'q-glossary__list disclosure__body' }, items.map(function (k) {
          return [h('dt', null, t('q.g.' + k)), h('dd', null, t('q.g.' + k + 'Text'))];
        })));
    }

    setState(patch) {
      Object.assign(this.state, patch);
      var s = this.state;
      CQA.router.setQuery({ run: s.run || null, split: s.split !== 'all' ? s.split : null, by: s.by !== 'language' ? s.by : null });
      this.load();
      this.renderRuns();
    }

    renderControls() {
      var self = this, s = this.state;
      var runSel = h('select', { class: 'select select--small q-run', id: 'q-run', 'aria-label': t('q.run') });
      (this.runs || []).forEach(function (r) {
        runSel.appendChild(h('option', { value: r.id, selected: r.id === s.run },
          '#' + r.id + ' · ' + r.model + (r.local ? ' · ' + t('q.local') : '') + ' · v' + r.checklist_version + ' · ' + t('q.runN', { n: r.n })));
      });
      if (s.run) runSel.value = String(s.run);
      runSel.addEventListener('change', function () { self.setState({ run: Number(runSel.value) }); });
      var split = h('div', { class: 'segmented', role: 'group', 'aria-label': t('q.split') }, SPLITS.map(function (k) {
        return h('button', { type: 'button', 'aria-pressed': String(s.split === k), title: t('q.splitTitle.' + k), onClick: function () { self.setState({ split: k }); } }, t('q.split.' + k));
      }));
      var by = h('select', { class: 'select select--small', id: 'q-by', 'aria-label': t('q.by') }, BYS.map(function (k) {
        return h('option', { value: k, selected: s.by === k }, t('q.by.' + k));
      }));
      by.addEventListener('change', function () { self.setState({ by: by.value }); });
      CQA.dom.replace(this.controls,
        h('span', { class: 'q-ctl' }, h('label', { class: 'muted', for: 'q-run' }, t('q.run')), runSel),
        h('div', { class: 'segmented-scroll' }, split),
        h('span', { class: 'q-ctl' }, h('label', { class: 'muted', for: 'q-by' }, t('q.by')), by));
    }

    load() {
      var self = this, s = this.state;
      var seq = (this._seq = (this._seq || 0) + 1);
      this.box.classList.add('is-loading');
      CQA.api.quality({ run: s.run, split: s.split, by: s.by }).then(function (r) {
        if (seq !== self._seq) return;
        self.box.classList.remove('is-loading');
        self.report = r;
        if (!s.run && r.run) { s.run = r.run.id; self.renderControls(); self.renderRuns(); }
        self.render();
      }, function (err) {
        if (seq !== self._seq) return;
        self.box.classList.remove('is-loading');
        CQA.dom.replace(self.box, ui.error(err, function () { self.load(); }));
      });
    }

    render() {
      var r = this.report;
      this.titles = {};
      var self = this;
      (r.criteria || []).concat((r.ceiling && r.ceiling.criteria) || []).forEach(function (c) { self.titles[c.criterion] = c.title; });
      if (this.auto) this.renderAutotests();
      if (!r.run) {
        CQA.dom.replace(this.box, ui.empty(t('q.noRun'), t('q.noRunText'), null, 'chart'));
        return;
      }
      var run = r.run, ov = r.overall || {}, ceil = r.ceiling || {}, cov = ceil.overall || {}, tot = r.total || {}, cost = r.cost || {};
      var meta = h('div', { class: 'q-runmeta row' },
        ui.badge(CQA.i18n.tx('runKind.' + run.kind, run.kind), 'neutral'),
        run.status !== 'done' ? ui.badge(CQA.i18n.tx('runStatus.' + run.status, run.status), CQA.runStatusTone(run.status)) : null,
        h('b', null, run.model), h('span', { class: 'muted' }, run.profile),
        ui.badge(run.local ? t('q.local') : t('q.cloud'), run.local ? 'success' : 'brand', run.local ? 'server' : 'cloud'),
        h('span', { class: 'muted' }, t('eval.checklistVersion', { v: run.checklist_version }) + ' · ' + t('log.prompt', { v: run.prompt_version }) + ' · ' + fmt.date(run.created_at)),
        h('span', { class: 'spacer' }),
        h('span', { class: 'muted' }, t('q.dialogsLine', { n: r.n, g: r.gold_dialogs })));
      var warn = [];
      if (r.missing && r.missing.length) warn.push(ui.msgbar('warning', t('q.missing', { n: r.missing.length }), t('q.missingText')));
      (r.warnings || []).forEach(function (w) { warn.push(ui.msgbar('warning', null, typeof w === 'string' ? w : JSON.stringify(w))); });

      var tiles = h('div', { class: 'kpis q-kpis' },
        vsTile(t('q.exact'), ov.exact_rate, cov.exact_rate, { hint: t('q.g.exactText') }),
        vsTile(t('q.compatible'), ov.compatible_rate, cov.compatible_rate, { hint: t('q.g.compatibleText') }),
        vsTile(t('q.kappa'), ov.kappa_mean, cov.kappa_mean, { fmt: kappa, hint: t('q.g.kappaText'), eps: 0.01 }),
        vsTile(t('q.mae'), tot.mae, tot.ceiling_mae, { fmt: function (v) { return fmt.num(v, 2); }, lowerBetter: true, hint: t('q.g.maeText'), eps: 0.05 }),
        plainTile(t('q.costPerDialog'), fmt.money(cost.cost_per_dialog_usd), cost.estimated ? t('q.costEstimated') : t('q.costExact'), t('q.costHint')),
        plainTile(t('q.timePerDialog'), cost.latency_sec_per_dialog != null ? t('unit.sec', { n: fmt.num(cost.latency_sec_per_dialog, 1) }) : '—',
          cost.duration_sec != null ? t('q.runDuration', { d: fmt.duration(cost.duration_sec) }) : null, t('q.timeHint')));

      CQA.dom.replace(this.box,
        h('section', { class: 'card q-summary' }, meta, tiles),
        warn,
        h('section', { class: 'card', 'aria-labelledby': 'q-crit' },
          sectionHead('q-crit', t('q.critTitle'), t('q.critHint', { a: (ceil.files || []).join(' и ') || '—' })),
          this.criteriaTable(r)),
        h('div', { class: 'q-grid' },
          h('section', { class: 'card', 'aria-labelledby': 'q-traps' },
            sectionHead('q-traps', t('q.trapsTitle'), t('q.trapsHint')),
            this.trapsTable(r)),
          h('section', { class: 'card', 'aria-labelledby': 'q-by-t' },
            sectionHead('q-by-t', t('q.byTitle.' + ((r.by && r.by.key) || this.state.by)), t('q.byHint')),
            this.byTable(r))),
        h('section', { class: 'card', 'aria-labelledby': 'q-cost' },
          sectionHead('q-cost', t('q.costTitle'), t('q.costTitleHint')),
          this.costBlock(cost)));
    }

    criteriaTable(r) {
      var tbody = h('tbody');
      r.criteria.forEach(function (c) {
        var ceilBetter = c.ceiling_exact_rate != null && c.exact_rate + 0.005 < c.ceiling_exact_rate;
        tbody.appendChild(h('tr', null,
          h('td', { class: 'mono' }, c.criterion),
          h('th', { scope: 'row', class: 'q-crit-title' }, c.title, h('div', { class: 'muted t-caption1' }, t('ctype.' + c.type))),
          h('td', { class: 'q-bar-cell' },
            h('div', { class: 'q-cell' }, ui.bar(c.exact_rate, { tone: rateTone(c.exact_rate), mark: c.ceiling_exact_rate, markTitle: t('q.ceilingMark') }),
              h('b', { class: 'num' }, fmt.pct1(c.exact_rate))),
            h('div', { class: 't-caption1 ' + (ceilBetter ? 'q-below' : 'muted') }, t('q.ceilingShort', { v: fmt.pct1(c.ceiling_exact_rate) }))),
          h('td', { class: 'num' }, fmt.pct1(c.compatible_rate)),
          h('td', { class: 'num' }, h('b', null, kappa(c.kappa)), h('div', { class: 'muted t-caption1' }, t('q.ceilingShort', { v: kappa(c.ceiling_kappa) }))),
          h('td', { class: 'num' }, c.near != null ? fmt.pct1(c.near) : h('span', { class: 'muted' }, '—')),
          h('td', { class: 'num' }, String(c.mismatches), c.hard ? h('div', { class: 't-caption1 q-below' }, t('q.hard', { n: c.hard })) : null)));
      });
      return h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact q-table' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, '№'), h('th', { scope: 'col' }, t('q.cCriterion')),
          h('th', { scope: 'col' }, t('q.exact'), ' ', ui.hint(t('q.g.exactText'))),
          h('th', { scope: 'col', class: 'num' }, t('q.compatible'), ' ', ui.hint(t('q.g.compatibleText'))),
          h('th', { scope: 'col', class: 'num' }, t('q.kappaShort'), ' ', ui.hint(t('q.g.kappaText'))),
          h('th', { scope: 'col', class: 'num' }, t('q.near'), ' ', ui.hint(t('q.g.nearText'))),
          h('th', { scope: 'col', class: 'num' }, t('q.mismatches')))),
        tbody));
    }

    trapsTable(r) {
      var self = this;
      var order = { flag: 0, no: 1, na: 2 };
      var list = (r.traps || []).slice().sort(function (a, b) { return (order[a.kind] - order[b.kind]) || a.criterion.localeCompare(b.criterion); });
      if (!list.length) return h('p', { class: 'muted' }, t('q.noTraps'));
      var tbody = h('tbody');
      list.forEach(function (x) {
        tbody.appendChild(h('tr', { class: x.kind === 'flag' ? 'q-trap--flag' : null },
          h('th', { scope: 'row' }, h('span', { class: 'mono' }, x.criterion), ' ', h('span', { class: 'q-trap-title' }, self.titles[x.criterion] || ''),
            ' ', CQA.verdict(x.label, x.kind === 'flag' ? 'flag' : 'yes_no')),
          h('td', { class: 'num' }, String(x.reference)),
          h('td', { class: 'num' }, String(x.predicted)),
          h('td', { class: 'num' }, h('b', { class: x.recall < 1 ? 'q-below' : null }, fmt.pct(x.recall)), x.fn ? h('div', { class: 'muted t-caption1' }, t('q.missed', { n: x.fn })) : null),
          h('td', { class: 'num' }, h('b', null, fmt.pct(x.precision)), x.fp ? h('div', { class: 'muted t-caption1' }, t('q.falseAlarms', { n: x.fp })) : null)));
      });
      return h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, t('q.trap')), h('th', { scope: 'col', class: 'num' }, t('q.trapRef')), h('th', { scope: 'col', class: 'num' }, t('q.trapPred')),
          h('th', { scope: 'col', class: 'num' }, t('q.recall')), h('th', { scope: 'col', class: 'num' }, t('q.precision')))),
        tbody));
    }

    byTable(r) {
      if (!r.by || !r.by.groups || !r.by.groups.length) return h('p', { class: 'muted' }, '—');
      var key = r.by.key;
      var tbody = h('tbody');
      r.by.groups.forEach(function (g) {
        var label = key === 'channel' ? CQA.util.channelLabel(g.value) : g.value;
        tbody.appendChild(h('tr', null,
          h('th', { scope: 'row' }, label),
          h('td', { class: 'num' }, String(g.n)),
          h('td', { class: 'q-bar-cell' }, h('div', { class: 'q-cell' }, ui.bar(g.overall.exact_rate, { tone: rateTone(g.overall.exact_rate) }), h('b', { class: 'num' }, fmt.pct1(g.overall.exact_rate)))),
          h('td', { class: 'num' }, kappa(g.overall.kappa_mean)),
          h('td', { class: 'num' }, fmt.num(g.total.mae, 2))));
      });
      return h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, t('q.by.' + key)), h('th', { scope: 'col', class: 'num' }, t('q.dialogs')),
          h('th', { scope: 'col' }, t('q.exact')), h('th', { scope: 'col', class: 'num' }, t('q.kappaShort')), h('th', { scope: 'col', class: 'num' }, t('q.maeShort')))),
        tbody));
    }

    costBlock(c) {
      if (!c || c.n_dialogs == null) return h('p', { class: 'muted' }, '—');
      var stat = function (label, value, note) {
        return h('div', { class: 'run-stat' }, h('div', { class: 'run-stat__label' }, label), h('div', { class: 'run-stat__value num' }, value),
          note ? h('div', { class: 'muted t-caption1' }, note) : null);
      };
      var tk = c.tokens || {};
      return h('div', { class: 'stack' },
        h('div', { class: 'run-stats' },
          stat(t('q.costPerDialog'), fmt.money(c.cost_per_dialog_usd), t('q.costFull', { v: fmt.money(c.eval_cost_usd), n: c.n_dialogs })),
          stat(t('q.costRun'), fmt.money(c.run_cost_usd), t('q.costRunNote', { cached: c.units_cached, copied: c.units_copied })),
          stat(t('q.calls'), fmt.int(c.n_llm_calls), t('q.callsNote', { n: c.units_total })),
          stat(t('q.duration'), c.duration_sec != null ? fmt.duration(c.duration_sec) : '—', t('q.latencyNote', { s: fmt.num(c.latency_sec_per_dialog, 1) })),
          stat(t('q.tokens'), fmt.int(tk.prompt), t('q.tokensNote', { c: fmt.int(tk.cached), o: fmt.int(tk.completion), r: fmt.int(tk.reasoning) }))),
        c.estimated ? h('div', { class: 'muted t-caption1' }, t('q.estimatedNote')) : null,
        c.incomplete ? h('div', { class: 'muted t-caption1' }, t('q.incompleteNote')) : null);
    }

    // ------------------------------------------------------------ прогоны (сравнение моделей)
    loadRuns() {
      var self = this;
      CQA.api.qualityRuns().then(function (runs) {
        self.runs = runs || [];
        self.renderControls();
        self.renderRuns();
      }, function (err) {
        self.runs = [];
        self.renderControls();
        CQA.dom.replace(self.runsBox, ui.error(err, function () { self.loadRuns(); }));
      });
    }

    renderRuns() {
      var self = this, runs = this.runs;
      if (!runs) return;
      if (!runs.length) { CQA.dom.replace(this.runsBox, h('p', { class: 'muted' }, t('q.noRuns'))); return; }
      var tbody = h('tbody');
      runs.forEach(function (r) {
        var trap = r.traps && r.traps['08:Да'];
        var sel = r.id === self.state.run;
        var tr = h('tr', { class: 'is-clickable' + (sel ? ' is-selected' : ''), tabindex: '0', 'aria-selected': String(sel) },
          h('td', { class: 'nowrap' }, h('b', null, '#' + r.id), ' ', h('span', { class: 'muted t-caption1' }, CQA.i18n.tx('runKind.' + r.kind, r.kind)),
            r.status !== 'done' ? h('div', null, ui.badge(CQA.i18n.tx('runStatus.' + r.status, r.status), CQA.runStatusTone(r.status))) : null),
          h('td', null, h('div', { class: 'row row--nowrap' }, h('span', { class: 'nowrap' }, r.model),
            r.local ? ui.badge(t('q.local'), 'success', 'server') : null),
            h('div', { class: 'muted t-caption1' }, r.profile)),
          h('td', { class: 'num' }, 'v' + r.checklist_version),
          h('td', { class: 'num' }, String(r.n), r.complete ? null : h('div', { class: 'muted t-caption1' }, t('q.partial'))),
          h('td', { class: 'num' }, h('b', null, fmt.pct1(r.exact_rate))),
          h('td', { class: 'num' }, kappa(r.kappa_mean)),
          h('td', { class: 'num' }, r.mae == null ? '—' : fmt.num(r.mae, 2)),
          h('td', { class: 'num nowrap' }, trap ? fmt.pct(trap.recall) + ' / ' + fmt.pct(trap.precision) : '—'),
          h('td', { class: 'num nowrap' }, fmt.money(r.cost_per_dialog_usd), r.cost_estimated && r.cost_per_dialog_usd != null ? h('span', { class: 'muted', title: t('q.costEstimated') }, ' *') : null),
          h('td', { class: 'num nowrap' }, r.latency_sec_per_dialog != null ? t('unit.sec', { n: fmt.num(r.latency_sec_per_dialog, 1) }) : '—'));
        var pick = function () { if (r.id !== self.state.run) self.setState({ run: r.id }); };
        tr.addEventListener('click', pick);
        tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
        tbody.appendChild(tr);
      });
      CQA.dom.replace(this.runsBox, h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact q-runs' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, t('q.cRun')), h('th', { scope: 'col' }, t('run.model')), h('th', { scope: 'col', class: 'num' }, t('q.cVersion')),
          h('th', { scope: 'col', class: 'num' }, t('q.dialogs')), h('th', { scope: 'col', class: 'num' }, t('q.exact')),
          h('th', { scope: 'col', class: 'num' }, t('q.kappaShort')), h('th', { scope: 'col', class: 'num' }, t('q.maeShort')),
          h('th', { scope: 'col', class: 'num' }, t('q.cTrap08')), h('th', { scope: 'col', class: 'num' }, t('q.cCost')),
          h('th', { scope: 'col', class: 'num' }, t('q.cLatency')))),
        tbody)),
        h('div', { class: 'muted t-caption1 q-foot' }, t('q.runsFoot')));
    }

    // ------------------------------------------------------------ автотесты
    loadAutotests() {
      var self = this;
      ui.load(this.autoBox, ui.skeleton('table', 6), function () { return CQA.api.autotests(); }, function (a) {
        self.auto = a;
        self.renderAutotests();
      });
    }

    expectText(e) {
      if (e === 'same_as_base') return t('q.sameAsBase');
      if (!e || typeof e !== 'object') return '—';
      return Object.keys(e).map(function (k) { return k + ' = ' + e[k].join(t('q.or')); }).join(', ');
    }

    renderAutotests() {
      var self = this, a = this.auto;
      if (!a || !a.total || !a.total.n) { CQA.dom.replace(this.autoBox, h('p', { class: 'muted' }, t('q.noAuto'))); return; }
      var langs = (a.by_language || []).map(function (x) { return x.language; });
      var cell = function (x) {
        if (!x || !x.n) return h('td', { class: 'num muted' }, '—');
        var ok = x.passed === x.evaluated && x.evaluated === x.n;
        return h('td', { class: 'num' }, h('span', { class: 'q-pass ' + (ok ? 'q-pass--ok' : 'q-pass--bad') }, x.passed + '/' + x.n));
      };
      var tbody = h('tbody');
      a.by_mutation.forEach(function (m) {
        tbody.appendChild(h('tr', null,
          h('th', { scope: 'row' }, m.title, h('div', { class: 'muted t-caption1 mono' }, m.mutation)),
          h('td', { class: 't-caption1' }, self.expectText(m.expect)),
          langs.map(function (l) { return cell(m.by_language && m.by_language[l]); }),
          cell(m)));
      });
      var tot = a.total, inj = a.injection || {};
      var injText = inj.evaluated ? t('q.injLine', { r: inj.robust, n: inj.evaluated, s: inj.exact_same }) : null;
      var injChanged = inj.changed_verdicts ? t('q.injChanged', { n: inj.changed_verdicts, list: Object.keys(inj.changed_by_criterion || {}).map(function (k) { return k + ' (' + inj.changed_by_criterion[k] + ')'; }).join(', ') }) : null;
      var injFlip = inj.evaluated ? (inj.flipped_to_yes ? t('q.injFlipBad', { n: inj.flipped_to_yes }) : t('q.injFlipOk')) : null;

      var failed = (a.failed || []).map(function (f) {
        return h('li', { class: 'q-failed' },
          h('div', { class: 'row' },
            ui.badge(CQA.i18n.tx('q.status.' + f.status, f.status), f.status === 'failed' ? 'danger' : 'warning'),
            h('a', { class: 'mono', href: '#/c/' + encodeURIComponent(f.uid) }, fmt.externalId(f.uid)),
            h('span', { class: 'muted' }, (self.mutTitle(f.mutation) || f.mutation) + ' · ' + f.language),
            h('span', { class: 'spacer' }),
            f.base_uid ? h('a', { class: 't-caption1', href: '#/c/' + encodeURIComponent(f.base_uid) }, t('q.baseDialog', { id: f.base_external_id || fmt.externalId(f.base_uid) })) : null,
            f.evaluation_id ? h('a', { class: 't-caption1', href: '#/log/' + f.evaluation_id }, icon('code', 'icon--16'), ' ', t('eval.log')) : null),
          (f.mismatches || []).map(function (x) {
            return h('div', { class: 'q-failed__m' },
              h('div', { class: 'row' }, h('span', { class: 'mono' }, x.criterion_id), h('span', null, self.titles && self.titles[x.criterion_id] || ''),
                h('span', { class: 'muted' }, t('q.expected')), (x.expected || []).map(function (v) { return ui.badge(v, 'neutral', null, { class: 'badge verdict badge--neutral' }); }),
                h('span', { class: 'muted' }, t('q.got')), ui.badge(x.got == null ? '—' : x.got, 'danger', null, { class: 'badge verdict badge--danger' })),
              x.explanation ? h('div', { class: 't-caption1 subtle' }, x.explanation) : null);
          }));
      });

      CQA.dom.replace(this.autoBox, h('div', { class: 'stack stack--l' },
        h('div', { class: 'kpis q-kpis' },
          plainTile(t('q.autoPassed'), tot.passed + ' / ' + tot.n, t('q.autoRate', { p: fmt.pct1(tot.rate) }) + (tot.errors ? ' · ' + t('q.autoErrors', { n: tot.errors }) : '') + (tot.pending ? ' · ' + t('q.autoPending', { n: tot.pending }) : '')),
          inj.evaluated ? plainTile(t('q.injTitle'), inj.robust + ' / ' + inj.evaluated, injFlip, t('q.injHint')) : null,
          plainTile(t('q.autoModels'), (a.models || []).join(', ') || '—', a.cost_usd != null ? t('q.autoCost', { c: fmt.money(a.cost_usd) }) : null)),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact q-auto' },
          h('thead', null, h('tr', null,
            h('th', { scope: 'col' }, t('q.mutation')), h('th', { scope: 'col' }, t('q.expect')),
            langs.map(function (l) { return h('th', { scope: 'col', class: 'num' }, l); }),
            h('th', { scope: 'col', class: 'num' }, t('q.totalCol')))),
          tbody)),
        injText ? h('div', { class: 'msgbar msgbar--' + (inj.flipped_to_yes ? 'danger' : 'info') }, icon('shield'),
          h('div', { class: 'msgbar__body' }, h('div', { class: 'msgbar__title' }, injText), [injChanged, injFlip].filter(Boolean).join('. ') + '.')) : null,
        failed.length ? h('div', { class: 'stack' }, h('h3', { class: 'log-h3' }, t('q.failedTitle', { n: failed.length })), h('ul', { class: 'q-failed-list' }, failed))
          : h('p', { class: 'muted' }, t('q.noFailed'))));
    }

    mutTitle(key) {
      var m = (this.auto && this.auto.by_mutation || []).filter(function (x) { return x.mutation === key; })[0];
      return m ? m.title : null;
    }
  }

  customElements.define('cqa-page-quality', CqaPageQuality);
})(window.CQA = window.CQA || {});
