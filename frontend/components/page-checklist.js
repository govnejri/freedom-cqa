/* <cqa-page-checklist> — редактор чек-листа #/checklists/{id}, …/v/{vid}, …/diff/{a}/{b}.
   Версии неизменяемы: правки копятся в черновике (sessionStorage) и сохраняются как новая версия. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var STATUS_TONE = { published: 'success', draft: 'neutral', frozen: 'brand' };

  class CqaPageChecklist extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.cid = this.params.cid;
      this.root = h('div', { class: 'page cl-page' }, ui.skeleton('card', 6));
      this.textContent = '';
      this.appendChild(this.root);
      document.title = t('nav.checklist') + ' · CQA';
      this.load();
    }

    update(params, query) {
      if (params.cid !== this.cid || !this.versions) return false;
      var samePath = this.params && params.vid === this.params.vid && params.a === this.params.a && params.b === this.params.b && params.tab === this.params.tab;
      this.params = params;
      this.query = query;
      this.renderAll();
      if (!samePath) { var main = document.getElementById('main'); if (main) main.scrollTop = 0; }
      return true;
    }

    load() {
      var self = this;
      CQA.dom.replace(this.root, ui.skeleton('card', 6));
      CQA.api.checklistVersions(this.cid).then(function (list) {
        self.versions = (list || []).slice().sort(function (a, b) { return b.version - a.version; });
        self.renderAll();
      }, function (err) {
        CQA.dom.replace(self.root, ui.error(err, function () { self.load(); }, err.status === 404 ? t('cl.notFound') : null));
      });
    }

    active() { return this.versions.filter(function (v) { return v.is_active; })[0] || null; }
    byId(id) { return this.versions.filter(function (v) { return v.id === Number(id); })[0] || null; }
    previous(v) { return this.versions.filter(function (x) { return x.version < v.version; }).sort(function (a, b) { return b.version - a.version; })[0] || null; }

    ensureConfig(v) {
      if (!v) return Promise.resolve(null);
      if (v.config) return Promise.resolve(v);
      return CQA.api.checklistVersion(v.id).then(function (full) { v.config = full.config; return v; });
    }

    renderAll() {
      var self = this, p = this.params;
      if (!this.versions.length) {
        CQA.dom.replace(this.root, ui.empty(t('cl.noVersions'), t('cl.noVersionsText'), null, 'checklist'));
        return;
      }
      this.aside = h('aside', { class: 'cl-versions card', 'aria-label': t('cl.versions') });
      this.main = h('div', { class: 'cl-main' });
      var act = this.active();
      var title = (act && act.config && act.config.title) || (this.versions[0].config && this.versions[0].config.title) || this.cid;
      var newest = this.versions[0];
      var cmpBase = act && act.id !== newest.id ? act : this.versions[1];
      var base = '#/checklists/' + encodeURIComponent(this.cid);
      var isPrec = p.tab === 'precedents';
      CQA.dom.replace(this.root,
        h('div', { class: 'page-head' },
          h('div', null, h('h1', null, t('nav.checklist')), h('p', { class: 'page-head__what' }, isPrec ? t('cl.whatPrec') : t('cl.what'))),
          cmpBase && !isPrec ? h('div', { class: 'page-head__actions' }, h('a', { class: 'btn btn--subtle', href: base + '/diff/' + cmpBase.id + '/' + newest.id },
            icon('compare'), t('cl.compareVersions'))) : null),
        h('nav', { class: 'tabs cl-tabs', 'aria-label': t('cl.tabsAria') },
          h('a', { class: 'tab', href: base, 'aria-current': isPrec ? null : 'page', 'aria-selected': String(!isPrec) }, icon('checklist'), t('cl.tabVersions')),
          h('a', { class: 'tab', href: base + '/precedents', 'aria-current': isPrec ? 'page' : null, 'aria-selected': String(isPrec) }, icon('scale'), t('cl.tabPrecedents'))),
        h('div', { class: 'cl-layout' }, this.aside, this.main));
      if (isPrec) {
        document.title = t('cl.tabPrecedents') + ' · ' + t('nav.checklist') + ' · CQA';
        this.renderAside(null);
        CQA.dom.replace(this.main, h('div', { class: 'stack stack--l' },
          ui.msgbar('info', t('prec.introTitle'), t('prec.intro')),
          document.createElement('cqa-precedents')));
        return;
      }
      if (p.a && p.b) {
        this.renderAside(null);
        this.renderDiff(this.byId(p.a), this.byId(p.b));
      } else {
        var v = p.vid ? this.byId(p.vid) : (act || this.versions[0]);
        if (!v) { CQA.dom.replace(this.main, ui.empty(t('cl.versionNotFound'), null, h('a', { class: 'btn', href: '#/checklists/' + encodeURIComponent(this.cid) }, t('cl.toActive')), 'warning')); this.renderAside(null); return; }
        this.renderAside(v.id);
        CQA.dom.replace(this.main, ui.skeleton('card', 8));
        Promise.all([this.ensureConfig(v), this.ensureConfig(act)]).then(function () { self.renderEditor(v); },
          function (err) { CQA.dom.replace(self.main, ui.error(err, function () { self.renderAll(); })); });
      }
    }

    renderAside(currentId) {
      var cid = encodeURIComponent(this.cid);
      var ul = h('ul', { class: 'cl-versions__list' });
      this.versions.forEach(function (v) {
        ul.appendChild(h('li', null, h('a', { class: 'cl-version' + (v.id === currentId ? ' is-current' : ''), href: '#/checklists/' + cid + '/v/' + v.id, 'aria-current': v.id === currentId ? 'page' : null },
          h('div', { class: 'row' },
            h('span', { class: 't-body1-strong' }, t('cl.versionN', { v: v.version })),
            v.is_active ? ui.badge(t('cl.active'), 'brand-filled', 'checkCircle') : ui.badge(CQA.i18n.tx('vstatus.' + v.status, v.status), STATUS_TONE[v.status] || 'neutral')),
          h('div', { class: 'muted t-caption1' }, fmt.date(v.created_at)),
          v.comment ? h('div', { class: 'cl-version__comment t-caption1' }, v.comment) : null)));
      });
      CQA.dom.replace(this.aside, h('h2', { class: 'card__title' }, t('cl.versions')), ul);
    }

    // ------------------------------------------------------------ редактор версии
    draftKey(v) { return 'cqa.draft.' + this.cid + '.' + v.id; }

    renderEditor(v) {
      var self = this;
      var act = this.active();
      var cid = encodeURIComponent(this.cid);
      this.version = v;
      document.title = t('cl.versionN', { v: v.version }) + ' · ' + t('nav.checklist') + ' · CQA';
      var saved = CQA.store.get(this.draftKey(v), null, true);
      var restored = !!(saved && saved.config && !U.sameJson(saved.config, v.config));
      this.draft = restored ? saved.config : U.deepClone(v.config);
      this.expanded = new Set(restored && saved.expanded ? saved.expanded : []);

      var prev = this.previous(v);
      var publishBtn = !v.is_active ? h('button', { class: 'btn btn--primary', type: 'button', 'data-tip': t('cl.publishApplyTip'), onClick: function () { self.publish(v, this, true); } }, icon('checkCircle'), t('cl.publishApply')) : null;
      var applyBtn = v.is_active ? h('button', { class: 'btn btn--subtle', type: 'button', 'data-tip': t('cl.applyActiveTip'), onClick: function () {
        Promise.all([self.ensureConfig(act), self.ensureConfig(prev)]).then(function () {
          CQA.openApplyDialog({ version: v, active: act, baseline: prev });
        });
      } }, icon('flash'), t('cl.apply')) : null;
      var dryBtn = !v.is_active ? h('button', { class: 'btn', type: 'button', 'data-tip': t('cl.dryRunTitle'), onClick: function () { self.dryRun(v, this); } }, icon('compare'), t('cl.dryRun')) : null;
      var cmp = !v.is_active && act ? h('a', { class: 'btn btn--subtle', href: '#/checklists/' + cid + '/diff/' + act.id + '/' + v.id }, icon('compare'), t('cl.compareWithActive'))
        : prev ? h('a', { class: 'btn btn--subtle', href: '#/checklists/' + cid + '/diff/' + prev.id + '/' + v.id }, icon('compare'), t('cl.compareWithPrev', { v: prev.version })) : null;

      this.notice = h('div', { class: 'stack' });
      if (v.is_active && !(this.query && this.query.saved)) {
        this.notice.appendChild(h('div', { class: 'cl-how' }, icon('info', 'icon--16'), h('span', null, t('cl.howActive'))));
      }
      if (this.query && this.query.saved) {
        this.notice.appendChild(ui.nextStep({ icon: 'compare',
          title: t('cl.savedTitle', { v: v.version }) + '. ' + t('cl.nextCheck'),
          text: t(v.is_active ? 'cl.savedActiveText' : 'cl.savedDryText'),
          actions: [
            !v.is_active ? { label: t('cl.dryRun'), primary: true, icon: 'compare', onClick: function (e) { self.dryRun(v, e.currentTarget); } } : null,
            !v.is_active ? { label: t('cl.publishApply'), icon: 'checkCircle', onClick: function (e) { self.publish(v, e.currentTarget, true); } } : null] }));
      }
      if (restored) {
        this.notice.appendChild(ui.msgbar('info', t('cl.draftRestored'), null,
          h('button', { class: 'btn btn--small', type: 'button', onClick: function () { CQA.store.set(self.draftKey(v), null, true); self.renderEditor(v); } }, t('cl.discard'))));
      }
      this.errorsBox = h('div', { 'aria-live': 'assertive' });

      var focus = this.query && this.query.crit;
      if (focus) this.expanded.add(focus);
      this.editor = document.createElement('cqa-checklist-editor');
      this.editor.data = { config: this.draft, base: v.config, expanded: this.expanded, focus: focus || null };

      this.comment = h('input', { class: 'input', id: 'cl-comment', placeholder: t('cl.commentPlaceholder'), value: (restored && saved.comment) || '', maxlength: '300' });
      this.dirtyEl = h('span', { class: 'cl-savebar__status', 'aria-live': 'polite' });
      this.resetBtn = h('button', { class: 'btn btn--subtle', type: 'button', onClick: function () { self.reset(); } }, t('cl.discard'));
      this.saveOnlyBtn = h('button', { class: 'btn', type: 'button', 'data-tip': t('cl.saveOnlyTip'), onClick: function () { self.save(false); } }, t('cl.saveOnly'));
      this.saveBtn = h('button', { class: 'btn btn--primary', type: 'button', 'data-tip': t('cl.saveCheckTip'), onClick: function () { self.save(true); } }, icon('compare'), t('cl.saveCheck'));
      var savebar = h('div', { class: 'cl-savebar', role: 'region', 'aria-label': t('cl.saveRegion') },
        this.dirtyEl,
        h('label', { class: 'sr-only', for: 'cl-comment' }, t('cl.comment')),
        this.comment, this.resetBtn, this.saveOnlyBtn, this.saveBtn);

      CQA.dom.replace(this.main,
        h('section', { class: 'card cl-vhead' },
          h('div', { class: 'row row--between' },
            h('div', { class: 'stack', style: { gap: '2px' } },
              h('div', { class: 'row' },
                h('h2', { class: 't-subtitle1' }, t('cl.versionN', { v: v.version })),
                v.is_active ? ui.badge(t('cl.active'), 'brand-filled', 'checkCircle') : null,
                ui.badge(CQA.i18n.tx('vstatus.' + v.status, v.status), STATUS_TONE[v.status] || 'neutral')),
              h('div', { class: 'muted t-caption1' }, t('cl.createdAt', { d: fmt.date(v.created_at) }) + (v.comment ? ' · ' + v.comment : ''))),
            h('div', { class: 'row' }, cmp, dryBtn, publishBtn, applyBtn)),
          this.dryList = h('div', { class: 'cl-dry t-caption1' })),
        this.notice, this.errorsBox, this.editor, savebar);

      this.editor.addEventListener('cqa-change', function () { self.onChange(); });
      this.comment.addEventListener('input', function () { self.persist(); });
      this.onChange(true);
      this.loadDryRuns(v);
    }

    /** Пробные прогоны этой версии — ссылки на «было/стало». */
    loadDryRuns(v) {
      var box = this.dryList;
      CQA.api.runs().then(function (runs) {
        var list = (runs || []).filter(function (r) { return r.kind === 'dry_run' && r.checklist_version_id === v.id; });
        if (!list.length || !box.isConnected) return;
        CQA.dom.replace(box, h('span', { class: 'muted' }, t('cl.dryRuns')), list.slice(0, 5).map(function (r, i) {
          return [i ? ' · ' : ' ', h('a', { href: '#/runs/' + r.id + '/diff' }, '#' + r.id), h('span', { class: 'muted' }, ' ' + fmt.date(r.created_at) + ' · ' + CQA.i18n.tx('runStatus.' + r.status, r.status))];
        }));
      }).catch(function () { /* список прогонов не критичен */ });
    }

    /** Пробный прогон на 10 диалогах по изменённым критериям → «было/стало». */
    dryRun(v, btn) {
      if (this.dirty) { ui.toast(t('cl.dryDirty'), { tone: 'warning' }); return; }
      var label = btn ? Array.prototype.slice.call(btn.childNodes) : null;
      if (btn) { btn.disabled = true; CQA.dom.replace(btn, ui.spinner(), t('dash.starting')); }
      CQA.api.dryRun(v.id, { sample: 10 }).then(function (run) {
        CQA.router.go('/runs/' + run.id + '/diff');
      }, function (err) {
        if (btn) { btn.disabled = false; CQA.dom.replace(btn, label); }
        var ve = CQA.api.validationErrors(err);
        ui.toast(t('cl.dryFailed'), { tone: 'error', detail: ve.length ? ve.map(function (x) { return x.msg; }).join('; ') : err.message, timeout: 12000 });
      });
    }

    onChange(initial) {
      var v = this.version;
      var dirty = !U.sameJson(this.draft, v.config);
      this.dirty = dirty;
      var d = CQA.checklistDiff(v.config, this.draft);
      var parts = [];
      if (d.criteria.length) parts.push(t('cl.changedCriteria', { n: d.criteria.length, list: d.criteria.map(function (c) { return c.id; }).join(', ') }));
      if (d.top.length) parts.push(t('cl.changedGeneral'));
      this.dirtyEl.textContent = dirty ? t('cl.unsaved', { what: parts.join(' · ') }) : t('cl.noChanges');
      this.dirtyEl.classList.toggle('is-dirty', dirty);
      if (this.dirtyEl.parentNode) this.dirtyEl.parentNode.classList.toggle('is-dirty', dirty);
      this.saveBtn.disabled = !dirty;
      this.saveOnlyBtn.disabled = !dirty;
      this.resetBtn.disabled = !dirty;
      if (!initial) this.persist();
    }

    persist() {
      if (!this.version) return;
      var key = this.draftKey(this.version);
      if (!this.dirty) { CQA.store.set(key, null, true); return; }
      clearTimeout(this._persistT);
      var self = this;
      this._persistT = setTimeout(function () {
        CQA.store.set(key, { config: self.draft, comment: self.comment.value, expanded: Array.from(self.expanded) }, true);
      }, 300);
    }

    reset() {
      var self = this;
      ui.confirm(t('cl.discardTitle'), t('cl.discardText'), t('cl.discard'), true).then(function (ok) {
        if (!ok) return;
        CQA.store.set(self.draftKey(self.version), null, true);
        self.query = {};
        self.renderEditor(self.version);
      });
    }

    humanLoc(loc) {
      var m = /^criteria\.(\d+)(?:\.(\w+))?(?:\.(\d+))?(?:\.(\w+))?/.exec(loc);
      if (m) {
        var c = this.draft.criteria[Number(m[1])];
        var s = t('cl.locCriterion', { id: c ? c.id : '#' + (Number(m[1]) + 1) });
        if (m[2]) s += ' · ' + CQA.i18n.tx('field.' + m[2], m[2]);
        if (m[3] != null) s += ' ' + (Number(m[3]) + 1);
        return s;
      }
      var top = loc.split('.')[0];
      return CQA.i18n.tx('field.' + top, loc);
    }

    showErrors(errs, title) {
      var self = this;
      this.editor.clearErrors();
      var first = this.editor.showErrors(errs);
      CQA.dom.replace(this.errorsBox, ui.msgbar('danger', title || t('cl.errorsTitle', { n: errs.length }),
        h('ul', null, errs.slice(0, 8).map(function (e) { return h('li', null, h('b', null, self.humanLoc(e.loc) + ': '), e.msg); }),
          errs.length > 8 ? h('li', null, t('cl.moreErrors', { n: errs.length - 8 })) : null)));
      var target = first || this.errorsBox;
      CQA.dom.scrollIntoPane(target, { block: 'start' });
      var ctl = target.querySelector && target.querySelector('[aria-invalid="true"]');
      if (ctl) ctl.focus({ preventScroll: true });
    }

    save(check) {
      var self = this, v = this.version;
      CQA.dom.clear(this.errorsBox);
      var errs = CQA.scoring.validateConfig(this.draft);
      if (errs.length) { this.showErrors(errs); return; }
      this.editor.clearErrors();
      var btn = check ? this.saveBtn : this.saveOnlyBtn;
      this.saveBtn.disabled = true; this.saveOnlyBtn.disabled = true;
      CQA.dom.replace(btn, ui.spinner(), check ? t('cl.savingCheck') : t('cl.saving'));
      var comment = this.comment.value.trim() || null;
      CQA.api.createChecklistVersion(this.cid, this.draft, comment).then(function (nv) {
        CQA.store.set(self.draftKey(v), null, true);
        if (!nv.config) nv.config = U.deepClone(self.draft);
        self.versions.unshift(nv);
        self.versions.sort(function (a, b) { return b.version - a.version; });
        if (!check) {
          ui.toast(t('cl.savedTitle', { v: nv.version }), { tone: 'success' });
          CQA.router.go('/checklists/' + encodeURIComponent(self.cid) + '/v/' + nv.id, { saved: 1 });
          return;
        }
        return CQA.api.dryRun(nv.id, { sample: 10 }).then(function (run) {
          ui.toast(t('cl.savedChecking', { v: nv.version }), { tone: 'success' });
          CQA.router.go('/runs/' + run.id + '/diff');
        }, function (err) {
          var ve = CQA.api.validationErrors(err);
          ui.toast(t('cl.dryFailed'), { tone: 'warning', detail: ve.length ? ve.map(function (x) { return x.msg; }).join('; ') : err.message, timeout: 12000 });
          CQA.router.go('/checklists/' + encodeURIComponent(self.cid) + '/v/' + nv.id, { saved: 1 });
        });
      }, function (err) {
        self.saveBtn.disabled = false; self.saveOnlyBtn.disabled = false;
        CQA.dom.replace(self.saveBtn, icon('compare'), t('cl.saveCheck'));
        CQA.dom.replace(self.saveOnlyBtn, t('cl.saveOnly'));
        var ve = CQA.api.validationErrors(err);
        if (ve.length) self.showErrors(ve, t('cl.serverErrors'));
        else CQA.dom.replace(self.errorsBox, ui.msgbar('danger', t('cl.saveFailed'), err.message));
      });
    }

    publish(v, btn, thenApply, confirmed) {
      var self = this;
      if (this.dirty) {
        ui.toast(t('cl.publishDirty'), { tone: 'warning' });
        return;
      }
      if (!confirmed) {
        ui.confirm(t('cl.publishConfirm', { v: v.version }), t('cl.publishConfirmText'), t('cl.publishConfirmOk')).then(function (ok) { if (ok) self.publish(v, btn, thenApply, true); });
        return;
      }
      if (btn) btn.disabled = true;
      CQA.api.publishVersion(v.id).then(function (pv) {
        self.versions.forEach(function (x) { x.is_active = x.id === v.id; if (x.id === v.id && pv && pv.status) x.status = pv.status; });
        ui.toast(t('cl.published', { v: v.version }), { tone: 'success', action: { label: t('cl.apply'), onClick: function () {
          var prev = self.previous(v);
          self.ensureConfig(prev).then(function () { CQA.openApplyDialog({ version: v, active: v, baseline: prev }); });
        } } });
        self.query = {};
        CQA.router.setQuery({});
        self.renderAll();
        if (thenApply) {
          var prev = self.previous(v);
          self.ensureConfig(prev).then(function () { CQA.openApplyDialog({ version: v, active: v, baseline: prev }); });
        }
      }, function (err) {
        if (btn) btn.disabled = false;
        ui.toast(t('cl.publishFailed'), { tone: 'error', detail: err.message });
      });
    }

    // ------------------------------------------------------------ сравнение версий
    renderDiff(a, b) {
      var self = this;
      var cid = encodeURIComponent(this.cid);
      if (!a || !b) { CQA.dom.replace(this.main, ui.empty(t('cl.versionNotFound'), null, null, 'warning')); return; }
      document.title = t('diff.title', { a: a.version, b: b.version }) + ' · CQA';
      var opt = function (sel) {
        return self.versions.map(function (v) { return h('option', { value: v.id, selected: v.id === sel.id }, t('cl.versionN', { v: v.version }) + (v.is_active ? ' · ' + t('cl.active') : '')); });
      };
      var selA = h('select', { class: 'select', id: 'diff-a', 'aria-label': t('diff.a') }, opt(a));
      var selB = h('select', { class: 'select', id: 'diff-b', 'aria-label': t('diff.b') }, opt(b));
      var go = function () { CQA.router.go('/checklists/' + cid + '/diff/' + selA.value + '/' + selB.value); };
      selA.addEventListener('change', go);
      selB.addEventListener('change', go);
      var box = h('div', { class: 'stack stack--l' }, ui.skeleton('card', 5));
      CQA.dom.replace(this.main,
        h('section', { class: 'card' },
          h('div', { class: 'row diff-head' },
            h('h2', { class: 't-subtitle1' }, t('diff.heading')),
            h('span', { class: 'spacer' }),
            h('label', { class: 'muted', for: 'diff-a' }, t('diff.from')), selA,
            h('button', { class: 'btn btn--subtle btn--icon', type: 'button', 'aria-label': t('diff.swap'), title: t('diff.swap'),
              onClick: function () { CQA.router.go('/checklists/' + cid + '/diff/' + selB.value + '/' + selA.value); } }, icon('arrowSort')),
            h('label', { class: 'muted', for: 'diff-b' }, t('diff.to')), selB)),
        box);
      Promise.all([this.ensureConfig(a), this.ensureConfig(b)]).then(function () {
        var d = document.createElement('cqa-checklist-diff');
        d.data = { a: a, b: b };
        CQA.dom.replace(box, d);
      }, function (err) { CQA.dom.replace(box, ui.error(err, function () { self.renderDiff(a, b); })); });
    }
  }

  customElements.define('cqa-page-checklist', CqaPageChecklist);
})(window.CQA = window.CQA || {});
