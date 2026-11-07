/* <cqa-transcript> — транскрипт: реплики с ролями, участки (бот / ожидание / удержание), метки
   «дубль», «вне записи», «после завершения»; подсветка доказательств и выбор реплик для апелляции.

   .data = {messages, channel}
   .highlight({evidence: [mid], range: [from, to], tone, label})
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
      var block = null, prevSeg = null;
      var self = this;
      this._messages.forEach(function (m, i) {
        if (m.segment !== prevSeg) {
          block = self.segmentBlock(m.segment, prevSeg, i);
          list.appendChild(block);
          prevSeg = m.segment;
        }
        block.appendChild(self.renderMessage(m));
      });
      if (!this._messages.length) list.appendChild(h('div', { class: 'muted' }, t('transcript.empty')));
      this.appendChild(list);
      this.list = list;
    }

    segmentBlock(seg, prevSeg, i) {
      var label;
      if (seg === 'hold') label = t('segment.hold');
      else if (seg === 'waiting') label = t('segment.waiting');
      else if (seg === 'bot') {
        var hasBot = this._messages.some(function (m) { return m.segment === 'bot' && m.speaker === 'bot'; });
        label = hasBot ? t('segment.bot') : t('segment.beforeOperator');
      } else if (seg === 'operator') label = prevSeg === 'hold' ? t('segment.holdEnd') : t('segment.operator');
      else label = seg;
      var showHeader = !(seg === 'operator' && i === 0);
      return h('section', { class: 'seg seg--' + seg + (prevSeg === 'hold' ? ' seg--after-hold' : ''), role: 'presentation' },
        showHeader ? h('div', { class: 'seg__header', role: 'separator', 'aria-label': label },
          seg === 'hold' ? icon('hold') : null, h('span', null, label)) : null);
    }

    renderMessage(m) {
      var self = this;
      var role = m.speaker || 'unknown';
      var labels = [];
      if (m.dup_of) labels.push(h('span', { class: 'mlabel mlabel--dup', title: t('label.dupTitle', { mid: m.dup_of }) }, t('label.dup', { mid: m.dup_of })));
      if (this.isCall && m.recorded === false) labels.push(h('span', { class: 'mlabel mlabel--offrec', title: t('label.offRecordTitle') }, t('label.offRecord')));
      if (m.after_end) labels.push(h('span', { class: 'mlabel mlabel--after', title: t('label.afterEndTitle') }, t('label.afterEnd')));
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
            h('span', { class: 'msg__mid' }, m.mid),
            h('span', { class: 'msg__text' }, m.text),
            h('span', { class: 'msg__time' }, time),
            audioBtn, labels),
          h('div', { class: 'msg__marks' }));
      } else {
        el = h('div', { class: 'msg msg--' + role, id: 'msg-' + m.mid, 'data-mid': m.mid, role: 'listitem', tabindex: '-1',
          'aria-label': t('role.' + role) + ', ' + m.mid + (time ? ', ' + time : '') },
          h('div', { class: 'msg__meta' },
            h('span', { class: 'msg__role' }, icon(ROLE_ICON[role] || 'info'), t('role.' + role)),
            h('span', { class: 'msg__mid' }, m.mid),
            time ? h('span', { class: 'msg__time' }, time) : null,
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

    /** Подсветка доказательств выбранного критерия и проверенного участка. */
    highlight(opts) {
      opts = opts || {};
      this.querySelectorAll('.is-evidence, .is-range').forEach(function (el) {
        el.classList.remove('is-evidence', 'is-range', 'is-range-start', 'is-range-end');
        el.removeAttribute('data-tone');
        var marks = el.querySelector('.msg__marks'); if (marks) marks.textContent = '';
      });
      var self = this;
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
      (opts.evidence || []).forEach(function (mid) {
        var el = self.msgEl(mid);
        if (!el) return;
        el.classList.add('is-evidence');
        el.setAttribute('data-tone', opts.tone || 'neutral');
        var marks = el.querySelector('.msg__marks');
        if (marks && opts.label) {
          marks.textContent = '';
          marks.appendChild(h('span', { class: 'msg__mark' }, icon('flag'), opts.label));
        }
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
