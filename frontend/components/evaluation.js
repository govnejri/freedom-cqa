/* <cqa-evaluation> — правая колонка карточки: итог (финальный и исходный), секции, грубая ошибка,
   разложение весов, критерии по секциям.
   .data = {evaluation: EvaluationDTO, tops: {cid: top}, appeals: {id: AppealDTO}, selected: cid}
   .select(cid, scroll) */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, U = CQA.util;

  class CqaEvaluation extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d, ev = d.evaluation;
      this.textContent = '';
      this.classList.add('evaluation');
      this.items = {};

      // ---------------- сводка
      var hasFinal = ev.final_total != null || ev.total != null;
      var finalTotal = ev.final_total != null ? ev.final_total : ev.total;
      var differs = ev.final_total != null && ev.total != null && Math.abs(ev.final_total - ev.total) >= 0.005;
      var badges = [];
      if (ev.gross) badges.push(CQA.ui.badge(t('badge.gross') + (ev.gross_categories && ev.gross_categories.length ? ': ' + ev.gross_categories.join(', ') : ''), 'danger-filled', 'warning'));
      if (ev.needs_review) badges.push(CQA.ui.badge(t('badge.review'), 'warning', 'eye', { title: t('badge.reviewTitle') }));
      if (ev.status && ev.status !== 'done') badges.push(CQA.ui.badge(CQA.i18n.tx('evalStatus.' + ev.status, ev.status), ev.status === 'failed' ? 'danger' : 'neutral'));

      var summary = h('section', { class: 'eval-summary card', 'aria-label': t('eval.summary') },
        h('div', { class: 'eval-summary__top' },
          h('div', { class: 'eval-summary__score' },
            h('div', { class: 'muted t-caption1' }, differs ? t('eval.finalTotal') : t('eval.total')),
            hasFinal ? CQA.score(finalTotal, differs ? ev.total : null, { size: 'xl', max: true }) : h('div', { class: 'muted' }, t('eval.noTotal')),
            differs ? h('div', { class: 't-caption1 muted' }, t('eval.originalNote', { o: fmt.score(ev.total), f: fmt.score(ev.final_total) })) : null),
          h('div', { class: 'eval-summary__badges' }, badges)));

      // секции
      var secs = (ev.sections || []).filter(function (s) { return s.max_points > 0; });
      var orig = {};
      (ev.original_sections || []).forEach(function (s) { orig[s.section_id] = s; });
      if (secs.length) {
        var meters = h('div', { class: 'eval-sections' });
        secs.forEach(function (s) {
          var share = s.max_points ? s.points / s.max_points : 0;
          var tone = share >= 0.85 ? 'good' : share >= 0.6 ? 'mid' : 'bad';
          var o = orig[s.section_id];
          var oDiff = o && Math.abs(o.points - s.points) >= 0.005;
          meters.appendChild(h('div', { class: 'meter meter--' + tone },
            h('span', { class: 'meter__label', title: s.title }, s.title),
            h('span', { class: 'meter__value' },
              oDiff ? h('s', { class: 'muted t-caption1', title: t('score.originalTitle') }, fmt.score(o.points)) : null, oDiff ? ' ' : null,
              h('b', null, fmt.score(s.points)), h('span', { class: 'muted' }, ' / ' + fmt.score(s.max_points))),
            h('div', { class: 'progress', role: 'progressbar', 'aria-label': s.title, 'aria-valuemin': '0', 'aria-valuemax': String(s.max_points), 'aria-valuenow': String(s.points) },
              h('div', { class: 'progress__bar', style: { width: Math.round(share * 100) + '%' } }))));
        });
        summary.appendChild(meters);
      }

      // разложение: какие критерии N/A и как перераспределён вес
      var na = ev.criteria.filter(function (c) { return (c.final_verdict || c.verdict) === 'N/A'; });
      if (na.length) {
        var naW = na.reduce(function (a, c) { return a + (Number(c.weight) || 0); }, 0);
        var sumW = ev.criteria.reduce(function (a, c) { return a + (c.type === 'flag' ? 0 : Number(c.weight) || 0); }, 0);
        summary.appendChild(h('div', { class: 'eval-na t-caption1' }, icon('info', 'icon--16'),
          h('span', null, t('eval.naBreakdown', { list: na.map(function (c) { return c.criterion_id; }).join(', '), w: fmt.num(naW), applicable: fmt.num(sumW - naW) }))));
      }

      summary.appendChild(h('div', { class: 'eval-meta t-caption1 muted' },
        h('span', null, t('eval.checklistVersion', { v: ev.checklist_version })),
        h('span', null, ev.model),
        ev.n_llm_calls ? h('span', null, t('eval.calls', { n: ev.n_llm_calls })) : null,
        h('span', null, fmt.money(ev.cost_usd)),
        ev.latency_ms ? h('span', null, fmt.ms(ev.latency_ms)) : null,
        h('span', null, fmt.date(ev.created_at))));
      this.appendChild(summary);

      // ---------------- критерии по секциям
      var titles = {};
      (ev.sections || []).concat(ev.original_sections || []).forEach(function (s) { titles[s.section_id] = s.title; });
      var groups = [], byKey = {};
      ev.criteria.forEach(function (c) {
        var k = c.section || '';
        if (!byKey[k]) { byKey[k] = { key: k, title: titles[k] || k || t('eval.noSection'), items: [] }; groups.push(byKey[k]); }
        byKey[k].items.push(c);
      });
      var secPts = {};
      (ev.sections || []).forEach(function (s) { secPts[s.section_id] = s; });
      var self = this;
      groups.forEach(function (g) {
        var sp = secPts[g.key];
        var sec = h('section', { class: 'eval-group', 'aria-label': g.title },
          h('h3', { class: 'eval-group__title' },
            h('span', null, g.title),
            sp && sp.max_points > 0 ? h('span', { class: 'eval-group__pts num muted' }, fmt.score(sp.points) + ' / ' + fmt.score(sp.max_points)) : null));
        g.items.forEach(function (c) {
          var el = document.createElement('cqa-criterion');
          el.data = {
            cr: c, top: d.tops && d.tops[c.criterion_id], appeal: c.appeal_id && d.appeals ? d.appeals[c.appeal_id] : null,
            selected: d.selected === c.criterion_id, canAppeal: d.canAppeal
          };
          self.items[c.criterion_id] = el;
          sec.appendChild(el);
        });
        self.appendChild(sec);
      });
    }

    select(cid, scroll) {
      var self = this;
      Object.keys(this.items || {}).forEach(function (k) { self.items[k].selected = k === cid; });
      if (this._d) this._d.selected = cid;
      var el = this.items && this.items[cid];
      if (el && scroll) CQA.dom.scrollIntoPane(el, { block: 'start', margin: 8 });
      return el;
    }

    criterion(cid) {
      var list = (this._d && this._d.evaluation.criteria) || [];
      for (var i = 0; i < list.length; i++) if (list[i].criterion_id === cid) return list[i];
      return null;
    }
  }

  customElements.define('cqa-evaluation', CqaEvaluation);
})(window.CQA = window.CQA || {});
