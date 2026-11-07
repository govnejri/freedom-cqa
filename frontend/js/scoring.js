/* Скоринг §3 спецификации — для мок-режима и предпросмотра нового итога в решении по апелляции.
   Источник истины — бэкенд (cqa/core/scoring.py); здесь та же формула: N/A → перераспределение весов,
   шкала = вес × ступень / верхняя ступень, у flag вес 0, итог округляется half-up до 0,01. */
(function (CQA) {
  'use strict';

  function top(crit) {
    if (!crit.scale || !crit.scale.length) return null;
    return Math.max.apply(null, crit.scale.map(function (s) { return Number(s.value); }));
  }

  function allowedVerdicts(crit) {
    var vals;
    if (crit.type === 'scale') {
      vals = (crit.scale || []).slice().sort(function (a, b) { return b.value - a.value; }).map(function (s) { return String(s.value); });
    } else {
      vals = ['Да', 'Нет'];
    }
    if (crit.na_allowed) vals.push('N/A');
    return vals;
  }

  /** Округление half-up до 2 знаков с защитой от двоичной погрешности (82.345 → 82.35). */
  function round2(x) {
    var s = Math.round((x + Number.EPSILON) * 1e6) / 1e6; // убираем хвосты вроде 0.30000000000000004
    return Math.sign(s) * Math.round(Math.abs(s) * 100 + 1e-9) / 100;
  }

  /**
   * score(cfg, verdicts, categories) → ScoreResult-подобный объект.
   * cfg — ChecklistConfig (JSON), verdicts — {criterion_id: verdict}, categories — {criterion_id: category}.
   */
  function score(cfg, verdicts, categories) {
    categories = categories || {};
    var crits = cfg.criteria.filter(function (c) { return c.enabled !== false && verdicts[c.id] != null; });
    var W = 0;
    crits.forEach(function (c) {
      if (c.type !== 'flag' && c.weight > 0 && verdicts[c.id] !== 'N/A') W += Number(c.weight);
    });
    var rows = crits.map(function (c) {
      var v = verdicts[c.id];
      var applicable = v !== 'N/A';
      var w = Number(c.weight) || 0;
      var raw = 0, eff = 0, pts = 0;
      if (c.type !== 'flag' && applicable && W > 0) {
        if (c.type === 'scale') raw = w * Number(v) / (top(c) || 1);
        else raw = v === 'Да' ? w : 0;
        eff = w * 100 / W;
        pts = raw * 100 / W;
      }
      return { criterion_id: c.id, verdict: v, applicable: applicable, weight: w, effective_weight: eff, raw_points: raw, points: pts, max_points: eff, _section: c.section };
    });
    var total = W > 0 ? round2(rows.reduce(function (s, r) { return s + r.points; }, 0)) : null;
    var sections = (cfg.sections || []).map(function (s) {
      var rs = rows.filter(function (r) { return r._section === s.id; });
      return {
        section_id: s.id, title: s.title,
        points: round2(rs.reduce(function (a, r) { return a + r.points; }, 0)),
        max_points: round2(rs.reduce(function (a, r) { return a + r.max_points; }, 0))
      };
    });
    var flags = crits.filter(function (c) { return c.type === 'flag' && verdicts[c.id] === 'Да'; });
    var cats = [];
    flags.forEach(function (c) { var k = categories[c.id]; if (k && cats.indexOf(k) < 0) cats.push(k); });
    rows.forEach(function (r) {
      delete r._section;
      ['effective_weight', 'raw_points', 'points', 'max_points'].forEach(function (k) { r[k] = Math.round(r[k] * 1e4) / 1e4; });
    });
    return { total: total, criteria: rows, sections: sections, gross: flags.length > 0, gross_categories: cats.sort() };
  }

  /** Клиентская проверка конфига (зеркало pydantic-валидаторов cqa/checklist/model.py). → [{loc, msg}] */
  function validateConfig(cfg) {
    var t = CQA.t;
    var errs = [];
    var push = function (loc, key, p) { errs.push({ loc: loc, msg: t(key, p) }); };
    if (!cfg.title || !String(cfg.title).trim()) push('title', 'val.required');
    var secIds = {};
    (cfg.sections || []).forEach(function (s, i) {
      if (!s.id || !String(s.id).trim()) push('sections.' + i + '.id', 'val.required');
      else if (secIds[s.id]) push('sections.' + i + '.id', 'val.duplicateId');
      secIds[s.id] = true;
      if (!s.title || !String(s.title).trim()) push('sections.' + i + '.title', 'val.required');
    });
    var ids = {};
    var anyWeighted = false;
    (cfg.criteria || []).forEach(function (c, i) {
      var L = 'criteria.' + i + '.';
      if (!c.id || !String(c.id).trim()) push(L + 'id', 'val.required');
      else if (ids[c.id]) push(L + 'id', 'val.duplicateId');
      ids[c.id] = true;
      if (!c.title || !String(c.title).trim()) push(L + 'title', 'val.required');
      if (!secIds[c.section]) push(L + 'section', 'val.noSection');
      if (!c.definition || !String(c.definition).trim()) push(L + 'definition', 'val.required');
      var w = Number(c.weight);
      if (isNaN(w) || w < 0) push(L + 'weight', 'val.weightNonNeg');
      if (c.type === 'flag' && w !== 0) push(L + 'weight', 'val.flagWeight');
      if (c.type === 'scale') {
        var steps = c.scale || [];
        if (!steps.length) push(L + 'scale', 'val.scaleRequired');
        var vals = steps.map(function (s) { return Number(s.value); });
        steps.forEach(function (s, j) {
          if (s.value === '' || s.value == null || isNaN(Number(s.value)) || Number(s.value) !== Math.floor(Number(s.value))) push(L + 'scale.' + j + '.value', 'val.integer');
          if (!s.text || !String(s.text).trim()) push(L + 'scale.' + j + '.text', 'val.required');
        });
        var uniq = vals.filter(function (v, k) { return vals.indexOf(v) === k; });
        if (steps.length && uniq.length !== vals.length) push(L + 'scale', 'val.scaleUnique');
        if (steps.length && Math.max.apply(null, vals) <= 0) push(L + 'scale', 'val.scaleMax');
      }
      if (c.type === 'flag' && c.flag_categories && !c.flag_categories.length) push(L + 'flag_categories', 'val.flagCategories');
      var allowed = allowedVerdicts(c);
      (c.examples || []).forEach(function (ex, j) {
        if (allowed.indexOf(ex.verdict) < 0) push(L + 'examples.' + j + '.verdict', 'val.exampleVerdict', { v: ex.verdict });
        if (!ex.fragment || !String(ex.fragment).trim()) push(L + 'examples.' + j + '.fragment', 'val.required');
      });
      if (!c.evidence_roles || !c.evidence_roles.length) push(L + 'evidence_roles', 'val.rolesRequired');
      if (c.enabled !== false && w > 0) anyWeighted = true;
    });
    if (!anyWeighted) push('criteria', 'val.noWeighted');
    return errs;
  }

  CQA.scoring = { score: score, top: top, allowedVerdicts: allowedVerdicts, round2: round2, validateConfig: validateConfig };
})(window.CQA = window.CQA || {});
