/* <cqa-appeal-form> — подача апелляции: критерий, комментарий, реплики-контрдоказательства (клик по транскрипту).
   .data = {evaluation, criterionId, tops, selected: Set<mid>}
   .syncSelection() — перерисовать выбранные реплики
   События: cqa-appeal-cancel, cqa-appeal-created {appeal}, cqa-appeal-pick (на узком экране — перейти к диалогу) */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  class CqaAppealForm extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d, ev = d.evaluation, self = this;
      this.textContent = '';
      this.classList.add('appeal-form', 'card');
      var options = ev.criteria.filter(function (c) { return !(c.appeal_id && c.appeal_status === 'open'); });
      var fid = CQA.dom.uid('appeal');
      this.select = h('select', { class: 'select', id: fid + '-crit', required: true },
        options.map(function (c) {
          return h('option', { value: c.criterion_id, selected: c.criterion_id === d.criterionId },
            c.criterion_id + ' · ' + c.title + ' — ' + CQA.util.verdictLabel(c.final_verdict || c.verdict, c.type, d.tops && d.tops[c.criterion_id]));
        }));
      this.current = h('div', { class: 'appeal-form__current' });
      this.comment = h('textarea', { class: 'textarea', id: fid + '-comment', rows: '4', required: true, maxlength: '2000',
        placeholder: t('appeal.commentPlaceholder'), 'aria-describedby': fid + '-comment-err' });
      this.commentErr = h('div', { class: 'field__error', id: fid + '-comment-err', 'aria-live': 'polite' });
      this.chips = h('div', { class: 'chip-list', 'aria-live': 'polite' });
      this.error = h('div', { 'aria-live': 'assertive' });
      this.submit = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('scale'), t('appeal.submit'));

      var form = h('form', { class: 'stack stack--l', novalidate: true },
        h('div', { class: 'row row--between' },
          h('h2', { class: 't-subtitle1' }, t('appeal.title')),
          h('button', { class: 'btn btn--subtle btn--icon', type: 'button', 'aria-label': t('action.close'), onClick: function () { self.cancel(); } }, icon('close'))),
        ui.msgbar('info', null, t('appeal.intro')),
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: fid + '-crit' }, t('appeal.criterion')),
          this.select),
        this.current,
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: fid + '-comment' }, t('appeal.comment'), h('span', { class: 'req', 'aria-hidden': 'true' }, '*')),
          this.comment, this.commentErr),
        h('div', { class: 'field' },
          h('span', { class: 'field__label' }, t('appeal.counter')),
          h('div', { class: 'field__hint' }, t('appeal.counterHint')),
          this.chips,
          h('button', { class: 'btn btn--small appeal-form__pick', type: 'button', onClick: function () { self.dispatchEvent(new CustomEvent('cqa-appeal-pick', { bubbles: true })); } },
            icon('cursor'), t('appeal.pick'))),
        this.error,
        h('div', { class: 'row' },
          this.submit,
          h('button', { class: 'btn', type: 'button', onClick: function () { self.cancel(); } }, t('action.cancel'))));
      form.addEventListener('submit', function (e) { e.preventDefault(); self.send(); });
      this.select.addEventListener('change', function () { self.renderCurrent(); });
      this.appendChild(form);
      this.renderCurrent();
      this.syncSelection();
      setTimeout(function () { self.comment.focus(); }, 0);
    }

    renderCurrent() {
      var d = this._d;
      var c = d.evaluation.criteria.filter(function (x) { return x.criterion_id === this.select.value; }, this)[0];
      CQA.dom.clear(this.current);
      if (!c) return;
      var v = c.final_verdict || c.verdict;
      this.current.appendChild(h('div', { class: 'quote quote--neutral t-caption1' },
        h('div', { class: 'row' }, h('span', { class: 'muted' }, t('appeal.currentVerdict')), CQA.verdict(v, c.type, d.tops && d.tops[c.criterion_id]),
          c.score && c.type !== 'flag' && v !== 'N/A' ? h('span', { class: 'num muted' }, fmt.score(c.score.points) + ' / ' + fmt.score(c.score.max_points)) : null),
        c.explanation ? h('div', { class: 'subtle', style: { marginTop: '4px' } }, c.explanation) : null));
      this.dispatchEvent(new CustomEvent('cqa-criterion-select', { bubbles: true, detail: { cid: c.criterion_id, keepForm: true } }));
    }

    syncSelection() {
      var self = this, sel = this._d.selected;
      CQA.dom.clear(this.chips);
      if (!sel.size) {
        this.chips.appendChild(h('span', { class: 'muted t-caption1' }, t('appeal.noneSelected')));
        return;
      }
      Array.from(sel).sort(function (a, b) { return parseInt(a.slice(1), 10) - parseInt(b.slice(1), 10); }).forEach(function (mid) {
        self.chips.appendChild(h('span', { class: 'tag' },
          h('span', { class: 'mono' }, mid),
          h('button', { type: 'button', 'aria-label': t('appeal.removeMid', { mid: mid }), onClick: function () {
            self.dispatchEvent(new CustomEvent('cqa-appeal-unselect', { bubbles: true, detail: { mid: mid } }));
          } }, icon('close'))));
      });
    }

    cancel() { this.dispatchEvent(new CustomEvent('cqa-appeal-cancel', { bubbles: true })); }

    send() {
      var self = this, d = this._d;
      this.commentErr.textContent = '';
      this.comment.removeAttribute('aria-invalid');
      CQA.dom.clear(this.error);
      if (!this.comment.value.trim()) {
        this.comment.setAttribute('aria-invalid', 'true');
        CQA.dom.replace(this.commentErr, icon('error'), t('val.required'));
        this.comment.focus();
        return;
      }
      this.submit.disabled = true;
      CQA.dom.replace(this.submit, ui.spinner(), t('appeal.sending'));
      CQA.api.createAppeal({
        evaluation_id: d.evaluation.id,
        criterion_id: this.select.value,
        comment: this.comment.value.trim(),
        counter_evidence: Array.from(d.selected)
      }).then(function (appeal) {
        self.dispatchEvent(new CustomEvent('cqa-appeal-created', { bubbles: true, detail: { appeal: appeal } }));
      }, function (err) {
        self.submit.disabled = false;
        CQA.dom.replace(self.submit, icon('scale'), t('appeal.submit'));
        var ve = CQA.api.validationErrors(err);
        var cm = ve.filter(function (x) { return /comment/.test(x.loc); })[0];
        if (cm) { self.comment.setAttribute('aria-invalid', 'true'); CQA.dom.replace(self.commentErr, icon('error'), cm.msg); }
        self.error.appendChild(ui.msgbar('danger', t('appeal.failed'), err.message));
      });
    }
  }

  customElements.define('cqa-appeal-form', CqaAppealForm);
})(window.CQA = window.CQA || {});
