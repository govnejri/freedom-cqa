"""Тьютор (cqa/features/tutor.py, API /tutor/*): профиль fake, без сети, временная БД."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqa.checklist.store import seed
from cqa.core.runner import create_run, recompute_final, run_sync
from cqa.core.signals import save_signals
from cqa.db.conn import now_iso
from cqa.ingest.service import ingest_records
from cqa.ingest.store import list_uids
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT, KZ = "MINI:mini-ru-chat", "MINI:mini-kz-chat"


def _set(con, eid: int, cid: str, verdict: str, how_to_fix: str = "") -> None:
    con.execute("UPDATE criterion_results SET verdict=?, category='', how_to_fix=? WHERE evaluation_id=? AND criterion_id=?",
                (verdict, how_to_fix, eid, cid))


def _signals(con, eid: int, requests: list[dict], reason: str) -> None:
    """Сигналы как будто от модели: полный ответ — в журнале, строка — в signals."""
    raw = {"requests": requests, "contact_reason": reason, "resolved": "да", "churn_mid": "", "churn_risk": "нет",
           "missed_opportunity": ""}
    call = con.execute(
        "INSERT INTO llm_calls(ts, purpose, profile, provider, model, request_json, response_text, status)"
        " VALUES (?, 'signals', 'fake', 'fake', 'fake', '{}', ?, 'ok')", (now_iso(), json.dumps(raw, ensure_ascii=False))
    ).lastrowid
    save_signals(con, eid, {"contact_reason": reason, "resolved": True, "unanswered": [], "churn_risk": False,
                            "missed_opportunity": ""}, call)


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    """MINI оценена fake. Казахский чат — лучший (04 = 20, без грубых), в нём закрытый вопрос про пополнение.
    Русский чат (оператор Ольга) — с замечаниями по 04 и 05."""
    import cqa.api.app as app_mod

    con = tmp_db
    s = get_settings()
    monkeypatch.setattr(s, "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")
    vid = seed(con, s.checklist_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ingest_records(con, "MINI", payload["data"]["records"], "file")
    assert run_sync(con, create_run(con, uids=list_uids(con, "MINI"), version_id=vid, profile_name="fake"))[
        "units_failed"] == 0
    ev = {r["uid"]: r["current_evaluation_id"] for r in con.execute("SELECT uid, current_evaluation_id FROM conversations")}
    _set(con, ev[KZ], "04", "20")
    _set(con, ev[KZ], "08", "Нет")
    _set(con, ev[CHAT], "04", "10", "Назовите тариф и где он указан в приложении.")
    _set(con, ev[CHAT], "05", "Нет", "Посочувствуйте клиенту.")
    _set(con, ev[CHAT], "08", "Нет")
    for eid in ev.values():
        recompute_final(con, eid)
    _signals(con, ev[KZ], [{"mid": "m1", "text": "Как пополнить счёт?", "answer_mid": "m8", "status": "закрыт"}],
             "пополнение")
    _signals(con, ev[CHAT], [{"mid": "m1", "text": "Где посмотреть тариф?", "answer_mid": "m10", "status": "закрыт"}],
             "тарифы и комиссии")
    with TestClient(app_mod.create_app(fake=True, worker=False)) as client:
        yield {"con": con, "client": client, "ev": ev}


def test_stems_and_rank():
    from cqa.features.tutor import rank, stems

    assert stems("подать вывод после изменения анкеты") == {"пода", "вывод", "посл", "изменени", "анкет"}
    pairs = [
        {"uid": "A", "question_mid": "m1", "question": "Можно ли уже подавать вывод?", "client_text": "",
         "answer_text": "Дождитесь исполнения", "topic": "вывод средств"},
        {"uid": "B", "question_mid": "m1", "question": "Где тариф?", "client_text": "", "answer_text": "В профиле",
         "topic": "тарифы и комиссии"},
    ]
    r = rank(pairs, "Когда клиенту снова подать вывод после изменения анкеты?")
    assert [p["uid"] for p in r] == ["A"] and set(r[0]["matched"]) == {"пода", "вывод"}


def test_mentioned_criteria(checklist):
    from cqa.features.tutor import mentioned_criteria

    assert mentioned_criteria(checklist, "Почему мне снизили 04?") == {"04"}
    assert mentioned_criteria(checklist, "критерий 7, завершение") == {"07"}
    assert mentioned_criteria(checklist, "почему снизили эмпатию") == {"05"}
    assert mentioned_criteria(checklist, "как отвечать про вывод средств в 2026 году") == set()


def test_best_answers_base(env):
    client = env["client"]
    r = client.get("/api/tutor/best-answers").json()
    # только лучший диалог (04 = верхняя ступень, без грубых): казахский чат; русский с 04 = 10 не попадает
    assert {p["uid"] for p in r["items"]} == {KZ}
    p = r["items"][0]
    assert p["question_mid"] == "m1" and p["answer_mid"] == "m8" and p["topic"] == "пополнение"
    assert p["answer_text"] == "Мобильді қосымшада «Толтыру» бөлімін ашыңыз."
    assert r["topics"] == [{"topic": "пополнение", "n": 1}]
    assert client.get("/api/tutor/best-answers", params={"topic": "пополнение"}).json()["total"] == 1
    assert client.get("/api/tutor/best-answers", params={"topic": "тарифы и комиссии"}).json()["total"] == 0
    assert client.get("/api/tutor/best-answers", params={"q": "как пополнить счёт"}).json()["total"] == 1


def test_ask_sources_point_to_real_messages(env):
    con, client = env["con"], env["client"]
    r = client.post("/api/tutor/ask", json={"question": "Почему мне снизили 04 и как отвечать, если клиент "
                                                        "спрашивает, как пополнить счёт?", "operator": "Ольга"})
    assert r.status_code == 200, r.text
    r = r.json()
    assert r["answer"] and r["operator"] == "Ольга" and "04" in r["mentioned_criteria"]
    kinds = {s["kind"] for s in r["sources"]}
    assert {"own", "best_answer", "checklist"} <= kinds
    for s in r["sources"]:
        assert s["ref"].startswith("S") and s["text"]
        if s["mid"]:
            row = con.execute("SELECT speaker, text FROM messages WHERE uid=? AND mid=?", (s["uid"], s["mid"])).fetchone()
            assert row is not None, s
            if s["kind"] == "best_answer":
                assert row["speaker"] == "operator" and row["text"] == s["text"]
        if s["kind"] == "own":
            assert s["uid"] == CHAT
        if s["kind"] == "checklist":
            assert s["uid"] is None and s["mid"] is None
    own04 = next(s for s in r["sources"] if s["kind"] == "own" and s["criterion_id"] == "04")
    assert "Как надо было: Назовите тариф" in own04["text"]
    # журнал: purpose=tutor
    assert con.execute("SELECT COUNT(*) FROM llm_calls WHERE purpose='tutor'").fetchone()[0] == 1


def test_ask_cited_sources_only(env, monkeypatch):
    """Модель сослалась на [S#] → в sources только эти элементы, cited=True."""
    from cqa.features import tutor
    from cqa.llm import gateway

    real = gateway.complete
    seen = {}

    async def fake_complete(req, profile, ctx=None, con=None):
        resp = await real(req, profile, ctx, con)
        text = req.messages[-1].content
        ref = next(line.split("]")[0][1:] for line in text.splitlines() if "оператор m8" in line)
        seen["ref"] = ref
        resp.parsed = {"sources": [], "answer": f"Направьте клиента в раздел «Толтыру» [{ref}].", "found": "да"}
        return resp

    monkeypatch.setattr(tutor.gateway, "complete", fake_complete)
    r = env["client"].post("/api/tutor/ask", json={"question": "Как подсказать, где пополнить счёт?"}).json()
    assert [s["ref"] for s in r["sources"]] == [seen["ref"]]
    s = r["sources"][0]
    assert s["cited"] is True and s["kind"] == "best_answer" and (s["uid"], s["mid"]) == (KZ, "m8")


def test_ask_uid_and_errors(env):
    client = env["client"]
    r = client.post("/api/tutor/ask", json={"question": "Почему снизили?", "uid": CHAT}).json()
    assert r["operator"] == "Ольга" and r["uid"] == CHAT        # оператор — из диалога
    assert any(s["kind"] == "own" and s["uid"] == CHAT for s in r["sources"])
    assert client.post("/api/tutor/ask", json={"question": "x", "uid": "MINI:nope"}).status_code == 404
    assert client.post("/api/tutor/ask", json={"question": ""}).status_code == 422
