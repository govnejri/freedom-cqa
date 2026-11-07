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


# ---------------------------------------------------------------- P2/P3: операторы, срезы, прецеденты, тренажёр, тьютор, ассистент

def _p23():
    return load("p23")


def test_p23_mock_static_formats():
    """frontend/mock/p23.json — реальные диалоги-дополнения и записанные реальные ответы (frontend/mock/generate_p23.py)."""
    from cqa.api.routes.assistant import AssistantAnswerDTO, ExampleDTO
    from cqa.features.assistant import EXAMPLES
    from cqa.features.trainer import HIDDEN_FIELDS

    p = _p23()
    mock_evals = load("evaluations")
    eval_ids = {e["id"] for e in mock_evals.values()}
    cr_ids = {c["id"] for e in mock_evals.values() for c in e["criteria"]}
    mock_uids = {s["uid"] for s in load("conversations")}
    versions = {v["id"] for v in load("checklist-versions")}
    for x in p["extras"] + p["trainer_cards"]:
        assert x["uid"] not in mock_uids
        c = ConversationDTO.model_validate(x["conversation"])
        e = EvaluationDTO.model_validate(x["evaluation"])
        assert c.uid == e.uid == x["uid"] and c.audio_url is None and not c.has_audio
        assert e.id not in eval_ids and e.checklist_version_id in versions
        eval_ids.add(e.id)
        for cr in e.criteria:
            assert cr.id not in cr_ids
            cr_ids.add(cr.id)
        by = {m["mid"]: m["text"] for m in x["conversation"]["messages"]}
        for cr in e.criteria:
            for ev in cr.evidence:
                if ev.ok is not False:
                    assert ev.id in by, (x["uid"], cr.criterion_id, ev.id)
        if x["in_lists"]:
            s = ConversationSummaryDTO.model_validate(x["summary"])
            assert s.uid == x["uid"] and s.evaluation_id == e.id
        else:
            assert x["summary"] is None and x["uid"].startswith("TRAINER:")

    tr = p["trainer"]
    assert tr and tr["turns"] and tr["prefix"]
    assert all(m["source"] == "base" for m in tr["prefix"])
    assert not set(HIDDEN_FIELDS) & set(tr["persona_public"])
    assert set(HIDDEN_FIELDS) <= set(tr["persona_hidden"])
    s = tr["session"]
    assert s["status"] == "finished" and s["comparison"]["after"]["evaluation_id"] == s["result_evaluation_id"]
    assert s["uid"] == tr["result_uid"] and any(x["uid"] == tr["result_uid"] for x in p["trainer_cards"])
    assert s["comparison"]["before"]["evaluation_id"] == s["base_evaluation_id"]
    ids = {x["evaluation"]["id"] for x in p["extras"] + p["trainer_cards"]}
    assert {s["base_evaluation_id"], s["result_evaluation_id"]} <= ids
    for les in p["lessons"]:
        assert les["title"] and les["base_uids"]

    assert [ExampleDTO.model_validate(e).question for e in p["assistant_examples"]] == [e["question"] for e in EXAMPLES]
    for a in p["assistant"]:
        AssistantAnswerDTO.model_validate(a)
    for t in p["tutor"]:
        assert t["request"]["question"]
        r = t["response"]
        assert {"answer", "found", "sources", "operator", "uid", "llm_call_id", "cost_usd"} <= set(r)
        for src in r["sources"]:
            assert src["kind"] in ("own", "checklist", "best_answer") and src["ref"].startswith("S")
    b = p["best_answers"]
    assert b["total"] == len(b["items"]) and sum(x["n"] for x in b["topics"]) == b["total"]
    for it in b["items"][:5]:
        assert {"uid", "external_id", "topic", "question", "question_mid", "client_text", "answer_mid", "answer_text"} <= set(it)
    for sm in p["summaries"]:
        assert sm["name"] and sm["summary"]


