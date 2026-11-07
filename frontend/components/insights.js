/* Инсайты руководителя (P1): радар рисков, вопросы без ответа по темам, причины обращения.
   <cqa-risk-radar>  .data = {risks: RisksDTO, limit: 3|null, category: null|'обход компании', detailed: bool}
   <cqa-unanswered>  .data = UnansweredDTO
   <cqa-reasons>     .data = ReasonsDTO
   Событие: cqa-compliance-changed {case} (bubbles) — дело передано в комплаенс. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  function convHref(uid, q) { return CQA.router.href('/c/' + encodeURIComponent(uid), q); }
  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  // ------------------------------------------------------------------ радар рисков
  var CAT_ICON = { 'оскорбление': 'warning', 'обход компании': 'flag', 'раскрытие данных': 'eye' };

  class CqaRiskRadar extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d, self = this, r = d.risks;
      this.textContent = '';
      this.classList.add('radar');
      if (!r || !r.total) {
        this.appendChild(h('div', { class: 'radar__empty' }, icon('checkCircle'), h('span', null, t('risk.none'))));
        return;
      }
      var byId = {};
      r.items.forEach(function (it) { byId[it.criterion_result_id] = it; });
      var cats = r.by_category.filter(function (c) { return !d.category || c.category === d.category; });
      var grid = h('div', { class: 'radar__grid' + (cats.length === 1 ? ' radar__grid--single' : '') });
      cats.forEach(function (c) {
        var items = c.criterion_result_ids.map(function (id) { return byId[id]; }).filter(Boolean);
        var sent = items.filter(function (it) { return it.compliance; }).length;
        var shown = d.limit ? items.slice(0, d.limit) : items;
        var list = h('ul', { class: 'radar__list' });
        shown.forEach(function (it) { list.appendChild(h('li', null, self.item(it))); });
        grid.appendChild(h('section', { class: 'radar__cat', 'aria-label': cap(c.category) },
          h('div', { class: 'radar__cat-head' },
            h('span', { class: 'radar__cat-icon' }, icon(CAT_ICON[c.category] || 'warning')),
            h('h3', { class: 'radar__cat-title' }, cap(c.category)),
            h('span', { class: 'radar__count num' }, String(c.count)),
            h('span', { class: 'spacer' }),
            sent ? h('span', { class: 'muted t-caption1' }, t('risk.sentN', { n: sent, total: c.count })) : null),
          list,
          items.length > shown.length ? h('a', { class: 'radar__more', href: CQA.router.href('/risks', { category: c.category }) },
            t('risk.more', { n: items.length - shown.length }), icon('chevronRight', 'icon--16')) : null));
      });
      this.appendChild(grid);
    }

    item(it) {
      var self = this;
      var ev = it.evidence[0];
      var quotes = h('div', { class: 'risk__quotes' });
      (this._d.detailed ? it.evidence : it.evidence.slice(0, 1)).forEach(function (e) {
        quotes.appendChild(h('a', { class: 'risk__quote', href: convHref(it.uid, { crit: it.criterion_id, m: e.mid }),
          title: t('risk.openQuote', { mid: e.mid }) },
          h('span', { class: 'risk__mid' }, e.mid + (e.speaker ? ' · ' + CQA.i18n.tx('role.' + e.speaker, e.speaker) : '')),
          h('span', { class: 'risk__text' }, '«' + (e.quote || e.text || '') + '»')));
      });
      if (!this._d.detailed && it.evidence.length > 1) quotes.appendChild(h('span', { class: 'muted t-caption1' }, t('risk.moreQuotes', { n: it.evidence.length - 1 })));
      if (!ev) quotes.appendChild(h('span', { class: 'muted t-caption1' }, t('risk.noQuote')));

      var foot = h('div', { class: 'risk__foot' });
      this.renderCompliance(foot, it);
      return h('article', { class: 'risk' + (it.compliance ? ' is-sent' : '') },
        h('div', { class: 'risk__head' },
          h('a', { class: 'risk__id', href: convHref(it.uid, { crit: it.criterion_id }) }, it.external_id),
          h('span', { class: 'muted t-caption1' }, it.language + ' · ' + U.channelLabel(it.channel)),
          h('span', { class: 'spacer' }),
          it.total != null ? CQA.score(it.total) : null),
        quotes,
        this._d.detailed && it.explanation ? h('p', { class: 'risk__expl t-caption1 subtle' }, it.explanation) : null,
        it.changed_by_appeal || it.needs_review ? h('div', { class: 'row' },
          it.changed_by_appeal ? ui.badge(t('crit.changedByAppeal'), 'brand', 'scale') : null,
          it.needs_review ? ui.badge(t('badge.review'), 'warning', 'eye') : null) : null,
        foot);
    }

    renderCompliance(foot, it) {
      var self = this;
      foot.textContent = '';
      if (it.compliance) {
        foot.appendChild(ui.badge(t('risk.sent'), 'success', 'checkCircle', { title: t('risk.sentTitle', { id: it.compliance.id, d: fmt.date(it.compliance.created_at) }) }));
        foot.appendChild(h('span', { class: 'muted t-caption1' }, t('risk.caseN', { id: it.compliance.id }) + ' · ' + fmt.date(it.compliance.created_at)));
        return;
      }
      var btn = h('button', { class: 'btn btn--small risk__send', type: 'button' }, icon('send'), t('risk.toCompliance'));
      btn.addEventListener('click', function () {
        btn.disabled = true;
        CQA.dom.replace(btn, ui.spinner(), t('risk.sending'));
        CQA.api.toCompliance(it.criterion_result_id).then(function (c) {
          it.compliance = c;
          ui.toast(c.created ? t('risk.sentToast', { id: it.external_id }) : t('risk.alreadyToast', { id: it.external_id }), { tone: 'success' });
          self.dispatchEvent(new CustomEvent('cqa-compliance-changed', { bubbles: true, detail: { case: c } }));
          var art = foot.closest('.risk');
          if (art) art.classList.add('is-sent');
          self.renderCompliance(foot, it);
        }, function (err) {
          btn.disabled = false;
          CQA.dom.replace(btn, icon('send'), t('risk.toCompliance'));
          ui.toast(t('risk.sendFailed'), { tone: 'error', detail: err.message });
        });
      });
      foot.appendChild(btn);
    }
  }

  // ------------------------------------------------------------------ вопросы без ответа
  class CqaUnanswered extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d;
      this.textContent = '';
      this.classList.add('unans');
      if (!d.n_with_signals) {
        this.appendChild(h('p', { class: 'muted' }, t('unans.noSignals')));
        return;
      }
      if (!d.topics.length) {
        this.appendChild(h('p', { class: 'muted' }, t('unans.none')));
        return;
      }
      var max = Math.max.apply(null, d.topics.map(function (x) { return x.n_questions; }));
      var list = h('div', { class: 'unans__list' });
      d.topics.forEach(function (tp) {
        var qs = h('ul', { class: 'unans__qs' });
        tp.questions.forEach(function (q) {
          qs.appendChild(h('li', { class: 'unans__q' },
            h('div', { class: 'unans__q-main' },
              ui.badge(q.status, q.status === 'частично' ? 'mid' : 'warning'),
              h('span', { class: 'unans__q-text' }, q.text)),
            q.message_text ? h('div', { class: 'unans__q-orig t-caption1 muted' }, '«' + q.message_text + '»') : null,
            h('a', { class: 'unans__q-link t-caption1', href: convHref(q.uid, { m: q.mid }) },
              icon('chat', 'icon--16'), q.external_id + ' · ' + q.mid,
              q.score != null ? h('span', { class: 'muted' }, ' · ' + t('unans.score', { s: fmt.score(q.score) })) : null)));
        });
        list.appendChild(h('details', { class: 'unans__topic' },
          h('summary', null,
            icon('chevronRight', 'icon--16 unans__chev'),
            h('span', { class: 'unans__name' }, cap(tp.topic)),
            h('span', { class: 'unans__bar' }, ui.bar(tp.n_questions / max, { tone: 'warning' })),
            h('span', { class: 'unans__nums num' },
              h('b', null, t('unans.questions', { n: tp.n_questions })),
              h('span', { class: 'muted' }, ' · ' + t('unans.dialogs', { n: tp.n_conversations }))),
            h('span', { class: 'unans__avg', title: t('unans.avgTitle') }, CQA.score(tp.avg_total_unanswered))),
          qs));
      });
      this.appendChild(h('div', { class: 'unans__legend t-caption1 muted' },
        h('span', null, t('unans.legendTopic')), h('span', null, t('unans.legendAvg'))));
      this.appendChild(list);
    }
  }

  // ------------------------------------------------------------------ причины обращения
  class CqaReasons extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d;
      this.textContent = '';
      if (!d.n_with_signals || !d.reasons.length) {
        this.appendChild(h('p', { class: 'muted' }, t('unans.noSignals')));
        return;
      }
      var maxShare = Math.max.apply(null, d.reasons.map(function (r) { return r.share; }));
      var tbody = h('tbody');
      d.reasons.forEach(function (r) {
        var tone = U.scoreTone(r.avg_total);
        tbody.appendChild(h('tr', null,
          h('th', { scope: 'row', class: 'reasons__topic' }, cap(r.topic)),
          h('td', { class: 'reasons__bar-cell' },
            h('div', { class: 'reasons__cell' }, ui.bar(r.share / maxShare, { tone: 'brand' }),
              h('span', { class: 'num nowrap' }, h('b', null, fmt.pct(r.share)), h('span', { class: 'muted' }, ' · ' + r.n_conversations)))),
          h('td', { class: 'reasons__bar-cell' },
            h('div', { class: 'reasons__cell' }, ui.bar(r.avg_total == null ? 0 : r.avg_total / 100, { tone: tone === 'good' ? 'success' : tone === 'mid' ? 'mid' : 'danger' }),
              h('span', { class: 'num nowrap' }, CQA.score(r.avg_total)))),
          h('td', { class: 'reasons__bar-cell' },
            h('div', { class: 'reasons__cell' }, ui.bar(r.resolved_share, { tone: r.resolved_share >= 0.75 ? 'success' : r.resolved_share >= 0.5 ? 'mid' : 'danger' }),
              h('span', { class: 'num nowrap' }, fmt.pct(r.resolved_share)))),
          h('td', { class: 'num' }, r.gross_count ? h('a', { class: 'reasons__gross', href: '#/risks' }, String(r.gross_count)) : h('span', { class: 'muted' }, '0')),
          h('td', { class: 'num col-unans' }, r.n_unanswered_questions ? String(r.n_unanswered_questions) : h('span', { class: 'muted' }, '0'))));
      });
      this.appendChild(h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact reasons' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, t('reasons.topic')),
          h('th', { scope: 'col' }, t('reasons.share')),
          h('th', { scope: 'col' }, t('reasons.avg')),
          h('th', { scope: 'col' }, t('reasons.resolved')),
          h('th', { scope: 'col', class: 'num' }, t('reasons.gross')),
          h('th', { scope: 'col', class: 'num col-unans' }, t('reasons.unanswered')))),
        tbody)));
    }
  }

  customElements.define('cqa-risk-radar', CqaRiskRadar);
  customElements.define('cqa-unanswered', CqaUnanswered);
  customElements.define('cqa-reasons', CqaReasons);
})(window.CQA = window.CQA || {});
