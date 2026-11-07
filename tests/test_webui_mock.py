"""Мок-данные фронтенда соответствуют DTO (cqa/api/schemas.py), итоги — правилам скоринга, i18n полон."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import pytest

from cqa.api.schemas import (
    AppealDTO,
    ChecklistVersionDTO,
    ConversationDTO,
    ConversationSummaryDTO,
    EvaluationDTO,
    HealthDTO,
    RunDTO,
)
from cqa.checklist.model import ChecklistConfig
from cqa.webui.build import FRONTEND

MOCK = FRONTEND / "mock"


def load(name):
    return json.loads((MOCK / f"{name}.json").read_text(encoding="utf-8"))


def test_mock_matches_dto():
    HealthDTO.model_validate(load("health"))
    for s in load("conversations"):
        ConversationSummaryDTO.model_validate(s)
    for uid, c in load("conversation-details").items():
        assert ConversationDTO.model_validate(c).uid == uid
    for uid, e in load("evaluations").items():
        assert EvaluationDTO.model_validate(e).uid == uid
    for a in load("appeals"):
        AppealDTO.model_validate(a)
    for v in load("checklist-versions"):
        ChecklistVersionDTO.model_validate(v)
        ChecklistConfig.model_validate(v["config"])
    for r in load("runs"):
        RunDTO.model_validate(r)


def test_mock_has_no_audio_links():
    for p in MOCK.glob("*.json"):
        text = p.read_text(encoding="utf-8")
        assert "sig=" not in text, p.name
        assert not re.search(r"https?://", text), p.name
    for c in load("conversation-details").values():
        assert c["audio_url"] is None


def test_mock_evidence_quotes_exist():
    convs = load("conversation-details")
    for uid, e in load("evaluations").items():
        by = {m["mid"]: m["text"] for m in convs[uid]["messages"]}
        for cr in e["criteria"]:
            for ev in cr["evidence"]:
                assert ev["quote"] in by[ev["id"]], (uid, cr["criterion_id"], ev)


def _q2(x: Fraction) -> float:
    return float((Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _total(cfg: ChecklistConfig, verdicts: dict[str, str]) -> float | None:
    """Независимая реализация §3 для проверки мок-итогов."""
    w = sum((Fraction(str(c.weight)) for c in cfg.enabled_criteria()
             if c.type != "flag" and c.weight > 0 and verdicts[c.id] != "N/A"), Fraction(0))
    if not w:
        return None
    pts = Fraction(0)
    for c in cfg.enabled_criteria():
        v = verdicts[c.id]
        if c.type == "flag" or v == "N/A":
            continue
        raw = Fraction(str(c.weight)) * int(v) / c.top if c.type == "scale" else (Fraction(str(c.weight)) if v == "Да" else 0)
        pts += raw * 100 / w
    return _q2(pts)


def test_mock_totals_follow_scoring_rules():
    versions = {v["id"]: ChecklistConfig.model_validate(v["config"]) for v in load("checklist-versions")}
    for uid, e in load("evaluations").items():
        cfg = versions[e["checklist_version_id"]]
        orig = {c["criterion_id"]: c["verdict"] for c in e["criteria"]}
        final = {c["criterion_id"]: c["final_verdict"] for c in e["criteria"]}
        assert _total(cfg, orig) == e["total"], uid
        assert _total(cfg, final) == e["final_total"], uid
        assert sum(c["score"]["effective_weight"] for c in e["criteria"]) == pytest.approx(100, abs=0.01)


@pytest.mark.skipif(shutil.which("node") is None, reason="нет node")
def test_js_scoring_matches_mock():
    """frontend/js/scoring.js (мок-пересчёт и предпросмотр решения) считает так же, как бэкенд-правила."""
    harness = """
    global.window = {}; window.CQA = {};
    const fs = require('fs');
    eval(fs.readFileSync(process.argv[1], 'utf8'));
    const versions = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const evals = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
    const out = {};
    for (const [uid, e] of Object.entries(evals)) {
      const cfg = versions.find(v => v.id === e.checklist_version_id).config;
      const o = {}, f = {}, cats = {};
      e.criteria.forEach(c => { o[c.criterion_id] = c.verdict; f[c.criterion_id] = c.final_verdict; cats[c.criterion_id] = c.category; });
      const ro = window.CQA.scoring.score(cfg, o, cats), rf = window.CQA.scoring.score(cfg, f, cats);
      out[uid] = [ro.total, rf.total, ro.gross, ro.gross_categories];
    }
    console.log(JSON.stringify(out));
    """
    r = subprocess.run(["node", "-e", harness, str(FRONTEND / "js" / "scoring.js"),
                        str(MOCK / "checklist-versions.json"), str(MOCK / "evaluations.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    for uid, e in load("evaluations").items():
        total, final, gross, cats = res[uid]
        assert total == e["total"] and final == e["final_total"], uid
        assert gross == e["gross"] and cats == e["gross_categories"], uid


def test_i18n_keys_present():
    src = (FRONTEND / "js" / "i18n.js").read_text(encoding="utf-8")
    keys = set(re.findall(r"^\s*'([\w.]+)':", src, re.M))
    used = set()
    for p in list((FRONTEND / "js").glob("*.js")) + list((FRONTEND / "components").glob("*.js")):
        if p.name == "i18n.js":
            continue
        text = p.read_text(encoding="utf-8")
        used |= set(re.findall(r"\bt\('([\w.]+)'", text))
        used |= set(re.findall(r"\bCQA\.t\('([\w.]+)'", text))
        used |= set(re.findall(r"push\([^,]+,\s*'(val\.[\w.]+)'", text))
    missing = sorted(k for k in used if k not in keys and not k.endswith("."))
    assert not missing, missing


# ---------------------------------------------------------------- P1: инсайты, качество, журнал, второе мнение, «было/стало»

def test_p1_mock_static_formats():
    """frontend/mock/p1.json — реальные ответы роутов P1 в их форматах (генератор frontend/mock/generate_p1.py)."""
    from cqa.api.schemas import LLMCallDTO

    p1 = load("p1")
    s = p1["settings"]
    assert set(s) >= {"roles", "defaults", "overrides", "fake", "profiles"}
    assert any(p["name"] == "local" and p["local"] for p in s["profiles"])
    q = p1["quality"]
    assert q["default"] in q["reports"]
    for r in q["reports"].values():
        assert set(r) >= {"run", "split", "ceiling", "criteria", "overall", "total", "traps", "by", "cost"}
        for c in r["criteria"]:
            assert {"criterion", "exact_rate", "compatible_rate", "kappa", "ceiling_exact_rate", "ceiling_kappa"} <= set(c)
    for r in p1["quality_runs"]:
        assert {"id", "model", "local", "n", "exact_rate", "kappa_mean", "mae", "cost_per_dialog_usd"} <= set(r)
    a = p1["autotests"]
    assert {"total", "by_mutation", "by_language", "injection", "failed"} <= set(a)
    RunDTO.model_validate(p1["dry_run_diff"]["run"])
    assert p1["dry_run_diff"]["summary"]["verdicts_compared"] >= 1
    calls = p1["calls"]
    evals = {str(e["id"]) for e in load("evaluations").values()}
    assert set(calls["by_evaluation"]) <= evals
    for lst in calls["by_evaluation"].values():
        for c in lst:
            LLMCallDTO.model_validate(c)
            assert str(c["id"]) in calls["details"]
    for d in calls["details"].values():
        assert d["response_text"] is None or 0 <= d["response_text"] < len(calls["strings"])
    for op in p1["appeal_opinions"].values():
        assert op["appeal_supported"] in ("да", "частично", "нет") and op["blind"] is True


_P1_HARNESS = r"""
const fs = require('fs'), path = require('path');
const FE = process.argv[1];
global.window = global; window.CQA = {};
global.HTMLElement = class {}; global.customElements = { define() {} };
global.document = { getElementById: (id) => ({ textContent: fs.readFileSync(path.join(FE, 'mock', id.replace(/^mock-/, '') + '.json'), 'utf8') }) };
for (const f of ['js/i18n.js', 'js/util.js', 'js/scoring.js', 'js/mock.js', 'js/api.js', 'components/checklist-diff.js'])
  eval(fs.readFileSync(path.join(FE, f), 'utf8'));
