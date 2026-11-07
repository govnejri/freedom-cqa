/* <cqa-app> — каркас: шапка с переключателем роли (руководитель / QA / оператор), разделы роли, шестерёнка настроек,
   индикатор режима (API/демо), область страниц. Роль запоминается в localStorage; прямая ссылка на раздел другой роли
   переключает роль. На узком экране разделы и роль — в меню-«гамбургере». */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui;

  var LS_ROLE = 'cqa.role';
  var NAV = {
    dashboard: { href: '#/', icon: 'home', label: 'nav.dashboard' },
    risks: { href: '#/risks', icon: 'shield', label: 'nav.risks' },
    slices: { href: '#/slices', icon: 'grid', label: 'nav.slices' },
    assistant: { href: '#/assistant', icon: 'sparkle', label: 'nav.assistant' },
    conversations: { href: '#/conversations', icon: 'chat', label: 'nav.conversations' },
    appeals: { href: '#/appeals', icon: 'scale', label: 'nav.appeals' },
    checklist: { href: null, icon: 'checklist', label: 'nav.checklist' },
    quality: { href: '#/quality', icon: 'chart', label: 'nav.quality' },
    profile: { href: null, icon: 'person', label: 'nav.profile' },
    trainer: { href: '#/trainer', icon: 'target', label: 'nav.trainer' },
    tutor: { href: '#/tutor', icon: 'book', label: 'nav.tutor' }
  };
  var ROLES = [
    { key: 'lead', icon: 'chart', items: ['dashboard', 'risks', 'slices', 'assistant'] },
    { key: 'qa', icon: 'checklist', items: ['conversations', 'appeals', 'checklist', 'quality'] },
    { key: 'operator', icon: 'headset', items: ['profile', 'trainer', 'tutor'] }
  ];
  function roleDef(key) { return ROLES.filter(function (r) { return r.key === key; })[0] || null; }

  function navHref(key) {
    if (key === 'checklist') return '#/checklists/' + encodeURIComponent(CQA.config.checklistId);
    if (key === 'profile') { var me = CQA.me.get(); return me ? '#/operators/' + encodeURIComponent(me) : '#/operators'; }
    return NAV[key].href;
  }

  /** Домашняя страница роли — куда ведёт переключение роли. */
  CQA.roleHome = function (role) { var r = roleDef(role); return r ? navHref(r.items[0]) : '#/'; };
  CQA.roles = ROLES.map(function (r) { return r.key; });

  class CqaApp extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      var self = this;
      this.role = CQA.store.get(LS_ROLE, null);
      if (!roleDef(this.role)) this.role = null;
      this.nav = h('nav', { class: 'app-nav', 'aria-label': t('nav.aria') });
      this.roleSeg = h('div', { class: 'segmented role-switch', role: 'group', 'aria-label': t('roles.aria') });
      this.roleBtns = {};
      ROLES.forEach(function (r) {
        var b = h('button', { type: 'button', 'aria-pressed': 'false', title: t('roles.' + r.key + 'Title'),
          onClick: function () { self.switchRole(r.key); } }, icon(r.icon), h('span', null, t('roles.' + r.key)));
        self.roleBtns[r.key] = b;
        self.roleSeg.appendChild(b);
      });
      this.gear = h('a', { class: 'btn btn--subtle btn--icon app-gear', href: '#/settings', 'aria-label': t('nav.settings'), title: t('nav.settings') }, icon('settings'));
      this.modeBtn = h('button', { class: 'btn btn--subtle btn--small', type: 'button', onClick: this.openConnection.bind(this) });
      this.menuBtn = h('button', { class: 'btn btn--subtle btn--icon app-menu-btn', type: 'button', 'aria-label': t('nav.menu'), 'aria-expanded': 'false', 'aria-controls': 'app-menu',
        onClick: function () { self.toggleMenu(); } }, icon('menu'));
      this.menu = h('div', { class: 'app-menu', id: 'app-menu', hidden: true, tabindex: '-1', role: 'region', 'aria-label': t('nav.menu') });
      this.outlet = h('main', { class: 'app-main', id: 'main', tabindex: '-1' }, h('div', { class: 'page' }, CQA.ui.skeleton('tiles', 6)));
      this.textContent = '';
      this.append(
        h('a', { class: 'skip-link', href: '#main', onClick: function (e) { e.preventDefault(); document.getElementById('main').focus(); } }, t('nav.skip')),
        h('header', { class: 'app-header' },
          h('a', { class: 'app-brand', href: '#/', 'aria-label': t('app.title') },
            h('span', { class: 'app-brand__logo', 'aria-hidden': 'true' }, 'CQA'),
            h('span', { class: 'app-brand__text' }, t('app.name')),
            h('span', { class: 'app-brand__sub' }, t('app.subtitle'))),
          this.roleSeg,
          this.nav,
          h('div', { class: 'app-header__end' }, this.modeBtn, this.gear, this.menuBtn)),
        this.menu,
        this.outlet);
      this.renderNav();
      CQA.router.onChange(function (cur) {
        self.closeMenu();
        var roles = cur.route && cur.route.roles;
        if (roles && roles.length && roles.indexOf(self.role) < 0) self.setRole(roles[0]);
        else if (!self.role) self.setRole('lead');
        self.current = cur;
        self.highlight();
      });
      document.addEventListener('cqa-appeals-changed', function () { self.refreshCounts(); });
      document.addEventListener('cqa-me-changed', function () { self.renderNav(); });
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !self.menu.hidden) { self.closeMenu(); self.menuBtn.focus(); } });
      document.addEventListener('click', function (e) {
        if (!self.menu.hidden && !self.menu.contains(e.target) && !self.menuBtn.contains(e.target)) self.closeMenu();
      });
    }

    setRole(role) {
      if (!roleDef(role)) return;
      this.role = role;
      CQA.store.set(LS_ROLE, role);
      document.documentElement.setAttribute('data-role', role);
      this.renderNav();
    }

    /** Переключение роли пользователем — переход на домашний раздел роли. */
    switchRole(role) {
      if (role === this.role) { this.closeMenu(); return; }
      this.setRole(role);
      location.hash = CQA.roleHome(role);
    }

    renderNav() {
      var self = this, r = roleDef(this.role || 'lead');
      this.links = {};
      CQA.dom.clear(this.nav);
      r.items.forEach(function (k) {
        var n = NAV[k];
        var a = h('a', { href: navHref(k) }, icon(n.icon), h('span', { class: 'nav-label' }, t(n.label)));
        a.setAttribute('aria-label', t(n.label));
        if (k === 'appeals') { self.appealsBadge = h('span', { class: 'badge badge--count badge--brand-filled', hidden: true }); a.appendChild(self.appealsBadge); }
        self.links[k] = a;
        self.nav.appendChild(a);
      });
      Object.keys(this.roleBtns).forEach(function (k) { self.roleBtns[k].setAttribute('aria-pressed', String(k === (self.role || 'lead'))); });
      if (this.appealsBadge && this._appealsN != null) this.setAppealsBadge(this._appealsN);
      this.renderMenu();
      this.highlight();
    }

    highlight() {
      var cur = this.current, self = this;
      var key = cur && cur.route ? cur.route.nav : null;
      // «Мой профиль» подсвечиваем на своём профиле и на списке, но не на чужом профиле
      var me = CQA.me.get();
      if (key === 'profile' && cur.params && cur.params.name && me && cur.params.name.toLocaleLowerCase('ru') !== me.toLocaleLowerCase('ru')) key = null;
      Object.keys(this.links || {}).forEach(function (k) {
        if (k === key) self.links[k].setAttribute('aria-current', 'page'); else self.links[k].removeAttribute('aria-current');
      });
      if (key === 'settings') this.gear.setAttribute('aria-current', 'page'); else this.gear.removeAttribute('aria-current');
      Array.prototype.forEach.call(this.menu.querySelectorAll('a[data-nav]'), function (a) {
        if (a.getAttribute('data-nav') === key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      });
    }

    // ------------------------------------------------------------ меню на узком экране
    renderMenu() {
      var self = this, cur = this.role || 'lead';
      var list = h('div', { class: 'app-menu__roles' });
      ROLES.forEach(function (r) {
        var sec = h('section', { class: 'app-menu__role' + (r.key === cur ? ' is-current' : '') },
          h('button', { class: 'app-menu__role-head', type: 'button', 'aria-pressed': String(r.key === cur), onClick: function () { self.switchRole(r.key); } },
            icon(r.icon), h('span', null, t('roles.' + r.key)), r.key === cur ? ui.badge(t('roles.current'), 'brand') : null));
        var ul = h('ul', { class: 'app-menu__links' });
        r.items.forEach(function (k) {
          ul.appendChild(h('li', null, h('a', { href: navHref(k), 'data-nav': k, onClick: function () { if (r.key !== cur) self.setRole(r.key); } },
            icon(NAV[k].icon), h('span', null, t(NAV[k].label)))));
        });
        sec.appendChild(ul);
        list.appendChild(sec);
      });
      CQA.dom.replace(this.menu, list,
        h('div', { class: 'app-menu__foot' },
          h('a', { class: 'btn', href: '#/settings', 'data-nav': 'settings' }, icon('settings'), t('nav.settings'))));
    }

    toggleMenu() { if (this.menu.hidden) this.openMenu(); else this.closeMenu(); }
    openMenu() {
      this.renderMenu();
      this.highlight();
      this.menu.hidden = false;
      this.menuBtn.setAttribute('aria-expanded', 'true');
      this.menu.focus({ preventScroll: true });
    }
    closeMenu() {
      if (!this.menu || this.menu.hidden) return;
      this.menu.hidden = true;
      this.menuBtn.setAttribute('aria-expanded', 'false');
    }

    ready() {
      var mock = CQA.api.mode === 'mock';
      CQA.dom.replace(this.modeBtn,
        icon(mock ? 'database' : 'plug'),
        h('span', { class: 'mode-label' }, mock ? t('mode.mock') : t('mode.api')));
      this.modeBtn.className = 'btn btn--small mode-btn ' + (mock ? 'mode-btn--mock' : 'mode-btn--api');
      this.modeBtn.setAttribute('title', mock ? t('mode.mockReason.' + CQA.api.reason) : t('mode.apiTitle', { base: CQA.api.base }));
      this.refreshCounts();
    }

    refreshCounts() {
      var self = this;
      CQA.api.appeals('open').then(function (list) { self.setAppealsBadge(list.length); })
        .catch(function () { self._appealsN = 0; if (self.appealsBadge) self.appealsBadge.hidden = true; });
    }

    setAppealsBadge(n) {
      this._appealsN = n;
      var badge = this.appealsBadge;
      if (!badge) return;
      badge.textContent = String(n);
      badge.hidden = !n;
      badge.setAttribute('aria-label', t('nav.openAppeals', { n: n }));
    }

    openConnection() {
      var api = CQA.api;
      var input = h('input', { class: 'input', id: 'conn-base', type: 'url', placeholder: 'https://cqa.example.kz', value: api.base || '' });
      var status = h('div', { 'aria-live': 'polite' });
      var mock = api.mode === 'mock';
      var body = h('div', { class: 'stack stack--l' },
        ui.msgbar(mock ? 'warning' : 'success',
          mock ? t('mode.mock') : t('mode.api'),
          mock ? t('mode.mockReason.' + api.reason) : t('mode.apiTitle', { base: api.base })),
        h('div', { class: 'field' },
          h('label', { class: 'field__label', for: 'conn-base' }, t('conn.base')),
          input,
          h('div', { class: 'field__hint' }, t('conn.hint'))),
        status);
      ui.modal({
        title: t('conn.title'),
        body: body,
        footer: function (close) {
          var check = h('button', { class: 'btn', type: 'button' }, t('conn.check'));
          check.addEventListener('click', function () {
            CQA.dom.replace(status, ui.spinner());
            api.checkBase(input.value).then(function () {
              CQA.dom.replace(status, ui.msgbar('success', t('conn.ok')));
            }, function (e) {
              CQA.dom.replace(status, ui.msgbar('danger', t('conn.fail'), e.message));
            });
          });
          return [
            h('button', { class: 'btn btn--subtle', type: 'button', onClick: function () { api.setBase(null); location.search = '?mock=1'; } }, t('conn.useMock')),
            h('span', { class: 'spacer' }),
            check,
            h('button', { class: 'btn btn--primary', type: 'button', onClick: function () {
              api.setBase(input.value || null);
              var sp = new URLSearchParams(location.search); sp.delete('mock');
              var s = sp.toString();
              location.href = location.pathname + (s ? '?' + s : '') + location.hash;
              close();
            } }, t('conn.save'))
          ];
        }
      });
    }
  }

  /** Страница «не найдено». */
  class CqaPageNotfound extends HTMLElement {
    connectedCallback() {
      this.textContent = '';
      this.appendChild(h('div', { class: 'page' },
        ui.empty(t('notfound.title'), t('notfound.text'), h('a', { class: 'btn btn--primary', href: CQA.roleHome(CQA.store.get('cqa.role', 'lead')) }, t('notfound.home')), 'warning')));
      document.title = t('notfound.title') + ' · CQA';
    }
  }

  customElements.define('cqa-app', CqaApp);
  customElements.define('cqa-page-notfound', CqaPageNotfound);
})(window.CQA = window.CQA || {});
