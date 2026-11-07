/* <cqa-verdict verdict="Да" type="yes_no" top="20" size="l"> — бейдж вердикта.
   Да — зелёный, Нет — красный, N/A — серый, шкала — по доле от верхней ступени. У флага «Да» = нарушение (красный). */
(function (CQA) {
  'use strict';
  var h = CQA.h, U = CQA.util;

  class CqaVerdict extends HTMLElement {
    static get observedAttributes() { return ['verdict', 'type', 'top', 'size', 'strike']; }
    connectedCallback() { this.render(); }
    attributeChangedCallback() { if (this.isConnected) this.render(); }
    render() {
      var v = this.getAttribute('verdict');
      var type = this.getAttribute('type') || 'yes_no';
      var top = this.getAttribute('top');
      var tone = U.verdictTone(v, type, top);
      var label = U.verdictLabel(v, type, top);
      var title = type === 'flag' && v === 'Да' ? CQA.t('verdict.flagYesTitle') : type === 'flag' && v === 'Нет' ? CQA.t('verdict.flagNoTitle') : null;
      this.textContent = '';
      this.appendChild(h('span', {
        class: ['badge', 'verdict', 'badge--' + tone, this.getAttribute('size') === 'l' && 'verdict--l', this.hasAttribute('strike') && 'strike'],
        title: title
      }, label));
    }
  }

  /** Фабрика: CQA.verdict(verdict, type, top, {size, strike}) */
  CQA.verdict = function (verdict, type, top, opts) {
    opts = opts || {};
    var el = document.createElement('cqa-verdict');
    if (verdict != null) el.setAttribute('verdict', verdict);
    el.setAttribute('type', type || 'yes_no');
    if (top != null) el.setAttribute('top', top);
    if (opts.size) el.setAttribute('size', opts.size);
    if (opts.strike) el.setAttribute('strike', '');
    return el;
  };

  customElements.define('cqa-verdict', CqaVerdict);
})(window.CQA = window.CQA || {});
