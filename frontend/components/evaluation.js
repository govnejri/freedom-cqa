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

      // второе мнение: заметно только расхождения, согласие — одной строкой
      var so = this.secondOpinion(ev);
      if (so) summary.appendChild(so);

      // разложение итога: N/A, перераспределение весов, сумма баллов
      summary.appendChild(this.breakdown(ev, d.tops || {}));

      var metaParts = [ev.model];
      if (ev.n_llm_calls) metaParts.push(t('eval.calls', { n: ev.n_llm_calls }));
      metaParts.push(fmt.money(ev.cost_usd));
      if (ev.latency_ms) metaParts.push(fmt.ms(ev.latency_ms));
      summary.appendChild(h('div', { class: 'eval-meta t-caption1 muted' },
        h('a', { class: 'eval-log-link', href: '#/log/' + ev.id, title: t('eval.logTitle') },
          icon('code', 'icon--16'), h('span', { class: 'eval-log-link__label' }, t('eval.log')), h('span', null, metaParts.join(' · '))),
        h('span', null, t('eval.checklistVersion', { v: ev.checklist_version })),
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

    /** Сводка перекрёстной проверки: блок при расхождении, отметка при полном согласии. */
    secondOpinion(ev) {
      var self = this;
      var checked = ev.criteria.filter(function (c) { return c.second_opinion && c.second_opinion.verdict; });
      if (!checked.length) return null;
      var model = checked[0].second_opinion.model || checked[0].second_opinion.profile || '';
      var bad = checked.filter(function (c) { return c.second_opinion.agree === false; });
      if (!bad.length) {
        return h('div', { class: 'eval-checked t-caption1', title: t('eval.checkedTitle') }, icon('checkCircle', 'icon--16'),
          h('span', null, t('eval.checkedAll', { n: checked.length, m: model })));
      }
      var tops = (this._d && this._d.tops) || {};
      return h('div', { class: 'eval-disagree', role: 'note' },
        h('div', { class: 'eval-disagree__title' }, icon('warning', 'icon--16'),
          h('span', null, t('eval.disagreeTitle', { n: bad.length, total: checked.length, m: model }))),
        h('ul', { class: 'eval-disagree__list' }, bad.map(function (c) {
          var s = c.second_opinion;
          return h('li', null, h('button', { class: 'eval-disagree__item', type: 'button',
            onClick: function () { self.dispatchEvent(new CustomEvent('cqa-criterion-select', { bubbles: true, detail: { cid: c.criterion_id, scroll: true } })); } },
            h('span', { class: 'mono' }, c.criterion_id), h('span', { class: 'eval-disagree__name' }, c.title),
            h('span', { class: 'row row--nowrap eval-disagree__v' },
              h('span', { class: 'muted' }, t('eval.mainModel')), CQA.verdict(c.verdict, c.type, tops[c.criterion_id] || c.scale_top),
              h('span', { class: 'muted' }, t('eval.secondModel')), CQA.verdict(s.verdict, c.type, tops[c.criterion_id] || c.scale_top),
              s.near ? h('span', { class: 'muted t-caption1' }, t('eval.near')) : null),
            c.final_verdict != null && c.final_verdict !== c.verdict ? CQA.ui.badge(t('eval.resolvedByAppeal', { v: CQA.util.verdictLabel(c.final_verdict, c.type, tops[c.criterion_id] || c.scale_top) }), 'brand', 'scale') : null));
        })));
    }

    /** «Как посчитан итог»: какие критерии N/A, вес → эффективный вес, баллы, сумма. */
    breakdown(ev, tops) {
      var crits = ev.criteria.filter(function (c) { return c.type !== 'flag'; });
      var flags = ev.criteria.filter(function (c) { return c.type === 'flag'; });
      var vOf = function (c) { return c.final_verdict != null ? c.final_verdict : c.verdict; };
      var na = crits.filter(function (c) { return vOf(c) === 'N/A'; });
      var sumW = crits.reduce(function (a, c) { return a + (Number(c.weight) || 0); }, 0);
      var naW = na.reduce(function (a, c) { return a + (Number(c.weight) || 0); }, 0);
      var appW = sumW - naW;
      var head = na.length
        ? t('eval.naBreakdown', { list: na.map(function (c) { return c.criterion_id; }).join(', '), w: fmt.num(naW), applicable: fmt.num(appW) })
        : t('eval.noNa');
      var tbody = h('tbody');
      var sumPts = 0;
      crits.forEach(function (c) {
        var v = vOf(c), sc = c.score || {};
        var isNA = v === 'N/A';
        var failed = c.status === 'failed' || v == null;
        if (!isNA && sc.points != null) sumPts += sc.points;
        var eff = sc.effective_weight;
        tbody.appendChild(h('tr', { class: isNA ? 'is-na' : null },
          h('td', { class: 'mono bd-num' }, c.criterion_id),
          h('td', { class: 'bd-title' }, c.title),
          h('td', null, failed ? h('span', { class: 'muted' }, '—') : CQA.verdict(v, c.type, tops[c.criterion_id] || c.scale_top)),
          h('td', { class: 'num nowrap' }, fmt.num(c.weight),
            isNA ? h('span', { class: 'muted' }, ' → 0')
              : eff != null && Math.abs(eff - c.weight) >= 0.005 ? [h('span', { class: 'muted' }, ' → '), h('b', null, fmt.num(eff))] : null),
          h('td', { class: 'num nowrap' }, isNA ? h('span', { class: 'muted' }, t('eval.bdExcluded'))
            : sc.points != null ? [h('b', null, fmt.score(sc.points)), h('span', { class: 'muted' }, ' / ' + fmt.score(sc.max_points))] : '—')));
      });
      flags.forEach(function (c) {
        var v = vOf(c);
        tbody.appendChild(h('tr', { class: 'is-flag' },
          h('td', { class: 'mono bd-num' }, c.criterion_id),
          h('td', { class: 'bd-title' }, c.title),
          h('td', null, CQA.verdict(v, c.type)),
          h('td', { class: 'num muted', colspan: '2' }, v === 'Да' ? t('eval.bdFlagYes') : t('eval.bdFlag'))));
      });
      var total = ev.final_total != null ? ev.final_total : ev.total;
      return h('details', { class: 'disclosure eval-breakdown' },
        h('summary', null, icon('chevronRight'), h('span', null, t('eval.bdTitle')), h('span', { class: 'eval-breakdown__head muted' }, head)),
        h('div', { class: 'disclosure__body stack' },
          h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact bd-table' },
            h('thead', null, h('tr', null,
              h('th', { scope: 'col', class: 'bd-num' }, '№'), h('th', { scope: 'col' }, t('eval.bdCriterion')), h('th', { scope: 'col' }, t('eval.bdVerdict')),
              h('th', { scope: 'col', class: 'num' }, t('eval.bdWeight')), h('th', { scope: 'col', class: 'num' }, t('eval.bdPoints')))),
            tbody,
            h('tfoot', null, h('tr', null,
              h('td', { class: 'bd-num' }),
              h('td', { colspan: '2' }, t('eval.bdSum', { w: fmt.num(appW) })),
              h('td', { class: 'num' }, appW ? '100' : '—'),
              h('td', { class: 'num' }, h('b', null, total == null ? '—' : fmt.score(total)), h('span', { class: 'muted' }, ' / 100')))))),
          h('div', { class: 't-caption1 muted' }, t('eval.bdFormula', { w: fmt.num(appW) }))));
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
