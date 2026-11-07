"""Ассистент руководителя: представления (исключение мутантов), поиск, полный вопрос с подменой ответов модели,
API /api/assistant. Профиль fake, ответы модели — через monkeypatch FakeProvider.complete; временная БД."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqa.api.schemas import AppealCreateDTO, DecisionCreateDTO
from cqa.appeals.service import create_appeal, decide
from cqa.checklist.store import seed
from cqa.core.runner import create_run, run_sync
from cqa.features import assistant as A
from cqa.ingest.service import ingest_records
from cqa.llm.base import LLMResponse, Usage
from cqa.llm.fake import FakeProvider
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT, KZ, CALL = "MINI:mini-ru-chat", "MINI:mini-kz-chat", "MINI:mini-ru-call"
MINI = {CHAT, KZ, CALL}


@pytest.fixture
def db(tmp_db):
    """MINI (3 диалога) + мутанты: AUTOTEST и MUT (parent_uid задан), группа TRAINER; всё оценено (fake) и с сигналами."""
    con = tmp_db
    vid = seed(con, get_settings().checklist_path)
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["records"]
    for group in ("MINI", "AUTOTEST", "MUT", "TRAINER"):
        ingest_records(con, group, records, "file")
    con.execute("UPDATE conversations SET parent_uid = 'MINI:' || external_id WHERE group_id IN ('AUTOTEST', 'MUT')")
    uids = [r[0] for r in con.execute("SELECT uid FROM conversations")]
    assert run_sync(con, create_run(con, uids=uids, version_id=vid, profile_name="fake"))["units_failed"] == 0
    for uid, eid, ext in con.execute("SELECT uid, current_evaluation_id, external_id FROM conversations").fetchall():
        client_mid = con.execute("SELECT mid FROM messages WHERE uid=? AND speaker='client' ORDER BY idx LIMIT 1",
                                 (uid,)).fetchone()[0]
        topic = {"mini-ru-chat": "тарифы и комиссии", "mini-kz-chat": "пополнение", "mini-ru-call": "прочее"}[ext]
        unanswered = [{"text": f"вопрос клиента {ext}", "mid": client_mid, "status": "открыт"}]
        con.execute("INSERT INTO signals(evaluation_id, contact_reason, resolved, unanswered_json, churn_risk,"
                    " missed_opportunity) VALUES (?,?,?,?,?,?)",
                    (eid, topic, int(ext != "mini-ru-call"), json.dumps(unanswered, ensure_ascii=False),
                     int(ext == "mini-ru-call"), "предложить приложение" if ext == "mini-ru-chat" else ""))
    return con


def _route(kind="numbers", **kw):
    out = {"kind": kind, "language": "ru", "question_ru": "", "topics": [], "criteria": [],
           "terms_ru": [], "terms_kz": [], "terms_en": []}
    out.update(kw)
    return out


def _install(monkeypatch, route: dict, sqls: list[str], answer) -> list[dict]:
    """Подмена ответа модели: шаг определяется по схеме запроса (роутер / SQL / ответ)."""
    calls: list[dict] = []
    sql_iter = iter(sqls)

    async def complete(self, req, profile):
        props = (req.json_schema or {}).get("properties", {})
        text = "\n".join(m.content for m in req.messages)
        if "kind" in props:
            step, out = "route", route
        elif "sql" in props:
            step, out = "sql", {"plan": "тест", "sql": next(sql_iter)}
        elif "answer" in props:
            step, out = "answer", (answer(text) if callable(answer) else answer)
        else:  # pragma: no cover
            raise AssertionError(f"неожиданная схема {list(props)}")
        calls.append({"step": step, "text": text, "roles": [m.role for m in req.messages]})
        return LLMResponse(text=json.dumps(out, ensure_ascii=False), usage=Usage(prompt_tokens=100, completion_tokens=20),
                           finish_reason="STOP", model=profile.model, provider="fake")

    monkeypatch.setattr(FakeProvider, "complete", complete)
    return calls


def _ask(con, question, group=None):
    return asyncio.run(A.ask(question, group, con=con, db_path=get_settings().db_path,
                             router_profile="fake", strong_profile="fake"))


# ---------------------------------------------------------------- представления и поиск

def test_views_exclude_mutants_and_service_groups(db):
    sb = A.open_sandbox(get_settings().db_path)
    rd = A.open_reader(get_settings().db_path)
    try:
        for view in A.VIEWS[:3]:
            uids = {r[0] for r in sb.execute(f"SELECT DISTINCT uid FROM {view}")}
            assert uids == MINI, view
        assert sb.execute("SELECT COUNT(*) FROM v_criteria").fetchone()[0] == 3 * 8
        row = sb.execute("SELECT contact_reason, resolved, churn_risk, n_unanswered, missed_opportunity, evaluated"
                         " FROM v_dialogs WHERE uid=?", (CALL,)).fetchone()
        assert row == ("прочее", 0, 1, 1, None, 1)
        # FTS по слову, которое есть во всех копиях диалога, отдаёт только исходный диалог
        hits = A.fts_hits(rd, ["тариф"])
        assert hits and {h["uid"] for h in hits} == {CHAT}
        frags = A.search(sb, rd, {"terms": ["тариф"], "topics": ["пополнение"]})
        assert {f["uid"] for f in frags} == {CHAT, KZ}
        assert any(f["source"].startswith("реплика") and f["uid"] == CHAT for f in frags)
        assert all(f["uid"] in MINI for f in frags)
    finally:
        sb.close()
        rd.close()
    sb = A.open_sandbox(get_settings().db_path, group="MINI")
    try:
        assert sb.execute("SELECT COUNT(*) FROM v_dialogs").fetchone()[0] == 3
    finally:
        sb.close()


def test_views_final_verdict_from_appeal_decision(db):
    eid = db.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (CHAT,)).fetchone()[0]
    model = db.execute("SELECT verdict FROM criterion_results WHERE evaluation_id=? AND criterion_id='01'",
                       (eid,)).fetchone()[0]
    new = "Нет" if model == "Да" else "Да"
    a = create_appeal(db, AppealCreateDTO(evaluation_id=eid, criterion_id="01", comment="оператор представился"))
    decide(db, a.id, DecisionCreateDTO(action="change", new_verdict=new, reason="пересмотрено"))
    sb = A.open_sandbox(get_settings().db_path)
    try:
        r = sb.execute("SELECT model_verdict, final_verdict, changed_by_appeal, failed, title FROM v_criteria"
                       " WHERE uid=? AND criterion_id='01'", (CHAT,)).fetchone()
        assert r == (model, new, 1, int(new == "Нет"), "Установление контакта")
        ap = sb.execute("SELECT uid, criterion_id, decision, new_verdict, status FROM v_appeals").fetchall()
        assert ap == [(CHAT, "01", "change", new, "decided")]
        total = sb.execute("SELECT final_total FROM v_dialogs WHERE uid=?", (CHAT,)).fetchone()[0]
        assert total == db.execute("SELECT final_total FROM evaluations WHERE id=?", (eid,)).fetchone()[0]
    finally:
        sb.close()


# ---------------------------------------------------------------- вопрос целиком

def test_ask_numbers_sources_exist(db, monkeypatch):
    sql = ("SELECT operator, COUNT(*) AS dialogs, json_group_array(uid) AS uids FROM v_criteria"
           " WHERE criterion_id = '03' GROUP BY operator ORDER BY dialogs DESC")
    answer = {
        "answer": "Всего 3 диалога. См. [mini-ru-chat](MINI:mini-ru-chat), [выдумка](MINI:nope), "
                  "[мутант](AUTOTEST:mini-ru-chat) и mini-kz-chat. Ещё 42 нарушения.",
        "sources": [
            {"uid": CHAT, "mid": "m7", "why": "раскрыт тариф"},
            {"uid": "MINI:nope", "mid": "", "why": "нет такого"},
            {"uid": "AUTOTEST:mini-ru-chat", "mid": "m1", "why": "мутант"},
            {"uid": "mini-kz-chat", "mid": "m999", "why": "external_id и несуществующая реплика"},
        ],
    }
    calls = _install(monkeypatch, _route("numbers", criteria=["03"]), [sql], answer)
    r = _ask(db, "Кто чаще всех нарушает идентификацию?")

    assert [c["step"] for c in calls] == ["route", "sql", "answer"]
    assert r["kind"] == "numbers" and r["sql"] == sql and r["columns"] == ["operator", "dialogs", "uids"]
    assert 1 <= len(r["rows"]) <= A.ROWS_OUT and r["row_count"] == len(r["rows"])
    # ссылки и источники — только существующие диалоги из данных ответа, реплики — существующие
    for s in r["sources"]:
        assert s["uid"] in MINI
        ext = db.execute("SELECT external_id FROM conversations WHERE uid=?", (s["uid"],)).fetchone()[0]
        assert s["external_id"] == ext
        if s["mid"]:
            assert db.execute("SELECT 1 FROM messages WHERE uid=? AND mid=?", (s["uid"], s["mid"])).fetchone()
    by_uid = {s["uid"]: s for s in r["sources"]}
    assert set(by_uid) == {CHAT, KZ}
    assert by_uid[CHAT]["mid"] == "m7" and by_uid[KZ]["mid"] is None
    assert "(MINI:nope)" not in r["answer"] and "(AUTOTEST:" not in r["answer"]
    assert "[mini-kz-chat](MINI:mini-kz-chat)" in r["answer"]            # голый external_id стал ссылкой
    # «3» есть в результате SQL (dialogs), «42» — нет
    assert [w for w in r["warnings"] if w.startswith("числа")] == ["числа не найдены в результате запроса: 42"]
    assert "отброшено источников вне данных: 2" in r["warnings"]
    # в промпт ответа попали результат SQL и пояснения критерия 03, но не мутанты
    prompt = calls[-1]["text"]
    assert "<result" in prompt and "«Идентификация»" in prompt and "AUTOTEST:" not in prompt and "MUT:" not in prompt
    # журнал: три вызова с назначением ассистента
    purposes = [db.execute("SELECT purpose FROM llm_calls WHERE id=?", (i,)).fetchone()[0] for i in r["llm_call_ids"]]
    assert purposes == ["assistant_route", "assistant_sql", "assistant_answer"]
    assert r["cost_usd"] == 0.0


def test_ask_sql_retry_with_error_text(db, monkeypatch):
    good = "SELECT contact_reason, COUNT(*) AS dialogs FROM v_dialogs GROUP BY contact_reason ORDER BY dialogs DESC"
    calls = _install(monkeypatch, _route("numbers"), ["SELECT * FROM conversations", good],
                     {"answer": "По темам — по 1 диалогу.", "sources": []})
    r = _ask(db, "Сколько диалогов по темам?")
    sql_calls = [c for c in calls if c["step"] == "sql"]
    assert len(sql_calls) == 2
    assert sql_calls[1]["roles"][-2:] == ["assistant", "user"]
    assert "таблица conversations недоступна" in sql_calls[1]["text"]
    assert r["sql"] == good and r["row_count"] == 3 and not any("SQL" in w for w in r["warnings"])


def test_ask_sql_execution_error_then_give_up(db, monkeypatch):
    calls = _install(monkeypatch, _route("numbers"), ["SELECT nope FROM v_dialogs", "SELECT nope2 FROM v_dialogs"],
                     {"answer": "Данных нет.", "sources": []})
    r = _ask(db, "Сколько чего-то?")
    sql_calls = [c for c in calls if c["step"] == "sql"]
    assert len(sql_calls) == 2 and "no such column: nope" in sql_calls[1]["text"]
    assert r["rows"] is None and any("SQL не получен" in w for w in r["warnings"])
    assert "запрос не выполнен" in calls[-1]["text"]


def test_ask_meaning_uses_search_and_facts(db, monkeypatch):
    def answer(prompt: str) -> dict:
        assert "<fragments>" in prompt and "mini-ru-chat" in prompt and "AUTOTEST:" not in prompt
        return {"answer": "Клиенты спрашивают про тариф: [mini-ru-chat](MINI:mini-ru-chat).",
                "sources": [{"uid": CHAT, "mid": "m1", "why": "вопрос про тариф"}]}

    calls = _install(monkeypatch, _route("meaning", terms_ru=["тариф"], terms_en=["tariff"]), [], answer)
    r = _ask(db, "Почему клиенты спрашивают про тарифы?")
    assert [c["step"] for c in calls] == ["route", "answer"]
    assert r["kind"] == "meaning" and r["route"]["terms"] == ["тариф", "tariff"]
    assert r["sql"] and "v_dialogs" in r["sql"]                    # цифры — из служебного SQL по представлениям
    assert r["rows"][0]["scope"] == "найденные диалоги" and r["rows"][0]["dialogs"] == 1
    assert r["sources"] == [{"uid": CHAT, "external_id": "mini-ru-chat", "mid": "m1", "why": "вопрос про тариф"}]


def test_ask_mixed_searches_only_found_dialogs(db, monkeypatch):
    sql = "SELECT uid, external_id, churn_risk FROM v_dialogs WHERE churn_risk = 1"

    def answer(prompt: str) -> dict:
        frag = prompt.split("<fragments>")[1].split("</fragments>")[0]
        assert "mini-ru-call" in frag and "mini-ru-chat" not in frag and "mini-kz-chat" not in frag
        return {"answer": "Риск ухода — [mini-ru-call](MINI:mini-ru-call).", "sources": []}

    calls = _install(monkeypatch, _route("mixed", terms_ru=["заявк", "тариф"]), [sql], answer)
    r = _ask(db, "Где мы теряем клиентов и почему?")
    assert [c["step"] for c in calls] == ["route", "sql", "answer"]
    assert "одна строка — один диалог" in calls[1]["text"]
    assert r["sources"] == [{"uid": CALL, "external_id": "mini-ru-call", "mid": None, "why": "упомянут в ответе"}]


def test_ask_router_failure_falls_back(db, monkeypatch):
    async def broken(self, req, profile):
        return LLMResponse(text="", status="http_error", error="503", model="fake", provider="fake")

    monkeypatch.setattr(FakeProvider, "complete", broken)
    r = _ask(db, "Почему клиенты недовольны тарифом?")
    assert r["kind"] == "meaning" and any("роутер" in w for w in r["warnings"])
    assert r["answer"] and all(s["uid"] in MINI for s in r["sources"])


# ---------------------------------------------------------------- вспомогательные функции

def test_fix_links_and_numbers():
    known = {CHAT: "mini-ru-chat", KZ: "mini-kz-chat"}
    text, linked = A.fix_links("A [x](mini-ru-chat), [y](DEV:zzz), mini-kz-chat; [mini-ru-chat](MINI:mini-ru-chat)", known)
    assert text == "A [x](MINI:mini-ru-chat), y, [mini-kz-chat](MINI:mini-kz-chat); [mini-ru-chat](MINI:mini-ru-chat)"
    assert linked == [CHAT, KZ]
    pool = A._pool([{"n": 3, "avg": 63.08, "share": 0.667}])
    assert A.unverified_numbers("3 диалога, средний балл 63,1, доля 66,7%, критерий 03, [p1-ru-007](DEV:p1-ru-007)",
                                pool) == []
    assert A.unverified_numbers("в 5 диалогах из 3", pool) == ["5"]


# ---------------------------------------------------------------- API

@pytest.fixture
def client(db, tmp_path, monkeypatch):
    import cqa.api.app as app_mod

    monkeypatch.setattr(get_settings(), "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")
    with TestClient(app_mod.create_app(fake=True, worker=False)) as c:
        yield c


def test_api_ask_and_examples(client, monkeypatch):
    sql = "SELECT uid, external_id, final_total FROM v_dialogs ORDER BY final_total LIMIT 2"
    _install(monkeypatch, _route("numbers"), [sql],
             {"answer": "Худший — [mini-ru-call](MINI:mini-ru-call).", "sources": [{"uid": CALL, "mid": "", "why": "x"}]})
    r = client.post("/api/assistant/ask", json={"question": "  Какие диалоги хуже всех? ", "group": "MINI"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["question"] == "Какие диалоги хуже всех?" and data["kind"] == "numbers" and data["sql"] == sql
    assert data["row_count"] == 2 and len(data["rows"]) == 2 and set(data["rows"][0]) == {"uid", "external_id", "final_total"}
    assert data["sources"][0]["uid"] == CALL and len(data["llm_call_ids"]) == 3
    assert set(data) >= {"answer", "kind", "sql", "rows", "sources", "cost_usd", "llm_call_ids", "warnings"}

    assert client.post("/api/assistant/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/assistant/ask", json={"question": "   "}).status_code == 422

    ex = client.get("/api/assistant/examples")
    assert ex.status_code == 200
    items = ex.json()
    assert len(items) == 5 and {i["kind"] for i in items} <= {"numbers", "meaning", "mixed"}
    assert all(i["question"].endswith("?") and i["label"] for i in items)
