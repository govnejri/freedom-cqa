/* «Загрузка и оценка» — служебный конвейер в выезжающей панели: загрузить набор, оценить диалоги, посчитать сигналы,
   последние прогоны. Раньше эти кнопки стояли главными на сводке руководителя; теперь — в «Очереди» и в Ctrl+K.
   CQA.openPipeline({group, focus: 'signals'}) */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

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

  function groupOptions(sel, datasets, value) {
    CQA.dom.clear(sel);
    sel.appendChild(h('option', { value: '' }, t('dash.allGroups')));
    (datasets || []).forEach(function (d) {
      sel.appendChild(h('option', { value: d.group_id }, d.group_id + ' · ' + t('dash.records', { n: d.n_conversations != null ? d.n_conversations : d.total_records })));
    });
    sel.value = value || '';
  }

  CQA.openPipeline = function (opts) {
    opts = opts || {};
    var group = opts.group || null;
    var sections = {};
    var datasets = [];
    var stopped = false, sigTimer = null;

    // ---- загрузить набор
    var groupInput = h('input', { class: 'input', id: 'pl-group', value: (CQA.api.launchData && CQA.api.launchData.group) || 'DEV', autocomplete: 'off', spellcheck: 'false' });
    var fileInput = h('input', { type: 'file', accept: '.json,application/json', class: 'sr-only', id: 'pl-file' });
    var loadStatus = h('div', { 'aria-live': 'polite' });
    var loadBtn = h('button', { class: 'btn btn--primary', type: 'button' }, icon('download'), t('dash.loadDataset'));
    function loaded(res, g) {
      CQA.dom.replace(loadStatus, ui.msgbar('success', t('dash.loaded', { g: (res && res.group_id) || g, n: res && res.total_records != null ? res.total_records : '?' }), res && res.note));
      refreshDatasets((res && res.group_id) || group);
      document.dispatchEvent(new CustomEvent('cqa-data-changed'));
    }
    loadBtn.addEventListener('click', function () {
      var g = groupInput.value.trim();
      if (!g) { groupInput.setAttribute('aria-invalid', 'true'); groupInput.focus(); return; }
      groupInput.removeAttribute('aria-invalid');
      loadBtn.disabled = true;
      CQA.dom.replace(loadBtn, ui.spinner(), t('dash.loading'));
      CQA.api.fetchDataset(g).then(function (res) { loaded(res, g); }, function (err) {
        CQA.dom.replace(loadStatus, ui.msgbar('danger', t('dash.loadFailed'), err.message));
      }).finally(function () { loadBtn.disabled = false; CQA.dom.replace(loadBtn, icon('download'), t('dash.loadDataset')); });
    });
    fileInput.addEventListener('change', function () {
      var file = fileInput.files[0];
      if (!file) return;
      CQA.dom.replace(loadStatus, h('div', { class: 'row' }, ui.spinner(), t('dash.uploading', { name: file.name })));
      CQA.api.uploadDataset(file, groupInput.value.trim() || null).then(function (res) { loaded(res, file.name); }, function (err) {
        CQA.dom.replace(loadStatus, ui.msgbar('danger', t('dash.loadFailed'), err.message));
      }).finally(function () { fileInput.value = ''; });
    });
    sections.load = section('download', t('pipe.loadTitle'), t('pipe.loadText'),
      h('div', { class: 'field' },
        h('label', { class: 'field__label', for: 'pl-group' }, t('dash.groupId')),
        h('div', { class: 'row row--nowrap' }, groupInput, loadBtn),
        h('div', { class: 'field__hint' }, t('dash.groupHint'), ' ',
          h('label', { for: 'pl-file', class: 'link-label', tabindex: '0', onKeydown: function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } } }, t('dash.uploadFile')), fileInput)),
      loadStatus);

    // ---- оценить набор
    var evalSel = h('select', { class: 'select', id: 'pl-eval-group' });
    var force = h('input', { type: 'checkbox' });
    var evalStatus = h('div', { 'aria-live': 'polite' });
    var estimate = h('p', { class: 'pipe-estimate', 'aria-live': 'polite' });
    /** Сколько диалогов и примерно сколько это стоит — до запуска. */
    function renderEstimate() {
      var g = evalSel.value || null;
      var list = (datasets || []).filter(function (d) { return !g || d.group_id === g; });
      var n = list.reduce(function (a, d) { return a + (d.n_conversations != null ? d.n_conversations : d.total_records || 0); }, 0);
      CQA.dom.replace(estimate, icon('info', 'icon--16'), h('span', null, n
        ? t(force.checked ? 'pipe.estimateForce' : 'pipe.estimate', { n: n, c: fmt.money(n * 0.024) })
        : t('pipe.estimateNone')));
    }
    evalSel.addEventListener('change', renderEstimate);
    force.addEventListener('change', renderEstimate);
    var evalBtn = h('button', { class: 'btn btn--primary', type: 'button' }, icon('flash'), t('pipe.evaluate'));
    evalBtn.addEventListener('click', function () {
      evalBtn.disabled = true;
      CQA.dom.replace(evalBtn, ui.spinner(), t('dash.starting'));
      CQA.api.createRun({ group: evalSel.value || null, force: force.checked, kind: 'batch' }).then(function (run) {
        drawer.close();
        CQA.router.go('/runs/' + run.id);
      }, function (err) {
        evalBtn.disabled = false;
        CQA.dom.replace(evalBtn, icon('flash'), t('pipe.evaluate'));
        CQA.dom.replace(evalStatus, ui.msgbar('danger', t('run.createFailed'), err.message));
      });
    });
    sections.evaluate = section('flash', t('pipe.evalTitle'), t('dash.evaluateIntro'),
      h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'pl-eval-group' }, t('apply.group')), evalSel),
      h('label', { class: 'check check--small' }, force, t('pipe.force')),
      estimate,
      h('div', null, evalBtn), evalStatus);

    // ---- сигналы
    var sigStatus = h('div', { 'aria-live': 'polite' });
    var sigBtn = h('button', { class: 'btn', type: 'button' }, icon('sparkle'), t('dash.signals'));
    function sigDone() { sigBtn.disabled = false; CQA.dom.replace(sigBtn, icon('sparkle'), t('dash.signals')); }
    function poll(t0, queued, before, g) {
      clearTimeout(sigTimer);
      sigTimer = setTimeout(function () {
        if (stopped) return;
        CQA.api.insightsSummary(g).then(function (s) {
          var done = s.n_with_signals - before;
          if (done >= queued || s.n_with_signals >= s.n_evaluated || Date.now() - t0 > 180000) {
            CQA.dom.replace(sigStatus, ui.msgbar('success', t('dash.signalsReady', { n: Math.max(0, done) })));
            sigDone();
            document.dispatchEvent(new CustomEvent('cqa-data-changed'));
          } else {
            CQA.dom.replace(sigStatus, ui.msgbar('info', h('span', { class: 'row' }, ui.spinner(), t('dash.signalsProgress', { d: Math.max(0, done), n: queued }))));
            poll(t0, queued, before, g);
          }
        }, function () { poll(t0, queued, before, g); });
      }, 3000);
    }
    sigBtn.addEventListener('click', function () {
      var g = evalSel.value || null;
      sigBtn.disabled = true;
      CQA.dom.replace(sigBtn, ui.spinner(), t('dash.signalsStarting'));
      CQA.api.insightsSummary(g).catch(function () { return {}; }).then(function (s) {
        var before = s.n_with_signals || 0;
        return CQA.api.runSignals({ group: g }).then(function (r) {
          if (r.status === 'nothing_to_do') {
            CQA.dom.replace(sigStatus, ui.msgbar('info', t('dash.signalsNothing', { n: r.already })));
            sigDone();
            return;
          }
          CQA.dom.replace(sigStatus, ui.msgbar('info', h('span', { class: 'row' }, ui.spinner(), t('dash.signalsRunning', { n: r.queued, p: r.profile }))));
          poll(Date.now(), r.queued, before, g);
        });
      }).catch(function (err) {
        CQA.dom.replace(sigStatus, ui.msgbar('danger', t('dash.signalsFailed'), err.message));
        sigDone();
      });
    });
    sections.signals = section('sparkle', t('pipe.sigTitle'), t('pipe.sigText'), h('div', null, sigBtn), sigStatus);

    // ---- прогоны
    var runsBox = h('div', null, ui.skeleton('table', 3));
    sections.runs = section('history', t('dash.runsTitle'), null, runsBox);

    var drawer = ui.drawer({
      title: t('pipe.title'), sub: t('pipe.sub'), wide: true,
      body: h('div', { class: 'stack stack--xl' }, sections.load, sections.evaluate, sections.signals, sections.runs),
      onClose: function () { stopped = true; clearTimeout(sigTimer); }
    });

    function refreshDatasets(sel) {
      return CQA.api.datasets().catch(function () { return []; }).then(function (list) {
        datasets = list || [];
        group = sel || group || CQA.defaultGroup(datasets);
        groupOptions(evalSel, datasets, group);
        renderEstimate();
      });
    }
    refreshDatasets(group);
    ui.load(runsBox, ui.skeleton('table', 3), function () { return CQA.api.runs(); }, function (runs) {
      if (!runs.length) { runsBox.appendChild(h('p', { class: 'muted' }, t('dash.noRuns'))); return; }
      var tbody = h('tbody');
      runs.slice(0, 8).forEach(function (r) {
        var processed = r.units_done + r.units_failed;
        var pct = r.units_total ? processed / r.units_total : 0;
        var href = r.kind === 'dry_run' ? '#/runs/' + r.id + '/diff' : '#/runs/' + r.id;
        tbody.appendChild(h('tr', null,
          h('td', null, h('a', { href: href, onClick: function () { drawer.close(); } }, '#' + r.id)),
          h('td', null, CQA.i18n.tx('runKind.' + r.kind, r.kind)),
          h('td', null, ui.badge(CQA.i18n.tx('runStatus.' + r.status, r.status), CQA.runStatusTone(r.status))),
          h('td', { class: 'run-progress-cell' },
            h('div', { class: 'row row--nowrap' },
              h('div', { class: 'progress grow' }, h('div', { class: 'progress__bar', style: { width: Math.round(pct * 100) + '%' } })),
              h('span', { class: 'num t-caption1 muted nowrap' }, processed + '/' + r.units_total))),
          h('td', { class: 'num' }, fmt.money(r.cost_usd)),
          h('td', { class: 'nowrap muted' }, fmt.date(r.created_at))));
      });
      runsBox.appendChild(h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, '№'), h('th', { scope: 'col' }, t('run.kind')), h('th', { scope: 'col' }, t('run.status')),
          h('th', { scope: 'col' }, t('run.progress')), h('th', { scope: 'col', class: 'num' }, t('run.cost')), h('th', { scope: 'col' }, t('run.created')))),
        tbody)));
    });
    if (opts.focus && sections[opts.focus]) setTimeout(function () { sections[opts.focus].scrollIntoView({ block: 'start' }); }, 50);
    return drawer;
  };

  function section(ic, title, text) {
    var rest = Array.prototype.slice.call(arguments, 3);
    return h('section', { class: 'drawer__section' }, h('h3', null, icon(ic), title), text ? h('p', { class: 'muted t-caption1' }, text) : null, rest);
  }
})(window.CQA = window.CQA || {});
