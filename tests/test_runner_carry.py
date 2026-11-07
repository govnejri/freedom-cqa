"""Перепрогон диалога сохраняет решения по апелляциям, если вердикт модели и критерий не изменились."""
from __future__ import annotations

import json
from pathlib import Path

from cqa.api.schemas import AppealCreateDTO, DecisionCreateDTO
from cqa.appeals.service import create_appeal, decide
from cqa.checklist.store import seed
from cqa.core.runner import create_run, run_sync, retry_failed
from cqa.ingest.service import ingest_records
from cqa.ingest.store import list_uids
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _setup(con):
    vid = seed(con, get_settings().checklist_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = payload["data"]["records"] if "data" in payload else payload["records"]
    ingest_records(con, "T", records, "file")
    uid = list_uids(con, "T")[0]
    return vid, uid


def test_decision_survives_rerun(tmp_db):
    con = tmp_db
    vid, uid = _setup(con)
    r1 = run_sync(con, create_run(con, uids=[uid], version_id=vid, profile_name="fake"))
    assert r1["units_failed"] == 0
    ev1 = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]
    cr = con.execute("SELECT criterion_id, verdict FROM criterion_results WHERE evaluation_id=? AND criterion_id='01'",
                     (ev1,)).fetchone()
    new_v = "Нет" if cr["verdict"] == "Да" else "Да"
    appeal = create_appeal(con, AppealCreateDTO(evaluation_id=ev1, criterion_id="01", comment="не согласен"))
    decide(con, appeal.id, DecisionCreateDTO(action="change", new_verdict=new_v, reason="проверил"))
    final1 = con.execute("SELECT final_total FROM evaluations WHERE id=?", (ev1,)).fetchone()[0]

    # перепрогон: fake детерминирован, вердикт и критерий те же → решение переносится
    run_sync(con, create_run(con, uids=[uid], version_id=vid, profile_name="fake", force=True))
    ev2 = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]
    assert ev2 != ev1
    moved = con.execute("SELECT a.status, d.new_verdict FROM appeals a JOIN decisions d ON d.appeal_id=a.id"
                        " WHERE a.evaluation_id=? AND a.criterion_id='01'", (ev2,)).fetchone()
    assert moved is not None and moved["status"] == "decided" and moved["new_verdict"] == new_v
    final2 = con.execute("SELECT final_total FROM evaluations WHERE id=?", (ev2,)).fetchone()[0]
    assert final2 == final1


def test_retry_failed_does_not_resume_cancelled(tmp_db):
    con = tmp_db
    vid, uid = _setup(con)
    run_id = create_run(con, uids=[uid], version_id=vid, profile_name="fake")
    con.execute("UPDATE runs SET status='cancelled' WHERE id=?", (run_id,))
    assert retry_failed(con, run_id) == 0
    assert con.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()[0] == "cancelled"
