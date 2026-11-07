/* Hash-роутинг (§9). Страница — custom element; при смене только query у той же страницы вызывается page.update(). */
(function (CQA) {
  'use strict';

  // roles: в каких ролях маршрут «свой» (первая — домашняя). Прямая ссылка на маршрут чужой роли переключает роль.
  var ALL = ['lead', 'qa', 'operator'];
  var routes = [
    { re: /^\/?$/, tag: 'cqa-page-dashboard', nav: 'dashboard', keys: [], roles: ['lead'] },
    { re: /^\/risks\/?$/, tag: 'cqa-page-risks', nav: 'risks', keys: [], roles: ['lead'] },
    { re: /^\/slices\/?$/, tag: 'cqa-page-slices', nav: 'slices', keys: [], roles: ['lead'] },
    { re: /^\/assistant\/?$/, tag: 'cqa-page-assistant', nav: 'assistant', keys: [], roles: ['lead'] },
    { re: /^\/conversations\/?$/, tag: 'cqa-page-conversations', nav: 'conversations', keys: [], roles: ['qa', 'lead'] },
    { re: /^\/c\/([^/]+)$/, tag: 'cqa-page-conversation', nav: 'conversations', keys: ['uid'], roles: ['qa', 'lead', 'operator'] },
    { re: /^\/appeals\/?$/, tag: 'cqa-page-appeals', nav: 'appeals', keys: [], roles: ['qa'] },
    { re: /^\/appeals\/(\d+)$/, tag: 'cqa-page-appeal', nav: 'appeals', keys: ['id'], roles: ['qa'] },
    { re: /^\/checklists\/([^/]+)\/?$/, tag: 'cqa-page-checklist', nav: 'checklist', keys: ['cid'], roles: ['qa'] },
    { re: /^\/checklists\/([^/]+)\/(precedents)$/, tag: 'cqa-page-checklist', nav: 'checklist', keys: ['cid', 'tab'], roles: ['qa'] },
    { re: /^\/checklists\/([^/]+)\/v\/(\d+)$/, tag: 'cqa-page-checklist', nav: 'checklist', keys: ['cid', 'vid'], roles: ['qa'] },
    { re: /^\/checklists\/([^/]+)\/diff\/(\d+)\/(\d+)$/, tag: 'cqa-page-checklist', nav: 'checklist', keys: ['cid', 'a', 'b'], roles: ['qa'] },
    { re: /^\/runs\/(\d+)$/, tag: 'cqa-page-run', nav: 'dashboard', keys: ['id'], roles: ALL },
    { re: /^\/runs\/(\d+)\/diff$/, tag: 'cqa-page-run-diff', nav: 'checklist', keys: ['id'], roles: ['qa', 'lead'] },
    { re: /^\/log\/(\d+)$/, tag: 'cqa-page-log', nav: 'conversations', keys: ['id'], roles: ALL },
    { re: /^\/quality\/?$/, tag: 'cqa-page-quality', nav: 'quality', keys: [], roles: ['qa'] },
    { re: /^\/operators\/?$/, tag: 'cqa-page-operators', nav: 'profile', keys: [], roles: ['operator', 'lead', 'qa'] },
    { re: /^\/operators\/([^/]+)$/, tag: 'cqa-page-operator', nav: 'profile', keys: ['name'], roles: ['operator', 'lead', 'qa'] },
    { re: /^\/trainer\/?$/, tag: 'cqa-page-trainer', nav: 'trainer', keys: [], roles: ['operator'] },
    { re: /^\/trainer\/new$/, tag: 'cqa-page-trainer-new', nav: 'trainer', keys: [], roles: ['operator'] },
    { re: /^\/trainer\/(\d+)$/, tag: 'cqa-page-trainer-session', nav: 'trainer', keys: ['id'], roles: ['operator'] },
    { re: /^\/tutor\/?$/, tag: 'cqa-page-tutor', nav: 'tutor', keys: [], roles: ['operator'] },
    { re: /^\/settings\/?$/, tag: 'cqa-page-settings', nav: 'settings', keys: [], roles: ALL }
  ];

  var outlet = null;
  var current = null; // {route, path, params, query, el}
  var listeners = [];

  function parse(hash) {
    var s = (hash || '').replace(/^#/, '');
    if (!s) s = '/';
    var qi = s.indexOf('?');
    var path = qi >= 0 ? s.slice(0, qi) : s;
    var query = {};
    if (qi >= 0) new URLSearchParams(s.slice(qi + 1)).forEach(function (v, k) { query[k] = v; });
    return { path: path, query: query };
  }

  function match(path) {
    for (var i = 0; i < routes.length; i++) {
      var m = routes[i].re.exec(path);
      if (m) {
        var params = {};
        routes[i].keys.forEach(function (k, j) { try { params[k] = decodeURIComponent(m[j + 1]); } catch (e) { params[k] = m[j + 1]; } });
        return { route: routes[i], params: params };
      }
    }
    return null;
  }

  function qs(query) {
    if (!query) return '';
    var p = new URLSearchParams();
    Object.keys(query).forEach(function (k) {
      var v = query[k];
      if (v == null || v === '' || v === false) return;
      p.set(k, v === true ? '1' : String(v));
    });
    var s = p.toString();
    return s ? '?' + s : '';
  }

  function href(path, query) { return '#' + path + qs(query); }

  function render(isFirst) {
    var p = parse(location.hash);
    var m = match(p.path);
    if (!m) {
      mount({ tag: 'cqa-page-notfound', nav: null }, p, {}, isFirst);
      return;
    }
    if (current && current.el && current.route.tag === m.route.tag && typeof current.el.update === 'function' &&
        current.el.update(m.params, p.query, current.params) === true) {
      current = { route: m.route, path: p.path, params: m.params, query: p.query, el: current.el };
      emit();
      return;
    }
    mount(m.route, p, m.params, isFirst);
  }

  function mount(route, p, params, isFirst) {
    var el = document.createElement(route.tag);
    el.params = params;
    el.query = p.query;
    outlet.textContent = '';
    // окна (ui.modal) живут в body: при смене страницы закрываем оставшиеся — как по Esc
    Array.prototype.forEach.call(document.querySelectorAll('dialog.dialog'), function (d) { d.dispatchEvent(new Event('cancel')); });
    outlet.appendChild(el);
    current = { route: route, path: p.path, params: params, query: p.query, el: el };
    if (!isFirst) {
      outlet.scrollTop = 0;
      // фокус на заголовок новой страницы для экранных читалок (после первой отрисовки)
      setTimeout(function () {
        var h1 = outlet.querySelector('h1');
        if (h1) { h1.setAttribute('tabindex', '-1'); h1.focus({ preventScroll: true }); }
      }, 0);
    }
    emit();
  }

  function emit() { listeners.forEach(function (fn) { try { fn(current); } catch (e) { console.error(e); } }); }

  var router = {
    start: function (el) {
      outlet = el;
      window.addEventListener('hashchange', function () { render(false); });
      render(true);
    },
    href: href,
    parse: parse,
    go: function (path, query) {
      var h = href(path, query);
      if (location.hash === h) render(false); else location.hash = h;
    },
    /** Обновить query текущего маршрута без перерисовки (replaceState не вызывает hashchange). */
    setQuery: function (query, push) {
      if (!current) return;
      var h = href(current.path, query);
      current.query = Object.assign({}, query);
      try {
        if (push) history.pushState(null, '', h); else history.replaceState(null, '', h);
      } catch (e) { /* iframe с ограничениями: просто молча оставляем URL */ }
    },
    get current() { return current; },
    routes: routes,
    onChange: function (fn) { listeners.push(fn); }
  };

  CQA.router = router;
})(window.CQA = window.CQA || {});
