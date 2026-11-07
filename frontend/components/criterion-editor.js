/* <cqa-criterion-editor> — форма одного критерия чек-листа (сворачиваемая строка + полная форма).
   .data = {crit (изменяемый объект), index, sections, base (критерий базовой версии или null), expanded}
   Поля помечены data-loc="criteria.{i}.{поле}" — по ним показываются ошибки 422 и клиентской проверки.
   События: cqa-change (любое изменение), cqa-delete {index}, cqa-toggle-expand {index, expanded} */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, U = CQA.util;

  var TYPES = ['yes_no', 'scale', 'flag'];
  var ROLES = ['operator', 'client', 'bot', 'system'];
  var CHANNELS = ['agent chat', 'voice call'];

  function lines(v) { return (v || []).join('\n'); }
  function parseLines(s) { return s.split('\n').map(function (x) { return x.trim(); }).filter(Boolean); }

  class CqaCriterionEditor extends HTMLElement {
    set data(d) {
      this._d = d;
      this.c = d.crit;
      this.render();
    }

    get index() { return this._d.index; }
    loc(f) { return 'criteria.' + this._d.index + (f ? '.' + f : ''); }

    changed(rerenderBody) {
      this.renderHead();
      if (rerenderBody && this._d.expanded) this.renderBody();
      this.dispatchEvent(new CustomEvent('cqa-change', { bubbles: true, detail: { index: this._d.index } }));
    }

    render() {
      this.className = 'ced' + (this._d.expanded ? ' is-expanded' : '') + (this.c.enabled === false ? ' is-disabled' : '');
      this.textContent = '';
      this.head = h('div', { class: 'ced__head' });
      this.bodyEl = h('div', { class: 'ced__body', id: 'ced-body-' + this._d.index });
      this.append(this.head, this.bodyEl);
      this.renderHead();
      if (this._d.expanded) this.renderBody();
    }

    status() {
      var base = this._d.base;
      if (!base) return 'added';
      return U.sameJson(base, this.c) ? null : 'changed';
    }

    renderHead() {
      // шапка перерисовывается при каждом изменении — возвращаем фокус клавиатуры на тот же элемент
      var ae = document.activeElement;
      var refocus = this.head && this.head.contains(ae) ? (ae.matches('input') ? 'input' : ae.classList.contains('ced__toggle') ? '.ced__toggle' : ae.classList.contains('ced__del') ? '.ced__del' : null) : null;
      this._renderHead();
      if (refocus) { var el = this.head.querySelector(refocus); if (el) el.focus(); }
    }

    _renderHead() {
      var self = this, c = this.c, d = this._d;
      var sec = (d.sections || []).filter(function (s) { return s.id === c.section; })[0];
      var st = this.status();
      var enabled = h('input', { type: 'checkbox', checked: c.enabled !== false, 'aria-label': t('ced.enabledAria', { id: c.id }) });
      enabled.addEventListener('change', function () { c.enabled = enabled.checked; self.classList.toggle('is-disabled', !enabled.checked); self.changed(false); });
      var toggle = h('button', { class: 'ced__toggle', type: 'button', 'aria-expanded': String(!!d.expanded), 'aria-controls': 'ced-body-' + d.index },
        icon(d.expanded ? 'chevronDown' : 'chevronRight', 'icon--16'),
        h('span', { class: 'ced__id mono' }, c.id || '—'),
        h('span', { class: 'ced__title' }, c.title || t('ced.untitled')));
      toggle.addEventListener('click', function () { self.setExpanded(!self._d.expanded); });
      CQA.dom.replace(this.head,
        h('label', { class: 'switch', title: t('ced.enabled') }, enabled),
        toggle,
        h('span', { class: 'ced__meta' },
          sec ? h('span', { class: 'badge badge--outline ced__sec' }, sec.title) : CQA.ui.badge(t('ced.noSection'), 'danger'),
          h('span', { class: 'badge badge--neutral' }, t('ctype.' + c.type)),
          c.type !== 'flag' ? h('span', { class: 'badge badge--brand num' }, t('ced.weightShort', { w: fmt.num(Number(c.weight) || 0) })) : null,
          c.na_allowed ? h('span', { class: 'badge badge--neutral' }, 'N/A') : null,
          st ? CQA.ui.badge(t('ced.status.' + st), st === 'added' ? 'success' : 'warning') : null,
          c.enabled === false ? CQA.ui.badge(t('ced.off'), 'neutral') : null),
        h('button', { class: 'btn btn--subtle btn--icon btn--small ced__del', type: 'button', 'aria-label': t('ced.deleteAria', { id: c.id }), title: t('ced.delete'),
          onClick: function () { self.dispatchEvent(new CustomEvent('cqa-delete', { bubbles: true, detail: { index: d.index } })); } }, icon('delete')));
    }

    setExpanded(on) {
      this._d.expanded = on;
      this.classList.toggle('is-expanded', on);
      this.renderHead();
      if (on) this.renderBody(); else this.bodyEl.textContent = '';
      this.dispatchEvent(new CustomEvent('cqa-toggle-expand', { bubbles: true, detail: { index: this._d.index, expanded: on } }));
    }

    // ------------------------------------------------------------ поля
    field(label, control, opts) {
      opts = opts || {};
      var id = control.id || (control.id = CQA.dom.uid('f'));
      var err = h('div', { class: 'field__error', id: id + '-err', 'aria-live': 'polite' });
      var describedBy = [opts.hint ? id + '-hint' : null, id + '-err'].filter(Boolean).join(' ');
      if (control.setAttribute && /^(INPUT|SELECT|TEXTAREA)$/.test(control.tagName)) control.setAttribute('aria-describedby', describedBy);
      return h('div', { class: ['field', opts.cls], 'data-loc': opts.loc },
        h(opts.group ? 'span' : 'label', { class: 'field__label', for: opts.group ? null : id, id: opts.group ? id + '-label' : null }, label,
          opts.required ? h('span', { class: 'req', 'aria-hidden': 'true' }, '*') : null),
        opts.hint ? h('div', { class: 'field__hint', id: id + '-hint' }, opts.hint) : null,
        control, err);
    }

    input(key, opts) {
      var self = this, c = this.c;
      opts = opts || {};
      var el = h(opts.multiline ? 'textarea' : 'input', {
        class: opts.multiline ? 'textarea' : 'input' + (opts.num ? ' input--num' : ''),
        type: opts.multiline ? null : (opts.num ? 'number' : 'text'),
        min: opts.num ? '0' : null, step: opts.num ? 'any' : null,
        rows: opts.rows || null, value: opts.lines ? lines(c[key]) : (c[key] == null ? '' : String(c[key])),
        disabled: opts.disabled || null, placeholder: opts.placeholder || null, spellcheck: opts.mono ? 'false' : null
      });
      el.addEventListener('input', function () {
        if (opts.lines) c[key] = parseLines(el.value);
        else if (opts.num) c[key] = el.value === '' ? 0 : Number(el.value);
        else if (opts.nullable) c[key] = el.value.trim() ? el.value : null;
        else c[key] = el.value;
        self.clearError(el);
        self.changed(false);
      });
      return el;
    }

    renderBody() {
      var self = this, c = this.c, d = this._d;
      var body = this.bodyEl;
      body.textContent = '';

      var sectionSel = h('select', { class: 'select' }, (d.sections || []).map(function (s) {
        return h('option', { value: s.id, selected: s.id === c.section }, s.title + ' (' + s.id + ')');
      }));
      if (!(d.sections || []).some(function (s) { return s.id === c.section; })) sectionSel.insertBefore(h('option', { value: c.section || '', selected: true }, c.section || '—'), sectionSel.firstChild);
      sectionSel.addEventListener('change', function () { c.section = sectionSel.value; self.clearError(sectionSel); self.changed(false); });

      var typeSel = h('select', { class: 'select' }, TYPES.map(function (tp) { return h('option', { value: tp, selected: tp === c.type }, t('ctype.' + tp)); }));
      typeSel.addEventListener('change', function () {
        c.type = typeSel.value;
        if (c.type === 'flag') { c.weight = 0; if (!c.flag_categories) c.flag_categories = []; }
        if (c.type === 'scale' && (!c.scale || !c.scale.length)) c.scale = [{ value: 10, text: '' }, { value: 5, text: '' }, { value: 0, text: '' }];
        if (c.type !== 'scale') c.scale = null;
        self.fixExamples();
        self.changed(true);
      });

      var naSwitch = h('input', { type: 'checkbox', checked: !!c.na_allowed });
      naSwitch.addEventListener('change', function () { c.na_allowed = naSwitch.checked; if (!c.na_allowed) c.na_condition = null; self.fixExamples(); self.changed(true); });
      var crossSwitch = h('input', { type: 'checkbox', checked: !!c.cross_check });
      crossSwitch.addEventListener('change', function () { c.cross_check = crossSwitch.checked; self.changed(false); });

      var grid = h('div', { class: 'ced__grid' },
        this.field(t('field.id'), this.input('id', { mono: true }), { loc: this.loc('id'), required: true, cls: 'ced__f-id' }),
        this.field(t('field.title'), this.input('title'), { loc: this.loc('title'), required: true, cls: 'ced__f-title' }),
        this.field(t('field.section'), sectionSel, { loc: this.loc('section'), cls: 'ced__f-section' }),
        this.field(t('field.type'), typeSel, { loc: this.loc('type'), cls: 'ced__f-type' }),
        this.field(t('field.weight'), this.input('weight', { num: true, disabled: c.type === 'flag' }), { loc: this.loc('weight'), hint: c.type === 'flag' ? t('ced.flagWeightHint') : null, cls: 'ced__f-weight' }));
      body.appendChild(grid);

      body.appendChild(h('div', { class: 'row ced__switches' },
        h('label', { class: 'switch' }, naSwitch, t('field.na_allowed')),
        h('label', { class: 'switch' }, crossSwitch, t('field.cross_check'))));

      if (c.na_allowed) body.appendChild(this.field(t('field.na_condition'), this.input('na_condition', { multiline: true, rows: 2, nullable: true }), { loc: this.loc('na_condition'), hint: t('ced.naHint') }));

      if (c.type === 'scale') body.appendChild(this.scaleEditor());
      if (c.type === 'flag') {
        var cats = h('input', { class: 'input', value: (c.flag_categories || []).join(', '), placeholder: t('ced.categoriesPlaceholder') });
        cats.addEventListener('input', function () { c.flag_categories = cats.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean); self.clearError(cats); self.changed(false); });
        body.appendChild(this.field(t('field.flag_categories'), cats, { loc: this.loc('flag_categories'), hint: t('ced.categoriesHint') }));
      }

      body.appendChild(this.field(t('field.definition'), this.input('definition', { multiline: true, rows: 3 }), { loc: this.loc('definition'), required: true, hint: t('ced.definitionHint') }));
      body.appendChild(this.field(t('field.check_steps'), this.input('check_steps', { multiline: true, rows: 3, lines: true }), { loc: this.loc('check_steps'), hint: t('ced.linesHint') }));
      body.appendChild(this.field(t('field.interpretations'), this.input('interpretations', { multiline: true, rows: 3, lines: true }), { loc: this.loc('interpretations'), hint: t('ced.linesHint') }));
      body.appendChild(this.examplesEditor());

      // роли доказательств и каналы
      var roles = h('div', { class: 'row', role: 'group' }, ROLES.map(function (r) {
        var cb = h('input', { type: 'checkbox', checked: (c.evidence_roles || []).indexOf(r) >= 0 });
        cb.addEventListener('change', function () {
          var set = (c.evidence_roles || []).filter(function (x) { return x !== r; });
          if (cb.checked) set.push(r);
          c.evidence_roles = ROLES.filter(function (x) { return set.indexOf(x) >= 0; });
          self.clearError(roles);
          self.changed(false);
        });
        return h('label', { class: 'check' }, cb, t('role.' + r));
      }));
      var chans = h('div', { class: 'row', role: 'group' }, CHANNELS.map(function (ch) {
        var cb = h('input', { type: 'checkbox', checked: !!(c.channels && c.channels.indexOf(ch) >= 0) });
        cb.addEventListener('change', function () {
          var set = (c.channels || []).filter(function (x) { return x !== ch; });
          if (cb.checked) set.push(ch);
          c.channels = set.length ? CHANNELS.filter(function (x) { return set.indexOf(x) >= 0; }) : null;
          self.changed(false);
        });
        return h('label', { class: 'check' }, cb, U.channelLabel(ch));
      }));
      var rolesField = this.field(t('field.evidence_roles'), roles, { loc: this.loc('evidence_roles'), group: true, hint: t('ced.rolesHint') });
      roles.setAttribute('aria-labelledby', roles.id + '-label');
      var chansField = this.field(t('field.channels'), chans, { loc: this.loc('channels'), group: true, hint: t('ced.channelsHint') });
      chans.setAttribute('aria-labelledby', chans.id + '-label');
      body.appendChild(h('div', { class: 'ced__grid2' }, rolesField, chansField));
    }

    scaleEditor() {
      var self = this, c = this.c;
      var list = h('div', { class: 'ced__list' });
      (c.scale || []).forEach(function (s, j) {
        var v = h('input', { class: 'input input--num', type: 'number', step: '1', value: String(s.value), 'aria-label': t('ced.stepValue', { n: j + 1 }) });
        var tx = h('input', { class: 'input', value: s.text || '', 'aria-label': t('ced.stepText', { n: j + 1 }), placeholder: t('ced.stepTextPlaceholder') });
        v.addEventListener('input', function () { s.value = v.value === '' ? '' : Number(v.value); self.clearError(v); self.fixExamples(); self.changed(false); });
        tx.addEventListener('input', function () { s.text = tx.value; self.clearError(tx); self.changed(false); });
        list.appendChild(h('div', { class: 'ced__step' },
          h('div', { class: 'field', 'data-loc': self.loc('scale.' + j + '.value') }, v, h('div', { class: 'field__error' })),
          h('div', { class: 'field grow', 'data-loc': self.loc('scale.' + j + '.text') }, tx, h('div', { class: 'field__error' })),
          h('button', { class: 'btn btn--subtle btn--icon btn--small', type: 'button', 'aria-label': t('ced.removeStep', { n: j + 1 }), disabled: c.scale.length <= 1,
            onClick: function () { c.scale.splice(j, 1); self.fixExamples(); self.changed(true); } }, icon('delete'))));
      });
      var add = h('button', { class: 'btn btn--small', type: 'button', onClick: function () {
        var min = (c.scale || []).reduce(function (a, s) { return Math.min(a, Number(s.value) || 0); }, Infinity);
        c.scale = (c.scale || []).concat([{ value: isFinite(min) ? Math.max(0, min - 1) : 0, text: '' }]);
        self.changed(true);
        var inputs = self.querySelectorAll('.ced__step input[type=number]');
        if (inputs.length) inputs[inputs.length - 1].focus();
      } }, icon('add'), t('ced.addStep'));
      return h('div', { class: 'field', 'data-loc': this.loc('scale') },
        h('span', { class: 'field__label' }, t('field.scale')),
        h('div', { class: 'field__hint' }, t('ced.scaleHint')),
        list, h('div', { class: 'field__error' }), h('div', null, add));
    }

    allowed() { return CQA.scoring.allowedVerdicts(this.c); }

    /** Примеры с вердиктом, которого больше нет среди допустимых, переводим на первый допустимый. */
    fixExamples() {
      var allowed = this.allowed();
      (this.c.examples || []).forEach(function (ex) { if (allowed.indexOf(ex.verdict) < 0) ex.verdict = allowed[0]; });
    }

    examplesEditor() {
      var self = this, c = this.c;
      var allowed = this.allowed();
      var list = h('div', { class: 'ced__examples' });
      (c.examples || []).forEach(function (ex, j) {
        var sel = h('select', { class: 'select select--small', 'aria-label': t('ced.exVerdict', { n: j + 1 }) },
          allowed.map(function (v) { return h('option', { value: v, selected: v === ex.verdict }, U.verdictLabel(v, c.type, CQA.scoring.top(c))); }));
        if (allowed.indexOf(ex.verdict) < 0) sel.insertBefore(h('option', { value: ex.verdict, selected: true }, ex.verdict + ' ⚠'), sel.firstChild);
        sel.addEventListener('change', function () { ex.verdict = sel.value; self.clearError(sel); self.changed(false); });
        var frag = h('textarea', { class: 'textarea', rows: '2', value: ex.fragment || '', 'aria-label': t('ced.exFragment', { n: j + 1 }), placeholder: t('ced.exFragmentPlaceholder') });
        frag.addEventListener('input', function () { ex.fragment = frag.value; self.clearError(frag); self.changed(false); });
        var why = h('input', { class: 'input', value: ex.why || '', 'aria-label': t('ced.exWhy', { n: j + 1 }), placeholder: t('ced.exWhyPlaceholder') });
        why.addEventListener('input', function () { ex.why = why.value; self.changed(false); });
        list.appendChild(h('div', { class: 'ced__example' },
          h('div', { class: 'ced__example-head' },
            h('div', { class: 'field', 'data-loc': self.loc('examples.' + j + '.verdict') }, sel, h('div', { class: 'field__error' })),
            h('span', { class: 'muted t-caption1' }, t('ced.exampleN', { n: j + 1 })),
            h('span', { class: 'spacer' }),
            h('button', { class: 'btn btn--subtle btn--icon btn--small', type: 'button', 'aria-label': t('ced.removeExample', { n: j + 1 }),
              onClick: function () { c.examples.splice(j, 1); self.changed(true); } }, icon('delete'))),
          h('div', { class: 'field', 'data-loc': self.loc('examples.' + j + '.fragment') }, frag, h('div', { class: 'field__error' })),
          h('div', { class: 'field', 'data-loc': self.loc('examples.' + j + '.why') }, why, h('div', { class: 'field__error' }))));
      });
      var add = h('button', { class: 'btn btn--small', type: 'button', onClick: function () {
        c.examples = (c.examples || []).concat([{ verdict: allowed[0], fragment: '', why: '' }]);
        self.changed(true);
        var ta = self.querySelectorAll('.ced__example textarea');
        if (ta.length) ta[ta.length - 1].focus();
      } }, icon('add'), t('ced.addExample'));
      return h('div', { class: 'field', 'data-loc': this.loc('examples') },
        h('span', { class: 'field__label' }, t('field.examples')),
        h('div', { class: 'field__hint' }, t('ced.examplesHint')),
        list, h('div', { class: 'field__error' }), h('div', null, add));
    }

    // ------------------------------------------------------------ ошибки
    clearError(control) {
      var f = control.closest('[data-loc]');
      if (!f) return;
      var e = f.querySelector(':scope > .field__error');
      if (e) e.textContent = '';
      control.removeAttribute('aria-invalid');
    }

    /** errs: [{loc: 'criteria.3.weight', msg}] — только свои; разворачивает форму, если есть ошибки. */
    showErrors(errs) {
      if (!errs.length) return;
      if (!this._d.expanded) this.setExpanded(true);
      var self = this;
      errs.forEach(function (e) {
        var f = self.findField(e.loc);
        if (!f) { self.head.classList.add('has-error'); return; }
        var box = f.querySelector(':scope > .field__error');
        if (box) CQA.dom.replace(box, icon('error'), e.msg);
        var ctl = f.querySelector('input, select, textarea');
        if (ctl) ctl.setAttribute('aria-invalid', 'true');
      });
      this.head.classList.add('has-error');
    }

    findField(loc) {
      var parts = loc.split('.');
      while (parts.length > 2) {
        var f = this.querySelector('[data-loc="' + parts.join('.') + '"]');
        if (f) return f;
        parts.pop();
      }
      return null;
    }

    clearAllErrors() {
      this.querySelectorAll('.field__error').forEach(function (e) { e.textContent = ''; });
      this.querySelectorAll('[aria-invalid]').forEach(function (e) { e.removeAttribute('aria-invalid'); });
      if (this.head) this.head.classList.remove('has-error');
    }
  }

  customElements.define('cqa-criterion-editor', CqaCriterionEditor);
})(window.CQA = window.CQA || {});
