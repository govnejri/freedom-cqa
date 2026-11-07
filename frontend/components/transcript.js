/* <cqa-transcript> — переписка: реплики с ролями, участки (бот / ожидание / удержание), метки
   «дубль», «вне записи», «после завершения»; подсветка доказательств прямо в тексте реплики и выбор реплик для апелляции.
   Вступление бота до оператора свёрнуто в одну строку (раскрывается сам, если доказательство внутри).

   .data = {messages, channel, compact}
   .highlight({evidence: [mid | {id, quote}], range: [from, to], tone, label})
   .tags({mid: [{label, tone}]}) — пометки под репликой («вопрос без ответа»)
   .focusMessage(mid) — прокрутка + вспышка
   .setSelectable(on, selectedSet) — режим выбора контрдоказательств
   .setPlaying(audioSec)
   События: cqa-seek {sec, mid}, cqa-toggle-message {mid, selected}
*/
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt;

  var ROLE_ICON = { client: 'person', operator: 'headset', bot: 'bot', system: 'info', unknown: 'info' };

  class CqaTranscript extends HTMLElement {
    constructor() {
      super();
      this._messages = [];
      this._byMid = {};
      this._selectable = false;
      this._selected = new Set();
      this._playingMid = null;
      this._tags = {};
    }

    set data(d) {
      this._messages = d.messages || [];
      this._channel = d.channel;
      this._compact = !!d.compact;
      this._byMid = {};
      this._messages.forEach(function (m) { this._byMid[m.mid] = m; }, this);
      this.render();
    }

    get isCall() { return this._channel === 'voice call'; }

    render() {
      this.textContent = '';
      this.classList.add('transcript');
      this.classList.toggle('transcript--compact', this._compact);
      var list = h('div', { class: 'transcript__list', role: 'list', 'aria-label': t('transcript.aria') });
      var block = null, prevSeg = null, body = null;
      var self = this;
      this._messages.forEach(function (m, i) {
        if (m.segment !== prevSeg) {
          var seg = self.segmentBlock(m.segment, prevSeg, i);
          block = seg.el;
          body = seg.body;
          list.appendChild(block);
          prevSeg = m.segment;
        }
        body.appendChild(self.renderMessage(m));
      });
      if (!this._messages.length) list.appendChild(h('div', { class: 'muted' }, t('transcript.empty')));
      this.appendChild(list);
      this.list = list;
    }

    segmentBlock(seg, prevSeg, i) {
      var self = this;
      var inSeg = this._messages.filter(function (m) { return m.segment === seg; });
      // вступление бота: одна строка «Бот до оператора · 8 реплик · Показать»
      var hasBot = inSeg.some(function (m) { return m.speaker === 'bot'; });
      if (seg === 'bot' && !this._compact && inSeg.length > 2 && hasBot) {
        var firstClient = inSeg.filter(function (m) { return m.speaker === 'client'; })[0];
        var body = h('div', { class: 'seg__body', id: 'seg-bot-body' });
        var toggle = h('button', { class: 'seg__toggle', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'seg-bot-body' },
          icon('bot', 'icon--16'),
          h('span', { class: 'seg__toggle-main' },
            h('b', null, t('segment.botIntro')),
            h('span', { class: 'muted' }, ' · ' + t('transcript.nMessages', { n: inSeg.length })),
            firstClient ? h('span', { class: 'seg__preview' }, t('role.client') + ': ' + firstClient.text) : null),
          h('span', { class: 'seg__more' }, h('span', { class: 'seg__more-show' }, t('transcript.show')), h('span', { class: 'seg__more-hide' }, t('transcript.hide')), icon('chevronDown', 'icon--16')));
        var el = h('section', { class: 'seg seg--bot seg--collapsible is-collapsed', role: 'presentation' }, toggle, body);
        toggle.addEventListener('click', function () { self.expandSeg(el, el.classList.contains('is-collapsed')); });
        return { el: el, body: body };
      }
      var label;
      if (seg === 'hold') label = t('segment.hold');
      else if (seg === 'waiting') label = t('segment.waiting');
      else if (seg === 'bot') {
        var hb = inSeg.some(function (m) { return m.speaker === 'bot'; });
        label = hb ? t('segment.bot') : t('segment.beforeOperator');
      } else if (seg === 'operator') label = prevSeg === 'hold' ? t('segment.holdEnd') : t('segment.operator');
      else label = seg;
      var showHeader = !(seg === 'operator' && i === 0);
      var sec = h('section', { class: 'seg seg--' + seg + (prevSeg === 'hold' ? ' seg--after-hold' : ''), role: 'presentation' },
        showHeader ? h('div', { class: 'seg__header', role: 'separator', 'aria-label': label },
          seg === 'hold' ? icon('hold') : null, h('span', null, label)) : null);
      return { el: sec, body: sec };
    }

    expandSeg(el, on) {
      el.classList.toggle('is-collapsed', !on);
      var b = el.querySelector('.seg__toggle');
      if (b) b.setAttribute('aria-expanded', String(on));
    }

    /** Раскрыть свёрнутый участок, если реплика внутри него. */
    ensureVisible(mid) {
      var el = this.msgEl(mid);
      if (!el) return;
      var seg = el.closest('.seg--collapsible');
      if (seg && seg.classList.contains('is-collapsed')) this.expandSeg(seg, true);
    }

    renderMessage(m) {
      var self = this;
      var role = m.speaker || 'unknown';
      var labels = [];
      if (m.dup_of) labels.push(h('span', { class: 'mlabel mlabel--dup', 'data-tip': t('label.dupTitle', { mid: m.dup_of }) }, t('label.dup', { mid: m.dup_of })));
      if (this.isCall && m.recorded === false) labels.push(h('span', { class: 'mlabel mlabel--offrec', 'data-tip': t('label.offRecordTitle') }, t('label.offRecord')));
      if (m.after_end) labels.push(h('span', { class: 'mlabel mlabel--after', 'data-tip': t('label.afterEndTitle') }, t('label.afterEnd')));
      if (m.event_type === 'unknown') labels.push(h('span', { class: 'mlabel' }, t('label.unknownEvent')));

      var time = fmt.ts(m.ts_raw);
      var audioBtn = null;
      if (this.isCall && m.audio_sec != null) {
        audioBtn = h('button', {
          class: 'msg__audio', type: 'button',
          'aria-label': t('transcript.playFrom', { mid: m.mid, t: fmt.clock(m.audio_sec) }),
          onClick: function (e) { e.stopPropagation(); self.dispatchEvent(new CustomEvent('cqa-seek', { bubbles: true, detail: { sec: m.audio_sec, mid: m.mid, play: true } })); }
        }, icon('play'), fmt.clock(m.audio_sec));
      }
      var el;
      if (role === 'system') {
        el = h('div', { class: 'msg msg--system', id: 'msg-' + m.mid, 'data-mid': m.mid, role: 'listitem', tabindex: '-1' },
          h('div', { class: 'msg__system' },
            h('span', { class: 'msg__text' }, m.text),
            time ? h('span', { class: 'msg__time' }, time) : null,
            audioBtn, labels),
          h('div', { class: 'msg__marks' }));
      } else {
        el = h('div', { class: 'msg msg--' + role, id: 'msg-' + m.mid, 'data-mid': m.mid, role: 'listitem', tabindex: '-1',
          'aria-label': t('role.' + role) + ', ' + m.mid + (time ? ', ' + time : '') },
          h('div', { class: 'msg__meta' },
            h('span', { class: 'msg__role' }, t('role.' + role)),
            time ? h('span', { class: 'msg__time' }, time) : null,
            h('span', { class: 'msg__mid', 'aria-hidden': 'true' }, m.mid),
            audioBtn, labels),
          h('div', { class: 'msg__bubble' }, h('span', { class: 'msg__text' }, m.text)),
          h('div', { class: 'msg__marks' }));
      }
      if (m.segment === 'hold') el.classList.add('msg--hold');
      if (m.after_end) el.classList.add('msg--after');
      if (m.dup_of) el.classList.add('msg--dup');
      el.addEventListener('click', function (e) {
        if (!self._selectable) return;
        if (e.target.closest('button') && !e.target.closest('.msg__select')) return;
        self.toggle(m.mid);
      });
      return el;
    }

    msgEl(mid) { return this.querySelector('[data-mid="' + CSS.escape(mid) + '"]'); }
    has(mid) { return !!this._byMid[mid]; }
    message(mid) { return this._byMid[mid]; }

    /** Текст реплики заново; quote — подсветить цитату прямо в тексте. */
    setText(el, mid, quote) {
      var span = el.querySelector('.msg__text');
      var m = this._byMid[mid];
      if (!span || !m) return;
      var text = m.text || '';
      var at = quote ? text.indexOf(quote) : -1;
      if (at < 0 && quote) { var lo = text.toLowerCase(); at = lo.indexOf(quote.toLowerCase()); }
      if (at < 0) { span.textContent = text; return; }
      CQA.dom.replace(span, text.slice(0, at), h('mark', { class: 'msg__quote' }, text.slice(at, at + quote.length)), text.slice(at + quote.length));
    }

    /** Подсветка доказательств выбранного критерия и проверенного участка. */
    highlight(opts) {
      opts = opts || {};
      var self = this;
      this.querySelectorAll('.is-evidence, .is-range').forEach(function (el) {
        el.classList.remove('is-evidence', 'is-range', 'is-range-start', 'is-range-end');
        el.removeAttribute('data-tone');
        var marks = el.querySelector('.msg__marks');
        if (marks) Array.prototype.slice.call(marks.querySelectorAll('.msg__mark--ev')).forEach(function (x) { x.remove(); });
        self.setText(el, el.getAttribute('data-mid'), null);
      });
      if (opts.range && opts.range.length === 2) {
        var a = this._byMid[opts.range[0]], b = this._byMid[opts.range[1]];
        if (a && b) {
          var lo = Math.min(a.idx, b.idx), hi = Math.max(a.idx, b.idx);
          this._messages.forEach(function (m) {
            if (m.idx < lo || m.idx > hi) return;
            var el = self.msgEl(m.mid);
            if (!el) return;
            el.classList.add('is-range');
            if (m.idx === lo) el.classList.add('is-range-start');
            if (m.idx === hi) el.classList.add('is-range-end');
          });
        }
      }
      (opts.evidence || []).forEach(function (ev) {
        var mid = typeof ev === 'string' ? ev : ev.id;
        var el = self.msgEl(mid);
        if (!el) return;
        el.classList.add('is-evidence');
        el.setAttribute('data-tone', opts.tone || 'neutral');
        if (typeof ev !== 'string' && ev.quote) self.setText(el, mid, ev.quote);
        var marks = el.querySelector('.msg__marks');
        if (marks && opts.label && !marks.querySelector('.msg__mark--ev')) {
          marks.insertBefore(h('span', { class: 'msg__mark msg__mark--ev' }, opts.label), marks.firstChild);
        }
      });
    }

    /** Пометки под репликами: {mid: [{label, tone, tip}]} (вопрос без ответа и т. п.). */
    tags(map) {
      var self = this;
      this._tags = map || {};
      this.querySelectorAll('.msg__mark--tag').forEach(function (x) { x.remove(); });
      Object.keys(this._tags).forEach(function (mid) {
        var el = self.msgEl(mid);
        var marks = el && el.querySelector('.msg__marks');
        if (!marks) return;
        self._tags[mid].forEach(function (tg) {
          marks.appendChild(h('span', { class: 'msg__mark msg__mark--tag msg__mark--' + (tg.tone || 'warning'), 'data-tip': tg.tip || null, tabindex: tg.tip ? '0' : null }, tg.label));
        });
      });
    }

    /** Дополнительная пометка реплик (контрдоказательства апелляции). */
    markExtra(mids, label) {
      var self = this;
      this.querySelectorAll('.is-counter').forEach(function (el) { el.classList.remove('is-counter'); var x = el.querySelector('.msg__mark--counter'); if (x) x.remove(); });
      (mids || []).forEach(function (mid) {
        var el = self.msgEl(mid);
        if (!el) return;
        el.classList.add('is-counter');
        var marks = el.querySelector('.msg__marks');
        if (marks) marks.appendChild(h('span', { class: 'msg__mark msg__mark--counter' }, icon('scale'), label));
      });
    }

    /** Показать реплику, если она вне видимой области (без вспышки и фокуса). */
    reveal(mid) {
      this.ensureVisible(mid);
      var el = this.msgEl(mid);
      if (!el) return;
      var pane = this.closest('.conv-pane') || this.parentElement;
      if (pane) {
        var pr = pane.getBoundingClientRect(), er = el.getBoundingClientRect();
        if (er.top >= pr.top + 60 && er.bottom <= pr.bottom - 20) return;
      }
      CQA.dom.scrollIntoPane(el, { block: 'center' });
    }

    focusMessage(mid, opts) {
      this.ensureVisible(mid);
      var el = this.msgEl(mid);
      if (!el) return false;
      CQA.dom.scrollIntoPane(el, { block: 'center' });
      el.classList.remove('is-flash');
      void el.offsetWidth; // перезапуск анимации
      el.classList.add('is-flash');
      if (!opts || opts.focus !== false) el.focus({ preventScroll: true });
      return true;
    }

    setSelectable(on, selected) {
      this._selectable = !!on;
      this._selected = selected || new Set();
      this.classList.toggle('is-selecting', this._selectable);
      var self = this;
      if (on) this.querySelectorAll('.seg--collapsible.is-collapsed').forEach(function (s) { self.expandSeg(s, true); });
      this.querySelectorAll('.msg').forEach(function (el) {
        var mid = el.getAttribute('data-mid');
        var old = el.querySelector('.msg__select');
        if (old) old.remove();
        el.classList.toggle('is-selected', self._selectable && self._selected.has(mid));
        if (!self._selectable) return;
        var btn = h('button', {
          class: 'msg__select', type: 'button', 'aria-pressed': String(self._selected.has(mid)),
          'aria-label': t('transcript.select', { mid: mid })
        }, icon('check'));
        btn.addEventListener('click', function (e) { e.stopPropagation(); self.toggle(mid); });
        el.insertBefore(btn, el.firstChild);
      });
    }

    toggle(mid) {
      var on = !this._selected.has(mid);
      if (on) this._selected.add(mid); else this._selected.delete(mid);
      var el = this.msgEl(mid);
      if (el) {
        el.classList.toggle('is-selected', on);
        var b = el.querySelector('.msg__select');
        if (b) b.setAttribute('aria-pressed', String(on));
      }
      this.dispatchEvent(new CustomEvent('cqa-toggle-message', { bubbles: true, detail: { mid: mid, selected: on } }));
    }

    /** Подсветить реплику, которая сейчас звучит в плеере. */
    setPlaying(sec) {
      var cur = null;
      for (var i = 0; i < this._messages.length; i++) {
        var m = this._messages[i];
        if (m.audio_sec != null && m.audio_sec <= sec + 0.05 && m.speaker !== 'system') cur = m.mid;
        if (m.audio_sec != null && m.audio_sec > sec) break;
      }
      if (cur === this._playingMid) return;
      if (this._playingMid) { var o = this.msgEl(this._playingMid); if (o) o.classList.remove('is-playing'); }
      this._playingMid = cur;
      if (cur) { var n = this.msgEl(cur); if (n) n.classList.add('is-playing'); }
    }
  }

  customElements.define('cqa-transcript', CqaTranscript);
})(window.CQA = window.CQA || {});
