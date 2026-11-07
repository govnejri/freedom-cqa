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
