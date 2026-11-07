/* <cqa-page-conversation> — карточка диалога #/c/{uid}?crit=08&m=m10.
   Слева транскрипт (+ плеер для звонка), справа оценка или форма апелляции. На узком экране — вкладки. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var NARROW = '(max-width: 1023px)';

  class CqaPageConversation extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.uid = this.params.uid;
      this.selectedMids = new Set();
      this.appealMode = false;
      this.mq = window.matchMedia(NARROW);
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
      this.header = h('div', { class: 'conv-header' }, h('span', { class: 'skeleton skeleton--title' }));
      this.tabT = h('button', { class: 'tab', role: 'tab', id: 'tab-transcript', 'aria-controls': 'pane-transcript', type: 'button' }, icon('chat'), t('conv.tabTranscript'));
      this.tabE = h('button', { class: 'tab', role: 'tab', id: 'tab-eval', 'aria-controls': 'pane-eval', type: 'button' }, icon('checklist'), t('conv.tabEval'));
      this.tabs = h('div', { class: 'tabs conv-tabs', role: 'tablist', 'aria-label': t('conv.tabsAria') }, this.tabT, this.tabE);
      this.tabT.addEventListener('click', function () { self.setTab('transcript', true); });
      this.tabE.addEventListener('click', function () { self.setTab('eval', true); });
      this.tabs.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { self.setTab(self.tab === 'eval' ? 'transcript' : 'eval', true); e.preventDefault(); }
      });
      this.transcriptPane = h('div', { class: 'conv-pane conv-pane--transcript', id: 'pane-transcript', role: 'tabpanel', 'aria-labelledby': 'tab-transcript', tabindex: '-1' },
        ui.skeleton('transcript', 6));
      this.evalPane = h('div', { class: 'conv-pane conv-pane--eval', id: 'pane-eval', role: 'tabpanel', 'aria-labelledby': 'tab-eval', tabindex: '-1' },
        ui.skeleton('criteria', 4));
      this.body = h('div', { class: 'conv-body' }, this.transcriptPane, this.evalPane);
      this.textContent = '';
      this.appendChild(h('div', { class: 'page page--full conv-page' }, this.header, this.tabs, this.body));
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
      this.addEventListener('cqa-criterion-select', function (e) { self.selectCriterion(e.detail.cid, { scroll: !!e.detail.scroll, url: true }); });
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
        ui.toast(t('appeal.created', { id: a.id }), { tone: 'success', action: { label: t('appeal.open'), href: '#/appeals/' + a.id } });
        document.dispatchEvent(new CustomEvent('cqa-appeals-changed'));
        self.closeAppeal();
        self.reloadEvaluation();
      });
    }

    // ------------------------------------------------------------ загрузка
    load() {
      var self = this;
      var api = CQA.api;
      CQA.dom.replace(this.transcriptPane, ui.skeleton('transcript', 6));
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
      }).catch(function (err) {
        console.warn('[CQA]', err);
        CQA.dom.replace(self.header, h('a', { class: 'back-link', href: '#/conversations' }, icon('arrowLeft', 'icon--16'), t('nav.conversations')));
        var title = err.status === 404 ? t('conv.notFound') : t('state.errorTitle');
        self.body.classList.add('is-error');
        self.body.parentNode.classList.add('is-error');
        CQA.dom.replace(self.transcriptPane, ui.error(err, function () {
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

    reloadEvaluation() {
      var self = this;
      return CQA.api.evaluation(this.uid).then(function (ev) {
        self.ev = ev;
        return self.loadExtras();
      }).then(function () { self.renderEval(); self.applyQuery(false); })
        .catch(function (err) { ui.toast(err.message, { tone: 'error' }); });
    }

    // ------------------------------------------------------------ отрисовка
    render() {
      var self = this, c = this.conv;
      document.title = c.external_id + ' · CQA';
      var isCall = c.channel === 'voice call';
      var lastAudio = 0;
      c.messages.forEach(function (m) { if (m.audio_sec != null) lastAudio = Math.max(lastAudio, m.audio_sec); });

      var actions = h('div', { class: 'row' },
        h('button', { class: 'btn', type: 'button', onClick: function () { self.reevaluate(this); } },
          icon('flash'), this.ev ? t('conv.reevaluate') : t('conv.evaluate')));
      CQA.dom.replace(this.header,
        h('div', { class: 'conv-header__main' },
          h('a', { class: 'back-link', href: '#/conversations' }, icon('arrowLeft', 'icon--16'), t('nav.conversations')),
          h('div', { class: 'row' },
            h('h1', { class: 't-subtitle1 conv-title' }, c.external_id),
            ui.badge(c.language, 'neutral', 'globe'),
            ui.badge(U.channelLabel(c.channel), 'neutral', U.channelIcon(c.channel)),
            c.operator_name ? ui.badge(c.operator_name, 'neutral', 'headset') : null,
            h('span', { class: 'muted t-caption1' }, t('conv.messages', { n: c.messages.length }) + (isCall && lastAudio ? ' · ' + t('conv.recording', { d: fmt.clock(lastAudio) }) : '') + ' · ' + c.group_id))),
        actions);

      // транскрипт
      var pane = this.transcriptPane;
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
      this.evalEl = document.createElement('cqa-evaluation');
      this.evalEl.data = { evaluation: this.ev, tops: this.tops, appeals: this.appealsById, selected: this.selectedCid };
      this.evalPane.appendChild(this.evalEl);
    }

    // ------------------------------------------------------------ навигация по доказательствам
    applyQuery(initial) {
      var q = this.query || {};
      if (q.crit && this.ev) this.selectCriterion(q.crit, { scroll: true, url: false, reveal: !q.m });
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
      var cr = this.ev.criteria.filter(function (c) { return c.criterion_id === cid; })[0];
      if (!cr) return;
      this.selectedCid = cid;
      if (this.evalEl) this.evalEl.select(cid, opts.scroll);
      var verdict = cr.final_verdict || cr.verdict;
      var tone = U.verdictTone(verdict, cr.type, this.tops[cid]);
      var mids = (cr.evidence || []).map(function (e) { return e.id; });
      if (this.transcript) {
        this.transcript.highlight({ evidence: mids, range: cr.checked_range, tone: tone, label: t('conv.evidenceOf', { id: cid }) });
        var first = mids[0] || (cr.checked_range && cr.checked_range[0]);
        if (opts.reveal !== false && first && !(this.mq.matches && this.tab !== 'transcript')) this.transcript.reveal(first);
      }
      if (this.player) {
        var tr = this.transcript;
        this.player.setMarkers(mids.map(function (mid) {
          var m = tr.message(mid);
          return m && m.audio_sec != null ? { sec: m.audio_sec, tone: tone, label: mid + ' · ' + fmt.clock(m.audio_sec) } : null;
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
      if (btn) btn.disabled = true;
      CQA.api.createRun({ uids: [this.uid], kind: 'single' }).then(function (run) {
        CQA.router.go('/runs/' + run.id);
      }, function (err) {
        if (btn) btn.disabled = false;
        ui.toast(t('run.createFailed'), { tone: 'error', detail: err.message });
      });
    }
  }

  customElements.define('cqa-page-conversation', CqaPageConversation);
})(window.CQA = window.CQA || {});
