/* <cqa-criterion> — строка критерия в карточке: номер, название, вердикт-пилюля, потеря баллов.
   Раскрытая строка: объяснение, цитаты (клик подсвечивает реплику), «как надо было», второе мнение при расхождении,
   апелляция и действия «Оспорить», «Потренироваться» / «Назначить тренировку», «Изменить критерий».
   .data = {cr, top, appeal, selected, canAppeal, canEdit, trainUid, trainOperator, trainSelf, messages: {mid: MessageDTO}}
   События (bubbles): cqa-criterion-select {cid, toggle}, cqa-evidence {cid, mid}, cqa-range {cid, range}, cqa-appeal {cid} */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, U = CQA.util, ui = CQA.ui;

  function fire(el, name, detail) { el.dispatchEvent(new CustomEvent(name, { bubbles: true, detail: detail })); }

  class CqaCriterion extends HTMLElement {
    set data(d) { this._d = d; this.render(); }
    get data() { return this._d; }
    get cid() { return this._d && this._d.cr.criterion_id; }

    set selected(on) {
      this.classList.toggle('is-open', !!on);
      if (this.headBtn) this.headBtn.setAttribute('aria-expanded', String(!!on));
      if (this.body) this.body.hidden = !on;
    }

    render() {
      var d = this._d, cr = d.cr, self = this;
      var E = CQA.evalUtil;
      var verdict = E.vOf(cr);
      var changed = cr.final_verdict != null && cr.verdict != null && cr.final_verdict !== cr.verdict;
      var isFlag = cr.type === 'flag';
      var isNA = verdict === 'N/A';
      var gross = E.isGross(cr);
      var loss = E.lossOf(cr);
      var tone = gross ? 'gross' : U.verdictTone(verdict, cr.type, d.top);
      var violation = gross || (CQA.isViolation && CQA.isViolation(verdict, cr.type, d.top));
      this.className = 'crow crow--' + tone + (isNA ? ' crow--na' : '') + (cr.status === 'failed' ? ' crow--failed' : '');
      this.id = 'crit-' + cr.criterion_id;
      this.textContent = '';

      // ---- строка
      var pts;
      if (cr.status === 'failed' || verdict == null) pts = h('span', { class: 'crow__pts muted' }, '—');
      else if (isFlag || isNA) pts = h('span', { class: 'crow__pts muted' }, '—');
      else if (loss > 0.004) pts = h('span', { class: 'crow__pts loss', 'aria-label': t('crit.lossAria', { p: fmt.score(loss) }) }, '−' + fmt.score(loss));
      else pts = h('span', { class: 'crow__pts' }, cr.score ? fmt.score(cr.score.points) : '—');
      var pill = gross
        ? h('span', { class: 'badge badge--gross verdict' }, cr.category ? cap(cr.category) : t('badge.gross'))
        : CQA.verdict(verdict, cr.type, d.top);
      this.headBtn = h('button', {
        class: 'crow__head', type: 'button', 'aria-expanded': String(!!d.selected), 'aria-controls': 'crit-body-' + cr.criterion_id,
        'aria-label': t('crit.selectAria', { id: cr.criterion_id, title: cr.title, v: gross ? t('badge.gross') : U.verdictLabel(verdict, cr.type, d.top) }),
        onClick: function () { fire(self, 'cqa-criterion-select', { cid: cr.criterion_id, toggle: true }); }
      },
        h('span', { class: 'crow__id' }, cr.criterion_id),
        h('span', { class: 'crow__title' }, cr.title,
          cr.appeal_id ? h('span', { class: 'crow__flag', 'data-tip': t('crit.hasAppeal') }, icon('scale', 'icon--16')) : null,
          cr.needs_review ? h('span', { class: 'crow__flag', 'data-tip': t('badge.reviewTitle') }, icon('eye', 'icon--16')) : null),
        h('span', { class: 'crow__verdict' }, changed ? CQA.verdict(cr.verdict, cr.type, d.top, { strike: true }) : null, pill),
        pts,
        h('span', { class: 'crow__chev', 'aria-hidden': 'true' }, icon('chevronDown', 'icon--16')));
      this.appendChild(this.headBtn);

      // ---- раскрытие
      var body = h('div', { class: 'crow__body', id: 'crit-body-' + cr.criterion_id, hidden: !d.selected });
      this.body = body;
      this.appendChild(body);

      if (cr.status === 'failed') body.appendChild(ui.msgbar('warning', t('crit.failed'), cr.error || null));
      var expl = cleanExplanation(cr.explanation, cr.title);
      if (expl) body.appendChild(h('p', { class: 'crow__expl' }, expl));

      // доказательства: «кто, когда — «цитата»» → подсветка реплики в переписке
      if (cr.evidence && cr.evidence.length) {
        var chips = h('div', { class: 'evchips' });
        cr.evidence.forEach(function (ev) {
          var bad = ev.ok === false;
          var m = d.messages && d.messages[ev.id];
          var who = m ? [CQA.i18n.tx('role.' + m.speaker, m.speaker), fmt.ts(m.ts_raw)].filter(Boolean).join(', ') : ev.id;
          chips.appendChild(h('button', {
            class: 'evchip' + (bad ? ' evchip--bad' : ''), type: 'button',
            'data-tip': bad ? t('crit.evidenceBad') : null,
            'aria-label': t('crit.goToMessage', { mid: ev.id }) + ': ' + ev.quote,
            onClick: function (e) {
              Array.prototype.forEach.call(chips.querySelectorAll('.evchip'), function (c) { c.classList.remove('is-active'); });
              e.currentTarget.classList.add('is-active');
              fire(self, 'cqa-evidence', { cid: cr.criterion_id, mid: ev.id });
            }
          },
            h('span', { class: 'evchip__id' }, who),
            h('span', { class: 'evchip__q' }, '«' + ev.quote + '»'),
            bad ? icon('warning') : null));
        });
        body.appendChild(h('div', { class: 'crow__section' }, h('div', { class: 'crow__label' }, t('crit.evidence'), h('span', { class: 'muted' }, ' · ' + t('crit.evidenceHint'))), chips));
      }

      // как надо было
      if (cr.how_to_fix) {
        body.appendChild(h('div', { class: 'crow__section' },
          h('div', { class: 'crow__label' }, t('crit.howToFix')),
          h('div', { class: 'quote crow__fix' }, cr.how_to_fix)));
      }

      // второе мнение — только расхождение
      var so = cr.second_opinion && cr.second_opinion.verdict ? cr.second_opinion : null;
      if (so && so.agree === false) {
        body.appendChild(h('div', { class: 'crow__second', role: 'note' },
          h('div', { class: 'row' }, icon('people', 'icon--16'),
            h('b', null, t('crit.secondDisagree')),
            CQA.verdict(so.verdict, cr.type, d.top),
            so.near ? h('span', { class: 'muted t-caption1' }, t('eval.near')) : null),
          so.explanation ? h('div', { class: 't-caption1 subtle' }, so.explanation) : null,
          d.canAppeal !== false ? h('div', { class: 't-caption1 muted' }, t('crit.secondDecide')) : null));
      }

      // апелляция
      if (cr.appeal_id) body.appendChild(this.appealBox(cr, d));

      // действия
      var actions = h('div', { class: 'crow__actions' });
      if (!cr.appeal_id && d.canAppeal !== false && cr.status !== 'failed' && verdict != null) {
        actions.appendChild(h('button', { class: 'btn btn--small' + (violation ? ' btn--primary' : ''), type: 'button',
          onClick: function () { fire(self, 'cqa-appeal', { cid: cr.criterion_id }); } }, icon('scale'), t('crit.dispute')));
      }
      if (d.trainUid && cr.status !== 'failed' && violation && !isFlag) {
        actions.appendChild(h('a', { class: 'btn btn--small', href: CQA.trainHref(d.trainUid, { focus: [cr.criterion_id], operator: d.trainOperator }),
          'data-tip': d.trainSelf ? t('crit.trainTitle', { id: cr.criterion_id }) : t('crit.assignTitle', { id: cr.criterion_id }) },
          icon('target'), d.trainSelf ? t('crit.train') : t('crit.assign')));
      }
      if (d.canEdit) {
        actions.appendChild(h('span', { class: 'spacer' }));
        actions.appendChild(h('a', { class: 'btn btn--subtle btn--small crow__edit', href: CQA.navHref('checklist') + '?crit=' + encodeURIComponent(cr.criterion_id),
          'data-tip': t('crit.editTitle') }, icon('edit'), t('crit.edit')));
      }
      if (actions.childNodes.length) body.appendChild(actions);

      // служебное: ход рассуждения и проверенный участок
      var r = cr.checked_range && cr.checked_range.length === 2 && cr.checked_range[0] ? cr.checked_range : null;
      if (cr.reasoning || r) {
        body.appendChild(h('details', { class: 'disclosure crow__more' },
          h('summary', null, icon('chevronRight'), t('crit.reasoning')),
          h('div', { class: 'disclosure__body stack' },
            r ? h('div', { class: 'row t-caption1' }, h('span', { class: 'muted' }, t('crit.checkedRange')),
              h('button', { class: 'btn btn--link t-caption1', type: 'button', onClick: function () { fire(self, 'cqa-range', { cid: cr.criterion_id, range: r }); } },
                r[0] === r[1] ? r[0] : r[0] + '–' + r[1])) : null,
            cr.reasoning ? h('div', { class: 't-caption1 subtle pre-wrap' }, cr.reasoning) : null)));
      }
      this.selected = d.selected;
    }

    appealBox(cr, d) {
      var ap = d.appeal;
      var st = cr.appeal_status || (ap && ap.status) || 'open';
      var box = h('div', { class: 'crow__appeal crow__appeal--' + st },
        h('div', { class: 'row' },
          icon('scale', 'icon--16'),
          h('b', null, st === 'decided' ? t('crit.appealDecided') : t('crit.appealOpen')),
          h('a', { href: '#/appeals/' + cr.appeal_id }, t('crit.appealN', { id: cr.appeal_id }))));
      if (ap && ap.decision) {
        var dec = ap.decision;
        box.appendChild(h('div', { class: 'crow__decision t-caption1' },
          h('div', { class: 'row' }, h('span', { class: 'muted' }, t('decision.label') + ':'),
            dec.action === 'change' ? [t('decision.changeTo'), CQA.verdict(dec.new_verdict, cr.type, d.top)] : t('decision.confirmed')),
          dec.reason ? h('div', null, h('span', { class: 'muted' }, t('decision.reason') + ': '), dec.reason) : null));
      } else if (st !== 'decided') {
        box.appendChild(h('div', { class: 't-caption1 muted' }, t('crit.appealWaiting')));
      }
      return box;
    }
  }

  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  /** Объяснение без повторов: «Эмпатия … — Нет.» в начале и «Основание — m13.» в конце уже видны в строке и в цитатах. */
  function cleanExplanation(text, title) {
    var s = String(text || '').trim();
    if (!s) return '';
    var esc = String(title || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (esc) s = s.replace(new RegExp('^' + esc + '\\s*[—–-]\\s*[^.]{1,24}\\.\\s*', 'i'), '');
    s = s.replace(/\s*Основани[ея]\s*[—–:-]\s*m\d+(?:\s*[,–-]\s*m\d+)*\.?\s*$/i, '');
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : String(text || '').trim();
  }
  CQA.cleanExplanation = cleanExplanation;

  customElements.define('cqa-criterion', CqaCriterion);
})(window.CQA = window.CQA || {});
