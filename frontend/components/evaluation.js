/* <cqa-evaluation> — правая колонка карточки: компактный список критериев (по потерям или по порядку чек-листа).
   Первое нарушение раскрыто. «Как посчитан итог», второе мнение и журнал модели — в «Деталях оценки» (CQA.evalDetails).
   .data = {evaluation, tops, appeals, selected, canAppeal, trainUid, trainOperator, trainSelf, messages}
   .select(cid, scroll)
   Общие расчёты карточки — CQA.evalUtil (потери, порядок, короткие названия). */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var LS_SORT = 'cqa.critSort';

  // ------------------------------------------------------------------ расчёты
  function vOf(c) { return c.final_verdict != null ? c.final_verdict : c.verdict; }
  function topOf(c, tops) { return (tops && tops[c.criterion_id]) || c.scale_top; }
  /** Потеря баллов по критерию: максимум после перераспределения минус набранное. У флагов и N/A — 0. */
  function lossOf(c) {
    if (c.type === 'flag' || vOf(c) === 'N/A' || !c.score || c.score.max_points == null) return 0;
    return Math.max(0, (Number(c.score.max_points) || 0) - (Number(c.score.points) || 0));
  }
  function isGross(c) { return c.type === 'flag' && vOf(c) === 'Да'; }
  /** «Корректная и полная консультация» → «Консультация», «Эмпатия и клиентоориентированность» → «Эмпатия». */
  function shortTitle(title) {
    var s = String(title || '').trim();
    var first = s.split(/,|\s+и\s+/)[0].trim();
    if (first !== s && /^[А-ЯЁA-Z][а-яёa-z]+(ая|ый|ое|ий|ой|ые)$/.test(first)) {
      var words = s.split(/\s+/);
      var last = words[words.length - 1];
      return last.charAt(0).toUpperCase() + last.slice(1);
    }
    return first;
  }
  /** Порядок «по потерям»: грубые → потери по убыванию → ошибки → выполненные → флаги без нарушений → N/A. */
  function byLoss(list) {
    function rank(c) {
      if (isGross(c)) return 0;
      if (lossOf(c) > 0.004) return 1;
      if (c.status === 'failed' || vOf(c) == null) return 2;
      if (vOf(c) === 'N/A') return 5;
      if (c.type === 'flag') return 4;
      return 3;
    }
    return list.slice().sort(function (a, b) {
      return rank(a) - rank(b) || (lossOf(b) - lossOf(a)) || String(a.criterion_id).localeCompare(String(b.criterion_id), 'ru', { numeric: true });
    });
  }
  /** Где потеряны баллы: грубые нарушения и критерии с потерей, по убыванию. */
  function losses(ev) {
    return byLoss(ev.criteria.filter(function (c) { return isGross(c) || lossOf(c) > 0.004; }));
  }
  /** Первое нарушение — его раскрываем по умолчанию. */
  function firstViolation(ev, tops) {
    var l = losses(ev);
    if (l.length) return l[0].criterion_id;
    var v = ev.criteria.filter(function (c) { return CQA.isViolation(vOf(c), c.type, topOf(c, tops)); })[0];
    return v ? v.criterion_id : null;
  }
  CQA.evalUtil = { vOf: vOf, lossOf: lossOf, isGross: isGross, shortTitle: shortTitle, byLoss: byLoss, losses: losses, firstViolation: firstViolation, topOf: topOf };

  // ------------------------------------------------------------------ список критериев
  class CqaEvaluation extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    get sortMode() { var m = CQA.store.get(LS_SORT, 'loss'); return m === 'order' ? 'order' : 'loss'; }

    render() {
      var d = this._d, ev = d.evaluation, self = this;
      this.textContent = '';
      this.classList.add('evaluation');
      this.items = {};
      var mode = this.sortMode;
      var crits = mode === 'loss' ? byLoss(ev.criteria) : ev.criteria.slice();

      var seg = h('div', { class: 'segmented segmented--small', role: 'group', 'aria-label': t('eval.sortAria') },
        h('button', { type: 'button', 'aria-pressed': String(mode === 'loss'), onClick: function () { CQA.store.set(LS_SORT, 'loss'); self.render(); } }, t('eval.sortLoss')),
        h('button', { type: 'button', 'aria-pressed': String(mode === 'order'), onClick: function () { CQA.store.set(LS_SORT, 'order'); self.render(); } }, t('eval.sortOrder')));
      var lost = ev.criteria.reduce(function (a, c) { return a + lossOf(c); }, 0);
      this.appendChild(h('div', { class: 'crit-panel__head' },
        h('h2', { class: 'panel__title' }, t('eval.criteria')),
        h('span', { class: 'panel__meta' }, [ev.gross ? t('eval.grossMeta') : null, lost > 0.004 ? t('eval.lostTotal', { p: fmt.score(lost) }) : ev.gross ? null : t('eval.lostNone')].filter(Boolean).join(' · ')),
        h('span', { class: 'spacer' }),
        seg));

      var list = h('div', { class: 'crit-list', role: 'list' });
      crits.forEach(function (c) {
        var el = document.createElement('cqa-criterion');
        el.setAttribute('role', 'listitem');
        el.data = {
          cr: c, top: topOf(c, d.tops), appeal: c.appeal_id && d.appeals ? d.appeals[c.appeal_id] : null,
          selected: d.selected === c.criterion_id, canAppeal: d.canAppeal, canEdit: d.canEdit, trainUid: d.trainUid,
          trainOperator: d.trainOperator, trainSelf: d.trainSelf, messages: d.messages || {}
        };
        self.items[c.criterion_id] = el;
        list.appendChild(el);
      });
      this.appendChild(list);

      // N/A и перераспределение — одной строкой, подробности — в «Деталях оценки»
      var na = ev.criteria.filter(function (c) { return c.type !== 'flag' && vOf(c) === 'N/A'; });
      var naW = na.reduce(function (a, c) { return a + (Number(c.weight) || 0); }, 0);
      this.appendChild(h('div', { class: 'crit-panel__foot' },
        na.length ? h('span', null,
          t('eval.naLine1', { list: na.map(function (c) { return c.criterion_id; }).join(', ') }), ' ',
          ui.term(t('eval.naTerm'), 'term.na'), ', ',
          t('eval.naLine2', { w: fmt.num(naW) }), ' ',
          ui.term(t('eval.redistTerm'), 'term.redistribution'), '.') : h('span', null, t('eval.noNaShort')),
        h('button', { class: 'btn btn--link', type: 'button', onClick: function () { CQA.evalDetails(ev, d.tops, 'breakdown'); } }, t('eval.bdTitle'))));
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

  // ------------------------------------------------------------------ «Детали оценки» (выезжающая панель)
  /** Служебное по оценке: как посчитан итог, второе мнение, журнал модели. focus — 'breakdown' | 'second' | 'log'. */
  CQA.evalDetails = function (ev, tops, focus) {
    tops = tops || {};
    var secs = {};
    var body = h('div', { class: 'stack stack--xl' },
      secs.breakdown = section('chart', t('eval.bdTitle'), breakdown(ev, tops)),
      secs.second = section('people', t('eval.secondTitle'), secondOpinion(ev, tops)),
      secs.log = section('code', t('eval.logTitle2'), logInfo(ev)));
    var dr = ui.drawer({ title: t('eval.detailsTitle'), sub: t('eval.detailsSub'), body: body, wide: true });
    if (focus && secs[focus]) setTimeout(function () { secs[focus].scrollIntoView({ block: 'start' }); }, 50);
    return dr;
  };

  function section(ic, title, content) {
    return h('section', { class: 'drawer__section' }, h('h3', null, icon(ic), title), content);
  }

  function breakdown(ev, tops) {
    var crits = ev.criteria.filter(function (c) { return c.type !== 'flag'; });
    var flags = ev.criteria.filter(function (c) { return c.type === 'flag'; });
    var na = crits.filter(function (c) { return vOf(c) === 'N/A'; });
    var sumW = crits.reduce(function (a, c) { return a + (Number(c.weight) || 0); }, 0);
    var naW = na.reduce(function (a, c) { return a + (Number(c.weight) || 0); }, 0);
    var appW = sumW - naW;
    var tbody = h('tbody');
    crits.forEach(function (c) {
      var v = vOf(c), sc = c.score || {};
      var isNA = v === 'N/A';
      var failed = c.status === 'failed' || v == null;
      var eff = sc.effective_weight;
      tbody.appendChild(h('tr', { class: isNA ? 'is-na' : null },
        h('td', { class: 'bd-num' }, c.criterion_id),
        h('td', { class: 'bd-title' }, c.title),
        h('td', null, failed ? h('span', { class: 'muted' }, '—') : CQA.verdict(v, c.type, topOf(c, tops))),
        h('td', { class: 'num nowrap' }, fmt.num(c.weight),
          isNA ? h('span', { class: 'muted' }, ' → 0')
            : eff != null && Math.abs(eff - c.weight) >= 0.005 ? [h('span', { class: 'muted' }, ' → '), h('b', null, fmt.num(eff))] : null),
        h('td', { class: 'num nowrap' }, isNA ? h('span', { class: 'muted' }, t('eval.bdExcluded'))
          : sc.points != null ? [h('b', null, fmt.score(sc.points)), h('span', { class: 'muted' }, ' / ' + fmt.score(sc.max_points))] : '—')));
    });
    flags.forEach(function (c) {
      var v = vOf(c);
      tbody.appendChild(h('tr', { class: 'is-flag' },
        h('td', { class: 'bd-num' }, c.criterion_id),
        h('td', { class: 'bd-title' }, c.title),
        h('td', null, CQA.verdict(v, c.type)),
        h('td', { class: 'num muted', colspan: '2' }, v === 'Да' ? t('eval.bdFlagYes') : t('eval.bdFlag'))));
    });
    var total = ev.final_total != null ? ev.final_total : ev.total;
    // по секциям чек-листа
    var secs = (ev.sections || []).filter(function (s) { return s.max_points > 0; });
    var orig = {};
    (ev.original_sections || []).forEach(function (s) { orig[s.section_id] = s; });
    var meters = secs.length ? h('div', { class: 'eval-sections' }, secs.map(function (s) {
      var share = s.max_points ? s.points / s.max_points : 0;
      var o = orig[s.section_id];
      var oDiff = o && Math.abs(o.points - s.points) >= 0.005;
      return h('div', { class: 'meter meter--' + (share >= 0.85 ? 'good' : share >= 0.6 ? 'mid' : 'bad') },
        h('span', { class: 'meter__label', 'data-tip': s.title }, s.title),
        h('span', { class: 'meter__value' },
          oDiff ? [h('s', { class: 'muted t-caption1' }, fmt.score(o.points)), ' '] : null,
          h('b', null, fmt.score(s.points)), h('span', { class: 'muted' }, ' / ' + fmt.score(s.max_points))),
        h('div', { class: 'progress', role: 'progressbar', 'aria-label': s.title, 'aria-valuemin': '0', 'aria-valuemax': String(s.max_points), 'aria-valuenow': String(s.points) },
          h('div', { class: 'progress__bar', style: { width: Math.round(share * 100) + '%' } })));
    })) : null;
    return h('div', { class: 'stack stack--l' },
      meters,
      h('p', { class: 'muted' }, na.length
        ? [t('eval.bdIntroNa', { list: na.map(function (c) { return c.criterion_id; }).join(', '), w: fmt.num(naW) }), ' ', ui.term(t('eval.redistTerm'), 'term.redistribution'), ' ', t('eval.bdIntroNa2', { applicable: fmt.num(appW) })]
        : t('eval.noNa')),
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
      h('p', { class: 't-caption1 muted' }, t('eval.bdFormula', { w: fmt.num(appW) })),
      ev.gross ? ui.msgbar('danger', t('eval.grossRuleTitle'), t('eval.grossRule')) : null);
  }

  function secondOpinion(ev, tops) {
    var checked = ev.criteria.filter(function (c) { return c.second_opinion && c.second_opinion.verdict; });
    if (!checked.length) return h('p', { class: 'muted' }, t('eval.secondNone'));
    var model = checked[0].second_opinion.model || checked[0].second_opinion.profile || '';
    var bad = checked.filter(function (c) { return c.second_opinion.agree === false; });
    var intro = h('p', null, bad.length ? t('eval.disagreeTitle', { n: bad.length, total: checked.length, m: model }) : t('eval.checkedAll', { n: checked.length, m: model }),
      ' ', ui.hint(t('term.secondOpinion')));
    if (!bad.length) return intro;
    return h('div', { class: 'stack' }, intro,
      h('ul', { class: 'stack' }, bad.map(function (c) {
        var s = c.second_opinion;
        return h('li', { class: 'second-row' },
          h('span', { class: 'second-row__name' }, h('b', null, c.criterion_id), ' ', c.title),
          h('span', { class: 'row row--nowrap' },
            h('span', { class: 'muted' }, t('eval.mainModel')), CQA.verdict(c.verdict, c.type, topOf(c, tops)),
            h('span', { class: 'muted' }, t('eval.secondModel')), CQA.verdict(s.verdict, c.type, topOf(c, tops))),
          s.explanation ? h('span', { class: 'muted t-caption1' }, s.explanation) : null);
      })));
  }

  function logInfo(ev) {
    var parts = [ev.model];
    if (ev.n_llm_calls) parts.push(t('eval.calls', { n: ev.n_llm_calls }));
    return h('div', { class: 'stack' },
      h('dl', { class: 'kv' },
        h('dt', null, t('eval.logModel')), h('dd', null, parts.join(' · ')),
        h('dt', null, t('eval.logCost')), h('dd', null, fmt.money(ev.cost_usd) + (ev.latency_ms ? ' · ' + fmt.ms(ev.latency_ms) : '')),
        h('dt', null, t('eval.logChecklist')), h('dd', null, t('eval.checklistVersion', { v: ev.checklist_version })),
        h('dt', null, t('eval.logDate')), h('dd', null, fmt.date(ev.created_at))),
      h('p', { class: 'muted t-caption1' }, t('eval.logHint')),
      logLink(ev));
  }

  function logLink(ev) {
    var box = h('div', null, h('a', { class: 'btn btn--small', href: '#/log/' + ev.id }, icon('code'), t('eval.logOpen')));
    if (CQA.api.mode === 'mock') {
      CQA.api.evaluationCalls(ev.id).then(function (list) {
        if (!list || !list.length) CQA.dom.replace(box, h('p', { class: 'muted t-caption1' }, t('eval.logNotInDemo')));
      }, function () { CQA.dom.replace(box, h('p', { class: 'muted t-caption1' }, t('eval.logNotInDemo'))); });
    }
    return box;
  }

  customElements.define('cqa-evaluation', CqaEvaluation);
})(window.CQA = window.CQA || {});
