/* <cqa-page-tutor> — тьютор оператора #/tutor?tab=best&topic=&q=
   «Чат»: вопрос (+ необязательно оператор), ответ с метками [S1] → ссылки на карточки источников («ваша оценка»,
   «ответ коллеги», «чек-лист») с переходом к реплике #/c/{uid}?m={mid}; found=false — честное «нет информации».
   История вопросов — в sessionStorage вкладки.
   «Лучшие ответы»: база ответов коллег (GET /tutor/best-answers) — фильтр по теме и поиск по словам. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var SS_HIST = 'cqa.tutor.history';
  var KIND = {
    own: { tone: 'brand', icon: 'person', label: 'tutor.kOwn' },
    best_answer: { tone: 'success', icon: 'people', label: 'tutor.kBest' },
    checklist: { tone: 'neutral', icon: 'checklist', label: 'tutor.kChecklist' }
  };
  var KIND_ORDER = ['own', 'best_answer', 'checklist'];
  var EXAMPLES = ['tutor.ex1', 'tutor.ex2', 'tutor.ex3', 'tutor.ex4'];
  /** Вопрос о себе: без выбранного оператора тьютору не на что опереться — платный вызов впустую. */
  var PERSONAL = /(^|[^а-яё])(мне|меня|мой|моя|мои|моих|моё|мое|мною|я)([^а-яё]|$)/i;

  class CqaPageTutor extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.tutor') + ' · CQA';
      var self = this, q = this.query || {};
      this.tab = q.tab === 'best' ? 'best' : 'chat';
      this.tabChat = h('button', { class: 'tab', role: 'tab', type: 'button', id: 'tutor-tab-chat', 'aria-controls': 'tutor-pane' }, icon('chat'), t('tutor.tabChat'));
      this.tabBest = h('button', { class: 'tab', role: 'tab', type: 'button', id: 'tutor-tab-best', 'aria-controls': 'tutor-pane' }, icon('people'), t('tutor.tabBest'));
      this.tabChat.addEventListener('click', function () { self.setTab('chat'); });
      this.tabBest.addEventListener('click', function () { self.setTab('best'); });
      this.pane = h('div', { id: 'tutor-pane', role: 'tabpanel' });
      this.textContent = '';
      this.appendChild(h('div', { class: 'page tutor-page' },
        CQA.subnav('learning', 'tutor'),
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('nav.tutor')), h('div', { class: 'page-header__sub' }, t('tutor.sub')))),
        h('div', { class: 'tabs tutor-tabs', role: 'tablist', 'aria-label': t('nav.tutor') }, this.tabChat, this.tabBest),
        this.pane));
      this.renderTab();
    }

    update(params, query) {
      var tab = query.tab === 'best' ? 'best' : 'chat';
      this.query = query;
      if (tab !== this.tab) { this.tab = tab; this.renderTab(); }
      return true;
    }

    setTab(tab) {
      if (tab === this.tab) return;
      this.tab = tab;
      CQA.router.setQuery(tab === 'best' ? { tab: 'best' } : {});
      this.query = tab === 'best' ? { tab: 'best' } : {};
      this.renderTab();
    }

    renderTab() {
      this.tabChat.setAttribute('aria-selected', String(this.tab === 'chat'));
      this.tabBest.setAttribute('aria-selected', String(this.tab === 'best'));
      this.pane.setAttribute('aria-labelledby', this.tab === 'chat' ? 'tutor-tab-chat' : 'tutor-tab-best');
      if (this.tab === 'chat') this.renderChat(); else this.renderBest();
    }

    // ================================================================ чат
    history() { return CQA.store.get(SS_HIST, [], true) || []; }

    renderChat() {
      var self = this;
      this.opSel = CQA.operatorSelect({ id: 'tutor-op', value: CQA.me.get() || '', small: false });
      this.input = h('textarea', { class: 'textarea', id: 'tutor-q', rows: '2', maxlength: '2000', placeholder: t('tutor.placeholder') });
      this.askBtn = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('send'), t('tutor.ask'));
      this.thread = h('div', { class: 'tutor-thread', 'aria-live': 'polite' });
      var form = h('form', { class: 'card tutor-form stack' },
        h('div', { class: 'row tutor-form__top' },
          h('label', { class: 'field__label', for: 'tutor-q' }, t('tutor.question')),
          h('span', { class: 'spacer' }),
          h('label', { class: 'muted t-caption1', for: 'tutor-op' }, t('tutor.operator')), this.opSel),
        this.input,
        h('div', { class: 'row' },
          h('div', { class: 'chip-list tutor-examples' }, h('span', { class: 'muted t-caption1' }, t('tutor.examples')), EXAMPLES.map(function (k) {
            return h('button', { class: 'toggle tutor-ex', type: 'button', onClick: function () { self.input.value = t(k); self.ask(); } }, t(k));
          })),
          h('span', { class: 'spacer' }), this.askBtn),
        h('div', { class: 'muted t-caption1 row' }, icon('info', 'icon--16'), t('tutor.note')));
      var opHint = h('div', { class: 'muted t-caption1 tutor-op-hint' });
      this.opNeed = h('div', { 'aria-live': 'polite' });
      form.appendChild(this.opNeed);
      var syncHint = function () {
        opHint.textContent = self.opSel.value ? t('tutor.opHintSet', { name: self.opSel.value }) : t('tutor.opHintNone');
        if (self.opSel.value) { CQA.dom.clear(self.opNeed); self.opSel.removeAttribute('aria-invalid'); }
      };
      this.opSel.addEventListener('change', syncHint);
      syncHint();
      form.insertBefore(opHint, this.input);
      form.addEventListener('submit', function (e) { e.preventDefault(); self.ask(); });
      this.input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); self.ask(); } });
      var hist = this.history();
      if (this.query && this.query.q && !this.input.value) this.input.value = this.query.q;
      CQA.dom.replace(this.pane, h('div', { class: 'tutor-chat stack stack--l' }, form, this.thread));
      CQA.dom.clear(this.thread);
      if (!hist.length) this.thread.appendChild(h('p', { class: 'muted tutor-empty' }, t('tutor.empty')));
      hist.slice().reverse().forEach(function (x, i) { self.thread.appendChild(self.answerCard(x.request, x.response, hist.length - 1 - i)); });
      if (hist.length) this.thread.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn btn--small btn--subtle', type: 'button', onClick: function () {
        CQA.store.set(SS_HIST, null, true); self.renderChat();
      } }, icon('delete', 'icon--16'), t('tutor.clear'))));
    }

    ask() {
      var self = this, question = this.input.value.trim();
      if (!question || this.busy) { if (!question) this.input.focus(); return; }
      if (!this.opSel.value && PERSONAL.test(question)) {
        this.opSel.setAttribute('aria-invalid', 'true');
        CQA.dom.replace(this.opNeed, ui.msgbar('warning', t('tutor.needOp'), t('tutor.needOpText')));
        this.opSel.focus();
        return;
      }
      CQA.dom.clear(this.opNeed);
      this.opSel.removeAttribute('aria-invalid');
      var req = { question: question, operator: this.opSel.value || null };
      this.busy = true;
      this.askBtn.disabled = true;
      var empty = this.thread.querySelector('.tutor-empty');
      if (empty) empty.remove();
      var el = h('span', { class: 'muted num' });
      var wait = h('article', { class: 'card tutor-answer is-wait', role: 'status' },
        h('div', { class: 'tutor-q' }, icon('person', 'icon--16'), h('span', null, question)),
        h('div', { class: 'row' }, ui.spinner(), h('span', null, t('tutor.thinking')), el),
        h('div', { class: 'muted t-caption1' }, t('tutor.thinkingNote')));
      this.thread.insertBefore(wait, this.thread.firstChild);
      U.elapsed(el);
      CQA.api.tutorAsk(req).then(function (res) {
        var hist = self.history();
        hist.push({ request: req, response: res, at: new Date().toISOString() });
        CQA.store.set(SS_HIST, hist.slice(-20), true);
        wait.replaceWith(self.answerCard(req, res, hist.length - 1));
        self.input.value = '';
      }, function (err) {
        wait.replaceWith(h('article', { class: 'card tutor-answer' },
          h('div', { class: 'tutor-q' }, icon('person', 'icon--16'), h('span', null, question)),
          ui.msgbar(CQA.api.mode === 'mock' && err.status === 422 ? 'warning' : 'danger', t('tutor.failed'), U.errText(err))));
      }).finally(function () { self.busy = false; self.askBtn.disabled = false; });
    }

    /** Ответ: текст с [S#] → ссылки на карточки источников ниже, карточки по видам. */
    answerCard(req, res, idx) {
      var by = {};
      (res.sources || []).forEach(function (s) { by[s.ref] = s; });
      var pfx = 'tsrc-' + idx + '-';
      var cite = function (ref) {
        var s = by[ref];
        if (!s) return h('span', { class: 'cite cite--missing', title: t('tutor.citeMissing') }, ref);
        var k = KIND[s.kind] || KIND.checklist;
        return h('a', { class: 'cite cite--' + s.kind, href: '#' + pfx + ref, title: t(k.label) + ': ' + (s.text || '').slice(0, 160),
          onClick: function (e) {
            e.preventDefault();
            var card = document.getElementById(pfx + ref);
            if (!card) return;
            CQA.dom.scrollIntoPane(card, { block: 'center' });
            card.classList.remove('is-flash'); void card.offsetWidth; card.classList.add('is-flash');
          } }, ref);
      };
      var groups = {};
      (res.sources || []).forEach(function (s) { (groups[s.kind] = groups[s.kind] || []).push(s); });
      var srcBox = KIND_ORDER.filter(function (k) { return groups[k]; }).map(function (k) {
        var meta = KIND[k];
        return h('div', { class: 'tsrc-group' },
          h('h4', { class: 'tsrc-group__title' }, icon(meta.icon, 'icon--16'), t(meta.label), h('span', { class: 'muted' }, ' · ' + groups[k].length)),
          h('div', { class: 'tsrc-list' }, groups[k].map(function (s) { return sourceCard(s, pfx); })));
      });
      return h('article', { class: 'card tutor-answer' },
        h('div', { class: 'tutor-q' }, icon('person', 'icon--16'), h('span', null, req.question),
          res.operator || req.operator ? ui.badge(res.operator || req.operator, 'neutral', 'headset', { title: t('tutor.operatorBadge') }) : null,
          CQA.recordedBadge()),
        res.found ? null : ui.msgbar('warning', t('tutor.notFound'), res.answer && res.answer.length < 400 ? res.answer : t('tutor.notFoundText')),
        res.found || (res.answer && res.answer.length >= 400) ? h('div', { class: 'tutor-a' }, h('span', { class: 'tutor-a__icon', 'aria-hidden': 'true' }, icon('book')), CQA.md(res.answer, { cite: cite })) : null,
        res.found && res.sources && res.sources.length && res.sources[0].cited === false ? h('div', { class: 'muted t-caption1' }, t('tutor.uncited')) : null,
        srcBox.length ? h('div', { class: 'tsrc' }, srcBox) : null,
        h('div', { class: 'muted t-caption1 row' },
          h('span', null, t('tutor.meta', { n: res.context_size != null ? res.context_size : (res.sources || []).length })),
          res.mentioned_criteria && res.mentioned_criteria.length ? h('span', null, '· ' + t('tutor.mentioned', { c: res.mentioned_criteria.join(', ') })) : null,
          res.cost_usd != null ? h('span', null, '· ' + fmt.money(res.cost_usd)) : null));
    }

    // ================================================================ лучшие ответы
    renderBest() {
      var self = this, q = this.query || {};
      this.topicSel = h('select', { class: 'select', id: 'best-topic', 'aria-label': t('tutor.topic') }, h('option', { value: '' }, t('tutor.allTopics')));
      this.search = h('input', { class: 'input', type: 'search', id: 'best-q', placeholder: t('tutor.searchPlaceholder'), value: q.q || '', 'aria-label': t('tutor.search') });
      this.bestBox = h('div', { class: 'list-box' }, ui.skeleton('card', 5));
      this.bestCount = h('span', { class: 'muted' });
      var run = U.debounce(function () { self.loadBest(); }, 300);
      this.search.addEventListener('input', run);
      this.topicSel.addEventListener('change', function () { self.loadBest(); });
      CQA.dom.replace(this.pane, h('div', { class: 'stack stack--l' },
        ui.msgbar('info', null, t('tutor.bestIntro')),
        h('div', { class: 'filters card' },
          h('div', { class: 'input-with-icon filters__search' }, icon('search'), this.search),
          this.topicSel, this.bestCount),
        this.bestBox));
      this.pendingTopic = q.topic || '';
      this.loadBest();
    }

    loadBest() {
      var self = this, seq = (this._bseq = (this._bseq || 0) + 1);
      var topic = this.topicSel.value || this.pendingTopic || '', q = this.search.value.trim();
      this.bestBox.classList.add('is-loading');
      CQA.router.setQuery({ tab: 'best', topic: topic || null, q: q || null });
      CQA.api.bestAnswers({ topic: topic || null, q: q || null, limit: 60 }).then(function (d) {
        if (seq !== self._bseq) return;
        self.bestBox.classList.remove('is-loading');
        if (self.topicSel.options.length <= 1) {
          (d.topics || []).forEach(function (tp) { self.topicSel.appendChild(h('option', { value: tp.topic }, (tp.topic || t('tutor.noTopic')) + ' · ' + tp.n)); });
          self.topicSel.value = topic;
          self.pendingTopic = '';
        }
        self.bestCount.textContent = t('tutor.bestCount', { n: d.total });
        if (!d.items.length) {
          CQA.dom.replace(self.bestBox, ui.empty(t('tutor.bestEmpty'), q ? t('tutor.bestEmptyText') : null, null, 'search'));
          return;
        }
        CQA.dom.replace(self.bestBox, h('div', { class: 'best-list' }, d.items.map(bestCard)),
          d.total > d.items.length ? h('p', { class: 'muted t-caption1' }, t('tutor.bestMore', { n: d.items.length, total: d.total })) : null);
      }, function (err) {
        if (seq !== self._bseq) return;
        self.bestBox.classList.remove('is-loading');
        CQA.dom.replace(self.bestBox, ui.error(err, function () { self.loadBest(); }));
      });
    }
  }

  /** Карточка источника тьютора по виду. */
  function sourceCard(s, pfx) {
    var meta = KIND[s.kind] || KIND.checklist;
    var head = h('div', { class: 'tsrc-card__head' }, h('span', { class: 'tsrc-card__ref mono' }, s.ref), ui.badge(t(meta.label), meta.tone, meta.icon));
    var body;
    if (s.kind === 'own') {
      head.appendChild(h('a', { class: 'mono', href: CQA.convHref(s.uid, { crit: s.criterion_id, m: s.mid || null }) }, s.external_id + (s.mid ? ' · ' + s.mid : '')));
      body = [h('div', { class: 'tsrc-card__text' }, s.text),
        s.quote ? h('a', { class: 'tsrc-card__quote', href: CQA.convHref(s.uid, { crit: s.criterion_id, m: s.mid }) }, '«' + s.quote + '»') : null];
    } else if (s.kind === 'best_answer') {
      head.appendChild(h('a', { class: 'mono', href: CQA.convHref(s.uid, { m: s.mid }) }, s.external_id + ' · ' + s.mid));
      if (s.topic) head.appendChild(h('span', { class: 'muted t-caption1' }, s.topic));
      body = [
        h('div', { class: 't-caption1' }, h('span', { class: 'muted' }, t('tutor.clientAsked') + ' '),
          h('a', { href: CQA.convHref(s.uid, { m: s.question_mid }) }, '«' + (s.client_text || s.question) + '»')),
        h('div', { class: 'quote tsrc-card__answer' }, s.text)];
    } else {
      head.appendChild(h('a', { href: '#/checklists/' + encodeURIComponent(CQA.config.checklistId) }, CQA.critLabel(s.criterion_id, s.title)));
      body = [h('details', { class: 'disclosure' }, h('summary', null, icon('chevronRight'), t('tutor.ruleText')),
        h('div', { class: 'disclosure__body t-caption1 subtle pre-wrap' }, s.text))];
    }
    return h('div', { class: 'tsrc-card tsrc-card--' + s.kind, id: pfx + s.ref }, head, body);
  }

  function bestCard(p) {
    return h('article', { class: 'card best' },
      h('div', { class: 'row best__head' },
        p.topic ? ui.badge(p.topic, 'brand') : null,
        ui.badge(p.language, 'neutral', 'globe'),
        h('a', { class: 'mono t-caption1', href: CQA.convHref(p.uid) }, p.external_id),
        p.operator_name ? h('span', { class: 'muted t-caption1' }, p.operator_name) : null,
        h('span', { class: 'spacer' }),
        p.total != null ? CQA.score(p.total) : null),
      h('div', { class: 'best__q' }, h('span', { class: 'crit__label' }, t('tutor.clientQ')),
        h('a', { class: 'best__qtext', href: CQA.convHref(p.uid, { m: p.question_mid }) }, '«' + p.client_text + '»'),
        p.question && p.question !== p.client_text ? h('div', { class: 'muted t-caption1' }, t('tutor.gist', { q: p.question })) : null),
      h('div', null, h('span', { class: 'crit__label' }, t('tutor.colleagueA')),
        h('div', { class: 'quote' }, p.answer_text)),
      h('div', { class: 'row t-caption1' },
        h('a', { href: CQA.convHref(p.uid, { m: p.answer_mid }) }, icon('chat', 'icon--16'), ' ', t('tutor.openAnswer', { mid: p.answer_mid })),
        p.matched && p.matched.length ? h('span', { class: 'muted' }, t('tutor.matched', { w: p.matched.join(', ') })) : null));
  }

  customElements.define('cqa-page-tutor', CqaPageTutor);
})(window.CQA = window.CQA || {});
