/* Инсайты руководителя: радар рисков (компактный список), вопросы без ответа по темам, причины обращения.
   <cqa-risk-radar>  .data = {risks: RisksDTO, limit: число строк|null, category: null|'обход компании', detailed: bool,
                              operators: {uid: имя оператора}}
   <cqa-unanswered>  .data = UnansweredDTO, .limit = число тем|null
   <cqa-reasons>     .data = ReasonsDTO
   Событие: cqa-compliance-changed {case} (bubbles) — дело передано в комплаенс. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  function convHref(uid, q) { return CQA.router.href('/c/' + encodeURIComponent(uid), q); }
  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  // ------------------------------------------------------------------ радар рисков
  class CqaRiskRadar extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d, self = this, r = d.risks;
      this.textContent = '';
      if (!r || !r.total) {
        this.appendChild(h('div', { class: 'risks__empty' }, icon('checkCircle'), h('span', null, t('risk.none'))));
        return;
      }
      var byId = {};
      r.items.forEach(function (it) { byId[it.criterion_result_id] = it; });
      var cats = r.by_category.filter(function (c) { return !d.category || c.category === d.category; });
      var left = d.limit || Infinity, shownTotal = 0, total = 0;
      cats.forEach(function (c) {
        var items = c.criterion_result_ids.map(function (id) { return byId[id]; }).filter(Boolean);
        total += items.length;
        if (left <= 0) return;
        var shown = items.slice(0, left);
        left -= shown.length;
        shownTotal += shown.length;
        var sent = items.filter(function (it) { return it.compliance; }).length;
        self.appendChild(h('div', { class: 'risks__cat' },
          h('span', { class: 'risks__dot', 'aria-hidden': 'true' }),
          h('span', null, cap(c.category)),
          h('span', { class: 'risks__count' }, String(c.count)),
          sent ? h('span', { class: 'muted t-caption1' }, t('risk.sentN', { n: sent, total: c.count })) : null));
        shown.forEach(function (it) { self.appendChild(self.row(it)); });
      });
      if (d.limit && total > shownTotal) {
        this.appendChild(h('div', { class: 'risks__more' },
          h('a', { class: 'link-more', href: '#/risks' }, t('risk.allN', { n: total }), icon('chevronRight'))));
      }
    }

    row(it) {
      var d = this._d;
      var ev = it.evidence[0];
      var who = (d.operators && d.operators[it.uid]) || null;
      var quote = ev
        ? h('a', { class: 'risks__quote', href: convHref(it.uid, { crit: it.criterion_id, m: ev.mid }), 'data-tip': t('risk.openQuote', { mid: ev.mid }) },
          h('span', null, '«' + (ev.quote || ev.text || '') + '»'),
          it.evidence.length > 1 ? h('small', null, t('risk.moreQuotes', { n: it.evidence.length - 1 })) : null)
        : h('span', { class: 'muted' }, t('risk.noQuote'));
      var act = h('div', { class: 'risks__act' });
      var row = h('div', { class: 'risks__row' + (it.compliance ? ' is-sent' : '') },
        h('div', { class: 'risks__who' },
          h('b', null, who || t('risk.noOperator')),
          h('a', { href: convHref(it.uid, { crit: it.criterion_id }) }, it.external_id + ' · ' + it.language)),
        quote,
        act,
        d.detailed && it.explanation ? h('div', { class: 'risks__expl' }, CQA.cleanExplanation ? CQA.cleanExplanation(it.explanation, it.criterion_title) : it.explanation) : null);
      this.renderCompliance(act, it, row);
      return row;
    }

    /** «В комплаенс» в два шага на месте: «Передать?» → «Да» / «Отмена». */
    renderCompliance(box, it, row) {
      var self = this;
      CQA.dom.clear(box);
      if (it.compliance) {
        box.appendChild(h('span', { class: 'risks__sent', 'data-tip': t('risk.sentTitle', { id: it.compliance.id, d: fmt.date(it.compliance.created_at) }) },
          icon('checkCircle', 'icon--16'), t('risk.sentShort', { id: it.compliance.id })));
        return;
      }
      var btn = h('button', { class: 'btn btn--small', type: 'button' }, icon('send'), t('risk.toCompliance'));
      btn.addEventListener('click', function () {
        var timer;
        var yes = h('button', { class: 'btn btn--small btn--primary', type: 'button' }, t('risk.confirmYes'));
        var no = h('button', { class: 'btn btn--small btn--subtle', type: 'button', onClick: function () { clearTimeout(timer); self.renderCompliance(box, it, row); } }, t('action.cancel'));
        yes.addEventListener('click', function () {
          clearTimeout(timer);
          yes.disabled = true; no.disabled = true;
          CQA.dom.replace(yes, ui.spinner(), t('risk.sending'));
          CQA.api.toCompliance(it.criterion_result_id).then(function (c) {
            it.compliance = c;
            ui.toast(c.created ? t('risk.sentToast', { id: it.external_id }) : t('risk.alreadyToast', { id: it.external_id }), { tone: 'success' });
            self.dispatchEvent(new CustomEvent('cqa-compliance-changed', { bubbles: true, detail: { case: c } }));
            document.dispatchEvent(new CustomEvent('cqa-compliance-changed'));
            row.classList.add('is-sent');
            self.renderCompliance(box, it, row);
          }, function (err) {
            ui.toast(t('risk.sendFailed'), { tone: 'error', detail: err.message });
            self.renderCompliance(box, it, row);
          });
        });
        CQA.dom.replace(box, h('span', { class: 'risks__confirm' }, h('span', { class: 't-caption1 muted' }, t('risk.confirmQ')), yes, no));
        yes.focus();
        timer = setTimeout(function () { if (yes.isConnected && !yes.disabled) self.renderCompliance(box, it, row); }, 8000);
      });
      box.appendChild(btn);
    }
  }

  // ------------------------------------------------------------------ вопросы без ответа
  class CqaUnanswered extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var d = this._d, self = this;
      this.textContent = '';
      if (!d.n_with_signals) {
        this.appendChild(h('div', { class: 'unans__empty' }, ui.empty(t('unans.noSignalsTitle'), t('unans.noSignals'),
          h('button', { class: 'btn btn--primary', type: 'button', onClick: function () { CQA.openPipeline({ focus: 'signals' }); } }, icon('sparkle'), t('dash.signals')), 'question')));
        return;
      }
      if (!d.topics.length) {
        this.appendChild(h('div', { class: 'risks__empty' }, icon('checkCircle'), h('span', null, t('unans.none'))));
        return;
      }
      var max = Math.max.apply(null, d.topics.map(function (x) { return x.n_questions; }));
      var all = !this.limit || this.expanded;
      var topics = all ? d.topics : d.topics.slice(0, this.limit);
      this.appendChild(h('div', { class: 'unans__head', 'aria-hidden': 'true' },
        h('span', null, t('reasons.topic')), h('span', null, t('unans.colQ')), h('span', null, t('unans.colD')), h('span', null, t('unans.colAvg'))));
      topics.forEach(function (tp) {
        var qs = h('ul', { class: 'unans__qs' });
        tp.questions.forEach(function (q) {
          qs.appendChild(h('li', { class: 'unans__q' },
            h('span', { class: 'unans__q-text' }, q.text),
            q.message_text ? h('span', { class: 'unans__q-orig' }, '«' + q.message_text + '»') : null,
            h('a', { class: 'unans__q-link', href: convHref(q.uid, { m: q.mid }) },
              t('unans.openQ', { id: q.external_id }), q.score != null ? h('span', { class: 'muted' }, ' · ' + t('unans.score', { s: fmt.score(q.score) })) : null)));
        });
        var uids = [], seen = {};
        tp.questions.forEach(function (q) { if (!seen[q.uid]) { seen[q.uid] = 1; uids.push({ uid: q.uid, label: q.external_id, note: q.text }); } });
        if (uids.length) {
          qs.appendChild(h('li', { class: 'unans__lesson' },
            h('button', { class: 'btn btn--small', type: 'button', 'data-tip': t('lesson.fromTopicTitle'), onClick: function () {
              CQA.openLessonDialog({ title: t('lesson.topicTitle', { topic: cap(tp.topic) }), items: uids });
            } }, icon('cap'), t('lesson.fromTopic', { n: uids.length }))));
        }
        self.appendChild(h('details', { class: 'unans__topic' },
          h('summary', null,
            h('span', { class: 'unans__name' }, icon('chevronRight', 'icon--16'), cap(tp.topic)),
            h('span', { class: 'unans__n', 'aria-label': t('unans.questions', { n: tp.n_questions }) }, ui.bar(tp.n_questions / max, { tone: 'warning' }), String(tp.n_questions)),
            h('span', { class: 'unans__c', 'aria-label': t('unans.dialogs', { n: tp.n_conversations }) },
              tp.n_conversations_total ? t('unans.nOf', { n: tp.n_conversations, m: tp.n_conversations_total }) : String(tp.n_conversations)),
            h('span', { class: 'unans__avg', 'data-tip': t('unans.avgTitle') }, CQA.score(tp.avg_total_unanswered))),
          qs));
      });
      if (this.limit && d.topics.length > this.limit) {
        this.appendChild(h('div', { class: 'unans__more' },
          h('button', { class: 'btn btn--link', type: 'button', onClick: function () { self.expanded = !self.expanded; self.render(); } },
            this.expanded ? t('unans.fewer') : t('unans.allN', { n: d.topics.length }))));
      }
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
            h('div', { class: 'reasons__cell' }, ui.bar(r.avg_total == null ? 0 : r.avg_total / 100, { tone: tone === 'good' ? 'success' : 'warning' }),
              h('span', { class: 'num nowrap' }, CQA.score(r.avg_total)))),
          h('td', { class: 'reasons__bar-cell' },
            h('div', { class: 'reasons__cell' }, ui.bar(r.resolved_share, { tone: r.resolved_share >= 0.75 ? 'success' : 'warning' }),
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
