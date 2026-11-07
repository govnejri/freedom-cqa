/* Мок-бэкенд: ответы §8 из frontend/mock/*.json (в сборке — <script type="application/json" id="mock-…">).
   Запись (апелляции, решения, версии чек-листа, прогоны, прецеденты, сессии тренажёра) живёт в памяти вкладки.
   P2 (операторы, срезы, прецеденты) считается здесь по диалогам мока — как cqa/features/operators.py и slices.py.
   P3 (тренажёр, тьютор, ассистент, сводка оператора) — записанные реальные ответы из mock/p23.json
   (frontend/mock/generate_p23.py); там же реальные диалоги, на которые эти ответы ссылаются. */
(function (CQA) {
  'use strict';

  var FILES = ['health', 'datasets', 'conversations', 'conversation-details', 'evaluations', 'appeals', 'checklist-versions', 'runs', 'p1', 'p23'];
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
        seq: { appeal: 100, version: 100, run: 100, compliance: 0, precedent: 0, session: 0, lesson: 0 },
        p1: d.p1 || null,
        p23: d.p23 || null,
        compliance: {},
        overrides: {},
        opOverrides: {},
        precedents: [],
        sessions: {},
        lessons: []
      };
      mergeP23();
      enrichP1();
      initP23();
      return db;
    });
    return loading;
  }

  // ---------------------------------------------------------------- P1: дополнение мок-оценок
  /** Допустимые вердикты, верхняя ступень и мнение второй модели (реальная перекрёстная проверка того же диалога;
      согласие пересчитывается против мок-вердикта). Число вызовов, стоимость и время — по реальному журналу. */
  function stepDist(c, a, b) {
    var vals = (c.scale || []).map(function (x) { return String(x.value); }).sort(function (x, y) { return Number(y) - Number(x); });
    var i = vals.indexOf(String(a)), j = vals.indexOf(String(b));
    return i < 0 || j < 0 ? null : Math.abs(i - j);
  }
  function decorate(cr, c, so) {
    if (c) {
      cr.allowed_verdicts = CQA.scoring.allowedVerdicts(c);
      cr.scale_top = c.type === 'scale' ? CQA.scoring.top(c) : null;
    }
    if (so && so.verdict) {
      var o = Object.assign({}, so, { compared_to: cr.verdict, agree: so.verdict === cr.verdict });
      o.near = !o.agree && !!c && c.type === 'scale' && stepDist(c, cr.verdict, so.verdict) === 1;
      cr.second_opinion = o;
    }
  }
  function enrichP1() {
    var p1 = db.p1;
    if (!p1) return;
    var crit = function (vid, cid) {
      var ver = versionById(vid) || activeVersion();
      return ver.config.criteria.filter(function (c) { return c.id === cid; })[0] || null;
    };
    Object.keys(db.evaluations).forEach(function (uid) {
      var ev = db.evaluations[uid];
      var so = (p1.second_opinions || {})[uid] || {};
      ev.criteria.forEach(function (cr) { decorate(cr, crit(ev.checklist_version_id, cr.criterion_id), so[cr.criterion_id]); });
      var calls = (p1.calls && p1.calls.by_evaluation[String(ev.id)]) || null;
      if (calls) {
        var paid = calls.filter(function (c) { return !c.cache_hit; });
        ev.n_llm_calls = calls.length;
        ev.cost_usd = Math.round(paid.reduce(function (a, c) { return a + (c.cost_usd || 0); }, 0) * 1e8) / 1e8;
        ev.latency_ms = paid.reduce(function (a, c) { return a + (c.latency_ms || 0); }, 0);
      }
    });
    db.appeals.forEach(function (a) {
      var ev = evalById(a.evaluation_id);
      if (ev && a.original) decorate(a.original, crit(ev.checklist_version_id, a.criterion_id), null);
      // реальное второе мнение по такой же апелляции — выдаётся по кнопке «Запросить второе мнение ИИ»
      if ((p1.appeal_opinions || {})[a.id] && a.status === 'open') a.ai_opinion = null;
    });
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
    if (!c) throw err(404, notInDemo(decodeURIComponent(m[1])));
    var out = clone(c);
    if (out.has_audio) {
      var last = 0;
      out.messages.forEach(function (x) { if (x.audio_sec != null) last = Math.max(last, x.audio_sec); });
      out.audio_url = silentWav(last + 8);
    }
    return out;
  });
  route('GET', /^\/conversations\/([^/]+)\/evaluation$/, function (m) {
    var uid = decodeURIComponent(m[1]);
    var e = db.evaluations[uid];
    if (!e) throw err(404, db.conversations[uid] ? CQA.t('mock.notEvaluated') : notInDemo(uid));
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
    if (body.make_precedent) addPrecedent(a);
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

  // ---------------------------------------------------------------- P1: инсайты
  function round2(x) { return Math.round(x * 100) / 100; }
  function avg(list) {
    var v = list.filter(function (x) { return x != null; });
    return v.length ? round2(v.reduce(function (a, x) { return a + x; }, 0) / v.length) : null;
  }
  function finalScore(ev) { return ev.final_total != null ? ev.final_total : ev.total; }
  function finalGross(ev) { return ev.criteria.some(function (c) { return c.type === 'flag' && (c.final_verdict || c.verdict) === 'Да'; }); }
  function currentRows(group) {
    return db.summaries.filter(function (s) { return !group || s.group_id === group; }).map(function (s) {
      var ev = db.evaluations[s.uid];
      return ev ? { s: s, ev: ev, sig: db.p1 && db.p1.signals[s.uid] } : null;
    }).filter(Boolean);
  }
  function messageText(uid, mid) {
    var c = db.conversations[uid];
    var m = c && c.messages.filter(function (x) { return x.mid === mid; })[0];
    return m || null;
  }
  function caseKey(uid, cid) { return uid + '|' + cid; }

  function risksList(group) {
    var items = [];
    currentRows(group).forEach(function (row) {
      row.ev.criteria.forEach(function (cr) {
        var fin = cr.final_verdict || cr.verdict;
        if (cr.type !== 'flag' || fin !== 'Да') return;
        var c = db.conversations[row.s.uid];
        items.push({
          criterion_result_id: cr.id, evaluation_id: row.ev.id, criterion_id: cr.criterion_id, criterion_title: cr.title,
          uid: row.s.uid, group_id: row.s.group_id, external_id: row.s.external_id, language: row.s.language, channel: row.s.channel,
          category: (cr.category || '').trim() || 'без категории', verdict: fin, changed_by_appeal: fin !== cr.verdict,
          evidence: (cr.evidence || []).filter(function (e) { return e.ok !== false; }).map(function (e) {
            var m = c && c.messages.filter(function (x) { return x.mid === e.id; })[0];
            return { mid: e.id, quote: e.quote, text: m ? m.text : null, speaker: m ? m.speaker : null, audio_sec: m ? m.audio_sec : null };
          }),
          explanation: cr.explanation || '', how_to_fix: cr.how_to_fix || '', needs_review: !!cr.needs_review,
          total: finalScore(row.ev), evaluated_at: row.ev.created_at, compliance: db.compliance[caseKey(row.s.uid, cr.criterion_id)] || null
        });
      });
    });
    items.sort(function (a, b) { return String(b.evaluated_at).localeCompare(String(a.evaluated_at)) || b.criterion_result_id - a.criterion_result_id; });
    var cats = {};
    items.forEach(function (it) { (cats[it.category] = cats[it.category] || []).push(it.criterion_result_id); });
    var by = Object.keys(cats).map(function (k) { return { category: k, count: cats[k].length, criterion_result_ids: cats[k] }; })
      .sort(function (a, b) { return b.count - a.count || a.category.localeCompare(b.category); });
    var uids = {};
    items.forEach(function (it) { uids[it.uid] = 1; });
    return { total: items.length, n_conversations: Object.keys(uids).length, items: items, by_category: by };
  }

  function unansweredData(group, topic) {
    var rows = currentRows(group), withSig = rows.filter(function (r) { return r.sig; });
    var by = {};
    withSig.forEach(function (r) { var k = r.sig.contact_reason || 'прочее'; (by[k] = by[k] || []).push(r); });
    var topics = [];
    Object.keys(by).forEach(function (name) {
      if (topic && name.toLowerCase() !== String(topic).toLowerCase()) return;
      var questions = [], scores = [];
      by[name].forEach(function (r) {
        if (!r.sig.unanswered.length) return;
        scores.push(finalScore(r.ev));
        r.sig.unanswered.forEach(function (q) {
          var m = messageText(r.s.uid, q.mid);
          questions.push({ text: q.text || '', status: q.status || 'открыт', uid: r.s.uid, external_id: r.s.external_id, mid: q.mid || '',
            message_text: m ? m.text : null, language: r.s.language, channel: r.s.channel, score: finalScore(r.ev) });
        });
      });
      if (!questions.length && !topic) return;
      topics.push({ topic: name, n_conversations: scores.length, n_conversations_total: by[name].length, n_questions: questions.length,
        avg_total: avg(by[name].map(function (r) { return finalScore(r.ev); })), avg_total_unanswered: avg(scores), questions: questions });
    });
    topics.sort(function (a, b) { return b.n_questions - a.n_questions || a.topic.localeCompare(b.topic); });
    return { n_evaluated: rows.length, n_with_signals: withSig.length,
      total_questions: topics.reduce(function (a, t) { return a + t.n_questions; }, 0),
      n_conversations: topics.reduce(function (a, t) { return a + t.n_conversations; }, 0), topics: topics };
  }

  route('GET', /^\/insights\/risks$/, function (m, q) { return risksList(q.group); });
  route('POST', /^\/insights\/risks\/(\d+)\/compliance$/, function (m, q, body) {
    var id = Number(m[1]), found = null;
    Object.keys(db.evaluations).forEach(function (uid) {
      db.evaluations[uid].criteria.forEach(function (cr) { if (cr.id === id) found = { uid: uid, cr: cr }; });
    });
    if (!found) throw err(404, CQA.t('mock.notFound'));
    if (found.cr.type !== 'flag') throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'criterion_result_id', msg: 'не флаг грубой ошибки' }]);
    var key = caseKey(found.uid, found.cr.criterion_id);
    if (db.compliance[key]) return Object.assign({}, db.compliance[key], { created: false });
    if ((found.cr.final_verdict || found.cr.verdict) !== 'Да') throw err(409, 'флаг не выставлен: финальный вердикт не «Да»');
    db.compliance[key] = { id: ++db.seq.compliance, criterion_result_id: id, status: 'open', note: (body && body.note) || null, created_at: now(), created: false };
    return Object.assign({}, db.compliance[key], { created: true });
  });
  route('GET', /^\/insights\/unanswered$/, function (m, q) { return unansweredData(q.group, q.topic); });
  route('GET', /^\/insights\/reasons$/, function (m, q) {
    var rows = currentRows(q.group), withSig = rows.filter(function (r) { return r.sig; });
    var by = {};
    withSig.forEach(function (r) { var k = r.sig.contact_reason || 'прочее'; (by[k] = by[k] || []).push(r); });
    var reasons = Object.keys(by).map(function (name) {
      var list = by[name], n = list.length;
      var un = list.map(function (r) { return r.sig.unanswered.length; });
      var resolved = list.filter(function (r) { return r.sig.resolved; }).length;
      return { topic: name, n_conversations: n, share: Math.round(n / withSig.length * 1e4) / 1e4,
        avg_total: avg(list.map(function (r) { return finalScore(r.ev); })), resolved_share: Math.round(resolved / n * 1e4) / 1e4, n_resolved: resolved,
        gross_count: list.filter(function (r) { return finalGross(r.ev); }).length, churn_count: list.filter(function (r) { return r.sig.churn_risk; }).length,
        n_unanswered_conversations: un.filter(Boolean).length, n_unanswered_questions: un.reduce(function (a, x) { return a + x; }, 0),
        missed_opportunity_count: list.filter(function (r) { return (r.sig.missed_opportunity || '').trim(); }).length };
    }).sort(function (a, b) { return b.n_conversations - a.n_conversations || a.topic.localeCompare(b.topic); });
    return { n_evaluated: rows.length, n_with_signals: withSig.length, reasons: reasons };
  });
  route('POST', /^\/insights\/signals$/, function (m, q, body) {
    var rows = currentRows(body && body.group);
    // в демо-режиме модель не вызывается: сигналы есть только у диалогов из демо-набора
    return { status: 'nothing_to_do', queued: 0, already: rows.filter(function (r) { return r.sig; }).length, group: (body && body.group) || null, profile: currentRoles().signals };
  });
  route('GET', /^\/insights\/summary$/, function (m, q) {
    var list = db.summaries.filter(function (s) { return !q.group || s.group_id === q.group; });
    var rows = currentRows(q.group);
    var un = unansweredData(q.group);
    var openCases = Object.keys(db.compliance).filter(function (k) {
      var uid = k.split('|')[0], s = summary(uid);
      return db.compliance[k].status === 'open' && s && (!q.group || s.group_id === q.group);
    }).length;
    return {
      group: q.group || null, n_conversations: list.length, n_evaluated: rows.length,
      avg_total: avg(rows.map(function (r) { return finalScore(r.ev); })),
      gross_count: rows.filter(function (r) { return finalGross(r.ev); }).length,
      needs_review: rows.filter(function (r) { return r.ev.needs_review; }).length,
      open_appeals: db.appeals.filter(function (a) { var s = summary(a.uid); return a.status === 'open' && s && (!q.group || s.group_id === q.group); }).length,
      risks_open_compliance: openCases, n_with_signals: un.n_with_signals, unanswered_questions: un.total_questions,
      top_unanswered_topics: un.topics.slice(0, 3).map(function (t) { return { topic: t.topic, n_questions: t.n_questions, n_conversations: t.n_conversations }; })
    };
  });

  // ---------------------------------------------------------------- P1: качество
  function p1(name) {
    if (!db.p1 || db.p1[name] == null) throw err(404, CQA.t('mock.noRoute', { m: 'GET', p: name }));
    return db.p1[name];
  }
  route('GET', /^\/quality$/, function (m, q) {
    var qd = p1('quality');
    var run = q.run ? String(q.run) : qd['default'].split('|')[0];
    var key = run + '|' + (q.split || 'all') + '|' + (q.by || 'language');
    var r = qd.reports[key];
    if (r) return r;
    var base = qd.reports[run + '|all|language'];
    if (!base) throw err(404, CQA.t('mock.notFound'));
    r = clone(base);
    r.warnings = (r.warnings || []).concat([CQA.t('mock.qualitySlice')]);
    return r;
  });
  route('GET', /^\/quality\/runs$/, function () { return p1('quality_runs'); });
  route('GET', /^\/quality\/autotests$/, function () { return p1('autotests'); });

  // ---------------------------------------------------------------- P1: настройки ролей
  function currentRoles() {
    var s = db.p1 ? db.p1.settings : { defaults: db.health.roles };
    var roles = Object.assign({}, s.defaults);
    Object.keys(db.overrides).forEach(function (k) { roles[k] = db.overrides[k]; });
    return roles;
  }
  function settingsView() {
    var s = clone(p1('settings'));
    s.roles = currentRoles();
    s.overrides = clone(db.overrides);
    return s;
  }
  route('GET', /^\/settings$/, function () { return settingsView(); });
  route('PUT', /^\/settings$/, function (m, q, body) {
    var s = p1('settings');
    var names = s.profiles.map(function (p) { return p.name; });
    var roles = (body && body.roles) || {};
    Object.keys(roles).forEach(function (role) {
      if (!Object.prototype.hasOwnProperty.call(s.defaults, role)) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'roles.' + role, msg: 'нет роли ' + role }]);
      var v = roles[role];
      if (!v) { delete db.overrides[role]; return; }
      if (names.indexOf(v) < 0) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'roles.' + role, msg: 'нет профиля ' + v }]);
      db.overrides[role] = v;
    });
    db.health.roles = currentRoles();
    return settingsView();
  });

  // ---------------------------------------------------------------- P1: журнал оценки
  function strings(i) { var list = db.p1.calls.strings; return i == null ? null : list[i]; }
  function callMeta(id) {
    var by = db.p1 ? db.p1.calls.by_evaluation : {};
    for (var k in by) for (var i = 0; i < by[k].length; i++) if (by[k][i].id === id) return by[k][i];
    return null;
  }
  route('GET', /^\/conversations\/([^/]+)\/evaluations$/, function (m) {
    var e = db.evaluations[decodeURIComponent(m[1])];
    return e ? [e] : [];
  });
  route('GET', /^\/evaluations\/(\d+)\/calls$/, function (m) {
    if (!evalById(m[1])) throw err(404, CQA.t('mock.notFound'));
    var calls = db.p1 && db.p1.calls.by_evaluation[m[1]];
    if (!calls && db.p23Evals && db.p23Evals[m[1]]) throw err(404, 'Журнал этой оценки в демо-данные не включён — он есть в полной базе (подключите API).');
    return calls || [];
  });
  route('GET', /^\/llm-calls\/(\d+)$/, function (m) {
    var meta = callMeta(Number(m[1]));
    var det = db.p1 && db.p1.calls.details[m[1]];
    if (!meta || !det) throw err(404, CQA.t('mock.notFound'));
    var req = det.request ? clone(det.request) : null;
    if (req) {
      req.messages = (req.messages || []).map(function (x) { return { role: x.role, content: strings(x.content) }; });
      if (req.json_schema != null) { try { req.json_schema = JSON.parse(strings(req.json_schema)); } catch (e) { req.json_schema = null; } }
    }
    return Object.assign({}, meta, { request: req, response_text: strings(det.response_text) });
  });

  // ---------------------------------------------------------------- P1: второе мнение по апелляции
  route('POST', /^\/appeals\/(\d+)\/second-opinion$/, function (m) {
    var a = db.appeals.filter(function (x) { return x.id === Number(m[1]); })[0];
    if (!a) throw err(404, CQA.t('mock.notFound'));
    var op = db.p1 && db.p1.appeal_opinions[String(a.id)];
    if (!op) throw err(422, CQA.t('mock.noOpinion'), [{ loc: 'appeal', msg: CQA.t('mock.noOpinion') }]);
    a.ai_opinion = Object.assign({}, op, { created_at: now() });
    return a;
  });

  // ---------------------------------------------------------------- P1: пробный прогон и «было/стало»
  function diffSummary(dialogs, criteria) {
    var s = { dialogs: dialogs.length, completed: 0, with_baseline: 0, verdicts_compared: 0, verdicts_changed: 0, dialogs_changed: 0, gross_changed: 0,
      mean_delta: null, mean_abs_delta: null, new_verdicts: {}, old_verdicts: {}, transitions: {} };
    var deltas = [];
    criteria.forEach(function (c) { s.new_verdicts[c] = {}; s.old_verdicts[c] = {}; });
    dialogs.forEach(function (x) {
      if (x.status === 'done') s.completed += 1;
      if (x.baseline_evaluation_id != null) s.with_baseline += 1;
      if (x.changed_criteria.length) s.dialogs_changed += 1;
      if (x.delta != null) deltas.push(x.delta);
      if (x.old_gross != null && x.new_gross != null && x.old_gross !== x.new_gross) s.gross_changed += 1;
      x.criteria.forEach(function (c) {
        if (!c.requested || c.new == null) return;
        var nv = s.new_verdicts[c.criterion_id];
        nv[c.new] = (nv[c.new] || 0) + 1;
        if (c.old == null) return;
        var ov = s.old_verdicts[c.criterion_id];
        ov[c.old] = (ov[c.old] || 0) + 1;
        s.verdicts_compared += 1;
        if (c.new !== c.old) {
          s.verdicts_changed += 1;
          var tr = s.transitions[c.criterion_id] = s.transitions[c.criterion_id] || {};
          tr[c.old + ' → ' + c.new] = (tr[c.old + ' → ' + c.new] || 0) + 1;
        }
      });
    });
    if (deltas.length) {
      s.mean_delta = round2(deltas.reduce(function (a, x) { return a + x; }, 0) / deltas.length);
      s.mean_abs_delta = round2(deltas.reduce(function (a, x) { return a + Math.abs(x); }, 0) / deltas.length);
    }
    return s;
  }
  route('POST', /^\/checklist-versions\/(\d+)\/dry-run$/, function (m, q, body) {
    var v = versionById(m[1]);
    if (!v) throw err(404, CQA.t('mock.notFound'));
    if (body && body.uids && body.uids.length) return dryRunUids(v, body);
    var tpl = db.p1 && db.p1.dry_run_diff;
    if (!tpl) throw err(404, CQA.t('mock.noRoute', { m: 'POST', p: 'dry-run' }));
    var act = activeVersion();
    var base = act.id !== v.id ? act : (db.versions.filter(function (x) { return x.checklist_id === v.checklist_id && x.version < v.version; }).sort(function (a, b) { return b.version - a.version; })[0] || null);
    var changed = base && CQA.checklistChangedIds ? CQA.checklistChangedIds(base.config, v.config) : [];
    if (!changed.length) throw err(422, CQA.t('mock.validationFailed'), [{ loc: 'criteria', msg: 'версия не отличается от активной по критериям — укажите criteria явно' }]);
    createRun({ kind: 'dry_run', checklist_version_id: v.id, criteria: tpl.criteria, uids: tpl.dialogs.map(function (x) { return x.uid; }), force: true });
    var r = db.runs[db.runs.length - 1];
    r._failAt = null; r._cached = 0; r._rate = 1.2;
    r._diff = { base: base ? base.id : null };
    return runView(r);
  });
  route('GET', /^\/runs\/(\d+)\/diff$/, function (m) {
    var r = db.runs.filter(function (x) { return x.id === Number(m[1]); })[0];
    if (!r) throw err(404, CQA.t('mock.notFound'));
    var tpl = r._diff && r._diff.tpl ? r._diff.tpl : db.p1 && db.p1.dry_run_diff;
    if (!r._diff || !tpl) throw err(404, CQA.t('mock.noRoute', { m: 'GET', p: '/runs/' + r.id + '/diff' }));
    var rv = runView(r);
    var n = tpl.dialogs.length;
    var k = rv.units_total ? Math.floor((rv.units_done + rv.units_failed) / rv.units_total * n) : 0;
    var dialogs = tpl.dialogs.map(function (x, i) {
      if (i < k) return clone(x);
      var y = clone(x);
      y.status = 'running'; y.new_total = null; y.delta = null; y.new_gross = null; y.changed_criteria = [];
      y.criteria.forEach(function (c) { c.new = null; c.status = 'running'; c.changed = false; c.new_explanation = ''; c.new_evidence = []; });
      return y;
    });
    var v = versionById(r.checklist_version_id), b = r._diff.base ? versionById(r._diff.base) : null;
    var out = {
      run: rv, checklist_version_id: r.checklist_version_id, checklist_version: v ? v.version : null,
      compared_with_version_id: b ? b.id : null, compared_with_version: b ? b.version : null, baseline: 'current',
      criteria: tpl.criteria.slice(), summary: diffSummary(dialogs, tpl.criteria), dialogs: dialogs
    };
    if (r._diff.demo) out.demo_note = DEMO_DRY_NOTE;
    return out;
  });

  // ================================================================ P2/P3: реальные диалоги-дополнения и записи (mock/p23.json)
  var DEMO_DRY_NOTE = 'Демо-режим: модель не вызывается — «стало» повторяет «было». Реальный результат — с подключённым API.';
  var DEMO_TRAINER_NOTE = 'Демо-режим: клиент отвечает записанными репликами реальной сессии, оценка «стало» — записанная оценка той сессии.';

  function notInDemo(uid) {
    return 'В демо-данных нет диалога ' + fmtId(uid) + ' — он есть в полной базе (подключите API).';
  }
  function fmtId(uid) { var i = String(uid || '').indexOf(':'); return i >= 0 ? String(uid).slice(i + 1) : String(uid || ''); }
  function slow(ms, v) {
    if (CQA.mock && CQA.mock.fast) return v;
    return new Promise(function (res) { setTimeout(function () { res(v); }, ms); });
  }
  function vErr(status, loc, msg) { return err(status, msg, [{ loc: loc, msg: msg }]); }
  function strip(o) {
    var out = clone(o);
    Object.keys(out).forEach(function (k) { if (k.charAt(0) === '_') delete out[k]; });
    return out;
  }

  /** Диалоги из p23.json: реальные сводка, транскрипт, оценка и сигналы; диалоги тренажёра — только карточки. */
  function mergeP23() {
    var p = db.p23;
    if (!p) return;
    var added = {};
    (p.extras || []).concat(p.trainer_cards || []).forEach(function (x) {
      if (db.conversations[x.uid]) return;
      db.conversations[x.uid] = x.conversation;
      db.evaluations[x.uid] = x.evaluation;
      db.p23Evals = db.p23Evals || {};
      db.p23Evals[x.evaluation.id] = true;
      if (x.in_lists && x.summary) {
        db.summaries.push(x.summary);
        added[x.summary.group_id] = (added[x.summary.group_id] || 0) + 1;
        if (x.signals && db.p1) db.p1.signals[x.uid] = x.signals;
      }
    });
    db.datasets.forEach(function (d) { if (added[d.group_id]) d.total_records += added[d.group_id]; });
    var pf = p.precedent_flow;
    if (pf && pf.appeal && evalById(pf.appeal.evaluation_id) && !db.appeals.some(function (a) { return a.id === pf.appeal.id; })) {
      db.appeals.push(clone(pf.appeal));
    }
  }

  function initP23() {
    // прецеденты: решения мок-апелляций с make_precedent, затем записанный реальный
    db.appeals.slice().sort(function (a, b) { return a.id - b.id; }).forEach(function (a) {
      if (a.status === 'decided' && a.decision && a.decision.make_precedent && !(db.p23 && db.p23.precedent_flow && db.p23.precedent_flow.appeal && db.p23.precedent_flow.appeal.id === a.id)) addPrecedent(a, a.decision.created_at);
    });
    var p = db.p23;
    if (!p) return;
    var pf = p.precedent_flow;
    if (pf && pf.precedent) {
      var rp = pf.precedent;
      db.precedents.push({ id: ++db.seq.precedent, appeal_id: rp.appeal_id, criterion_id: rp.criterion_id, fragment_full: rp.fragment_full,
        verdict: rp.verdict, why: rp.why, added_in_version_id: null, created_at: rp.created_at, _recorded: true,
        _rec: { uid: rp.uid, external_id: rp.external_id, group_id: rp.group_id, language: rp.language, channel: rp.channel,
          evaluation_id: rp.evaluation_id, original_verdict: rp.original_verdict, action: rp.action, reviewer: rp.reviewer } });
    }
    var tr = p.trainer;
    if (tr && tr.session) {
      var s = clone(tr.session);
      db.sessions[s.id] = s;
      db.seq.session = Math.max(db.seq.session, s.id);
    }
    (p.lessons || []).forEach(function (l) { db.lessons.push(clone(l)); db.seq.lesson = Math.max(db.seq.lesson, l.id); });
  }

  // ---------------------------------------------------------------- P2: текущие оценки диалогов (cqa/features/operators.py)
  var AUTOTEST = 'AUTOTEST', YES = 'Да', NO = 'Нет', NA = 'N/A';
  function normName(n) { var s = String(n == null ? '' : n).split(/\s+/).filter(Boolean).join(' '); return s || null; }
  function nameKey(n) { return (normName(n) || '').toLowerCase(); }
  function enabledCrits(cfg) { return (cfg.criteria || []).filter(function (c) { return c.enabled !== false; }); }
  function rnd(x, nd) {
    if (x == null || isNaN(x)) return null;
    var f = Math.pow(10, nd == null ? 2 : nd);
    return Math.round((x + (x >= 0 ? 1 : -1) * Number.EPSILON) * f) / f;
  }
  function mean(xs) { return xs.length ? xs.reduce(function (a, x) { return a + x; }, 0) / xs.length : null; }
  function cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

  function loadDialogs(group) {
    var out = [];
    db.summaries.forEach(function (s) {
      if (s.group_id === AUTOTEST || (group && s.group_id !== group)) return;
      var ev = db.evaluations[s.uid];
      if (!ev) return;
      var sig = db.p1 && db.p1.signals ? db.p1.signals[s.uid] : null;
      var d = {
        uid: s.uid, group_id: s.group_id, external_id: s.external_id, language: s.language, channel: s.channel,
        operator: normName(s.operator_name), evaluation_id: ev.id, status: ev.status || 'done', total: finalScore(ev),
        gross: finalGross(ev), gross_categories: [], needs_review: !!ev.needs_review,
        contact_reason: sig ? sig.contact_reason || null : null, manual: has(db.opOverrides, s.uid),
        n_appeals: 0, n_open_appeals: 0, evaluated_at: ev.created_at || null,
        verdicts: {}, original: {}, points: {}, results: {}, changes: {}
      };
      ev.criteria.forEach(function (cr) {
        if (cr.score) d.points[cr.criterion_id] = [Number(cr.score.points) || 0, Number(cr.score.max_points) || 0];
        if (cr.status !== 'done' || !cr.verdict) return;
        d.results[cr.criterion_id] = cr;
        d.original[cr.criterion_id] = cr.verdict;
        d.verdicts[cr.criterion_id] = cr.final_verdict != null ? cr.final_verdict : cr.verdict;
        if (cr.type === 'flag' && d.verdicts[cr.criterion_id] === YES) {
          var cat = String(cr.category || '').trim();
          if (cat && d.gross_categories.indexOf(cat) < 0) d.gross_categories.push(cat);
        }
      });
      if (d.gross && !d.gross_categories.length) d.gross_categories = (ev.gross_categories || []).slice();
      db.appeals.forEach(function (a) {
        if (a.evaluation_id !== ev.id) return;
        d.n_appeals += 1;
        if (a.status === 'open') d.n_open_appeals += 1;
        if (a.decision && a.decision.action === 'change' && a.decision.new_verdict && has(d.verdicts, a.criterion_id)) {
          d.changes[a.criterion_id] = a.decision.reason || '';
        }
      });
      out.push(d);
    });
    out.sort(function (a, b) { return cmp(a.group_id, b.group_id) || cmp(a.external_id, b.external_id); });
    return out;
  }

  function verdictDegree(c, v) {
    if (v == null || v === NA) return null;
    if (c.type === 'scale') {
      var n = Number(v), top = CQA.scoring.top(c) || 0;
      if (isNaN(n) || !top) return null;
      return Math.min(1, Math.max(0, 1 - n / top));
    }
    if (c.type === 'flag') return v === YES ? 1 : v === NO ? 0 : null;
    return v === YES ? 0 : v === NO ? 1 : null;
  }

  function critStats(dialogs, c) {
    var n = 0, nNa = 0, nYes = 0, viol = 0, degSum = 0, steps = [], pts = [], maxp = [], lost = [], dist = {};
    dialogs.forEach(function (d) {
      var v = d.verdicts[c.id];
      if (v == null) return;
      n += 1;
      dist[v] = (dist[v] || 0) + 1;
      var p = d.points[c.id];
      lost.push(p ? Math.max(0, p[1] - p[0]) : 0);
      var deg = verdictDegree(c, v);
      if (deg == null) { nNa += 1; return; }
      degSum += deg;
      if (deg > 0) viol += 1;
      if (p) { pts.push(p[0]); maxp.push(p[1]); }
      if (c.type === 'scale') steps.push(Number(v));
      else if (v === YES) nYes += 1;
    });
    var nApp = n - nNa, top = CQA.scoring.top(c);
    var out = { n: n, n_applicable: nApp, n_na: nNa, verdicts: dist, violations: viol };
    if (c.type === 'scale') {
      var m = mean(steps);
      out.mean_step = rnd(m); out.top = top; out.quality = m != null && top ? rnd(m / top, 4) : null;
    } else {
      var share = nApp ? nYes / nApp : null;
      out.n_yes = nYes; out.yes_share = rnd(share, 4);
      out.quality = rnd(share != null && c.type === 'flag' ? 1 - share : share, 4);
    }
    out.violation_rate = nApp ? rnd(degSum / nApp, 4) : null;
    out.loss = n ? rnd(Number(c.weight) * degSum / n) : 0;
    out.avg_points = rnd(mean(pts));
    out.avg_max_points = rnd(mean(maxp));
    out.lost_points = rnd(mean(lost)) || 0;
    return out;
  }

  function avgTotal(dialogs) { return rnd(mean(dialogs.map(function (d) { return d.total; }).filter(function (x) { return x != null; }))); }

  function criteriaHeader(cfg) {
    return enabledCrits(cfg).map(function (c) {
      return { id: c.id, title: c.title, type: c.type, weight: c.weight, top: CQA.scoring.top(c), section: c.section };
    });
  }

  function dialogExample(d, cid) {
    var r = d.results[cid] || null;
    var evidence = ((r && r.evidence) || []).filter(function (e) { return e && e.id && e.ok !== false; });
    var rng = r && r.checked_range && r.checked_range.length ? r.checked_range : null;
    var mids = [];
    evidence.forEach(function (e) { if (mids.indexOf(e.id) < 0) mids.push(e.id); });
    var mid = mids[0] || (rng ? rng[0] : null);
    var msg = mid ? messageText(d.uid, mid) : null;
    var expl = r ? r.explanation || '' : '';
    if (has(d.changes, cid)) expl = ('Изменено по апелляции: ' + d.changes[cid]).trim();
    return {
      uid: d.uid, external_id: d.external_id, evaluation_id: d.evaluation_id, criterion_result_id: r ? r.id : null,
      language: d.language, channel: d.channel, total: d.total,
      verdict: has(d.verdicts, cid) ? d.verdicts[cid] : null, original_verdict: has(d.original, cid) ? d.original[cid] : null,
      changed_by_appeal: has(d.changes, cid), mid: mid || null, mids: mids, checked_range: rng ? rng.slice() : null,
      quote: evidence.length ? evidence[0].quote || '' : '', message_text: msg ? msg.text : null,
      audio_sec: msg && msg.audio_sec != null ? msg.audio_sec : null,
      explanation: expl, how_to_fix: r ? r.how_to_fix || '' : ''
    };
  }

  function violationExamples(dialogs, c, k) {
    var bad = dialogs.map(function (d) { return [verdictDegree(c, d.verdicts[c.id]) || 0, d]; }).filter(function (x) { return x[0] > 0; });
    bad.sort(function (a, b) {
      return (b[0] - a[0]) || ((a[1].total != null ? a[1].total : 101) - (b[1].total != null ? b[1].total : 101)) || cmp(a[1].external_id, b[1].external_id);
    });
    return bad.slice(0, k == null ? 3 : k).map(function (x) { return dialogExample(x[1], c.id); });
  }

  function growthZones(cfg, mine, dept) {
    var zones = [];
    enabledCrits(cfg).forEach(function (c) {
      if (!(Number(c.weight) > 0)) return;
      var st = critStats(mine, c);
      if (!st.n || !st.loss || st.loss <= 0) return;
      var dst = dept ? critStats(dept, c) : null;
      zones.push({
        criterion_id: c.id, title: c.title, type: c.type, weight: c.weight, loss: st.loss, lost_points: st.lost_points,
        violations: st.violations, n_applicable: st.n_applicable, n_na: st.n_na, violation_rate: st.violation_rate,
        quality: st.quality, dept_loss: dst ? dst.loss : null, dept_quality: dst ? dst.quality : null, examples: []
      });
    });
    zones.sort(function (a, b) { return (b.loss - a.loss) || ((b.lost_points || 0) - (a.lost_points || 0)) || (b.weight - a.weight) || cmp(a.criterion_id, b.criterion_id); });
    zones = zones.slice(0, 3);
    var byId = {};
    cfg.criteria.forEach(function (c) { byId[c.id] = c; });
    zones.forEach(function (z) { z.examples = violationExamples(mine, byId[z.criterion_id], 3); });
    return zones;
  }

  function strengthsOf(cfg, mine, dept, exclude) {
    var out = [], minN = mine.length >= 3 ? 2 : 1;
    enabledCrits(cfg).forEach(function (c) {
      if (!(Number(c.weight) > 0) || exclude[c.id]) return;
      var st = critStats(mine, c), dst = critStats(dept, c);
      if (st.n_applicable < minN || st.quality == null) return;
      var dq = dst.quality;
      if (dq != null && st.quality < dq) return;
      var best = mine.filter(function (d) { return verdictDegree(c, d.verdicts[c.id]) === 0; })[0] || null;
      out.push({
        criterion_id: c.id, title: c.title, type: c.type, weight: c.weight, quality: st.quality, dept_quality: dq,
        delta: dq != null ? rnd(st.quality - dq, 4) : null, n_applicable: st.n_applicable, violations: st.violations,
        example: best ? dialogExample(best, c.id) : null
      });
    });
    out.sort(function (a, b) { return (b.quality - a.quality) || ((b.delta || 0) - (a.delta || 0)) || (b.weight - a.weight) || cmp(a.criterion_id, b.criterion_id); });
    return out.slice(0, 3);
  }

  function dialogBrief(d) {
    return {
      uid: d.uid, group_id: d.group_id, external_id: d.external_id, language: d.language, channel: d.channel,
      evaluation_id: d.evaluation_id, status: d.status, total: d.total, gross: d.gross, gross_categories: d.gross_categories,
      needs_review: d.needs_review, n_appeals: d.n_appeals, n_open_appeals: d.n_open_appeals, contact_reason: d.contact_reason,
      manual: d.manual, evaluated_at: d.evaluated_at, verdicts: clone(d.verdicts)
    };
  }

  function byOperator(dialogs) {
    var out = {}, order = [];
    dialogs.forEach(function (d) {
      if (!d.operator) return;
      var k = nameKey(d.operator);
      if (!out[k]) { out[k] = []; order.push(k); }
      out[k].push(d);
    });
    return { map: out, keys: order };
  }
  function sum(list, f) { return list.reduce(function (a, d) { return a + (f(d) ? (f(d) === true ? 1 : Number(f(d))) : 0); }, 0); }
  function uniqSorted(list) { return list.filter(function (x, i) { return list.indexOf(x) === i; }).sort(cmp); }

  function operatorsList(group) {
    var dialogs = loadDialogs(group), by = byOperator(dialogs);
    var ops = by.keys.map(function (k) {
      var items = by.map[k];
      return {
        name: items[0].operator, n_dialogs: items.length, avg_total: avgTotal(items),
        gross_count: sum(items, function (d) { return d.gross; }), n_appeals: sum(items, function (d) { return d.n_appeals; }),
        n_open_appeals: sum(items, function (d) { return d.n_open_appeals; }), needs_review: sum(items, function (d) { return d.needs_review; }),
        languages: uniqSorted(items.map(function (d) { return d.language; })), channels: uniqSorted(items.map(function (d) { return d.channel; })),
        manual: sum(items, function (d) { return d.manual; })
      };
    });
    ops.sort(function (a, b) { return (b.n_dialogs - a.n_dialogs) || cmp(a.name.toLowerCase(), b.name.toLowerCase()); });
    return {
      group: group || null, n_dialogs: dialogs.length, avg_total: avgTotal(dialogs), n_operators: ops.length,
      unassigned: dialogs.filter(function (d) { return !d.operator; }).map(function (d) { return d.uid; }), operators: ops
    };
  }

  function operatorProfile(name, group) {
    var cfg = activeVersion().config;
    var dept = loadDialogs(group), by = byOperator(dept);
    var mine = by.map[nameKey(name)];
    if (!mine || !mine.length) throw err(404, 'не найдено: оператор \'' + name + '\' (нет оценённых диалогов)');
    var criteria = enabledCrits(cfg).map(function (c) {
      var st = critStats(mine, c), dst = critStats(dept, c);
      return { criterion_id: c.id, title: c.title, type: c.type, weight: c.weight, top: CQA.scoring.top(c), operator: st, department: dst,
        delta: st.quality != null && dst.quality != null ? rnd(st.quality - dst.quality, 4) : null };
    });
    var zones = growthZones(cfg, mine, dept);
    var excl = {};
    zones.forEach(function (z) { excl[z.criterion_id] = 1; });
    var gross = [];
    enabledCrits(cfg).forEach(function (c) {
      if (c.type !== 'flag') return;
      violationExamples(mine, c, mine.length).forEach(function (ex) {
        var d = mine.filter(function (x) { return x.uid === ex.uid; })[0], r = d.results[c.id];
        gross.push(Object.assign({}, ex, { criterion_id: c.id, title: c.title, category: r ? r.category || '' : '' }));
      });
    });
    var sorted = mine.slice().sort(function (a, b) { return ((a.total != null ? a.total : 101) - (b.total != null ? b.total : 101)) || cmp(a.external_id, b.external_id); });
    return {
      name: mine[0].operator, group: group || null, n_dialogs: mine.length, avg_total: avgTotal(mine),
      gross_count: sum(mine, function (d) { return d.gross; }), needs_review: sum(mine, function (d) { return d.needs_review; }),
      n_appeals: sum(mine, function (d) { return d.n_appeals; }), n_open_appeals: sum(mine, function (d) { return d.n_open_appeals; }),
      manual: sum(mine, function (d) { return d.manual; }),
      department: { n_dialogs: dept.length, avg_total: avgTotal(dept), n_operators: by.keys.length, gross_count: sum(dept, function (d) { return d.gross; }) },
      criteria: criteria, growth_zones: zones, strengths: strengthsOf(cfg, mine, dept, excl), gross_errors: gross,
      dialogs: sorted.map(dialogBrief)
    };
  }

  route('GET', /^\/operators$/, function (m, q) { return operatorsList(q.group || null); });
  route('GET', /^\/operators\/([^/]+)$/, function (m, q) { return operatorProfile(decodeURIComponent(m[1]), q.group || null); });
  route('POST', /^\/operators\/([^/]+)\/summary$/, function (m, q) {
    var name = decodeURIComponent(m[1]);
    var p = operatorProfile(name, q.group || null);
    var list = (db.p23 && db.p23.summaries) || [];
    var rec = list.filter(function (s) { return nameKey(s.name) === nameKey(name); })[0];
    if (!rec) {
      var names = list.map(function (s) { return s.name; });
      throw vErr(422, 'name', names.length ? 'В демо-режиме сводка ИИ записана только для оператора ' + names.join(', ') + '. Для остальных — подключите API.'
        : 'В демо-режиме сводка ИИ не записана — подключите API.');
    }
    return slow(2000, Object.assign(clone(rec), { name: p.name, group: q.group || null }));
  });
  route('PUT', /^\/conversations\/([^/]+)\/operator$/, function (m, q, body) {
    var uid = decodeURIComponent(m[1]);
    var s = summary(uid), conv = db.conversations[uid];
    if (!s && !conv) throw err(404, 'не найдено: диалог ' + uid);
    var name = normName(body && body.name);
    var prev = db.opOverrides[uid];
    var auto = prev ? prev.auto : normName(s ? s.operator_name : conv.operator_name);
    var current, manual;
    if (name == null || name === auto) { delete db.opOverrides[uid]; current = auto; manual = false; }
    else { db.opOverrides[uid] = { name: name, auto: auto, at: now() }; current = name; manual = true; }
    if (s) s.operator_name = current;
    if (conv) conv.operator_name = current;
    return { uid: uid, operator_name: current, auto_operator_name: auto, manual: manual };
  });

  // ---------------------------------------------------------------- P2: срезы (cqa/features/slices.py)
  var SLICE_BY = ['language', 'channel', 'operator', 'reason'];
  var STAT_KEYS = ['n', 'n_applicable', 'n_na', 'violations', 'quality', 'yes_share', 'n_yes', 'mean_step', 'top', 'avg_points', 'lost_points', 'loss'];
  function sliceKey(d, by) {
    if (by === 'language') return d.language;
    if (by === 'channel') return d.channel;
    if (by === 'operator') return d.operator || 'не назначен';
    return d.contact_reason || 'нет сигналов';
  }
  function sliceRow(key, dialogs, cfg) {
    var n = dialogs.length, gross = sum(dialogs, function (d) { return d.gross; });
    var crit = {};
    enabledCrits(cfg).forEach(function (c) {
      var st = critStats(dialogs, c), o = {};
      STAT_KEYS.forEach(function (k) { if (has(st, k)) o[k] = st[k]; });
      crit[c.id] = o;
    });
    return {
      key: key, n_dialogs: n, avg_total: avgTotal(dialogs), gross_count: gross, gross_share: n ? rnd(gross / n, 4) : null,
      needs_review: sum(dialogs, function (d) { return d.needs_review; }), n_appeals: sum(dialogs, function (d) { return d.n_appeals; }),
      uids: dialogs.map(function (d) { return d.uid; }), criteria: crit
    };
  }
  route('GET', /^\/slices$/, function (m, q) {
    var by = q.by || 'language';
    if (SLICE_BY.indexOf(by) < 0) throw vErr(422, 'by', 'ожидается одно из ' + SLICE_BY.join(', '));
    var cfg = activeVersion().config, dialogs = loadDialogs(q.group || null);
    var groups = {}, names = {}, order = [];
    dialogs.forEach(function (d) {
      var key = sliceKey(d, by), norm = by === 'operator' ? key.toLowerCase() : key;
      if (!groups[norm]) { groups[norm] = []; names[norm] = key; order.push(norm); }
      groups[norm].push(d);
    });
    var rows = order.map(function (k) { return sliceRow(names[k], groups[k], cfg); });
    rows.sort(function (a, b) { return (b.n_dialogs - a.n_dialogs) || cmp(a.key.toLowerCase(), b.key.toLowerCase()); });
    return { by: by, group: q.group || null, criteria: criteriaHeader(cfg), overall: sliceRow('все', dialogs, cfg), slices: rows };
  });

  // ---------------------------------------------------------------- P2: прецеденты (cqa/features/precedents.py)
  var ROLE_LABEL = { client: 'КЛИЕНТ', operator: 'ОПЕРАТОР', bot: 'БОТ', system: 'СИСТЕМА', unknown: 'НЕИЗВЕСТНО' };
  function squash(t) { return String(t || '').split(/\s+/).filter(Boolean).join(' '); }
  function clip(text, n) {
    text = squash(text);
    if (text.length <= n) return text;
    var cut = text.slice(0, Math.max(1, n - 1));
    var sp = cut.lastIndexOf(' ');
    if (sp > n * 0.6) cut = cut.slice(0, sp);
    return cut.replace(/[ ,.;:—-]+$/, '') + '…';
  }
  function shortFragment(text, limit) {
    limit = limit || 300;
    var parts = String(text || '').split(' / ').map(squash).filter(Boolean);
    var joined = parts.join(' / ');
    if (joined.length <= limit) return joined;
    var per = Math.max(80, Math.floor((limit - 3 * (parts.length - 1)) / parts.length));
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var q = clip(parts[i], per);
      if (out.concat([q]).join(' / ').length > limit) {
        if (!out.length) return clip(parts[i], limit);
        break;
      }
      out.push(q);
    }
    return out.join(' / ');
  }
  function exampleOf(p) { return { verdict: p.verdict, fragment: shortFragment(p.fragment_full), why: clip(p.why, 240) }; }

  /** Реплики прецедента: доказательства → контрдоказательства → начало проверенного участка (как appeals/service.py). */
  function addPrecedent(a, createdAt) {
    var ev = evalById(a.evaluation_id);
    if (!ev) return null;
    var cr = ev.criteria.filter(function (c) { return c.criterion_id === a.criterion_id; })[0];
    var conv = db.conversations[ev.uid];
    if (!cr || !conv) return null;
    var orig = a.original || cr;
    var evidence = (orig.evidence || []).map(function (e) { return e.id; }).filter(Boolean);
    var counter = a.counter_evidence || [];
    var mids = evidence.length ? evidence : counter;
    mids = mids.filter(function (x, i) { return mids.indexOf(x) === i; });
    var rng = orig.checked_range;
    if (!mids.length && rng && rng.length) {
      var order = conv.messages.map(function (x) { return x.mid; });
      var ends = rng.map(function (x) { return order.indexOf(x); }).filter(function (i) { return i >= 0; });
      if (ends.length) mids = order.slice(Math.min.apply(null, ends), Math.max.apply(null, ends) + 1).slice(0, 6);
    }
    var by = {};
    conv.messages.forEach(function (x) { by[x.mid] = x; });
    var fragment = mids.filter(function (x) { return by[x]; }).map(function (x) {
      return (ROLE_LABEL[by[x].speaker] || String(by[x].speaker).toUpperCase()) + ': ' + by[x].text;
    }).join(' / ');
    var p = { id: ++db.seq.precedent, appeal_id: a.id, criterion_id: a.criterion_id, fragment_full: fragment,
      verdict: cr.final_verdict || cr.verdict, why: a.decision ? a.decision.reason : '', added_in_version_id: null,
      created_at: createdAt || now() };
    db.precedents.push(p);
    return p;
  }

  function precedentView(p) {
    var act = activeVersion();
    var a = db.appeals.filter(function (x) { return x.id === p.appeal_id; })[0] || null;
    var ev = a ? evalById(a.evaluation_id) : null;
    var s = ev ? summary(ev.uid) : null;
    var conv = ev ? db.conversations[ev.uid] : null;
    var rec = p._rec || {};
    var crit = act.config.criteria.filter(function (c) { return c.id === p.criterion_id; })[0];
    var v = p.added_in_version_id ? versionById(p.added_in_version_id) : null;
    var base = s || conv || {};
    var out = {
      id: p.id, appeal_id: p.appeal_id, evaluation_id: ev ? ev.id : rec.evaluation_id || null, uid: ev ? ev.uid : rec.uid || null,
      external_id: base.external_id || rec.external_id || null, group_id: base.group_id || rec.group_id || null,
      language: base.language || rec.language || null, channel: base.channel || rec.channel || null,
      criterion_id: p.criterion_id, criterion_title: crit ? crit.title : p.criterion_id, verdict: p.verdict,
      original_verdict: a && a.original ? a.original.verdict : rec.original_verdict || null,
      action: a && a.decision ? a.decision.action : rec.action || null, reviewer: a && a.decision ? a.decision.reviewer : rec.reviewer || null,
      why: p.why, fragment_full: p.fragment_full, added_in_version_id: p.added_in_version_id,
      added_in_version: v ? v.version : null, added_in_version_status: v ? v.status : null, created_at: p.created_at, holdout_family: null
    };
    var ex = exampleOf(p);
    out.fragment = ex.fragment;
    out.in_active_version = !!(crit && (crit.examples || []).some(function (e) { return e.verdict === ex.verdict && e.fragment === ex.fragment; }));
    return out;
  }
  function precedentById(id) { return db.precedents.filter(function (p) { return p.id === Number(id); })[0] || null; }

  route('GET', /^\/precedents$/, function (m, q) {
    return db.precedents.filter(function (p) { return !q.criterion || p.criterion_id === q.criterion; })
      .slice().sort(function (a, b) { return b.id - a.id; }).map(precedentView);
  });

  /** Сравнение конфигов в формате cqa/checklist/store.diff. */
  function canon(v) {
    if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
    if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ':' + canon(v[k]); }).join(',') + '}';
    return JSON.stringify(v === undefined ? null : v);
  }
  function configDiff(a, b) {
    var out = [];
    Object.keys(b).forEach(function (k) {
      if (k === 'criteria' || canon(a[k]) === canon(b[k])) return;
      var bf = {}, af = {};
      bf[k] = a[k] === undefined ? null : a[k]; af[k] = b[k];
      out.push({ scope: 'config', id: k, change: 'changed', fields: [k], before: bf, after: af });
    });
    var ca = {}, cb = {};
    a.criteria.forEach(function (c) { ca[c.id] = c; });
    b.criteria.forEach(function (c) { cb[c.id] = c; });
    b.criteria.forEach(function (y) {
      var x = ca[y.id];
      if (!x) { out.push({ scope: 'criterion', id: y.id, change: 'added', fields: Object.keys(y), before: null, after: y }); return; }
      var fields = Object.keys(y).filter(function (k) { return canon(x[k]) !== canon(y[k]); });
      if (!fields.length) return;
      var before = {}, after = {};
      fields.forEach(function (k) { before[k] = x[k] === undefined ? null : x[k]; after[k] = y[k]; });
      out.push({ scope: 'criterion', id: y.id, change: 'changed', fields: fields, before: before, after: after });
    });
    a.criteria.forEach(function (x) { if (!cb[x.id]) out.push({ scope: 'criterion', id: x.id, change: 'removed', fields: Object.keys(x), before: x, after: null }); });
    return out;
  }

  route('POST', /^\/precedents\/to-version$/, function (m, q, body) {
    var ids = ((body && body.precedent_ids) || []).map(Number).filter(function (x, i, arr) { return !isNaN(x) && arr.indexOf(x) === i; });
    if (!ids.length) throw vErr(422, 'precedent_ids', 'нужен хотя бы один прецедент');
    var missing = ids.filter(function (i) { return !precedentById(i); });
    if (missing.length) throw err(404, 'нет прецедентов: ' + missing.join(', '));
    var act = activeVersion();
    var cfg = clone(act.config), crits = {};
    cfg.criteria.forEach(function (c) { crits[c.id] = c; });
    var added = [], skipped = [], byCrit = {}, critOrder = [];
    ids.forEach(function (i) {
      var p = precedentById(i), c = crits[p.criterion_id];
      if (!c) throw vErr(422, 'precedent_ids', 'прецедент ' + i + ': критерия ' + p.criterion_id + ' нет в активной версии');
      var allowed = CQA.scoring.allowedVerdicts(c);
      if (allowed.indexOf(p.verdict) < 0) throw vErr(422, 'precedent_ids', 'прецедент ' + i + ': вердикт \'' + p.verdict + '\' недопустим для критерия ' + p.criterion_id + ' (' + allowed.join(', ') + ')');
      if (!String(p.fragment_full || '').trim()) throw vErr(422, 'precedent_ids', 'прецедент ' + i + ': пустой фрагмент');
      var ex = exampleOf(p);
      c.examples = c.examples || [];
      if (c.examples.some(function (e) { return e.verdict === ex.verdict && e.fragment === ex.fragment; })) {
        skipped.push({ id: i, reason: 'пример уже есть в активной версии' });
        return;
      }
      c.examples.push(ex);
      added.push(i);
      if (!byCrit[p.criterion_id]) { byCrit[p.criterion_id] = []; critOrder.push(p.criterion_id); }
      byCrit[p.criterion_id].push(i);
    });
    if (!added.length) throw err(409, 'все выбранные прецеденты уже есть в активной версии');
    var auto = 'прецеденты: ' + critOrder.map(function (cid) { return cid + ' ← #' + byCrit[cid].join(', #'); }).join('; ');
    var comment = String((body && body.comment) || '').trim() || auto;
    var same = db.versions.filter(function (v) { return v.checklist_id === act.checklist_id; });
    var v = {
      id: ++db.seq.version, checklist_id: act.checklist_id, version: same.reduce(function (a, x) { return Math.max(a, x.version); }, 0) + 1,
      status: 'draft', config: cfg, comment: comment, created_at: now(), is_active: false
    };
    db.versions.push(v);
    added.forEach(function (i) { precedentById(i).added_in_version_id = v.id; });
    return { version_id: v.id, version: v.version, status: v.status, parent_id: act.id, parent_version: act.version, comment: comment,
      added: added, skipped: skipped, diff: configDiff(act.config, cfg), warnings: [] };
  });

  route('GET', /^\/precedents\/(\d+)\/similar$/, function (m, q) {
    var p = precedentById(m[1]);
    if (!p) throw err(404, 'прецедент ' + m[1] + ' не найден');
    var view = precedentView(p);
    var limit = Math.max(1, Math.min(100, Number(q.limit) || 10));
    var pf = db.p23 && db.p23.precedent_flow;
    if (p._recorded && pf && pf.similar) {
      var rs = clone(pf.similar);
      rs.precedent_id = p.id;
      rs.added_in_version_id = p.added_in_version_id;
      rs.items = rs.items.slice(0, limit);
      rs.uids = rs.items.map(function (x) { return x.uid; });
      return rs;
    }
    if (!view.uid) throw vErr(422, 'precedent_id', 'прецедент ' + p.id + ' не связан с диалогом');
    var src = summary(view.uid) || db.conversations[view.uid];
    var sig = db.p1 && db.p1.signals ? db.p1.signals[view.uid] : null;
    var reason = sig && sig.contact_reason ? sig.contact_reason : null;
    var verdict = view.original_verdict, cid = p.criterion_id;
    var items = [];
    loadDialogs(null).forEach(function (d) {
      if (d.uid === view.uid) return;
      var match = { reason: !!reason && d.contact_reason === reason, verdict: verdict != null && d.original[cid] === verdict,
        language: d.language === src.language, channel: d.channel === src.channel };
      if (!(match.reason || match.verdict)) return;
      items.push({ key: [!match.reason, !match.verdict, !match.language, !match.channel, d.group_id !== src.group_id, d.group_id, d.external_id],
        item: { uid: d.uid, external_id: d.external_id, group_id: d.group_id, language: d.language, channel: d.channel,
          contact_reason: d.contact_reason, verdict: has(d.original, cid) ? d.original[cid] : null,
          final_verdict: has(d.verdicts, cid) ? d.verdicts[cid] : null, total: d.total, match: match } });
    });
    items.sort(function (a, b) {
      for (var i = 0; i < a.key.length; i++) { var c = cmp(a.key[i], b.key[i]); if (c) return c; }
      return 0;
    });
    var picked = items.slice(0, limit).map(function (x) { return x.item; });
    return { precedent_id: p.id, criterion_id: cid, source_uid: view.uid, contact_reason: reason, original_verdict: verdict,
      language: src.language, channel: src.channel, added_in_version_id: p.added_in_version_id,
      uids: picked.map(function (x) { return x.uid; }), items: picked };
  });

  /** Пробный прогон на выбранных диалогах: записанный реальный (если это выборка записанного прецедента) или демо «стало = было». */
  function dryRunUids(v, body) {
    var uids = body.uids.filter(function (x, i, arr) { return arr.indexOf(x) === i; });
    var missing = uids.filter(function (u) { return !db.conversations[u]; });
    if (missing.length) throw vErr(422, 'uids', 'нет диалогов: ' + missing.slice(0, 10).join(', '));
    var act = activeVersion();
    var base = act.id !== v.id ? act : (db.versions.filter(function (x) { return x.checklist_id === v.checklist_id && x.version < v.version; }).sort(function (a, b) { return b.version - a.version; })[0] || null);
    var enabled = enabledCrits(v.config).map(function (c) { return c.id; });
    var criteria;
    if (body.criteria && body.criteria.length) {
      var bad = body.criteria.filter(function (c) { return enabled.indexOf(c) < 0; });
      if (bad.length) throw vErr(422, 'criteria', 'нет включённых критериев ' + bad.join(', ') + '; есть: ' + enabled.join(', '));
      criteria = body.criteria.filter(function (x, i, arr) { return arr.indexOf(x) === i; });
    } else {
      criteria = base && CQA.checklistChangedIds ? CQA.checklistChangedIds(base.config, v.config).filter(function (c) { return enabled.indexOf(c) >= 0; }) : [];
      if (!criteria.length) throw vErr(422, 'criteria', 'версия не отличается от активной по критериям — укажите criteria явно');
    }
    var pf = db.p23 && db.p23.precedent_flow;
    var recUids = pf && pf.diff ? pf.diff.dialogs.map(function (x) { return x.uid; }) : [];
    var recorded = recUids.length === uids.length && uids.every(function (u) { return recUids.indexOf(u) >= 0; });
    var tpl;
    if (recorded) {
      tpl = { criteria: pf.diff.criteria.slice(), dialogs: clone(pf.diff.dialogs) };
      criteria = tpl.criteria;
    } else {
      var titles = {};
      v.config.criteria.forEach(function (c) { titles[c.id] = c.title; });
      tpl = { criteria: criteria, dialogs: uids.map(function (uid) {
        var ev = db.evaluations[uid], s = summary(uid) || db.conversations[uid];
        var rows = criteria.map(function (cid) {
          var cr = ev ? ev.criteria.filter(function (c) { return c.criterion_id === cid; })[0] : null;
          var old = cr && cr.status === 'done' ? cr.verdict : null;
          return { criterion_id: cid, title: titles[cid] || cid, requested: true, old: old, new: old, status: cr ? 'done' : null, changed: false,
            old_explanation: cr ? cr.explanation || '' : '', new_explanation: cr ? cr.explanation || '' : '',
            new_evidence: cr ? (cr.evidence || []).map(function (e) { return e.id; }) : [] };
        });
        return { uid: uid, external_id: s.external_id, language: s.language, channel: s.channel, status: 'done', evaluation_id: null,
          baseline_evaluation_id: ev ? ev.id : null, baseline_checklist_version_id: ev ? ev.checklist_version_id : null,
          old_total: ev ? ev.total : null, new_total: ev ? ev.total : null, delta: ev && ev.total != null ? 0 : null,
          old_final_total: ev ? ev.final_total : null, old_gross: ev ? !!ev.gross : null, new_gross: ev ? !!ev.gross : null,
          changed_criteria: [], criteria: rows };
      }) };
    }
    createRun({ kind: 'dry_run', checklist_version_id: v.id, criteria: criteria, uids: uids, force: true });
    var r = db.runs[db.runs.length - 1];
    r._failAt = null; r._cached = 0; r._rate = 1.2;
    r._diff = { base: base ? base.id : null, tpl: tpl, demo: !recorded };
    return runView(r);
  }

  // ---------------------------------------------------------------- P3: тренажёр (записанная реальная сессия)
  function trainerRec() { return db.p23 && db.p23.trainer; }
  function secOf(ts) { var p = String(ts || '0:0').split(':').map(Number); return (p[0] || 0) * 60 + (p[1] || 0); }
  function tsOf(sec) { sec = Math.max(0, Math.round(sec)); var mm = Math.floor(sec / 60), ss = sec % 60; return (mm < 10 ? '0' : '') + mm + ':' + (ss < 10 ? '0' : '') + ss; }
  function firstOpSec() {
    var t = trainerRec();
    var first = t ? t.session.transcript.filter(function (x) { return x.source === 'trainee'; })[0] : null;
    return first ? secOf(first.ts) : null;
  }
  function appendMsg(s, speaker, text, source) {
    var tr = s.transcript, prev = tr.length ? secOf(tr[tr.length - 1].ts) : 0, t;
    if (speaker === 'operator') {
      var f = firstOpSec();
      t = f != null && !tr.some(function (x) { return x.speaker === 'operator'; }) ? Math.max(prev + 5, f) : prev + 30;
    } else t = prev + 20;
    var item = { mid: 'm' + (tr.length + 1), speaker: speaker, text: text, ts: tsOf(t), source: source };
    tr.push(item);
    return clone(item);
  }
  function sessionView(s) { return strip(s); }
  function sessionSummary(s) {
    var before = s.base_evaluation_id ? evalById(s.base_evaluation_id) : null;
    var after = s.result_evaluation_id ? evalById(s.result_evaluation_id) : null;
    return {
      id: s.id, base_uid: s.base_uid, status: s.status, created_at: s.created_at, title: (s.persona || {}).title || '',
      operator: s.operator, lesson_id: s.lesson_id, turns: s.turns, outcome: s.outcome, client_finished: !!s.client_finished,
      result_evaluation_id: s.result_evaluation_id, evaluation_status: after ? after.status || 'done' : null,
      before_total: before ? finalScore(before) : null, after_total: after ? finalScore(after) : null
    };
  }
  function sessionsList(baseUid, lessonId, status) {
    return Object.keys(db.sessions).map(function (k) { return db.sessions[k]; }).filter(function (s) {
      return (!baseUid || s.base_uid === baseUid) && (!status || s.status === status) && (lessonId == null || s.lesson_id === lessonId);
    }).sort(function (a, b) { return b.id - a.id; }).map(sessionSummary);
  }
  function sessionById(id) {
    var s = db.sessions[Number(id)];
    if (!s) throw err(404, 'не найдено: сессия тренажёра ' + id);
    return s;
  }

  route('GET', /^\/trainer\/sessions$/, function (m, q) {
    return sessionsList(q.base_uid || null, q.lesson_id != null && q.lesson_id !== '' ? Number(q.lesson_id) : null, q.status || null);
  });
  route('GET', /^\/trainer\/sessions\/(\d+)$/, function (m) { return sessionView(sessionById(m[1])); });
  route('POST', /^\/trainer\/sessions$/, function (m, q, body) {
    body = body || {};
    var uid = String(body.base_uid || '').trim();
    if (!db.conversations[uid]) throw err(404, 'не найдено: диалог ' + uid);
    if (uid.indexOf('TRAINER:') === 0) throw vErr(422, 'base_uid', 'это служебный диалог (тренажёр или автотест), выберите исходный диалог');
    var rec = trainerRec();
    if (!rec || rec.session.base_uid !== uid) {
      throw vErr(422, 'base_uid', 'В демо-режиме тренажёр записан для диалога ' + (rec ? fmtId(rec.session.base_uid) : 'p1-ru-007') + ' — откройте его карточку.');
    }
    var cfg = activeVersion().config, ids = cfg.criteria.map(function (c) { return c.id; });
    (body.criteria_focus || []).forEach(function (cid) {
      if (ids.indexOf(cid) < 0) throw vErr(422, 'criteria_focus', 'нет критерия \'' + cid + '\' в чек-листе');
    });
    var persona = clone(rec.persona_public);
    var s = {
      id: ++db.seq.session, base_uid: uid, base_external_id: rec.session.base_external_id, status: 'active', created_at: now(),
      operator: String(body.operator || '').trim() || null, lesson_id: body.lesson_id != null ? Number(body.lesson_id) : null,
      criteria_focus: (body.criteria_focus || []).slice(), persona: persona, transcript: clone(rec.prefix), prefix_len: rec.prefix.length,
      turns: 0, max_turns: rec.max_turns, client_finished: false, outcome: 'продолжает', uid: null,
      base_evaluation_id: rec.session.base_evaluation_id, result_evaluation_id: null, comparison: null, demo_note: DEMO_TRAINER_NOTE
    };
    db.sessions[s.id] = s;
    return slow(3000, sessionView(s));
  });
  route('POST', /^\/trainer\/sessions\/(\d+)\/reply$/, function (m, q, body) {
    var s = sessionById(m[1]);
    var text = String((body && body.text) || '').trim();
    if (!text) throw vErr(422, 'text', 'пустая реплика');
    if (s.status !== 'active') throw vErr(422, 'session', 'сессия уже завершена и оценена');
    var rec = trainerRec();
    if (s.client_finished) {
      var op0 = appendMsg(s, 'operator', text, 'trainee');
      return slow(300, { session_id: s.id, new_messages: [op0], finished: true, outcome: s.outcome, turn: s.turns, max_turns: s.max_turns, check: '' });
    }
    var turn = s.turns + 1, rt = rec && rec.turns[turn - 1];
    var out = [appendMsg(s, 'operator', text, 'trainee')], finished, outcome, check = '';
    if (rt) {
      out.push(appendMsg(s, 'client', rt.client, 'ai'));
      finished = !!rt.finished || turn >= s.max_turns;
      outcome = rt.finished ? rt.outcome : (finished ? 'лимит ходов' : 'продолжает');
      check = rt.check || '';
    } else {
      finished = true;
      outcome = rec ? rec.session.outcome || 'лимит ходов' : 'лимит ходов';
    }
    s.turns = turn; s.client_finished = finished; s.outcome = outcome;
    return slow(1500, { session_id: s.id, new_messages: out, finished: finished, outcome: outcome, turn: turn, max_turns: s.max_turns, check: check });
  });
  route('POST', /^\/trainer\/sessions\/(\d+)\/finish$/, function (m) {
    var s = sessionById(m[1]);
    if (s.status === 'finished') return sessionView(s);
    if (!s.transcript.some(function (x) { return x.source === 'trainee'; })) throw vErr(422, 'session', 'оператор ещё не ответил клиенту');
    var rec = trainerRec();
    s.status = 'finished';
    s.persona = Object.assign(clone(rec.persona_public), clone(rec.persona_hidden));
    s.uid = rec.result_uid;
    s.result_evaluation_id = rec.session.result_evaluation_id;
    s.comparison = clone(rec.session.comparison);
    return slow(4000, sessionView(s));
  });

  function lessonView(l) {
    var n = 0, done = 0;
    var items = l.base_uids.map(function (uid) {
      var c = summary(uid) || db.conversations[uid] || null, ev = db.evaluations[uid];
      var sessions = sessionsList(uid, null, null);
      n += sessions.length;
      done += sessions.filter(function (x) { return x.status === 'finished'; }).length;
      return { base_uid: uid, external_id: c ? c.external_id : null, language: c ? c.language : null, channel: c ? c.channel : null,
        base_total: ev ? finalScore(ev) : null, sessions: sessions };
    });
    return Object.assign(clone(l), { items: items, n_sessions: n, n_finished: done });
  }
  route('GET', /^\/trainer\/lessons$/, function () {
    return db.lessons.slice().sort(function (a, b) { return b.id - a.id; }).map(lessonView);
  });
  route('POST', /^\/trainer\/lessons$/, function (m, q, body) {
    body = body || {};
    var title = squash(body.title);
    var uids = (body.base_uids || []).map(function (u) { return String(u || '').trim(); }).filter(Boolean);
    uids = uids.filter(function (u, i) { return uids.indexOf(u) === i; });
    if (!title) throw vErr(422, 'title', 'нужно название урока');
    if (!uids.length) throw vErr(422, 'base_uids', 'нужен хотя бы один диалог');
    if (uids.length > 50) throw vErr(422, 'base_uids', 'не больше 50 диалогов');
    var missing = uids.filter(function (u) { return !db.conversations[u]; });
    if (missing.length) throw vErr(422, 'base_uids', 'нет диалогов: ' + missing.slice(0, 10).join(', '));
    var service = uids.filter(function (u) { return u.indexOf('TRAINER:') === 0; });
    if (service.length) throw vErr(422, 'base_uids', 'служебные диалоги: ' + service.slice(0, 10).join(', '));
    var l = { id: ++db.seq.lesson, title: title, base_uids: uids, created_at: now() };
    db.lessons.push(l);
    return lessonView(l);
  });

  // ---------------------------------------------------------------- P3: тьютор и база лучших ответов
  function normQ(q) { return squash(String(q || '').toLowerCase()).replace(/[?.!… ]+$/, '').trim(); }
  route('POST', /^\/tutor\/ask$/, function (m, q, body) {
    var question = squash(body && body.question);
    if (!question) throw vErr(422, 'body', 'пустой вопрос');
    var list = (db.p23 && db.p23.tutor) || [];
    var rec = list.filter(function (x) { return normQ(x.request.question) === normQ(question); })[0];
    if (!rec) {
      throw vErr(422, 'question', list.length
        ? 'В демо-режиме тьютор отвечает только на записанные вопросы: ' + list.map(function (x) { return '«' + x.request.question + '»'; }).join(', ') + '.'
        : 'В демо-режиме ответы тьютора не записаны — подключите API.');
    }
    var op = String((body && body.operator) || '').trim();
    var recOp = rec.response.operator || rec.request.operator || '';
    if (op && recOp && op.toLowerCase() !== String(recOp).toLowerCase()) {
      throw vErr(422, 'operator', 'В демо-режиме ответ тьютора записан для оператора ' + recOp + '.');
    }
    return slow(3000, clone(rec.response));
  });

  function stemsOf(text) {
    return (String(text || '').toLowerCase().replace(/ё/g, 'е').match(/[a-zа-яәғқңөұүһі0-9]+/g) || []);
  }
  route('GET', /^\/tutor\/best-answers$/, function (m, q) {
    var base = (db.p23 && db.p23.best_answers) || { topics: [], items: [] };
    var limit = Math.max(1, Math.min(500, Number(q.limit) || 100));
    var items = clone(base.items), topic = q.topic || null, text = q.q || null;
    var query = [text, topic].filter(Boolean).join(' ');
    var known = base.topics.some(function (t) { return topic && t.topic.toLowerCase() === topic.toLowerCase(); });
    if (known) {
      items = items.filter(function (p) { return p.topic.toLowerCase() === topic.toLowerCase(); });
      query = text || '';
    }
    var words = stemsOf(query).filter(function (w) { return w.length >= 4; }).map(function (w) { return w.slice(0, Math.max(4, w.length - 2)); });
    if (words.length) {
      items = items.map(function (p) {
        var qd = stemsOf(p.question + ' ' + p.client_text), ad = stemsOf(p.answer_text), td = stemsOf(p.topic);
        var hit = function (w, doc) { return doc.some(function (d) { return d.indexOf(w) === 0; }); };
        var hq = words.filter(function (w) { return hit(w, qd); }), ha = words.filter(function (w) { return hit(w, ad); });
        var score = 2 * hq.length + ha.filter(function (w) { return hq.indexOf(w) < 0; }).length + (words.some(function (w) { return hit(w, td); }) ? 1 : 0);
        if (!hq.length && !ha.length) return null;
        return Object.assign(p, { score: score, matched: uniqSorted(hq.concat(ha)) });
      }).filter(Boolean).sort(function (a, b) { return (b.score - a.score) || cmp(a.uid, b.uid); });
    }
    return { topic: topic, q: text, total: items.length, topics: clone(base.topics), items: items.slice(0, limit) };
  });

  // ---------------------------------------------------------------- P3: ассистент руководителя
  route('GET', /^\/assistant\/examples$/, function () { return clone((db.p23 && db.p23.assistant_examples) || []); });
  route('POST', /^\/assistant\/ask$/, function (m, q, body) {
    var question = squash(body && body.question);
    if (!question) throw vErr(422, 'question', 'пустой вопрос');
    var rec = ((db.p23 && db.p23.assistant) || []).filter(function (x) { return normQ(x.question) === normQ(question); })[0];
    if (!rec) throw vErr(422, 'question', 'В демо-режиме ассистент отвечает только на вопросы-примеры — подключите API, чтобы спросить своё.');
    return slow(6000, clone(rec));
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

  CQA.mock = { load: load, handle: handle, silentWav: silentWav, fast: false };
})(window.CQA = window.CQA || {});
