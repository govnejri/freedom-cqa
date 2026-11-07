/* Прецеденты (P2): «оценка учится на апелляциях».
   <cqa-precedent-flow> .data = {precedents: [PrecedentDTO], compact: bool} — путь в три шага:
     1) «Создать версию чек-листа с прецедентом» → POST /precedents/to-version → что добавлено (критерий, пример);
     2) «Проверить на похожих диалогах» → GET /precedents/{id}/similar → выбор диалогов;
     3) пробный прогон POST /checklist-versions/{vid}/dry-run {uids, criteria} → #/runs/{id}/diff.
   <cqa-precedents> .data = {cid} — вкладка чек-листа «Прецеденты»: список, в какой версии добавлен, выбор нескольких. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  var SIMILAR_MAX = 5;

  function versionHref(vid) { return '#/checklists/' + encodeURIComponent(CQA.config.checklistId) + '/v/' + vid; }

  /** Типы и верхние ступени критериев активной версии — чтобы вердикт прецедента окрасить правильно (флаг «Да» — красный). */
  var metaP = null, META = {};
  function loadMeta() {
    if (metaP) return metaP;
    metaP = CQA.api.checklistVersions(CQA.config.checklistId).then(function (list) {
      var act = (list || []).filter(function (v) { return v.is_active; })[0] || (list || [])[0];
      return act ? CQA.api.checklistVersion(act.id) : null;
    }).then(function (v) {
      ((v && v.config && v.config.criteria) || []).forEach(function (c) { META[c.id] = { type: c.type, top: CQA.scoring.top(c) }; });
      return META;
    }).catch(function () { metaP = null; return META; });
    return metaP;
  }
  function pv(cid, v, opts) { var m = META[cid] || {}; return CQA.verdict(v, m.type || 'yes_no', m.top, opts); }

  /** Пример из прецедента так, как он попадёт в чек-лист: вердикт, фрагмент, почему. */
  function exampleView(cid, verdict, fragment, why) {
    return h('div', { class: 'prec-example' },
      h('div', { class: 'row' }, h('span', { class: 'crit__label' }, t('prec.exampleVerdict')), pv(cid, verdict)),
      h('div', { class: 'prec-example__frag' }, fragment),
      why ? h('div', { class: 't-caption1 subtle' }, h('b', null, t('prec.why') + ': '), why) : null);
  }

  // ================================================================== поток «прецедент → версия → проверка»
  class CqaPrecedentFlow extends HTMLElement {
    set data(d) {
      this._d = d;
      this.precs = d.precedents || [];
      var withV = this.precs.filter(function (p) { return p.added_in_version_id; });
      // все уже добавлены в одну версию — сразу ко второму шагу
      this.version = withV.length === this.precs.length && withV.length && withV.every(function (p) { return p.added_in_version_id === withV[0].added_in_version_id; })
        ? { version_id: withV[0].added_in_version_id, version: withV[0].added_in_version, status: withV[0].added_in_version_status, existing: true } : null;
      this.similar = null;
      var self = this;
      loadMeta().then(function () { self.render(); });
    }

    step() { return this.similar ? 2 : this.version ? 1 : 0; }

    render() {
      var self = this, precs = this.precs;
      this.textContent = '';
      this.classList.add('prec-flow');
      if (!precs.length) return;
      this.appendChild(CQA.steps([
        { title: t('prec.step1'), text: t('prec.step1Text') },
        { title: t('prec.step2'), text: t('prec.step2Text') },
        { title: t('prec.step3'), text: t('prec.step3Text') }
      ], this.step()));
      this.status = h('div', { 'aria-live': 'polite' });
      if (!this.version) {
        var p0 = precs[0];
        var already = precs.filter(function (p) { return p.in_active_version; }).length;
        var btn = h('button', { class: 'btn btn--primary', type: 'button' }, icon('checklist'), precs.length > 1 ? t('prec.toVersionN', { n: precs.length }) : t('prec.toVersion'));
        btn.addEventListener('click', function () { self.toVersion(btn); });
        this.appendChild(h('div', { class: 'stack' },
          precs.length === 1 ? h('div', { class: 'prec-preview' },
            h('div', { class: 'row' }, CQA.critLabel(p0.criterion_id, p0.criterion_title)),
            exampleView(p0.criterion_id, p0.verdict, p0.fragment, p0.why)) : null,
          already ? ui.msgbar('info', t('prec.inActive', { n: already })) : null,
          h('p', { class: 'muted t-caption1' }, t('prec.toVersionNote')),
          h('div', { class: 'row' }, btn),
          this.status));
        return;
      }
      this.appendChild(this.versionBlock());
      if (!this.similar) {
        var sb = h('button', { class: 'btn btn--primary', type: 'button' }, icon('search'), t('prec.findSimilar'));
        sb.addEventListener('click', function () { self.findSimilar(sb); });
        this.appendChild(h('div', { class: 'stack' }, h('p', { class: 'muted t-caption1' }, t('prec.findSimilarNote')), h('div', { class: 'row' }, sb), this.status));
        return;
      }
      this.appendChild(this.similarBlock());
      this.appendChild(this.status);
    }

    toVersion(btn) {
      var self = this;
      btn.disabled = true;
      CQA.dom.replace(btn, ui.spinner(), t('prec.creating'));
      CQA.api.precedentsToVersion(this.precs.map(function (p) { return p.id; }), null).then(function (r) {
        self.version = r;
        self.precs.forEach(function (p) { if (r.added.indexOf(p.id) >= 0) { p.added_in_version_id = r.version_id; p.added_in_version = r.version; p.added_in_version_status = r.status; } });
        ui.toast(t('prec.created', { v: r.version }), { tone: 'success' });
        self.dispatchEvent(new CustomEvent('cqa-precedents-changed', { bubbles: true }));
        self.render();
      }, function (err) {
        btn.disabled = false;
        CQA.dom.replace(btn, icon('checklist'), t('prec.toVersion'));
        CQA.dom.replace(self.status, ui.msgbar('danger', t('prec.createFailed'), U.errText(err)));
      });
    }

    /** Что вошло в версию: критерий и добавленный пример (из diff ответа to-version или из самих прецедентов). */
    versionBlock() {
      var v = this.version, precs = this.precs;
      var items = [];
      (v.diff || []).forEach(function (d) {
        if (d.scope !== 'criterion' || d.change !== 'changed' || !d.after || !d.after.examples) return;
        var before = (d.before && d.before.examples) || [];
        var key = function (e) { return e.verdict + '|' + e.fragment; };
        var old = {};
        before.forEach(function (e) { old[key(e)] = 1; });
        d.after.examples.filter(function (e) { return !old[key(e)]; }).forEach(function (e) {
          var p = precs.filter(function (x) { return x.criterion_id === d.id; })[0];
          items.push({ cid: d.id, title: p ? p.criterion_title : '', ex: e });
        });
      });
      if (!items.length) precs.forEach(function (p) { items.push({ cid: p.criterion_id, title: p.criterion_title, ex: { verdict: p.verdict, fragment: p.fragment, why: p.why } }); });
      var act = v.parent_version != null ? t('prec.fromActive', { v: v.parent_version }) : '';
      var cmp = v.parent_id ? '#/checklists/' + encodeURIComponent(CQA.config.checklistId) + '/diff/' + v.parent_id + '/' + v.version_id : null;
      return h('div', { class: 'prec-version stack' },
        h('div', { class: 'row' },
          icon('checkCircle', 'prec-version__ok'),
          h('b', null, v.existing ? t('prec.inVersion', { v: v.version }) : t('prec.versionCreated', { v: v.version })),
          ui.badge(CQA.i18n.tx('vstatus.' + (v.status || 'draft'), v.status || 'draft'), v.status === 'published' ? 'success' : 'neutral'),
          act ? h('span', { class: 'muted t-caption1' }, act) : null),
        h('div', { class: 'stack' }, items.map(function (it) {
          return h('div', { class: 'prec-added' },
            h('div', { class: 'row' }, h('span', { class: 'diff-sign diff-add', 'aria-label': t('diff.added') }, '+'), CQA.critLabel(it.cid, it.title), h('span', { class: 'muted t-caption1' }, t('prec.addedExample'))),
            exampleView(it.cid, it.ex.verdict, it.ex.fragment, it.ex.why));
        })),
        (v.skipped || []).length ? ui.msgbar('info', t('prec.skipped', { n: v.skipped.length }), v.skipped.map(function (s) { return '#' + s.id + ': ' + s.reason; }).join('; ')) : null,
        (v.warnings || []).length ? ui.msgbar('warning', t('prec.warnings'), h('ul', null, v.warnings.map(function (w) { return h('li', null, w); }))) : null,
        h('div', { class: 'row t-caption1' },
          h('a', { href: versionHref(v.version_id) }, icon('checklist', 'icon--16'), ' ', t('prec.openVersion', { v: v.version })),
          cmp ? h('a', { href: cmp }, icon('compare', 'icon--16'), ' ', t('prec.compareActive')) : null),
        h('p', { class: 'muted t-caption1' }, t('prec.notPublished')),
        this.dryRunsOf(v.version_id));
    }

    /** Уже запущенные пробные прогоны этой версии — ссылки на «было/стало» (путь не теряется после перезагрузки). */
    dryRunsOf(vid) {
      var box = h('div', { class: 'row t-caption1 prec-runs' });
      CQA.api.runs().then(function (runs) {
        var list = (runs || []).filter(function (r) { return r.kind === 'dry_run' && r.checklist_version_id === vid; });
        if (!list.length || !box.isConnected) return;
        CQA.dom.replace(box, icon('compare', 'icon--16'), h('span', { class: 'muted' }, t('prec.dryRuns')), list.slice(0, 5).map(function (r) {
          return h('a', { href: '#/runs/' + r.id + '/diff' }, '#' + r.id + ' · ' + CQA.i18n.tx('runStatus.' + r.status, r.status));
        }));
      }).catch(function () { /* список прогонов не критичен */ });
      return box;
    }

    findSimilar(btn) {
      var self = this;
      btn.disabled = true;
      CQA.dom.replace(btn, ui.spinner(), t('prec.searching'));
      Promise.all(this.precs.map(function (p) { return CQA.api.precedentSimilar(p.id, SIMILAR_MAX); })).then(function (list) {
        var seen = {}, items = [];
        list.forEach(function (r) {
          r.items.forEach(function (it) { if (!seen[it.uid]) { seen[it.uid] = 1; it.criterion_id = r.criterion_id; items.push(it); } });
        });
        self.similar = { items: items, meta: list[0] };
        self.picked = new Set(items.slice(0, SIMILAR_MAX).map(function (x) { return x.uid; }));
        self.render();
      }, function (err) {
        btn.disabled = false;
        CQA.dom.replace(btn, icon('search'), t('prec.findSimilar'));
        CQA.dom.replace(self.status, ui.msgbar('danger', t('prec.similarFailed'), U.errText(err)));
      });
    }

    similarBlock() {
      var self = this, s = this.similar, meta = s.meta || {};
      if (!s.items.length) return ui.msgbar('warning', t('prec.noSimilar'), t('prec.noSimilarText'));
      var countEl = h('b');
      var runBtn = h('button', { class: 'btn btn--primary', type: 'button' }, icon('compare'), t('prec.runDry'));
      var sync = function () {
        countEl.textContent = String(self.picked.size);
        runBtn.disabled = !self.picked.size;
      };
      runBtn.addEventListener('click', function () { self.runDry(runBtn); });
      var MATCH = [['reason', t('prec.mReason')], ['verdict', t('prec.mVerdict')], ['language', t('prec.mLang')], ['channel', t('prec.mChannel')]];
      var list = h('ul', { class: 'prec-similar' }, s.items.map(function (it) {
        var cb = h('input', { type: 'checkbox', checked: self.picked.has(it.uid) });
        cb.addEventListener('change', function () { if (cb.checked) self.picked.add(it.uid); else self.picked.delete(it.uid); sync(); });
        return h('li', null, h('label', { class: 'prec-similar__item' }, cb,
          h('span', { class: 'prec-similar__main' },
            h('span', { class: 'row' },
              h('a', { class: 'mono t-body1-strong', href: CQA.convHref(it.uid, { crit: it.criterion_id }) }, it.external_id),
              h('span', { class: 'muted t-caption1' }, it.language + ' · ' + U.channelLabel(it.channel)),
              it.verdict ? [h('span', { class: 'muted t-caption1' }, t('prec.nowVerdict')), pv(it.criterion_id, it.verdict)] : null),
            h('span', { class: 'row prec-similar__match' }, MATCH.filter(function (m) { return it.match && it.match[m[0]]; }).map(function (m) {
              return ui.badge(m[0] === 'reason' && it.contact_reason ? t('prec.mReasonV', { r: it.contact_reason }) : m[1], m[0] === 'reason' || m[0] === 'verdict' ? 'brand' : 'neutral');
            }))),
          it.total != null ? CQA.score(it.total) : null));
      }));
      sync();
      return h('div', { class: 'stack' },
        h('div', { class: 't-body1-strong' }, t('prec.similarTitle', { n: s.items.length })),
        h('div', { class: 'muted t-caption1' }, t('prec.similarSub', { r: meta.contact_reason || '—', v: meta.original_verdict || '—' })),
        list,
        h('div', { class: 'row' }, runBtn, h('span', { class: 'muted t-caption1' }, t('prec.pickedN'), ' ', countEl, ' · ', t('prec.dryCost'))));
    }

    runDry(btn) {
      var self = this, vid = this.version.version_id;
      var crits = [];
      this.precs.forEach(function (p) { if (crits.indexOf(p.criterion_id) < 0) crits.push(p.criterion_id); });
      btn.disabled = true;
      CQA.dom.replace(btn, ui.spinner(), t('dash.starting'));
      CQA.api.dryRun(vid, { uids: Array.from(this.picked), criteria: crits }).then(function (run) {
        CQA.router.go('/runs/' + run.id + '/diff');
      }, function (err) {
        btn.disabled = false;
        CQA.dom.replace(btn, icon('compare'), t('prec.runDry'));
        CQA.dom.replace(self.status, ui.msgbar('danger', t('cl.dryFailed'), U.errText(err)));
      });
    }
  }

  // ================================================================== вкладка «Прецеденты» чек-листа
  class CqaPrecedents extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.picked = new Set();
      this.flowBox = h('div');
      this.box = h('div', null, ui.skeleton('table', 5));
      this.textContent = '';
      this.append(this.flowBox, this.box);
      var self = this;
      this.addEventListener('cqa-precedents-changed', function () { self.load(true); });
      this.load();
    }

    load(keepFlow) {
      var self = this;
      Promise.all([CQA.api.precedents(), loadMeta()]).then(function (res) {
        var list = res[0];
        self.list = list || [];
        self.render();
      }, function (err) { CQA.dom.replace(self.box, ui.error(err, function () { self.load(); })); });
      if (!keepFlow) CQA.dom.clear(this.flowBox);
    }

    render() {
      var self = this, list = this.list;
      if (!list.length) {
        CQA.dom.replace(this.box, h('section', { class: 'card' }, ui.empty(t('prec.empty'), t('prec.emptyText'),
          h('a', { class: 'btn btn--primary', href: '#/appeals?status=open' }, icon('scale'), t('prec.toAppeals')), 'scale')));
        return;
      }
      var bar = h('div', { class: 'prec-bar row' });
      var syncBar = function () {
        var n = self.picked.size;
        CQA.dom.replace(bar,
          h('span', { class: 'muted' }, n ? t('prec.selected', { n: n }) : t('prec.selectHint')),
          h('span', { class: 'spacer' }),
          h('button', { class: 'btn btn--primary', type: 'button', disabled: !n || null, onClick: function () { self.startFlow(); } }, icon('checklist'), t('prec.toVersionN', { n: n || 0 })));
      };
      var tbody = h('tbody', null, list.map(function (p) {
        var cb = h('input', { type: 'checkbox', checked: self.picked.has(p.id), 'aria-label': t('prec.pickAria', { id: p.id }) });
        cb.addEventListener('change', function () { if (cb.checked) self.picked.add(p.id); else self.picked.delete(p.id); syncBar(); });
        var vcell = p.added_in_version_id
          ? h('a', { href: versionHref(p.added_in_version_id) }, ui.badge(t('cl.versionN', { v: p.added_in_version }) + ' · ' + CQA.i18n.tx('vstatus.' + p.added_in_version_status, p.added_in_version_status || ''), p.added_in_version_status === 'published' ? 'success' : 'neutral'))
          : h('span', { class: 'muted t-caption1' }, t('prec.notAdded'));
        return h('tr', null,
          h('td', null, h('label', { class: 'check' }, cb, h('span', { class: 'mono muted' }, '#' + p.id))),
          h('td', null, CQA.critLabel(p.criterion_id, p.criterion_title)),
          h('td', null, h('span', { class: 'row row--nowrap' },
            p.original_verdict && p.original_verdict !== p.verdict ? [pv(p.criterion_id, p.original_verdict, { strike: true }), icon('arrowRight', 'icon--16 muted')] : null,
            pv(p.criterion_id, p.verdict))),
          h('td', { class: 'prec-frag' }, h('div', { class: 'prec-example__frag' }, p.fragment), p.why ? h('div', { class: 't-caption1 subtle' }, h('b', null, t('prec.why') + ': '), p.why) : null),
          h('td', { class: 'nowrap' },
            p.uid ? h('a', { class: 'mono', href: CQA.convHref(p.uid, { crit: p.criterion_id }) }, p.external_id) : '—',
            p.appeal_id ? h('div', { class: 't-caption1' }, h('a', { href: '#/appeals/' + p.appeal_id }, t('crit.appealN', { id: p.appeal_id }))) : null),
          h('td', null, vcell, p.in_active_version ? h('div', null, ui.badge(t('prec.inActiveBadge'), 'brand-filled', 'checkCircle')) : null,
            p.holdout_family ? h('div', null, ui.badge(t('prec.holdout'), 'warning', 'warning', { title: t('prec.holdoutTitle', { f: p.holdout_family }) })) : null),
          h('td', { class: 'muted t-caption1 nowrap' }, fmt.date(p.created_at)));
      }));
      syncBar();
      CQA.dom.replace(this.box, h('section', { class: 'card', 'aria-labelledby': 'prec-list-title' },
        h('div', { class: 'card__header' },
          h('div', null, h('h2', { class: 'card__title', id: 'prec-list-title' }, t('prec.listTitle', { n: list.length })),
            h('div', { class: 'muted t-caption1' }, t('prec.listSub')))),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact prec-table' },
          h('thead', null, h('tr', null, h('th', { scope: 'col' }, ''), h('th', { scope: 'col' }, t('prec.cCrit')), h('th', { scope: 'col' }, t('prec.cVerdict')),
            h('th', { scope: 'col' }, t('prec.cFragment')), h('th', { scope: 'col' }, t('prec.cDialog')), h('th', { scope: 'col' }, t('prec.cVersion')), h('th', { scope: 'col' }, t('prec.cDate')))),
          tbody)),
        bar));
    }

    startFlow() {
      var self = this;
      var precs = this.list.filter(function (p) { return self.picked.has(p.id); });
      var flow = document.createElement('cqa-precedent-flow');
      flow.data = { precedents: precs };
      CQA.dom.replace(this.flowBox, h('section', { class: 'card prec-card', 'aria-labelledby': 'prec-flow-title' },
        h('div', { class: 'card__header' },
          h('h2', { class: 'card__title row', id: 'prec-flow-title' }, icon('sparkle'), t('prec.flowTitle')),
          h('button', { class: 'btn btn--small btn--subtle', type: 'button', onClick: function () { CQA.dom.clear(self.flowBox); } }, icon('close', 'icon--16'), t('action.close'))),
        flow));
      CQA.dom.scrollIntoPane(this.flowBox, { block: 'start' });
    }
  }

  customElements.define('cqa-precedent-flow', CqaPrecedentFlow);
  customElements.define('cqa-precedents', CqaPrecedents);
})(window.CQA = window.CQA || {});
