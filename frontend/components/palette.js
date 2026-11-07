/* Палитра Ctrl+K: поиск диалога по ID или оператору, переход к оператору и быстрые действия.
   Список диалогов и операторов загружается один раз и фильтруется на месте (без учёта регистра и «ё»). */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, U = CQA.util;

  var cache = { convs: null, ops: null, at: 0 };
  function norm(s) { return String(s || '').toLocaleLowerCase('ru').replace(/ё/g, 'е').trim(); }
  function scoreOf(s) { return s.final_total != null ? s.final_total : s.total; }

  function load() {
    if (cache.convs && Date.now() - cache.at < 60000) return Promise.resolve(cache);
    return Promise.all([
      CQA.api.conversations({}).catch(function () { return []; }),
      CQA.api.operators().then(function (d) { return d.operators || []; }).catch(function () { return []; })
    ]).then(function (res) {
      cache.convs = (res[0] || []).filter(function (s) { return !CQA.isTrainerUid(s.uid); });
      cache.ops = res[1] || [];
      cache.at = Date.now();
      return cache;
    });
  }

  function actions() {
    var list = [];
    CQA.navGroups.forEach(function (g) {
      g.items.forEach(function (k) {
        list.push({ icon: CQA.navItems[k].icon, label: t(CQA.navItems[k].label), sub: t('roles.' + g.key), href: CQA.navHref(k) });
      });
    });
    list.push({ icon: 'compass', label: t('guide.title'), sub: t('palette.guideSub'), run: function () { CQA.guide.open(); } });
    list.push({ icon: 'download', label: t('pipe.title'), sub: t('palette.pipeSub'), run: function () { CQA.openPipeline(); } });
    list.push({ icon: 'cap', label: t('nav.tutor'), sub: t('roles.operator'), href: '#/tutor' });
    list.push({ icon: 'grid', label: t('team.slices'), sub: t('roles.lead'), href: '#/slices' });
    list.push({ icon: 'settings', label: t('nav.settings'), sub: t('palette.settingsSub'), href: '#/settings' });
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    list.push({ icon: dark ? 'sun' : 'moon', label: dark ? t('theme.toLight') : t('theme.toDark'), sub: t('theme.title'), run: function () { CQA.setTheme(dark ? 'light' : 'dark'); } });
    return list;
  }

  function open() {
    if (document.querySelector('dialog.palette')) return;
    var input = h('input', { class: 'palette__input', type: 'text', placeholder: t('palette.placeholder'), 'aria-label': t('palette.placeholder'),
      autocomplete: 'off', spellcheck: 'false', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'palette-list' });
    var listEl = h('div', { class: 'palette__list', id: 'palette-list', role: 'listbox', 'aria-label': t('palette.results') });
    var dlg = h('dialog', { class: 'palette', 'aria-label': t('palette.open') },
      h('div', { class: 'palette__search' }, icon('search'), input, h('kbd', null, 'Esc')),
      listEl,
      h('div', { class: 'palette__foot' },
        h('span', null, h('kbd', null, '↑'), h('kbd', null, '↓'), t('palette.move')),
        h('span', null, h('kbd', null, 'Enter'), t('palette.go')),
        h('span', null, h('kbd', null, 'Esc'), t('action.close'))));
    var items = [], active = 0, data = null;

    function close() { if (dlg.open) dlg.close(); if (dlg.parentNode) dlg.parentNode.removeChild(dlg); }
    function go(it) {
      close();
      if (it.run) it.run();
      else if (it.href) location.hash = it.href.replace(/^#/, '');
    }
    function setActive(i) {
      if (!items.length) return;
      active = (i + items.length) % items.length;
      items.forEach(function (it, j) { it.el.classList.toggle('is-active', j === active); it.el.setAttribute('aria-selected', String(j === active)); });
      items[active].el.scrollIntoView({ block: 'nearest' });
      input.setAttribute('aria-activedescendant', items[active].el.id);
    }

    function render() {
      var q = norm(input.value);
      var groups = [];
      if (data && q) {
        var convs = data.convs.filter(function (s) {
          return norm(s.external_id).indexOf(q) >= 0 || norm(s.operator_name).indexOf(q) >= 0 || norm(s.uid).indexOf(q) >= 0;
        });
        convs.sort(function (a, b) {
          var ax = norm(a.external_id).indexOf(q) === 0 ? 0 : 1, bx = norm(b.external_id).indexOf(q) === 0 ? 0 : 1;
          return ax - bx || String(a.external_id).localeCompare(String(b.external_id), 'ru', { numeric: true });
        });
        if (convs.length) groups.push({ title: t('palette.dialogs'), items: convs.slice(0, 7).map(function (s) {
          var sc = scoreOf(s);
          return { icon: U.channelIcon(s.channel), label: s.external_id,
            sub: [s.operator_name, s.language, U.channelLabel(s.channel), sc != null ? t('palette.score', { s: fmt.score(sc) }) : null, s.gross ? t('badge.gross') : null].filter(Boolean).join(' · '),
            href: CQA.convHref(s.uid) };
        }) });
        var ops = data.ops.filter(function (o) { return norm(o.name).indexOf(q) >= 0; }).slice(0, 4);
        if (ops.length) groups.push({ title: t('palette.operators'), items: ops.map(function (o) {
          return { icon: 'person', label: o.name, sub: t('palette.opSub', { n: o.n_dialogs, s: fmt.score(o.avg_total) }), href: CQA.operatorHref(o.name) };
        }) });
      }
      var acts = actions().filter(function (a) { return !q || norm(a.label).indexOf(q) >= 0 || norm(a.sub).indexOf(q) >= 0; });
      if (acts.length) groups.push({ title: q ? t('palette.actions') : t('palette.goTo'), items: acts });

      CQA.dom.clear(listEl);
      items = [];
      if (!groups.length) {
        listEl.appendChild(h('div', { class: 'palette__empty' }, data ? t('palette.nothing') : t('state.loading')));
        return;
      }
      groups.forEach(function (g) {
        listEl.appendChild(h('div', { class: 'palette__group', role: 'presentation' }, g.title));
        g.items.forEach(function (it) {
          var idx = items.length;
          var el = h('button', { class: 'palette__item', type: 'button', role: 'option', id: 'pal-' + idx, 'aria-selected': 'false', tabindex: '-1',
            onClick: function () { go(it); }, onMousemove: function () { if (active !== idx) setActive(idx); } },
            icon(it.icon || 'arrowRight'),
            h('span', { class: 'palette__main' }, h('span', { class: 'palette__label' }, it.label), it.sub ? h('span', { class: 'palette__sub' }, it.sub) : null));
          it.el = el;
          items.push(it);
          listEl.appendChild(el);
        });
      });
      setActive(0);
    }

    input.addEventListener('input', render);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); if (items[active]) go(items[active]); }
    });
    dlg.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) close(); });
    document.body.appendChild(dlg);
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    input.focus();
    render();
    load().then(function (d) { data = d; render(); });
  }

  CQA.palette = { open: open };
})(window.CQA = window.CQA || {});
