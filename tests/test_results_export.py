"""Экспорт в формат сдачи организаторам (results.json, JSON 1.0)."""
from __future__ import annotations

import json
from pathlib import Path

from cqa.checklist.store import seed
from cqa.core.runner import create_run, run_sync
from cqa.export.results import CODES, _turns, build_results, validate
from cqa.ingest.service import ingest_records
from cqa.ingest.store import list_uids
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _setup(con):
    vid = seed(con, get_settings().checklist_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ingest_records(con, "T", payload["data"]["records"] if "data" in payload else payload["records"], "file")
    return vid, list_uids(con, "T")


def test_results_format_valid_and_complete(tmp_db):
    vid, uids = _setup(tmp_db)
    run_sync(tmp_db, create_run(tmp_db, uids=uids[:-1], version_id=vid, profile_name="fake"))
    doc = build_results(tmp_db, "T", "Команда")
    ids = [r[0] for r in tmp_db.execute("SELECT external_id FROM conversations WHERE group_id='T' AND parent_uid IS NULL")]
    assert validate(doc, ids) == []
    assert len(doc["results"]) == len(uids)            # неоценённый диалог не пропал
    by_status = {r["status"] for r in doc["results"]}
    assert by_status == {"ok", "error"}
    err = next(r for r in doc["results"] if r["status"] == "error")
    assert err["criteria"] == [] and err["score"] is None and err["error"]
    ok = next(r for r in doc["results"] if r["status"] == "ok")
    assert [c["criterion_id"] for c in ok["criteria"]] == list(CODES.values())
    crit = next(c["value"] for c in ok["criteria"] if c["criterion_id"] == "critical")
    assert ok["score"]["critical_flag"] is crit
    json.dumps(doc, ensure_ascii=False)


def test_validate_catches_bad_values():
    doc = {"schema_version": "1.0", "team_name": "x", "model": {"mode": "external_api", "name": "m"}, "results": [
        {"conversation_id": "a", "status": "ok", "score": {"final_score": 50.0, "critical_flag": False}, "criteria": [
            {"criterion_id": k, "value": v, "rationale": "", "evidence_turns": [1]} for k, v in
            [("contact", True), ("discovery", None), ("identification", None), ("consultation", 15),
             ("empathy", True), ("dialogue", False), ("closure", True), ("critical", False)]]}]}
    errs = validate(doc, ["a"])
    assert any("dialogue" in e for e in errs)          # False вместо числа шкалы
    doc["results"][0]["criteria"][5]["value"] = 7
    assert validate(doc, ["a"]) == []
    assert validate(doc, ["a", "b"])                    # пропущенный диалог


def test_turns_from_evidence_or_explanation():
    assert _turns('[{"id": "m12", "quote": "x"}, {"id": "m3", "quote": "y"}]') == [3, 12]
    assert _turns("[]", "N/A. Основание — проверены m10–m12.", 20) == [10, 11, 12]
    assert _turns("[]", "Основание — m1, m30.", 20) == [1]
