/* Общие блоки экранов P2/P3: безопасный markdown (ответы ассистента и тьютора), реплика чата, «до → после»,
   окно «Урок для всех», ссылки на тренажёр, выбор оператора. Всё строится через h() — без innerHTML. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt, ui = CQA.ui, U = CQA.util;

  // ------------------------------------------------------------------ ссылки
  CQA.convHref = function (uid, q) { return CQA.router.href('/c/' + encodeURIComponent(uid), q); };
  CQA.operatorHref = function (name) { return '#/operators/' + encodeURIComponent(name); };
  /** Начало тренировки: экран-подтверждение #/trainer/new (сессия создаётся только по кнопке). */
  CQA.trainHref = function (uid, opts) {
    opts = opts || {};
    return CQA.router.href('/trainer/new', {
      uid: uid, focus: opts.focus && opts.focus.length ? [].concat(opts.focus).join(',') : null,
      operator: opts.operator || null, lesson: opts.lesson || null
    });
  };
  CQA.isTrainerUid = function (uid) { return /^TRAINER:/.test(uid || ''); };
  /** Нарушение критерия: «Нет», ступень ниже верхней, флаг «Да». */
  CQA.isViolation = function (verdict, type, top) {
    var tone = U.verdictTone(verdict, type, top);
    return tone === 'danger' || tone === 'low' || tone === 'mid';
  };

  // ------------------------------------------------------------------ markdown (подмножество)
  var INLINE = /\*\*([^*\n]+)\*\*|\[([^\]\n]{1,160})\]\(([^)\s]{1,240})\)|\[(S\d{1,3}(?:\s*[,;]\s*S\d{1,3})*)\]|«([^»\n]{1,400})»/g;

  /** Строка → узлы: **жирный**, [текст](uid) → ссылка на диалог, [S1] → cite(ref), «цитата». */
  function inline(text, opts) {
    var out = [], last = 0, m;
    INLINE.lastIndex = 0;
    while ((m = INLINE.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      if (m[1] != null) out.push(h('b', null, m[1]));
      else if (m[2] != null) out.push(link(m[2], m[3]));
      else if (m[4] != null) {
        if (opts && opts.cite) m[4].split(/\s*[,;]\s*/).forEach(function (ref) { out.push(opts.cite(ref)); });
        else out.push('[' + m[4] + ']');
      }
      else if (m[5] != null) out.push(h('q', { class: 'md-quote' }, m[5]));
      last = INLINE.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  function link(label, target) {
    if (/^https?:/i.test(target)) return label;              // внешние ссылки не открываем
    if (/^#\//.test(target)) return h('a', { href: target }, label);
    var mm = /^([^?#]+)(?:[?#]m=(m\d+))?$/.exec(target);
    if (mm && mm[1].indexOf(':') > 0) return h('a', { class: 'md-conv', href: CQA.convHref(mm[1], mm[2] ? { m: mm[2] } : null) }, label);
    return label;
  }

  /** Markdown-ответ → фрагмент: абзацы, списки «- » и «1. », заголовки «#». */
  CQA.md = function (text, opts) {
    var root = h('div', { class: 'md' });
    var lines = String(text || '').replace(/\r/g, '').split('\n');
    var list = null, para = [];
    function flushPara() {
      if (!para.length) return;
      root.appendChild(h('p', null, inline(para.join(' '), opts)));
      para = [];
    }
    lines.forEach(function (raw) {
      var line = raw.trim();
      var li = /^(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (li) {
        flushPara();
        var ordered = /^\d/.test(line);
        if (!list || list.ordered !== ordered) { list = h(ordered ? 'ol' : 'ul'); list.ordered = ordered; root.appendChild(list); }
        list.appendChild(h('li', null, inline(li[1], opts)));
        return;
      }
      if (!line) { flushPara(); list = null; return; }
      var hd = /^#{1,4}\s+(.*)$/.exec(line);
      if (hd) { flushPara(); list = null; root.appendChild(h('h4', null, inline(hd[1], opts))); return; }
      list = null;
      para.push(line);
    });
    flushPara();
    return root;
  };

  // ------------------------------------------------------------------ реплика чата (как в транскрипте)
  var ROLE_ICON = { client: 'person', operator: 'headset', bot: 'bot', system: 'info', unknown: 'info' };
  /** m = {mid, speaker, text, ts, source}; opts = {label, muted} */
  CQA.chatMsg = function (m, opts) {
    opts = opts || {};
    var role = m.speaker || 'unknown';
    if (role === 'system') {
      return h('div', { class: 'msg msg--system' + (opts.muted ? ' msg--base' : ''), 'data-mid': m.mid },
        h('div', { class: 'msg__system' }, h('span', { class: 'msg__text' }, m.text), m.ts ? h('span', { class: 'msg__time' }, m.ts) : null));
    }
    return h('div', { class: 'msg msg--' + role + (opts.muted ? ' msg--base' : '') + (opts.fresh ? ' is-fresh' : ''), 'data-mid': m.mid },
      h('div', { class: 'msg__meta' },
        h('span', { class: 'msg__role' }, icon(ROLE_ICON[role] || 'info'), opts.label || t('role.' + role)),
        m.ts ? h('span', { class: 'msg__time' }, m.ts) : null),
      h('div', { class: 'msg__bubble' }, h('span', { class: 'msg__text' }, m.text)));
  };

  /** Пузырь «клиент печатает…». */
  CQA.typingMsg = function (label) {
    return h('div', { class: 'msg msg--client msg--typing', role: 'status', 'aria-live': 'polite' },
      h('div', { class: 'msg__meta' }, h('span', { class: 'msg__role' }, icon('person'), label || t('trainer.typing'))),
      h('div', { class: 'msg__bubble' }, h('span', { class: 'typing-dots', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'))));
  };

  // ------------------------------------------------------------------ «до → после» на шкале 0–100
  /** Гантель: серая точка «до», цветная «после», линия между. */
  CQA.dumbbell = function (before, after, opts) {
    opts = opts || {};
    var pos = function (v) { return Math.max(0, Math.min(100, Number(v))) + '%'; };
    var wrap = h('span', { class: 'dumbbell' + (opts.small ? ' dumbbell--small' : ''), role: 'img',
      'aria-label': t('trainer.beforeAfterAria', { b: fmt.score(before), a: fmt.score(after) }) });
    if (before != null && after != null) {
      var lo = Math.min(before, after), hi = Math.max(before, after);
      wrap.appendChild(h('span', { class: 'dumbbell__line' + (after > before ? ' is-up' : after < before ? ' is-down' : ''), style: { left: pos(lo), width: (hi - lo) + '%' } }));
    }
    if (before != null) wrap.appendChild(h('span', { class: 'dumbbell__dot dumbbell__dot--before', style: { left: pos(before) }, title: t('trainer.before') + ': ' + fmt.score(before) }));
    if (after != null) wrap.appendChild(h('span', { class: 'dumbbell__dot dumbbell__dot--after dumbbell__dot--' + U.scoreTone(after), style: { left: pos(after) }, title: t('trainer.after') + ': ' + fmt.score(after) }));
    return wrap;
  };

  /** «+12,5» зелёным / «−3» красным. */
  CQA.delta = function (d, opts) {
    opts = opts || {};
    if (d == null || isNaN(d)) return h('span', { class: 'muted' }, '—');
    var tone = Math.abs(d) < 0.005 ? 'same' : d > 0 ? 'up' : 'down';
    return h('span', { class: 'delta delta--' + tone + (opts.big ? ' delta--big' : '') }, tone === 'same' ? '±0' : fmt.signed(d));
  };

  // ------------------------------------------------------------------ выбор оператора
  /** <select> операторов: «не выбран» + список из /operators. Значение — имя. */
  CQA.operatorSelect = function (opts) {
    opts = opts || {};
    var sel = h('select', { class: 'select' + (opts.small ? ' select--small' : ''), id: opts.id || null, 'aria-label': opts.label || t('op.pick') },
      h('option', { value: '' }, opts.empty || t('op.notChosen')));
    var cur = opts.value || '';
    if (cur) sel.appendChild(h('option', { value: cur, selected: true }, cur));
    CQA.api.operators().then(function (d) {
      var names = (d.operators || []).map(function (o) { return o.name; }).sort(function (a, b) { return a.localeCompare(b, 'ru'); });
      CQA.dom.clear(sel);
      sel.appendChild(h('option', { value: '' }, opts.empty || t('op.notChosen')));
      if (cur && names.indexOf(cur) < 0) names.unshift(cur);
      names.forEach(function (n) { sel.appendChild(h('option', { value: n, selected: n === cur }, n)); });
      sel.value = cur;
    }).catch(function () { /* без списка — только текущее значение */ });
    return sel;
  };

  // ------------------------------------------------------------------ «Урок для всех»
  /** opts = {title, items: [{uid, label, note}], preselect: bool} → POST /trainer/lessons → переход в тренажёр. */
  CQA.openLessonDialog = function (opts) {
    var titleInput = h('input', { class: 'input', id: 'lesson-title', maxlength: '200', value: opts.title || '' });
    var titleErr = h('div', { class: 'field__error', 'aria-live': 'polite' });
    var checks = [];
    var list = h('div', { class: 'lesson-pick' }, (opts.items || []).map(function (it) {
      var cb = h('input', { type: 'checkbox', checked: opts.preselect !== false, value: it.uid });
      checks.push(cb);
      return h('label', { class: 'check lesson-pick__item' }, cb,
        h('span', { class: 'lesson-pick__main' }, h('span', { class: 'mono' }, it.label || fmt.externalId(it.uid)),
          it.note ? h('span', { class: 'muted t-caption1' }, it.note) : null));
    }));
    var status = h('div', { 'aria-live': 'polite' });
    ui.modal({
      title: t('lesson.createTitle'),
      body: h('div', { class: 'stack stack--l' },
        h('p', { class: 'subtle' }, t('lesson.createIntro')),
        h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'lesson-title' }, t('lesson.name')), titleInput, titleErr),
        h('fieldset', { class: 'field lesson-fieldset' }, h('legend', { class: 'field__label' }, t('lesson.dialogs', { n: checks.length })), list),
        status),
      footer: function (close) {
        var go = h('button', { class: 'btn btn--primary', type: 'button' }, icon('target'), t('lesson.create'));
        go.addEventListener('click', function () {
          var uids = checks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
          var title = titleInput.value.trim();
          titleErr.textContent = '';
          if (!title) { titleInput.setAttribute('aria-invalid', 'true'); titleErr.textContent = t('lesson.nameRequired'); titleInput.focus(); return; }
          titleInput.removeAttribute('aria-invalid');
          if (!uids.length) { CQA.dom.replace(status, ui.msgbar('warning', t('lesson.pickOne'))); return; }
          go.disabled = true;
          CQA.dom.replace(go, ui.spinner(), t('lesson.creating'));
          CQA.api.createLesson(uids, title).then(function (l) {
            close();
            ui.toast(t('lesson.created', { title: l.title }), { tone: 'success', action: { label: t('lesson.open'), href: '#/trainer?lesson=' + l.id } });
            if (opts.onCreated) opts.onCreated(l);
          }, function (err) {
            go.disabled = false;
            CQA.dom.replace(go, icon('target'), t('lesson.create'));
            CQA.dom.replace(status, ui.msgbar('danger', t('lesson.failed'), U.errText(err)));
          });
        });
        return [h('button', { class: 'btn', type: 'button', onClick: function () { close(); } }, t('action.cancel')), go];
      }
    });
  };

  /** Ступенчатая подсказка «как это работает»: [{icon, title, text}] */
  CQA.steps = function (items, current) {
    return h('ol', { class: 'steps' }, items.map(function (s, i) {
      var state = current == null ? '' : i < current ? ' is-done' : i === current ? ' is-current' : '';
      return h('li', { class: 'steps__item' + state },
        h('span', { class: 'steps__num', 'aria-hidden': 'true' }, current != null && i < current ? icon('check', 'icon--16') : String(i + 1)),
        h('span', { class: 'steps__body' }, h('span', { class: 'steps__title' }, s.title), s.text ? h('span', { class: 'steps__text' }, s.text) : null));
    }));
  };

  /** В демо-режиме ответы моделей — записанные реальные ответы: честная пометка рядом с ответом. */
  CQA.recordedBadge = function () {
    return CQA.api.mode === 'mock' ? ui.badge(t('demo.recorded'), 'warning', 'database', { title: t('demo.recordedTitle') }) : null;
  };

  /** Метка критерия «04 Консультация». */
  CQA.critLabel = function (id, title) {
    return h('span', { class: 'crit-label' }, h('span', { class: 'crit-label__id mono' }, id), title ? h('span', { class: 'crit-label__title' }, title) : null);
  };
})(window.CQA = window.CQA || {});
