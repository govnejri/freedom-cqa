/* <cqa-page-conversation> — карточка диалога #/c/{uid}?crit=08&m=m10.
   Сверху компактная полоса: ID, канал, язык, оператор; итог крупно; грубое нарушение красной плашкой над баллом;
   «где потеряны баллы» чипами. Ниже две зоны: переписка слева, критерии справа (первое нарушение раскрыто).
   Служебное — «Детали оценки» (выезжающая панель). На узком экране — вкладки «Переписка / Оценка». */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var NARROW = '(max-width: 1100px)';
  var unansCache = {};

  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }
  function same(a, b) { return String(a || '').toLocaleLowerCase('ru') === String(b || '').toLocaleLowerCase('ru'); }

  class CqaPageConversation extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.uid = this.params.uid;
      this.selectedMids = new Set();
      this.appealMode = false;
      this.mq = window.matchMedia(NARROW);
      this.from = CQA.router.previous;
      this.buildLayout();
      this.bindEvents();
      this.load();
    }

    update(params, query) {
      if (params.uid !== this.uid) return false;
      this.query = query;
      if (this.ev || this.conv) this.applyQuery(false);
      return true;
    }

    // ------------------------------------------------------------ каркас
    buildLayout() {
      var self = this;
      document.title = fmt.externalId(this.uid) + ' · CQA';
      this.top = h('section', { class: 'conv-top', 'aria-label': t('conv.summaryAria') }, h('span', { class: 'skeleton skeleton--title' }), h('span', { class: 'skeleton skeleton--text', style: { width: '60%' } }));
      this.tabT = h('button', { class: 'tab', role: 'tab', id: 'tab-transcript', 'aria-controls': 'pane-transcript', type: 'button' }, icon('chat'), t('conv.tabTranscript'));
      this.tabE = h('button', { class: 'tab', role: 'tab', id: 'tab-eval', 'aria-controls': 'pane-eval', type: 'button' }, icon('checklist'), t('conv.tabEval'));
      this.tabs = h('div', { class: 'tabs conv-tabs', role: 'tablist', 'aria-label': t('conv.tabsAria') }, this.tabT, this.tabE);
      this.tabT.addEventListener('click', function () { self.setTab('transcript', true); });
      this.tabE.addEventListener('click', function () { self.setTab('eval', true); });
      this.tabs.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { self.setTab(self.tab === 'eval' ? 'transcript' : 'eval', true); e.preventDefault(); }
      });
      this.trHead = h('div', { class: 'pane-head' }, h('h2', { class: 'panel__title' }, t('conv.transcript')));
      this.trScroll = h('div', { class: 'pane-scroll' }, ui.skeleton('transcript', 6));
      this.transcriptPane = h('section', { class: 'conv-pane conv-pane--transcript', id: 'pane-transcript', role: 'tabpanel', 'aria-labelledby': 'tab-transcript', tabindex: '-1' },
        this.trHead, this.trScroll);
      this.evalPane = h('section', { class: 'conv-pane conv-pane--eval', id: 'pane-eval', role: 'tabpanel', 'aria-labelledby': 'tab-eval', tabindex: '-1' },
        ui.skeleton('criteria', 4));
      this.body = h('div', { class: 'conv-body' }, this.transcriptPane, this.evalPane);
      this.textContent = '';
      this.appendChild(h('div', { class: 'page page--full conv-page' }, this.top, this.tabs, this.body));
      this.setTab(this.query && this.query.m ? 'transcript' : 'eval', false);
    }

    setTab(tab, focus) {
      this.tab = tab;
      this.body.setAttribute('data-tab', tab);
      this.tabT.setAttribute('aria-selected', String(tab === 'transcript'));
      this.tabE.setAttribute('aria-selected', String(tab === 'eval'));
      this.tabT.tabIndex = tab === 'transcript' ? 0 : -1;
      this.tabE.tabIndex = tab === 'eval' ? 0 : -1;
      if (focus) (tab === 'eval' ? this.tabE : this.tabT).focus();
    }

    bindEvents() {
      var self = this;
      this.addEventListener('cqa-criterion-select', function (e) {
        if (e.detail.keepForm) { self.selectCriterion(e.detail.cid, { scroll: false, url: false }); return; }
        var closing = e.detail.toggle && self.selectedCid === e.detail.cid;
        if (closing) { self.selectCriterion(null, { url: true }); return; }
        self.selectCriterion(e.detail.cid, { scroll: !!e.detail.scroll, url: true });
      });
      this.addEventListener('cqa-evidence', function (e) {
        self.selectCriterion(e.detail.cid, { scroll: false, url: false, reveal: false });
        self.goToMessage(e.detail.mid, { play: true });
        self.setUrl({ crit: e.detail.cid, m: e.detail.mid });
      });
      this.addEventListener('cqa-range', function (e) {
        self.selectCriterion(e.detail.cid, { scroll: false, url: true, reveal: false });
        self.goToMessage(e.detail.range[0], { play: false });
      });
      this.addEventListener('cqa-appeal', function (e) { self.openAppeal(e.detail.cid); });
      this.addEventListener('cqa-seek', function (e) { if (self.player) self.player.seek(e.detail.sec, e.detail.play); });
      this.addEventListener('cqa-time', function (e) { if (self.transcript) self.transcript.setPlaying(e.detail.sec); });
      this.addEventListener('cqa-toggle-message', function () { if (self.form) self.form.syncSelection(); });
      this.addEventListener('cqa-appeal-unselect', function (e) { if (self.transcript) self.transcript.toggle(e.detail.mid); });
      this.addEventListener('cqa-appeal-pick', function () { self.setTab('transcript', true); });
      this.addEventListener('cqa-appeal-cancel', function () { self.closeAppeal(); });
      this.addEventListener('cqa-appeal-created', function (e) {
        var a = e.detail.appeal;
        ui.toast(t('appeal.created', { id: a.id }), { tone: 'success', detail: t('appeal.createdDetail'), action: { label: t('appeal.open'), href: '#/appeals/' + a.id } });
        document.dispatchEvent(new CustomEvent('cqa-appeals-changed'));
        self.closeAppeal();
        self.reloadEvaluation();
      });
    }

    // ------------------------------------------------------------ загрузка
    load() {
      var self = this;
      var api = CQA.api;
      CQA.dom.replace(this.trScroll, ui.skeleton('transcript', 6));
      CQA.dom.replace(this.evalPane, ui.skeleton('criteria', 4));
      return Promise.all([
        api.conversation(this.uid),
        api.evaluation(this.uid).catch(function (e) { if (e.status === 404) return null; throw e; })
      ]).then(function (res) {
        self.conv = res[0];
        self.ev = res[1];
        return self.loadExtras();
      }).then(function () {
        self.render();
        self.loadTags();
      }).catch(function (err) {
        console.warn('[CQA]', err);
        CQA.dom.replace(self.top, self.backLink());
        var title = err.status === 404 ? t('conv.notFound') : t('state.errorTitle');
        self.body.classList.add('is-error');
        self.body.parentNode.classList.add('is-error');
        CQA.dom.clear(self.evalPane);
        CQA.dom.clear(self.trHead);
        CQA.dom.replace(self.trScroll, ui.error(err, function () {
          self.body.classList.remove('is-error'); self.body.parentNode.classList.remove('is-error'); self.load();
        }, title));
        self.setTab('transcript', false);
      });
    }

    /** Верхние ступени шкал (scale_top из оценки; для старых данных — из версии чек-листа) и решения по апелляциям. */
    loadExtras() {
      var self = this, ev = this.ev;
      this.tops = {};
      this.appealsById = {};
      if (!ev) return Promise.resolve();
      var needConfig = false;
      ev.criteria.forEach(function (c) {
        if (c.scale_top != null) self.tops[c.criterion_id] = c.scale_top;
        else if (c.type === 'scale') needConfig = true;
      });
      var ids = ev.criteria.filter(function (c) { return c.appeal_id; }).map(function (c) { return c.appeal_id; });
      return Promise.all([
        needConfig ? CQA.api.checklistVersion(ev.checklist_version_id).then(function (v) {
          (v.config.criteria || []).forEach(function (c) { var tp = CQA.scoring.top(c); if (tp != null && self.tops[c.id] == null) self.tops[c.id] = tp; });
        }).catch(function () { /* без конфига: ступень покажем числом */ }) : null,
        Promise.all(ids.map(function (id) { return CQA.api.appeal(id).then(function (a) { self.appealsById[id] = a; }).catch(function () {}); }))
      ]);
    }

    /** Вопросы клиента без ответа — пометкой прямо в переписке (сигналы руководителя). */
    loadTags() {
      var self = this, g = this.conv && this.conv.group_id;
      if (!g || CQA.isTrainerUid(this.uid)) return;
      var p = unansCache[g] || (unansCache[g] = CQA.api.unanswered(g).catch(function () { unansCache[g] = null; return null; }));
      p.then(function (d) {
        if (!d || !self.transcript) return;
        var map = {};
        (d.topics || []).forEach(function (tp) {
          tp.questions.forEach(function (q) {
            if (q.uid !== self.uid) return;
            (map[q.mid] = map[q.mid] || []).push({ label: q.status === 'частично' ? t('conv.partlyAnswered') : t('conv.unanswered'), tone: 'warning', tip: t('conv.unansweredTip', { q: q.text, topic: tp.topic }) });
          });
        });
        self.transcript.tags(map);
      });
    }

    reloadEvaluation() {
      var self = this;
      return CQA.api.evaluation(this.uid).then(function (ev) {
        self.ev = ev;
        return self.loadExtras();
      }).then(function () { self.renderTop(); self.renderEval(); self.applyQuery(false); })
        .catch(function (err) { ui.toast(err.message, { tone: 'error' }); });
    }

    // ------------------------------------------------------------ отрисовка
    backLink() {
      var prev = this.from, nav = prev && prev.nav && CQA.navItems[prev.nav];
      if (prev && nav && !/^\/c\//.test(prev.path)) {
        return h('a', { class: 'back-link', href: CQA.router.href(prev.path, prev.query) }, icon('arrowLeft', 'icon--16'), t(nav.label));
      }
      return h('a', { class: 'back-link', href: '#/conversations' }, icon('arrowLeft', 'icon--16'), t('nav.queue'));
    }

    render() {
      var self = this, c = this.conv;
      document.title = c.external_id + ' · CQA';
      var isCall = c.channel === 'voice call';
      var lastAudio = 0;
      c.messages.forEach(function (m) { if (m.audio_sec != null) lastAudio = Math.max(lastAudio, m.audio_sec); });
      this.lastAudio = lastAudio;
      this.msgMap = {};
      c.messages.forEach(function (m) { self.msgMap[m.mid] = m; });
      this.renderTop();

      // переписка
      // у звонка длительность показывает плеер (по mp3) — не дублируем её числом из реплик
      var dur = isCall ? null : lastTime(c.messages);
      this.selLabel = h('span', { class: 'pane-head__sel', hidden: true });
      CQA.dom.replace(this.trHead,
        h('h2', { class: 'panel__title' }, t('conv.transcript')),
        h('span', { class: 'panel__meta' }, [t('conv.messages', { n: c.messages.length }), dur].filter(Boolean).join(' · ')),
        h('span', { class: 'spacer' }),
        this.selLabel);
      var pane = this.trScroll;
      pane.textContent = '';
      if (isCall) {
        this.player = document.createElement('cqa-player');
        pane.appendChild(h('div', { class: 'conv-player' }, this.player));
        this.player.setSource(CQA.api.audioSrc(c), lastAudio + 5);
        this.setHoldRegion();
      } else {
        this.player = null;
      }
      this.selectBar = h('div', { class: 'select-bar', hidden: true, role: 'status' });
      pane.appendChild(this.selectBar);
      this.transcript = document.createElement('cqa-transcript');
      this.transcript.data = { messages: c.messages, channel: c.channel };
      pane.appendChild(this.transcript);

      this.renderEval();
      this.setTab(this.ev && !(this.query && this.query.m) ? 'eval' : 'transcript', false);
      this.applyQuery(true);
    }

    /** Полоса сверху: кто/что, итог, грубое нарушение, где потеряны баллы, действия. */
    renderTop() {
      var self = this, c = this.conv, ev = this.ev;
      var isTrainer = CQA.isTrainerUid(c.uid);
      var E = CQA.evalUtil;
      var meta = h('div', { class: 'conv-top__meta' },
        h('span', { class: 'conv-meta' }, icon(U.channelIcon(c.channel), 'icon--16'), U.channelLabel(c.channel)),
        h('span', { class: 'conv-meta' }, icon('globe', 'icon--16'), c.language),
        c.operator_name ? h('a', { class: 'conv-meta conv-meta--link', href: CQA.operatorHref(c.operator_name), 'data-tip': t('conv.operatorProfile') },
          icon('headset', 'icon--16'), c.operator_name) : h('span', { class: 'conv-meta muted' }, icon('headset', 'icon--16'), t('conv.noOperator')),
        isTrainer ? ui.badge(t('conv.trainerDialog'), 'brand', 'target') : null,
        ev && ev.needs_review ? ui.badge(t('badge.review'), 'warning', 'eye', { 'data-tip': t('badge.reviewTitle') }) : null);

      var detailsBtn = ev ? h('button', { class: 'btn', type: 'button', onClick: function () { CQA.evalDetails(self.ev, self.tops); } }, icon('panel'), t('conv.details')) : null;
      var reBtn = !isTrainer ? h('button', { class: 'btn btn--subtle', type: 'button', 'data-tip': t('conv.reevaluateTip'), onClick: function () { self.reevaluate(this); } },
        icon('refresh'), ev ? t('conv.reevaluate') : t('conv.evaluate')) : null;

      var head = h('div', { class: 'conv-top__head' },
        h('div', { class: 'conv-top__id' },
          this.backLink(),
          h('div', { class: 'row row--nowrap conv-top__title' }, h('h1', null, c.external_id), meta)),
        h('div', { class: 'conv-top__actions' }, reBtn, detailsBtn));

      if (!ev) { CQA.dom.replace(this.top, head); return; }

      // итог: при грубом нарушении — красная плашка над приглушённым баллом
      var finalTotal = ev.final_total != null ? ev.final_total : ev.total;
      var differs = ev.final_total != null && ev.total != null && Math.abs(ev.final_total - ev.total) >= 0.005;
      var score = CQA.score(finalTotal, differs ? ev.total : null, { size: 'xl', max: true, muted: ev.gross });
      var cats = (ev.gross_categories || []).map(cap).join(', ');
      var scoreBox = h('div', { class: 'conv-score' + (ev.gross ? ' conv-score--gross' : '') },
        ev.gross ? h('div', { class: 'gross-plaque', role: 'alert' }, icon('warning'), h('span', null, t('conv.grossPlaque', { c: cats || t('badge.gross') }))) : null,
        h('div', { class: 'conv-score__row' },
          finalTotal == null ? h('span', { class: 'muted' }, t('eval.noTotal')) : score,
          h('div', { class: 'conv-score__note' },
            ev.gross ? [ui.term(t('conv.grossScoreNote'), 'term.grossScore')]
              : differs ? t('conv.changedByAppeal', { o: fmt.score(ev.total) })
              : h('span', null, t('conv.scoreOf100')))));

      // где потеряны баллы
      var lost = E.losses(ev);
      var chips = lost.map(function (cr) {
        var gross = E.isGross(cr);
        return h('button', { class: 'lost-chip' + (gross ? ' lost-chip--gross' : ''), type: 'button', 'data-tip': cr.title,
          onClick: function () { self.selectCriterion(cr.criterion_id, { scroll: true, url: true }); if (self.mq.matches) self.setTab('eval', false); } },
          h('span', { class: 'lost-chip__id' }, cr.criterion_id),
          h('span', { class: 'lost-chip__name' }, gross ? cap(cr.category) || t('badge.gross') : E.shortTitle(cr.title)),
          gross ? null : h('span', { class: 'lost-chip__pts' }, '−' + fmt.score(E.lossOf(cr))));
      });
      var lostBox = h('div', { class: 'conv-lost' },
        h('div', { class: 'conv-lost__label' }, t('conv.whereLost')),
        chips.length ? h('div', { class: 'conv-lost__chips' }, chips)
          : h('div', { class: 'conv-lost__none' }, icon('checkCircle', 'icon--16'), t('conv.nothingLost')));

      CQA.dom.replace(this.top, head, h('div', { class: 'conv-top__sum' }, scoreBox, lostBox));
    }

    setHoldRegion() {
      var f = this.conv.facts, by = {};
      this.conv.messages.forEach(function (m) { by[m.mid] = m; });
      var regions = [];
      if (f && f.hold_range && by[f.hold_range[0]] && by[f.hold_range[1]]) {
        regions.push({ from: by[f.hold_range[0]].audio_sec, to: by[f.hold_range[1]].audio_sec, kind: 'hold', label: t('segment.hold') });
      }
      this.player.setRegions(regions);
    }

    renderEval() {
      var self = this;
      this.form = null;
      this.evalPane.textContent = '';
      if (!this.ev) {
        this.evalEl = null;
        this.evalPane.appendChild(ui.empty(t('conv.notEvaluated'), t('conv.notEvaluatedText'),
          h('button', { class: 'btn btn--primary', type: 'button', onClick: function () { self.reevaluate(this); } }, icon('flash'), t('conv.evaluate')), 'checklist'));
        return;
      }
      var isTrainer = CQA.isTrainerUid(this.uid);
      var op = this.conv && this.conv.operator_name;
      var me = CQA.me.get();
      if (this.selectedCid === undefined) {
        var q = this.query || {};
        this.selectedCid = q.crit || CQA.evalUtil.firstViolation(this.ev, this.tops);
      }
      this.evalEl = document.createElement('cqa-evaluation');
      this.evalEl.data = { evaluation: this.ev, tops: this.tops, appeals: this.appealsById, selected: this.selectedCid,
        trainUid: isTrainer ? null : this.uid, trainOperator: op || me || null, trainSelf: !op || (me && same(me, op)),
        canAppeal: isTrainer ? false : undefined, canEdit: !isTrainer, messages: this.msgMap };
      this.evalPane.appendChild(this.evalEl);
    }

    // ------------------------------------------------------------ навигация по доказательствам
    applyQuery(initial) {
      var q = this.query || {};
      var cid = q.crit || (initial ? this.selectedCid : null);
      if (cid && this.ev) this.selectCriterion(cid, { scroll: !!q.crit, url: false, reveal: !q.m });
      if (q.m) this.goToMessage(q.m, { play: true, quiet: !initial });
    }

    setUrl(patch) {
      var q = Object.assign({}, this.query || {}, patch);
      Object.keys(q).forEach(function (k) { if (q[k] == null) delete q[k]; });
      this.query = q;
      CQA.router.setQuery(q);
    }

    selectCriterion(cid, opts) {
      opts = opts || {};
      if (!this.ev) return;
      var self = this;
      if (cid == null) {
        this.selectedCid = null;
        if (this.evalEl) this.evalEl.select(null);
        if (this.transcript) this.transcript.highlight({});
        if (this.player) this.player.setMarkers([]);
        this.selLabel.hidden = true;
        if (opts.url) this.setUrl({ crit: null, m: null });
        return;
      }
      var cr = this.ev.criteria.filter(function (c) { return c.criterion_id === cid; })[0];
      if (!cr) return;
      this.selectedCid = cid;
      if (this.evalEl) this.evalEl.select(cid, opts.scroll);
      var E = CQA.evalUtil;
      var verdict = E.vOf(cr);
      var tone = E.isGross(cr) ? 'gross' : U.verdictTone(verdict, cr.type, this.tops[cid]);
      var evs = (cr.evidence || []).map(function (e) { return { id: e.id, quote: e.quote }; });
      var mids = evs.map(function (e) { return e.id; });
      var label = cr.criterion_id + ' ' + E.shortTitle(cr.title);
      if (this.transcript) {
        this.transcript.highlight({ evidence: evs, range: cr.checked_range, tone: tone, label: label });
        var first = mids[0] || (cr.checked_range && cr.checked_range[0]);
        if (opts.reveal !== false && first && !(this.mq.matches && this.tab !== 'transcript')) this.transcript.reveal(first);
      }
      // «Подсвечено: 05 Эмпатия ×» над перепиской
      if (mids.length) {
        CQA.dom.replace(this.selLabel, h('span', { class: 'sel-dot sel-dot--' + tone }), t('conv.highlighted', { c: label }),
          h('button', { class: 'btn btn--subtle btn--icon btn--small', type: 'button', 'aria-label': t('conv.clearHighlight'), onClick: function () { self.selectCriterion(null, { url: true }); } }, icon('close', 'icon--16')));
        this.selLabel.hidden = false;
      } else this.selLabel.hidden = true;
      if (this.player) {
        var tr = this.transcript;
        this.player.setMarkers(mids.map(function (mid) {
          var m = tr.message(mid);
          return m && m.audio_sec != null ? { sec: m.audio_sec, tone: tone === 'gross' ? 'danger' : tone, label: mid + ' · ' + fmt.clock(m.audio_sec) } : null;
        }).filter(Boolean));
      }
      if (opts.url) this.setUrl({ crit: cid, m: null });
    }

    goToMessage(mid, opts) {
      opts = opts || {};
      if (!this.transcript) return;
      var m = this.transcript.message(mid);
      if (!m) { ui.toast(t('conv.messageMissing', { mid: mid }), { tone: 'warning' }); return; }
      if (this.mq.matches && this.tab !== 'transcript') this.setTab('transcript', false);
      var self = this;
      // вкладка могла только что стать видимой — прокручиваем в следующем кадре
      requestAnimationFrame(function () { self.transcript.focusMessage(mid, { focus: !opts.quiet }); });
      if (this.player) {
        if (m.audio_sec != null && m.recorded !== false) {
          this.player.seek(m.audio_sec, opts.play);
        } else {
          ui.toast(t('conv.offRecord', { mid: mid }), { tone: 'warning', detail: t('conv.offRecordDetail') });
        }
      }
    }

    // ------------------------------------------------------------ апелляция
    openAppeal(cid) {
      var self = this;
      this.appealMode = true;
      this.selectedMids = new Set();
      this.transcript.setSelectable(true, this.selectedMids);
      this.selectBar.hidden = false;
      CQA.dom.replace(this.selectBar, icon('cursor'), h('span', null, t('appeal.selectBar')),
        h('button', { class: 'btn btn--small conv-select-done', type: 'button', onClick: function () { self.setTab('eval', true); } }, t('appeal.selectDone')));
      this.form = document.createElement('cqa-appeal-form');
      this.form.data = { evaluation: this.ev, criterionId: cid, tops: this.tops, selected: this.selectedMids };
      this.evalPane.textContent = '';
      this.evalPane.appendChild(this.form);
      this.evalPane.scrollTop = 0;
      this.setTab('eval', false);
      this.selectCriterion(cid, { url: true });
    }

    closeAppeal() {
      this.appealMode = false;
      if (this.transcript) this.transcript.setSelectable(false);
      if (this.selectBar) this.selectBar.hidden = true;
      this.renderEval();
      if (this.selectedCid) this.selectCriterion(this.selectedCid, { scroll: true });
    }

    reevaluate(btn) {
      var self = this;
      ui.confirm(t('conv.reevaluateConfirm'), t('conv.reevaluateText'), t('conv.reevaluate')).then(function (ok) {
        if (!ok) return;
        if (btn) btn.disabled = true;
        CQA.api.createRun({ uids: [self.uid], kind: 'single' }).then(function (run) {
          CQA.router.go('/runs/' + run.id, { back: self.uid });
        }, function (err) {
          if (btn) btn.disabled = false;
          ui.toast(t('run.createFailed'), { tone: 'error', detail: err.message });
        });
      });
    }
  }

  /** Длительность чата по меткам времени «03:41». */
  function lastTime(msgs) {
    for (var i = msgs.length - 1; i >= 0; i--) {
      var s = fmt.ts(msgs[i].ts_raw);
      if (/^\d{1,2}:\d{2}$/.test(s)) return s;
    }
    return null;
  }

  customElements.define('cqa-page-conversation', CqaPageConversation);
})(window.CQA = window.CQA || {});
