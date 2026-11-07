/* Тренажёр оператора (P3): ИИ играет клиента из реального диалога, оператор отвечает заново, новый диалог
   оценивается тем же чек-листом и тем же судьёй → «было/стало».
   <cqa-page-trainer>         #/trainer?lesson=  — тренировки (мои / все) и «Уроки для всех» с прогрессом
   <cqa-page-trainer-new>     #/trainer/new?uid=&focus=04,05&operator=&lesson= — подтверждение и создание сессии
   <cqa-page-trainer-session> #/trainer/{id} — чат с ИИ-клиентом, «Завершить и оценить», итог «было/стало» */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  function same(a, b) { return String(a || '').toLocaleLowerCase('ru') === String(b || '').toLocaleLowerCase('ru'); }
  function scoreOf(s) { return s.final_total != null ? s.final_total : s.total; }

  var HOW = function () {
    return [
      { title: t('trainer.how1'), text: t('trainer.how1Text') },
      { title: t('trainer.how2'), text: t('trainer.how2Text') },
      { title: t('trainer.how3'), text: t('trainer.how3Text') }
    ];
  };

  /** Окно выбора диалога для новой тренировки: худшие по баллу сверху, поиск по ID. */
  function pickDialog() {
    var input = h('input', { class: 'input', type: 'search', placeholder: t('list.searchPlaceholder'), 'aria-label': t('list.search') });
    var box = h('div', { class: 'pick-list' }, ui.skeleton('table', 6));
    var rows = [];
    var render = function () {
      var q = input.value.trim().toLowerCase();
      var list = rows.filter(function (s) { return !q || s.external_id.toLowerCase().indexOf(q) >= 0 || (s.operator_name || '').toLowerCase().indexOf(q) >= 0; }).slice(0, 30);
      CQA.dom.replace(box, list.length ? h('ul', { class: 'pick-list__ul' }, list.map(function (s) {
        return h('li', null, h('a', { class: 'pick-list__item', href: CQA.trainHref(s.uid, { operator: CQA.me.get() }) },
          h('span', { class: 'mono t-body1-strong' }, s.external_id),
          h('span', { class: 'muted t-caption1' }, [s.language, U.channelLabel(s.channel), s.operator_name].filter(Boolean).join(' · ')),
          h('span', { class: 'spacer' }), CQA.score(scoreOf(s))));
      })) : h('p', { class: 'muted' }, t('list.emptyFiltered')));
    };
    input.addEventListener('input', U.debounce(render, 150));
    var m = ui.modal({ title: t('trainer.pickTitle'), body: h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('trainer.pickText')), input, box) });
    m.el.addEventListener('click', function (e) { if (e.target.closest('a')) m.close(); });
    CQA.api.conversations({ sort: 'score' }).then(function (list) {
      rows = (list || []).filter(function (s) { return scoreOf(s) != null && !CQA.isTrainerUid(s.uid); });
      rows.sort(function (a, b) { return scoreOf(a) - scoreOf(b); });
      render();
    }, function (err) { CQA.dom.replace(box, ui.error(err)); });
  }

  /** Урок из худших диалогов (с тренажёра); со сводки — из вопросов без ответа по теме. */
  function lessonFromWorst(onCreated) {
    CQA.api.conversations({ sort: 'score' }).then(function (list) {
      var rows = (list || []).filter(function (s) { return scoreOf(s) != null && !CQA.isTrainerUid(s.uid); });
      rows.sort(function (a, b) { return scoreOf(a) - scoreOf(b); });
      CQA.openLessonDialog({ title: '', preselect: false, onCreated: onCreated,
        items: rows.slice(0, 20).map(function (s) {
          return { uid: s.uid, label: s.external_id, note: [s.language, U.channelLabel(s.channel), t('trainer.scoreN', { s: fmt.score(scoreOf(s)) })].join(' · ') };
        }) });
    }, function (err) { ui.toast(err.message, { tone: 'error' }); });
  }

  // ================================================================== список и уроки
  class CqaPageTrainer extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.trainer') + ' · CQA';
      var self = this;
      this.mine = !!CQA.me.get();
      this.sessBox = h('div', null, ui.skeleton('table', 4));
      this.lessBox = h('div', null, ui.skeleton('card', 4));
      this.mineToggle = h('div');
      this.textContent = '';
      this.appendChild(h('div', { class: 'page tr-page' },
        CQA.subnav('learning', 'trainer'),
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('nav.trainer')), h('div', { class: 'page-header__sub' }, t('trainer.sub'))),
          h('div', { class: 'row' },
            h('button', { class: 'btn', type: 'button', onClick: function () { lessonFromWorst(function () { self.load(); }); } }, icon('people'), t('lesson.createBtn')),
            h('button', { class: 'btn btn--primary', type: 'button', onClick: pickDialog }, icon('target'), t('trainer.new')))),
        h('div', { class: 'stack stack--xl' },
          h('section', { class: 'card tr-how' }, CQA.steps(HOW())),
          h('div', { class: 'tr-cols' },
            h('section', { class: 'card', 'aria-labelledby': 'tr-sess' },
              h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'tr-sess' }, t('trainer.sessions')), this.mineToggle),
              this.sessBox),
            h('section', { class: 'card', 'aria-labelledby': 'tr-less' },
              h('div', { class: 'card__header' },
                h('div', null, h('h2', { class: 'card__title row', id: 'tr-less' }, icon('people'), t('lesson.title')),
                  h('div', { class: 'muted t-caption1' }, t('lesson.sub')))),
              this.lessBox)))));
      this.load();
    }

    update(params, query) { this.query = query; this.renderLessons(); return true; }

    load() {
      var self = this;
      Promise.all([CQA.api.trainerSessions(), CQA.api.lessons()]).then(function (res) {
        self.sessions = res[0] || [];
        self.lessons = res[1] || [];
        self.renderSessions();
        self.renderLessons();
      }, function (err) {
        CQA.dom.replace(self.sessBox, ui.error(err, function () { self.load(); }));
        CQA.dom.clear(self.lessBox);
      });
    }

    renderSessions() {
      var self = this, me = CQA.me.get();
      if (me) {
        CQA.dom.replace(this.mineToggle, h('div', { class: 'segmented', role: 'group', 'aria-label': t('trainer.whose') },
          h('button', { type: 'button', 'aria-pressed': String(this.mine), onClick: function () { self.mine = true; self.renderSessions(); } }, t('trainer.mine', { name: me })),
          h('button', { type: 'button', 'aria-pressed': String(!this.mine), onClick: function () { self.mine = false; self.renderSessions(); } }, t('trainer.all'))));
      }
      var list = this.sessions.filter(function (s) { return !me || !self.mine || same(s.operator, me); });
      if (!list.length) {
        CQA.dom.replace(this.sessBox, ui.empty(t('trainer.noSessions'), t('trainer.noSessionsText'),
          h('button', { class: 'btn', type: 'button', onClick: pickDialog }, icon('target'), t('trainer.new')), 'target'));
        return;
      }
      CQA.dom.replace(this.sessBox, CQA.sessionRows(list, { showOperator: !self.mine || !me }));
    }

    renderLessons() {
      if (!this.lessons) return;
      var self = this, me = CQA.me.get(), focus = Number((this.query || {}).lesson) || null;
      if (!this.lessons.length) {
        CQA.dom.replace(this.lessBox, h('div', { class: 'stack' },
          h('p', { class: 'muted' }, t('lesson.none')),
          h('div', { class: 'row' },
            h('a', { class: 'btn btn--small', href: '#/' }, icon('question'), t('lesson.fromDash')),
            h('button', { class: 'btn btn--small', type: 'button', onClick: function () { lessonFromWorst(function () { self.load(); }); } }, icon('people'), t('lesson.createBtn')))));
        return;
      }
      var cards = this.lessons.map(function (l) {
        var done = l.items.filter(function (it) { return it.sessions.some(function (s) { return s.status === 'finished'; }); }).length;
        var mineDone = me ? l.items.filter(function (it) { return it.sessions.some(function (s) { return s.status === 'finished' && same(s.operator, me); }); }).length : null;
        var share = l.items.length ? (me ? mineDone : done) / l.items.length : 0;
        return h('article', { class: 'lesson' + (focus === l.id ? ' is-focus' : ''), id: 'lesson-' + l.id },
          h('div', { class: 'lesson__head' },
            h('h3', { class: 'lesson__title' }, l.title),
            h('span', { class: 'muted t-caption1' }, fmt.date(l.created_at))),
          h('div', { class: 'lesson__progress' },
            ui.bar(share, { tone: share >= 1 ? 'success' : 'brand', label: t('lesson.progressAria') }),
            h('span', { class: 't-caption1 nowrap' }, me ? t('lesson.progressMe', { d: mineDone, n: l.items.length }) : t('lesson.progress', { d: done, n: l.items.length }))),
          h('div', { class: 'muted t-caption1' }, t('lesson.sessionsN', { n: l.n_sessions, f: l.n_finished })),
          h('ul', { class: 'lesson__items' }, l.items.map(function (it) {
            var fin = it.sessions.filter(function (s) { return s.status === 'finished'; });
            var myFin = me && fin.some(function (s) { return same(s.operator, me); });
            var best = fin.reduce(function (a, s) { return s.after_total != null && (a == null || s.after_total > a) ? s.after_total : a; }, null);
            return h('li', { class: 'lesson__item' },
              myFin ? icon('checkCircle', 'lesson__ok') : icon('target', 'muted'),
              h('a', { class: 'mono', href: CQA.convHref(it.base_uid) }, it.external_id || fmt.externalId(it.base_uid)),
              h('span', { class: 'muted t-caption1' }, [it.language, it.channel ? U.channelLabel(it.channel) : null].filter(Boolean).join(' · ')),
              h('span', { class: 'spacer' }),
              it.base_total != null ? h('span', { class: 't-caption1 muted nowrap' }, t('trainer.wasScore'), ' ', CQA.score(it.base_total)) : null,
              best != null ? h('span', { class: 't-caption1 muted nowrap' }, t('lesson.best'), ' ', CQA.score(best)) : null,
              h('a', { class: 'btn btn--small' + (myFin ? '' : ' btn--primary'), href: CQA.trainHref(it.base_uid, { lesson: l.id, operator: me }) }, icon('target'), myFin ? t('lesson.again') : t('lesson.start')));
          })));
      });
      CQA.dom.replace(this.lessBox, h('div', { class: 'stack' }, cards));
      if (focus) { var el = this.querySelector('#lesson-' + focus); if (el) CQA.dom.scrollIntoPane(el, { block: 'start' }); }
    }
  }

  // ================================================================== старт тренировки
  class CqaPageTrainerNew extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('trainer.newTitle') + ' · CQA';
      this.root = h('div', { class: 'page tr-page tr-new' }, ui.skeleton('card', 6));
      this.textContent = '';
      this.appendChild(this.root);
      this.load();
    }

    update(params, query) { this.query = query; this._built = false; this.connectedCallback(); return true; }

    load() {
      var self = this, q = this.query || {};
      this.uid = q.uid || null;
      if (!this.uid) {
        CQA.dom.replace(this.root, this.back(), ui.empty(t('trainer.noUid'), t('trainer.noUidText'),
          h('button', { class: 'btn btn--primary', type: 'button', onClick: pickDialog }, icon('target'), t('trainer.pickTitle')), 'target'));
        return;
      }
      Promise.all([
        CQA.api.conversation(this.uid),
        CQA.api.evaluation(this.uid).catch(function (e) { if (e.status === 404) return null; throw e; }),
        q.lesson ? CQA.api.lessons().catch(function () { return []; }) : Promise.resolve([])
      ]).then(function (res) {
        self.conv = res[0];
        self.ev = res[1];
        self.lesson = (res[2] || []).filter(function (l) { return String(l.id) === String(q.lesson); })[0] || null;
        self.render();
      }, function (err) {
        CQA.dom.replace(self.root, self.back(), ui.error(err, function () { self.load(); }, err.status === 404 ? t('conv.notFound') : null));
      });
    }

    back() { return h('a', { class: 'back-link', href: '#/trainer' }, icon('arrowLeft', 'icon--16'), t('nav.trainer')); }

    render() {
      var self = this, c = this.conv, ev = this.ev, q = this.query || {};
      var focus = (q.focus || '').split(',').filter(Boolean);
      if (CQA.isTrainerUid(c.uid)) {
        CQA.dom.replace(this.root, this.back(), ui.msgbar('warning', t('trainer.isTrainer'), t('trainer.isTrainerText')));
        return;
      }
      // с чего начнёт клиент: первая реплика клиента до оператора
      var firstOp = c.messages.findIndex(function (m) { return m.speaker === 'operator'; });
      var before = firstOp >= 0 ? c.messages.slice(0, firstOp) : c.messages;
      var firstClient = before.filter(function (m) { return m.speaker === 'client'; })[0];
      var tops = {};
      var checks = [];
      var crits = ev ? ev.criteria.filter(function (cr) { return cr.weight > 0 || cr.type === 'flag'; }) : [];
      var critList = h('div', { class: 'tr-focus' }, crits.map(function (cr) {
        var v = cr.final_verdict != null ? cr.final_verdict : cr.verdict;
        var bad = CQA.isViolation(v, cr.type, cr.scale_top);
        var cb = h('input', { type: 'checkbox', value: cr.criterion_id, checked: focus.indexOf(cr.criterion_id) >= 0 });
        checks.push(cb);
        tops[cr.criterion_id] = cr.scale_top;
        return h('label', { class: 'check tr-focus__item' + (bad ? ' is-bad' : '') }, cb,
          CQA.critLabel(cr.criterion_id, cr.title), CQA.verdict(v, cr.type, cr.scale_top));
      }));
      this.opSel = CQA.operatorSelect({ id: 'tr-op', value: q.operator || CQA.me.get() || '' });
      this.status = h('div', { 'aria-live': 'polite' });
      this.startBtn = h('button', { class: 'btn btn--primary btn--large', type: 'button', onClick: function () { self.start(checks); } }, icon('target'), t('trainer.start'));
      var score = ev ? (ev.final_total != null ? ev.final_total : ev.total) : null;
      CQA.dom.replace(this.root, this.back(),
        h('div', { class: 'page-header' },
          h('div', null,
            h('h1', null, t('trainer.newTitle')),
            h('div', { class: 'page-header__sub row' },
              h('a', { class: 'mono', href: CQA.convHref(c.uid) }, c.external_id),
              ui.badge(c.language, 'neutral', 'globe'), ui.badge(U.channelLabel(c.channel), 'neutral', U.channelIcon(c.channel)),
              c.operator_name ? ui.badge(c.operator_name, 'neutral', 'headset') : null,
              score != null ? [h('span', { class: 'muted' }, t('trainer.wasScore')), CQA.score(score)] : null,
              this.lesson ? ui.badge(t('lesson.badge', { title: this.lesson.title }), 'brand', 'people') : null))),
        h('div', { class: 'tr-new__grid' },
          h('section', { class: 'card stack stack--l' },
            CQA.steps(HOW(), 0),
            firstClient ? h('div', { class: 'stack' }, h('span', { class: 'crit__label' }, t('trainer.clientStarts')),
              CQA.chatMsg({ speaker: 'client', text: firstClient.text, ts: fmt.ts(firstClient.ts_raw) }, { muted: true })) : null,
            h('div', { class: 'field' },
              h('span', { class: 'field__label' }, t('trainer.focus')),
              h('span', { class: 'field__hint' }, ev ? t('trainer.focusHint') : t('trainer.noEval')),
              critList),
            h('div', { class: 'field' },
              h('label', { class: 'field__label', for: 'tr-op' }, t('trainer.who')),
              this.opSel,
              h('div', { class: 'field__hint' }, t('trainer.whoHint'))),
            this.status,
            h('div', { class: 'row' }, this.startBtn, h('span', { class: 'muted t-caption1' }, t('trainer.startNote')))),
          h('aside', { class: 'card tr-new__info stack' },
            h('h2', { class: 'card__title row' }, icon('info'), t('trainer.rulesTitle')),
            h('ul', { class: 'tr-rules' },
              h('li', null, t('trainer.rule1')), h('li', null, t('trainer.rule2')),
              h('li', null, t('trainer.rule3')), h('li', null, t('trainer.rule4'))))));
    }

    start(checks) {
      var self = this, q = this.query || {};
      var focus = checks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
      var dto = { base_uid: this.uid, criteria_focus: focus.length ? focus : null, operator: this.opSel.value || null,
        lesson_id: q.lesson ? Number(q.lesson) : null };
      this.startBtn.disabled = true;
      var el = h('span', { class: 'muted num' });
      CQA.dom.replace(this.status, h('div', { class: 'tr-wait', role: 'status' },
        ui.spinner(), h('div', null, h('b', null, t('trainer.preparing')), h('div', { class: 'muted t-caption1' }, t('trainer.preparingText'), ' ', el))));
      U.elapsed(el);
      CQA.api.createTrainerSession(dto).then(function (s) {
        if (dto.operator && !CQA.me.get()) CQA.me.set(dto.operator);
        try { location.replace(location.pathname + location.search + '#/trainer/' + s.id); }
        catch (e) { location.hash = '#/trainer/' + s.id; }
      }, function (err) {
        self.startBtn.disabled = false;
        var demo = CQA.api.mode === 'mock' && err.status === 422;
        CQA.dom.replace(self.status, ui.msgbar(demo ? 'warning' : 'danger', demo ? t('trainer.demoOnly') : t('trainer.startFailed'), U.errText(err),
          demo ? h('a', { class: 'btn btn--small', href: CQA.trainHref('DEV:p1-ru-007', { operator: dto.operator }) }, t('trainer.openDemo')) : null));
      });
    }
  }

  // ================================================================== сессия
  var OUTCOME = { 'доволен': ['success', 'trainer.outHappy'], 'уходит': ['danger', 'trainer.outLeaves'], 'лимит ходов': ['warning', 'trainer.outLimit'] };
  var CHANGE = ['worse', 'improved', 'same', 'not_comparable', 'pending'];
  var CHANGE_TONE = { improved: 'success', worse: 'danger', same: 'neutral', not_comparable: 'neutral', pending: 'neutral' };
  var CHANGE_ICON = { improved: 'arrowUp', worse: 'arrowDown', same: 'check', not_comparable: 'info', pending: 'history' };

  class CqaPageTrainerSession extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.sid = Number(this.params.id);
      document.title = t('trainer.sessionN', { id: this.sid }) + ' · CQA';
      this.root = h('div', { class: 'page tr-page tr-session' }, ui.skeleton('card', 4), ui.skeleton('transcript', 5));
      this.textContent = '';
      this.appendChild(this.root);
      this.load();
    }

    update(params) { return Number(params.id) === this.sid; }

    load() {
      var self = this;
      CQA.api.trainerSession(this.sid).then(function (s) {
        self.s = s;
        self.render();
      }, function (err) {
        CQA.dom.replace(self.root, h('a', { class: 'back-link', href: '#/trainer' }, icon('arrowLeft', 'icon--16'), t('nav.trainer')),
          ui.error(err, function () { self.load(); }, err.status === 404 ? t('trainer.notFound') : null));
      });
    }

    render() {
      var s = this.s;
      document.title = (s.persona.title || t('trainer.sessionN', { id: s.id })) + ' · CQA';
      this.feed = h('div', { class: 'tr-feed', role: 'log', 'aria-live': 'polite', 'aria-label': t('trainer.feedAria') });
      this.notice = h('div', { class: 'tr-notice', 'aria-live': 'polite' });
      this.composer = h('div', { class: 'tr-composer' });
      var finished = s.status === 'finished';
      CQA.dom.replace(this.root,
        h('a', { class: 'back-link', href: '#/trainer' }, icon('arrowLeft', 'icon--16'), t('nav.trainer')),
        this.header(),
        s.demo_note ? ui.msgbar('info', t('mode.mock'), s.demo_note) : null,
        finished ? this.result() : null,
        h('div', { class: 'tr-layout' + (finished ? ' is-finished' : '') },
          h('section', { class: 'card tr-chat', 'aria-labelledby': 'tr-chat-title' },
            h('div', { class: 'tr-chat__head' },
              h('h2', { class: 'card__title', id: 'tr-chat-title' }, finished ? t('trainer.transcript') : t('trainer.chat')),
              h('span', { class: 'muted t-caption1 num' }, t('trainer.turnOf', { n: s.turns, m: s.max_turns }))),
            this.feed, this.notice, finished ? null : this.composer),
          this.persona()));
      this.renderFeed();
      if (!finished) { this.renderNotice(); this.renderComposer(); this.scrollFeed(); }
    }

    header() {
      var s = this.s, finished = s.status === 'finished';
      var out = s.outcome && OUTCOME[s.outcome];
      return h('div', { class: 'page-header' },
        h('div', null,
          h('div', { class: 'row' }, h('h1', null, s.persona.title || t('trainer.sessionN', { id: s.id })),
            finished ? ui.badge(t('trainer.statusDone'), 'success', 'checkCircle') : ui.badge(t('trainer.statusActive'), 'brand', 'chat'),
            s.client_finished && out ? ui.badge(t(out[1]), out[0]) : null),
          h('div', { class: 'page-header__sub row' },
            h('span', null, t('trainer.byDialog')), h('a', { class: 'mono', href: CQA.convHref(s.base_uid) }, s.base_external_id || fmt.externalId(s.base_uid)),
            s.persona.language_label ? h('span', { class: 'muted' }, '· ' + s.persona.language_label) : null,
            s.operator ? h('span', null, '· ', h('a', { href: CQA.operatorHref(s.operator) }, s.operator)) : null,
            s.criteria_focus.length ? [h('span', { class: 'muted' }, '· ' + t('trainer.focusShort')), s.criteria_focus.map(function (c) { return ui.badge(c, 'brand'); })] : null)),
        finished ? h('div', { class: 'row' },
          h('a', { class: 'btn', href: CQA.trainHref(s.base_uid, { focus: s.criteria_focus, operator: s.operator, lesson: s.lesson_id }) }, icon('refresh'), t('trainer.again'))) : null);
    }

    persona() {
      var p = this.s.persona, finished = this.s.status === 'finished';
      var item = function (label, value) { return value ? [h('dt', null, label), h('dd', null, value)] : null; };
      return h('aside', { class: 'card tr-persona', 'aria-labelledby': 'tr-persona-title' },
        h('h2', { class: 'card__title row', id: 'tr-persona-title' }, icon('person'), t('trainer.persona')),
        h('dl', { class: 'tr-persona__kv' },
          item(t('trainer.pGoal'), p.goal),
          item(t('trainer.pCharacter'), p.character),
          p.already_told && p.already_told.length ? item(t('trainer.pTold'), h('ul', { class: 'tr-list' }, p.already_told.map(function (x) { return h('li', null, x); }))) : null,
          finished ? item(t('trainer.pSatisfied'), p.satisfied_when) : null,
          finished ? item(t('trainer.pLeaves'), p.leaves_when) : null),
        finished ? null : h('div', { class: 'tr-persona__hidden t-caption1' }, icon('eye', 'icon--16'), h('span', null, t('trainer.hiddenNote'))));
    }

    renderFeed() {
      var s = this.s, feed = this.feed;
      CQA.dom.clear(feed);
      var prefix = s.transcript.filter(function (m) { return m.source === 'base'; });
      var rest = s.transcript.filter(function (m) { return m.source !== 'base'; });
      if (prefix.length) {
        feed.appendChild(h('div', { class: 'seg__header tr-sep' }, h('span', null, t('trainer.prefix'))));
        prefix.forEach(function (m) { feed.appendChild(CQA.chatMsg(m, { muted: true })); });
      }
      feed.appendChild(h('div', { class: 'seg__header tr-sep tr-sep--you' }, h('span', null, t('trainer.youJoin'))));
      if (!rest.length && s.status !== 'finished') feed.appendChild(h('p', { class: 'tr-hint muted t-caption1' }, t('trainer.firstHint')));
      rest.forEach(function (m) { feed.appendChild(CQA.chatMsg(m, { label: m.source === 'trainee' ? t('trainer.you') : m.source === 'ai' ? t('trainer.aiClient') : null })); });
    }

    scrollFeed() {
      var feed = this.feed;
      requestAnimationFrame(function () {
        var last = feed.lastElementChild;
        if (last) CQA.dom.scrollIntoPane(last, { block: 'start', smooth: false });
      });
    }

    trainees() { return this.s.transcript.filter(function (m) { return m.source === 'trainee'; }).length; }

    renderNotice() {
      var s = this.s, self = this;
      if (!s.client_finished) { CQA.dom.clear(this.notice); return; }
      var out = OUTCOME[s.outcome] || ['info', 'trainer.outDone'];
      CQA.dom.replace(this.notice, ui.msgbar(out[0] === 'danger' ? 'warning' : out[0] === 'warning' ? 'warning' : 'success', t(out[1]), t('trainer.canBye'),
        h('button', { class: 'btn btn--small btn--primary', type: 'button', onClick: function () { self.finish(); } }, icon('checkCircle'), t('trainer.finish'))));
    }

    renderComposer() {
      var self = this, s = this.s;
      this.input = h('textarea', { class: 'textarea tr-input', rows: '2', maxlength: '4000', placeholder: s.client_finished ? t('trainer.byePlaceholder') : t('trainer.placeholder'),
        'aria-label': t('trainer.inputAria') });
      this.sendBtn = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('send'), t('trainer.send'));
      var n = this.trainees();
      this.finishBtn = h('button', { class: 'btn' + (s.client_finished ? ' btn--primary' : ''), type: 'button', disabled: !n || null,
        title: n ? null : t('trainer.finishDisabled'), onClick: function () { self.finish(); } }, icon('checkCircle'), t('trainer.finish'));
      this.err = h('div', { 'aria-live': 'assertive' });
      var form = h('form', { class: 'tr-composer__form' }, this.input, h('div', { class: 'tr-composer__actions' }, this.sendBtn));
      form.addEventListener('submit', function (e) { e.preventDefault(); self.send(); });
      this.input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); self.send(); }
      });
      CQA.dom.replace(this.composer, this.err, form,
        h('div', { class: 'row tr-composer__foot' }, h('span', { class: 'muted t-caption1' }, t('trainer.enterHint')), h('span', { class: 'spacer' }), this.finishBtn));
    }

    send() {
      var self = this, text = this.input.value.trim();
      if (!text || this.busy) return;
      this.busy = true;
      CQA.dom.clear(this.err);
      this.input.disabled = true;
      this.sendBtn.disabled = true;
      this.finishBtn.disabled = true;
      var hint = this.feed.querySelector('.tr-hint');
      if (hint) hint.remove();
      var pending = CQA.chatMsg({ speaker: 'operator', text: text }, { label: t('trainer.you') });
      pending.classList.add('is-pending');
      this.feed.appendChild(pending);
      var typing = this.s.client_finished ? null : CQA.typingMsg();
      if (typing) this.feed.appendChild(typing);
      this.scrollFeed();
      CQA.api.trainerReply(this.sid, text).then(function (r) {
        pending.remove();
        if (typing) typing.remove();
        r.new_messages.forEach(function (m) {
          self.s.transcript.push(m);
          self.feed.appendChild(CQA.chatMsg(m, { label: m.source === 'trainee' ? t('trainer.you') : t('trainer.aiClient'), fresh: m.source === 'ai' }));
        });
        self.s.turns = r.turn;
        self.s.client_finished = r.finished;
        self.s.outcome = r.outcome;
        self.input.value = '';
        self.busy = false;
        var head = self.root.querySelector('.tr-chat__head .num');
        if (head) head.textContent = t('trainer.turnOf', { n: r.turn, m: r.max_turns });
        self.renderNotice();
        self.renderComposer();
        self.scrollFeed();
        self.input.focus();
        if (r.finished) {
          var out = OUTCOME[r.outcome];
          var hb = self.root.querySelector('.page-header');
          if (hb) hb.replaceWith(self.header());
          if (out) ui.toast(t(out[1]), { tone: out[0] === 'danger' ? 'warning' : out[0] });
        }
      }, function (err) {
        pending.remove();
        if (typing) typing.remove();
        self.busy = false;
        self.input.disabled = false;
        self.sendBtn.disabled = false;
        self.finishBtn.disabled = !self.trainees();
        // реплика не сохранилась на сервере — текст остаётся в поле, можно отправить ещё раз
        CQA.dom.replace(self.err, ui.msgbar('danger', t('trainer.replyFailed'), U.errText(err),
          h('button', { class: 'btn btn--small', type: 'button', onClick: function () { self.send(); } }, icon('refresh'), t('action.retry'))));
        self.input.focus();
      });
    }

    finish() {
      var self = this;
      if (this.busy) return;
      if (!this.trainees()) { ui.toast(t('trainer.finishDisabled'), { tone: 'warning' }); return; }
      var go = function () {
        self.busy = true;
        if (self.input) self.input.disabled = true;
        var el = h('span', { class: 'muted num' });
        var wait = h('section', { class: 'card tr-finishing', role: 'status' },
          h('div', { class: 'row' }, ui.spinner(), h('b', null, t('trainer.evaluating')), el),
          CQA.steps([{ title: t('trainer.ev1') }, { title: t('trainer.ev2') }, { title: t('trainer.ev3') }], 1),
          h('div', { class: 'progress progress--l progress--indeterminate' }, h('div', { class: 'progress__bar' })),
          h('div', { class: 'muted t-caption1' }, t('trainer.evaluatingNote')));
        CQA.dom.replace(self.composer, wait);
        CQA.dom.clear(self.notice);
        U.elapsed(el);
        CQA.dom.scrollIntoPane(wait, { block: 'start' });
        CQA.api.trainerFinish(self.sid).then(function (s) {
          self.busy = false;
          self.s = s;
          self.render();
          var main = document.getElementById('main');
          if (main) main.scrollTop = 0;
          ui.toast(t('trainer.evaluated'), { tone: 'success' });
        }, function (err) {
          self.busy = false;
          self.renderComposer();
          self.renderNotice();
          CQA.dom.replace(self.err, ui.msgbar('danger', t('trainer.finishFailed'), U.errText(err),
            h('button', { class: 'btn btn--small', type: 'button', onClick: function () { self.finish(); } }, icon('refresh'), t('action.retry'))));
        });
      };
      if (this.s.client_finished) go();
      else ui.confirm(t('trainer.finishEarlyTitle'), t('trainer.finishEarlyText'), t('trainer.finish')).then(function (ok) { if (ok) go(); });
    }

    // ---- итог «было / стало»
    result() {
      var s = this.s, c = s.comparison, p = s.persona;
      if (!c) {
        return ui.msgbar('warning', t('trainer.noResult'), t('trainer.noResultText'),
          h('button', { class: 'btn btn--small', type: 'button', onClick: this.finish.bind(this) }, icon('refresh'), t('trainer.finish')));
      }
      var bt = c.before && c.before.total, at = c.after && c.after.total;
      var groups = {};
      c.criteria.forEach(function (x) { (groups[x.change] = groups[x.change] || []).push(x); });
      var count = function (k) { return (groups[k] || []).length; };
      var focus = s.criteria_focus || [];
      var crit = h('div', { class: 'tr-crits' }, CHANGE.filter(function (k) { return groups[k]; }).map(function (k) {
        return h('div', { class: 'tr-crit-group tr-crit-group--' + k },
          h('h3', { class: 'tr-crit-group__title' }, icon(CHANGE_ICON[k], 'icon--16'), t('trainer.ch.' + k), h('span', { class: 'muted' }, ' · ' + groups[k].length)),
          groups[k].map(function (x) {
            var b = x.before || {}, a = x.after || {};
            var top = x.type === 'scale' ? x.top : null;
            var open = focus.indexOf(x.criterion_id) >= 0 || k === 'worse' || k === 'improved' || (!x.ok_after && k === 'same');
            return h('details', { class: 'tr-crit', open: open },
              h('summary', null,
                icon('chevronRight', 'icon--16 tr-crit__chev'),
                CQA.critLabel(x.criterion_id, x.title),
                focus.indexOf(x.criterion_id) >= 0 ? ui.badge(t('trainer.focusBadge'), 'brand', 'target') : null,
                !x.ok_after && a.verdict && k !== 'worse' ? ui.badge(t('trainer.stillBad'), 'warning', 'warning', { title: t('trainer.stillBadTitle') }) : null,
                h('span', { class: 'spacer' }),
                h('span', { class: 'row row--nowrap tr-crit__vv' },
                  b.verdict ? CQA.verdict(b.verdict, x.type, top, { strike: x.change !== 'same' }) : h('span', { class: 'muted' }, '—'),
                  icon('arrowRight', 'icon--16 muted'),
                  a.verdict ? CQA.verdict(a.verdict, x.type, top, { size: 'l' }) : h('span', { class: 'muted' }, '—'))),
              h('div', { class: 'tr-crit__body' },
                h('div', { class: 'diff-pair' },
                  h('div', { class: 'diff-before' }, h('span', { class: 'diff-tag' }, t('trainer.before') + (b.verdict ? ' · ' + U.verdictLabel(b.verdict, x.type, top) : '')), b.explanation || '—'),
                  h('div', { class: 'diff-after' }, h('span', { class: 'diff-tag' }, t('trainer.after') + (a.verdict ? ' · ' + U.verdictLabel(a.verdict, x.type, top) : '')), a.explanation || '—')),
                !x.ok_after && a.how_to_fix ? h('div', null, h('span', { class: 'crit__label' }, t('crit.howToFix')), h('div', { class: 'quote' }, a.how_to_fix)) : null,
                a.evidence && a.evidence.length && s.uid ? h('div', { class: 'row t-caption1' }, h('span', { class: 'muted' }, t('crit.evidence') + ':'),
                  a.evidence.map(function (e) { return h('a', { class: 'mono', href: CQA.convHref(s.uid, { crit: x.criterion_id, m: e.id }), title: e.quote }, e.id); })) : null));
          }));
      }));
      var hidden = p.hidden_question || (p.reveal_if_asked && p.reveal_if_asked.length);
      // «что дальше»: ещё раз, следующая зона роста оператора, вопрос тьютору
      var nextBox = h('div');
      var again = { label: t('trainer.again'), href: CQA.trainHref(s.base_uid, { focus: s.criteria_focus, operator: s.operator, lesson: s.lesson_id }), icon: 'refresh', primary: true };
      var worstLeft = (c.criteria || []).filter(function (x) { return x.change === 'worse' || x.ok_after === false; })[0];
      var tutorQ = worstLeft ? t('trainer.tutorQ', { id: worstLeft.criterion_id }) : null;
      var renderNext = function (zone) {
        var acts = [again];
        if (zone) acts.push({ label: t('trainer.nextZone', { c: zone.criterion_id + ' ' + CQA.evalUtil.shortTitle(zone.title) }), icon: 'arrowRight',
          href: CQA.trainHref(zone.examples[0].uid, { focus: [zone.criterion_id], operator: s.operator }) });
        if (tutorQ) acts.push({ label: t('trainer.askTutor'), icon: 'book', href: '#/tutor?q=' + encodeURIComponent(tutorQ) });
        CQA.dom.replace(nextBox, ui.nextStep({ icon: 'target', closable: false,
          title: c.delta_total > 0 ? t('trainer.nextTitleUp') : t('trainer.nextTitle'),
          text: zone ? t('trainer.nextText', { c: zone.criterion_id + ' ' + zone.title }) : t('trainer.nextTextNoZone'),
          actions: acts }));
      };
      renderNext(null);
      if (s.operator) {
        CQA.api.operator(s.operator).then(function (op) {
          var focus = s.criteria_focus || [];
          var zone = (op.growth_zones || []).filter(function (z) { return focus.indexOf(z.criterion_id) < 0 && z.examples && z.examples.length; })[0];
          if (zone) renderNext(zone);
        }, function () { /* без профиля — без следующей зоны */ });
      }
      return h('div', { class: 'stack stack--l tr-result' },
        nextBox,
        h('section', { class: 'card tr-score', 'aria-labelledby': 'tr-score-title' },
          h('h2', { class: 'sr-only', id: 'tr-score-title' }, t('trainer.resultTitle')),
          h('div', { class: 'tr-score__pair' },
            h('div', { class: 'tr-score__side' }, h('span', { class: 'muted' }, t('trainer.wasOriginal')), CQA.score(bt, null, { size: 'xl', max: true })),
            h('span', { class: 'tr-score__arrow', 'aria-hidden': 'true' }, icon('arrowRight', 'icon--24')),
            h('div', { class: 'tr-score__side' }, h('span', { class: 'muted' }, t('trainer.nowTraining')), CQA.score(at, null, { size: 'xl', max: true })),
            h('div', { class: 'tr-score__delta' }, CQA.delta(c.delta_total, { big: true }), h('span', { class: 'muted t-caption1' }, t('trainer.points')))),
          h('div', { class: 'tr-score__chart' }, CQA.dumbbell(bt, at)),
          h('div', { class: 'row tr-score__counts' },
            ui.badge(t('trainer.nImproved', { n: count('improved') }), 'success', 'arrowUp'),
            ui.badge(t('trainer.nSame', { n: count('same') }), 'neutral'),
            ui.badge(t('trainer.nWorse', { n: count('worse') }), count('worse') ? 'danger' : 'neutral', 'arrowDown'),
            c.after && c.after.gross ? ui.badge(t('badge.gross'), 'danger-filled', 'warning') : null),
          c.summary ? h('p', { class: 'subtle' }, String(c.summary).replace(/(\d)\.(\d)/g, '$1,$2')) : null,
          h('div', { class: 'row t-caption1' },
            s.uid ? h('a', { class: 'btn btn--small', href: CQA.convHref(s.uid) }, icon('checklist'), t('trainer.openEval')) : null,
            s.result_evaluation_id ? h('a', { class: 'btn btn--small btn--subtle', href: '#/log/' + s.result_evaluation_id }, icon('code'), t('eval.log')) : null,
            h('a', { class: 'btn btn--small btn--subtle', href: CQA.convHref(s.base_uid) }, icon('chat'), t('trainer.openBase')),
            c.after && c.after.profile ? h('span', { class: 'muted' }, t('trainer.sameJudge', { p: c.after.profile })) : null)),
        hidden ? h('section', { class: 'card tr-reveal', 'aria-labelledby': 'tr-reveal-title' },
          h('h2', { class: 'card__title row', id: 'tr-reveal-title' }, icon('bulb'), t('trainer.revealTitle')),
          p.hidden_question ? h('div', { class: 'quote quote--brand' }, p.hidden_question) : null,
          p.hidden_question ? h('p', { class: 'muted t-caption1' }, t('trainer.revealNote')) : null,
          p.reveal_if_asked && p.reveal_if_asked.length ? h('div', null, h('span', { class: 'crit__label' }, t('trainer.revealAsked')),
            h('ul', { class: 'tr-list' }, p.reveal_if_asked.map(function (x) { return h('li', null, x); }))) : null) : null,
        h('section', { class: 'card', 'aria-labelledby': 'tr-crit-title' },
          h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'tr-crit-title' }, t('trainer.byCriteria')),
            h('span', { class: 'muted t-caption1' }, t('trainer.byCriteriaSub'))),
          crit));
    }
  }

  CQA.openTrainerPick = pickDialog;
  customElements.define('cqa-page-trainer', CqaPageTrainer);
  customElements.define('cqa-page-trainer-new', CqaPageTrainerNew);
  customElements.define('cqa-page-trainer-session', CqaPageTrainerSession);
})(window.CQA = window.CQA || {});
