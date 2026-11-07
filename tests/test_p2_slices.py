"""P2, срезы для руководителя: по языку, каналу, оператору и причине обращения на синтетических вердиктах.
Временная БД, fake, без сети."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqa.checklist.store import seed
from cqa.core.runner import create_run, recompute_final, run_sync
from cqa.features.slices import NO_OPERATOR, NO_REASON, slices
from cqa.ingest.service import ingest_records
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"

# mini-ru-chat (RU, чат, Ольга), mini-kz-chat (KZ, чат, Айгүл), mini-ru-call (RU, звонок, без имени)
VERDICTS = {
    "T:mini-ru-chat": {"01": "Да", "02": "Нет", "03": "N/A", "04": "20", "05": "Да", "06": "10", "07": "Да", "08": "Нет"},
    "T:mini-kz-chat": {"01": "Нет", "02": "Да", "03": "Да", "04": "10", "05": "Да", "06": "7", "07": "Нет", "08": "Да"},
    "T:mini-ru-call": {"01": "Да", "02": "N/A", "03": "Да", "04": "0", "05": "Нет", "06": "4", "07": "N/A", "08": "Нет"},
}
REASONS = {"T:mini-ru-chat": "вывод средств", "T:mini-kz-chat": "вывод средств"}     # у звонка сигналов нет


@pytest.fixture
def con(tmp_db):
    con = tmp_db
    vid = seed(con, get_settings().checklist_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ingest_records(con, "T", payload["data"]["records"], "file")
    ingest_records(con, "AUTOTEST", payload["data"]["records"], "file")
    uids = [r[0] for r in con.execute("SELECT uid FROM conversations ORDER BY uid")]
    assert run_sync(con, create_run(con, uids=uids, version_id=vid, profile_name="fake"))["units_failed"] == 0
    for uid, verdicts in VERDICTS.items():
        ev = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]
        for cid, v in verdicts.items():
            con.execute("UPDATE criterion_results SET verdict=?, category=? WHERE evaluation_id=? AND criterion_id=?",
                        (v, "оскорбление" if cid == "08" and v == "Да" else "", ev, cid))
        con.execute("UPDATE evaluations SET needs_review=? WHERE id=?", (int(uid.endswith("call")), ev))
        recompute_final(con, ev)
        if uid in REASONS:
            con.execute("INSERT INTO signals(evaluation_id, contact_reason, resolved, unanswered_json, churn_risk)"
                        " VALUES (?, ?, 1, '[]', 0)", (ev, REASONS[uid]))
    return con


def _totals(con) -> dict[str, float]:
    return {r[0]: r[1] for r in con.execute(
        "SELECT c.uid, e.final_total FROM conversations c JOIN evaluations e ON e.id = c.current_evaluation_id"
        " WHERE c.group_id = 'T'")}


def test_slices_by_language(con):
    data = slices(con, "language")
    assert [c["id"] for c in data["criteria"]] == ["01", "02", "03", "04", "05", "06", "07", "08"]
    rows = {r["key"]: r for r in data["slices"]}
    assert list(rows) == ["RU", "KZ"]                          # по числу диалогов; AUTOTEST не считается
    ru, kz = rows["RU"], rows["KZ"]
    t = _totals(con)
    assert ru["n_dialogs"] == 2 and ru["avg_total"] == round((t["T:mini-ru-chat"] + t["T:mini-ru-call"]) / 2, 2)
    assert ru["criteria"]["01"]["yes_share"] == 1.0
    assert ru["criteria"]["02"] == {**ru["criteria"]["02"], "yes_share": 0.0, "n_applicable": 1, "n_na": 1}
    assert ru["criteria"]["04"]["mean_step"] == 10.0 and ru["criteria"]["04"]["quality"] == 0.5
    assert ru["criteria"]["06"]["mean_step"] == 7.0
    assert ru["criteria"]["07"]["yes_share"] == 1.0 and ru["criteria"]["07"]["n_na"] == 1
    assert ru["gross_count"] == 0 and ru["needs_review"] == 1
    assert kz["gross_count"] == 1 and kz["criteria"]["08"]["n_yes"] == 1 and kz["criteria"]["08"]["quality"] == 0.0
    assert data["overall"]["n_dialogs"] == 3 and data["overall"]["criteria"]["01"]["yes_share"] == round(2 / 3, 4)


def test_slices_by_channel_operator_reason(con):
    ch = {r["key"]: r["n_dialogs"] for r in slices(con, "channel")["slices"]}
    assert ch == {"agent chat": 2, "voice call": 1}

    op = {r["key"]: r for r in slices(con, "operator")["slices"]}
    assert set(op) == {"Ольга", "Айгүл", NO_OPERATOR}
    assert op[NO_OPERATOR]["uids"] == ["T:mini-ru-call"]

    rs = {r["key"]: r for r in slices(con, "reason")["slices"]}
    assert set(rs) == {"вывод средств", NO_REASON}
    w = rs["вывод средств"]
    assert w["n_dialogs"] == 2 and w["criteria"]["02"]["yes_share"] == 0.5 and w["gross_count"] == 1
    assert w["criteria"]["04"]["mean_step"] == 15.0

    with pytest.raises(ValueError):
        slices(con, "family")


def test_slices_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import cqa.api.app as app_mod

    s = get_settings()
    monkeypatch.setattr(s, "db_path", tmp_path / "cqa.db")
    monkeypatch.setattr(s, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(s, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(s, "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")
    with TestClient(app_mod.create_app(fake=True, worker=False)) as c:
        con = c.app.state.con
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        ingest_records(con, "T", payload["data"]["records"], "file")
        uids = [r[0] for r in con.execute("SELECT uid FROM conversations")]
        vid = con.execute("SELECT active_version_id FROM checklists").fetchone()[0]
        run_sync(con, create_run(con, uids=uids, version_id=vid, profile_name="fake"))
        r = c.get("/api/slices", params={"by": "language", "group": "T"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["by"] == "language" and body["group"] == "T" and body["overall"]["n_dialogs"] == 3
        assert {x["key"] for x in body["slices"]} == {"RU", "KZ"}
        assert c.get("/api/slices", params={"by": "family"}).status_code == 422
        assert c.get("/api/slices", params={"by": "language", "group": "NONE"}).json()["overall"]["n_dialogs"] == 0
