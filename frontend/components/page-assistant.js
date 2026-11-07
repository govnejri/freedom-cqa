/* <cqa-page-assistant> — ассистент руководителя #/assistant: вопрос обычным языком → ответ с цифрами и ссылками на
   диалоги. Ответ идёт 25–30 с: показываем этапы (разбор вопроса → SQL или поиск → ответ) и таймер.
   В ответе: текст (markdown, ссылки [p1-ru-007](uid)), источники, «SQL-запрос» и «Данные», тип, стоимость, время,
   предупреждения. История вопросов — в sessionStorage вкладки. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var SS_HIST = 'cqa.assistant.history';
  var KIND_ICON = { numbers: 'chart', meaning: 'search', mixed: 'compare' };

  function cell(v) {
    if (v == null) return h('span', { class: 'muted' }, 'NULL');
    if (typeof v === 'number') return fmt.num(v, 2);
    var s = String(v);
    if (/^[A-Z]+:[\w-]+$/.test(s)) return h('a', { class: 'mono', href: CQA.convHref(s) }, s);
    return s.length > 160 ? s.slice(0, 157) + '…' : s;
  }

  class CqaPageAssistant extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.assistant') + ' · CQA';
      var self = this;
      this.input = h('textarea', { class: 'textarea as-input', id: 'as-q', rows: '2', maxlength: '1000', placeholder: t('as.placeholder') });
      this.askBtn = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('sparkle'), t('as.ask'));
      this.examples = h('div', { class: 'chip-list as-examples' });
      this.answerBox = h('div', { class: 'as-answer-box', 'aria-live': 'polite' });
      this.histBox = h('div');
      var form = h('form', { class: 'card stack as-form' },
        h('label', { class: 'field__label', for: 'as-q' }, t('as.question')),
        h('div', { class: 'as-input-row' }, this.input, this.askBtn),
        h('div', { class: 'row' }, h('span', { class: 'muted t-caption1' }, t('as.examples')), this.examples));
      form.addEventListener('submit', function (e) { e.preventDefault(); self.ask(); });
      this.input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); self.ask(); } });
      this.textContent = '';
      this.appendChild(h('div', { class: 'page as-page' },
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('nav.assistant')), h('div', { class: 'page-header__sub' }, t('as.sub')))),
        h('div', { class: 'as-layout' },
          h('div', { class: 'stack stack--l as-main' }, form, this.answerBox),
          h('aside', { class: 'card as-hist', 'aria-labelledby': 'as-hist-title' },
            h('h2', { class: 'card__title row', id: 'as-hist-title' }, icon('history'), t('as.history')),
            h('div', { class: 'muted t-caption1' }, t('as.historyNote')),
            this.histBox))));
      this.loadExamples();
      this.renderHistory();
      var hist = this.history();
      if (hist.length) this.showAnswer(hist[hist.length - 1].answer);
      else CQA.dom.replace(this.answerBox, h('section', { class: 'card' }, CQA.steps([
        { title: t('as.how1'), text: t('as.how1Text') }, { title: t('as.how2'), text: t('as.how2Text') }, { title: t('as.how3'), text: t('as.how3Text') }])));
    }

    disconnectedCallback() { clearInterval(this.stepTimer); }

    loadExamples() {
      var self = this;
      CQA.api.assistantExamples().then(function (list) {
        CQA.dom.replace(self.examples, (list || []).map(function (e) {
          return h('button', { class: 'toggle', type: 'button', title: e.question, onClick: function () { self.input.value = e.question; self.ask(); } },
            icon(KIND_ICON[e.kind] || 'sparkle'), e.label);
        }));
      }, function () { CQA.dom.replace(self.examples, h('span', { class: 'muted t-caption1' }, t('as.noExamples'))); });
    }

    history() { return CQA.store.get(SS_HIST, [], true) || []; }

    renderHistory() {
      var self = this, list = this.history();
      if (!list.length) { CQA.dom.replace(this.histBox, h('p', { class: 'muted' }, t('as.historyEmpty'))); return; }
      CQA.dom.replace(this.histBox, h('ul', { class: 'as-hist__list' }, list.slice().reverse().map(function (x) {
        return h('li', null, h('button', { class: 'as-hist__item' + (self.shown === x.answer ? ' is-current' : ''), type: 'button', onClick: function () { self.showAnswer(x.answer); self.renderHistory(); } },
          h('span', { class: 'as-hist__q' }, x.question),
          h('span', { class: 'muted t-caption1' }, fmt.time(x.at) + ' · ' + t('as.kind.' + x.answer.kind) + ' · ' + fmt.ms(x.answer.latency_ms))));
      })), h('button', { class: 'btn btn--small btn--subtle', type: 'button', onClick: function () { CQA.store.set(SS_HIST, null, true); self.renderHistory(); } }, icon('delete', 'icon--16'), t('as.clear')));
    }

    ask() {
      var self = this, question = this.input.value.trim();
      if (!question) { this.input.focus(); return; }
      if (this.busy) return;
      this.busy = true;
      this.askBtn.disabled = true;
      var el = h('span', { class: 'num' });
      var stepsBox = h('div');
      var t0 = Date.now();
      var paint = function () {
        var sec = (Date.now() - t0) / 1000;
        CQA.dom.replace(stepsBox, CQA.steps([
          { title: t('as.step1'), text: t('as.step1Text') },
          { title: t('as.step2'), text: t('as.step2Text') },
          { title: t('as.step3'), text: t('as.step3Text') }], sec < 4 ? 0 : sec < 15 ? 1 : 2));
      };
      paint();
      clearInterval(this.stepTimer);
      this.stepTimer = setInterval(paint, 1000);
      CQA.dom.replace(this.answerBox, h('section', { class: 'card as-wait', role: 'status', 'aria-busy': 'true' },
        h('div', { class: 'as-wait__q' }, icon('question', 'icon--16'), h('span', null, question)),
        h('div', { class: 'row' }, ui.spinner(), h('b', null, t('as.analyzing')), h('span', { class: 'muted' }, el)),
        stepsBox,
        h('div', { class: 'progress progress--indeterminate' }, h('div', { class: 'progress__bar' })),
        h('div', { class: 'muted t-caption1' }, t('as.waitNote'))));
      U.elapsed(el, t0);
      CQA.api.assistantAsk(question).then(function (a) {
        var hist = self.history();
        hist.push({ question: question, answer: a, at: new Date().toISOString() });
        CQA.store.set(SS_HIST, hist.slice(-10), true);
        self.showAnswer(a);
        self.renderHistory();
      }, function (err) {
        CQA.dom.replace(self.answerBox, h('section', { class: 'card' },
          h('div', { class: 'as-wait__q' }, icon('question', 'icon--16'), h('span', null, question)),
          ui.msgbar(CQA.api.mode === 'mock' && err.status === 422 ? 'warning' : 'danger', t('as.failed'), U.errText(err),
            h('button', { class: 'btn btn--small', type: 'button', onClick: function () { self.ask(); } }, icon('refresh'), t('action.retry')))));
      }).finally(function () {
        clearInterval(self.stepTimer);
        self.busy = false;
        self.askBtn.disabled = false;
      });
    }

    showAnswer(a) {
      this.shown = a;
      var rows = a.rows || [], cols = a.columns || (rows[0] ? Object.keys(rows[0]) : []);
      var r = a.route || {};
      var kindBadge = ui.badge(t('as.kind.' + a.kind), 'brand', KIND_ICON[a.kind] || 'sparkle', { title: t('as.kindTitle.' + a.kind) });
      var sources = (a.sources || []).length ? h('section', { class: 'as-sources', 'aria-labelledby': 'as-src-title' },
        h('h3', { class: 'as-sub', id: 'as-src-title' }, icon('link', 'icon--16'), t('as.sources', { n: a.sources.length })),
        h('ul', { class: 'as-src-list' }, a.sources.map(function (s) {
          return h('li', null, h('a', { class: 'as-src', href: CQA.convHref(s.uid, s.mid ? { m: s.mid } : null) },
            h('span', { class: 'mono as-src__id' }, s.external_id), s.mid ? h('span', { class: 'msg__mid' }, s.mid) : null,
            h('span', { class: 'as-src__why' }, s.why), icon('chevronRight', 'icon--16 muted')));
        }))) : null;
      var sql = a.sql ? h('details', { class: 'disclosure as-disc' },
        h('summary', null, icon('chevronRight'), icon('code', 'icon--16'), t('as.sql')),
        h('pre', { class: 'code-block code-block--response' }, a.sql)) : null;
      var data = cols.length ? h('details', { class: 'disclosure as-disc' },
        h('summary', null, icon('chevronRight'), icon('database', 'icon--16'), t('as.data', { n: a.row_count != null ? a.row_count : rows.length })),
        h('div', { class: 'disclosure__body stack' },
          rows.length ? h('div', { class: 'table-scroll as-data' }, h('table', { class: 'table table--compact' },
            h('thead', null, h('tr', null, cols.map(function (c) { return h('th', { scope: 'col' }, c); }))),
            h('tbody', null, rows.map(function (row) { return h('tr', null, cols.map(function (c) { return h('td', null, cell(row[c])); })); })))) : h('p', { class: 'muted' }, t('as.noRows')),
          a.row_count != null && a.row_count > rows.length ? h('div', { class: 'muted t-caption1' }, t('as.rowsShown', { n: rows.length, total: a.row_count }) + (a.truncated ? ' ' + t('as.truncated') : '')) : null)) : null;
      var route = h('details', { class: 'disclosure as-disc' },
        h('summary', null, icon('chevronRight'), icon('info', 'icon--16'), t('as.route')),
        h('dl', { class: 'kv t-caption1 disclosure__body' },
          h('dt', null, t('as.rKind')), h('dd', null, t('as.kindTitle.' + a.kind)),
          r.language ? [h('dt', null, t('as.rLang')), h('dd', null, r.language.toUpperCase())] : null,
          r.topics && r.topics.length ? [h('dt', null, t('as.rTopics')), h('dd', null, r.topics.join(', '))] : null,
          r.criteria && r.criteria.length ? [h('dt', null, t('as.rCriteria')), h('dd', null, r.criteria.join(', '))] : null,
          r.terms && r.terms.length ? [h('dt', null, t('as.rTerms')), h('dd', null, r.terms.join(', '))] : null,
          a.llm_call_ids && a.llm_call_ids.length ? [h('dt', null, t('as.rCalls')), h('dd', null, String(a.llm_call_ids.length))] : null));
      CQA.dom.replace(this.answerBox, h('section', { class: 'card as-answer', 'aria-labelledby': 'as-answer-q' },
        h('div', { class: 'as-answer__head' },
          h('h2', { class: 'as-answer__q', id: 'as-answer-q' }, a.question),
          h('div', { class: 'row t-caption1 muted' }, kindBadge,
            h('span', { title: t('as.latencyTitle') }, icon('history', 'icon--16'), ' ', fmt.ms(a.latency_ms)),
            h('span', { title: t('as.costTitle') }, fmt.money(a.cost_usd)), CQA.recordedBadge())),
        (a.warnings || []).length ? ui.msgbar('warning', t('as.warnings'), h('ul', null, a.warnings.map(function (w) { return h('li', null, w); }))) : null,
        CQA.md(a.answer),
        sources,
        h('div', { class: 'stack as-discs' }, sql, data, route)));
      var main = document.getElementById('main');
      if (main && this.answerBox.getBoundingClientRect().top < 0) CQA.dom.scrollIntoPane(this.answerBox, { block: 'start' });
    }
  }

  customElements.define('cqa-page-assistant', CqaPageAssistant);
})(window.CQA = window.CQA || {});
