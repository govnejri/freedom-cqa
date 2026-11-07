"""Исправления по итогам QA: поиск по кириллице, лимиты длины, апелляция на устаревшую оценку,
прецедент из контрдоказательств, выборка пробного прогона по нарушениям."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cqa.api.schemas import AppealCreateDTO, DecisionCreateDTO
from cqa.appeals.service import AppealInvalid, _precedent_mids, create_appeal
from cqa.checklist.store import get_version, seed
from cqa.core.runner import create_run, run_sync
from cqa.ingest.service import ingest_records
from cqa.ingest.store import list_uids
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _setup(con):
    vid = seed(con, get_settings().checklist_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ingest_records(con, "T", payload["data"]["records"] if "data" in payload else payload["records"], "file")
    return vid, list_uids(con, "T")


def test_cyrillic_search_is_case_insensitive(tmp_db):
    tmp_db.execute("CREATE TEMP TABLE t(x TEXT)")
    tmp_db.execute("INSERT INTO t VALUES ('Мұрат'), ('Алина')")
    rows = tmp_db.execute("SELECT x FROM t WHERE py_lower(x) LIKE py_lower(?)", ("%мұр%",)).fetchall()
    assert [r[0] for r in rows] == ["Мұрат"]


def test_length_limits():
    with pytest.raises(ValidationError):
        AppealCreateDTO(evaluation_id=1, criterion_id="01", comment="x" * 2001)
    with pytest.raises(ValidationError):
        DecisionCreateDTO(action="confirm", reason="x" * 2001)
    AppealCreateDTO(evaluation_id=1, criterion_id="01", comment="x" * 2000)


def test_appeal_on_outdated_evaluation_is_rejected(tmp_db):
    vid, uids = _setup(tmp_db)
    run_sync(tmp_db, create_run(tmp_db, uids=[uids[0]], version_id=vid, profile_name="fake"))
    old = tmp_db.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uids[0],)).fetchone()[0]
    run_sync(tmp_db, create_run(tmp_db, uids=[uids[0]], version_id=vid, profile_name="fake", force=True))
    with pytest.raises(AppealInvalid):
        create_appeal(tmp_db, AppealCreateDTO(evaluation_id=old, criterion_id="01", comment="не согласен"))


def test_precedent_prefers_counter_evidence_when_changed(tmp_db):
    assert _precedent_mids(tmp_db, "x", ["m3"], ["m7"], None) == ["m3"]
    assert _precedent_mids(tmp_db, "x", ["m3"], ["m7"], None, prefer_counter=True) == ["m7"]
    assert _precedent_mids(tmp_db, "x", ["m3"], [], None, prefer_counter=True) == ["m3"]


def test_dry_run_sample_prefers_violations(tmp_db):
    from cqa.api.routes.dryrun import sample_uids

    vid, uids = _setup(tmp_db)
    run_sync(tmp_db, create_run(tmp_db, uids=uids, version_id=vid, profile_name="fake"))
    cfg, _ = get_version(tmp_db, vid)
    # пометим у последнего диалога нарушение по 07 — он должен идти первым
    target = uids[-1]
    ev = tmp_db.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (target,)).fetchone()[0]
    tmp_db.execute("UPDATE criterion_results SET verdict='Нет' WHERE evaluation_id=? AND criterion_id='07'", (ev,))
    others = [u for u in uids if u != target]
    for u in others:
        e = tmp_db.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (u,)).fetchone()[0]
        tmp_db.execute("UPDATE criterion_results SET verdict='Да' WHERE evaluation_id=? AND criterion_id='07'", (e,))
    assert sample_uids(tmp_db, "T", 2, ["07"], cfg)[0] == target
