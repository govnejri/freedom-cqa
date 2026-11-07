/* <cqa-page-run> — прогресс прогона #/runs/{id}: опрос GET /runs/{id} раз в секунду, повтор упавших, отмена. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  var ACTIVE = { queued: 1, running: 1 };

  class CqaPageRun extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.id_ = this.params.id;
      document.title = t('run.title', { id: this.id_ }) + ' · CQA';
      this.box = h('div', null, ui.skeleton('card', 4));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page run-page' },
        h('a', { class: 'back-link', href: '#/' }, icon('arrowLeft', 'icon--16'), t('nav.dashboard')),
        h('div', { class: 'page-header' }, h('h1', null, t('run.title', { id: this.id_ }))),
        this.box));
      this.first = true;
      this.poll();
    }

    disconnectedCallback() { clearTimeout(this.timer); this.stopped = true; }

    poll() {
      var self = this;
      clearTimeout(this.timer);
      CQA.api.run(this.id_).then(function (run) {
        if (self.stopped) return;
        self.errors = 0;
        self.render(run);
        if (ACTIVE[run.status]) self.timer = setTimeout(function () { self.poll(); }, 1000);
      }, function (err) {
        if (self.stopped) return;
        self.errors = (self.errors || 0) + 1;
        if (self.first || err.status === 404) {
          CQA.dom.replace(self.box, ui.error(err, function () { self.first = true; CQA.dom.replace(self.box, ui.skeleton('card', 4)); self.poll(); }));
          return;
        }
        // временный сбой опроса — продолжаем, показывая предупреждение
        if (self.warn) CQA.dom.replace(self.warn, ui.msgbar('warning', t('run.pollFailed'), err.message));
        self.timer = setTimeout(function () { self.poll(); }, Math.min(10000, 1000 * Math.pow(2, self.errors)));
      });
    }

    render(run) {
      var self = this;
      this.first = false;
      var processed = run.units_done + run.units_failed;
      var pct = run.units_total ? processed / run.units_total : 0;
      var active = !!ACTIVE[run.status];
      var tone = CQA.runStatusTone(run.status);

      // опрос перерисовывает карточку раз в секунду — сохраняем фокус клавиатуры на кнопке
      var focused = document.activeElement && this.box.contains(document.activeElement) ? document.activeElement.getAttribute('data-action') : null;
      var retry = h('button', { class: 'btn', type: 'button', 'data-action': 'retry', disabled: !run.units_failed || active }, icon('refresh'), t('run.retryFailed'));
      retry.addEventListener('click', function () { self.act(retry, CQA.api.retryFailed(self.id_), t('run.retried')); });
      var cancel = h('button', { class: 'btn btn--danger', type: 'button', 'data-action': 'cancel', disabled: !active }, icon('stop'), t('run.cancel'));
      cancel.addEventListener('click', function () {
        ui.confirm(t('run.cancelConfirmTitle'), t('run.cancelConfirm'), t('run.cancel'), true).then(function (ok) {
          if (ok) self.act(cancel, CQA.api.cancelRun(self.id_), t('run.cancelled'));
        });
      });

      var stat = function (label, value, cls) {
        return h('div', { class: 'run-stat' + (cls ? ' ' + cls : '') }, h('div', { class: 'run-stat__label' }, label), h('div', { class: 'run-stat__value num' }, value));
      };

      this.warn = h('div', { 'aria-live': 'polite' });
      var criteria = run.metrics && run.metrics.criteria;
      CQA.dom.replace(this.box, h('div', { class: 'stack stack--l' },
        h('section', { class: 'card' },
          h('div', { class: 'card__header' },
            h('div', { class: 'row' },
              ui.badge(CQA.i18n.tx('runStatus.' + run.status, run.status), tone, run.status === 'running' ? null : run.status === 'done' ? 'checkCircle' : null),
              run.status === 'running' ? ui.spinner() : null,
              h('span', { class: 'muted' }, CQA.i18n.tx('runKind.' + run.kind, run.kind))),
            h('div', { class: 'row' }, retry, cancel)),
          h('div', { class: 'run-progress' },
            h('div', { class: 'row row--between' },
              h('span', { class: 't-subtitle2 num' }, fmt.pct(pct)),
              h('span', { class: 'muted num' }, t('run.units', { done: processed, total: run.units_total }))),
            h('div', { class: 'progress progress--l' + (run.status === 'queued' ? ' progress--indeterminate' : ''), role: 'progressbar',
              'aria-label': t('run.progress'), 'aria-valuemin': '0', 'aria-valuemax': String(run.units_total), 'aria-valuenow': String(processed) },
              h('div', { class: 'progress__bar', style: { width: (pct * 100) + '%' } }),
              run.units_failed && run.units_total ? h('div', { class: 'progress__bar progress__bar--failed', style: { left: (run.units_done / run.units_total * 100) + '%', width: (run.units_failed / run.units_total * 100) + '%' } }) : null)),
          h('div', { class: 'run-stats' },
            stat(t('run.done'), fmt.num(run.units_done, 0), 'run-stat--done'),
            stat(t('run.failed'), fmt.num(run.units_failed, 0), run.units_failed ? 'run-stat--failed' : ''),
            stat(t('run.cached'), fmt.num(run.units_cached, 0)),
            stat(t('run.cost'), fmt.money(run.cost_usd)),
            stat(t('run.eta'), active ? (run.eta_sec != null ? fmt.duration(run.eta_sec) : t('run.etaUnknown')) : '—')),
          this.warn),
        h('section', { class: 'card' },
          h('dl', { class: 'kv' },
            h('dt', null, t('run.checklistVersion')), h('dd', null, h('a', { href: '#/checklists/' + encodeURIComponent(CQA.config.checklistId) + '/v/' + run.checklist_version_id }, '#' + run.checklist_version_id)),
            h('dt', null, t('run.model')), h('dd', null, run.model + ' · ' + run.judge_profile),
            criteria ? [h('dt', null, t('run.criteria')), h('dd', null, criteria.join(', '))] : null,
            h('dt', null, t('run.created')), h('dd', null, fmt.date(run.created_at)),
            h('dt', null, t('run.started')), h('dd', null, fmt.date(run.started_at)),
            h('dt', null, t('run.finished')), h('dd', null, fmt.date(run.finished_at)))),
        run.kind === 'dry_run' ? h('div', { class: 'row' },
          h('a', { class: 'btn btn--primary', href: '#/runs/' + run.id + '/diff' }, icon('compare'), t('rd.open'))) : null,
        !active && run.kind !== 'dry_run' ? h('div', { class: 'row' },
          h('a', { class: 'btn btn--primary', href: '#/conversations' }, icon('chat'), t('run.openList')),
          run.units_failed ? h('a', { class: 'btn', href: '#/conversations?review=1' }, t('run.openReview')) : null) : null));
      if (focused) {
        var again = this.box.querySelector('[data-action="' + focused + '"]');
        if (again && !again.disabled) again.focus();
      }
    }

    act(btn, promise, okMsg) {
      var self = this;
      btn.disabled = true;
      promise.then(function () { ui.toast(okMsg, { tone: 'success' }); self.poll(); }, function (err) {
        btn.disabled = false;
        ui.toast(err.message, { tone: 'error' });
      });
    }
  }

  customElements.define('cqa-page-run', CqaPageRun);
})(window.CQA = window.CQA || {});
