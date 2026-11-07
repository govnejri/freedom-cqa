"""Второе мнение ИИ в апелляции: слепая проверка (исходного вердикта и обоснования в запросе нет),
appeal_supported считает код, запись в appeals.ai_opinion_json / ai_call_id."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cqa.api.schemas import AppealCreateDTO
from cqa.appeals.service import create_appeal, get_appeal
from cqa.core import crosscheck as cc

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT = "MINI:mini-ru-chat"
SECRET_EXPL = "ИСХОДНОЕ-ОБОСНОВАНИЕ-7f3a"
SECRET_REASON = "ИСХОДНЫЙ-РАЗБОР-7f3a"
SECRET_FIX = "ИСХОДНЫЙ-СОВЕТ-7f3a"
SECRET_QUOTE = "ИСХОДНАЯ-ЦИТАТА-7f3a"


@pytest.fixture
def evaluated(tmp_db):
    from cqa.checklist.store import get_active, seed
    from cqa.core.runner import create_run, run_sync
    from cqa.ingest.service import ingest_payload
    from cqa.settings import get_settings

    seed(tmp_db, get_settings().checklist_path)
    ingest_payload(tmp_db, json.loads(MINI.read_text(encoding="utf-8")), "file")
    vid = get_active(tmp_db)[1]["id"]
    assert run_sync(tmp_db, create_run(tmp_db, uids=[CHAT], version_id=vid, profile_name="fake"))["status"] == "done"
    ev = tmp_db.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (CHAT,)).fetchone()[0]
    return tmp_db, ev


def _set_original(con, ev: int, cid: str, verdict: str, tag: str = "") -> None:
    con.execute(
        "UPDATE criterion_results SET verdict=?, explanation=?, reasoning=?, how_to_fix=?, evidence_json=?"
        " WHERE evaluation_id=? AND criterion_id=?",
        (verdict, SECRET_EXPL + tag, SECRET_REASON + tag, SECRET_FIX + tag,
         json.dumps([{"id": "m3", "quote": SECRET_QUOTE + tag}], ensure_ascii=False), ev, cid))


def _operator_mid(con) -> tuple[str, str]:
    r = con.execute("SELECT mid, text FROM messages WHERE uid=? AND speaker='operator' ORDER BY idx LIMIT 1",
                    (CHAT,)).fetchone()
    return r["mid"], r["text"]


def _request(con, call_id: int) -> str:
    return con.execute("SELECT request_json FROM llm_calls WHERE id=?", (call_id,)).fetchone()[0]


def test_second_opinion_is_blind(evaluated):
    con, ev = evaluated
    _set_original(con, ev, "01", "Нет")
    mid, text = _operator_mid(con)
    comment = "Я поздоровался и назвал имя в первой же реплике."
    appeal = create_appeal(con, AppealCreateDTO(evaluation_id=ev, criterion_id="01", comment=comment,
                                                counter_evidence=[mid]))

    op = asyncio.run(cc.second_opinion(con, appeal.id, "fake"))
    row = con.execute("SELECT ai_opinion_json, ai_call_id FROM appeals WHERE id=?", (appeal.id,)).fetchone()
    assert row["ai_call_id"] == op["ai_call_id"] and row["ai_call_id"] is not None
    call = con.execute("SELECT purpose, criterion_id FROM llm_calls WHERE id=?", (row["ai_call_id"],)).fetchone()
    assert call["purpose"] == "second_opinion" and call["criterion_id"] == "01"

    req = _request(con, row["ai_call_id"])
    # исходного обоснования, разбора, совета и доказательств системы в запросе нет
    for secret in (SECRET_EXPL, SECRET_REASON, SECRET_FIX, SECRET_QUOTE):
        assert secret not in req
    assert "Исходный вердикт" not in req and "вердикт системы" not in req
    # комментарий оператора и его контрдоказательство — есть
    assert comment in req
    appeal_block = json.loads(req)["messages"][-1]["content"]
    assert appeal_block.startswith("<appeal>") and f"{mid} |" in appeal_block and " ".join(text.split()) in appeal_block

    # тот же запрос при другом исходном вердикте и обосновании → исходный вердикт в запрос не попадает
    _set_original(con, ev, "01", "Да", tag="-другое")
    op2 = asyncio.run(cc.second_opinion(con, appeal.id, "fake"))
    assert _request(con, op2["ai_call_id"]) == req
    assert op2["verdict"] == op["verdict"]


def test_opinion_saved_and_supported(evaluated):
    con, ev = evaluated
    _set_original(con, ev, "01", "Нет")
    # [[FAKE:verdict=Да]] — маркер провайдера fake: вторая модель ответит «Да»
    appeal = create_appeal(con, AppealCreateDTO(evaluation_id=ev, criterion_id="01",
                                                comment="Представился сразу [[FAKE:verdict=Да]]"))
    op = asyncio.run(cc.second_opinion(con, appeal.id, "fake"))
    assert op["verdict"] == "Да" and op["appeal_supported"] == "да" and op["compared_to"] == "Нет"
    dto = get_appeal(con, appeal.id)
    assert dto.ai_opinion["verdict"] == "Да" and dto.ai_opinion["appeal_supported"] == "да"
    assert dto.ai_opinion["model"] == "fake" and dto.ai_opinion["explanation"]
    assert dto.ai_opinion["blind"] is True

    # исходный вердикт «Да» (апелляция не о чем) — та же модель «Да» → не поддерживает
    _set_original(con, ev, "01", "Да")
    op = asyncio.run(cc.second_opinion(con, appeal.id, "fake"))
    assert op["appeal_supported"] == "нет"

    with pytest.raises(cc.CrossCheckNotFound):
        asyncio.run(cc.second_opinion(con, 99999, "fake"))


def test_appeal_supported_rules(checklist):
    s = cc.appeal_supported
    c01, c02, c04, c08 = (checklist.criterion(i) for i in ("01", "02", "04", "08"))
    # да / нет
    assert s(c01, "Нет", "Да") == "да"
    assert s(c01, "Нет", "Нет") == "нет"
    assert s(c01, "Да", "Нет") == "нет"
    # N/A
    assert s(c02, "Нет", "N/A") == "частично"
    assert s(c02, "Да", "N/A") == "нет"
    assert s(c02, "N/A", "Да") == "да"
    assert s(c02, "N/A", "Нет") == "нет"
    # шкала
    assert s(c04, "10", "20") == "да"
    assert s(c04, "10", "15") == "частично"
    assert s(c04, "10", "5") == "нет"
    assert s(c04, "10", "10") == "нет"
    assert s(c04, "5", "N/A") == "частично"
    # флаг: «Да» — нарушение
    assert s(c08, "Да", "Нет") == "да"
    assert s(c08, "Нет", "Да") == "нет"
    assert s(c08, "Да", "Да", "раскрытие данных", "раскрытие данных") == "нет"
    assert s(c08, "Да", "Да", "раскрытие данных", "обход компании") == "частично"
    assert set(cc.SUPPORTED) == {"да", "частично", "нет"}
