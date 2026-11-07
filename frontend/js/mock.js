/* Мок-бэкенд: ответы §8 из frontend/mock/*.json (в сборке — <script type="application/json" id="mock-…">).
   Запись (апелляции, решения, версии чек-листа, прогоны) живёт в памяти вкладки. */
(function (CQA) {
  'use strict';

  var FILES = ['health', 'datasets', 'conversations', 'conversation-details', 'evaluations', 'appeals', 'checklist-versions', 'runs', 'p1'];
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
        seq: { appeal: 100, version: 100, run: 100, compliance: 0 },
        p1: d.p1 || null,
        compliance: {},
        overrides: {}
      };
      enrichP1();
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
    return (db.p1 && db.p1.calls.by_evaluation[m[1]]) || [];
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
  route('POST', /^\/checklist-versions\/(\d+)\/dry-run$/, function (m) {
    var v = versionById(m[1]);
    if (!v) throw err(404, CQA.t('mock.notFound'));
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
    var tpl = db.p1 && db.p1.dry_run_diff;
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
    return {
      run: rv, checklist_version_id: r.checklist_version_id, checklist_version: v ? v.version : null,
      compared_with_version_id: b ? b.id : null, compared_with_version: b ? b.version : null, baseline: 'current',
      criteria: tpl.criteria.slice(), summary: diffSummary(dialogs, tpl.criteria), dialogs: dialogs
    };
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
