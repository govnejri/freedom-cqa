/* <cqa-page-appeal> — экран проверяющего #/appeals/{id}: спорная оценка, комментарий, фрагмент диалога, мнение ИИ,
   решение «подтвердить» / «изменить» (новый вердикт из допустимых) с причиной → POST /appeals/{id}/decision. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  class CqaPageAppeal extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.aid = this.params.id;
      document.title = t('appeal.pageTitle', { id: this.aid }) + ' · CQA';
      this.root = h('div', { class: 'page appeal-page' });
      this.textContent = '';
      this.appendChild(this.root);
      var self = this;
      this.addEventListener('cqa-evidence', function (e) { self.goTo(e.detail.mid); });
      this.addEventListener('cqa-range', function (e) { self.goTo(e.detail.range[0]); });
      // во фрагменте нет плеера — «▶» у реплики звонка открывает карточку на этой секунде
      this.addEventListener('cqa-seek', function (e) {
        location.hash = '#/c/' + encodeURIComponent(self.appeal.uid) + '?crit=' + encodeURIComponent(self.appeal.criterion_id) + '&m=' + encodeURIComponent(e.detail.mid);
      });
      this.load();
    }

    load() {
      var self = this;
      CQA.dom.replace(this.root, ui.skeleton('card', 6));
      CQA.api.appeal(this.aid).then(function (a) {
        self.appeal = a;
        return CQA.api.evaluationById(a.evaluation_id).then(function (ev) {
          self.ev = ev;
          return CQA.api.checklistVersion(ev.checklist_version_id).then(function (v) { self.config = v.config; }, function () { self.config = null; });
        }, function () { self.ev = null; self.config = null; });
      }).then(function () { self.render(); }, function (err) {
        CQA.dom.replace(self.root,
          h('a', { class: 'back-link', href: '#/appeals' }, icon('arrowLeft', 'icon--16'), t('nav.appeals')),
          ui.error(err, function () { self.load(); }, err.status === 404 ? t('appeal.notFound') : null));
      });
    }

    critConfig() {
      var cid = this.appeal.criterion_id;
      return this.config ? (this.config.criteria || []).filter(function (c) { return c.id === cid; })[0] : null;
    }

    top() { var c = this.critConfig(); return c ? CQA.scoring.top(c) : null; }

    allowed() {
      var c = this.critConfig();
      if (c) return CQA.scoring.allowedVerdicts(c);
      var o = this.appeal.original || {};
      var base = ['Да', 'Нет'];
      if (o.verdict === 'N/A') base.push('N/A');
      return o.type === 'scale' ? [o.verdict].filter(Boolean) : base;
    }

    render() {
      var a = this.appeal, o = a.original || {}, self = this;
      var top = this.top();
      var isCall = (a.fragment || []).some(function (m) { return /T\d{2}:\d{2}/.test(m.ts_raw || ''); });
      var convHref = '#/c/' + encodeURIComponent(a.uid) + '?crit=' + encodeURIComponent(a.criterion_id);

      // ---- спорная оценка
      var crit = document.createElement('cqa-criterion');
      var shown = Object.assign({}, o, { appeal_id: null, appeal_status: null });
      crit.data = { cr: shown, top: top, selected: true, canAppeal: false };

      // ---- фрагмент
      this.fragment = document.createElement('cqa-transcript');
      this.fragment.data = { messages: a.fragment || [], channel: isCall ? 'voice call' : 'agent chat', compact: true };
      var tone = U.verdictTone(o.verdict, o.type, top);

      var left = h('div', { class: 'stack stack--l' },
        h('section', { class: 'card', 'aria-labelledby': 'ap-disputed' },
          h('h2', { class: 'card__title', id: 'ap-disputed', style: { marginBottom: '12px' } }, t('appeal.disputed')),
          crit),
        h('section', { class: 'card', 'aria-labelledby': 'ap-comment' },
          h('h2', { class: 'card__title', id: 'ap-comment', style: { marginBottom: '8px' } }, t('appeal.operatorComment')),
          h('div', { class: 'quote quote--brand' }, a.comment),
          a.counter_evidence && a.counter_evidence.length ? h('div', { class: 'crit__section', style: { marginTop: '12px' } },
            h('div', { class: 'crit__label' }, t('appeal.counter')),
            h('div', { class: 'evchips evchips--inline' }, a.counter_evidence.map(function (mid) {
              return h('button', { class: 'evchip evchip--plain', type: 'button', onClick: function () { self.goTo(mid); } }, h('span', { class: 'evchip__id' }, mid));
            }))) : null),
        h('section', { class: 'card', 'aria-labelledby': 'ap-fragment' },
          h('div', { class: 'card__header' },
            h('h2', { class: 'card__title', id: 'ap-fragment' }, t('appeal.fragment')),
            h('a', { class: 'btn btn--small', href: convHref }, icon('external'), t('appeal.openConversation'))),
          h('div', { class: 'appeal-fragment' }, this.fragment)));

      this.fragment.highlight({ evidence: (o.evidence || []).map(function (e) { return e.id; }), range: o.checked_range, tone: tone, label: t('conv.evidenceOf', { id: a.criterion_id }) });
      this.fragment.markExtra(a.counter_evidence, t('appeal.counterMark'));

      // ---- правая колонка
      this.right = h('div', { class: 'stack stack--l appeal-side' });
      if (a.ai_opinion) {
        var ai = a.ai_opinion;
        this.right.appendChild(h('section', { class: 'card ai-card', 'aria-labelledby': 'ap-ai' },
          h('div', { class: 'card__header' },
            h('h2', { class: 'card__title row', id: 'ap-ai' }, icon('sparkle'), t('appeal.aiOpinion')),
            ai.model ? h('span', { class: 'muted t-caption1' }, ai.model) : null),
          ai.verdict ? h('div', { class: 'row', style: { marginBottom: '6px' } }, h('span', { class: 'muted' }, t('appeal.aiVerdict')), CQA.verdict(ai.verdict, o.type, top, { size: 'l' })) : null,
          ai.explanation ? h('p', { class: 'subtle' }, ai.explanation) : null,
          h('div', { class: 'muted t-caption1', style: { marginTop: '8px' } }, t('appeal.aiNote'))));
      }
      this.decisionBox = h('div');
      this.right.appendChild(this.decisionBox);
      if (a.status === 'decided' && a.decision) this.renderResult(); else this.renderForm();

      CQA.dom.replace(this.root,
        h('a', { class: 'back-link', href: '#/appeals' }, icon('arrowLeft', 'icon--16'), t('nav.appeals')),
        h('div', { class: 'page-header' },
          h('div', null,
            h('div', { class: 'row' },
              h('h1', null, t('appeal.pageTitle', { id: a.id })),
              ui.badge(CQA.i18n.tx('appealStatus.' + a.status, a.status), a.status === 'decided' ? 'success' : 'warning')),
            h('div', { class: 'page-header__sub' },
              t('appeal.headerSub', { c: a.criterion_id + ' ' + a.criterion_title, d: fmt.date(a.created_at) }), ' · ',
              h('a', { href: convHref }, fmt.externalId(a.uid))))),
        h('div', { class: 'appeal-layout' }, left, this.right));
    }

    goTo(mid) {
      if (this.fragment && this.fragment.has(mid)) { this.fragment.focusMessage(mid); return; }
      location.hash = '#/c/' + encodeURIComponent(this.appeal.uid) + '?crit=' + encodeURIComponent(this.appeal.criterion_id) + '&m=' + encodeURIComponent(mid);
    }

    // ------------------------------------------------------------ форма решения
    renderForm() {
      var self = this, a = this.appeal, o = a.original || {};
      var top = this.top();
      var current = o.final_verdict || o.verdict;
      var options = this.allowed().filter(function (v) { return v !== current; });
      var rConfirm = h('input', { type: 'radio', name: 'action', value: 'confirm', checked: true });
      var rChange = h('input', { type: 'radio', name: 'action', value: 'change', disabled: !options.length });
      this.newVerdict = h('select', { class: 'select', id: 'ap-new', disabled: true },
        options.map(function (v) { return h('option', { value: v }, U.verdictLabel(v, o.type, top)); }));
      this.reason = h('textarea', { class: 'textarea', id: 'ap-reason', rows: '4', required: true, maxlength: '2000', placeholder: t('decision.reasonPlaceholder'), 'aria-describedby': 'ap-reason-err' });
      this.reasonErr = h('div', { class: 'field__error', id: 'ap-reason-err', 'aria-live': 'polite' });
      this.precedent = h('input', { type: 'checkbox' });
      this.preview = h('div', { class: 'decision-preview', 'aria-live': 'polite' });
      this.formErr = h('div', { 'aria-live': 'assertive' });
      this.submitBtn = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('check'), t('decision.submit'));
      var verdictField = h('div', { class: 'field', hidden: true },
        h('label', { class: 'field__label', for: 'ap-new' }, t('decision.newVerdict')), this.newVerdict,
        h('div', { class: 'field__hint' }, t('decision.allowedHint', { list: this.allowed().map(function (v) { return U.verdictLabel(v, o.type, top); }).join(', ') })));

      var sync = function () {
        var change = rChange.checked;
        verdictField.hidden = !change;
        self.newVerdict.disabled = !change;
        CQA.dom.replace(self.submitBtn, icon('check'), change ? t('decision.submitChange') : t('decision.submitConfirm'));
        self.updatePreview(change ? self.newVerdict.value : null);
      };
      rConfirm.addEventListener('change', sync);
      rChange.addEventListener('change', sync);
      this.newVerdict.addEventListener('change', sync);

      var form = h('form', { class: 'card stack stack--l', novalidate: true, 'aria-labelledby': 'ap-decision' },
        h('h2', { class: 'card__title', id: 'ap-decision' }, t('decision.title')),
        h('fieldset', { class: 'radio-cards' },
          h('legend', { class: 'sr-only' }, t('decision.title')),
          h('label', { class: 'radio-card' }, rConfirm,
            h('span', null, h('span', { class: 'radio-card__title' }, t('decision.confirm')), h('span', { class: 'radio-card__text' }, t('decision.confirmText', { v: U.verdictLabel(current, o.type, top) })))),
          h('label', { class: 'radio-card' + (options.length ? '' : ' is-disabled') }, rChange,
            h('span', null, h('span', { class: 'radio-card__title' }, t('decision.change')), h('span', { class: 'radio-card__text' }, t('decision.changeText'))))),
        verdictField,
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: 'ap-reason' }, t('decision.reason'), h('span', { class: 'req', 'aria-hidden': 'true' }, '*')),
          this.reason, this.reasonErr),
        h('label', { class: 'check check--small' }, this.precedent, t('decision.precedent')),
        this.preview,
        this.formErr,
        h('div', { class: 'row' }, this.submitBtn));
      form.addEventListener('submit', function (e) { e.preventDefault(); self.submit(rChange.checked); });
      CQA.dom.replace(this.decisionBox, form);
      sync();
    }

    /** Предпросмотр нового итога той же формулой §3 (окончательный пересчёт делает бэкенд). */
    updatePreview(newVerdict) {
      var box = this.preview;
      box.textContent = '';
      var ev = this.ev, cfg = this.config;
      if (!ev || !cfg) return;
      var cur = ev.final_total != null ? ev.final_total : ev.total;
      if (!newVerdict) {
        box.appendChild(h('div', { class: 'row' }, h('span', { class: 'muted' }, t('decision.totalStays')), CQA.score(cur, null, { max: true })));
        return;
      }
      var verdicts = {}, cats = {};
      ev.criteria.forEach(function (c) { verdicts[c.criterion_id] = c.final_verdict || c.verdict; cats[c.criterion_id] = c.category; });
      verdicts[this.appeal.criterion_id] = newVerdict;
      try {
        var res = CQA.scoring.score(cfg, verdicts, cats);
        box.appendChild(h('div', { class: 'row' }, h('span', { class: 'muted' }, t('decision.totalWillBe')), CQA.score(res.total, cur, { max: true })));
      } catch (e) { /* конфиг не совпал — без предпросмотра */ }
    }

    submit(change) {
      var self = this;
      this.reasonErr.textContent = '';
      this.reason.removeAttribute('aria-invalid');
      CQA.dom.clear(this.formErr);
      if (!this.reason.value.trim()) {
        this.reason.setAttribute('aria-invalid', 'true');
        CQA.dom.replace(this.reasonErr, icon('error'), t('decision.reasonRequired'));
        this.reason.focus();
        return;
      }
      var dto = { action: change ? 'change' : 'confirm', new_verdict: change ? this.newVerdict.value : null, reason: this.reason.value.trim(), make_precedent: this.precedent.checked };
      this.submitBtn.disabled = true;
      CQA.dom.replace(this.submitBtn, ui.spinner(), t('decision.saving'));
      CQA.api.decide(this.appeal.id, dto).then(function (a) {
        self.appeal = a;
        ui.toast(t('decision.saved'), { tone: 'success' });
        document.dispatchEvent(new CustomEvent('cqa-appeals-changed'));
        self.render();
      }, function (err) {
        self.submitBtn.disabled = false;
        CQA.dom.replace(self.submitBtn, icon('check'), t('decision.submit'));
        var ve = CQA.api.validationErrors(err);
        ve.forEach(function (x) { if (/reason/.test(x.loc)) { self.reason.setAttribute('aria-invalid', 'true'); CQA.dom.replace(self.reasonErr, icon('error'), x.msg); } });
        self.formErr.appendChild(ui.msgbar('danger', t('decision.failed'), err.message));
      });
    }

    // ------------------------------------------------------------ результат
    renderResult() {
      var a = this.appeal, o = a.original || {}, d = a.decision || {};
      var top = this.top();
      var prev = a.prev_final_total != null ? a.prev_final_total : d.prev_final_total;
      var next = a.new_final_total != null ? a.new_final_total : d.new_final_total;
      CQA.dom.replace(this.decisionBox, h('section', { class: 'card decision-result', 'aria-labelledby': 'ap-result' },
        h('div', { class: 'card__header' }, h('h2', { class: 'card__title row', id: 'ap-result' }, icon('checkCircle'), t('decision.resultTitle')),
          d.created_at ? h('span', { class: 'muted t-caption1' }, fmt.date(d.created_at)) : null),
        h('dl', { class: 'kv decision-kv' },
          h('dt', null, t('decision.original')),
          h('dd', null, h('span', { class: 'row' }, CQA.verdict(o.verdict, o.type, top),
            o.score && o.type !== 'flag' && o.verdict !== 'N/A' ? h('span', { class: 'muted num t-caption1' }, t('decision.points', { p: fmt.score(o.score.points), m: fmt.score(o.score.max_points) })) : null)),
          h('dt', null, t('decision.label')),
          h('dd', null, d.action === 'change'
            ? h('span', { class: 'row' }, t('decision.changeTo'), CQA.verdict(d.new_verdict, o.type, top))
            : t('decision.confirmed')),
          h('dt', null, t('decision.reason')), h('dd', null, d.reason || '—'),
          d.make_precedent ? [h('dt', null, t('decision.precedentShort')), h('dd', null, t('decision.precedentYes'))] : null,
          h('dt', null, t('decision.total')),
          h('dd', null, next != null ? CQA.score(next, prev, { max: true }) : '—')),
        h('div', { class: 'card__footer' },
          h('a', { class: 'btn btn--primary', href: '#/c/' + encodeURIComponent(a.uid) + '?crit=' + encodeURIComponent(a.criterion_id) }, icon('chat'), t('decision.openCard')),
          h('a', { class: 'btn', href: '#/appeals' }, t('decision.toList')))));
    }
  }

  customElements.define('cqa-page-appeal', CqaPageAppeal);
})(window.CQA = window.CQA || {});
