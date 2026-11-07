/* Мок-бэкенд: ответы §8 из frontend/mock/*.json (в сборке — <script type="application/json" id="mock-…">).
   Запись (апелляции, решения, версии чек-листа, прогоны) живёт в памяти вкладки. */
(function (CQA) {
  'use strict';

  var FILES = ['health', 'datasets', 'conversations', 'conversation-details', 'evaluations', 'appeals', 'checklist-versions', 'runs'];
  var db = null;
  var loading = null;
  var silentUrl = null;

  function clone(o) { return o == null ? o : JSON.parse(JSON.stringify(o)); }
  function now() { return new Date().toISOString().replace(/\.\d+Z$/, 'Z'); }

  function loadFile(name) {
    var el = document.getElementById('mock-' + name);
    if (el) return Promise.resolve(JSON.parse(el.textContent));
    return fetch('mock/' + name + '.json', { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error('mock/' + name + '.json: HTTP ' + r.status);
      return r.json();
    });
  }

  function load() {
    if (loading) return loading;
    loading = Promise.all(FILES.map(loadFile)).then(function (parts) {
      var d = {};
      FILES.forEach(function (f, i) { d[f] = parts[i]; });
      db = {
        health: d.health,
        datasets: d.datasets,
        summaries: d.conversations,
        conversations: d['conversation-details'],
        evaluations: d.evaluations,
        appeals: d.appeals,
        versions: d['checklist-versions'],
        runs: d.runs.map(function (r) { return Object.assign({}, r, { _static: true }); }),
        seq: { appeal: 100, version: 100, run: 100 }
      };
      return db;
    });
    return loading;
  }

  function err(status, message, body) {
    var e = new CQA.ApiError(status, message, body);
    return e;
  }

  function delay(v) {
    return new Promise(function (res) { setTimeout(function () { res(v); }, 90 + Math.random() * 160); });
  }

  // ---------------------------------------------------------------- помощники
  function evalById(id) {
    for (var uid in db.evaluations) if (db.evaluations[uid].id === Number(id)) return db.evaluations[uid];
    return null;
  }
  function versionById(id) { return db.versions.filter(function (v) { return v.id === Number(id); })[0] || null; }
  function activeVersion() { return db.versions.filter(function (v) { return v.is_active; })[0] || db.versions[db.versions.length - 1]; }
  function summary(uid) { return db.summaries.filter(function (s) { return s.uid === uid; })[0]; }

  /** Тихая WAV-запись нужной длины: в моке нет настоящего mp3 (подписанные ссылки организаторов не копируем). */
  function silentWav(seconds) {
    if (silentUrl) return silentUrl;
    var rate = 8000, n = Math.ceil(seconds * rate);
    var buf = new ArrayBuffer(44 + n), v = new DataView(buf);
    var str = function (o, s) { for (var i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); v.setUint32(4, 36 + n, true); str(8, 'WAVE'); str(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
    str(36, 'data'); v.setUint32(40, n, true);
    new Uint8Array(buf, 44).fill(128);
    silentUrl = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
    return silentUrl;
  }

  function fragmentFor(conv, cr, counter) {
    var mids = (cr.evidence || []).map(function (e) { return e.id; }).concat(cr.checked_range || [], counter || []);
    var idx = mids.map(function (m) { return parseInt(String(m).replace(/\D/g, ''), 10); }).filter(function (n) { return !isNaN(n); });
    if (!idx.length) idx = [1];
    var lo = Math.max(1, Math.min.apply(null, idx) - 3), hi = Math.max.apply(null, idx) + 3;
    return conv.messages.filter(function (m) { return m.idx >= lo && m.idx <= hi; });
  }

  /** Пересчёт финальной оценки после решения по апелляции (та же формула §3). */
  function recomputeFinal(ev) {
    var ver = versionById(ev.checklist_version_id) || activeVersion();
    var cfg = ver.config;
    var verdicts = {}, cats = {};
    ev.criteria.forEach(function (c) { verdicts[c.criterion_id] = c.final_verdict; cats[c.criterion_id] = c.category; });
    var res = CQA.scoring.score(cfg, verdicts, cats);
    ev.final_total = res.total;
    ev.sections = res.sections;
    ev.criteria.forEach(function (c) {
      var s = res.criteria.filter(function (x) { return x.criterion_id === c.criterion_id; })[0];
      if (s) c.score = s;
    });
    var sum = summary(ev.uid);
    if (sum) sum.final_total = res.total;
    return res.total;
  }

  // ---------------------------------------------------------------- прогоны (симуляция)
  function runView(r) {
    if (!r._static && (r.status === 'queued' || r.status === 'running')) {
      var elapsed = (Date.now() - r._t0) / 1000;
      var processed = Math.min(r.units_total, Math.floor(Math.max(0, elapsed - 0.8) * r._rate) + r._base);
      var failed = r._failAt != null && processed > r._failAt ? 1 : 0;
      r.units_failed = failed;
      r.units_cached = Math.min(processed, r._cached);
      r.units_done = processed - failed;
      r.cost_usd = Math.round((r.units_done - r.units_cached) * 0.00112 * 1e4) / 1e4;
      r.status = processed === 0 ? 'queued' : 'running';
      if (!r.started_at && processed > 0) r.started_at = now();
      var left = r.units_total - processed;
      r.eta_sec = left > 0 ? Math.ceil(left / r._rate) : 0;
      if (left <= 0) { r.status = 'done'; r.finished_at = now(); r.eta_sec = null; }
    }
    var out = clone(r);
    Object.keys(out).forEach(function (k) { if (k.charAt(0) === '_') delete out[k]; });
    return out;
  }

  function createRun(body) {
    var uids;
    if (body.uids && body.uids.length) uids = body.uids;
    else uids = db.summaries.filter(function (s) { return !body.group || s.group_id === body.group; }).map(function (s) { return s.uid; });
    if (!uids.length) throw err(400, CQA.t('mock.noConversations'));
    var ver = body.checklist_version_id ? versionById(body.checklist_version_id) : activeVersion();
    if (!ver) throw err(404, CQA.t('mock.versionNotFound'));
    var nCrit = body.criteria && body.criteria.length ? body.criteria.length : ver.config.criteria.filter(function (c) { return c.enabled !== false; }).length;
    var total = uids.length * nCrit;
    var r = {
      id: ++db.seq.run, kind: body.kind || 'batch', status: 'queued', checklist_version_id: ver.id,
      judge_profile: body.profile || 'gemini-flash', model: 'gemini-3.8-flash',
      units_total: total, units_done: 0, units_failed: 0, units_cached: 0, cost_usd: 0, eta_sec: null,
      created_at: now(), started_at: null, finished_at: null,
      metrics: body.criteria ? { criteria: body.criteria } : null,
      _t0: Date.now(), _rate: Math.max(2, Math.min(8, total / 6)), _base: 0,
      _cached: body.force ? 0 : Math.floor(total / 5), _failAt: total >= 8 ? Math.floor(total * 0.6) : null
    };
    db.runs.push(r);
    return runView(r);
  }

  // ---------------------------------------------------------------- маршруты
  var routes = [];
  function route(method, re, fn) { routes.push({ method: method, re: re, fn: fn }); }

  route('GET', /^\/health$/, function () { return db.health; });
  route('GET', /^\/datasets$/, function () { return db.datasets; });
  route('POST', /^\/datasets\/fetch$/, function (m, q, body) {
    var g = (body && body.group_id || '').trim();
    var ds = db.datasets.filter(function (d) { return d.group_id === g; })[0];
    if (!ds) throw err(404, CQA.t('mock.groupNotFound', { g: g }));
    ds.fetched_at = now();
    return { group_id: g, total_records: ds.total_records, loaded: ds.total_records, new: 0, updated: ds.total_records };
  });
  route('POST', /^\/datasets\/upload$/, function (m, q, body) {
    var file = body && body.get ? body.get('file') : null;
    if (!file) throw err(400, CQA.t('mock.noFile'));
    return file.text().then(function (txt) {
      var data = JSON.parse(txt);
      var recs = (data.data && data.data.records) || data.records || [];
      var g = (data.data && data.data.dataset_group_id) || 'UPLOAD';
      return { group_id: g, total_records: recs.length, loaded: 0, note: CQA.t('mock.uploadNote') };
    });
  });

  route('GET', /^\/conversations$/, function (m, q) {
    var list = db.summaries.filter(function (s) {
      var score = s.final_total != null ? s.final_total : s.total;
      if (q.group && s.group_id !== q.group) return false;
      if (q.lang && s.language !== q.lang) return false;
      if (q.channel && s.channel !== q.channel) return false;
      if (q.score_min !== undefined && q.score_min !== '' && (score == null || score < Number(q.score_min))) return false;
      if (q.score_max !== undefined && q.score_max !== '' && (score == null || score > Number(q.score_max))) return false;
      if (q.gross === '1' && !s.gross) return false;
      if (q.review === '1' && !s.needs_review) return false;
      if (q.appeal === '1' && !s.open_appeals && !hasAppeal(s.uid)) return false;
      if (q.q && s.external_id.toLowerCase().indexOf(String(q.q).toLowerCase()) < 0 && (s.operator_name || '').toLowerCase().indexOf(String(q.q).toLowerCase()) < 0) return false;
      return true;
    });
    return list;
  });
  function hasAppeal(uid) { return db.appeals.some(function (a) { return a.uid === uid; }); }

  route('GET', /^\/conversations\/([^/]+)$/, function (m) {
    var c = db.conversations[decodeURIComponent(m[1])];
    if (!c) throw err(404, CQA.t('mock.notFound'));
    var out = clone(c);
    if (out.has_audio) {
      var last = 0;
      out.messages.forEach(function (x) { if (x.audio_sec != null) last = Math.max(last, x.audio_sec); });
      out.audio_url = silentWav(last + 8);
    }
    return out;
  });
  route('GET', /^\/conversations\/([^/]+)\/evaluation$/, function (m) {
    var e = db.evaluations[decodeURIComponent(m[1])];
    if (!e) throw err(404, CQA.t('mock.notEvaluated'));
    return e;
  });
  route('GET', /^\/evaluations\/(\d+)$/, function (m) {
    var e = evalById(m[1]);
    if (!e) throw err(404, CQA.t('mock.notFound'));
    return e;
  });

  route('GET', /^\/runs$/, function () { return db.runs.slice().reverse().map(runView); });
  route('POST', /^\/runs$/, function (m, q, body) { return createRun(body || {}); });
  route('GET', /^\/runs\/(\d+)$/, function (m) {
    var r = db.runs.filter(function (x) { return x.id === Number(m[1]); })[0];
    if (!r) throw err(404, CQA.t('mock.notFound'));
    return runView(r);
  });
  route('POST', /^\/runs\/(\d+)\/retry-failed$/, function (m) {
    var r = db.runs.filter(function (x) { return x.id === Number(m[1]); })[0];
    if (!r) throw err(404, CQA.t('mock.notFound'));
    if (r.units_failed) {
      r.units_failed = 0; r.units_done += 1; r._failAt = null;
      if (r._static) { r.status = 'done'; }
    }
    return runView(r);
  });
  route('POST', /^\/runs\/(\d+)\/cancel$/, function (m) {
    var r = db.runs.filter(function (x) { return x.id === Number(m[1]); })[0];
    if (!r) throw err(404, CQA.t('mock.notFound'));
    runView(r);
    if (r.status === 'queued' || r.status === 'running') { r.status = 'cancelled'; r.finished_at = now(); r.eta_sec = null; r._static = true; }
    return runView(r);
  });

  route('GET', /^\/checklists\/([^/]+)\/versions$/, function (m) {
    return db.versions.filter(function (v) { return v.checklist_id === decodeURIComponent(m[1]); }).slice().reverse();
  });
  route('GET', /^\/checklist-versions\/(\d+)$/, function (m) {
    var v = versionById(m[1]);
    if (!v) throw err(404, CQA.t('mock.notFound'));
    return v;
  });
  route('POST', /^\/checklists\/([^/]+)\/versions$/, function (m, q, body) {
    var cid = decodeURIComponent(m[1]);
    var cfg = body && body.config;
    if (!cfg) throw err(422, 'config', [{ loc: 'config', msg: CQA.t('val.required') }]);
    var errors = CQA.scoring.validateConfig(cfg);
    if (errors.length) throw err(422, CQA.t('mock.validationFailed'), errors);
    var same = db.versions.filter(function (v) { return v.checklist_id === cid; });
    var v = {
      id: ++db.seq.version, checklist_id: cid, version: same.reduce(function (a, x) { return Math.max(a, x.version); }, 0) + 1,
      status: 'draft', config: clone(cfg), comment: body.comment || null, created_at: now(), is_active: false
    };
    db.versions.push(v);
    return v;
  });
  route('POST', /^\/checklist-versions\/(\d+)\/publish$/, function (m) {
    var v = versionById(m[1]);
    if (!v) throw err(404, CQA.t('mock.notFound'));
    db.versions.forEach(function (x) { if (x.checklist_id === v.checklist_id) x.is_active = false; });
    v.is_active = true; v.status = 'published';
    db.health.active_checklist_version_id = v.id;
    return v;
  });

  route('GET', /^\/appeals$/, function (m, q) {
    return db.appeals.filter(function (a) { return !q.status || q.status === 'all' || a.status === q.status; })
      .slice().sort(function (a, b) { return b.id - a.id; });
  });
  route('GET', /^\/appeals\/(\d+)$/, function (m) {
    var a = db.appeals.filter(function (x) { return x.id === Number(m[1]); })[0];
    if (!a) throw err(404, CQA.t('mock.notFound'));
    return a;
  });
  route('POST', /^\/appeals$/, function (m, q, body) {
    var ev = evalById(body.evaluation_id);
    if (!ev) throw err(404, CQA.t('mock.notFound'));
    var cr = ev.criteria.filter(function (c) { return c.criterion_id === body.criterion_id; })[0];
    if (!cr) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'criterion_id', msg: CQA.t('mock.notFound') }]);
    if (!body.comment || !String(body.comment).trim()) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'comment', msg: CQA.t('val.required') }]);
    if (cr.appeal_id && cr.appeal_status === 'open') throw err(409, CQA.t('mock.appealExists'));
    var conv = db.conversations[ev.uid];
    var original = clone(cr);
    original.final_verdict = cr.final_verdict;
    var a = {
      id: ++db.seq.appeal, evaluation_id: ev.id, uid: ev.uid, criterion_id: cr.criterion_id, criterion_title: cr.title,
      comment: body.comment, counter_evidence: body.counter_evidence || [], status: 'open', created_at: now(),
      original: original, fragment: fragmentFor(conv, cr, body.counter_evidence), ai_opinion: null, decision: null,
      prev_final_total: null, new_final_total: null
    };
    db.appeals.push(a);
    cr.appeal_id = a.id; cr.appeal_status = 'open';
    var s = summary(ev.uid); if (s) s.open_appeals += 1;
    return a;
  });
  route('POST', /^\/appeals\/(\d+)\/decision$/, function (m, q, body) {
    var a = db.appeals.filter(function (x) { return x.id === Number(m[1]); })[0];
    if (!a) throw err(404, CQA.t('mock.notFound'));
    if (a.status === 'decided') throw err(409, CQA.t('mock.alreadyDecided'));
    if (!body || (body.action !== 'confirm' && body.action !== 'change')) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'action', msg: CQA.t('val.required') }]);
    if (!body.reason || !String(body.reason).trim()) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'reason', msg: CQA.t('val.required') }]);
    var ev = evalById(a.evaluation_id);
    var cr = ev.criteria.filter(function (c) { return c.criterion_id === a.criterion_id; })[0];
    var prev = ev.final_total;
    if (body.action === 'change') {
      if (!body.new_verdict) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'new_verdict', msg: CQA.t('val.required') }]);
      cr.final_verdict = body.new_verdict;
    }
    var next = recomputeFinal(ev);
    cr.appeal_status = 'decided';
    a.status = 'decided';
    a.prev_final_total = prev;
    a.new_final_total = next;
    a.decision = { action: body.action, new_verdict: body.action === 'change' ? body.new_verdict : null, reason: body.reason, reviewer: 'reviewer', make_precedent: !!body.make_precedent, created_at: now(), prev_final_total: prev, new_final_total: next };
    var s = summary(ev.uid); if (s) s.open_appeals = Math.max(0, s.open_appeals - 1);
    return a;
  });

  route('GET', /^\/export$/, function (m, q) {
    var final = q.final === '1';
    var evs = db.summaries.filter(function (s) { return !q.group || s.group_id === q.group; }).map(function (s) { return db.evaluations[s.uid]; }).filter(Boolean);
    if (q.format === 'json') {
      return new Blob([JSON.stringify(evs, null, 1)], { type: 'application/json' });
    }
    var ver = activeVersion();
    var ids = ver.config.criteria.map(function (c) { return c.id; });
    var head = ['id', 'language', 'channel'].concat(ids.map(function (i) { return 'c' + i; }), ['total', 'gross', 'gross_categories']);
    var esc = function (v) { v = v == null ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    var lines = [head.join(',')];
    evs.forEach(function (e) {
      var s = summary(e.uid);
      var by = {};
      e.criteria.forEach(function (c) { by[c.criterion_id] = final ? c.final_verdict : c.verdict; });
      lines.push([s.external_id, s.language, s.channel].concat(ids.map(function (i) { return by[i] || ''; }),
        [final ? e.final_total : e.total, e.gross ? 1 : 0, (e.gross_categories || []).join('|')]).map(esc).join(','));
    });
    return new Blob(['\ufeff' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
  });

  function handle(method, path, opts) {
    opts = opts || {};
    return load().then(function () {
      var q = opts.query || {};
      for (var i = 0; i < routes.length; i++) {
        var r = routes[i];
        if (r.method !== method) continue;
        var mm = r.re.exec(path);
        if (!mm) continue;
        var body = opts.body;
        return Promise.resolve().then(function () { return r.fn(mm, q, body); }).then(function (res) {
          return delay(res instanceof Blob ? res : clone(res));
        });
      }
      throw err(404, CQA.t('mock.noRoute', { m: method, p: path }));
    });
  }

  CQA.mock = { load: load, handle: handle, silentWav: silentWav };
})(window.CQA = window.CQA || {});
