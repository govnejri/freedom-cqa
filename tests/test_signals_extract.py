"""Сигналы диалога (cqa/core/signals.py): схема, запись, проверка mid, группировка вопросов без ответа. Профиль fake."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cqa.checklist.store import seed
from cqa.core.runner import create_run, run_sync
from cqa.core.signals import (
    FIELDS,
    REQUEST_FIELDS,
    extract_signals,
    get_signals,
    normalize_signals,
    run_signals,
    signals_schema,
    unanswered_by_topic,
)
from cqa.ingest.service import ingest_records
from cqa.ingest.store import get_conversation, list_uids
from cqa.llm.base import LLMResponse, Usage
from cqa.llm.fake import FakeProvider
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT, KZ, CALL = "T:mini-ru-chat", "T:mini-kz-chat", "T:mini-ru-call"

# Ответы «модели» по маркеру из транскрипта. Ошибки намеренные: несуществующие mid, ответ бота, ответ на удержании.
CRAFTED = {
    "Хочу узнать статус заявки": {
        "requests": [
            {"mid": "m9", "text": "Какой статус заявки?", "answer_mid": "m12", "status": "закрыт"},   # ответ на удержании
            {"mid": "m99", "text": "Выдуманный вопрос", "answer_mid": "", "status": "открыт"},        # нет реплики
            {"mid": "m2", "text": "Вопрос из реплики бота", "answer_mid": "", "status": "открыт"},    # не клиент
        ],
        "contact_reason": "поиск бумаг и торговля", "resolved": "нет",
        "churn_mid": "", "churn_risk": "нет", "missed_opportunity": "",
    },
    "Подскажите, где посмотреть мой тариф": {
        "requests": [
            {"mid": "1", "text": "Где посмотреть тариф?", "answer_mid": "M10", "status": "закрыт"},
            {"mid": "m1", "text": "Как сменить тариф?", "answer_mid": "m2", "status": "частично"},    # ответ бота
            {"mid": "m6", "text": "Где тариф в приложении?", "answer_mid": "m50", "status": "закрыт"}, # нет реплики
            {"mid": "m6", "text": "Есть ли тариф дешевле?", "answer_mid": "m10", "status": "частично"},
        ],
        "contact_reason": "Тарифы и комиссии", "resolved": "да",
        "churn_mid": "m500", "churn_risk": "да", "missed_opportunity": "Не предложил сравнить тарифы.",
    },
    "Шотты қалай толтырамын": {
        "requests": [{"mid": "m1", "text": "Как пополнить счёт?", "answer_mid": "m8", "status": "закрыт"}],
        "contact_reason": "неизвестная тема", "resolved": "да",
        "churn_mid": "", "churn_risk": "нет", "missed_opportunity": "",
    },
}


def _setup(con) -> dict[str, int]:
    vid = seed(con, get_settings().checklist_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ingest_records(con, "T", payload["data"]["records"], "file")
    uids = list_uids(con, "T")
    run = run_sync(con, create_run(con, uids=uids, version_id=vid, profile_name="fake"))
    assert run["units_failed"] == 0
    return {r["uid"]: r["current_evaluation_id"]
            for r in con.execute("SELECT uid, current_evaluation_id FROM conversations WHERE group_id='T'")}


@pytest.fixture
def crafted(monkeypatch):
    """Подмена fake только для запросов сигналов (в схеме есть requests): ответ по маркеру из диалога."""
    orig = FakeProvider.complete

    async def complete(self, req, profile):
        props = (req.json_schema or {}).get("properties", {})
        if "requests" in props:
            full = "\n".join(m.content for m in req.messages)
            for marker, out in CRAFTED.items():
                if marker in full:
                    return LLMResponse(text=json.dumps(out, ensure_ascii=False), finish_reason="STOP",
                                       usage=Usage(prompt_tokens=100, completion_tokens=50),
                                       model=profile.model, provider="fake")
        return await orig(self, req, profile)

    monkeypatch.setattr(FakeProvider, "complete", complete)


def _types(node, acc: set[str]) -> set[str]:
    if isinstance(node, dict):
        if "type" in node:
            acc.add(node["type"])
        for v in node.values():
            _types(v, acc)
    elif isinstance(node, list):
        for v in node:
            _types(v, acc)
    return acc


def test_schema_strict(checklist):
    topics = checklist.signals.topics
    schema, ordering = signals_schema(topics)
    assert ordering == list(FIELDS) == list(schema["properties"]) == schema["required"]
    assert schema["additionalProperties"] is False
    item = schema["properties"]["requests"]["items"]
    assert item["required"] == list(REQUEST_FIELDS) == list(item["properties"])
    assert item["additionalProperties"] is False
    assert item["properties"]["status"]["enum"] == ["закрыт", "частично", "открыт"]
    assert schema["properties"]["contact_reason"]["enum"] == topics
    assert {"прочее", "вывод средств"} <= set(topics)
    assert _types(schema, set()) <= {"object", "array", "string"}


def test_extract_writes_row_and_journal(tmp_db, checklist):
    con = tmp_db
    evals = _setup(con)
    res = extract_signals(con, evals[CHAT], "fake")
    assert res["status"] == "ok" and res["uid"] == CHAT
    assert res["contact_reason"] in checklist.signals.topics
    assert set(res) >= {"requests", "unanswered", "resolved", "churn_risk", "churn_mid", "missed_opportunity"}
    row = con.execute("SELECT * FROM signals WHERE evaluation_id=?", (evals[CHAT],)).fetchone()
    assert row["contact_reason"] == res["contact_reason"]
    assert json.loads(row["unanswered_json"]) == res["unanswered"]
    call = con.execute("SELECT * FROM llm_calls WHERE id=?", (row["llm_call_id"],)).fetchone()
    assert call["purpose"] == "signals" and call["uid"] == CHAT and call["evaluation_id"] == evals[CHAT]
    req = json.loads(call["request_json"])
    assert req["property_ordering"] == list(FIELDS)
    assert "<transcript>" in req["messages"][1]["content"]
    full = get_signals(con, evals[CHAT])
    assert full["raw"] is not None and full["requests"] == res["requests"]


def test_mid_checks(tmp_db, crafted):
    con = tmp_db
    evals = _setup(con)
    call = extract_signals(con, evals[CALL], "fake")
    assert [r["mid"] for r in call["requests"]] == ["m9"]               # m99 и реплика бота отброшены
    assert call["requests"][0] == {"mid": "m9", "text": "Какой статус заявки?", "answer_mid": "", "status": "открыт"}
    reasons = {(d["field"], d["reason"]) for d in call["dropped"]}
    assert ("requests.mid", "нет такой реплики") in reasons
    assert ("requests.mid", "не реплика клиента") in reasons
    assert ("requests.answer_mid", "ответ на участке удержания") in reasons

    chat = extract_signals(con, evals[CHAT], "fake")
    by_text = {r["text"]: r for r in chat["requests"]}
    assert by_text["Где посмотреть тариф?"] == {"mid": "m1", "text": "Где посмотреть тариф?", "answer_mid": "m10",
                                                "status": "закрыт"}
    assert by_text["Как сменить тариф?"]["status"] == "открыт"          # ответил бот
    assert by_text["Где тариф в приложении?"]["answer_mid"] == "" and by_text["Где тариф в приложении?"]["status"] == "открыт"
    assert by_text["Есть ли тариф дешевле?"]["status"] == "частично"
    assert chat["contact_reason"] == "тарифы и комиссии"               # регистр приведён к теме чек-листа
    assert chat["churn_risk"] is True and chat["churn_mid"] == ""       # несуществующая реплика отброшена
    assert {u["text"] for u in chat["unanswered"]} == {"Как сменить тариф?", "Где тариф в приложении?",
                                                       "Есть ли тариф дешевле?"}
    row = con.execute("SELECT unanswered_json FROM signals WHERE evaluation_id=?", (evals[CHAT],)).fetchone()
    assert all(set(u) == {"text", "mid", "status"} for u in json.loads(row[0]))

    kz = extract_signals(con, evals[KZ], "fake")
    assert kz["contact_reason"] == "прочее" and kz["unanswered"] == []  # неизвестная тема → «прочее»


def test_normalize_handles_garbage(tmp_db):
    con = tmp_db
    _setup(con)
    conv = get_conversation(con, CHAT)
    out = normalize_signals({"requests": "не список", "resolved": None}, conv, ["прочее"])
    assert out["requests"] == [] and out["resolved"] is False and out["contact_reason"] == "прочее"
    assert normalize_signals(None, conv, [])["contact_reason"] == "прочее"


def test_run_skips_existing_and_groups_unanswered(tmp_db, crafted):
    con = tmp_db
    evals = _setup(con)
    r1 = asyncio.run(run_signals(con, group="T", profile="fake"))
    assert (r1["total"], r1["done"], r1["failed"], r1["skipped"]) == (3, 3, 0, 0)
    n_calls = con.execute("SELECT COUNT(*) FROM llm_calls WHERE purpose='signals'").fetchone()[0]
    assert n_calls == 3

    r2 = asyncio.run(run_signals(con, group="T", profile="fake"))
    assert (r2["total"], r2["done"], r2["skipped"]) == (0, 0, 3)
    assert con.execute("SELECT COUNT(*) FROM llm_calls WHERE purpose='signals'").fetchone()[0] == n_calls

    un = unanswered_by_topic(con, "T")
    assert un["n_with_signals"] == 3 and un["total_questions"] == 4 and un["n_conversations"] == 2
    topics = {t["topic"]: t for t in un["topics"]}
    assert list(topics) == ["тарифы и комиссии", "поиск бумаг и торговля"]   # по числу вопросов
    tariffs = topics["тарифы и комиссии"]
    assert tariffs["n_questions"] == 3 and tariffs["n_conversations"] == 1
    assert {q["uid"] for q in tariffs["questions"]} == {CHAT}
    assert {q["status"] for q in tariffs["questions"]} == {"открыт", "частично"}
    total = con.execute("SELECT COALESCE(final_total, total) FROM evaluations WHERE id=?", (evals[CHAT],)).fetchone()[0]
    assert tariffs["avg_total"] == pytest.approx(round(total, 2))
    q = next(q for q in topics["поиск бумаг и торговля"]["questions"])
    assert (q["uid"], q["mid"], q["message_text"]) == (CALL, "m9", "Хочу узнать статус заявки.")

    only = unanswered_by_topic(con, "T", topic="прочее")              # тема без вопросов — пустая группа
    assert [t["topic"] for t in only["topics"]] == ["прочее"] and only["topics"][0]["n_questions"] == 0
    assert only["topics"][0]["n_conversations_total"] == 1
