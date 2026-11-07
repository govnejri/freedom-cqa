/* <cqa-page-dashboard> — сводка руководителя #/?group=DEV.
   Сверху «На сегодня» — 3–4 дела, у каждого одна кнопка. Под ними — полоса метрик (включая точность ИИ),
   затем радар рисков и вопросы без ответа по темам. Причины обращения — свёрнуты внизу.
   Конвейер (загрузка, оценка, сигналы, прогоны) — в «Очереди» → «Загрузка и оценка» (CQA.openPipeline). */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }
  function safe(p) { return p.catch(function (e) { console.warn('[CQA]', e); return null; }); }

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
        self.loadAll();
      });
      this.what = h('p', { class: 'page-head__what' }, t('dash.what'));
      this.today = h('section', { class: 'panel today', 'aria-labelledby': 'dash-today' },
        h('div', { class: 'today__head' }, h('h2', { class: 'panel__title', id: 'dash-today' }, t('dash.today'))),
        h('div', { class: 'today__grid' }, h('div', { class: 'today__item' }, ui.skeleton('text', 3)), h('div', { class: 'today__item' }, ui.skeleton('text', 3)), h('div', { class: 'today__item' }, ui.skeleton('text', 3))));
      this.metrics = h('div', { class: 'metrics' }, h('div', { class: 'metric' }, ui.skeleton('text', 2)));
      this.radarMeta = h('span', { class: 'panel__meta' });
      this.radar = h('div', null, ui.skeleton('table', 4));
      this.unansMeta = h('span', { class: 'panel__meta' });
      this.unans = h('div', null, ui.skeleton('table', 4));
      this.reasonsBox = h('div', { class: 'dash-more__body' });
      var more = h('details', { class: 'panel dash-more' },
        h('summary', null, icon('chevronRight'), h('span', null, t('reasons.title')), h('span', { class: 'panel__meta' }, t('dash.reasonsHint'))),
        this.reasonsBox);
      more.addEventListener('toggle', function () { if (more.open && !self.reasonsLoaded) self.loadReasons(); });

      this.textContent = '';
      this.appendChild(h('div', { class: 'page dash-page' },
        h('div', { class: 'page-head' },
          h('div', null, h('h1', null, t('dash.title')), this.what),
          h('div', { class: 'page-head__actions' }, h('label', { class: 'muted t-caption1', for: 'dash-group-sel' }, t('dash.group')), this.groupSel)),
        this.today,
        this.metrics,
        h('div', { class: 'dash-cols' },
          h('section', { class: 'panel panel--flush', 'aria-labelledby': 'dash-radar' },
            h('div', { class: 'panel__head' },
              h('h2', { class: 'panel__title', id: 'dash-radar' }, t('risk.title')), this.radarMeta, h('span', { class: 'spacer' }),
              h('a', { class: 'link-more', href: '#/risks' }, t('risk.all'), icon('chevronRight'))),
            this.radar),
          h('section', { class: 'panel panel--flush', 'aria-labelledby': 'dash-unans' },
            h('div', { class: 'panel__head' },
              h('h2', { class: 'panel__title', id: 'dash-unans' }, t('unans.title')), this.unansMeta),
            this.unans)),
        more));
      this.addEventListener('cqa-compliance-changed', function () { self.renderRadarMeta(); self.loadToday(); });
      this._onData = function () { self.loadAll(); };
      document.addEventListener('cqa-data-changed', this._onData);
      this.init();
    }

    disconnectedCallback() { document.removeEventListener('cqa-data-changed', this._onData); }

    update(params, query) {
      var q = (query && query.group) || null;
      var g = q === 'all' ? null : q || this.group;
      if (g !== this.group && this.datasets) { this.group = g; this.fillGroups(); this.loadAll(); }
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
        self.loadAll();
      });
    }

    fillGroups() {
      var sel = this.groupSel, self = this;
      CQA.dom.clear(sel);
      sel.appendChild(h('option', { value: '' }, t('dash.allGroups')));
      (this.datasets || []).forEach(function (d) {
        sel.appendChild(h('option', { value: d.group_id, selected: d.group_id === self.group }, d.group_id + ' · ' + t('dash.records', { n: d.n_conversations != null ? d.n_conversations : d.total_records })));
      });
      sel.value = this.group || '';
    }

    loadAll() {
      var self = this, g = this.group;
      this.reasonsLoaded = false;
      var api = CQA.api;
      this.p = {
        summary: safe(api.insightsSummary(g)),
        risks: safe(api.risks(g)),
        unans: safe(api.unanswered(g)),
        ops: safe(api.operators(g)),
        convs: safe(api.conversations(g ? { group: g } : {})),
        quality: safe(api.quality({}))
      };
      this.p.summary.then(function (s) { self.summary = s; self.renderWhat(); });
      Promise.all([this.p.summary, this.p.quality]).then(function (r) { self.renderMetrics(r[0], r[1]); });
      Promise.all([this.p.risks, this.p.convs]).then(function (r) { self.renderRadar(r[0], r[1]); });
      this.p.unans.then(function (d) { self.renderUnans(d); });
      this.loadToday();
      if (this.querySelector('.dash-more[open]')) this.loadReasons();
    }

    renderWhat() {
      var s = this.summary;
      if (!s) return;
      this.what.textContent = t('dash.whatN', { g: this.group || t('dash.allGroupsShort'), n: s.n_evaluated });
    }

    // ------------------------------------------------------------ «На сегодня»
    loadToday() {
      var self = this, p = this.p;
      Promise.all([p.summary, p.risks, p.unans, p.ops]).then(function (r) { self.renderToday(r[0], r[1], r[2], r[3]); });
    }

    renderToday(s, risks, unans, ops) {
      var items = [];
      // 1. грубые нарушения, ещё не переданные в комплаенс
      if (risks && risks.total) {
        var pending = risks.items.filter(function (it) { return !it.compliance; });
        var cats = {};
        pending.forEach(function (it) { cats[it.category] = (cats[it.category] || 0) + 1; });
        var catText = Object.keys(cats).map(function (c) { return c + ' ' + cats[c]; }).join(', ');
        if (pending.length) items.push({ n: pending.length, tone: 'gross', title: t('today.gross', { n: pending.length }), sub: catText,
          btn: t('today.grossBtn'), href: '#/risks?open=1' });
      }
      // 2. главная тема вопросов без ответа → урок
      var top = unans && unans.topics && unans.topics[0];
      if (top) {
        var uids = [], seen = {};
        top.questions.forEach(function (q) { if (!seen[q.uid]) { seen[q.uid] = 1; uids.push({ uid: q.uid, label: q.external_id, note: q.text }); } });
        items.push({ n: top.n_questions, tone: 'loss', title: t('today.unans', { n: top.n_questions, topic: top.topic }),
          sub: t('today.unansSub', { c: top.n_conversations, s: fmt.score(top.avg_total_unanswered) }),
          btn: t('today.unansBtn'), onClick: function () { CQA.openLessonDialog({ title: t('lesson.topicTitle', { topic: cap(top.topic) }), items: uids }); } });
      } else if (s && s.n_evaluated && !s.n_with_signals) {
        var grp = this.group;
        items.push({ n: s.n_evaluated, tone: 'loss', title: t('today.signals', { n: s.n_evaluated }), sub: t('today.signalsSub'),
          btn: t('dash.signals'), onClick: function () { CQA.openPipeline({ group: grp, focus: 'signals' }); } });
      }
      // 3. операторы ниже отдела на 20+ баллов
      if (ops && ops.avg_total != null) {
        var below = (ops.operators || []).filter(function (o) { return o.avg_total != null && o.avg_total <= ops.avg_total - 20; })
          .sort(function (a, b) { return a.avg_total - b.avg_total; });
        if (below.length) {
          var names = below.slice(0, 3).map(function (o) { return o.name + ' ' + fmt.score(o.avg_total); }).join(', ');
          items.push({ n: below.length, title: t('today.below', { n: below.length }), sub: names + (below.length > 3 ? t('today.andMore', { n: below.length - 3 }) : ''),
            btn: t('today.belowBtn'), href: '#/operators?below=1' });
        }
      }
      // 4. апелляции или диалоги на проверку
      if (s && s.open_appeals) items.push({ n: s.open_appeals, title: t('today.appeals', { n: s.open_appeals }), sub: t('today.appealsSub'), btn: t('today.appealsBtn'), href: '#/appeals' });
      else if (s && s.needs_review) items.push({ n: s.needs_review, title: t('today.review', { n: s.needs_review }), sub: t('today.reviewSub'), btn: t('today.reviewBtn'), href: '#/conversations?review=1' });

      var grid = h('div', { class: 'today__grid' });
      items.slice(0, 4).forEach(function (it, i) {
        var cls = 'btn btn--small' + (i === 0 ? ' btn--primary' : '');
        var btn = it.href ? h('a', { class: cls, href: it.href }, it.btn, icon('arrowRight'))
          : h('button', { class: cls, type: 'button', onClick: it.onClick }, it.btn, icon('arrowRight'));
        grid.appendChild(h('div', { class: 'today__item' },
          h('span', { class: 'today__num' + (it.tone ? ' today__num--' + it.tone : '') }, fmt.int(it.n)),
          h('span', { class: 'today__title' }, it.title),
          it.sub ? h('span', { class: 'today__sub' }, it.sub) : h('span', { class: 'today__sub' }),
          btn));
      });
      var head = this.today.querySelector('.today__head');
      CQA.dom.replace(this.today, head, items.length ? grid
        : h('div', { class: 'today__calm' }, icon('checkCircle'), h('span', null, t('today.calm')), h('a', { class: 'link-more', href: '#/conversations' }, t('nav.queue'), icon('chevronRight'))));
    }

    // ------------------------------------------------------------ метрики
    renderMetrics(s, q) {
      var g = this.group;
      if (!s) { CQA.dom.replace(this.metrics, h('div', { class: 'metric' }, h('span', { class: 'muted' }, t('state.errorTitle')))); return; }
      var exact = q && q.overall && q.overall.exact_rate;
      CQA.dom.replace(this.metrics,
        h('a', { class: 'metric', href: '#/conversations' + (g ? '?group=' + encodeURIComponent(g) : '') },
          h('span', { class: 'metric__value' }, fmt.int(s.n_evaluated), h('small', null, ' ' + t('dash.ofN', { n: s.n_conversations }))),
          h('span', { class: 'metric__label' }, t('dash.mEvaluated'))),
        h('div', { class: 'metric' },
          h('span', { class: 'metric__value' }, s.avg_total == null ? '—' : fmt.score(s.avg_total)),
          h('span', { class: 'metric__label' }, t('dash.mAverage'))),
        h('a', { class: 'metric metric--gross', href: '#/risks' },
          h('span', { class: 'metric__value' }, fmt.int(s.gross_count)),
          h('span', { class: 'metric__label' }, t('dash.mGross', { n: s.gross_count }))),
        h('div', { class: 'metric metric--loss' },
          h('span', { class: 'metric__value' }, fmt.int(s.unanswered_questions)),
          h('span', { class: 'metric__label' }, t('dash.mUnans', { n: s.unanswered_questions }))),
        exact != null ? h('a', { class: 'metric metric--ai', href: '#/quality', 'data-tip': t('dash.aiTip') },
          icon('target', 'icon--24'),
          h('span', { class: 'metric__label' }, t('dash.aiBefore'), ' ', h('b', { class: 'metric__value', style: { fontSize: 'inherit' } }, fmt.pct1(exact)), ' ', t('dash.aiAfter')),
          icon('chevronRight')) : null);
    }

    // ------------------------------------------------------------ радар и вопросы
    renderRadar(r, convs) {
      this.risksData = r;
      if (!r) { CQA.dom.replace(this.radar, ui.error(new Error(t('state.errorTitle')), this.loadAll.bind(this))); return; }
      var ops = {};
      (convs || []).forEach(function (c) { if (c.operator_name) ops[c.uid] = c.operator_name; });
      this.renderRadarMeta();
      var el = document.createElement('cqa-risk-radar');
      el.data = { risks: r, limit: 6, operators: ops };
      CQA.dom.replace(this.radar, el);
    }

    renderRadarMeta() {
      var r = this.risksData;
      if (!r) return;
      var sent = r.items.filter(function (it) { return it.compliance; }).length;
      this.radarMeta.textContent = r.total ? t('risk.subShort', { n: r.total, s: sent }) : '';
    }

    renderUnans(d) {
      if (!d) { CQA.dom.replace(this.unans, ui.error(new Error(t('state.errorTitle')), this.loadAll.bind(this))); return; }
      this.unansMeta.textContent = d.n_with_signals ? t('unans.subShort', { q: d.total_questions, c: d.n_conversations }) : '';
      var el = document.createElement('cqa-unanswered');
      el.limit = 5;
      el.data = d;
      CQA.dom.replace(this.unans, el);
    }

    loadReasons() {
      var self = this, g = this.group;
      this.reasonsLoaded = true;
      ui.load(this.reasonsBox, ui.skeleton('table', 5), function () { return CQA.api.reasons(g); }, function (d) {
        var el = document.createElement('cqa-reasons');
        el.data = d;
        self.reasonsBox.appendChild(el);
      });
    }
  }

  customElements.define('cqa-page-dashboard', CqaPageDashboard);
})(window.CQA = window.CQA || {});
