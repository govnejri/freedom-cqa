"""API инсайтов (cqa/api/routes/insights.py): радар рисков, «В комплаенс», вопросы без ответа, причины, сводка,
фоновый расчёт сигналов. Профиль fake, без сети, временная БД."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqa.api.schemas import AppealCreateDTO, DecisionCreateDTO
from cqa.appeals.service import create_appeal, decide
from cqa.checklist.store import seed
from cqa.core.runner import create_run, recompute_final, run_sync
from cqa.ingest.service import ingest_records
from cqa.ingest.store import list_uids
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT, KZ, CALL = "MINI:mini-ru-chat", "MINI:mini-kz-chat", "MINI:mini-ru-call"


def _flag(con, eid: int, verdict: str, category: str = "", evidence: list | None = None) -> int:
    """Вердикт флага 08 в оценке (как будто его поставила модель) + пересчёт финального итога."""
    con.execute("UPDATE criterion_results SET verdict=?, category=?, evidence_json=? WHERE evaluation_id=? AND criterion_id='08'",
                (verdict, category, json.dumps(evidence or [], ensure_ascii=False), eid))
    con.execute("UPDATE evaluations SET gross=? WHERE id=?", (int(verdict == "Да"), eid))
    recompute_final(con, eid)
    return con.execute("SELECT id FROM criterion_results WHERE evaluation_id=? AND criterion_id='08'", (eid,)).fetchone()[0]


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    """Оценённая группа MINI (fake): в чате флаг 08 «Да», в звонке «Нет», в казахском чате «Нет» → «Да» апелляцией."""
    import cqa.api.app as app_mod

    con = tmp_db
    s = get_settings()
    monkeypatch.setattr(s, "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")
    vid = seed(con, s.checklist_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ingest_records(con, "MINI", payload["data"]["records"], "file")
    uids = list_uids(con, "MINI")
    assert run_sync(con, create_run(con, uids=uids, version_id=vid, profile_name="fake"))["units_failed"] == 0
    ev = {r["uid"]: r["current_evaluation_id"]
          for r in con.execute("SELECT uid, current_evaluation_id FROM conversations")}
    crid = {
        CHAT: _flag(con, ev[CHAT], "Да", "обход компании",
                    [{"id": "m10", "quote": "Ваш тариф — «Стандарт»", "ok": True},
                     {"id": "m7", "quote": "неверная цитата", "ok": False}]),
        CALL: _flag(con, ev[CALL], "Нет"),
        KZ: _flag(con, ev[KZ], "Нет"),
    }
    a = create_appeal(con, AppealCreateDTO(evaluation_id=ev[KZ], criterion_id="08", comment="было оскорбление"))
    decide(con, a.id, DecisionCreateDTO(action="change", new_verdict="Да", reason="подтверждаю"))
    with TestClient(app_mod.create_app(fake=True, worker=False)) as client:
        yield {"con": con, "client": client, "ev": ev, "crid": crid}


def _wait_signals(client: TestClient, n: int, timeout: float = 15.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = client.get("/api/insights/reasons", params={"group": "MINI"})
        assert r.status_code == 200, r.text
        if r.json()["n_with_signals"] >= n:
            return r.json()
        time.sleep(0.05)
    raise AssertionError("сигналы не посчитаны")


def test_risks_returns_final_flags(env):
    c = env["client"]
    r = c.get("/api/insights/risks", params={"group": "MINI"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2 and data["n_conversations"] == 2
    items = {it["uid"]: it for it in data["items"]}
    assert set(items) == {CHAT, KZ}                                   # звонок «Нет» — не риск
    chat = items[CHAT]
    assert chat["criterion_id"] == "08" and chat["verdict"] == "Да" and chat["category"] == "обход компании"
    assert chat["criterion_result_id"] == env["crid"][CHAT]
    assert chat["external_id"] == "mini-ru-chat" and chat["language"] == "RU" and chat["channel"] == "agent chat"
    assert chat["evidence"] == [{"mid": "m10", "quote": "Ваш тариф — «Стандарт»",
                                 "text": "Ваш тариф — «Стандарт», он указан в разделе «Профиль».",
                                 "speaker": "operator", "audio_sec": None}]   # отклонённая валидатором цитата не попадает
    assert chat["compliance"] is None and chat["changed_by_appeal"] is False
    kz = items[KZ]
    assert kz["changed_by_appeal"] is True and kz["category"] == "без категории"
    cats = {x["category"]: x for x in data["by_category"]}
    assert cats["обход компании"]["count"] == 1 and cats["обход компании"]["criterion_result_ids"] == [env["crid"][CHAT]]
    assert c.get("/api/insights/risks", params={"group": "NOPE"}).json()["total"] == 0


def test_compliance_idempotent(env):
    c, con, crid = env["client"], env["con"], env["crid"][CHAT]
    r1 = c.post(f"/api/insights/risks/{crid}/compliance", json={"note": "  проверить оператора  "})
    assert r1.status_code == 200, r1.text
    case = r1.json()
    assert case["created"] is True and case["status"] == "open" and case["note"] == "проверить оператора"
    r2 = c.post(f"/api/insights/risks/{crid}/compliance", json={"note": "ещё раз"})
    assert r2.status_code == 200 and r2.json()["id"] == case["id"] and r2.json()["created"] is False
    assert r2.json()["note"] == "проверить оператора"
    assert con.execute("SELECT COUNT(*) FROM compliance_cases").fetchone()[0] == 1
    risk = next(it for it in c.get("/api/insights/risks").json()["items"] if it["criterion_result_id"] == crid)
    assert risk["compliance"]["id"] == case["id"] and risk["compliance"]["status"] == "open"
    assert c.get("/api/insights/summary", params={"group": "MINI"}).json()["risks_open_compliance"] == 1

    assert c.post("/api/insights/risks/999999/compliance", json={}).status_code == 404
    not_flag = con.execute("SELECT id FROM criterion_results WHERE evaluation_id=? AND criterion_id='01'",
                           (env["ev"][CHAT],)).fetchone()[0]
    assert c.post(f"/api/insights/risks/{not_flag}/compliance", json={}).status_code == 422
    assert c.post(f"/api/insights/risks/{env['crid'][CALL]}/compliance").status_code == 409   # флаг «Нет»


def test_signals_in_background_and_reports(env):
    c = env["client"]
    before = c.get("/api/insights/unanswered", params={"group": "MINI"}).json()
    assert before["n_evaluated"] == 3 and before["n_with_signals"] == 0 and before["topics"] == []

    r = c.post("/api/insights/signals", json={"group": "MINI"})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "scheduled", "queued": 3, "already": 0, "group": "MINI", "profile": "fake"}
    reasons = _wait_signals(c, 3)
    assert sum(x["n_conversations"] for x in reasons["reasons"]) == 3
    for x in reasons["reasons"]:
        assert 0 <= x["resolved_share"] <= 1 and x["avg_total"] is not None
    assert sum(x["gross_count"] for x in reasons["reasons"]) == 2      # финальные флаги: чат и казахский чат

    again = c.post("/api/insights/signals", json={"group": "MINI"}).json()
    assert again["status"] == "nothing_to_do" and again["queued"] == 0 and again["already"] == 3

    s = c.get("/api/insights/summary", params={"group": "MINI"}).json()
    assert s["n_conversations"] == 3 and s["n_evaluated"] == 3 and s["gross_count"] == 2
    assert s["n_with_signals"] == 3 and s["open_appeals"] == 0 and isinstance(s["top_unanswered_topics"], list)


def test_unanswered_grouped_by_topic(env):
    c, con, ev = env["client"], env["con"], env["ev"]
    rows = {
        CHAT: ("тарифы и комиссии", [{"text": "Где посмотреть тариф?", "mid": "m1", "status": "открыт"},
                                     {"text": "Можно ли сменить тариф?", "mid": "m6", "status": "частично"}]),
        CALL: ("тарифы и комиссии", []),
        KZ: ("пополнение", [{"text": "Как пополнить счёт?", "mid": "m1", "status": "открыт"}]),
    }
    for uid, (topic, items) in rows.items():
        con.execute("INSERT INTO signals(evaluation_id, contact_reason, resolved, unanswered_json, churn_risk,"
                    " missed_opportunity) VALUES (?,?,?,?,0,'')",
                    (ev[uid], topic, int(not items), json.dumps(items, ensure_ascii=False)))
    data = c.get("/api/insights/unanswered", params={"group": "MINI"}).json()
    assert data["total_questions"] == 3 and data["n_conversations"] == 2 and data["n_with_signals"] == 3
    assert [t["topic"] for t in data["topics"]] == ["тарифы и комиссии", "пополнение"]
    tariffs = data["topics"][0]
    assert (tariffs["n_questions"], tariffs["n_conversations"], tariffs["n_conversations_total"]) == (2, 1, 2)
    scores = {r["uid"]: r["s"] for r in con.execute(
        "SELECT uid, COALESCE(final_total, total) AS s FROM evaluations WHERE id IN (?,?)", (ev[CHAT], ev[CALL]))}
    assert tariffs["avg_total"] == pytest.approx(round((scores[CHAT] + scores[CALL]) / 2, 2), abs=0.01)
    assert tariffs["avg_total_unanswered"] == pytest.approx(round(scores[CHAT], 2), abs=0.01)
    q = tariffs["questions"][0]
    assert q == {"text": "Где посмотреть тариф?", "status": "открыт", "uid": CHAT, "external_id": "mini-ru-chat",
                 "mid": "m1", "message_text": "Подскажите, где посмотреть мой тариф?", "language": "RU",
                 "channel": "agent chat", "score": q["score"]}

    only = c.get("/api/insights/unanswered", params={"group": "MINI", "topic": "пополнение"}).json()
    assert [t["topic"] for t in only["topics"]] == ["пополнение"] and only["total_questions"] == 1

    reasons = {x["topic"]: x for x in c.get("/api/insights/reasons", params={"group": "MINI"}).json()["reasons"]}
    assert reasons["тарифы и комиссии"]["n_conversations"] == 2
    assert reasons["тарифы и комиссии"]["resolved_share"] == 0.5
    assert reasons["тарифы и комиссии"]["n_unanswered_questions"] == 2
    assert reasons["пополнение"]["gross_count"] == 1

    s = c.get("/api/insights/summary", params={"group": "MINI"}).json()
    assert s["unanswered_questions"] == 3
    assert s["top_unanswered_topics"][0] == {"topic": "тарифы и комиссии", "n_questions": 2, "n_conversations": 1}
