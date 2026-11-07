/* <cqa-criterion> — результат по одному критерию: вердикт, баллы и вес (после перераспределения), объяснение,
   доказательства-чипы, проверенный участок, «как надо было», «требует проверки», апелляция.
   .data = {cr: CriterionResultDTO, top, appeal: AppealDTO|null, selected, canAppeal}
   События (bubbles): cqa-criterion-select {cid}, cqa-evidence {cid, mid}, cqa-range {cid, range}, cqa-appeal {cid} */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, U = CQA.util;

  function fire(el, name, detail) { el.dispatchEvent(new CustomEvent(name, { bubbles: true, detail: detail })); }

  class CqaCriterion extends HTMLElement {
    set data(d) { this._d = d; this.render(); }
    get data() { return this._d; }
    get cid() { return this._d && this._d.cr.criterion_id; }

    set selected(on) {
      this.classList.toggle('is-selected', !!on);
      if (this.headBtn) this.headBtn.setAttribute('aria-pressed', String(!!on));
    }

    render() {
      var d = this._d, cr = d.cr, self = this;
      var score = cr.score || null;
      var verdict = cr.final_verdict != null ? cr.final_verdict : cr.verdict;
      var changed = cr.final_verdict != null && cr.verdict != null && cr.final_verdict !== cr.verdict;
      var isFlag = cr.type === 'flag';
      var isNA = verdict === 'N/A';
      var tone = U.verdictTone(verdict, cr.type, d.top);
      this.className = 'crit crit--' + tone + (cr.status === 'failed' ? ' crit--failed' : '');
      this.id = 'crit-' + cr.criterion_id;
      this.textContent = '';

      // ---- заголовок (кнопка выбора критерия)
      var pts;
      if (isFlag) pts = null;
      else if (isNA) pts = h('span', { class: 'crit__pts muted' }, t('crit.naPoints'));
      else if (score) pts = h('span', { class: 'crit__pts num' }, h('b', null, fmt.score(score.points)), ' / ', fmt.score(score.max_points));
      var verdictEl = h('span', { class: 'crit__verdict' },
        changed ? [CQA.verdict(cr.verdict, cr.type, d.top, { strike: true }), icon('arrowRight', 'icon--16 muted')] : null,
        CQA.verdict(verdict, cr.type, d.top, { size: 'l' }));
      this.headBtn = h('button', {
        class: 'crit__head', type: 'button', 'aria-pressed': String(!!d.selected),
        'aria-label': t('crit.selectAria', { id: cr.criterion_id, title: cr.title, v: U.verdictLabel(verdict, cr.type, d.top) }),
        onClick: function () { fire(self, 'cqa-criterion-select', { cid: cr.criterion_id }); }
      },
        h('span', { class: 'crit__id' }, cr.criterion_id),
        h('span', { class: 'crit__title' }, cr.title),
        verdictEl, pts);
      this.appendChild(this.headBtn);

      var body = h('div', { class: 'crit__body' });
      this.appendChild(body);

      // ---- вес
      if (!isFlag) {
        var eff = score ? score.effective_weight : null;
        var wl = h('div', { class: 'crit__weight t-caption1' }, t('crit.weight', { w: fmt.num(cr.weight) }));
        if (isNA) wl.appendChild(h('span', null, ' · ', t('crit.weightRedistributed')));
        else if (eff != null && Math.abs(eff - cr.weight) >= 0.005) {
          wl.appendChild(h('span', { title: t('crit.effWeightTitle') }, ' → ', h('b', null, fmt.num(eff)), ' ', t('crit.afterRedistribution')));
        }
        body.appendChild(wl);
      }

      // ---- бейджи
      var badges = [];
      if (isFlag && verdict === 'Да') badges.push(CQA.ui.badge(cr.category ? t('crit.grossCategory', { c: cr.category }) : t('badge.gross'), 'danger-filled', 'warning'));
      if (cr.needs_review) badges.push(CQA.ui.badge(t('badge.review'), 'warning', 'eye', { title: t('badge.reviewTitle') }));
      if (cr.status === 'failed') badges.push(CQA.ui.badge(t('crit.failed'), 'danger', 'error'));
      if (changed) badges.push(CQA.ui.badge(t('crit.changedByAppeal'), 'brand', 'scale'));
      if (badges.length) body.appendChild(h('div', { class: 'row crit__badges' }, badges));

      if (cr.status === 'failed' && cr.error) body.appendChild(h('div', { class: 'crit__error t-caption1' }, cr.error));

      // ---- объяснение
      if (cr.explanation) body.appendChild(h('p', { class: 'crit__expl' }, cr.explanation));

      // ---- доказательства
      if (cr.evidence && cr.evidence.length) {
        var chips = h('div', { class: 'evchips' });
        cr.evidence.forEach(function (ev) {
          var bad = ev.ok === false;
          var chip = h('button', {
            class: 'evchip' + (bad ? ' evchip--bad' : ''), type: 'button',
            title: bad ? t('crit.evidenceBad') : t('crit.goToMessage', { mid: ev.id }),
            'aria-label': t('crit.goToMessage', { mid: ev.id }) + ': ' + ev.quote,
            onClick: function () { fire(self, 'cqa-evidence', { cid: cr.criterion_id, mid: ev.id }); }
          },
            h('span', { class: 'evchip__id' }, ev.id),
            h('span', { class: 'evchip__q' }, '«' + ev.quote + '»'),
            bad ? icon('warning') : ev.repaired ? h('span', { title: t('crit.evidenceRepaired') }, icon('info')) : null);
          chips.appendChild(chip);
        });
        body.appendChild(h('div', { class: 'crit__section' }, h('div', { class: 'crit__label' }, t('crit.evidence')), chips));
      }

      // ---- проверенный участок
      if (cr.checked_range && cr.checked_range.length === 2 && cr.checked_range[0]) {
        var r = cr.checked_range;
        var label = r[0] === r[1] ? r[0] : r[0] + '–' + r[1];
        body.appendChild(h('div', { class: 'crit__section crit__range' },
          h('span', { class: 'crit__label' }, t('crit.checkedRange')),
          h('button', { class: 'evchip evchip--plain', type: 'button', title: t('crit.showRange'),
            onClick: function () { fire(self, 'cqa-range', { cid: cr.criterion_id, range: r }); } },
          h('span', { class: 'evchip__id' }, label)),
          h('span', { class: 'muted t-caption1' }, t('crit.checkedRangeHint'))));
      }

      // ---- как надо было
      if (cr.how_to_fix) {
        body.appendChild(h('details', { class: 'disclosure crit__fix' },
          h('summary', null, icon('chevronRight'), t('crit.howToFix')),
          h('div', { class: 'disclosure__body' }, h('div', { class: 'quote' }, cr.how_to_fix))));
      }

      // ---- второе мнение
      if (cr.second_opinion && cr.second_opinion.verdict) {
        var so = cr.second_opinion;
        body.appendChild(h('div', { class: 'crit__second' },
          h('div', { class: 'row' }, icon('sparkle', 'icon--16'), h('span', { class: 't-caption1-strong' }, t('crit.secondOpinion')),
            CQA.verdict(so.verdict, cr.type, d.top), so.model ? h('span', { class: 'muted t-caption1' }, so.model) : null),
          so.explanation ? h('div', { class: 't-caption1 subtle' }, so.explanation) : null));
      }

      // ---- рассуждение модели
      if (cr.reasoning) {
        body.appendChild(h('details', { class: 'disclosure crit__reasoning' },
          h('summary', null, icon('chevronRight'), t('crit.reasoning')),
          h('div', { class: 'disclosure__body t-caption1 subtle pre-wrap' }, cr.reasoning)));
      }

      // ---- апелляция
      var foot = h('div', { class: 'crit__foot' });
      var ap = d.appeal;
      if (cr.appeal_id) {
        var st = cr.appeal_status || (ap && ap.status);
        var box = h('div', { class: 'crit__appeal crit__appeal--' + (st || 'open') },
          h('div', { class: 'row' },
            icon('scale', 'icon--16'),
            h('a', { href: '#/appeals/' + cr.appeal_id }, t('crit.appealN', { id: cr.appeal_id })),
            CQA.ui.badge(CQA.i18n.tx('appealStatus.' + (st || 'open'), st || 'open'), st === 'decided' ? 'success' : 'warning')));
        if (ap && ap.decision) {
          var dec = ap.decision;
          box.appendChild(h('div', { class: 'crit__decision t-caption1' },
            h('div', null, h('span', { class: 'muted' }, t('decision.original') + ': '), CQA.verdict(ap.original ? ap.original.verdict : cr.verdict, cr.type, d.top)),
            h('div', null, h('span', { class: 'muted' }, t('decision.label') + ': '),
              dec.action === 'change' ? [t('decision.changeTo') + ' ', CQA.verdict(dec.new_verdict, cr.type, d.top)] : t('decision.confirmed')),
            dec.reason ? h('div', null, h('span', { class: 'muted' }, t('decision.reason') + ': '), dec.reason) : null));
        }
        foot.appendChild(box);
      } else if (d.canAppeal !== false && cr.status !== 'failed') {
        foot.appendChild(h('button', { class: 'btn btn--small crit__dispute', type: 'button',
          onClick: function () { fire(self, 'cqa-appeal', { cid: cr.criterion_id }); } }, icon('scale'), t('crit.dispute')));
      }
      if (foot.childNodes.length) body.appendChild(foot);
      this.selected = d.selected;
    }
  }

  customElements.define('cqa-criterion', CqaCriterion);
})(window.CQA = window.CQA || {});
