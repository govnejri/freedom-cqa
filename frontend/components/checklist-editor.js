/* <cqa-checklist-editor> — редактор конфигурации чек-листа: общие правила, секции, критерии, сумма весов.
   .data = {config (черновик, изменяется на месте), base (конфиг версии), expanded: Set<id>}
   .showErrors([{loc, msg}]) · события cqa-change */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui;

  function nextId(criteria) {
    var max = 0;
    criteria.forEach(function (c) { var n = parseInt(c.id, 10); if (!isNaN(n)) max = Math.max(max, n); });
    var s = String(max + 1);
    return s.length < 2 ? '0' + s : s;
  }

  class CqaChecklistEditor extends HTMLElement {
    set data(d) {
      this._d = d;
      this.cfg = d.config;
      this.expanded = d.expanded || new Set();
      this.render();
    }

    connectedCallback() {
      if (this._bound) return;
      this._bound = true;
      var self = this;
      this.addEventListener('cqa-change', function (e) { self.updateWeights(); if (e.target !== self) self.syncExpanded(); });
      this.addEventListener('cqa-delete', function (e) { self.removeCriterion(e.detail.index); });
      this.addEventListener('cqa-toggle-expand', function (e) {
        var c = self.cfg.criteria[e.detail.index];
        if (!c) return;
        if (e.detail.expanded) self.expanded.add(c.id); else self.expanded.delete(c.id);
      });
    }

    emit() { this.dispatchEvent(new CustomEvent('cqa-change', { bubbles: true })); }

    render() {
      var self = this, cfg = this.cfg;
      this.textContent = '';
      this.classList.add('cl-editor');

      // ---- общие правила
      var rules = h('textarea', { class: 'textarea', id: 'cl-general', rows: '8', value: cfg.general_rules || '' });
      rules.addEventListener('input', function () { cfg.general_rules = rules.value; self.emit(); });
      var principles = h('textarea', { class: 'textarea', id: 'cl-principles', rows: '5', value: cfg.principles || '' });
      principles.addEventListener('input', function () { cfg.principles = principles.value; self.emit(); });
      var title = h('input', { class: 'input', id: 'cl-title', value: cfg.title || '' });
      title.addEventListener('input', function () { cfg.title = title.value; self.emit(); });
      var channelRules = Object.keys(cfg.channel_rules || {}).map(function (ch) {
        var ta = h('textarea', { class: 'textarea', rows: '4', id: 'cl-ch-' + ch.replace(/\W/g, '-'), value: cfg.channel_rules[ch] || '' });
        ta.addEventListener('input', function () { cfg.channel_rules[ch] = ta.value; self.emit(); });
        return h('div', { class: 'field', 'data-loc': 'channel_rules.' + ch },
          h('label', { class: 'field__label', for: ta.id }, t('cl.channelRules', { ch: CQA.util.channelLabel(ch) })), ta);
      });

      var general = h('section', { class: 'card stack stack--l', 'aria-labelledby': 'cl-general-h' },
        h('h2', { class: 'card__title', id: 'cl-general-h' }, t('cl.generalTitle')),
        h('div', { class: 'field', 'data-loc': 'title' }, h('label', { class: 'field__label', for: 'cl-title' }, t('cl.title')), title, h('div', { class: 'field__error' })),
        h('div', { class: 'field', 'data-loc': 'general_rules' },
          h('label', { class: 'field__label', for: 'cl-general' }, t('cl.generalRules')),
          h('div', { class: 'field__hint' }, t('cl.generalRulesHint')),
          rules, h('div', { class: 'field__error' })),
        h('details', { class: 'disclosure' },
          h('summary', null, icon('chevronRight'), t('cl.moreRules')),
          h('div', { class: 'disclosure__body stack stack--l' },
            h('div', { class: 'field', 'data-loc': 'principles' },
              h('label', { class: 'field__label', for: 'cl-principles' }, t('cl.principles')), principles),
            channelRules)));

      // ---- секции
      this.sectionsBox = h('div', { class: 'cl-sections' });
      var sections = h('section', { class: 'card', 'aria-labelledby': 'cl-sections-h' },
        h('div', { class: 'card__header' },
          h('h2', { class: 'card__title', id: 'cl-sections-h' }, t('cl.sections')),
          h('button', { class: 'btn btn--small', type: 'button', onClick: function () { self.addSection(); } }, icon('add'), t('cl.addSection'))),
        this.sectionsBox);

      // ---- критерии
      this.weightEl = h('div', { class: 'cl-weight', 'aria-live': 'polite' });
      this.critList = h('div', { class: 'cl-criteria' });
      this.critError = h('div', { class: 'field__error', 'data-loc-target': 'criteria' });
      var criteria = h('section', { class: 'card', 'aria-labelledby': 'cl-crit-h' },
        h('div', { class: 'card__header' },
          h('div', { class: 'row' }, h('h2', { class: 'card__title', id: 'cl-crit-h' }, t('cl.criteria')), this.countEl = h('span', { class: 'badge badge--neutral' })),
          h('div', { class: 'row' },
            h('button', { class: 'btn btn--small', type: 'button', onClick: function () { self.expandAll(); } }, t('cl.expandAll')),
            h('button', { class: 'btn btn--primary btn--small', type: 'button', onClick: function () { self.addCriterion(); } }, icon('add'), t('cl.addCriterion')))),
        this.weightEl, this.critError, this.critList);

      this.append(general, sections, criteria);
      this.renderSections();
      this.renderCriteria();
    }

    // ------------------------------------------------------------ секции
    renderSections() {
      var self = this, cfg = this.cfg;
      this.sectionsBox.textContent = '';
      var used = {};
      cfg.criteria.forEach(function (c) { used[c.section] = (used[c.section] || 0) + 1; });
      cfg.sections.forEach(function (s, i) {
        var id = h('input', { class: 'input input--small mono', value: s.id, 'aria-label': t('cl.sectionId', { n: i + 1 }), spellcheck: 'false' });
        var title = h('input', { class: 'input input--small', value: s.title, 'aria-label': t('cl.sectionTitle', { n: i + 1 }) });
        id.addEventListener('change', function () {
          var old = s.id;
          s.id = id.value.trim();
          cfg.criteria.forEach(function (c) { if (c.section === old) c.section = s.id; });
          self.emit(); self.renderCriteria();
        });
        title.addEventListener('input', function () { s.title = title.value; self.emit(); });
        title.addEventListener('change', function () { self.renderCriteria(); });
        self.sectionsBox.appendChild(h('div', { class: 'cl-section' },
          h('div', { class: 'field', 'data-loc': 'sections.' + i + '.id' }, id, h('div', { class: 'field__error' })),
          h('div', { class: 'field grow', 'data-loc': 'sections.' + i + '.title' }, title, h('div', { class: 'field__error' })),
          h('span', { class: 'muted t-caption1 nowrap' }, t('cl.sectionUsed', { n: used[s.id] || 0 })),
          h('button', { class: 'btn btn--subtle btn--icon btn--small', type: 'button', disabled: !!used[s.id],
            'aria-label': t('cl.removeSection', { id: s.id }), title: used[s.id] ? t('cl.sectionInUse') : t('cl.removeSection', { id: s.id }),
            onClick: function () { cfg.sections.splice(i, 1); self.emit(); self.renderSections(); } }, icon('delete'))));
      });
    }

    addSection() {
      var n = this.cfg.sections.length + 1;
      this.cfg.sections.push({ id: 'section' + n, title: t('cl.newSection') });
      this.emit();
      this.renderSections();
      var inputs = this.sectionsBox.querySelectorAll('input');
      if (inputs.length) inputs[inputs.length - 1].select();
    }

    // ------------------------------------------------------------ критерии
    renderCriteria() {
      var self = this, cfg = this.cfg, base = this._d.base;
      var baseById = {};
      ((base && base.criteria) || []).forEach(function (c) { baseById[c.id] = c; });
      this.critList.textContent = '';
      cfg.criteria.forEach(function (c, i) {
        var ed = document.createElement('cqa-criterion-editor');
        ed.data = { crit: c, index: i, sections: cfg.sections, base: baseById[c.id] || null, expanded: self.expanded.has(c.id) };
        self.critList.appendChild(ed);
      });
      var removed = ((base && base.criteria) || []).filter(function (b) { return !cfg.criteria.some(function (c) { return c.id === b.id; }); });
      if (removed.length) {
        this.critList.appendChild(h('div', { class: 'cl-removed t-caption1' }, icon('delete', 'icon--16'),
          t('cl.removedList', { list: removed.map(function (c) { return c.id + ' ' + c.title; }).join('; ') })));
      }
      this.updateWeights();
    }

    /** id критерия могли поменять — множество развёрнутых пересобираем по DOM (тот же объект Set, что у страницы). */
    syncExpanded() {
      if (!this.critList) return;
      var set = this.expanded;
      set.clear();
      this.critList.querySelectorAll('cqa-criterion-editor.is-expanded').forEach(function (e) { if (e.c && e.c.id) set.add(e.c.id); });
    }

    updateWeights() {
      var cfg = this.cfg;
      var enabled = cfg.criteria.filter(function (c) { return c.enabled !== false; });
      var sum = enabled.reduce(function (a, c) { return a + (c.type === 'flag' ? 0 : Number(c.weight) || 0); }, 0);
      sum = Math.round(sum * 100) / 100;
      this.countEl.textContent = t('cl.count', { n: cfg.criteria.length, e: enabled.length });
      var ok = Math.abs(sum - 100) < 0.005;
      CQA.dom.replace(this.weightEl, ok
        ? h('div', { class: 'cl-weight__ok row' }, icon('checkCircle', 'icon--16'), t('cl.weightSum', { s: fmt.num(sum) }))
        : ui.msgbar('warning', t('cl.weightSumBad', { s: fmt.num(sum) }), t('cl.weightSumHint')));
    }

    addCriterion() {
      var cfg = this.cfg;
      var c = {
        id: nextId(cfg.criteria), title: '', section: (cfg.sections[0] || {}).id || '', type: 'yes_no',
        na_allowed: false, weight: 0, scale: null, definition: '', na_condition: null,
        check_steps: [], interpretations: [], examples: [], evidence_roles: ['operator'],
        channels: null, flag_categories: [], cross_check: false, enabled: true
      };
      cfg.criteria.push(c);
      this.expanded.add(c.id);
      this.emit();
      this.renderCriteria();
      var eds = this.critList.querySelectorAll('cqa-criterion-editor');
      var last = eds[eds.length - 1];
      if (last) {
        CQA.dom.scrollIntoPane(last, { block: 'start' });
        var title = last.querySelector('[data-loc$=".title"] input');
        if (title) title.focus({ preventScroll: true });
      }
    }

    removeCriterion(i) {
      var self = this, c = this.cfg.criteria[i];
      if (!c) return;
      ui.confirm(t('cl.deleteTitle', { id: c.id }), t('cl.deleteText', { title: c.title || c.id }), t('ced.delete'), true).then(function (ok) {
        if (!ok) return;
        self.cfg.criteria.splice(i, 1);
        self.expanded.delete(c.id);
        self.emit();
        self.renderCriteria();
        self.renderSections();
        ui.toast(t('cl.deleted', { id: c.id }), { tone: 'info' });
      });
    }

    expandAll() {
      var eds = this.critList.querySelectorAll('cqa-criterion-editor');
      var anyCollapsed = Array.prototype.some.call(eds, function (e) { return !e.classList.contains('is-expanded'); });
      eds.forEach(function (e) { e.setExpanded(anyCollapsed); });
    }

    // ------------------------------------------------------------ ошибки
    clearErrors() {
      this.querySelectorAll('cqa-criterion-editor').forEach(function (e) { e.clearAllErrors(); });
      this.querySelectorAll(':scope > section .field__error').forEach(function (e) { e.textContent = ''; });
      this.querySelectorAll('[aria-invalid]').forEach(function (e) { e.removeAttribute('aria-invalid'); });
    }

    /** Показать ошибки у полей. Возвращает первый элемент с ошибкой (для прокрутки). */
    showErrors(errs) {
      var self = this, first = null;
      var eds = this.critList.querySelectorAll('cqa-criterion-editor');
      var byIdx = {};
      errs.forEach(function (e) {
        // индекс pydantic (criteria.3.weight) — число без ведущего нуля; иначе это id критерия (criteria.08.weight)
        var m = /^criteria\.(0|[1-9]\d*)(\.|$)/.exec(e.loc);
        if (!m) {
          var idm = /^criteria\.([^.]+)(\.|$)/.exec(e.loc);
          if (idm) {
            var idx = self.cfg.criteria.findIndex(function (c) { return c.id === idm[1]; });
            if (idx >= 0) { e = { loc: e.loc.replace('criteria.' + idm[1], 'criteria.' + idx), msg: e.msg }; m = [null, String(idx)]; }
          }
        }
        if (m) { (byIdx[m[1]] = byIdx[m[1]] || []).push(e); return; }
        var f = self.querySelector('[data-loc="' + e.loc + '"]') || (e.loc === 'criteria' ? null : self.querySelector('[data-loc="' + e.loc.split('.')[0] + '"]'));
        var box = f ? f.querySelector(':scope > .field__error') : (e.loc === 'criteria' ? self.critError : null);
        if (box) { CQA.dom.replace(box, icon('error'), e.msg); first = first || (f || box); }
      });
      Object.keys(byIdx).forEach(function (i) {
        var ed = eds[Number(i)];
        if (ed) { ed.showErrors(byIdx[i]); first = first || ed; }
      });
      return first;
    }
  }

  customElements.define('cqa-checklist-editor', CqaChecklistEditor);
})(window.CQA = window.CQA || {});
