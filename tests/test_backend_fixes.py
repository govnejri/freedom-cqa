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


def test_restart_does_not_revert_ui_published_version(tmp_db, tmp_path):
    """Перезапуск (sync_yaml) не откатывает версию, опубликованную в интерфейсе, если YAML не менялся."""
    from cqa.checklist.store import create_version, get_active, publish, sync_yaml

    path = get_settings().checklist_path
    vid, _ = sync_yaml(tmp_db, path)
    cfg, meta = get_active(tmp_db)
    cfg.criteria[6].definition = cfg.criteria[6].definition + " Уточнено в интерфейсе."
    ui_vid = create_version(tmp_db, cfg.id, cfg, parent_id=meta["id"], status="published")
    publish(tmp_db, ui_vid)
    again, created = sync_yaml(tmp_db, path)          # «перезапуск сервера»
    assert not created and again == ui_vid


def test_criterion_validation_rejects_bad_values():
    from cqa.checklist.model import Criterion

    base = dict(id="09", title="Новый", section="conduct", type="yes_no", weight=5, definition="Да — если …")
    Criterion(**base)
    for bad in ({"weight": -10}, {"title": "  "}, {"definition": ""}):
        with pytest.raises(ValidationError):
            Criterion(**{**base, **bad})
    # пробелы и пустое условие N/A нормализуются — хэш совпадает с «чистым» критерием
    from cqa.checklist.io import criterion_hash
    assert criterion_hash(Criterion(**{**base, "title": " Новый ", "na_condition": ""})) == criterion_hash(Criterion(**base))


def test_rerun_keeps_open_appeal_and_signals(tmp_db):
    vid, uids = _setup(tmp_db)
    uid = uids[0]
    run_sync(tmp_db, create_run(tmp_db, uids=[uid], version_id=vid, profile_name="fake"))
    ev1 = tmp_db.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]
    appeal = create_appeal(tmp_db, AppealCreateDTO(evaluation_id=ev1, criterion_id="01", comment="не согласен"))
    tmp_db.execute("INSERT INTO signals(evaluation_id, contact_reason, resolved, unanswered_json) VALUES (?,?,?,?)",
                   (ev1, "вывод средств", 0, '[{"text":"когда выводить?","mid":"m5","status":"открыт"}]'))
    run_sync(tmp_db, create_run(tmp_db, uids=[uid], version_id=vid, profile_name="fake", force=True))
    ev2 = tmp_db.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]
    assert ev2 != ev1
    a = tmp_db.execute("SELECT evaluation_id, status FROM appeals WHERE id=?", (appeal.id,)).fetchone()
    assert a["evaluation_id"] == ev2 and a["status"] == "open"
    s = tmp_db.execute("SELECT contact_reason FROM signals WHERE evaluation_id=?", (ev2,)).fetchone()
    assert s and s["contact_reason"] == "вывод средств"
