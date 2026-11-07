/* <cqa-app> — каркас: шапка с навигацией, индикатор режима (API/демо), область страниц. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui;

  var NAV = [
    { key: 'dashboard', href: '#/', icon: 'home', label: 'nav.dashboard' },
    { key: 'conversations', href: '#/conversations', icon: 'chat', label: 'nav.conversations' },
    { key: 'appeals', href: '#/appeals', icon: 'scale', label: 'nav.appeals' },
    { key: 'checklist', href: null, icon: 'checklist', label: 'nav.checklist' }
  ];

  class CqaApp extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.links = {};
      var nav = h('nav', { class: 'app-nav', 'aria-label': t('nav.aria') });
      NAV.forEach(function (n) {
        var a = h('a', { href: n.href || '#/checklists/' + encodeURIComponent(CQA.config.checklistId) },
          icon(n.icon), h('span', { class: 'nav-label' }, t(n.label)));
        a.setAttribute('aria-label', t(n.label));
        if (n.key === 'appeals') { this.appealsBadge = h('span', { class: 'badge badge--count badge--brand-filled', hidden: true }); a.appendChild(this.appealsBadge); }
        this.links[n.key] = a;
        nav.appendChild(a);
      }, this);
      this.modeBtn = h('button', { class: 'btn btn--subtle btn--small', type: 'button', onClick: this.openConnection.bind(this) });
      this.outlet = h('main', { class: 'app-main', id: 'main', tabindex: '-1' }, h('div', { class: 'page' }, CQA.ui.skeleton('tiles', 6)));
      this.textContent = '';
      this.append(
        h('a', { class: 'skip-link', href: '#main', onClick: function (e) { e.preventDefault(); document.getElementById('main').focus(); } }, t('nav.skip')),
        h('header', { class: 'app-header' },
          h('a', { class: 'app-brand', href: '#/', 'aria-label': t('app.title') },
            h('span', { class: 'app-brand__logo', 'aria-hidden': 'true' }, 'CQA'),
            h('span', { class: 'app-brand__text' }, t('app.name')),
            h('span', { class: 'app-brand__sub' }, t('app.subtitle'))),
          nav,
          h('div', { class: 'app-header__end' }, this.modeBtn)),
        this.outlet);
      var self = this;
      CQA.router.onChange(function (cur) {
        Object.keys(self.links).forEach(function (k) {
          if (cur.route.nav === k) self.links[k].setAttribute('aria-current', 'page');
          else self.links[k].removeAttribute('aria-current');
        });
      });
      document.addEventListener('cqa-appeals-changed', function () { self.refreshCounts(); });
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
      var badge = this.appealsBadge;
      CQA.api.appeals('open').then(function (list) {
        badge.textContent = String(list.length);
        badge.hidden = !list.length;
        badge.setAttribute('aria-label', t('nav.openAppeals', { n: list.length }));
      }).catch(function () { badge.hidden = true; });
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
        ui.empty(t('notfound.title'), t('notfound.text'), h('a', { class: 'btn btn--primary', href: '#/' }, t('nav.dashboard')), 'warning')));
      document.title = t('notfound.title') + ' · CQA';
    }
  }

  customElements.define('cqa-app', CqaApp);
  customElements.define('cqa-page-notfound', CqaPageNotfound);
})(window.CQA = window.CQA || {});
