/* Клиент REST API (§8). apiBase: ?data= (web resource Dynamics: JSON или строка URL) → localStorage → тот же origin.
   Мок-режим: ?mock=1 или API недоступен → ответы из CQA.mock (frontend/mock/*.json). */
(function (CQA) {
  'use strict';

  function ApiError(status, message, body) {
    this.name = 'ApiError';
    this.status = status;
    this.message = message;
    this.body = body;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;
  CQA.ApiError = ApiError;

  var LS_BASE = 'cqa.apiBase';
  var LS_RUNS = 'cqa.recentRuns';
  var state = { mode: 'api', base: '', reason: null, data: {}, health: null };

  // ---------------------------------------------------------------- параметры запуска
  function readDataParam() {
    var sp = new URLSearchParams(location.search);
    var raw = sp.get('data');
    if (!raw) return {};
    var tries = [raw];
    if (/%[0-9a-f]{2}/i.test(raw)) { try { tries.push(decodeURIComponent(raw)); } catch (e) { /* ignore */ } }
    for (var i = 0; i < tries.length; i++) {
      var s = tries[i].trim();
      try {
        var o = JSON.parse(s);
        if (o && typeof o === 'object') return o;
        if (typeof o === 'string') return { apiBase: o };
      } catch (e) { /* не JSON */ }
      if (/^https?:\/\//i.test(s)) return { apiBase: s };
      if (s.indexOf('=') > 0) {
        var p = new URLSearchParams(s), out = {};
        p.forEach(function (v, k) { out[k] = v; });
        if (out.apiBase || out.api || out.mock) return out;
      }
    }
    return {};
  }

  function normBase(u) {
    if (!u) return '';
    u = String(u).trim().replace(/\/+$/, '');
    return u.replace(/\/api$/, '');
  }

  function truthy(v) { return v === true || v === 1 || v === '1' || v === 'true'; }

  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(new ApiError(0, CQA.t('error.timeout'))); }, ms);
      promise.then(function (v) { clearTimeout(timer); resolve(v); }, function (e) { clearTimeout(timer); reject(e); });
    });
  }

  /** Определить режим: api или mock. Вызывается один раз при старте. */
  function init() {
    var sp = new URLSearchParams(location.search);
    state.data = readDataParam();
    var forced = sp.get('mock') === '1' || truthy(state.data.mock);
    var base = state.data.apiBase || state.data.api || state.data.url || CQA.store.get(LS_BASE, '') ||
      (/^https?:$/.test(location.protocol) ? location.origin : '');
    state.base = normBase(base);
    if (forced) return goMock('forced');
    if (!state.base) return goMock('noApi');
    state.mode = 'api';
    return withTimeout(rawRequest('GET', '/health'), 3500).then(function (h) {
      if (!h || typeof h !== 'object') throw new Error('bad health');
      state.health = h;
      state.reason = null;
      return state;
    }).catch(function () { return goMock('unreachable'); });
  }

  function goMock(reason) {
    state.mode = 'mock';
    state.reason = reason;
    return CQA.mock.load().then(function () {
      return CQA.mock.handle('GET', '/health').then(function (h) { state.health = h; return state; });
    });
  }

  // ---------------------------------------------------------------- запросы
  function buildQuery(query) {
    if (!query) return '';
    var p = new URLSearchParams();
    Object.keys(query).forEach(function (k) {
      var v = query[k];
      if (v == null || v === '' || v === false) return;
      if (Array.isArray(v)) v.forEach(function (x) { p.append(k, x); });
      else p.set(k, v === true ? '1' : String(v));
    });
    var s = p.toString();
    return s ? '?' + s : '';
  }

  function url(path, query) { return state.base + '/api' + path + buildQuery(query); }

  function messageFrom(body, status) {
    if (body && typeof body === 'object') {
      if (typeof body.detail === 'string') return body.detail;
      if (typeof body.message === 'string') return body.message;
      if (Array.isArray(body.detail) || Array.isArray(body)) return CQA.t('error.validation');
    }
    if (typeof body === 'string' && body && body.length < 300 && body.indexOf('<') < 0) return body;
    if (status === 404) return CQA.t('error.notFound');
    if (status >= 500) return CQA.t('error.server', { s: status });
    return CQA.t('error.http', { s: status });
  }

  function rawRequest(method, path, opts) {
    opts = opts || {};
    var init = { method: method, headers: { Accept: 'application/json' } };
    if (opts.body instanceof FormData) init.body = opts.body;
    else if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    return fetch(url(path, opts.query), init).then(function (res) {
      if (opts.blob && res.ok) return res.blob();
      var ct = res.headers.get('content-type') || '';
      var p = ct.indexOf('json') >= 0 ? res.json().catch(function () { return null; }) : res.text().catch(function () { return ''; });
      return p.then(function (body) {
        if (!res.ok) throw new ApiError(res.status, messageFrom(body, res.status), body);
        if (ct.indexOf('json') < 0 && !opts.text) throw new ApiError(res.status, CQA.t('error.notJson'), body);
        return body;
      });
    }, function () {
      throw new ApiError(0, CQA.t('error.network'));
    });
  }

  function request(method, path, opts) {
    if (state.mode === 'mock') return CQA.mock.handle(method, path, opts || {});
    return rawRequest(method, path, opts);
  }

  /** Ошибки валидации из 422: [{loc: 'criteria.2.weight', msg}] — поддерживает ValidationErrorDTO[] и формат FastAPI. */
  function validationErrors(err) {
    if (!err || err.status !== 422) return [];
    var body = err.body;
    var list = Array.isArray(body) ? body : (body && Array.isArray(body.detail) ? body.detail : (body && Array.isArray(body.errors) ? body.errors : []));
    return list.map(function (e) {
      var loc = e.loc;
      if (Array.isArray(loc)) loc = loc.filter(function (x) { return x !== 'body'; }).join('.');
      loc = String(loc || '').replace(/^(body\.)?config\./, '').replace(/\[(\w+)\]/g, '.$1').replace(/\//g, '.');
      return { loc: loc, msg: e.msg || e.message || String(e) };
    });
  }

  function enc(s) { return encodeURIComponent(s); }

  function rememberRun(id) {
    var ids = CQA.store.get(LS_RUNS, []);
    ids = [id].concat(ids.filter(function (x) { return x !== id; })).slice(0, 10);
    CQA.store.set(LS_RUNS, ids);
  }

  var versionCache = {};

  var api = {
    init: init,
    request: request,
    url: url,
    validationErrors: validationErrors,
    get mode() { return state.mode; },
    get base() { return state.base; },
    get reason() { return state.reason; },
    get health() { return state.health; },
    get launchData() { return state.data; },
    setBase: function (u) { CQA.store.set(LS_BASE, u ? normBase(u) : null); },
    checkBase: function (u) {
      var b = normBase(u);
      return withTimeout(fetch(b + '/api/health').then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }), 4000);
    },

    health_: function () { return request('GET', '/health'); },
    datasets: function () { return request('GET', '/datasets'); },
    fetchDataset: function (groupId) { return request('POST', '/datasets/fetch', { body: { group_id: groupId } }); },
    uploadDataset: function (file) { var fd = new FormData(); fd.append('file', file); return request('POST', '/datasets/upload', { body: fd }); },

    /** filters: group, lang, channel, score_min, score_max, gross, review, appeal, q, sort */
    conversations: function (filters) { return request('GET', '/conversations', { query: filters }); },
    conversation: function (uid) { return request('GET', '/conversations/' + enc(uid)); },
    evaluation: function (uid) { return request('GET', '/conversations/' + enc(uid) + '/evaluation'); },
    evaluationById: function (id) { return request('GET', '/evaluations/' + id); },
    /** audio_url из ConversationDTO — внутренний путь бэкенда; в мок-режиме — blob тихой записи. */
    audioSrc: function (conv) {
      if (!conv || !conv.has_audio) return null;
      var u = conv.audio_url;
      if (state.mode === 'mock') return u;
      if (!u) return url('/conversations/' + enc(conv.uid) + '/audio');
      if (/^(https?:|blob:|data:)/.test(u)) return u;
      return state.base + (u.charAt(0) === '/' ? '' : '/') + u;
    },

    runs: function () {
      return request('GET', '/runs').catch(function (e) {
        if (e.status !== 404 && e.status !== 405) throw e;
        // GET /runs нет в P0 — берём последние известные прогоны по id
        var ids = CQA.store.get(LS_RUNS, []);
        return Promise.all(ids.map(function (id) { return api.run(id).catch(function () { return null; }); }))
          .then(function (rs) { return rs.filter(Boolean); });
      });
    },
    createRun: function (dto) { return request('POST', '/runs', { body: dto }).then(function (r) { rememberRun(r.id); return r; }); },
    run: function (id) { return request('GET', '/runs/' + id); },
    retryFailed: function (id) { return request('POST', '/runs/' + id + '/retry-failed'); },
    cancelRun: function (id) { return request('POST', '/runs/' + id + '/cancel'); },

    checklistVersions: function (cid) { return request('GET', '/checklists/' + enc(cid) + '/versions'); },
    checklistVersion: function (vid) {
      if (versionCache[vid]) return Promise.resolve(versionCache[vid]);
      return request('GET', '/checklist-versions/' + vid).then(function (v) { versionCache[vid] = v; return v; });
    },
    createChecklistVersion: function (cid, config, comment) {
      return request('POST', '/checklists/' + enc(cid) + '/versions', { body: { config: config, comment: comment || null } });
    },
    publishVersion: function (vid) {
      delete versionCache[vid];
      return request('POST', '/checklist-versions/' + vid + '/publish');
    },

    createAppeal: function (dto) { return request('POST', '/appeals', { body: dto }); },
    appeals: function (status) { return request('GET', '/appeals', { query: { status: status && status !== 'all' ? status : null } }); },
    appeal: function (id) { return request('GET', '/appeals/' + id); },
    decide: function (id, dto) { return request('POST', '/appeals/' + id + '/decision', { body: dto }); },

    secondOpinion: function (id) { return request('POST', '/appeals/' + id + '/second-opinion'); },

    // ---- P1: инсайты руководителя
    insightsSummary: function (group) { return request('GET', '/insights/summary', { query: { group: group } }); },
    risks: function (group) { return request('GET', '/insights/risks', { query: { group: group } }); },
    toCompliance: function (crid, note) { return request('POST', '/insights/risks/' + crid + '/compliance', { body: { note: note || null } }); },
    unanswered: function (group, topic) { return request('GET', '/insights/unanswered', { query: { group: group, topic: topic } }); },
    reasons: function (group) { return request('GET', '/insights/reasons', { query: { group: group } }); },
    runSignals: function (dto) { return request('POST', '/insights/signals', { body: dto || {} }); },

    // ---- P1: качество, автотесты
    quality: function (params) { return request('GET', '/quality', { query: params }); },
    qualityRuns: function () { return request('GET', '/quality/runs'); },
    autotests: function (profile) { return request('GET', '/quality/autotests', { query: { profile: profile } }); },

    // ---- P1: пробный прогон и «было/стало»
    dryRun: function (vid, body) { return request('POST', '/checklist-versions/' + vid + '/dry-run', { body: body || { sample: 10 } }); },
    runDiff: function (id) { return request('GET', '/runs/' + id + '/diff'); },

    // ---- P1: журнал оценки
    evaluations: function (uid) { return request('GET', '/conversations/' + enc(uid) + '/evaluations'); },
    evaluationCalls: function (id) { return request('GET', '/evaluations/' + id + '/calls'); },
    llmCall: function (id) { return request('GET', '/llm-calls/' + id); },

    // ---- P1: настройки ролей
    settings: function () { return request('GET', '/settings'); },
    saveSettings: function (roles) { return request('PUT', '/settings', { body: { roles: roles } }); },

    /** Ссылка на экспорт (для прямого открытия, если скачивание из iframe заблокировано). */
    exportUrl: function (params) { return url('/export', params); },
    exportBlob: function (params) {
      if (state.mode === 'mock') return CQA.mock.handle('GET', '/export', { query: params });
      return rawRequest('GET', '/export', { query: params, blob: true });
    }
  };

  CQA.api = api;
})(window.CQA = window.CQA || {});
