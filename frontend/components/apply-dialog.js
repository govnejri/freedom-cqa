/* <cqa-apply-dialog> — «Применить к диалогам»: выбор набора или диалогов и критериев (по умолчанию — изменённые
   относительно активной версии) → POST /runs → #/runs/{id}.
   CQA.openApplyDialog({version, active, baseline}) — ChecklistVersionDTO; baseline — с чем сравнивать критерии */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui;

  class CqaApplyDialog extends HTMLElement {
    set data(d) { this._d = d; this.render(); }

    render() {
      var self = this, d = this._d;
      var cfg = d.version.config;
      var enabled = cfg.criteria.filter(function (c) { return c.enabled !== false; });
      // базой сравнения служит активная версия, а если применяем саму активную — предыдущая (d.baseline)
      var baseline = d.baseline || (d.active && d.active.id !== d.version.id ? d.active : null);
      var changed = baseline && baseline.config ? CQA.checklistChangedIds(baseline.config, cfg) : [];
      changed = changed.filter(function (id) { return enabled.some(function (c) { return c.id === id; }); });
      this.changed = changed;
      this.textContent = '';
      this.classList.add('stack', 'stack--l', 'apply');

      // ---- что оценивать
      this.scopeGroup = h('input', { type: 'radio', name: 'apply-scope', value: 'group', checked: true });
      this.scopeUids = h('input', { type: 'radio', name: 'apply-scope', value: 'uids' });
      this.groupSel = h('select', { class: 'select', 'aria-label': t('apply.group') }, h('option', { value: '' }, t('dash.allGroups')));
      this.convBox = h('div', { class: 'apply-convs', hidden: true }, ui.skeleton('text', 3));
      this.convSearch = h('input', { class: 'input input--small', type: 'search', placeholder: t('apply.searchConv'), 'aria-label': t('apply.searchConv') });
      var syncScope = function () {
        var uids = self.scopeUids.checked;
        self.convBox.hidden = !uids;
        self.groupSel.disabled = uids;
        if (uids && !self.convs) self.loadConversations();
      };
      this.scopeGroup.addEventListener('change', syncScope);
      this.scopeUids.addEventListener('change', syncScope);

      // ---- критерии
      this.critChecks = {};
      var critList = h('div', { class: 'apply-crit' }, enabled.map(function (c) {
        var cb = h('input', { type: 'checkbox', checked: changed.length ? changed.indexOf(c.id) >= 0 : true, value: c.id });
        self.critChecks[c.id] = cb;
        return h('label', { class: 'check check--small' }, cb, h('span', { class: 'mono' }, c.id), ' ', c.title,
          changed.indexOf(c.id) >= 0 ? CQA.ui.badge(t('ced.status.changed'), 'warning') : null);
      }));
      var setAll = function (ids) { Object.keys(self.critChecks).forEach(function (id) { self.critChecks[id].checked = !ids || ids.indexOf(id) >= 0; }); };
      this.force = h('input', { type: 'checkbox' });
      this.error = h('div', { 'aria-live': 'assertive' });

      CQA.dom.append(this, [
        h('div', { class: 'muted' }, t('apply.intro', { v: d.version.version })),
        d.active && d.active.id !== d.version.id ? ui.msgbar('warning', t('apply.notActive', { v: d.version.version, a: d.active.version }), t('apply.notActiveHint')) : null,
        h('fieldset', { class: 'stack' },
          h('legend', { class: 'field__label' }, t('apply.scope')),
          h('label', { class: 'check' }, this.scopeGroup, t('apply.scopeGroup'), this.groupSel),
          h('label', { class: 'check' }, this.scopeUids, t('apply.scopeUids')),
          this.convBox),
        h('fieldset', { class: 'stack' },
          h('legend', { class: 'field__label' }, t('apply.criteria')),
          h('div', { class: 'field__hint' }, changed.length ? t('apply.criteriaChanged', { list: changed.join(', '), v: baseline.version }) : t('apply.criteriaAll')),
          h('div', { class: 'row' },
            changed.length ? h('button', { class: 'btn btn--small', type: 'button', onClick: function () { setAll(changed); } }, t('apply.onlyChanged')) : null,
            h('button', { class: 'btn btn--small', type: 'button', onClick: function () { setAll(null); } }, t('apply.all'))),
          critList),
        h('label', { class: 'check check--small' }, this.force, t('dash.force')),
        this.error]);

      CQA.api.datasets().then(function (list) {
        (list || []).forEach(function (ds) { self.groupSel.appendChild(h('option', { value: ds.group_id }, ds.group_id + ' · ' + t('dash.records', { n: ds.total_records }))); });
        if (list && list.length === 1) self.groupSel.value = list[0].group_id;
      }).catch(function () {});
    }

    loadConversations() {
      var self = this;
      CQA.api.conversations({}).then(function (list) {
        self.convs = list;
        self.selectedUids = self.selectedUids || new Set();
        var render = function () {
          var q = self.convSearch.value.trim().toLowerCase();
          var rows = list.filter(function (s) { return !q || s.external_id.toLowerCase().indexOf(q) >= 0; });
          CQA.dom.replace(self.listEl, rows.map(function (s) {
            var cb = h('input', { type: 'checkbox', checked: self.selectedUids.has(s.uid) });
            cb.addEventListener('change', function () { if (cb.checked) self.selectedUids.add(s.uid); else self.selectedUids.delete(s.uid); self.countEl.textContent = t('apply.selected', { n: self.selectedUids.size }); });
            return h('label', { class: 'check check--small' }, cb, h('span', { class: 'mono' }, s.external_id), h('span', { class: 'muted' }, s.language + ' · ' + CQA.util.channelLabel(s.channel)));
          }));
        };
        self.listEl = h('div', { class: 'apply-convs__list' });
        self.countEl = h('span', { class: 'muted t-caption1' }, t('apply.selected', { n: self.selectedUids.size }));
        self.convSearch.addEventListener('input', render);
        CQA.dom.replace(self.convBox, h('div', { class: 'row' }, self.convSearch, self.countEl), self.listEl);
        render();
      }, function (err) {
        CQA.dom.replace(self.convBox, ui.error(err, function () { self.loadConversations(); }));
      });
    }

    value() {
      var crit = Object.keys(this.critChecks).filter(function (id) { return this.critChecks[id].checked; }, this);
      var all = crit.length === Object.keys(this.critChecks).length;
      var dto = { checklist_version_id: this._d.version.id, criteria: all ? null : crit, force: this.force.checked, kind: 'batch' };
      if (this.scopeUids.checked) dto.uids = Array.from(this.selectedUids || []);
      else dto.group = this.groupSel.value || null;
      return dto;
    }

    validate(dto) {
      CQA.dom.clear(this.error);
      var msg = null;
      if (dto.criteria && !dto.criteria.length) msg = t('apply.noCriteria');
      else if (this.scopeUids.checked && !dto.uids.length) msg = t('apply.noConvs');
      if (msg) this.error.appendChild(ui.msgbar('danger', msg));
      return !msg;
    }

    showError(err) { CQA.dom.replace(this.error, ui.msgbar('danger', t('run.createFailed'), err.message)); }
  }

  CQA.openApplyDialog = function (opts) {
    var form = document.createElement('cqa-apply-dialog');
    form.data = opts;
    ui.modal({
      title: t('apply.title'),
      wide: true,
      body: form,
      footer: function (close) {
        var go = h('button', { class: 'btn btn--primary', type: 'button' }, icon('flash'), t('apply.start'));
        go.addEventListener('click', function () {
          var dto = form.value();
          if (!form.validate(dto)) return;
          go.disabled = true;
          CQA.dom.replace(go, ui.spinner(), t('dash.starting'));
          CQA.api.createRun(dto).then(function (run) {
            close();
            CQA.router.go('/runs/' + run.id);
          }, function (err) {
            go.disabled = false;
            CQA.dom.replace(go, icon('flash'), t('apply.start'));
            form.showError(err);
          });
        });
        return [h('button', { class: 'btn', type: 'button', onClick: function () { close(); } }, t('action.cancel')), go];
      }
    });
  };

  customElements.define('cqa-apply-dialog', CqaApplyDialog);
})(window.CQA = window.CQA || {});
