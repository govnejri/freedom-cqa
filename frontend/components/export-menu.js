/* <cqa-export-menu> — экспорт GET /export?format=csv|json&final=0|1 → скачивание + прямая ссылка
   (в iframe Dynamics программное скачивание может блокироваться). .group = 'DEV' */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, ui = CQA.ui;

  var OPTIONS = [
    { format: 'csv', final: '0', label: 'export.csvOriginal', ext: 'csv' },
    { format: 'csv', final: '1', label: 'export.csvFinal', ext: 'csv' },
    { format: 'json', final: '1', label: 'export.json', ext: 'json' }
  ];

  class CqaExportMenu extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      var self = this;
      this.classList.add('menu-wrap');
      this.btn = h('button', { class: 'btn', type: 'button', 'aria-haspopup': 'true', 'aria-expanded': 'false' },
        icon('download'), t('export.button'), icon('chevronDown', 'icon--16'));
      this.menu = h('div', { class: 'menu', role: 'menu', hidden: true });
      this.append(this.btn, this.menu);
      this.btn.addEventListener('click', function () { self.toggle(); });
      this._outside = function (e) { if (!self.contains(e.target)) self.toggle(false); };
      this.addEventListener('keydown', function (e) { if (e.key === 'Escape') { self.toggle(false); self.btn.focus(); } });
    }

    disconnectedCallback() { document.removeEventListener('click', this._outside); }

    toggle(open) {
      if (open == null) open = this.menu.hidden;
      this.menu.hidden = !open;
      this.btn.setAttribute('aria-expanded', String(open));
      if (open) { this.renderMenu(); document.addEventListener('click', this._outside); var f = this.menu.querySelector('button'); if (f) f.focus(); }
      else document.removeEventListener('click', this._outside);
    }

    renderMenu() {
      var self = this;
      this.menu.textContent = '';
      OPTIONS.forEach(function (o) {
        self.menu.appendChild(h('button', { class: 'menu__item', role: 'menuitem', type: 'button', onClick: function () { self.download(o); } },
          icon(o.ext === 'json' ? 'copy' : 'download'), t(o.label)));
      });
      this.menu.appendChild(h('div', { class: 'menu__sep', role: 'separator' }));
      this.menu.appendChild(h('div', { class: 'menu__note' }, t('export.note')));
    }

    download(o) {
      var self = this;
      var params = { format: o.format, final: o.final, group: this.group || null };
      var name = 'cqa-' + (this.group || 'all') + (o.final === '1' ? '-final' : '') + '.' + o.ext;
      this.toggle(false);
      var direct = CQA.api.mode === 'api' ? CQA.api.exportUrl(params) : null;
      CQA.api.exportBlob(params).then(function (blob) {
        var url = CQA.util.downloadBlob(blob, name);
        ui.toast(t('export.started', { name: name }), {
          tone: 'success', timeout: 15000,
          detail: t('export.blockedHint'),
          action: { label: t('export.openLink'), href: direct || url, target: '_blank' }
        });
      }, function (err) {
        ui.toast(t('export.failed'), { tone: 'error', detail: err.message, action: direct ? { label: t('export.openLink'), href: direct, target: '_blank' } : null });
      }).finally(function () { self.btn.focus(); });
    }
  }

  customElements.define('cqa-export-menu', CqaExportMenu);
})(window.CQA = window.CQA || {});