const M = window.CQA.mock;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const out = {};
  const call = (m, p, o) => M.handle(m, p, o || {});
  out.risks = await call('GET', '/insights/risks', { query: { group: 'DEV' } });
  const crid = out.risks.items[0].criterion_result_id;
  out.case1 = await call('POST', '/insights/risks/' + crid + '/compliance', { body: {} });
  out.case2 = await call('POST', '/insights/risks/' + crid + '/compliance', { body: {} });
  out.risksAfter = await call('GET', '/insights/risks', { query: {} });
  out.unanswered = await call('GET', '/insights/unanswered', { query: { group: 'DEV' } });
  out.reasons = await call('GET', '/insights/reasons', { query: {} });
  out.summary = await call('GET', '/insights/summary', { query: { group: 'DEV' } });
  out.signals = await call('POST', '/insights/signals', { body: { group: 'DEV' } });
  out.quality = await call('GET', '/quality', { query: {} });
  out.qualitySlice = await call('GET', '/quality', { query: { split: 'holdout', by: 'channel' } });
  out.settings = await call('PUT', '/settings', { body: { roles: { judge: 'gemini-flash-37' } } });
  out.settingsReset = await call('PUT', '/settings', { body: { roles: { judge: null } } });
  const ev = await call('GET', '/evaluations/102');
  out.evaluation = ev;
  out.calls = await call('GET', '/evaluations/102/calls');
  out.call = await call('GET', '/llm-calls/' + out.calls[0].id);
  out.appeal = await call('POST', '/appeals/2/second-opinion');
  const run = await call('POST', '/checklist-versions/1/dry-run', { body: { sample: 10 } });
  out.run = run;
  out.diff0 = await call('GET', '/runs/' + run.id + '/diff');
  await sleep(10500);
  out.diff1 = await call('GET', '/runs/' + run.id + '/diff');
  const active = (await call('GET', '/checklists/freedom-cqa/versions')).find((v) => v.is_active);
  const same = await call('POST', '/checklists/freedom-cqa/versions', { body: { config: active.config, comment: 'копия' } });
  try { await call('POST', '/checklist-versions/' + same.id + '/dry-run', { body: {} }); out.sameErr = null; } catch (e) { out.sameErr = e.status; }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="нет node")
