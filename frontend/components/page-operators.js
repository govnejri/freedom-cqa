/* Операторы (P2).
   <cqa-page-operators> #/operators — список: диалогов, средний балл, грубые ошибки, апелляции; «Не назначены» (звонки
     без имени) с ручным назначением (PUT /conversations/{uid}/operator); выбор «это я» для раздела оператора.
   <cqa-page-operator> #/operators/{name} — профиль: балл против отдела, критерии против отдела, 3 зоны роста с примерами
     и кнопкой «Потренироваться», сильные стороны, грубые ошибки, тренировки «до/после», диалоги, «Сводка от ИИ». */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  function same(a, b) { return String(a || '').toLocaleLowerCase('ru') === String(b || '').toLocaleLowerCase('ru'); }

  function marks(d) {
    var out = [];
    if (d.gross) out.push(ui.badge(d.gross_categories && d.gross_categories.length ? t('badge.grossShort', { c: d.gross_categories.join(', ') }) : t('badge.gross'), 'danger-filled', 'warning'));
    if (d.needs_review) out.push(ui.badge(t('badge.review'), 'warning', 'eye'));
    if (d.n_open_appeals) out.push(ui.badge(t('badge.appeal', { n: d.n_open_appeals }), 'brand', 'scale'));
    if (d.manual) out.push(ui.badge(t('op.manualBadge'), 'neutral', 'edit', { title: t('op.manualTitle') }));
    return out;
  }

  /** Качество критерия словами: «Да» в 1 из 3 / ступень 16,7 из 20 / нарушений 0 из 6. */
  CQA.qualityText = function (c, st) {
    if (!st || !st.n_applicable) return t('op.qNone');
    if (c.type === 'scale') return t('op.qScale', { v: fmt.num(st.mean_step, 1), top: c.top, n: st.n_applicable });
    if (c.type === 'flag') return t('op.qFlag', { v: st.n_yes, n: st.n_applicable });
    return t('op.qYes', { v: st.n_yes, n: st.n_applicable });
  };

  function qualityTone(q) { return q == null ? null : q >= 0.85 ? 'success' : q >= 0.6 ? 'mid' : 'danger'; }
  function pp(d) { return d == null ? null : Math.round(d * 1000) / 10; }   // доля → п.п.

  // ================================================================== список
  class CqaPageOperators extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      document.title = t('op.listTitle') + ' · CQA';
      var self = this;
      this.sort = '-n_dialogs';
      this.filter = '';
      this.sub = h('div', { class: 'page-header__sub' });
      this.note = h('div');
      this.unBox = h('div');
      this.search = h('input', { class: 'input', type: 'search', placeholder: t('op.search'), 'aria-label': t('op.search') });
      this.search.addEventListener('input', U.debounce(function () { self.filter = self.search.value.trim(); self.renderTable(); }, 150));
      this.box = h('div', { class: 'list-box' }, ui.skeleton('table', 8));
      this.textContent = '';
      this.appendChild(h('div', { class: 'page op-page' },
        h('div', { class: 'page-header' }, h('div', null, h('h1', null, t('op.listTitle')), this.sub)),
        h('div', { class: 'stack stack--l' },
          this.note,
          this.unBox,
          h('div', { class: 'filters card' }, h('div', { class: 'input-with-icon filters__search' }, icon('search'), this.search)),
          this.box)));
      this.onMe = function () { self.renderNote(); self.renderTable(); };
      document.addEventListener('cqa-me-changed', this.onMe);
      this.load();
    }

    disconnectedCallback() { document.removeEventListener('cqa-me-changed', this.onMe); }

    load() {
      var self = this;
      if (!this.data) CQA.dom.replace(this.box, ui.skeleton('table', 8));
      return Promise.all([CQA.api.operators(), CQA.api.conversations({}).catch(function () { return []; })]).then(function (res) {
        self.data = res[0];
        self.convs = {};
        (res[1] || []).forEach(function (s) { self.convs[s.uid] = s; });
        self.sub.textContent = t('op.listSub', { n: self.data.n_operators, d: self.data.n_dialogs, a: fmt.score(self.data.avg_total) });
        self.renderNote();
        self.renderUnassigned();
        self.renderTable();
      }, function (err) {
        CQA.dom.replace(self.box, ui.error(err, function () { self.load(); }));
      });
    }

    renderNote() {
      var me = CQA.me.get();
      var role = document.documentElement.getAttribute('data-role');
      if (me) {
        CQA.dom.replace(this.note, ui.msgbar('success', t('op.meIs', { name: me }), t('op.meText'),
          h('div', { class: 'row' },
            h('a', { class: 'btn btn--small btn--primary', href: CQA.operatorHref(me) }, icon('person'), t('op.openMine')),
            h('button', { class: 'btn btn--small', type: 'button', onClick: function () { CQA.me.set(null); } }, t('op.meReset')))));
      } else if (role === 'operator') {
        CQA.dom.replace(this.note, ui.msgbar('info', t('op.pickMeTitle'), t('op.pickMeText')));
      } else CQA.dom.clear(this.note);
    }

    renderUnassigned() {
      var self = this, list = this.data.unassigned || [];
      if (!list.length) { CQA.dom.clear(this.unBox); return; }
      var names = (this.data.operators || []).map(function (o) { return o.name; });
      var dl = h('datalist', { id: 'op-names' }, names.map(function (n) { return h('option', { value: n }); }));
      var rows = list.map(function (uid) {
        var s = self.convs[uid] || { uid: uid, external_id: fmt.externalId(uid) };
        var input = h('input', { class: 'input input--small', list: 'op-names', placeholder: t('op.assignPlaceholder'), 'aria-label': t('op.assignAria', { id: s.external_id }), maxlength: '200' });
        var btn = h('button', { class: 'btn btn--small', type: 'submit' }, icon('person'), t('op.assign'));
        var form = h('form', { class: 'row row--nowrap op-assign' }, input, btn);
        form.addEventListener('submit', function (e) {
          e.preventDefault();
          var name = input.value.trim();
          if (!name) { input.setAttribute('aria-invalid', 'true'); input.focus(); return; }
          input.removeAttribute('aria-invalid');
          btn.disabled = true;
          CQA.dom.replace(btn, ui.spinner(), t('op.assigning'));
          CQA.api.assignOperator(uid, name).then(function (r) {
            ui.toast(t('op.assigned', { id: s.external_id, name: r.operator_name }), { tone: 'success', action: { label: t('op.openProfile'), href: CQA.operatorHref(r.operator_name) } });
            self.load();
          }, function (err) {
            btn.disabled = false;
            CQA.dom.replace(btn, icon('person'), t('op.assign'));
            ui.toast(t('op.assignFailed'), { tone: 'error', detail: U.errText(err) });
          });
        });
        var score = s.final_total != null ? s.final_total : s.total;
        return h('li', { class: 'op-un__row' },
          h('div', { class: 'op-un__id' },
            h('a', { class: 'mono t-body1-strong', href: CQA.convHref(uid) }, s.external_id),
            h('span', { class: 'row row--nowrap muted t-caption1' }, s.channel ? icon(U.channelIcon(s.channel), 'icon--16') : null,
              [s.channel ? U.channelLabel(s.channel) : null, s.language].filter(Boolean).join(' · '))),
          score != null ? CQA.score(score) : h('span', { class: 'muted' }, '—'),
          form);
      });
      var LIMIT = 4;
      var ul = h('ul', { class: 'op-un__list' }, rows.slice(0, this.unAll ? rows.length : LIMIT));
      var more = rows.length > LIMIT && !this.unAll ? h('button', { class: 'btn btn--small btn--subtle', type: 'button', onClick: function () {
        self.unAll = true; rows.slice(LIMIT).forEach(function (r) { ul.appendChild(r); }); more.remove();
      } }, icon('chevronDown', 'icon--16'), t('op.unMore', { n: rows.length - LIMIT })) : null;
      CQA.dom.replace(this.unBox, h('section', { class: 'card op-un', 'aria-labelledby': 'op-un-title' },
        h('div', { class: 'card__header' },
          h('div', null,
            h('h2', { class: 'card__title row', id: 'op-un-title' }, icon('phone'), t('op.unTitle', { n: list.length })),
            h('div', { class: 'muted t-caption1' }, t('op.unText')))),
        dl, ul, more));
    }

    renderTable() {
      if (!this.data) return;
      var self = this, me = CQA.me.get();
      var q = this.filter.toLocaleLowerCase('ru');
      var rows = this.data.operators.filter(function (o) { return !q || o.name.toLocaleLowerCase('ru').indexOf(q) >= 0; });
      var key = this.sort.replace(/^-/, ''), dir = this.sort.charAt(0) === '-' ? -1 : 1;
      rows.sort(function (a, b) {
        var x = a[key], y = b[key];
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        if (typeof x === 'number') return (x - y) * dir || a.name.localeCompare(b.name, 'ru');
        return String(x).localeCompare(String(y), 'ru') * dir;
      });
      if (!rows.length) {
        CQA.dom.replace(this.box, ui.empty(this.data.operators.length ? t('op.emptyFiltered') : t('op.empty'), this.data.operators.length ? null : t('op.emptyText'), null, 'person'));
        return;
      }
      var cols = [
        { key: 'name', label: t('op.colName') },
        { key: 'n_dialogs', label: t('op.colDialogs'), num: true },
        { key: 'avg_total', label: t('op.colAvg'), num: true },
        { key: 'gross_count', label: t('op.colGross'), num: true },
        { key: 'n_appeals', label: t('op.colAppeals'), num: true },
        { key: null, label: t('op.colWhere'), cls: 'col-operator' },
        { key: null, label: '' }
      ];
      var thead = h('tr', null, cols.map(function (c) {
        if (!c.key) return h('th', { scope: 'col', class: c.cls || null }, c.label);
        var state = key === c.key ? (dir < 0 ? 'descending' : 'ascending') : 'none';
        return h('th', { scope: 'col', class: c.num ? 'num' : null, 'aria-sort': state },
          h('button', { class: 'sort-btn', type: 'button', onClick: function () {
            self.sort = key === c.key ? (dir < 0 ? c.key : '-' + c.key) : (c.num ? '-' + c.key : c.key);
            self.renderTable();
          } }, c.label, icon(state === 'descending' ? 'arrowDown' : state === 'ascending' ? 'arrowUp' : 'arrowSort')));
      }));
      var avg = this.data.avg_total;
      var tbody = h('tbody', null, rows.map(function (o) {
        var href = CQA.operatorHref(o.name);
        var isMe = same(o.name, me);
        var tr = h('tr', { class: 'is-clickable' + (isMe ? ' is-selected' : '') },
          h('td', null, h('a', { class: 'op-name', href: href }, o.name), isMe ? ui.badge(t('op.you'), 'brand') : null),
          h('td', { class: 'num' }, String(o.n_dialogs)),
          h('td', { class: 'num' }, h('span', { class: 'row row--nowrap op-avg' }, CQA.score(o.avg_total),
            avg != null && o.avg_total != null ? h('span', { class: 't-caption1' }, CQA.delta(o.avg_total - avg)) : null)),
          h('td', { class: 'num' }, o.gross_count ? h('span', { class: 'reasons__gross' }, String(o.gross_count)) : h('span', { class: 'muted' }, '0')),
          h('td', { class: 'num' }, o.n_appeals ? [String(o.n_appeals), o.n_open_appeals ? h('span', { class: 'muted t-caption1' }, ' ' + t('op.openN', { n: o.n_open_appeals })) : null] : h('span', { class: 'muted' }, '0')),
          h('td', { class: 'col-operator muted t-caption1' }, (o.languages || []).join(', ') + ' · ' + (o.channels || []).map(U.channelLabel).join(', ')),
          h('td', { class: 'num' }, isMe ? null : h('button', { class: 'btn btn--small btn--subtle op-me-btn', type: 'button', title: t('op.itsMeTitle'),
            onClick: function (e) { e.stopPropagation(); CQA.me.set(o.name); ui.toast(t('op.meSet', { name: o.name }), { tone: 'success' }); } }, t('op.itsMe'))));
        tr.addEventListener('click', function (e) { if (!e.target.closest('a,button')) location.hash = href; });
        return tr;
      }));
      CQA.dom.replace(this.box, h('div', { class: 'table-wrap' }, h('table', { class: 'table op-table', 'aria-label': t('op.listTitle') }, h('thead', null, thead), tbody)),
        h('p', { class: 'muted t-caption1 op-foot' }, t('op.listFoot')));
    }
  }

  // ================================================================== профиль
  class CqaPageOperator extends HTMLElement {
    connectedCallback() {
      if (this._built) return;
      this._built = true;
      this.name = this.params.name;
      document.title = this.name + ' · CQA';
      this.root = h('div', { class: 'page op-page' }, ui.skeleton('tiles', 4), ui.skeleton('card', 6));
      this.textContent = '';
      this.appendChild(this.root);
      var self = this;
      this.onMe = function () { if (self.p) self.renderHeader(); };
      document.addEventListener('cqa-me-changed', this.onMe);
      this.load();
    }

    disconnectedCallback() { document.removeEventListener('cqa-me-changed', this.onMe); }

    update(params) { return params.name === this.name; }

    load() {
      var self = this;
      CQA.dom.replace(this.root, ui.skeleton('tiles', 4), ui.skeleton('card', 6));
      Promise.all([
        CQA.api.operator(this.name),
        CQA.api.trainerSessions().catch(function () { return []; })
      ]).then(function (res) {
        self.p = res[0];
        self.sessions = (res[1] || []).filter(function (s) { return same(s.operator, self.p.name); });
        self.render();
      }, function (err) {
        CQA.dom.replace(self.root, h('a', { class: 'back-link', href: '#/operators' }, icon('arrowLeft', 'icon--16'), t('op.all')),
          ui.error(err, function () { self.load(); }, err.status === 404 ? t('op.notFound', { name: self.name }) : null));
      });
    }

    render() {
      var p = this.p;
      document.title = p.name + ' · CQA';
      this.header = h('div');
      this.summaryBox = h('div');
      CQA.dom.replace(this.root,
        h('a', { class: 'back-link', href: '#/operators' }, icon('arrowLeft', 'icon--16'), t('op.all')),
        this.header,
        h('div', { class: 'stack stack--xl' },
          h('div', { class: 'op-top' }, this.vsCard(), this.kpis()),
          this.summaryBox,
          this.zonesSection(),
          this.strengthsSection(),
          this.criteriaSection(),
          this.grossSection(),
          this.trainingSection(),
          this.dialogsSection()));
      this.renderHeader();
      this.renderSummary(null);
    }

    renderHeader() {
      var p = this.p, self = this, me = CQA.me.get(), isMe = same(me, p.name);
      var langs = {}, chans = {};
      p.dialogs.forEach(function (d) { langs[d.language] = 1; chans[U.channelLabel(d.channel)] = 1; });
      var worst = p.growth_zones[0] && p.growth_zones[0].examples[0];
      CQA.dom.replace(this.header, h('div', { class: 'page-header' },
        h('div', null,
          h('div', { class: 'row' }, h('h1', null, p.name), isMe ? ui.badge(t('op.you'), 'brand', 'person') : null),
          h('div', { class: 'page-header__sub' },
            [t('op.dialogsN', { n: p.n_dialogs }), Object.keys(langs).join(', '), Object.keys(chans).join(', ')].join(' · '),
            p.manual ? ' · ' + t('op.manualN', { n: p.manual }) : '')),
        h('div', { class: 'row' },
          isMe ? null : h('button', { class: 'btn', type: 'button', title: t('op.itsMeTitle'), onClick: function () {
            CQA.me.set(p.name); ui.toast(t('op.meSet', { name: p.name }), { tone: 'success' });
          } }, icon('person'), t('op.itsMe')),
          worst ? h('a', { class: 'btn', href: CQA.trainHref(worst.uid, { focus: [p.growth_zones[0].criterion_id], operator: p.name }) }, icon('target'), t('op.trainWorst')) : null,
          h('button', { class: 'btn btn--primary', type: 'button', onClick: function () { self.askSummary(this); } }, icon('sparkle'), t('op.aiSummary')))));
    }

    // ---- балл против отдела
    vsCard() {
      var p = this.p, dep = p.department;
      var d = p.avg_total != null && dep.avg_total != null ? p.avg_total - dep.avg_total : null;
      var bar = function (label, v, tone, cls) {
        return h('div', { class: 'op-vs__row ' + (cls || '') },
          h('span', { class: 'op-vs__label' }, label),
          ui.bar(v == null ? 0 : v / 100, { tone: tone }),
          h('span', { class: 'op-vs__val num' }, v == null ? '—' : fmt.score(v)));
      };
      var tone = U.scoreTone(p.avg_total);
      return h('section', { class: 'card op-vs', 'aria-labelledby': 'op-vs-title' },
        h('div', { class: 'card__header' },
          h('h2', { class: 'card__title', id: 'op-vs-title' }, t('op.vsTitle')),
          h('span', { class: 'op-vs__delta' }, CQA.delta(d, { big: true }), h('span', { class: 'muted t-caption1' }, ' ' + t('op.vsDeltaNote')))),
        h('div', { class: 'op-vs__big' }, CQA.score(p.avg_total, null, { size: 'xl', max: true })),
        bar(p.name, p.avg_total, tone === 'good' ? 'success' : tone === 'mid' ? 'mid' : 'danger', 'is-op'),
        bar(t('op.department', { n: dep.n_operators }), dep.avg_total, 'brand'),
        h('div', { class: 'muted t-caption1' }, t('op.vsNote', { n: p.n_dialogs, d: dep.n_dialogs })));
    }

    kpis() {
      var p = this.p, dep = p.department;
      var tile = function (label, value, note, tone, ic) {
        return h('div', { class: 'kpi' + (tone ? ' kpi--' + tone : '') }, h('span', { class: 'kpi__label' }, icon(ic || 'info'), label),
          h('span', { class: 'kpi__value' }, value), note ? h('span', { class: 'kpi__note' }, note) : null);
      };
      return h('div', { class: 'kpis op-kpis' },
        tile(t('op.kDialogs'), String(p.n_dialogs), t('op.kDialogsNote', { n: dep.n_dialogs }), null, 'chat'),
        tile(t('op.kGross'), String(p.gross_count), t('op.kGrossNote', { n: dep.gross_count }), p.gross_count ? 'danger' : null, 'shield'),
        tile(t('op.kAppeals'), String(p.n_appeals), p.n_open_appeals ? t('op.openN', { n: p.n_open_appeals }) : t('op.kAppealsNote'), p.n_open_appeals ? 'warning' : null, 'scale'),
        tile(t('op.kReview'), String(p.needs_review), t('op.kReviewNote'), p.needs_review ? 'warning' : null, 'eye'));
    }

    // ---- сводка от ИИ
    renderSummary(s, state) {
      var self = this, box = this.summaryBox;
      if (!s && !state) { CQA.dom.clear(box); return; }
      var card = h('section', { class: 'card ai-card op-ai', 'aria-labelledby': 'op-ai-title', 'aria-busy': state === 'loading' ? 'true' : 'false' },
        h('div', { class: 'card__header' }, h('h2', { class: 'card__title row', id: 'op-ai-title' }, icon('sparkle'), t('op.aiSummary')),
          s ? h('span', { class: 'muted t-caption1' }, [s.model, s.cache_hit ? t('op.aiCached') : fmt.money(s.cost_usd)].filter(Boolean).join(' · ')) : null));
      if (state === 'loading') {
        var el = h('span', { class: 'muted num' });
        card.appendChild(h('div', { class: 'row', role: 'status' }, ui.spinner(), h('span', null, t('op.aiLoading')), el));
        U.elapsed(el);
      } else if (state && state.error) {
        card.appendChild(ui.msgbar(CQA.api.mode === 'mock' && state.error.status === 422 ? 'warning' : 'danger', t('op.aiFailed'), U.errText(state.error),
          h('button', { class: 'btn btn--small', type: 'button', onClick: function () { self.askSummary(); } }, icon('refresh'), t('action.retry'))));
      } else if (s) {
        card.appendChild(h('div', { class: 'quote quote--brand op-ai__text' }, s.summary));
        card.appendChild(h('div', { class: 'muted t-caption1 row' }, CQA.recordedBadge(), t('op.aiNote', { n: s.n_dialogs }),
          CQA.api.mode === 'mock' ? ' ' + t('demo.summaryNote') : null));
      }
      CQA.dom.replace(box, card);
    }

    askSummary(btn) {
      var self = this;
      if (btn) btn.disabled = true;
      this.renderSummary(null, 'loading');
      CQA.dom.scrollIntoPane(this.summaryBox, { block: 'start' });
      CQA.api.operatorSummary(this.p.name).then(function (s) {
        self.renderSummary(s);
      }, function (err) {
        self.renderSummary(null, { error: err });
      }).finally(function () { if (btn) btn.disabled = false; });
    }

    // ---- зоны роста
    zonesSection() {
      var p = this.p, self = this;
      var body;
      if (!p.growth_zones.length) body = h('div', { class: 'radar__empty' }, icon('checkCircle'), h('span', null, t('op.noZones')));
      else body = h('div', { class: 'zones' }, p.growth_zones.map(function (z, i) { return self.zoneCard(z, i); }));
      return h('section', { 'aria-labelledby': 'op-zones' },
        h('div', { class: 'section-head' },
          h('h2', { class: 'section-title', id: 'op-zones' }, icon('trend'), t('op.zonesTitle', { n: p.growth_zones.length })),
          h('span', { class: 'muted t-caption1' }, t('op.zonesSub'), ' ', ui.hint(t('op.zonesHint')))),
        body);
    }

    zoneCard(z, i) {
      var p = this.p;
      var exs = z.examples.map(function (ex) {
        var q = ex.quote || ex.message_text;
        return h('div', { class: 'zone-ex' },
          h('div', { class: 'row zone-ex__head' },
            h('a', { class: 'mono t-body1-strong', href: CQA.convHref(ex.uid, { crit: z.criterion_id }) }, ex.external_id),
            CQA.verdict(ex.verdict, z.type, z.type === 'scale' ? (p.criteria.filter(function (c) { return c.criterion_id === z.criterion_id; })[0] || {}).top : null),
            ex.changed_by_appeal ? ui.badge(t('crit.changedByAppeal'), 'brand', 'scale') : null,
            h('span', { class: 'spacer' }),
            ex.total != null ? CQA.score(ex.total) : null),
          q && ex.mid ? h('a', { class: 'risk__quote zone-ex__quote', href: CQA.convHref(ex.uid, { crit: z.criterion_id, m: ex.mid }), title: t('risk.openQuote', { mid: ex.mid }) },
            h('span', { class: 'risk__mid' }, ex.mid), h('span', { class: 'risk__text' }, '«' + q + '»')) : null,
          ex.explanation ? h('p', { class: 't-caption1 subtle zone-ex__expl' }, ex.explanation) : null,
          ex.how_to_fix ? h('div', { class: 'zone-ex__fix' }, h('span', { class: 'crit__label' }, t('crit.howToFix')), h('div', { class: 'quote' }, ex.how_to_fix)) : null,
          h('div', { class: 'row' }, h('a', { class: 'btn btn--small', href: CQA.trainHref(ex.uid, { focus: [z.criterion_id], operator: p.name }) }, icon('target'), t('op.train'))));
      });
      return h('article', { class: 'card zone' },
        h('div', { class: 'zone__head' },
          h('span', { class: 'zone__rank', 'aria-label': t('op.zoneRank', { n: i + 1 }) }, String(i + 1)),
          h('h3', { class: 'zone__title' }, CQA.critLabel(z.criterion_id, z.title))),
        h('div', { class: 'zone__loss' },
          h('span', { class: 'zone__loss-val num' }, '−' + fmt.num(z.loss, 2)),
          h('span', { class: 'zone__loss-text' }, t('op.lossPer'), h('span', { class: 'muted' }, ' · ' + t('op.lossDept', { v: z.dept_loss == null ? '—' : fmt.num(z.dept_loss, 2) })))),
        h('div', { class: 'muted t-caption1' }, t('op.zoneStats', { v: z.violations, n: z.n_applicable }) + (z.n_na ? ' · ' + t('op.naN', { n: z.n_na }) : '')),
        exs.length ? h('div', { class: 'zone__exs' }, exs) : h('p', { class: 'muted t-caption1' }, t('op.noExamples')));
    }

    // ---- сильные стороны
    strengthsSection() {
      var p = this.p;
      if (!p.strengths.length) return null;
      return h('section', { 'aria-labelledby': 'op-strong' },
        h('div', { class: 'section-head' },
          h('h2', { class: 'section-title', id: 'op-strong' }, icon('checkCircle'), t('op.strongTitle')),
          h('span', { class: 'muted t-caption1' }, t('op.strongSub'))),
        h('div', { class: 'strengths' }, p.strengths.map(function (s) {
          var ex = s.example, q = ex && (ex.quote || ex.message_text);
          return h('article', { class: 'card strength' },
            h('h3', { class: 'zone__title' }, CQA.critLabel(s.criterion_id, s.title)),
            h('div', { class: 'row strength__nums' },
              h('b', { class: 'num' }, fmt.pct(s.quality)),
              h('span', { class: 'muted' }, t('op.deptPct', { v: fmt.pct(s.dept_quality) })),
              s.delta != null && Math.abs(s.delta) >= 0.005 ? h('span', { class: 'delta delta--up t-caption1' }, t('op.ppPlus', { v: fmt.num(pp(s.delta), 1) })) : null),
            ex && q && ex.mid ? h('a', { class: 'strength__quote', href: CQA.convHref(ex.uid, { crit: s.criterion_id, m: ex.mid }) },
              h('span', { class: 'risk__mid' }, ex.external_id + ' · ' + ex.mid), h('span', null, '«' + q + '»')) : null);
        })));
    }

    // ---- критерии против отдела
    criteriaSection() {
      var p = this.p;
      var tbody = h('tbody', null, p.criteria.map(function (c) {
        var st = c.operator, dt = c.department;
        var none = !st.n_applicable;
        return h('tr', { class: none ? 'is-na' : null },
          h('th', { scope: 'row', class: 'op-crit__name' }, CQA.critLabel(c.criterion_id, c.title)),
          h('td', { class: 'op-crit__bar' }, none ? h('span', { class: 'muted t-caption1' }, t('op.qNone'))
            : h('div', { class: 'op-crit__cell' },
              ui.bar(st.quality, { tone: qualityTone(st.quality), mark: dt.quality, markTitle: t('op.deptMark', { v: fmt.pct(dt.quality) }),
                label: t('op.barAria', { v: fmt.pct(st.quality), d: fmt.pct(dt.quality) }) }),
              h('b', { class: 'num nowrap op-q op-q--' + qualityTone(st.quality) }, fmt.pct(st.quality)))),
          h('td', { class: 't-caption1' }, h('div', null, CQA.qualityText(c, st)), st.n_na ? h('div', { class: 'muted' }, t('op.naN', { n: st.n_na })) : null),
          h('td', { class: 't-caption1 muted col-operator' }, dt.n_applicable ? fmt.pct(dt.quality) + ' · ' + CQA.qualityText(c, dt) : t('op.qNone')),
          h('td', { class: 'num' }, c.delta == null ? h('span', { class: 'muted' }, '—')
            : h('span', { class: 'delta delta--' + (Math.abs(c.delta) < 0.005 ? 'same' : c.delta > 0 ? 'up' : 'down') }, t('op.pp', { v: fmt.signed(pp(c.delta), 1) }))));
      }));
      return h('section', { class: 'card', 'aria-labelledby': 'op-crit' },
        h('div', { class: 'card__header' },
          h('div', null, h('h2', { class: 'card__title', id: 'op-crit' }, t('op.critTitle')),
            h('div', { class: 'muted t-caption1' }, t('op.critSub')))),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact op-crit' },
          h('thead', null, h('tr', null,
            h('th', { scope: 'col' }, t('op.cCrit')),
            h('th', { scope: 'col' }, t('op.cQuality'), ' ', ui.hint(t('op.qualityHint'))),
            h('th', { scope: 'col' }, t('op.cOperator')),
            h('th', { scope: 'col', class: 'col-operator' }, t('op.cDept')),
            h('th', { scope: 'col', class: 'num' }, 'Δ'))),
          tbody)));
    }

    grossSection() {
      var p = this.p;
      if (!p.gross_errors.length) return null;
      return h('section', { class: 'card radar-card', 'aria-labelledby': 'op-gross' },
        h('div', { class: 'card__header' }, h('h2', { class: 'card__title radar-card__title', id: 'op-gross' }, icon('shield'), t('op.grossTitle', { n: p.gross_errors.length }))),
        h('div', { class: 'radar__list' }, p.gross_errors.map(function (g) {
          var q = g.quote || g.message_text;
          return h('article', { class: 'risk' },
            h('div', { class: 'risk__head' },
              h('a', { class: 'risk__id', href: CQA.convHref(g.uid, { crit: g.criterion_id }) }, g.external_id),
              g.category ? ui.badge(g.category, 'danger') : null, h('span', { class: 'spacer' }), g.total != null ? CQA.score(g.total) : null),
            q && g.mid ? h('a', { class: 'risk__quote', href: CQA.convHref(g.uid, { crit: g.criterion_id, m: g.mid }) },
              h('span', { class: 'risk__mid' }, g.mid), h('span', { class: 'risk__text' }, '«' + q + '»')) : null,
            g.explanation ? h('p', { class: 'risk__expl t-caption1 subtle' }, g.explanation) : null);
        })));
    }

    // ---- тренировки «до / после»
    trainingSection() {
      var p = this.p, list = this.sessions || [];
      var done = list.filter(function (s) { return s.status === 'finished' && s.after_total != null; });
      var active = list.filter(function (s) { return s.status !== 'finished'; });
      var zone = p.growth_zones[0], ex = zone && zone.examples[0];
      var head = h('div', { class: 'card__header' },
        h('div', null, h('h2', { class: 'card__title row', id: 'op-train' }, icon('target'), t('op.trainTitle')),
          h('div', { class: 'muted t-caption1' }, t('op.trainSub'))),
        h('a', { class: 'btn btn--small', href: '#/trainer' }, t('op.trainAll'), icon('chevronRight')));
      var card = h('section', { class: 'card', 'aria-labelledby': 'op-train' }, head);
      if (!list.length) {
        card.appendChild(h('div', { class: 'row' }, h('span', { class: 'muted' }, t('op.trainNone')),
          ex ? h('a', { class: 'btn btn--small btn--primary', href: CQA.trainHref(ex.uid, { focus: [zone.criterion_id], operator: p.name }) }, icon('target'), t('op.trainStart')) : null));
        return card;
      }
      if (done.length) {
        var deltas = done.filter(function (s) { return s.before_total != null; }).map(function (s) { return s.after_total - s.before_total; });
        var mean = deltas.length ? deltas.reduce(function (a, x) { return a + x; }, 0) / deltas.length : null;
        card.appendChild(h('div', { class: 'row op-train__sum' },
          h('span', null, t('op.trainDone', { n: done.length })),
          mean != null ? [h('span', { class: 'muted' }, t('op.trainMean')), CQA.delta(mean, { big: true })] : null,
          active.length ? h('span', { class: 'muted' }, ' · ' + t('op.trainActive', { n: active.length })) : null));
      }
      card.appendChild(CQA.sessionRows(list));
      return card;
    }

    dialogsSection() {
      var p = this.p;
      var tbody = h('tbody', null, p.dialogs.map(function (d) {
        var href = CQA.convHref(d.uid);
        var tr = h('tr', { class: 'is-clickable' },
          h('td', null, h('a', { class: 'row-link mono', href: href }, d.external_id)),
          h('td', null, ui.badge(d.language, 'neutral')),
          h('td', null, h('span', { class: 'row row--nowrap' }, icon(U.channelIcon(d.channel), 'icon--16 muted'), U.channelLabel(d.channel))),
          h('td', { class: 'col-operator' }, d.contact_reason || h('span', { class: 'muted' }, '—')),
          h('td', { class: 'num' }, d.total != null ? CQA.score(d.total) : '—'),
          h('td', null, h('div', { class: 'row marks' }, marks(d))));
        tr.addEventListener('click', function (e) { if (!e.target.closest('a')) location.hash = href; });
        return tr;
      }));
      return h('section', { class: 'card', 'aria-labelledby': 'op-dialogs' },
        h('div', { class: 'card__header' }, h('h2', { class: 'card__title', id: 'op-dialogs' }, t('op.dialogsTitle', { n: p.dialogs.length })),
          h('span', { class: 'muted t-caption1' }, t('op.dialogsSort'))),
        h('div', { class: 'table-scroll' }, h('table', { class: 'table table--compact' },
          h('thead', null, h('tr', null,
            h('th', { scope: 'col' }, t('list.colId')), h('th', { scope: 'col' }, t('list.colLang')), h('th', { scope: 'col' }, t('list.colChannel')),
            h('th', { scope: 'col', class: 'col-operator' }, t('op.colReason')), h('th', { scope: 'col', class: 'num' }, t('list.colTotal')), h('th', { scope: 'col' }, t('list.colMarks')))),
          tbody)));
    }
  }

  /** Строки сессий тренажёра: тема, диалог, «до → после» гантелью, Δ, статус. */
  CQA.sessionRows = function (list, opts) {
    opts = opts || {};
    return h('ul', { class: 'sessions' }, list.map(function (s) {
      var finished = s.status === 'finished';
      var d = finished && s.before_total != null && s.after_total != null ? s.after_total - s.before_total : null;
      return h('li', null, h('a', { class: 'session-row', href: '#/trainer/' + s.id },
        h('span', { class: 'session-row__main' },
          h('span', { class: 'session-row__title' }, s.title || t('trainer.untitled')),
          h('span', { class: 'muted t-caption1' }, [fmt.externalId(s.base_uid), opts.showOperator && s.operator ? s.operator : null,
            t('trainer.turnsN', { n: s.turns || 0 }), fmt.date(s.created_at)].filter(Boolean).join(' · '))),
        finished ? h('span', { class: 'session-row__chart' }, CQA.dumbbell(s.before_total, s.after_total, { small: true })) : h('span', { class: 'session-row__chart' }),
        h('span', { class: 'session-row__nums num' },
          finished ? [h('span', { class: 'muted' }, fmt.score(s.before_total)), icon('arrowRight', 'icon--16 muted'), CQA.score(s.after_total)]
            : ui.badge(t('trainer.statusActive'), 'brand', 'chat')),
        h('span', { class: 'session-row__delta' }, finished ? CQA.delta(d) : null)));
    }));
  };

  customElements.define('cqa-page-operators', CqaPageOperators);
  customElements.define('cqa-page-operator', CqaPageOperator);
})(window.CQA = window.CQA || {});
