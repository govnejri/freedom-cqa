/* <cqa-page-dashboard> — сводка #/: счётчики, загрузка набора, «Оценить всё», последние прогоны, грубые ошибки. */
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

  class CqaPageDashboard extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.dashboard') + ' · CQA';
      this.kpis = h('div', null, ui.skeleton('tiles', 6));
      this.actions = h('div', { class: 'dash-actions' });
      this.runsBox = h('div', null, ui.skeleton('table', 3));
      this.grossBox = h('div', null, ui.skeleton('text', 3));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page' },
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('dash.title')), h('div', { class: 'page-header__sub' }, t('dash.subtitle')))),
        h('div', { class: 'stack stack--xl' },
          this.kpis,
          h('div', { class: 'dash-grid' },
            h('section', { class: 'card', 'aria-labelledby': 'dash-data' },
              h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'dash-data' }, t('dash.dataTitle'))),
              this.actions),
            h('section', { class: 'card', 'aria-labelledby': 'dash-gross' },
              h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'dash-gross' }, t('dash.grossTitle')),
                h('a', { href: '#/conversations?gross=1' }, t('dash.showAll'))),
              this.grossBox)),
          h('section', { class: 'card', 'aria-labelledby': 'dash-runs' },
            h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'dash-runs' }, t('dash.runsTitle'))),
            this.runsBox))));
      this.renderActions();
      this.loadStats();
      this.loadRuns();
    }

    loadStats() {
      var self = this;
      ui.load(this.kpis, ui.skeleton('tiles', 6), function () {
        return Promise.all([CQA.api.conversations({}), CQA.api.appeals('open'), CQA.api.datasets().catch(function () { return []; })]);
      }, function (res) {
        var list = res[0], open = res[1];
        self.datasets = res[2] || [];
        self.fillGroups();
        var evaluated = list.filter(function (s) { return s.final_total != null || s.total != null; });
        var avg = evaluated.length ? evaluated.reduce(function (a, s) { return a + (s.final_total != null ? s.final_total : s.total); }, 0) / evaluated.length : null;
        var gross = list.filter(function (s) { return s.gross; });
        var review = list.filter(function (s) { return s.needs_review; });
        self.kpis.appendChild(h('div', { class: 'kpis' },
          kpi(t('dash.kConversations'), fmt.num(list.length, 0), { icon: 'chat', href: '#/conversations' }),
          kpi(t('dash.kEvaluated'), fmt.num(evaluated.length, 0), { icon: 'checkCircle', note: list.length ? t('dash.kEvaluatedNote', { p: fmt.pct(evaluated.length / list.length) }) : null }),
          kpi(t('dash.kAverage'), avg == null ? '—' : fmt.score(CQA.scoring.round2(avg)), { icon: 'checklist', tone: 'brand', note: t('dash.kAverageNote') }),
          kpi(t('dash.kGross'), fmt.num(gross.length, 0), { icon: 'warning', tone: gross.length ? 'danger' : null, href: '#/conversations?gross=1' }),
          kpi(t('dash.kReview'), fmt.num(review.length, 0), { icon: 'eye', tone: review.length ? 'warning' : null, href: '#/conversations?review=1' }),
          kpi(t('dash.kAppeals'), fmt.num(open.length, 0), { icon: 'scale', tone: open.length ? 'warning' : null, href: '#/appeals?status=open' })));
        self.renderGross(gross);
      });
    }

    renderGross(list) {
      var box = this.grossBox;
      box.textContent = '';
      if (!list.length) { box.appendChild(h('p', { class: 'muted' }, t('dash.noGross'))); return; }
      var ul = h('ul', { class: 'gross-list' });
      list.slice(0, 6).forEach(function (s) {
        ul.appendChild(h('li', null, h('a', { class: 'gross-item', href: '#/c/' + encodeURIComponent(s.uid) + '?crit=08' },
          h('span', { class: 'gross-item__id' }, s.external_id),
          h('span', { class: 'row' }, (s.gross_categories || []).map(function (c) { return ui.badge(c, 'danger'); })),
          h('span', { class: 'spacer' }),
          CQA.score(s.final_total != null ? s.final_total : s.total),
          icon('chevronRight', 'icon--16 muted'))));
      });
      box.appendChild(ul);
    }

    renderActions() {
      var self = this;
      this.groupInput = h('input', { class: 'input', id: 'dash-group', value: (CQA.api.launchData && CQA.api.launchData.group) || 'DEV', autocomplete: 'off', spellcheck: 'false' });
      this.loadBtn = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('download'), t('dash.loadDataset'));
      var fileInput = h('input', { type: 'file', accept: '.json,application/json', class: 'sr-only', id: 'dash-file' });
      fileInput.addEventListener('change', function () { if (fileInput.files[0]) self.upload(fileInput.files[0], fileInput); });
      var loadForm = h('form', { class: 'stack' },
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: 'dash-group' }, t('dash.groupId')),
          h('div', { class: 'row row--nowrap' }, this.groupInput, this.loadBtn),
          h('div', { class: 'field__hint' }, t('dash.groupHint'), ' ',
            h('label', { for: 'dash-file', class: 'link-label', tabindex: '0', onKeydown: function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } } }, t('dash.uploadFile')), fileInput)));
      loadForm.addEventListener('submit', function (e) { e.preventDefault(); self.fetchDataset(); });

      this.runGroup = h('select', { class: 'select', id: 'dash-run-group' }, h('option', { value: '' }, t('dash.allGroups')));
      this.force = h('input', { type: 'checkbox' });
      this.runBtn = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('flash'), t('dash.evaluateAll'));
      var runForm = h('form', { class: 'stack' },
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: 'dash-run-group' }, t('dash.evaluateTitle')),
          h('div', { class: 'row row--nowrap' }, this.runGroup, this.runBtn),
          h('label', { class: 'check check--small' }, this.force, t('dash.force'))));
      runForm.addEventListener('submit', function (e) { e.preventDefault(); self.evaluateAll(); });
      this.status = h('div', { 'aria-live': 'polite' });
      CQA.dom.replace(this.actions, h('div', { class: 'stack stack--l' }, loadForm, h('hr', { class: 'divider' }), runForm, this.status));
    }

    fillGroups() {
      var sel = this.runGroup;
      if (!sel) return;
      var cur = sel.value;
      while (sel.options.length > 1) sel.remove(1);
      (this.datasets || []).forEach(function (d) { sel.appendChild(h('option', { value: d.group_id }, d.group_id + ' · ' + t('dash.records', { n: d.total_records }))); });
      if (this.datasets && this.datasets.length === 1) sel.value = this.datasets[0].group_id;
      else sel.value = cur;
    }

    busy(btn, on, label, iconName) {
      btn.disabled = on;
      CQA.dom.replace(btn, on ? ui.spinner() : icon(iconName), label);
    }

    fetchDataset() {
      var self = this, g = this.groupInput.value.trim();
      if (!g) { this.groupInput.setAttribute('aria-invalid', 'true'); this.groupInput.focus(); return; }
      this.groupInput.removeAttribute('aria-invalid');
      this.busy(this.loadBtn, true, t('dash.loading'), 'download');
      CQA.api.fetchDataset(g).then(function (res) {
        CQA.dom.replace(self.status, ui.msgbar('success', t('dash.loaded', { g: (res && res.group_id) || g, n: res && res.total_records != null ? res.total_records : '?' })));
        self.loadStats();
      }, function (err) {
        CQA.dom.replace(self.status, ui.msgbar('danger', t('dash.loadFailed'), err.message));
      }).finally(function () { self.busy(self.loadBtn, false, t('dash.loadDataset'), 'download'); });
    }

    upload(file, input) {
      var self = this;
      CQA.dom.replace(this.status, h('div', { class: 'row' }, ui.spinner(), t('dash.uploading', { name: file.name })));
      CQA.api.uploadDataset(file).then(function (res) {
        CQA.dom.replace(self.status, ui.msgbar('success', t('dash.loaded', { g: (res && res.group_id) || file.name, n: res && res.total_records != null ? res.total_records : '?' }), res && res.note));
        self.loadStats();
      }, function (err) {
        CQA.dom.replace(self.status, ui.msgbar('danger', t('dash.loadFailed'), err.message));
      }).finally(function () { input.value = ''; });
    }

    evaluateAll() {
      var self = this;
      this.busy(this.runBtn, true, t('dash.starting'), 'flash');
      CQA.api.createRun({ group: this.runGroup.value || null, force: this.force.checked, kind: 'batch' }).then(function (run) {
        CQA.router.go('/runs/' + run.id);
      }, function (err) {
        self.busy(self.runBtn, false, t('dash.evaluateAll'), 'flash');
        CQA.dom.replace(self.status, ui.msgbar('danger', t('run.createFailed'), err.message));
      });
    }

    loadRuns() {
      var self = this;
      ui.load(this.runsBox, ui.skeleton('table', 3), function () { return CQA.api.runs(); }, function (runs) {
        if (!runs.length) { self.runsBox.appendChild(h('p', { class: 'muted' }, t('dash.noRuns'))); return; }
        var tbody = h('tbody');
        runs.slice(0, 8).forEach(function (r) {
          var processed = r.units_done + r.units_failed;
          var pct = r.units_total ? processed / r.units_total : 0;
          var tr = h('tr', { class: 'is-clickable' },
            h('td', null, h('a', { href: '#/runs/' + r.id }, '#' + r.id)),
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
          tr.addEventListener('click', function (e) { if (!e.target.closest('a')) location.hash = '#/runs/' + r.id; });
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
