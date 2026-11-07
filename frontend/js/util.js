/* Утилиты: DOM-хелпер h(), иконки, форматирование, состояния, тосты, диалоги. Классический скрипт → window.CQA. */
(function (CQA) {
  'use strict';

  var t = function (k, p) { return CQA.t(k, p); };

  // ---------------------------------------------------------------- DOM
  var PROPS = { value: 1, checked: 1, indeterminate: 1, selected: 1, htmlFor: 1, open: 1 };

  /** h('div', {class, onClick, ...}, ...children) — создаёт элемент. Текст всегда вставляется как текст (без innerHTML). */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
        var v = attrs[k];
        if (v == null || v === false) continue;
        if (k === 'class') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k === 'dataset') Object.assign(el.dataset, v);
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
        else if (PROPS[k]) el[k] = v;
        else el.setAttribute(k, v === true ? '' : String(v));
      }
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }

  function append(el, c) {
    if (c == null || c === false || c === true) return el;
    if (Array.isArray(c)) { c.forEach(function (x) { append(el, x); }); return el; }
    if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
    return el;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function replace(el) { clear(el); for (var i = 1; i < arguments.length; i++) append(el, arguments[i]); return el; }

  // ---------------------------------------------------------------- иконки (20×20, контур)
  var ICONS = {
    home: '<path d="M3.5 8.5 10 3.5l6.5 5V16a1 1 0 0 1-1 1H12v-4.5H8V17H4.5a1 1 0 0 1-1-1z"/>',
    chat: '<path d="M4 4.5h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H9.5L6 16.5v-3H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"/>',
    phone: '<path d="M7 3.5 8.6 7 7 8.5a9 9 0 0 0 4.5 4.5L13 11.4l3.5 1.6-.5 2.4a1.5 1.5 0 0 1-1.6 1.1A12.5 12.5 0 0 1 2.9 5.6 1.5 1.5 0 0 1 4 4z"/>',
    scale: '<path d="M10 3.5V17M6 17h8M4 6.5h12M10 3.5v3M4 6.5 2 11a2 2 0 0 0 4 0zM16 6.5 14 11a2 2 0 0 0 4 0z"/>',
    checklist: '<path d="M9 5.5h8M9 10h8M9 14.5h8M3 5.5l1.2 1.2L6.5 4.4M3 10l1.2 1.2L6.5 8.9M3 14.5l1.2 1.2 2.3-2.3"/>',
    play: '<path d="M6.5 4.3v11.4l9-5.7z" fill="currentColor"/>',
    pause: '<path d="M6 4.5h2.6v11H6zM11.4 4.5H14v11h-2.6z" fill="currentColor"/>',
    back5: '<path d="M4.5 10a5.5 5.5 0 1 0 1.7-4M4.5 3.5v3h3"/>',
    search: '<circle cx="8.5" cy="8.5" r="5"/><path d="m12.3 12.3 4.2 4.2"/>',
    download: '<path d="M10 3v9.5M6 9l4 4 4-4M4 16.5h12"/>',
    upload: '<path d="M10 13V3.5M6 7.5l4-4 4 4M4 16.5h12"/>',
    refresh: '<path d="M16 10a6 6 0 1 1-1.8-4.3M16 3.5v3h-3"/>',
    add: '<path d="M10 4v12M4 10h12"/>',
    delete: '<path d="M4 6h12M8 6V4.5h4V6M5.5 6l.8 10a1 1 0 0 0 1 .9h5.4a1 1 0 0 0 1-.9l.8-10M8.5 9v5M11.5 9v5"/>',
    close: '<path d="m5 5 10 10M15 5 5 15"/>',
    chevronDown: '<path d="m5.5 7.5 4.5 4.5 4.5-4.5"/>',
    chevronUp: '<path d="m5.5 12.5 4.5-4.5 4.5 4.5"/>',
    chevronRight: '<path d="m7.5 5.5 4.5 4.5-4.5 4.5"/>',
    arrowLeft: '<path d="M16 10H4M8.5 5.5 4 10l4.5 4.5"/>',
    arrowRight: '<path d="M4 10h12M11.5 5.5 16 10l-4.5 4.5"/>',
    arrowSort: '<path d="M7 4v12M4 7l3-3 3 3M13 16V4M10 13l3 3 3-3"/>',
    arrowUp: '<path d="M10 16V4M5.5 8.5 10 4l4.5 4.5"/>',
    arrowDown: '<path d="M10 4v12M5.5 11.5 10 16l4.5-4.5"/>',
    warning: '<path d="M10 3.2 17.8 16.5H2.2z"/><path d="M10 8.3v3.9M10 14.3v.2"/>',
    error: '<circle cx="10" cy="10" r="7"/><path d="M10 6.3v4.4M10 13.4v.2"/>',
    check: '<path d="m4.5 10.5 3.5 3.5 7.5-8"/>',
    checkCircle: '<circle cx="10" cy="10" r="7"/><path d="m7 10.2 2.1 2.1L13.2 8"/>',
    info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v4.8M10 6.4v.2"/>',
    external: '<path d="M11.5 3.5h5v5M16.5 3.5l-7 7M14 11.5v4a1 1 0 0 1-1 1H4.5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4"/>',
    edit: '<path d="M13.3 3.7a1.6 1.6 0 0 1 2.3 2.3L7 14.6l-3.2.8.8-3.2z"/>',
    history: '<circle cx="10" cy="10" r="7"/><path d="M10 6v4l2.6 1.8"/>',
    compare: '<path d="M7 3.5v13M13 3.5v13M3.5 7.5H7M13 12.5h3.5M3.5 12.5H7M13 7.5h3.5"/>',
    filter: '<path d="M3 4.5h14l-5.5 6.5V16l-3-1.5V11z"/>',
    flash: '<path d="M11 2.5 4.5 11H10l-1 6.5 6.5-8.5H10z"/>',
    plug: '<path d="M7 3v4M13 3v4M5 7h10v2.5a5 5 0 0 1-10 0zM10 14.5V17"/>',
    person: '<circle cx="10" cy="7" r="3"/><path d="M4 17a6 6 0 0 1 12 0"/>',
    headset: '<path d="M4 12v-2a6 6 0 0 1 12 0v2M4 12h2.5v4H4zM13.5 12H16v4h-2.5z"/>',
    bot: '<rect x="4" y="6.5" width="12" height="9" rx="2"/><path d="M10 3.5v3M7.5 10.5v1M12.5 10.5v1"/>',
    globe: '<circle cx="10" cy="10" r="7"/><path d="M3 10h14M10 3c2 2 2.8 4.3 2.8 7S12 15 10 17M10 3C8 5 7.2 7.3 7.2 10S8 15 10 17"/>',
    copy: '<rect x="7" y="7" width="9.5" height="9.5" rx="1.2"/><path d="M4 13V4.8A1.3 1.3 0 0 1 5.3 3.5H13"/>',
    stop: '<rect x="5.5" y="5.5" width="9" height="9" rx="1.5"/>',
    sparkle: '<path d="M10 3l1.6 4.4L16 9l-4.4 1.6L10 15l-1.6-4.4L4 9l4.4-1.6z"/>',
    flag: '<path d="M5 17V3.5M5 4h9.5l-2 3.5 2 3.5H5"/>',
    eye: '<path d="M2.5 10s2.7-5 7.5-5 7.5 5 7.5 5-2.7 5-7.5 5-7.5-5-7.5-5z"/><circle cx="10" cy="10" r="2.3"/>',
    hold: '<rect x="5.5" y="4.5" width="3" height="11" rx="1"/><rect x="11.5" y="4.5" width="3" height="11" rx="1"/>',
    cursor: '<path d="m5 3.5 10 6-4.5 1.2L13 16l-2 1-2.5-5.3L5 14.5z"/>',
    database: '<ellipse cx="10" cy="5" rx="6" ry="2.2"/><path d="M4 5v10c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2V5M4 10c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2"/>',
    more: '<circle cx="5" cy="10" r=".9" fill="currentColor"/><circle cx="10" cy="10" r=".9" fill="currentColor"/><circle cx="15" cy="10" r=".9" fill="currentColor"/>',
    shield: '<path d="M10 2.8 16 5v4.6c0 3.7-2.5 6.4-6 7.6-3.5-1.2-6-3.9-6-7.6V5z"/><path d="M10 6.8v3.8M10 13v.2"/>',
    chart: '<path d="M3.5 3.5v13h13"/><path d="M7 13V9.5M10.5 13V6.5M14 13v-5"/>',
    settings: '<path d="M8.6 2.8h2.8l.4 2.1 1.5.9 2-.8 1.4 2.4-1.6 1.4v1.7l1.6 1.4-1.4 2.4-2-.8-1.5.9-.4 2.1H8.6l-.4-2.1-1.5-.9-2 .8-1.4-2.4 1.6-1.4V9.3L3.3 7.9l1.4-2.4 2 .8 1.5-.9z"/><circle cx="10" cy="10" r="2.3"/>',
    send: '<path d="M3.5 10 16.5 3.5 13 16.5l-3.2-5.3z"/><path d="m9.8 11.2 6.7-7.7"/>',
    question: '<circle cx="10" cy="10" r="7"/><path d="M8 8a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.3v.4M10 14v.2"/>',
    cloud: '<path d="M6 15.5h8.2a3.3 3.3 0 0 0 .4-6.6A4.8 4.8 0 0 0 5.4 8 3.8 3.8 0 0 0 6 15.5z"/>',
    server: '<rect x="3.5" y="3.5" width="13" height="5" rx="1"/><rect x="3.5" y="11.5" width="13" height="5" rx="1"/><path d="M6.5 6h.1M6.5 14h.1"/>',
    key: '<circle cx="7" cy="12.5" r="3.2"/><path d="m9.3 10.2 6.7-6.7M13.5 6l2 2"/>',
    code: '<path d="m7 6-4 4 4 4M13 6l4 4-4 4"/>',
    link: '<path d="M8.5 11.5a3 3 0 0 0 4.2 0l2.6-2.6a3 3 0 0 0-4.2-4.2l-.9.9M11.5 8.5a3 3 0 0 0-4.2 0l-2.6 2.6a3 3 0 0 0 4.2 4.2l.9-.9"/>',
    grid: '<rect x="3.5" y="3.5" width="5" height="5" rx="1"/><rect x="11.5" y="3.5" width="5" height="5" rx="1"/><rect x="3.5" y="11.5" width="5" height="5" rx="1"/><rect x="11.5" y="11.5" width="5" height="5" rx="1"/>',
    book: '<path d="M10 5.5C8.5 4.3 6.3 3.8 3.5 4v11.5c2.8-.2 5 .3 6.5 1.5 1.5-1.2 3.7-1.7 6.5-1.5V4c-2.8-.2-5 .3-6.5 1.5zM10 5.5V17"/>',
    target: '<circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="4"/><circle cx="10" cy="10" r="1" fill="currentColor"/>',
    menu: '<path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13"/>',
    people: '<circle cx="7.5" cy="7" r="2.7"/><path d="M2.5 16.5a5 5 0 0 1 10 0"/><path d="M12.8 4.6a2.7 2.7 0 0 1 0 5.1M14.5 12a5 5 0 0 1 3 4.5"/>',
    trend: '<path d="M3.5 14.5 8 10l3 3 5.5-6.5"/><path d="M12.5 6.5h4v4"/>',
    bulb: '<path d="M7.5 14.5h5M8 17h4M10 3a5 5 0 0 0-3 9c.6.5 1 1.2 1 2h4c0-.8.4-1.5 1-2a5 5 0 0 0-3-9z"/>',
    compass: '<circle cx="10" cy="10" r="7"/><path d="m12.8 7.2-1.6 4-4 1.6 1.6-4z"/>',
    inbox: '<path d="M3.5 11.5 5.5 4.5h9l2 7V15a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"/><path d="M3.5 11.5h4l1 2h3l1-2h4"/>',
    panel: '<rect x="3.5" y="3.5" width="13" height="13" rx="2"/><path d="M12 3.5v13"/>',
    lightning: '<path d="M11 2.5 4.5 11H10l-1 6.5 6.5-8.5H10z"/>',
    moon: '<path d="M15.5 12.5A6.5 6.5 0 0 1 7.5 4.5a6.5 6.5 0 1 0 8 8z"/>',
    sun: '<circle cx="10" cy="10" r="3"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/>',
    cap: '<path d="M2.5 8 10 4.5 17.5 8 10 11.5z"/><path d="M5.5 9.5v3.5c1.2 1.3 2.7 2 4.5 2s3.3-.7 4.5-2V9.5M17.5 8v4"/>',
    undo: '<path d="M7 5.5 3.5 9 7 12.5"/><path d="M3.5 9H12a4.5 4.5 0 0 1 0 9H9"/>'
  };

  function icon(name, cls) {
    var span = document.createElement('span');
    span.className = 'icon' + (cls ? ' ' + cls : '');
    span.setAttribute('aria-hidden', 'true');
    span.innerHTML = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" focusable="false">' + (ICONS[name] || '') + '</svg>';
    return span;
  }

  // ---------------------------------------------------------------- форматирование
  function locale() { return (CQA.i18n && CQA.i18n.locale()) || 'ru-RU'; }
  var nfCache = {};
  function nf(min, max) {
    var key = locale() + min + max;
    return nfCache[key] || (nfCache[key] = new Intl.NumberFormat(locale(), { minimumFractionDigits: min, maximumFractionDigits: max }));
  }

  var fmt = {
    num: function (v, max) { return v == null || isNaN(v) ? '—' : nf(0, max == null ? 2 : max).format(v); },
    score: function (v) { return v == null || isNaN(v) ? '—' : nf(0, 2).format(v); },
    money: function (v) {
      if (v == null || isNaN(v)) return '—';
      if (v === 0) return '$0';
      return '$' + nf(v < 0.1 ? 4 : 2, v < 0.1 ? 4 : 2).format(v);
    },
    pct: function (v) { return v == null ? '—' : nf(0, 0).format(v * 100) + '%'; },
    /** Доля с одним знаком: 0.9384 → «93,8%». */
    pct1: function (v) { return v == null || isNaN(v) ? '—' : nf(0, 1).format(v * 100) + '%'; },
    /** Со знаком: +1,25 / −2 / 0. */
    signed: function (v, max) {
      if (v == null || isNaN(v)) return '—';
      var s = nf(0, max == null ? 2 : max).format(Math.abs(v));
      return v > 0 ? '+' + s : v < 0 ? '−' + s : s;
    },
    /** Время ISO → «17:26:19». */
    time: function (iso) {
      if (!iso) return '—';
      var d = new Date(iso);
      if (isNaN(d)) return iso;
      return new Intl.DateTimeFormat(locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(d);
    },
    /** Тысячи с пробелом: 1549967 → «1 549 967». */
    int: function (v) { return v == null || isNaN(v) ? '—' : nf(0, 0).format(v); },
    date: function (iso) {
      if (!iso) return '—';
      var d = new Date(iso);
      if (isNaN(d)) return iso;
      return new Intl.DateTimeFormat(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(d);
    },
    clock: function (sec) {
      if (sec == null || !isFinite(sec)) return '—:—';
      sec = Math.max(0, Math.floor(sec));
      var hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
      var p = function (n) { return (n < 10 ? '0' : '') + n; };
      return hh ? hh + ':' + p(mm) + ':' + p(ss) : mm + ':' + p(ss);
    },
    duration: function (sec) {
      if (sec == null || !isFinite(sec)) return '—';
      sec = Math.round(sec);
      if (sec < 60) return t('unit.sec', { n: sec });
      var m = Math.floor(sec / 60), s = sec % 60;
      if (m < 60) return t('unit.minSec', { m: m, s: s });
      return t('unit.hourMin', { h: Math.floor(m / 60), m: m % 60 });
    },
    ms: function (ms) { return ms == null ? '—' : t('unit.sec', { n: nf(0, 1).format(ms / 1000) }); },
    /** Время реплики для показа: чат «01:15» как есть, звонок ISO → «13:49:47». */
    ts: function (raw) {
      if (!raw) return '';
      var m = /T(\d{2}:\d{2}:\d{2})/.exec(raw);
      return m ? m[1] : raw;
    },
    externalId: function (uid) { var i = (uid || '').indexOf(':'); return i >= 0 ? uid.slice(i + 1) : uid; }
  };

  // ---------------------------------------------------------------- тона вердиктов и баллов
  /** Тон вердикта: success | danger | neutral | mid | low. Для флага «Да» — нарушение (danger). */
  function verdictTone(verdict, type, top) {
    if (verdict == null || verdict === '') return 'neutral';
    if (verdict === 'N/A') return 'neutral';
    if (type === 'flag') return verdict === 'Да' ? 'danger' : 'success';
    if (verdict === 'Да') return 'success';
    if (verdict === 'Нет') return 'danger';
    var n = Number(verdict);
    if (!isNaN(n)) {
      var max = Number(top) || n || 1;
      var share = n / max;
      if (share >= 1) return 'success';
      if (share >= 0.5) return 'mid';
      if (share > 0) return 'low';
      return 'danger';
    }
    return 'neutral';
  }

  function verdictLabel(verdict, type, top) {
    if (verdict == null || verdict === '') return t('verdict.none');
    if (verdict === 'Да') return t('verdict.yes');
    if (verdict === 'Нет') return t('verdict.no');
    if (verdict === 'N/A') return t('verdict.na');
    if (type === 'scale' && top) return t('verdict.scale', { v: verdict, top: top });
    return verdict;
  }

  function scoreTone(total) {
    if (total == null) return 'none';
    if (total >= 85) return 'good';
    if (total >= 60) return 'mid';
    return 'bad';
  }

  function channelLabel(ch) { return ch === 'voice call' ? t('channel.call') : ch === 'agent chat' ? t('channel.chat') : ch; }
  function channelIcon(ch) { return ch === 'voice call' ? 'phone' : 'chat'; }

  // ---------------------------------------------------------------- UI-блоки
  var ui = {};

  ui.badge = function (text, tone, iconName, extra) {
    return h('span', Object.assign({ class: ['badge', tone && 'badge--' + tone] }, extra || {}), iconName ? icon(iconName) : null, text);
  };

  /** Горизонтальная полоска доли 0..1: ui.bar(0.62, {tone: 'brand'|'success'|'danger'|'warning'|'mid', mark: 0.8, label}) */
  ui.bar = function (share, opts) {
    opts = opts || {};
    var v = share == null || isNaN(share) ? 0 : Math.max(0, Math.min(1, share));
    return h('span', { class: 'hbar' + (opts.tone ? ' hbar--' + opts.tone : ''), role: 'img', 'aria-label': opts.label || null },
      h('span', { class: 'hbar__fill', style: { width: (v * 100).toFixed(1) + '%' } }),
      opts.mark != null ? h('span', { class: 'hbar__mark', title: opts.markTitle || null, style: { left: (Math.max(0, Math.min(1, opts.mark)) * 100).toFixed(1) + '%' } }) : null);
  };

  /** Подсказка-пояснение «?»: текст всплывает по наведению и по фокусу (клавиатура), читается экранными читалками. */
  ui.hint = function (text) {
    return h('span', { class: 'hint', tabindex: '0', role: 'note', 'data-tip': text, 'aria-label': text }, icon('question', 'icon--16'));
  };

  /** Термин с пояснением: пунктирное подчёркивание, подсказка по наведению и фокусу. key — 'term.<имя>' из i18n. */
  ui.term = function (label, key) {
    var text = CQA.i18n.tx(key, key);
    return h('span', { class: 'term', tabindex: '0', 'data-tip': text, 'aria-label': label + '. ' + text }, label);
  };

  ui.spinner = function (label) { return h('span', { class: 'spinner', role: 'status', 'aria-label': label || t('state.loading') }); };

  /** Скелетон: kind = text | table | tiles | card | transcript | criteria */
  ui.skeleton = function (kind, n) {
    var wrap = h('div', { class: 'skeleton-group', 'aria-busy': 'true', 'aria-label': t('state.loading'), role: 'status' });
    var i;
    n = n || 4;
    if (kind === 'table') {
      for (i = 0; i < n; i++) wrap.appendChild(h('span', { class: 'skeleton skeleton--row' }));
    } else if (kind === 'tiles') {
      var g = h('div', { class: 'kpis' });
      for (i = 0; i < n; i++) g.appendChild(h('span', { class: 'skeleton skeleton--tile' }));
      wrap.appendChild(g);
    } else if (kind === 'transcript') {
      for (i = 0; i < n; i++) wrap.appendChild(h('span', { class: 'skeleton skeleton--bubble' + (i % 2 ? ' is-right' : '') }));
    } else if (kind === 'criteria') {
      wrap.appendChild(h('span', { class: 'skeleton skeleton--block', style: { height: '140px', marginBottom: '16px' } }));
      for (i = 0; i < n; i++) wrap.appendChild(h('span', { class: 'skeleton skeleton--block', style: { marginBottom: '12px' } }));
    } else if (kind === 'card') {
      wrap.appendChild(h('span', { class: 'skeleton skeleton--title' }));
      for (i = 0; i < n; i++) wrap.appendChild(h('span', { class: 'skeleton skeleton--text', style: { width: (60 + (i * 13) % 40) + '%' } }));
    } else {
      for (i = 0; i < n; i++) wrap.appendChild(h('span', { class: 'skeleton skeleton--text', style: { width: (55 + (i * 17) % 45) + '%' } }));
    }
    return wrap;
  };

  ui.error = function (err, onRetry, title) {
    var detail = err && (err.message || String(err));
    var notFound = err && err.status === 404;
    var client = err && err.status >= 400 && err.status < 500;
    return h('div', { class: 'state state--error', role: 'alert' },
      h('div', { class: 'state__icon' }, icon(notFound ? 'search' : 'error')),
      h('div', { class: 'state__title' }, title || (notFound ? t('state.notFoundTitle') : t('state.errorTitle'))),
      notFound ? h('div', { class: 'state__text' }, t('state.notFoundText')) : null,
      detail && !notFound ? h('div', { class: client ? 'state__text' : 'state__detail' }, detail) : null,
      onRetry && !notFound ? h('button', { class: 'btn', type: 'button', onClick: onRetry }, icon('refresh'), t('action.retry')) : null,
      notFound ? h('button', { class: 'btn', type: 'button', onClick: function () { CQA.palette.open(); } }, icon('search'), t('state.findCtrlK')) : null);
  };

  ui.empty = function (title, text, action, iconName) {
    return h('div', { class: 'state' },
      h('div', { class: 'state__icon' }, icon(iconName || 'info')),
      h('div', { class: 'state__title' }, title),
      text ? h('div', { class: 'state__text' }, text) : null,
      action || null);
  };

  ui.msgbar = function (tone, title, body, actions) {
    var ic = { info: 'info', warning: 'warning', danger: 'error', success: 'checkCircle' }[tone] || 'info';
    return h('div', { class: 'msgbar msgbar--' + tone, role: tone === 'danger' ? 'alert' : null },
      icon(ic),
      h('div', { class: 'msgbar__body' }, title ? h('div', { class: 'msgbar__title' }, title) : null, body || null),
      actions || null);
  };

  /** Загрузка с состояниями: скелетон → результат | ошибка с «повторить». */
  ui.load = function (container, skeleton, loader, render) {
    function run() {
      replace(container, skeleton);
      return Promise.resolve().then(loader).then(function (data) {
        clear(container);
        render(data);
      }, function (err) {
        console.warn('[CQA]', err);
        replace(container, ui.error(err, run));
      });
    }
    return run();
  };

  // ---------------------------------------------------------------- тосты
  var toastsEl = null;
  ui.toast = function (message, opts) {
    opts = opts || {};
    if (!toastsEl || !toastsEl.isConnected) {
      toastsEl = h('div', { class: 'toasts', 'aria-live': 'polite', 'aria-atomic': 'false' });
      document.body.appendChild(toastsEl);
    }
    var tone = opts.tone || 'info';
    var ic = { success: 'checkCircle', error: 'error', warning: 'warning', info: 'info' }[tone];
    var el = h('div', { class: 'toast toast--' + tone, role: tone === 'error' ? 'alert' : 'status' },
      icon(ic),
      h('div', { class: 'toast__body' },
        h('div', null, message),
        opts.detail ? h('div', { class: 'muted t-caption1' }, opts.detail) : null,
        opts.action ? h('div', { class: 'toast__action' }, opts.action.href
          ? h('a', { href: opts.action.href, target: opts.action.target || null, rel: opts.action.target ? 'noopener' : null }, opts.action.label)
          : h('button', { class: 'btn btn--link', type: 'button', onClick: function () { opts.action.onClick(); dismiss(); } }, opts.action.label)) : null),
      h('button', { class: 'btn btn--subtle btn--icon btn--small', type: 'button', 'aria-label': t('action.close'), onClick: function () { dismiss(); } }, icon('close')));
    function dismiss() { if (el.parentNode) el.parentNode.removeChild(el); }
    toastsEl.appendChild(el);
    var ms = opts.timeout == null ? (tone === 'error' ? 9000 : 5000) : opts.timeout;
    if (ms) setTimeout(dismiss, ms);
    return dismiss;
  };

  // ---------------------------------------------------------------- модальные диалоги (<dialog>)
  /** ui.modal({title, body, footer(close), wide, onClose}) → {el, close} */
  ui.modal = function (opts) {
    var dlg = h('dialog', { class: 'dialog' + (opts.wide ? ' dialog--wide' : ''), 'aria-labelledby': 'dlg-title-' + (++ui._dlgSeq) });
    var close = function (result) {
      if (dlg.open) dlg.close();
      if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
      if (opts.onClose) opts.onClose(result);
    };
    var footer = opts.footer ? opts.footer(close) : null;
    dlg.appendChild(h('div', { class: 'dialog__surface' },
      h('div', { class: 'dialog__header' },
        h('h2', { class: 'dialog__title', id: 'dlg-title-' + ui._dlgSeq }, opts.title),
        h('button', { class: 'btn btn--subtle btn--icon', type: 'button', 'aria-label': t('action.close'), onClick: function () { close(); } }, icon('close'))),
      h('div', { class: 'dialog__body' }, opts.body),
      footer ? h('div', { class: 'dialog__footer' }, footer) : null));
    dlg.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    // щелчок мимо окна закрывает его, но не в первые полсекунды: двойной клик по кнопке не должен сразу закрыть подтверждение
    var openedAt = Date.now();
    dlg.addEventListener('click', function (e) { if (e.target === dlg && Date.now() - openedAt > 500) close(); });
    document.body.appendChild(dlg);
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    var first = dlg.querySelector('.dialog__body input, .dialog__body select, .dialog__body textarea');
    if (first && !opts.noAutofocus) first.focus();
    return { el: dlg, close: close };
  };
  ui._dlgSeq = 0;

  /** Подтверждение без window.confirm (в iframe Dynamics он может быть заблокирован). */
  ui.confirm = function (title, text, okLabel, danger, cancelLabel) {
    return new Promise(function (resolve) {
      var done = false;
      var m = ui.modal({
        title: title,
        body: h('p', null, text),
        onClose: function () { if (!done) resolve(false); },
        footer: function (close) {
          return [
            h('button', { class: 'btn', type: 'button', onClick: function () { close(); } }, cancelLabel || t('action.cancel')),
            h('button', { class: 'btn btn--primary' + (danger ? ' btn--danger-primary' : ''), type: 'button', onClick: function () { done = true; resolve(true); close(); } }, okLabel || t('action.ok'))
          ];
        }
      });
      var ok = m.el.querySelector('.btn--primary');
      if (ok) ok.focus();
    });
  };

  /** Выезжающая панель справа: ui.drawer({title, sub, body, wide, onClose}) → {el, close, body}.
      Служебное (детали оценки, загрузка и прогоны) — здесь, а не на основном экране. */
  ui.drawer = function (opts) {
    var id = 'drw-' + (++ui._dlgSeq);
    var dlg = h('dialog', { class: 'drawer' + (opts.wide ? ' drawer--wide' : ''), 'aria-labelledby': id });
    var closed = false;
    var close = function (result) {
      if (closed) return;
      closed = true;
      if (dlg.open) dlg.close();
      if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
      if (opts.onClose) opts.onClose(result);
    };
    var body = h('div', { class: 'drawer__body' }, opts.body);
    dlg.appendChild(h('div', { class: 'drawer__surface' },
      h('div', { class: 'drawer__header' },
        h('div', { class: 'drawer__titles' },
          h('h2', { class: 'drawer__title', id: id, tabindex: '-1' }, opts.title),
          opts.sub ? h('div', { class: 'drawer__sub' }, opts.sub) : null),
        h('button', { class: 'btn btn--subtle btn--icon', type: 'button', 'aria-label': t('action.close'), onClick: function () { close(); } }, icon('close'))),
      body));
    dlg.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    var openedAt = Date.now();
    dlg.addEventListener('click', function (e) { if (e.target === dlg && Date.now() - openedAt > 500) close(); });
    dlg.classList.add('dialog');
    document.body.appendChild(dlg);
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    var head = dlg.querySelector('.drawer__title');
    if (head) head.focus({ preventScroll: true });
    return { el: dlg, close: close, body: body };
  };

  /** «Что дальше»: подсказка после действия — заголовок, пояснение и 1–2 кнопки.
      ui.nextStep({title, text, icon, actions: [{label, href, onClick, primary, icon}], onClose}) */
  ui.nextStep = function (opts) {
    var el = h('div', { class: 'next', role: 'status' });
    var close = function () { if (el.parentNode) el.parentNode.removeChild(el); if (opts.onClose) opts.onClose(); };
    var actions = (opts.actions || []).filter(Boolean).map(function (a) {
      var cls = 'btn btn--small' + (a.primary ? ' btn--primary' : '');
      if (a.href) return h('a', { class: cls, href: a.href }, a.icon ? icon(a.icon) : null, a.label);
      return h('button', { class: cls, type: 'button', onClick: function (e) { a.onClick(e, close); } }, a.icon ? icon(a.icon) : null, a.label);
    });
    el.appendChild(h('span', { class: 'next__icon', 'aria-hidden': 'true' }, icon(opts.icon || 'arrowRight')));
    el.appendChild(h('div', { class: 'next__body' },
      h('div', { class: 'next__title' }, opts.title),
      opts.text ? h('div', { class: 'next__text' }, opts.text) : null,
      actions.length ? h('div', { class: 'next__actions' }, actions) : null));
    if (opts.closable !== false) el.appendChild(h('button', { class: 'btn btn--subtle btn--icon btn--small next__close', type: 'button', 'aria-label': t('action.close'), onClick: close }, icon('close')));
    return el;
  };

  /** Шапка экрана: заголовок, одна строка «что здесь», одна главная кнопка (и при необходимости тихие). */
  ui.pageHead = function (opts) {
    return h('div', { class: 'page-head' },
      h('div', { class: 'page-head__main' },
        opts.back ? h('a', { class: 'back-link', href: opts.back.href }, icon('arrowLeft', 'icon--16'), opts.back.label) : null,
        h('h1', null, opts.title),
        opts.what ? h('p', { class: 'page-head__what' }, opts.what) : null),
      opts.actions ? h('div', { class: 'page-head__actions' }, opts.actions) : null);
  };

  // ---------------------------------------------------------------- всплывающие подсказки [data-tip]
  (function tips() {
    if (typeof document === 'undefined' || !document.addEventListener) return;   // node-тесты мока
    var tip = null, owner = null;
    function show(el) {
      var text = el.getAttribute('data-tip');
      if (!text) return;
      if (!tip) { tip = h('div', { class: 'tip', role: 'tooltip', 'aria-hidden': 'true' }); document.body.appendChild(tip); }
      if (!tip.isConnected) document.body.appendChild(tip);
      owner = el;
      tip.textContent = text;
      tip.classList.remove('is-on');
      var r = el.getBoundingClientRect();
      tip.style.left = '0px'; tip.style.top = '0px';
      var tr = tip.getBoundingClientRect();
      var left = Math.max(8, Math.min(window.innerWidth - tr.width - 8, r.left + r.width / 2 - tr.width / 2));
      var top = r.bottom + 8;
      if (top + tr.height > window.innerHeight - 8) top = Math.max(8, r.top - tr.height - 8);
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
      requestAnimationFrame(function () { if (owner === el && tip) tip.classList.add('is-on'); });
    }
    function hide(el) { if (tip && (!el || el === owner)) { tip.classList.remove('is-on'); owner = null; } }
    document.addEventListener('mouseover', function (e) { var el = e.target.closest && e.target.closest('[data-tip]'); if (el && el !== owner) show(el); else if (!el && owner) hide(); });
    document.addEventListener('focusin', function (e) { var el = e.target.closest && e.target.closest('[data-tip]'); if (el) show(el); });
    document.addEventListener('focusout', function (e) { var el = e.target.closest && e.target.closest('[data-tip]'); if (el) hide(el); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
    document.addEventListener('scroll', function () { hide(); }, true);
  })();

  // ---------------------------------------------------------------- прочее
  function debounce(fn, ms) {
    var timer = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  /** Прокрутить элемент в ближайшем прокручиваемом контейнере (без прокрутки родительского окна Dynamics). */
  function scrollIntoPane(el, opts) {
    opts = opts || {};
    var pane = el.parentElement;
    while (pane && pane !== document.body) {
      var oy = getComputedStyle(pane).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && pane.scrollHeight > pane.clientHeight) break;
      pane = pane.parentElement;
    }
    var smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches && opts.smooth !== false;
    if (!pane || pane === document.body) {
      el.scrollIntoView({ block: opts.block || 'center', behavior: smooth ? 'smooth' : 'auto' });
      return;
    }
    var pr = pane.getBoundingClientRect(), er = el.getBoundingClientRect();
    var offset = er.top - pr.top + pane.scrollTop;
    var target = opts.block === 'start' ? offset - (opts.margin || 12) : offset - (pane.clientHeight - er.height) / 2;
    pane.scrollTo({ top: Math.max(0, target), behavior: smooth ? 'smooth' : 'auto' });
  }

  function storage(kind) {
    try { var s = window[kind]; var k = '__cqa'; s.setItem(k, '1'); s.removeItem(k); return s; } catch (e) { return null; }
  }
  var ls = storage('localStorage'), ss = storage('sessionStorage');
  var store = {
    get: function (k, def, session) { var s = session ? ss : ls; if (!s) return def; try { var v = s.getItem(k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } },
    set: function (k, v, session) { var s = session ? ss : ls; if (!s) return; try { if (v == null) s.removeItem(k); else s.setItem(k, JSON.stringify(v)); } catch (e) { /* квота */ } }
  };

  function deepClone(o) { return o == null ? o : JSON.parse(JSON.stringify(o)); }
  function sameJson(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  /** Скачать Blob. В iframe Dynamics может блокироваться — поэтому вызывающий код всегда показывает и ссылку. */
  function downloadBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = h('a', { href: url, download: filename, style: { display: 'none' } });
    document.body.appendChild(a);
    try { a.click(); } catch (e) { /* заблокировано */ }
    setTimeout(function () { a.remove(); }, 0);
    return url;
  }

  function uidSeq(prefix) { uidSeq.n = (uidSeq.n || 0) + 1; return (prefix || 'id') + '-' + uidSeq.n; }

  // ---------------------------------------------------------------- P2/P3: общие помощники
  /** Текст ошибки для человека: у 422 реального API текст лежит в detail[].msg, а не в message. */
  function errText(err) {
    if (!err) return '';
    var ve = CQA.api && CQA.api.validationErrors ? CQA.api.validationErrors(err) : [];
    if (ve.length) return ve.map(function (x) { return x.msg; }).join('; ');
    return err.message || String(err);
  }

  /** «Я» — оператор, выбранный в разделе «Мой профиль» (для тренажёра и тьютора). */
  var LS_ME = 'cqa.operator';
  var me = {
    get: function () { return store.get(LS_ME, null); },
    set: function (name) {
      store.set(LS_ME, name || null);
      document.dispatchEvent(new CustomEvent('cqa-me-changed', { detail: { name: name || null } }));
    }
  };

  /** Таймер ожидания «12 с»: обновляет el раз в секунду, возвращает stop(). */
  function elapsed(el, t0) {
    t0 = t0 || Date.now();
    var tick = function () { el.textContent = fmt.duration((Date.now() - t0) / 1000); };
    tick();
    var id = setInterval(function () { if (!el.isConnected) { clearInterval(id); return; } tick(); }, 1000);
    return function () { clearInterval(id); return (Date.now() - t0) / 1000; };
  }

  CQA.h = h;
  CQA.dom = { h: h, append: append, clear: clear, replace: replace, scrollIntoPane: scrollIntoPane, uid: uidSeq };
  CQA.icon = icon;
  CQA.fmt = fmt;
  CQA.ui = ui;
  CQA.store = store;
  CQA.util = {
    debounce: debounce, deepClone: deepClone, sameJson: sameJson, downloadBlob: downloadBlob,
    verdictTone: verdictTone, verdictLabel: verdictLabel, scoreTone: scoreTone,
    channelLabel: channelLabel, channelIcon: channelIcon,
    errText: errText, elapsed: elapsed
  };
  CQA.me = me;
})(window.CQA = window.CQA || {});