_P23_HARNESS = r"""
const fs = require('fs'), path = require('path');
const FE = process.argv[1];
global.window = global; window.CQA = {};
global.HTMLElement = class {}; global.customElements = { define() {} };
// записанный прецедент подмешиваем в p23: проверяем, что мок проигрывает реальное «было/стало» именно на его выборке
function p23() {
  const d = JSON.parse(fs.readFileSync(path.join(FE, 'mock', 'p23.json'), 'utf8'));
  d.precedent_flow = {
    precedent: { id: 77, appeal_id: null, evaluation_id: 102, uid: 'DEV:p1-ru-022', external_id: 'p1-ru-022', group_id: 'DEV',
      language: 'RU', channel: 'agent chat', criterion_id: '07', criterion_title: 'Завершение', verdict: 'Нет', original_verdict: 'Да',
      action: 'change', reviewer: 'reviewer', why: 'Прощание при открытом вопросе — «Нет».', fragment_full: 'ОПЕРАТОР: Всего доброго!',
      added_in_version_id: null, created_at: '2026-11-08T10:00:00+00:00' },
    similar: { precedent_id: 77, criterion_id: '07', source_uid: 'DEV:p1-ru-022', contact_reason: 'облигации', original_verdict: 'Да',
      language: 'RU', channel: 'agent chat', added_in_version_id: null, uids: ['DEV:p1-kz-015', 'DEV:p1-ru-025'],
      items: ['DEV:p1-kz-015', 'DEV:p1-ru-025'].map((u) => ({ uid: u, external_id: u.slice(4), group_id: 'DEV', language: 'RU', channel: 'agent chat',
        contact_reason: null, verdict: 'Да', final_verdict: 'Да', total: 50, match: { reason: false, verdict: true, language: true, channel: true } })) },
    appeal: null,
    diff: { criteria: ['07'], dialogs: ['DEV:p1-kz-015', 'DEV:p1-ru-025'].map((u, i) => ({ uid: u, external_id: u.slice(4), language: 'RU',
      channel: 'agent chat', status: 'done', evaluation_id: null, baseline_evaluation_id: 103 + i, baseline_checklist_version_id: 1,
      old_total: 50, new_total: 40, delta: -10, old_final_total: 50, old_gross: false, new_gross: false, changed_criteria: ['07'],
      criteria: [{ criterion_id: '07', title: 'Завершение', requested: true, old: 'Да', new: 'Нет', status: 'done', changed: true,
        old_explanation: 'было', new_explanation: 'стало', new_evidence: ['m3'] }] })) }
  };
  return JSON.stringify(d);
}
global.document = { getElementById: (id) => ({ textContent: id === 'mock-p23' ? p23() : fs.readFileSync(path.join(FE, 'mock', id.replace(/^mock-/, '') + '.json'), 'utf8') }) };
for (const f of ['js/i18n.js', 'js/util.js', 'js/scoring.js', 'js/mock.js', 'js/api.js', 'components/checklist-diff.js'])
  eval(fs.readFileSync(path.join(FE, f), 'utf8'));
const M = window.CQA.mock;
M.fast = true;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (p) => p.then(() => null, (e) => ({ status: e.status, message: e.message, body: e.body }));
(async () => {
  const out = {};
  const call = (m, p, o) => M.handle(m, p, o || {});
  out.operators = await call('GET', '/operators', { query: {} });
  out.profile = await call('GET', '/operators/' + encodeURIComponent('кирилл'), { query: {} });
  out.profileErr = await fail(call('GET', '/operators/' + encodeURIComponent('Никто'), { query: {} }));
  out.summaryOk = await call('POST', '/operators/' + encodeURIComponent('Кирилл') + '/summary', { query: {} });
  out.summaryErr = await fail(call('POST', '/operators/' + encodeURIComponent('Алина') + '/summary', { query: {} }));
  out.slices = {};
  for (const by of ['language', 'channel', 'operator', 'reason']) out.slices[by] = await call('GET', '/slices', { query: { by } });
  out.sliceErr = await fail(call('GET', '/slices', { query: { by: 'x' } }));
  out.assign = await call('PUT', '/conversations/' + encodeURIComponent('DEV:p4-ru-002') + '/operator', { body: { name: '  Айбек ' } });
  out.opsAfter = await call('GET', '/operators', { query: {} });
  out.unassign = await call('PUT', '/conversations/' + encodeURIComponent('DEV:p4-ru-002') + '/operator', { body: { name: '' } });
  // апелляция с прецедентом → версия → похожие → пробный прогон (демо: «стало» = «было»)
  out.decided = await call('POST', '/appeals/2/decision', { body: { action: 'change', new_verdict: 'Нет', reason: 'Приветствия не было.', make_precedent: true } });
  out.precedents = await call('GET', '/precedents', { query: {} });
  const mine = out.precedents.find((p) => p.appeal_id === 2);
  out.toVersion = await call('POST', '/precedents/to-version', { body: { precedent_ids: [mine.id], comment: '' } });
  out.precedentsAfter = await call('GET', '/precedents', { query: { criterion: mine.criterion_id } });
  out.similar = await call('GET', '/precedents/' + mine.id + '/similar', { query: { limit: 3 } });
  const run = await call('POST', '/checklist-versions/' + out.toVersion.version_id + '/dry-run', { body: { uids: out.similar.uids } });
  out.run = run;
  await sleep(4500);
  out.diff = await call('GET', '/runs/' + run.id + '/diff');
  // записанный прецедент: реальное «было/стало»
  const rec = out.precedents.find((p) => p.uid === 'DEV:p1-ru-022' && p.appeal_id === null);
  out.recTo = await call('POST', '/precedents/to-version', { body: { precedent_ids: [rec.id] } });
  out.recSimilar = await call('GET', '/precedents/' + rec.id + '/similar', { query: {} });
  const run2 = await call('POST', '/checklist-versions/' + out.recTo.version_id + '/dry-run', { body: { uids: out.recSimilar.uids } });
  await sleep(3000);
  out.recDiff = await call('GET', '/runs/' + run2.id + '/diff');
  // тренажёр
  out.sessErr = await fail(call('POST', '/trainer/sessions', { body: { base_uid: 'DEV:p1-ru-022' } }));
  out.sess = await call('POST', '/trainer/sessions', { body: { base_uid: 'DEV:p1-ru-007', criteria_focus: ['04'], operator: 'Стажёр' } });
  out.reply1 = await call('POST', '/trainer/sessions/' + out.sess.id + '/reply', { body: { text: 'Здравствуйте! Подскажу путь.' } });
  out.reply2 = await call('POST', '/trainer/sessions/' + out.sess.id + '/reply', { body: { text: 'Дождитесь исполнения поручения, затем повторите вывод.' } });
  out.reply3 = await call('POST', '/trainer/sessions/' + out.sess.id + '/reply', { body: { text: 'Хорошего дня!' } });
  out.finish = await call('POST', '/trainer/sessions/' + out.sess.id + '/finish');
  out.finishCard = await call('GET', '/conversations/' + encodeURIComponent(out.finish.uid) + '/evaluation');
  out.sessions = await call('GET', '/trainer/sessions', { query: {} });
  out.lesson = await call('POST', '/trainer/lessons', { body: { title: 'Вопросы без ответа: облигации', base_uids: ['DEV:p1-ru-022', 'DEV:p1-ru-007'] } });
  out.lessonErr = await fail(call('POST', '/trainer/lessons', { body: { title: 'x', base_uids: ['DEV:nope'] } }));
  out.lessons = await call('GET', '/trainer/lessons');
  // тьютор и ассистент
  const tq = JSON.parse(p23()).tutor;
  out.tutorRec = tq;
  out.tutor = tq.length ? await call('POST', '/tutor/ask', { body: { question: tq[0].request.question.toUpperCase() + ' ', operator: tq[0].request.operator } }) : null;
  out.tutorErr = await fail(call('POST', '/tutor/ask', { body: { question: 'Совсем другой вопрос?' } }));
  out.best = await call('GET', '/tutor/best-answers', { query: {} });
  out.bestTopic = await call('GET', '/tutor/best-answers', { query: { topic: out.best.topics[0].topic } });
  out.bestQ = await call('GET', '/tutor/best-answers', { query: { q: 'вывод средств' } });
  out.examples = await call('GET', '/assistant/examples');
  out.assistant = await call('POST', '/assistant/ask', { body: { question: out.examples[0].question } });
  out.assistantErr = await fail(call('POST', '/assistant/ask', { body: { question: 'Сколько стоит кофе?' } }));
  out.unknownConv = await fail(call('GET', '/conversations/' + encodeURIComponent('DEV:p1-en-001')));
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def _mock_dialogs():
    """Диалоги мока так, как их видит mock.js: мок + дополнения p23 (для сверки с cqa/features/operators.py)."""
    from cqa.features.operators import Dialog

    convs = load("conversations") + [x["summary"] for x in _p23()["extras"] if x["in_lists"]]
    evals = dict(load("evaluations"))
    evals.update({x["uid"]: x["evaluation"] for x in _p23()["extras"]})
    out = []
    for s in convs:
        e = evals[s["uid"]]
        d = Dialog(uid=s["uid"], group_id=s["group_id"], external_id=s["external_id"], language=s["language"],
                   channel=s["channel"], operator=s["operator_name"], evaluation_id=e["id"],
                   checklist_version_id=e["checklist_version_id"], status=e["status"],
                   total=e["final_total"] if e["final_total"] is not None else e["total"], gross=e["gross"])
        for c in e["criteria"]:
            if c["score"]:
                d.points[c["criterion_id"]] = (float(c["score"]["points"] or 0), float(c["score"]["max_points"] or 0))
            if c["status"] == "done" and c["verdict"]:
                d.verdicts[c["criterion_id"]] = c["final_verdict"] or c["verdict"]
        out.append(d)
    return out


@pytest.mark.skipif(shutil.which("node") is None, reason="нет node")
def test_p23_mock_routes_follow_backend():
    """Мок-маршруты P2/P3 (frontend/js/mock.js) отвечают в форматах бэкенда; метрики критериев — как cqa/features/operators.py."""
    from cqa.api.routes.assistant import AssistantAnswerDTO
    from cqa.features.operators import criterion_stats
    from cqa.features.trainer import HIDDEN_FIELDS

    r = subprocess.run(["node", "-e", _P23_HARNESS, str(FRONTEND)], capture_output=True, text=True, timeout=90, check=False)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    n_dialogs = len(load("conversations")) + sum(1 for x in _p23()["extras"] if x["in_lists"])

    ops = out["operators"]
    assert ops["n_dialogs"] == n_dialogs and ops["n_operators"] == len(ops["operators"])
    # без имени оператора: звонок p4-ru-002 мока и реальные диалоги-дополнения, где оператор не представился
    no_name = sorted([s["uid"] for s in load("conversations") if not s["operator_name"]]
                     + [x["uid"] for x in _p23()["extras"] if x["in_lists"] and not x["summary"]["operator_name"]])
    assert sorted(ops["unassigned"]) == no_name and "DEV:p4-ru-002" in no_name
    assert {"name", "n_dialogs", "avg_total", "gross_count", "n_appeals", "n_open_appeals", "needs_review", "languages",
            "channels", "manual"} <= set(ops["operators"][0])
    prof = out["profile"]
    assert prof["name"] == "Кирилл" and prof["department"]["n_dialogs"] == n_dialogs
    assert {"criteria", "growth_zones", "strengths", "gross_errors", "dialogs"} <= set(prof)
    assert 1 <= len(prof["growth_zones"]) <= 3 and all(z["examples"] for z in prof["growth_zones"])
    ex = prof["growth_zones"][0]["examples"][0]
    assert {"uid", "mid", "quote", "message_text", "explanation", "how_to_fix", "verdict", "criterion_result_id"} <= set(ex)
    assert prof["gross_errors"] and prof["gross_errors"][0]["category"]
    assert out["profileErr"]["status"] == 404
    assert out["summaryOk"]["name"] == "Кирилл" and out["summaryOk"]["summary"]       # записанная реальная сводка
    assert out["summaryErr"]["status"] == 422 and "демо" in out["summaryErr"]["message"]

    # метрики критериев совпадают с бэкендом на тех же вердиктах
    from cqa.checklist.model import ChecklistConfig
    cfg = ChecklistConfig.model_validate(next(v for v in load("checklist-versions") if v["is_active"])["config"])
    dialogs = _mock_dialogs()
    overall = out["slices"]["language"]["overall"]
    assert overall["n_dialogs"] == n_dialogs
    for c in cfg.enabled_criteria():
        want = criterion_stats(dialogs, c)
        got = overall["criteria"][c.id]
        for k in ("n", "n_applicable", "n_na", "violations", "quality", "loss", "lost_points"):
            assert got[k] == pytest.approx(want[k], abs=0.011), (c.id, k)
    from cqa.features.operators import growth_zones, strengths
    mine = [d for d in dialogs if (d.operator or "").casefold() == "кирилл"]
    zones = growth_zones(cfg, mine, dialogs)
    assert [(z["criterion_id"], z["loss"]) for z in zones] == [(z["criterion_id"], z["loss"]) for z in prof["growth_zones"]]
    strong = strengths(cfg, mine, dialogs, {z["criterion_id"] for z in zones})
    assert [x["criterion_id"] for x in strong] == [x["criterion_id"] for x in prof["strengths"]]
    for by, s in out["slices"].items():
        assert s["by"] == by and sum(x["n_dialogs"] for x in s["slices"]) == n_dialogs
        assert [h["id"] for h in s["criteria"]] == [c.id for c in cfg.enabled_criteria()]
    assert "не назначен" in [x["key"] for x in out["slices"]["operator"]["slices"]]
    assert out["sliceErr"]["status"] == 422
    assert out["assign"] == {"uid": "DEV:p4-ru-002", "operator_name": "Айбек", "auto_operator_name": None, "manual": True}
    assert sorted(out["opsAfter"]["unassigned"]) == [u for u in no_name if u != "DEV:p4-ru-002"]
    assert out["unassign"]["manual"] is False

    # прецеденты
    AppealDTO.model_validate(out["decided"])
    p = next(x for x in out["precedents"] if x["appeal_id"] == 2)
    assert p["verdict"] == "Нет" and p["fragment"] and p["fragment_full"].split(":")[0] in ("КЛИЕНТ", "ОПЕРАТОР", "БОТ", "СИСТЕМА")
    assert {"criterion_title", "original_verdict", "added_in_version_id", "in_active_version", "uid", "external_id"} <= set(p)
    tv = out["toVersion"]
    assert tv["status"] == "draft" and tv["added"] == [p["id"]] and tv["comment"].startswith("прецеденты: 01 ← #")
    d = tv["diff"][0]
    assert d["scope"] == "criterion" and d["id"] == "01" and d["fields"] == ["examples"]
    assert d["after"]["examples"][-1]["verdict"] == "Нет"
    assert any(x["added_in_version_id"] == tv["version_id"] for x in out["precedentsAfter"])
    sim = out["similar"]
    assert sim["uids"] == [x["uid"] for x in sim["items"]] and len(sim["uids"]) <= 3
    assert all(x["match"]["reason"] or x["match"]["verdict"] for x in sim["items"])
    run = RunDTO.model_validate(out["run"])
    assert run.kind == "dry_run"
    assert out["diff"]["run"]["status"] == "done" and out["diff"]["demo_note"]
    assert {x["uid"] for x in out["diff"]["dialogs"]} == set(sim["uids"]) and out["diff"]["summary"]["verdicts_changed"] == 0
    rd = out["recDiff"]
    assert "demo_note" not in rd and rd["summary"]["verdicts_changed"] == 2 and rd["checklist_version_id"] == out["recTo"]["version_id"]
    assert out["recSimilar"]["uids"] == ["DEV:p1-kz-015", "DEV:p1-ru-025"]

    # тренажёр
    assert out["sessErr"]["status"] == 422 and "p1-ru-007" in out["sessErr"]["message"]
    s = out["sess"]
    assert s["status"] == "active" and not set(HIDDEN_FIELDS) & set(s["persona"]) and s["criteria_focus"] == ["04"]
    assert all(m["source"] == "base" for m in s["transcript"]) and s["prefix_len"] == len(s["transcript"])
    r1 = out["reply1"]
    assert [m["speaker"] for m in r1["new_messages"]] == ["operator", "client"] and r1["turn"] == 1
    assert r1["new_messages"][0]["text"] == "Здравствуйте! Подскажу путь." and r1["new_messages"][0]["source"] == "trainee"
    assert out["reply3"]["finished"] and [m["speaker"] for m in out["reply3"]["new_messages"]] == ["operator"]
    f = out["finish"]
    assert f["status"] == "finished" and f["comparison"] and set(HIDDEN_FIELDS) <= set(f["persona"])
    assert f["transcript"][-1]["text"] == "Хорошего дня!"
    assert out["finishCard"]["id"] == f["result_evaluation_id"]
    summ = next(x for x in out["sessions"] if x["id"] == s["id"])
    assert summ["before_total"] is not None and summ["after_total"] is not None and summ["status"] == "finished"
    les = out["lesson"]
    assert les["n_sessions"] >= 1 and [i["base_uid"] for i in les["items"]] == ["DEV:p1-ru-022", "DEV:p1-ru-007"]
    assert out["lessonErr"]["status"] == 422
    assert out["lessons"][0]["id"] == les["id"]

    # тьютор и ассистент
    if out["tutorRec"]:
        assert out["tutor"]["answer"] == out["tutorRec"][0]["response"]["answer"]
    assert out["tutorErr"]["status"] == 422
    assert out["best"]["total"] == _p23()["best_answers"]["total"] and len(out["best"]["items"]) == min(100, out["best"]["total"])
    assert all(x["topic"] == out["best"]["topics"][0]["topic"] for x in out["bestTopic"]["items"])
    assert out["bestQ"]["items"] and all("score" in x for x in out["bestQ"]["items"])
    AssistantAnswerDTO.model_validate(out["assistant"])
    assert out["assistantErr"]["status"] == 422
    assert out["unknownConv"]["status"] == 404 and "демо" in out["unknownConv"]["message"]
