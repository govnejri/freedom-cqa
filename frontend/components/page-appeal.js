/* <cqa-page-appeal> — экран проверяющего #/appeals/{id}: спорная оценка, комментарий, фрагмент диалога, мнение ИИ,
   решение «подтвердить» / «изменить» (новый вердикт из допустимых) с причиной → POST /appeals/{id}/decision. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  /** Конфиг для предпросмотра итога из самой оценки (веса, типы, scale_top) — без запроса версии чек-листа.
      null, если у шкал нет scale_top (старые данные). */
  function configFromEval(ev) {
    if (!ev || !ev.criteria) return null;
    var ok = true;
    var criteria = ev.criteria.map(function (c) {
      if (c.type === 'scale' && c.scale_top == null) ok = false;
      var allowed = c.allowed_verdicts || [];
      return {
        id: c.criterion_id, title: c.title, section: c.section, type: c.type, weight: c.weight, enabled: true,
        na_allowed: allowed.indexOf('N/A') >= 0,
        scale: c.type === 'scale' ? allowed.filter(function (v) { return v !== 'N/A'; }).map(function (v) { return { value: Number(v), text: v }; }) : null
      };
    });
    if (!ok) return null;
    criteria.forEach(function (c, i) { if (c.type === 'scale' && !(c.scale && c.scale.length)) c.scale = [{ value: ev.criteria[i].scale_top, text: '' }]; });
    var sections = (ev.sections || []).map(function (s) { return { id: s.section_id, title: s.title }; });
    return { criteria: criteria, sections: sections };
  }

  class CqaPageAppeal extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.aid = this.params.id;
      document.title = t('appeal.pageTitle', { id: this.aid }) + ' · CQA';
      this.root = h('div', { class: 'page appeal-page' });
      this.textContent = '';
      this.appendChild(this.root);
      var self = this;
      this.addEventListener('cqa-evidence', function (e) { self.goTo(e.detail.mid); });
      this.addEventListener('cqa-range', function (e) { self.goTo(e.detail.range[0]); });
      // во фрагменте нет плеера — «▶» у реплики звонка открывает карточку на этой секунде
      this.addEventListener('cqa-seek', function (e) {
        location.hash = '#/c/' + encodeURIComponent(self.appeal.uid) + '?crit=' + encodeURIComponent(self.appeal.criterion_id) + '&m=' + encodeURIComponent(e.detail.mid);
      });
      this.load();
    }

    load() {
      var self = this;
      CQA.dom.replace(this.root, ui.skeleton('card', 6));
      CQA.api.appeal(this.aid).then(function (a) {
        self.appeal = a;
        return CQA.api.evaluationById(a.evaluation_id).then(function (ev) {
          self.ev = ev;
          self.config = configFromEval(ev);
          if (self.config) return null;
          // старые данные без scale_top — берём ступени из версии чек-листа
          return CQA.api.checklistVersion(ev.checklist_version_id).then(function (v) { self.config = v.config; }, function () { self.config = null; });
        }, function () { self.ev = null; self.config = null; });
      }).then(function () { self.render(); }, function (err) {
        CQA.dom.replace(self.root,
          h('a', { class: 'back-link', href: '#/appeals' }, icon('arrowLeft', 'icon--16'), t('nav.appeals')),
          ui.error(err, function () { self.load(); }, err.status === 404 ? t('appeal.notFound') : null));
      });
    }

    critConfig() {
      var cid = this.appeal.criterion_id;
      return this.config ? (this.config.criteria || []).filter(function (c) { return c.id === cid; })[0] : null;
    }

    top() {
      var o = this.appeal.original || {};
      if (o.scale_top != null) return o.scale_top;
      var c = this.critConfig();
      return c ? CQA.scoring.top(c) : null;
    }

    allowed() {
      var o = this.appeal.original || {};
      if (o.allowed_verdicts && o.allowed_verdicts.length) return o.allowed_verdicts.slice();
      var c = this.critConfig();
      if (c) return CQA.scoring.allowedVerdicts(c);
      var base = ['Да', 'Нет'];
      if (o.verdict === 'N/A') base.push('N/A');
      return o.type === 'scale' ? [o.verdict].filter(Boolean) : base;
    }

    render() {
      var a = this.appeal, o = a.original || {}, self = this;
      var top = this.top();
      var isCall = (a.fragment || []).some(function (m) { return /T\d{2}:\d{2}/.test(m.ts_raw || ''); });
      var convHref = '#/c/' + encodeURIComponent(a.uid) + '?crit=' + encodeURIComponent(a.criterion_id);

      // ---- спорная оценка
      var crit = document.createElement('cqa-criterion');
      var shown = Object.assign({}, o, { appeal_id: null, appeal_status: null });
      crit.data = { cr: shown, top: top, selected: true, canAppeal: false };

      // ---- фрагмент
      this.fragment = document.createElement('cqa-transcript');
      this.fragment.data = { messages: a.fragment || [], channel: isCall ? 'voice call' : 'agent chat', compact: true };
      var tone = U.verdictTone(o.verdict, o.type, top);

      var left = h('div', { class: 'stack stack--l' },
        h('section', { class: 'card', 'aria-labelledby': 'ap-disputed' },
          h('h2', { class: 'card__title', id: 'ap-disputed', style: { marginBottom: '12px' } }, t('appeal.disputed')),
          crit),
        h('section', { class: 'card', 'aria-labelledby': 'ap-comment' },
          h('h2', { class: 'card__title', id: 'ap-comment', style: { marginBottom: '8px' } }, t('appeal.operatorComment')),
          h('div', { class: 'quote quote--brand' }, a.comment),
          a.counter_evidence && a.counter_evidence.length ? h('div', { class: 'crit__section', style: { marginTop: '12px' } },
            h('div', { class: 'crit__label' }, t('appeal.counter')),
            h('div', { class: 'evchips evchips--inline' }, a.counter_evidence.map(function (mid) {
              return h('button', { class: 'evchip evchip--plain', type: 'button', onClick: function () { self.goTo(mid); } }, h('span', { class: 'evchip__id' }, mid));
            }))) : null),
        h('section', { class: 'card', 'aria-labelledby': 'ap-fragment' },
          h('div', { class: 'card__header' },
            h('h2', { class: 'card__title', id: 'ap-fragment' }, t('appeal.fragment')),
            h('a', { class: 'btn btn--small', href: convHref }, icon('external'), t('appeal.openConversation'))),
          h('div', { class: 'appeal-fragment' }, this.fragment)));

      this.fragment.highlight({ evidence: (o.evidence || []).map(function (e) { return e.id; }), range: o.checked_range, tone: tone, label: t('conv.evidenceOf', { id: a.criterion_id }) });
      this.fragment.markExtra(a.counter_evidence, t('appeal.counterMark'));

      // ---- правая колонка
      this.right = h('div', { class: 'stack stack--l appeal-side' });
      this.aiBox = h('div');
      this.right.appendChild(this.aiBox);
      this.renderAi();
      this.decisionBox = h('div');
      this.right.appendChild(this.decisionBox);
      if (a.status === 'decided' && a.decision) this.renderResult(); else this.renderForm();

      CQA.dom.replace(this.root,
        h('a', { class: 'back-link', href: '#/appeals' }, icon('arrowLeft', 'icon--16'), t('nav.appeals')),
        h('div', { class: 'page-header' },
          h('div', null,
            h('div', { class: 'row' },
              h('h1', null, t('appeal.pageTitle', { id: a.id })),
              ui.badge(CQA.i18n.tx('appealStatus.' + a.status, a.status), a.status === 'decided' ? 'success' : 'warning')),
            h('div', { class: 'page-header__sub' },
              t('appeal.headerSub', { c: a.criterion_id + ' ' + a.criterion_title, d: fmt.date(a.created_at) }), ' · ',
              h('a', { href: convHref }, fmt.externalId(a.uid))))),
        h('div', { class: 'appeal-layout' }, left, this.right));
    }

    // ------------------------------------------------------------ второе мнение ИИ
    renderAi() {
      var self = this, a = this.appeal, o = a.original || {}, ai = a.ai_opinion;
      var top = this.top();
      if (!ai && a.status !== 'open') { CQA.dom.clear(this.aiBox); return; }
      var head = h('div', { class: 'card__header' },
        h('h2', { class: 'card__title row', id: 'ap-ai' }, icon('sparkle'), t('appeal.aiOpinion')),
        ai && ai.model ? h('span', { class: 'muted t-caption1' }, ai.model) : null);
      var card = h('section', { class: 'card ai-card', 'aria-labelledby': 'ap-ai', 'aria-busy': 'false' }, head);
      var askBtn = function (label, primary) {
        var b = h('button', { class: 'btn' + (primary ? ' btn--primary' : ' btn--small btn--subtle'), type: 'button' }, icon('sparkle'), label);
        b.addEventListener('click', function () { self.askAi(b, card); });
        return b;
      };
      if (!ai) {
        card.appendChild(h('p', { class: 'subtle' }, t('appeal.aiAskText')));
        card.appendChild(h('div', { class: 'row', style: { marginTop: '12px' } }, askBtn(t('appeal.aiAsk'), true)));
        this.aiStatus = h('div', { 'aria-live': 'polite', style: { marginTop: '8px' } });
        card.appendChild(this.aiStatus);
        CQA.dom.replace(this.aiBox, card);
        return;
      }
      var sup = { 'да': ['success', 'checkCircle', 'appeal.aiSupportYes'], 'частично': ['mid', 'info', 'appeal.aiSupportPartly'], 'нет': ['neutral', 'close', 'appeal.aiSupportNo'] }[ai.appeal_supported] || null;
      card.appendChild(h('div', { class: 'stack' },
        sup ? h('div', { class: 'ai-support ai-support--' + sup[0] }, icon(sup[1]), h('span', null, t(sup[2]))) : null,
        h('dl', { class: 'kv ai-kv' },
          h('dt', null, t('appeal.aiVerdict')), h('dd', null, CQA.verdict(ai.verdict, o.type, top, { size: 'l' }),
            ai.category ? h('span', { class: 'muted t-caption1' }, ' ' + ai.category) : null),
          h('dt', null, t('appeal.aiSystemVerdict')), h('dd', null, CQA.verdict(ai.compared_to || o.verdict, o.type, top))),
        ai.explanation ? h('p', { class: 'subtle' }, ai.explanation) : null,
        ai.evidence && ai.evidence.length ? h('div', { class: 'crit__section' },
          h('div', { class: 'crit__label' }, t('crit.evidence')),
          h('div', { class: 'evchips' }, ai.evidence.map(function (e) {
            return h('button', { class: 'evchip', type: 'button', title: t('crit.goToMessage', { mid: e.id }), onClick: function () { self.goTo(e.id); } },
              h('span', { class: 'evchip__id' }, e.id), h('span', { class: 'evchip__q' }, '«' + e.quote + '»'));
          }))) : null,
        ai.reasoning ? h('details', { class: 'disclosure' },
          h('summary', null, icon('chevronRight'), t('crit.reasoning')),
          h('div', { class: 'disclosure__body t-caption1 subtle pre-wrap' }, ai.reasoning)) : null,
        ai.uncertain ? ui.badge(t('appeal.aiUncertain'), 'warning', 'eye') : null,
        h('div', { class: 'muted t-caption1' }, t('appeal.aiBlindNote')),
        h('div', { class: 'row t-caption1' },
          h('a', { href: '#/log/' + a.evaluation_id }, icon('code', 'icon--16'), ' ', t('appeal.aiLog')),
          ai.cost_usd != null ? h('span', { class: 'muted' }, fmt.money(ai.cost_usd)) : null,
          ai.created_at ? h('span', { class: 'muted' }, fmt.date(ai.created_at)) : null,
          h('span', { class: 'spacer' }),
          a.status === 'open' ? askBtn(t('appeal.aiAskAgain'), false) : null)));
      CQA.dom.replace(this.aiBox, card);
    }

    askAi(btn, card) {
      var self = this;
      btn.disabled = true;
      CQA.dom.replace(btn, ui.spinner(), t('appeal.aiAsking'));
      card.setAttribute('aria-busy', 'true');
      CQA.api.secondOpinion(this.appeal.id).then(function (a) {
        self.appeal = a;
        ui.toast(t('appeal.aiReady'), { tone: 'success' });
        self.renderAi();
      }, function (err) {
        btn.disabled = false;
        card.setAttribute('aria-busy', 'false');
        CQA.dom.replace(btn, icon('sparkle'), t('appeal.aiAsk'));
        ui.toast(t('appeal.aiFailed'), { tone: 'error', detail: err.message });
      });
    }

    goTo(mid) {
      if (this.fragment && this.fragment.has(mid)) { this.fragment.focusMessage(mid); return; }
      location.hash = '#/c/' + encodeURIComponent(this.appeal.uid) + '?crit=' + encodeURIComponent(this.appeal.criterion_id) + '&m=' + encodeURIComponent(mid);
    }

    // ------------------------------------------------------------ форма решения
    renderForm() {
      var self = this, a = this.appeal, o = a.original || {};
      var top = this.top();
      var current = o.final_verdict || o.verdict;
      var options = this.allowed().filter(function (v) { return v !== current; });
      var rConfirm = h('input', { type: 'radio', name: 'action', value: 'confirm', checked: true });
      var rChange = h('input', { type: 'radio', name: 'action', value: 'change', disabled: !options.length });
      this.newVerdict = h('select', { class: 'select', id: 'ap-new', disabled: true },
        options.map(function (v) { return h('option', { value: v }, U.verdictLabel(v, o.type, top)); }));
      this.reason = h('textarea', { class: 'textarea', id: 'ap-reason', rows: '4', required: true, maxlength: '2000', placeholder: t('decision.reasonPlaceholder'), 'aria-describedby': 'ap-reason-err' });
      this.reasonErr = h('div', { class: 'field__error', id: 'ap-reason-err', 'aria-live': 'polite' });
      this.precedent = h('input', { type: 'checkbox' });
      this.preview = h('div', { class: 'decision-preview', 'aria-live': 'polite' });
      this.formErr = h('div', { 'aria-live': 'assertive' });
      this.submitBtn = h('button', { class: 'btn btn--primary', type: 'submit' }, icon('check'), t('decision.submit'));
      var verdictField = h('div', { class: 'field', hidden: true },
        h('label', { class: 'field__label', for: 'ap-new' }, t('decision.newVerdict')), this.newVerdict,
        h('div', { class: 'field__hint' }, t('decision.allowedHint', { list: this.allowed().map(function (v) { return U.verdictLabel(v, o.type, top); }).join(', ') })));

      var sync = function () {
        var change = rChange.checked;
        verdictField.hidden = !change;
        self.newVerdict.disabled = !change;
        CQA.dom.replace(self.submitBtn, icon('check'), change ? t('decision.submitChange') : t('decision.submitConfirm'));
        self.updatePreview(change ? self.newVerdict.value : null);
      };
      rConfirm.addEventListener('change', sync);
      rChange.addEventListener('change', sync);
      this.newVerdict.addEventListener('change', sync);

      var form = h('form', { class: 'card stack stack--l', novalidate: true, 'aria-labelledby': 'ap-decision' },
        h('h2', { class: 'card__title', id: 'ap-decision' }, t('decision.title')),
        h('fieldset', { class: 'radio-cards' },
          h('legend', { class: 'sr-only' }, t('decision.title')),
          h('label', { class: 'radio-card' }, rConfirm,
            h('span', null, h('span', { class: 'radio-card__title' }, t('decision.confirm')), h('span', { class: 'radio-card__text' }, t('decision.confirmText', { v: U.verdictLabel(current, o.type, top) })))),
          h('label', { class: 'radio-card' + (options.length ? '' : ' is-disabled') }, rChange,
            h('span', null, h('span', { class: 'radio-card__title' }, t('decision.change')), h('span', { class: 'radio-card__text' }, t('decision.changeText'))))),
        verdictField,
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: 'ap-reason' }, t('decision.reason'), h('span', { class: 'req', 'aria-hidden': 'true' }, '*')),
          this.reason, this.reasonErr),
        h('div', { class: 'field' },
          h('label', { class: 'check check--small' }, this.precedent, t('decision.precedent')),
          h('div', { class: 'field__hint' }, t('decision.precedentHint'))),
        this.preview,
        this.formErr,
        h('div', { class: 'row' }, this.submitBtn));
      form.addEventListener('submit', function (e) { e.preventDefault(); self.submit(rChange.checked); });
      CQA.dom.replace(this.decisionBox, form);
      sync();
    }

    /** Предпросмотр нового итога той же формулой §3 (окончательный пересчёт делает бэкенд). */
    updatePreview(newVerdict) {
      var box = this.preview;
      box.textContent = '';
      var ev = this.ev, cfg = this.config;
      if (!ev || !cfg) return;
      var cur = ev.final_total != null ? ev.final_total : ev.total;
      if (!newVerdict) {
        box.appendChild(h('div', { class: 'row' }, h('span', { class: 'muted' }, t('decision.totalStays')), CQA.score(cur, null, { max: true })));
        return;
      }
      var verdicts = {}, cats = {};
      ev.criteria.forEach(function (c) { verdicts[c.criterion_id] = c.final_verdict || c.verdict; cats[c.criterion_id] = c.category; });
      verdicts[this.appeal.criterion_id] = newVerdict;
      try {
        var res = CQA.scoring.score(cfg, verdicts, cats);
        box.appendChild(h('div', { class: 'row' }, h('span', { class: 'muted' }, t('decision.totalWillBe')), CQA.score(res.total, cur, { max: true })));
      } catch (e) { /* конфиг не совпал — без предпросмотра */ }
    }

    submit(change) {
      var self = this;
      this.reasonErr.textContent = '';
      this.reason.removeAttribute('aria-invalid');
      CQA.dom.clear(this.formErr);
      if (!this.reason.value.trim()) {
        this.reason.setAttribute('aria-invalid', 'true');
        CQA.dom.replace(this.reasonErr, icon('error'), t('decision.reasonRequired'));
        this.reason.focus();
        return;
      }
      var dto = { action: change ? 'change' : 'confirm', new_verdict: change ? this.newVerdict.value : null, reason: this.reason.value.trim(), make_precedent: this.precedent.checked };
      this.submitBtn.disabled = true;
      CQA.dom.replace(this.submitBtn, ui.spinner(), t('decision.saving'));
      CQA.api.decide(this.appeal.id, dto).then(function (a) {
        self.appeal = a;
        ui.toast(t('decision.saved'), { tone: 'success' });
        document.dispatchEvent(new CustomEvent('cqa-appeals-changed'));
        self.render();
      }, function (err) {
        self.submitBtn.disabled = false;
        CQA.dom.replace(self.submitBtn, icon('check'), t('decision.submit'));
        var ve = CQA.api.validationErrors(err);
        ve.forEach(function (x) { if (/reason/.test(x.loc)) { self.reason.setAttribute('aria-invalid', 'true'); CQA.dom.replace(self.reasonErr, icon('error'), x.msg); } });
        self.formErr.appendChild(ui.msgbar('danger', t('decision.failed'), err.message));
      });
    }

    // ------------------------------------------------------------ результат
    renderResult() {
      var a = this.appeal, o = a.original || {}, d = a.decision || {};
      var top = this.top();
      var prev = a.prev_final_total != null ? a.prev_final_total : d.prev_final_total;
      var next = a.new_final_total != null ? a.new_final_total : d.new_final_total;
      CQA.dom.replace(this.decisionBox, h('section', { class: 'card decision-result', 'aria-labelledby': 'ap-result' },
        h('div', { class: 'card__header' }, h('h2', { class: 'card__title row', id: 'ap-result' }, icon('checkCircle'), t('decision.resultTitle')),
          d.created_at ? h('span', { class: 'muted t-caption1' }, fmt.date(d.created_at)) : null),
        h('dl', { class: 'kv decision-kv' },
          h('dt', null, t('decision.original')),
          h('dd', null, h('span', { class: 'row' }, CQA.verdict(o.verdict, o.type, top),
            o.score && o.type !== 'flag' && o.verdict !== 'N/A' && (o.final_verdict == null || o.final_verdict === o.verdict) ? h('span', { class: 'muted num t-caption1' }, t('decision.points', { p: fmt.score(o.score.points), m: fmt.score(o.score.max_points) })) : null)),
          h('dt', null, t('decision.label')),
          h('dd', null, d.action === 'change'
            ? h('span', { class: 'row' }, t('decision.changeTo'), CQA.verdict(d.new_verdict, o.type, top))
            : t('decision.confirmed')),
          h('dt', null, t('decision.reason')), h('dd', null, d.reason || '—'),
          d.make_precedent ? [h('dt', null, t('decision.precedentShort')), h('dd', null, t('decision.precedentYes'))] : null,
          h('dt', null, t('decision.total')),
          h('dd', null, next != null ? CQA.score(next, prev, { max: true }) : '—')),
        h('div', { class: 'card__footer' },
          h('a', { class: d.make_precedent ? 'btn' : 'btn btn--primary', href: '#/c/' + encodeURIComponent(a.uid) + '?crit=' + encodeURIComponent(a.criterion_id) }, icon('chat'), t('decision.openCard')),
          h('a', { class: 'btn', href: '#/appeals' }, t('decision.toList')))));
      if (d.make_precedent) this.renderPrecedent();
    }

    /** Решение стало прецедентом → путь «версия чек-листа с примером → проверка на похожих → было/стало». */
    renderPrecedent() {
      var self = this, a = this.appeal;
      if (!this.precBox) { this.precBox = h('div'); this.right.appendChild(this.precBox); }
      var card = function (body) {
        return h('section', { class: 'card prec-card', 'aria-labelledby': 'ap-prec' },
          h('div', { class: 'card__header' },
            h('div', null, h('h2', { class: 'card__title row', id: 'ap-prec' }, icon('sparkle'), t('prec.flowTitle')),
              h('div', { class: 'muted t-caption1' }, t('prec.flowSub')))),
          body);
      };
      CQA.dom.replace(this.precBox, card(ui.skeleton('text', 3)));
      CQA.api.precedents(a.criterion_id).then(function (list) {
        var mine = (list || []).filter(function (p) { return p.appeal_id === a.id; });
        if (!mine.length) { CQA.dom.replace(self.precBox, card(ui.msgbar('warning', t('prec.notFoundForAppeal')))); return; }
        var flow = document.createElement('cqa-precedent-flow');
        flow.data = { precedents: mine.slice(0, 1) };
        CQA.dom.replace(self.precBox, card(h('div', { class: 'stack' },
          h('div', { class: 'row t-caption1' }, h('span', { class: 'muted' }, t('prec.thisOne', { id: mine[0].id })),
            h('a', { href: '#/checklists/' + encodeURIComponent(CQA.config.checklistId) + '/precedents' }, t('prec.allLink'))),
          flow)));
      }, function (err) {
        CQA.dom.replace(self.precBox, card(ui.error(err, function () { self.renderPrecedent(); })));
      });
    }
  }

  customElements.define('cqa-page-appeal', CqaPageAppeal);
})(window.CQA = window.CQA || {});
