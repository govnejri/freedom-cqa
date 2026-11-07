"""P2, прецеденты: решение по апелляции → пример в критерии черновика версии (активная не меняется) → похожие
диалоги → пробный прогон. Профиль fake, временная БД, без сети."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from cqa.api.schemas import AppealCreateDTO, DecisionCreateDTO
from cqa.appeals.service import create_appeal, decide
from cqa.checklist.store import get_active, get_version, publish, seed
from cqa.core.runner import create_run, run_sync
from cqa.features.precedents import (
    FRAGMENT_LIMIT,
    PrecedentConflict,
    PrecedentNotFound,
    build_version,
    list_precedents,
    precedent_to_version,
    short_fragment,
    similar_dialogs,
    similar_items,
)
from cqa.ingest.service import ingest_records
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"
WHY = "клиент сам описал запрос, уточнения были не нужны"


def _records() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["data"]["records"] if "data" in payload else payload["records"]


def _evaluate(con, groups=("T",)) -> int:
    vid = seed(con, get_settings().checklist_path)
    uids: list[str] = []
    for g in groups:
        ingest_records(con, g, _records(), "file")
        uids += [r[0] for r in con.execute("SELECT uid FROM conversations WHERE group_id=? ORDER BY uid", (g,))]
    r = run_sync(con, create_run(con, uids=uids, version_id=vid, profile_name="fake"))
    assert r["units_failed"] == 0
    return vid


def _current(con, uid: str) -> int:
    return con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]


def _precedent(con, uid: str = "T:mini-ru-chat", cid: str = "02", new: str | None = None):
    ev = _current(con, uid)
    if new is None:      # любой вердикт, отличный от исходного
        old = con.execute("SELECT verdict FROM criterion_results WHERE evaluation_id=? AND criterion_id=?",
                          (ev, cid)).fetchone()[0]
        new = next(v for v in ("Да", "Нет", "N/A") if v != old)
    a = create_appeal(con, AppealCreateDTO(evaluation_id=ev, criterion_id=cid, comment="не согласен"))
    decide(con, a.id, DecisionCreateDTO(action="change", new_verdict=new, reason=WHY, make_precedent=True))
    return con.execute("SELECT * FROM precedents WHERE appeal_id=?", (a.id,)).fetchone()


def test_short_fragment_limits_length_and_keeps_roles():
    long = " / ".join([
        "КЛИЕНТ: " + "Не могу вывести деньги, приложение просит обновить анкетные данные и паспорт. " * 3,
        "ОПЕРАТОР: " + "Откройте Меню, затем Поручения, Персональные данные и Изменение сведений. " * 3,
        "КЛИЕНТ: Нашёл, спасибо.",
    ])
    out = short_fragment(long)
    assert len(out) <= FRAGMENT_LIMIT
    parts = out.split(" / ")
    assert parts[0].startswith("КЛИЕНТ: ") and parts[1].startswith("ОПЕРАТОР: ")
    assert "…" in parts[0]
    assert short_fragment("ОПЕРАТОР: Хорошего дня!") == "ОПЕРАТОР: Хорошего дня!"


def test_decision_with_make_precedent_writes_row(tmp_db):
    con = tmp_db
    _evaluate(con)
    p = _precedent(con)
    assert p is not None
    assert p["criterion_id"] == "02" and p["verdict"] in ("Да", "Нет", "N/A") and p["why"] == WHY
    assert p["fragment"] and p["added_in_version_id"] is None


def test_precedent_to_draft_version_keeps_active(tmp_db):
    from cqa.core.prompt import render_criterion

    con = tmp_db
    _evaluate(con)
    p = _precedent(con)
    active_before, meta_before = get_active(con)

    vid = precedent_to_version(con, [p["id"]], comment="прецедент по 02")

    cfg, meta = get_version(con, vid)
    assert meta["status"] == "draft" and meta["parent_id"] == meta_before["id"]
    assert meta["comment"] == "прецедент по 02"
    active_after, meta_after = get_active(con)                 # активная версия та же и не изменилась
    assert meta_after["id"] == meta_before["id"] and active_after == active_before

    before = active_before.criterion("02").examples
    examples = cfg.criterion("02").examples
    assert examples[:-1] == before and len(examples) == len(before) + 1
    ex = examples[-1]
    assert ex.verdict == p["verdict"] and ex.why == WHY
    assert 0 < len(ex.fragment) <= FRAGMENT_LIMIT
    assert ex.fragment.split(":")[0] in ("КЛИЕНТ", "ОПЕРАТОР", "БОТ", "СИСТЕМА", "НЕИЗВЕСТНО")
    for c in cfg.criteria:                                       # остальные критерии не тронуты
        if c.id != "02":
            assert c == active_before.criterion(c.id)
    assert f"«{ex.verdict}»: {ex.fragment} — {ex.why}" in render_criterion(cfg.criterion("02"))   # судья увидит пример

    assert con.execute("SELECT added_in_version_id FROM precedents WHERE id=?", (p["id"],)).fetchone()[0] == vid
    row = list_precedents(con)[0]
    assert row["added_in_version_id"] == vid and row["added_in_version_status"] == "draft"
    assert row["in_active_version"] is False and row["original_verdict"] not in (None, p["verdict"])

    publish(con, vid)                                            # QA опубликовал → пример в активной
    assert list_precedents(con)[0]["in_active_version"] is True
    with pytest.raises(PrecedentConflict):
        build_version(con, [p["id"]])
    with pytest.raises(PrecedentNotFound):
        build_version(con, [9999])


def _set(con, uid: str, cid: str, verdict: str, reason: str | None) -> None:
    ev = _current(con, uid)
    con.execute("UPDATE criterion_results SET verdict=? WHERE evaluation_id=? AND criterion_id=?", (verdict, ev, cid))
    if reason:
        con.execute("INSERT OR REPLACE INTO signals(evaluation_id, contact_reason, resolved, unanswered_json, churn_risk)"
                    " VALUES (?, ?, 1, '[]', 0)", (ev, reason))


def test_similar_dialogs_order_and_exclusions(tmp_db):
    con = tmp_db
    _evaluate(con, groups=("T", "U", "AUTOTEST"))
    setup = {
        "T:mini-ru-chat": ("Нет", "вывод средств"),          # источник прецедента
        "T:mini-kz-chat": ("Да", "вывод средств"),           # та же причина, другой вердикт, KZ
        "T:mini-ru-call": ("Нет", "пополнение"),             # тот же вердикт, другая причина
        "U:mini-ru-chat": ("Нет", "вывод средств"),          # всё совпадает
        "U:mini-ru-call": ("Нет", "вывод средств"),          # причина и вердикт, другой канал
        "U:mini-kz-chat": ("Да", "пополнение"),              # ничего — не берётся
        "AUTOTEST:mini-ru-chat": ("Нет", "вывод средств"),   # автотест — не берётся
    }
    for uid, (verdict, reason) in setup.items():
        _set(con, uid, "02", verdict, reason)
    p = _precedent(con, "T:mini-ru-chat", "02", "Да")

    r = similar_items(con, p["id"])
    assert r["source_uid"] == "T:mini-ru-chat" and r["contact_reason"] == "вывод средств"
    assert r["original_verdict"] == "Нет"
    assert r["uids"] == ["U:mini-ru-chat", "U:mini-ru-call", "T:mini-kz-chat", "T:mini-ru-call"]
    assert r["items"][0]["match"] == {"reason": True, "verdict": True, "language": True, "channel": True}
    assert similar_dialogs(con, p["id"], limit=2) == ["U:mini-ru-chat", "U:mini-ru-call"]


# ---------------------------------------------------------------- API: апелляция → прецедент → версия → dry-run

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import cqa.api.app as app_mod

    s = get_settings()
    monkeypatch.setattr(s, "db_path", tmp_path / "cqa.db")
    monkeypatch.setattr(s, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(s, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(s, "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")
    with TestClient(app_mod.create_app(fake=True)) as c:
        yield c


def _wait(c, run_id: int, timeout: float = 30.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = c.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("done", "failed", "cancelled"):
            return r
        time.sleep(0.05)
    raise AssertionError(f"прогон {run_id} не завершился")


def test_api_precedent_flow(client):
    c = client
    with open(FIXTURE, "rb") as f:
        assert c.post("/api/datasets/upload", files={"file": ("mini.json", f, "application/json")}).status_code == 200
    assert _wait(c, c.post("/api/runs", json={"group": "MINI"}).json()["id"])["status"] == "done"
    ev = c.get("/api/conversations/MINI:mini-ru-chat/evaluation").json()
    cr = next(x for x in ev["criteria"] if x["criterion_id"] == "07")
    new = next(v for v in cr["allowed_verdicts"] if v != cr["verdict"])
    appeal = c.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "07", "comment": "спорно"}).json()
    r = c.post(f"/api/appeals/{appeal['id']}/decision",
               json={"action": "change", "new_verdict": new, "reason": WHY, "make_precedent": True})
    assert r.status_code == 200, r.text

    lst = c.get("/api/precedents").json()
    assert len(lst) == 1
    p = lst[0]
    assert p["criterion_id"] == "07" and p["verdict"] == new and p["criterion_title"] == "Завершение"
    assert p["uid"] == "MINI:mini-ru-chat" and p["added_in_version_id"] is None and p["fragment"]

    active = next(v for v in c.get("/api/checklists/freedom-cqa/versions").json() if v["is_active"])
    r = c.post("/api/precedents/to-version", json={"precedent_ids": [p["id"]], "comment": "из апелляции"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == active["version"] + 1 and body["status"] == "draft" and body["added"] == [p["id"]]
    assert [(d["id"], d["fields"]) for d in body["diff"]] == [("07", ["examples"])]
    still = next(v for v in c.get("/api/checklists/freedom-cqa/versions").json() if v["is_active"])
    assert still["id"] == active["id"]
    assert c.get("/api/precedents").json()[0]["added_in_version"] == body["version"]
    assert c.post("/api/precedents/to-version", json={"precedent_ids": [12345]}).status_code == 404
    assert c.post("/api/precedents/to-version", json={"precedent_ids": []}).status_code == 422

    sim = c.get(f"/api/precedents/{p['id']}/similar").json()
    assert "MINI:mini-ru-chat" not in sim["uids"] and sim["source_uid"] == "MINI:mini-ru-chat"
    uids = sim["uids"] + [sim["source_uid"]]
    run = c.post(f"/api/checklist-versions/{body['version_id']}/dry-run", json={"uids": uids})
    assert run.status_code == 200, run.text
    run = _wait(c, run.json()["id"])
    assert run["status"] == "done" and run["units_total"] == len(uids)      # только изменённый критерий 07
