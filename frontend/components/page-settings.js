/* <cqa-page-settings> — настройки #/settings: профили моделей по ролям (GET/PUT /settings), переключатель
   «Облако / Локальная модель» для судьи, статус ключей и сервиса из /health. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui;

  var ROLES = ['judge', 'strong', 'cross_check', 'signals'];
  var LOCAL = 'local';

  class CqaPageSettings extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('nav.settings') + ' · CQA';
      this.root = h('div', { class: 'stack stack--l' }, ui.skeleton('card', 6));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page settings-page' },
        h('div', { class: 'page-header' },
          h('div', null, h('h1', null, t('nav.settings')), h('div', { class: 'page-header__sub' }, t('set.subtitle'))),
          h('div', { class: 'page-head__actions' },
            h('button', { class: 'btn', type: 'button', onClick: function () { CQA.openPipeline(); } }, icon('download'), t('pipe.open')),
            h('button', { class: 'btn btn--subtle', type: 'button', onClick: function () { CQA.guide.open(); } }, icon('compass'), t('guide.nav')))),
        this.root));
      this.load();
    }

    load() {
      var self = this;
      CQA.dom.replace(this.root, ui.skeleton('card', 6));
      Promise.all([CQA.api.settings(), CQA.api.health_().catch(function () { return CQA.api.health; })]).then(function (res) {
        self.s = res[0];
        self.health = res[1] || {};
        self.render();
      }, function (err) {
        CQA.dom.replace(self.root, ui.error(err, function () { self.load(); }));
      });
    }

    profile(name) { return (this.s.profiles || []).filter(function (p) { return p.name === name; })[0] || null; }

    save(roles, okMsg) {
      var self = this;
      this.busy = true;
      this.render();
      return CQA.api.saveSettings(roles).then(function (s) {
        self.s = s;
        self.busy = false;
        ui.toast(okMsg || t('set.saved'), { tone: 'success' });
        self.render();
        CQA.api.health_().then(function (hl) { self.health = hl; self.renderStatus(); }).catch(function () {});
      }, function (err) {
        self.busy = false;
        self.render();
        var ve = CQA.api.validationErrors(err);
        ui.toast(t('set.saveFailed'), { tone: 'error', detail: ve.length ? ve.map(function (x) { return x.msg; }).join('; ') : err.message });
      });
    }

    render() {
      var self = this, s = this.s;
      var judge = s.roles.judge;
      var isLocal = (this.profile(judge) || {}).local === true;
      var local = this.profile(LOCAL) || (s.profiles || []).filter(function (p) { return p.local; })[0] || null;
      var localOk = !!(local && local.available);
      var cloudTarget = s.defaults.judge && !(this.profile(s.defaults.judge) || {}).local ? null : 'gemini-flash';

      // ---- облако / локальная модель
      var segCloud = h('button', { type: 'button', role: 'radio', 'aria-checked': String(!isLocal), disabled: this.busy || null,
        onClick: function () { if (isLocal) self.save({ judge: cloudTarget }, t('set.toCloud')); } }, icon('cloud'), t('set.cloud'));
      var segLocal = h('button', { type: 'button', role: 'radio', 'aria-checked': String(isLocal), disabled: this.busy || (!localOk && !isLocal) || null,
        onClick: function () { if (!isLocal && local) self.save({ judge: local.name }, t('set.toLocal')); } }, icon('server'), t('set.local'));
      var jp = this.profile(judge) || {};
      var switchCard = h('section', { class: 'card set-switch', 'aria-labelledby': 'set-switch' },
        h('div', { class: 'card__header' },
          h('div', null, h('h2', { class: 'card__title', id: 'set-switch' }, t('set.switchTitle')),
            h('div', { class: 'muted t-caption1' }, t('set.switchHint')))),
        h('div', { class: 'row set-switch__row' },
          h('div', { class: 'segmented segmented--l', role: 'radiogroup', 'aria-label': t('set.switchTitle') }, segCloud, segLocal),
          h('div', { class: 'set-switch__now' },
            h('span', { class: 'muted' }, t('set.judgeNow')), ' ', h('b', null, jp.model || judge), ' ',
            h('span', { class: 'muted' }, '(' + judge + ')'))),
        !localOk ? ui.msgbar(isLocal ? 'danger' : 'warning', isLocal ? t('set.localBroken') : t('set.localUnavailable'), t('set.localHowTo')) : null,
        isLocal && localOk ? ui.msgbar('success', t('set.localOn'), t('set.localOnText')) : null);

      // ---- роли
      var rows = ROLES.map(function (role) {
        var cur = s.roles[role];
        var def = s.defaults[role];
        var overridden = Object.prototype.hasOwnProperty.call(s.overrides || {}, role);
        var sel = h('select', { class: 'select', id: 'set-role-' + role, disabled: self.busy || s.fake || null },
          (s.profiles || []).filter(function (p) { return p.name !== 'fake' || cur === 'fake'; }).map(function (p) {
            var label = p.name + ' — ' + p.model + (/-high$/.test(p.name) ? ' · ' + t('set.thinkHigh') : '') + (p.local ? ' · ' + t('q.local') : '') + (p.name === def ? ' · ' + t('set.default') : '') + (p.available ? '' : ' · ' + t('set.unavailable'));
            return h('option', { value: p.name, selected: p.name === cur, disabled: !p.available && p.name !== cur }, label);
          }));
        sel.addEventListener('change', function () {
          var patch = {};
          patch[role] = sel.value === def ? null : sel.value;
          self.save(patch);
        });
        var p = self.profile(cur) || {};
        return h('div', { class: 'set-role' },
          h('div', { class: 'set-role__label' },
            h('label', { class: 'field__label', for: 'set-role-' + role }, t('set.role.' + role)),
            h('div', { class: 'field__hint' }, t('set.roleHint.' + role))),
          h('div', { class: 'set-role__ctl' },
            sel,
            h('div', { class: 'row t-caption1' },
              ui.badge(p.local ? t('q.local') : t('q.cloud'), p.local ? 'success' : 'brand', p.local ? 'server' : 'cloud'),
              p.available === false ? ui.badge(t('set.unavailable'), 'danger', 'warning') : null,
              overridden ? h('span', { class: 'muted' }, t('set.overridden', { d: def })) : h('span', { class: 'muted' }, t('set.fromConfig')),
              overridden ? h('button', { class: 'btn btn--link t-caption1', type: 'button', disabled: self.busy || null, onClick: function () {
                var patch = {}; patch[role] = null; self.save(patch, t('set.reset'));
              } }, t('set.resetBtn')) : null)));
      });
      var rolesCard = h('section', { class: 'card', 'aria-labelledby': 'set-roles' },
        h('div', { class: 'card__header' },
          h('div', null, h('h2', { class: 'card__title', id: 'set-roles' }, t('set.rolesTitle')), h('div', { class: 'muted t-caption1' }, t('set.rolesHint')))),
        s.fake ? ui.msgbar('warning', t('set.fake'), t('set.fakeText')) : null,
        h('div', { class: 'set-roles' }, rows));

      this.statusBox = h('section', { class: 'card', 'aria-labelledby': 'set-status' });
      var cur = CQA.theme();
      var themeSeg = h('div', { class: 'segmented', role: 'radiogroup', 'aria-label': t('theme.title') }, ['light', 'dark', 'auto'].map(function (th) {
        return h('button', { type: 'button', role: 'radio', 'aria-checked': String(cur === th), onClick: function () {
          CQA.setTheme(th);
          Array.prototype.forEach.call(this.parentNode.children, function (b, i) { b.setAttribute('aria-checked', String(['light', 'dark', 'auto'][i] === th)); });
        } }, icon(th === 'light' ? 'sun' : th === 'dark' ? 'moon' : 'panel'), t('theme.' + th));
      }));
      var themeCard = h('section', { class: 'card', 'aria-labelledby': 'set-theme' },
        h('div', { class: 'card__header' },
          h('div', null, h('h2', { class: 'card__title', id: 'set-theme' }, t('theme.title')), h('div', { class: 'muted t-caption1' }, t('theme.hint')))),
        themeSeg);
      CQA.dom.replace(this.root, switchCard, rolesCard, themeCard, this.statusBox);
      this.renderStatus();
    }

    renderStatus() {
      var hl = this.health || {};
      var keys = hl.keys || {};
      var row = function (label, ok, okText, badText, hint) {
        return h('li', { class: 'set-key' },
          h('span', { class: 'set-key__icon set-key__icon--' + (ok ? 'ok' : 'bad') }, icon(ok ? 'checkCircle' : 'error')),
          h('span', { class: 'set-key__name' }, label),
          h('span', { class: ok ? 'set-key__ok' : 'set-key__bad' }, ok ? okText : badText),
          hint ? h('span', { class: 'muted t-caption1 set-key__hint' }, hint) : null);
      };
      var list = h('ul', { class: 'set-keys' },
        row(t('set.service'), hl.ok, t('set.ok'), t('set.notOk')),
        row(t('set.db'), hl.db, t('set.ok'), t('set.notOk')),
        Object.keys(keys).map(function (k) {
          return row(h('span', { class: 'mono' }, k), keys[k], t('set.keySet'), t('set.keyMissing'), CQA.i18n.tx('set.keyHint.' + k, ''));
        }),
        hl.active_checklist_version_id != null ? h('li', { class: 'set-key' },
          h('span', { class: 'set-key__icon set-key__icon--ok' }, icon('checklist')),
          h('span', { class: 'set-key__name' }, t('set.activeVersion')),
          h('a', { href: '#/checklists/' + encodeURIComponent(CQA.config.checklistId) + '/v/' + hl.active_checklist_version_id }, '#' + hl.active_checklist_version_id)) : null);
      CQA.dom.replace(this.statusBox,
        h('div', { class: 'card__header' },
          h('div', null, h('h2', { class: 'card__title', id: 'set-status' }, t('set.statusTitle')), h('div', { class: 'muted t-caption1' }, t('set.statusHint'))),
          h('button', { class: 'btn btn--small', type: 'button', onClick: this.load.bind(this) }, icon('refresh'), t('set.refresh'))),
        list,
        h('div', { class: 'muted t-caption1', style: { marginTop: '12px' } }, t('set.apiBase', { b: CQA.api.base || location.origin, m: CQA.api.mode === 'mock' ? t('mode.mock') : t('mode.api') })));
    }
  }

  customElements.define('cqa-page-settings', CqaPageSettings);
})(window.CQA = window.CQA || {});
