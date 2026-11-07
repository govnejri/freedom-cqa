/* <cqa-app> — каркас: графитовое меню слева с группами по задачам (руководитель / QA / оператор),
   внизу «Начало работы» и «Настройки»; сверху — «хлебные крошки» и поиск (Ctrl+K); справа — рабочая область.
   Группы меню — не режимы: ничего не переключается само. На узком экране меню выезжает по кнопке. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui, fmt = CQA.fmt;

  var NAV = {
    dashboard: { href: '#/', icon: 'home', label: 'nav.dashboard' },
    risks: { href: '#/risks', icon: 'shield', label: 'nav.risks' },
    team: { href: '#/operators', icon: 'people', label: 'nav.team' },
    assistant: { href: '#/assistant', icon: 'sparkle', label: 'nav.assistant' },
    queue: { href: '#/conversations', icon: 'inbox', label: 'nav.queue' },
    appeals: { href: '#/appeals', icon: 'scale', label: 'nav.appeals' },
    checklist: { href: null, icon: 'checklist', label: 'nav.checklist' },
    quality: { href: '#/quality', icon: 'target', label: 'nav.quality' },
    profile: { href: '#/me', icon: 'person', label: 'nav.profile' },
    learning: { href: '#/trainer', icon: 'cap', label: 'nav.learning' },
    settings: { href: '#/settings', icon: 'settings', label: 'nav.settings' }
  };
  var GROUPS = [
    { key: 'lead', items: ['dashboard', 'risks', 'team', 'assistant'] },
    { key: 'qa', items: ['queue', 'appeals', 'checklist', 'quality'] },
    { key: 'operator', items: ['profile', 'learning'] }
  ];
  var NARROW = '(max-width: 960px)';

  function navHref(key) {
    if (key === 'checklist') return '#/checklists/' + encodeURIComponent(CQA.config.checklistId);
    return NAV[key].href;
  }
  CQA.navHref = navHref;

  /** Подразделы одного пункта меню: «Команда» (операторы · срезы), «Обучение» (тренажёр · тьютор). */
  CQA.subnav = function (nav, current) {
    var items = nav === 'team'
      ? [{ k: 'operators', href: '#/operators', icon: 'people', label: 'team.operators' }, { k: 'slices', href: '#/slices', icon: 'grid', label: 'team.slices' }]
      : [{ k: 'trainer', href: '#/trainer', icon: 'target', label: 'learning.trainer' }, { k: 'tutor', href: '#/tutor', icon: 'book', label: 'learning.tutor' }];
    return h('nav', { class: 'subnav', 'aria-label': t(nav === 'team' ? 'nav.team' : 'nav.learning') }, items.map(function (it) {
      return h('a', { href: it.href, 'aria-current': it.k === current ? 'page' : null }, icon(it.icon), t(it.label));
    }));
  };
  CQA.navGroups = GROUPS;
  CQA.navItems = NAV;

  /** Последняя часть «хлебных крошек» для экранов-деталей. */
  function detailOf(cur) {
    var p = cur.params || {}, tag = cur.route.tag;
    if (p.uid) return fmt.externalId(p.uid);
    switch (tag) {
      case 'cqa-page-appeal': return t('crumb.appeal', { id: p.id });
      case 'cqa-page-operator': return p.name;
      case 'cqa-page-slices': return t('team.slices');
      case 'cqa-page-run': return t('crumb.run', { id: p.id });
      case 'cqa-page-run-diff': return t('crumb.runDiff');
      case 'cqa-page-log': return t('crumb.log');
      case 'cqa-page-trainer-session': return t('crumb.session', { id: p.id });
      case 'cqa-page-trainer-new': return t('crumb.newSession');
      case 'cqa-page-tutor': return t('nav.tutor');
      case 'cqa-page-checklist':
        if (p.tab === 'precedents') return t('cl.tabPrecedents');
        if (p.a && p.b) return t('crumb.compare');
        return null;
      default: return null;
    }
  }

  class CqaApp extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      var self = this;
      this.mq = window.matchMedia(NARROW);
      this.links = {};
      this.counts = {};
      this.nav = h('nav', { class: 'rail__nav', 'aria-label': t('nav.aria') });
      this.modeBtn = h('button', { class: 'rail__mode', type: 'button', onClick: this.openConnection.bind(this) });
      this.rail = h('aside', { class: 'rail', id: 'app-rail', 'aria-label': t('nav.menu') },
        h('div', { class: 'row row--nowrap' },
          h('a', { class: 'rail__brand', href: '#/', 'aria-label': t('app.title') },
            h('span', { class: 'rail__mark', 'aria-hidden': 'true' }, icon('check')),
            h('span', { class: 'rail__name' }, t('app.name'), h('small', null, t('app.subtitle')))),
          h('button', { class: 'btn btn--subtle btn--icon rail__close', type: 'button', 'aria-label': t('nav.closeMenu'), onClick: function () { self.closeMenu(true); } }, icon('close'))),
        this.nav,
        h('div', { class: 'rail__foot' },
          h('button', { class: 'rail__link', type: 'button', onClick: function () { self.closeMenu(); CQA.guide.open(); } },
            icon('compass'), h('span', null, t('guide.nav'))),
          this.settingsLink = h('a', { class: 'rail__link', href: '#/settings' }, icon('settings'), h('span', null, t('nav.settings'))),
          this.modeBtn));
      this.scrim = h('div', { class: 'rail-scrim', 'aria-hidden': 'true', onClick: function () { self.closeMenu(true); } });
      this.menuBtn = h('button', { class: 'btn btn--subtle btn--icon topbar__menu', type: 'button', 'aria-label': t('nav.menu'), 'aria-expanded': 'false', 'aria-controls': 'app-rail',
        onClick: function () { self.toggleMenu(); } }, icon('menu'));
      this.crumbs = h('div', { class: 'crumbs' });
      var searchBtn = h('button', { class: 'search-btn', type: 'button', 'aria-label': t('palette.open'), onClick: function () { CQA.palette.open(); } },
        icon('search'), h('span', null, t('palette.placeholderShort')), h('kbd', null, 'Ctrl K'));
      this.outlet = h('main', { class: 'app-main', id: 'main', tabindex: '-1' }, h('div', { class: 'page' }, ui.skeleton('tiles', 4)));
      this.banner = h('div', { class: 'offline', role: 'alert', hidden: true });
      this.textContent = '';
      this.append(
        h('a', { class: 'skip-link', href: '#main', onClick: function (e) { e.preventDefault(); document.getElementById('main').focus(); } }, t('nav.skip')),
        this.rail, this.scrim,
        h('div', { class: 'app-body' },
          h('header', { class: 'topbar' }, this.menuBtn, this.crumbs, h('div', { class: 'topbar__end' }, searchBtn)),
          this.banner,
          this.outlet));
      this.renderNav();
      this.syncInert();
      this.mq.addEventListener('change', function () { self.closeMenu(); self.syncInert(); });
      CQA.router.onChange(function (cur) {
        self.closeMenu();
        self.current = cur;
        self.highlight();
        self.renderCrumbs();
      });
      document.addEventListener('cqa-appeals-changed', function () { self.refreshCounts(); });
      document.addEventListener('cqa-compliance-changed', function () { self.refreshCounts(); });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && self.classList.contains('is-menu-open')) { self.closeMenu(true); return; }
        // Ctrl+K / ⌘K — палитра (по коду клавиши: работает и в русской раскладке)
        if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.code === 'KeyK' || (e.key || '').toLowerCase() === 'k')) {
          e.preventDefault();
          CQA.palette.open();
        }
      });
    }

    renderNav() {
      var self = this;
      CQA.dom.clear(this.nav);
      this.links = {};
      GROUPS.forEach(function (g) {
        self.nav.appendChild(h('div', { class: 'rail__group', id: 'rail-g-' + g.key }, t('roles.' + g.key)));
        g.items.forEach(function (k) {
          var n = NAV[k];
          var a = h('a', { class: 'rail__link', href: navHref(k), 'data-nav': k }, icon(n.icon), h('span', null, t(n.label)));
          if (k === 'appeals' || k === 'risks') {
            var c = h('span', { class: 'rail__count' + (k === 'risks' ? ' rail__count--gross' : ''), hidden: true });
            self.counts[k] = c;
            a.appendChild(c);
          }
          self.links[k] = a;
          self.nav.appendChild(a);
        });
      });
      this.highlight();
    }

    highlight() {
      var cur = this.current, self = this;
      var key = cur && cur.route ? cur.route.nav : null;
      // свой профиль из «Команды» подсвечивает «Мой профиль»
      if (key === 'team' && cur.route.tag === 'cqa-page-operator' && CQA.me.get() && cur.params.name &&
          cur.params.name.toLocaleLowerCase('ru') === CQA.me.get().toLocaleLowerCase('ru')) key = 'profile';
      Object.keys(this.links).forEach(function (k) {
        if (k === key) self.links[k].setAttribute('aria-current', 'page'); else self.links[k].removeAttribute('aria-current');
      });
      if (key === 'settings') this.settingsLink.setAttribute('aria-current', 'page'); else this.settingsLink.removeAttribute('aria-current');
    }

    renderCrumbs() {
      var cur = this.current;
      if (!cur || !cur.route || !cur.route.nav) { CQA.dom.replace(this.crumbs, h('b', null, t('notfound.title'))); return; }
      var r = cur.route, parts = [];
      if (r.group) parts.push(h('span', null, t('roles.' + r.group)));
      var navKey = r.nav;
      var detail = detailOf(cur);
      var label = t(NAV[navKey].label);
      parts.push(detail ? h('a', { href: navHref(navKey) }, label) : h('b', { 'aria-current': 'page' }, label));
      if (detail) parts.push(h('b', { 'aria-current': 'page' }, detail));
      var out = [];
      parts.forEach(function (p, i) { if (i) out.push(h('span', { class: 'sep', 'aria-hidden': 'true' }, '/')); out.push(p); });
      CQA.dom.replace(this.crumbs, h('nav', { class: 'crumbs', 'aria-label': t('nav.crumbs') }, out));
    }

    // ------------------------------------------------------------ узкий экран
    syncInert() {
      var off = this.mq.matches && !this.classList.contains('is-menu-open');
      if (off) this.rail.setAttribute('inert', ''); else this.rail.removeAttribute('inert');
    }
    toggleMenu() { if (this.classList.contains('is-menu-open')) this.closeMenu(true); else this.openMenu(); }
    openMenu() {
      this.classList.add('is-menu-open');
      this.menuBtn.setAttribute('aria-expanded', 'true');
      this.syncInert();
      var cur = this.rail.querySelector('[aria-current="page"]') || this.rail.querySelector('a');
      if (cur) cur.focus({ preventScroll: true });
    }
    closeMenu(focusBtn) {
      if (!this.classList.contains('is-menu-open')) return;
      this.classList.remove('is-menu-open');
      this.menuBtn.setAttribute('aria-expanded', 'false');
      this.syncInert();
      if (focusBtn) this.menuBtn.focus();
    }

    // ------------------------------------------------------------ готовность: режим и счётчики
    ready() {
      var mock = CQA.api.mode === 'mock';
      CQA.dom.replace(this.modeBtn, h('i', { 'aria-hidden': 'true' }), h('span', null, mock ? t('mode.mock') : t('mode.apiOn')));
      this.modeBtn.classList.toggle('is-mock', mock);
      this.modeBtn.setAttribute('data-tip', mock ? t('mode.mockReason.' + CQA.api.reason) : t('mode.apiTitle', { base: CQA.api.base }));
      this.renderBanner();
      this.refreshCounts();
    }

    /** Нет связи с API — заметно: демо-цифры не должны выглядеть настоящими. Явный ?mock=1 баннера не даёт. */
    renderBanner() {
      var self = this, api = CQA.api;
      if (api.mode !== 'mock' || api.reason === 'forced') { this.banner.hidden = true; return; }
      var status = h('span', { class: 'offline__status' }, api.reason === 'unreachable' ? t('offline.auto') : '');
      var retry = h('button', { class: 'btn btn--small', type: 'button' }, icon('refresh'), t('offline.retry'));
      retry.addEventListener('click', function () {
        retry.disabled = true;
        CQA.dom.replace(retry, ui.spinner(), t('offline.checking'));
        api.retryApi().then(function () { location.reload(); }, function () {
          retry.disabled = false;
          CQA.dom.replace(retry, icon('refresh'), t('offline.retry'));
          status.textContent = t('offline.still');
        });
      });
      CQA.dom.replace(this.banner,
        icon('warning'),
        h('span', { class: 'offline__text' }, h('b', null, api.reason === 'unreachable' ? t('offline.title') : t('offline.noApiTitle')), ' ', t('offline.text')),
        status,
        api.reason === 'unreachable' ? retry : h('button', { class: 'btn btn--small', type: 'button', onClick: function () { self.openConnection(); } }, icon('plug'), t('offline.connect')));
      this.banner.hidden = false;
      if (api.reason === 'unreachable') api.watchApi(function () { location.reload(); });
    }

    refreshCounts() {
      var self = this;
      CQA.api.appeals('open').then(function (list) { self.setCount('appeals', list.length, t('nav.openAppeals', { n: list.length })); })
        .catch(function () { self.setCount('appeals', 0); });
      CQA.api.risks(null).then(function (r) {
        var pending = (r.items || []).filter(function (it) { return !it.compliance; }).length;
        self.setCount('risks', pending, t('nav.pendingRisks', { n: pending }));
      }).catch(function () { self.setCount('risks', 0); });
    }

    setCount(key, n, label) {
      var el = this.counts[key];
      if (!el) return;
      el.textContent = String(n || 0);
      el.hidden = !n;
      if (label) el.setAttribute('aria-label', label);
    }

    openConnection() {
      var api = CQA.api;
      var input = h('input', { class: 'input', id: 'conn-base', type: 'url', placeholder: 'https://cqa.example.kz', value: api.base || '' });
      var status = h('div', { 'aria-live': 'polite' });
      var mock = api.mode === 'mock';
      var body = h('div', { class: 'stack stack--l' },
        ui.msgbar(mock ? 'warning' : 'success',
          mock ? t('mode.mock') : t('mode.apiOn'),
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
        ui.empty(t('notfound.title'), t('notfound.text'), h('a', { class: 'btn btn--primary', href: '#/' }, t('notfound.home')), 'warning')));
      document.title = t('notfound.title') + ' · CQA';
    }
  }

  customElements.define('cqa-app', CqaApp);
  customElements.define('cqa-page-notfound', CqaPageNotfound);
})(window.CQA = window.CQA || {});
