/* <cqa-score value="94.12" original="82.35" size="xl"> — итог: финальный крупно, исходный зачёркнут рядом, если отличается. */
(function (CQA) {
  'use strict';
  var h = CQA.h, fmt = CQA.fmt, U = CQA.util;

  function num(v) { return v == null || v === '' || v === 'null' ? null : Number(v); }

  class CqaScore extends HTMLElement {
    static get observedAttributes() { return ['value', 'original', 'size', 'max']; }
    connectedCallback() { this.render(); }
    attributeChangedCallback() { if (this.isConnected) this.render(); }
    render() {
      var v = num(this.getAttribute('value'));
      var o = num(this.getAttribute('original'));
      var size = this.getAttribute('size');
      var showMax = this.hasAttribute('max');
      this.className = 'score--' + U.scoreTone(v) + (size ? ' score--' + size : '');
      this.textContent = '';
      var differs = o != null && v != null && Math.abs(o - v) >= 0.005;
      var label = v == null ? CQA.t('score.none') : CQA.t('score.aria', { v: fmt.score(v) }) + (differs ? '. ' + CQA.t('score.originalAria', { v: fmt.score(o) }) : '');
      this.setAttribute('aria-label', label);
      this.setAttribute('role', 'img');
      this.appendChild(h('span', { class: 'score__value' }, v == null ? '—' : fmt.score(v)));
      if (showMax && v != null) this.appendChild(h('span', { class: 'score__max' }, '/100'));
      if (differs) this.appendChild(h('s', { class: 'score__orig', title: CQA.t('score.originalTitle') }, fmt.score(o)));
    }
  }

  CQA.score = function (value, original, opts) {
    opts = opts || {};
    var el = document.createElement('cqa-score');
    if (value != null) el.setAttribute('value', value);
    if (original != null) el.setAttribute('original', original);
    if (opts.size) el.setAttribute('size', opts.size);
    if (opts.max) el.setAttribute('max', '');
    return el;
  };

  customElements.define('cqa-score', CqaScore);
})(window.CQA = window.CQA || {});
