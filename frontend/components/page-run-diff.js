/* <cqa-page-run-diff> — «было/стало» пробного прогона #/runs/{id}/diff: прогресс (опрос), сводка (сколько вердиктов
   изменилось, средний сдвиг итога, переходы «Да → Нет»), таблица по диалогам; «Сделать активной», «Применить ко всем». */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var ACTIVE = { queued: 1, running: 1 };

  class CqaPageRunDiff extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.rid = Number(this.params.id);
      this.onlyChanged = false;
      this.open = new Set();
      document.title = t('rd.title', { id: this.rid }) + ' · CQA';
      this.head = h('div', null, h('span', { class: 'skeleton skeleton--title' }));
      this.box = h('div', { class: 'stack stack--l' }, ui.skeleton('card', 6));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page rd-page' }, this.head, this.box));
      this.loadVersions();
      this.poll();
    }

    disconnectedCallback() { clearTimeout(this.timer); this.stopped = true; }

    loadVersions() {
      var self = this;
      return CQA.api.checklistVersions(CQA.config.checklistId).then(function (list) {
        self.versions = list || [];
        if (self.diff) self.render();
      }, function () { self.versions = []; });
    }

    version(id) { return (this.versions || []).filter(function (v) { return v.id === id; })[0] || null; }

    poll() {
      var self = this;
      clearTimeout(this.timer);
      CQA.api.runDiff(this.rid).then(function (d) {
        if (self.stopped) return;
        self.errors = 0;
        self.diff = d;
        self.render();
        if (ACTIVE[d.run.status]) self.timer = setTimeout(function () { self.poll(); }, 1500);
      }, function (err) {
        if (self.stopped) return;
        self.errors = (self.errors || 0) + 1;
        if (!self.diff || err.status === 404) {
          CQA.dom.replace(self.head, h('a', { class: 'back-link', href: '#/checklists/' + encodeURIComponent(CQA.config.checklistId) }, icon('arrowLeft', 'icon--16'), t('nav.checklist')));
          CQA.dom.replace(self.box, ui.error(err, function () { self.poll(); }));
          return;
        }
        self.timer = setTimeout(function () { self.poll(); }, Math.min(10000, 1500 * Math.pow(2, self.errors)));
      });
    }

    render() {
      var self = this, d = this.diff, run = d.run, s = d.summary;
      var active = !!ACTIVE[run.status];
      var cid = encodeURIComponent(CQA.config.checklistId);
      var ver = this.version(d.checklist_version_id);
      var isActive = ver ? ver.is_active : false;
      var critTitles = {}, meta = {};
      if (ver && ver.config) (ver.config.criteria || []).forEach(function (c) { meta[c.id] = { type: c.type, top: CQA.scoring.top(c) }; });
      var vb = function (cid, v, opts) { var m = meta[cid] || {}; return CQA.verdict(v, m.type || 'yes_no', m.top, opts); };
      d.dialogs.forEach(function (x) { x.criteria.forEach(function (c) { critTitles[c.criterion_id] = c.title; }); });

      CQA.dom.replace(this.head,
        h('a', { class: 'back-link', href: '#/checklists/' + cid + '/v/' + d.checklist_version_id }, icon('arrowLeft', 'icon--16'), t('rd.back', { v: d.checklist_version })),
        h('div', { class: 'page-header' },
          h('div', null,
            h('div', { class: 'row' }, h('h1', null, t('rd.title', { id: run.id })),
              ui.badge(CQA.i18n.tx('runStatus.' + run.status, run.status), CQA.runStatusTone(run.status), run.status === 'done' ? 'checkCircle' : null)),
            h('div', { class: 'page-header__sub' }, t('rd.sub', {
              v: d.checklist_version, b: d.compared_with_version != null ? 'v' + d.compared_with_version : t('rd.current'),
              c: d.criteria.join(', '), n: s.dialogs, m: run.model }))),
          h('div', { class: 'row' },
            !isActive ? h('button', { class: 'btn', type: 'button', disabled: active || !ver || null, onClick: function () { self.publish(this); } }, icon('checkCircle'), t('cl.makeActive'))
              : ui.badge(t('cl.active'), 'brand-filled', 'checkCircle'),
            h('button', { class: 'btn btn--primary', type: 'button', disabled: active || !ver || null, onClick: function () { self.applyAll(); } }, icon('flash'), t('rd.applyAll')))));

      // ---- прогресс
      var processed = run.units_done + run.units_failed;
      var pct = run.units_total ? processed / run.units_total : 0;
      var progress = h('section', { class: 'card rd-progress' },
        h('div', { class: 'row row--between' },
          h('span', { class: 'row' }, active ? ui.spinner() : icon(run.status === 'done' ? 'checkCircle' : 'info', 'rd-progress__icon'),
            h('b', null, active ? t('rd.running') : t('rd.finished'))),
          h('span', { class: 'muted num' }, t('rd.progress', { d: s.completed, n: s.dialogs, u: processed, t: run.units_total }) + ' · ' + fmt.money(run.cost_usd))),
        h('div', { class: 'progress progress--l' + (run.status === 'queued' ? ' progress--indeterminate' : ''), role: 'progressbar', 'aria-label': t('run.progress'),
          'aria-valuemin': '0', 'aria-valuemax': String(run.units_total), 'aria-valuenow': String(processed) },
          h('div', { class: 'progress__bar', style: { width: (pct * 100) + '%' } })),
        run.units_failed ? h('div', { class: 't-caption1', style: { color: 'var(--colorStatusDangerForeground1)' } }, t('run.failedN', { n: run.units_failed }),
          ' · ', h('a', { href: '#/runs/' + run.id }, t('rd.openRun'))) : null,
        h('div', { class: 'muted t-caption1' }, t('rd.note')));

      // ---- сводка
      var tile = function (label, value, note, tone) {
        return h('div', { class: 'kpi' + (tone ? ' kpi--' + tone : '') }, h('span', { class: 'kpi__label' }, label),
          h('span', { class: 'kpi__value' }, value), note ? h('span', { class: 'kpi__note' }, note) : null);
      };
      var yesNo = 0;
      Object.keys(s.transitions || {}).forEach(function (c) { yesNo += (s.transitions[c]['Да → Нет'] || 0); });
      var tiles = h('div', { class: 'kpis rd-kpis' },
        tile(t('rd.kChanged'), s.verdicts_changed + ' / ' + s.verdicts_compared, t('rd.kChangedNote', { n: s.dialogs_changed, d: s.with_baseline }), s.verdicts_changed ? 'warning' : null),
        tile(t('rd.kYesNo'), String(yesNo), t('rd.kYesNoNote'), yesNo ? 'danger' : null),
        tile(t('rd.kShift'), s.mean_delta == null ? '—' : fmt.signed(s.mean_delta), s.mean_abs_delta != null ? t('rd.kShiftNote', { a: fmt.num(s.mean_abs_delta, 2) }) : null,
          s.mean_delta == null ? null : s.mean_delta < 0 ? 'danger' : s.mean_delta > 0 ? 'brand' : null),
        tile(t('rd.kGross'), String(s.gross_changed), t('rd.kGrossNote'), s.gross_changed ? 'danger' : null));

      // ---- критерии вне версии, у которых вердикт всё же разошёлся (перенесён не из той оценки, что «было»)
      var extra = d.dialogs.map(function (x) {
        var list = x.criteria.filter(function (c) { return c.changed && !c.requested; });
        return list.length ? { x: x, list: list } : null;
      }).filter(Boolean);
      var extraNote = extra.length ? ui.msgbar('warning', t('rd.extraTitle', { n: extra.length }),
        h('span', null, t('rd.extraText'), ' ', extra.map(function (e, i) {
          return [i ? '; ' : '', h('a', { class: 'mono', href: '#/c/' + encodeURIComponent(e.x.uid) }, e.x.external_id), ': ',
            e.list.map(function (c) { return c.criterion_id + ' ' + (c.old || '—') + ' → ' + (c.new || '—'); }).join(', ')];
        }))) : null;

      // ---- переходы и распределение по критериям
      var crits = h('div', { class: 'rd-crits' }, d.criteria.map(function (c) {
        var tr = (s.transitions || {})[c] || {};
        var oldD = (s.old_verdicts || {})[c] || {}, newD = (s.new_verdicts || {})[c] || {};
        var dist = function (o) { return Object.keys(o).sort().map(function (k) { return k + ': ' + o[k]; }).join(', ') || '—'; };
        return h('div', { class: 'rd-crit' },
          h('div', { class: 'row' }, h('span', { class: 'mono' }, c), h('b', null, critTitles[c] || '')),
          h('div', { class: 'row rd-trans' }, Object.keys(tr).length ? Object.keys(tr).map(function (k) {
            return ui.badge(k + ' × ' + tr[k], /Да → Нет/.test(k) ? 'danger' : /Нет → Да/.test(k) ? 'success' : 'warning');
          }) : h('span', { class: 'muted t-caption1' }, t('rd.noTransitions'))),
          h('div', { class: 'muted t-caption1' }, t('rd.dist', { o: dist(oldD), n: dist(newD) })));
      }));

      // ---- таблица по диалогам
      var toggle = h('button', { class: 'toggle', type: 'button', 'aria-pressed': String(this.onlyChanged), onClick: function () { self.onlyChanged = !self.onlyChanged; self.render(); } },
        icon('filter'), t('rd.onlyChanged', { n: s.dialogs_changed }));
      var rows = d.dialogs.filter(function (x) { return !self.onlyChanged || x.changed_criteria.length || (x.delta && Math.abs(x.delta) >= 0.005); });
      var tbody = h('tbody');
      rows.forEach(function (x) {
        var by = {};
        x.criteria.forEach(function (c) { by[c.criterion_id] = c; });
        var changed = x.changed_criteria.length > 0;
        var isOpen = self.open.has(x.uid);
        var tr = h('tr', { class: 'is-clickable' + (changed ? ' rd-row--changed' : '') + (isOpen ? ' is-open' : ''), tabindex: '0', 'aria-expanded': String(isOpen) },
          h('td', null, h('div', { class: 'row row--nowrap' }, icon(isOpen ? 'chevronDown' : 'chevronRight', 'icon--16 muted'),
            h('a', { class: 'mono', href: '#/c/' + encodeURIComponent(x.uid) + (d.criteria[0] ? '?crit=' + d.criteria[0] : '') }, x.external_id)),
            h('div', { class: 'muted t-caption1' }, x.language + ' · ' + U.channelLabel(x.channel)),
            x.criteria.filter(function (c) { return c.changed && !c.requested; }).map(function (c) {
              return ui.badge(t('rd.extraBadge', { id: c.criterion_id }), 'warning', 'warning', { title: t('rd.extraBadgeTitle', { id: c.criterion_id, o: c.old || '—', n: c.new || '—' }) });
            })),
          d.criteria.map(function (c) {
            var r = by[c] || {};
            if (x.status !== 'done' && r.new == null) return h('td', null, ACTIVE[run.status] ? ui.spinner() : h('span', { class: 'muted' }, CQA.i18n.tx('evalStatus.' + x.status, x.status)));
            return h('td', null, h('div', { class: 'row row--nowrap rd-vv' },
              vb(c, r.old, { strike: r.changed }),
              r.changed ? icon('arrowRight', 'icon--16') : null,
              r.changed ? vb(c, r.new, { size: 'l' }) : h('span', { class: 'muted t-caption1' }, r.new != null ? t('rd.same') : '')));
          }),
          h('td', { class: 'num' }, x.old_total != null ? CQA.score(x.old_total) : '—'),
          h('td', { class: 'num' }, x.new_total != null ? CQA.score(x.new_total) : '—'),
          h('td', { class: 'num nowrap rd-delta' + (x.delta < 0 ? ' is-neg' : x.delta > 0 ? ' is-pos' : '') }, x.delta == null ? '—' : fmt.signed(x.delta)));
        var flip = function (e) {
          if (e.target.closest('a')) return;
          if (self.open.has(x.uid)) self.open.delete(x.uid); else self.open.add(x.uid);
          self.render();
        };
        tr.addEventListener('click', flip);
        tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(e); } });
        tbody.appendChild(tr);
        if (isOpen) {
          tbody.appendChild(h('tr', { class: 'rd-detail' }, h('td', { colspan: String(d.criteria.length + 4) },
            h('div', { class: 'stack' }, x.criteria.filter(function (c) { return c.requested || c.changed; }).map(function (c) {
              return h('div', { class: 'rd-expl' },
                h('div', { class: 'row' }, h('span', { class: 'mono' }, c.criterion_id), h('b', null, c.title)),
                h('div', { class: 'diff-pair' },
                  h('div', { class: 'diff-before' }, h('span', { class: 'diff-tag' }, t('diff.before') + ' · ' + (c.old || '—')), c.old_explanation || '—'),
                  h('div', { class: 'diff-after' }, h('span', { class: 'diff-tag' }, t('diff.after') + ' · ' + (c.new || '—')), c.new_explanation || '—')),
                c.new_evidence && c.new_evidence.length ? h('div', { class: 'row t-caption1' }, h('span', { class: 'muted' }, t('crit.evidence') + ':'),
                  c.new_evidence.map(function (mid) { return h('a', { class: 'mono', href: '#/c/' + encodeURIComponent(x.uid) + '?crit=' + c.criterion_id + '&m=' + mid }, mid); })) : null);
            }),
            x.evaluation_id ? h('div', { class: 'row t-caption1' }, h('a', { href: '#/log/' + x.evaluation_id }, icon('code', 'icon--16'), ' ', t('rd.newLog')),
              x.baseline_evaluation_id ? h('a', { href: '#/log/' + x.baseline_evaluation_id }, t('rd.oldLog')) : null) : null))));
        }
      });
      var table = rows.length ? h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact rd-table' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, t('rd.cDialog')),
          d.criteria.map(function (c) { return h('th', { scope: 'col' }, h('span', { class: 'mono' }, c), ' ', h('span', { class: 'rd-th-title' }, critTitles[c] || ''), h('div', { class: 'muted t-caption1' }, t('rd.oldNew'))); }),
          h('th', { scope: 'col', class: 'num' }, t('rd.cOld')), h('th', { scope: 'col', class: 'num' }, t('rd.cNew')), h('th', { scope: 'col', class: 'num' }, 'Δ'))),
        tbody)) : h('p', { class: 'muted' }, t('rd.noChangedRows'));

      CQA.dom.replace(this.box,
        d.demo_note ? ui.msgbar('warning', t('mode.mock'), d.demo_note) : null,
        progress,
        tiles,
        extraNote,
        h('section', { class: 'card', 'aria-labelledby': 'rd-crits' },
          h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'rd-crits' }, t('rd.critsTitle'))),
          crits),
        h('section', { class: 'card', 'aria-labelledby': 'rd-dialogs' },
          h('div', { class: 'card__header' }, h('div', null, h('h2', { class: 'card__title', id: 'rd-dialogs' }, t('rd.dialogsTitle')), h('div', { class: 'muted t-caption1' }, t('rd.dialogsHint'))), toggle),
          table),
        !active ? h('section', { class: 'card rd-next' },
          h('div', { class: 'row' }, icon('info'), h('b', null, t('rd.nextTitle'))),
          h('p', { class: 'muted' }, isActive ? t('rd.nextActive') : t('rd.nextText')),
          h('div', { class: 'row' },
            !isActive ? h('button', { class: 'btn', type: 'button', disabled: !ver || null, onClick: function () { self.publish(this); } }, icon('checkCircle'), t('cl.makeActive')) : null,
            h('button', { class: 'btn btn--primary', type: 'button', disabled: !ver || null, onClick: function () { self.applyAll(); } }, icon('flash'), t('rd.applyAll')),
            h('a', { class: 'btn btn--subtle', href: '#/checklists/' + cid + '/v/' + d.checklist_version_id }, icon('edit'), t('rd.backToEdit')))) : null);
    }

    publish(btn) {
      var self = this, vid = this.diff.checklist_version_id;
      if (btn) btn.disabled = true;
      CQA.api.publishVersion(vid).then(function () {
        ui.toast(t('cl.published', { v: self.diff.checklist_version }), { tone: 'success' });
        return self.loadVersions();
      }, function (err) {
        if (btn) btn.disabled = false;
        ui.toast(t('cl.publishFailed'), { tone: 'error', detail: err.message });
      });
    }

    applyAll() {
      var self = this, d = this.diff;
      var v = this.version(d.checklist_version_id);
      if (!v) return;
      var act = (this.versions || []).filter(function (x) { return x.is_active; })[0] || null;
      var base = d.compared_with_version_id ? this.version(d.compared_with_version_id) : null;
      var need = [v, act, base].filter(function (x) { return x && !x.config; });
      Promise.all(need.map(function (x) { return CQA.api.checklistVersion(x.id).then(function (full) { x.config = full.config; }); })).then(function () {
        CQA.openApplyDialog({ version: v, active: act, baseline: base || (act && act.id !== v.id ? act : null) });
      }, function (err) { ui.toast(err.message, { tone: 'error' }); });
    }
  }

  customElements.define('cqa-page-run-diff', CqaPageRunDiff);
})(window.CQA = window.CQA || {});