def test_p1_mock_routes_follow_dtos():
    """Мок-маршруты P1 (frontend/js/mock.js) отвечают в форматах DTO бэкенда."""
    from cqa.api.routes.insights import (
        ComplianceCaseDTO,
        ReasonsDTO,
        RisksDTO,
        SignalsRunDTO,
        SummaryDTO,
        UnansweredDTO,
    )
    from cqa.api.schemas import LLMCallDTO

    r = subprocess.run(["node", "-e", _P1_HARNESS, str(FRONTEND)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    risks = RisksDTO.model_validate(out["risks"])
    assert risks.total >= 1 and all(it.verdict == "Да" and it.evidence for it in risks.items)
    assert sum(c.count for c in risks.by_category) == risks.total
    c1, c2 = ComplianceCaseDTO.model_validate(out["case1"]), ComplianceCaseDTO.model_validate(out["case2"])
    assert c1.created and not c2.created and c1.id == c2.id
    assert any(it.compliance for it in RisksDTO.model_validate(out["risksAfter"]).items)
    un = UnansweredDTO.model_validate(out["unanswered"])
    assert un.total_questions == sum(t.n_questions for t in un.topics)
    reasons = ReasonsDTO.model_validate(out["reasons"])
    assert abs(sum(x.share for x in reasons.reasons) - 1) < 0.01
    s = SummaryDTO.model_validate(out["summary"])
    assert s.risks_open_compliance == 1 and s.gross_count == risks.total
    SignalsRunDTO.model_validate(out["signals"])
    assert out["quality"]["run"] and out["quality"]["criteria"]
    assert out["qualitySlice"]["warnings"]                     # среза нет в моке — честное предупреждение
    assert out["settings"]["roles"]["judge"] == "gemini-flash-37" and out["settings"]["overrides"] == {"judge": "gemini-flash-37"}
    assert out["settingsReset"]["overrides"] == {}
    ev = EvaluationDTO.model_validate(out["evaluation"])
    assert all(c.allowed_verdicts for c in ev.criteria)
    assert all(c.scale_top for c in ev.criteria if c.type == "scale")
    assert any(c.second_opinion for c in ev.criteria) and ev.n_llm_calls == len(out["calls"])
    for c in out["calls"]:
        LLMCallDTO.model_validate(c)
    call = LLMCallDTO.model_validate(out["call"])
    assert call.request and call.request["messages"] and call.response_text
    appeal = AppealDTO.model_validate(out["appeal"])
    assert appeal.ai_opinion and appeal.ai_opinion["appeal_supported"] in ("да", "частично", "нет")
    run = RunDTO.model_validate(out["run"])
    assert run.kind == "dry_run"
    assert out["diff0"]["summary"]["completed"] < out["diff1"]["summary"]["completed"] == out["diff1"]["summary"]["dialogs"]
    assert out["diff1"]["run"]["status"] == "done"
    assert out["sameErr"] == 422                               # версия не отличается от активной — как в API
