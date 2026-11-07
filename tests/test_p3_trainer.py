"""Тренажёр оператора (cqa/features/trainer.py, API /trainer/*): профиль fake, без сети, временная БД."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqa.checklist.store import seed
from cqa.core.runner import create_run, recompute_final, run_sync
from cqa.ingest.service import ingest_records
from cqa.ingest.store import list_uids
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT = "MINI:mini-ru-chat"


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    """Группа MINI оценена профилем fake; API в режиме fake без воркера."""
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
    with TestClient(app_mod.create_app(fake=True, worker=False)) as client:
        yield {"con": con, "client": client, "vid": vid}


def _start(client, uid=CHAT, **kw) -> dict:
    r = client.post("/api/trainer/sessions", json={"base_uid": uid, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_persona_hides_hidden_question(env):
    con, client = env["con"], env["client"]
    s = _start(client, criteria_focus=["04"], operator="Стажёр")
    assert "hidden_question" not in s["persona"]
    assert "fake hidden_question" not in json.dumps(s, ensure_ascii=False)
    assert "reveal_if_asked" not in s["persona"]   # детали «только если спросят» скрыты до конца сессии
    for k in ("title", "goal", "already_told", "character", "satisfied_when", "leaves_when"):
        assert k in s["persona"]
    assert s["persona"]["language"] == "RU"
    assert s["criteria_focus"] == ["04"] and s["operator"] == "Стажёр"
    # в БД скрытый вопрос есть — им пользуется ИИ-клиент
    stored = json.loads(con.execute("SELECT persona_json FROM trainer_sessions WHERE id=?", (s["id"],)).fetchone()[0])
    assert stored["persona"]["hidden_question"] == "fake hidden_question"
    # GET тоже не показывает скрытый вопрос, пока сессия не завершена
    g = client.get(f"/api/trainer/sessions/{s['id']}").json()
    assert "hidden_question" not in g["persona"]
    # журнал: вызов персоны с purpose=trainer
    assert con.execute("SELECT COUNT(*) FROM llm_calls WHERE purpose='trainer' AND uid=?", (CHAT,)).fetchone()[0] == 1


def test_prefix_is_original_until_operator(env):
    s = _start(env["client"])
    tr = s["transcript"]
    # mini-ru-chat: 6 реплик до первой реплики оператора (клиент, бот, клиент, бот, эскалация, клиент)
    assert [t["speaker"] for t in tr] == ["client", "bot", "client", "bot", "system", "client"]
    assert tr[0]["text"] == "Подскажите, где посмотреть мой тариф?"
    assert [t["mid"] for t in tr] == [f"m{i}" for i in range(1, 7)]
    assert all(t["source"] == "base" for t in tr)
    assert s["status"] == "active" and s["turns"] == 0 and s["max_turns"] == 8


def test_dialog_turns_and_limit(env):
    con, client = env["con"], env["client"]
    s = _start(client)
    sid = s["id"]
    for turn in range(1, 9):
        r = client.post(f"/api/trainer/sessions/{sid}/reply", json={"text": f"Ответ оператора {turn}"})
        assert r.status_code == 200, r.text
        r = r.json()
        assert r["turn"] == turn
        assert [m["speaker"] for m in r["new_messages"]] == ["operator", "client"]
        assert r["new_messages"][0]["text"] == f"Ответ оператора {turn}"
        assert r["finished"] is (turn == 8)
    assert r["outcome"] == "лимит ходов"
    # на последнем ходу клиенту сказано завершить разговор
    last_req = con.execute("SELECT request_json FROM llm_calls WHERE purpose='trainer' AND uid=? ORDER BY id DESC LIMIT 1",
                           (f"TRAINER:{sid}",)).fetchone()[0]
    assert "последний ход" in last_req
    # после завершения клиентом оператор может попрощаться — клиент уже не отвечает
    r = client.post(f"/api/trainer/sessions/{sid}/reply", json={"text": "Всего доброго!"}).json()
    assert [m["speaker"] for m in r["new_messages"]] == ["operator"] and r["finished"] is True
    g = client.get(f"/api/trainer/sessions/{sid}").json()
    assert g["turns"] == 8 and g["client_finished"] is True
    assert len(g["transcript"]) == 6 + 8 * 2 + 1
    assert [t["mid"] for t in g["transcript"]] == [f"m{i}" for i in range(1, len(g["transcript"]) + 1)]
    # время по порядку, формат чата MM:SS; первая реплика оператора — во время исходной (01:47)
    secs = [int(t["ts"].split(":")[0]) * 60 + int(t["ts"].split(":")[1]) for t in g["transcript"]]
    assert secs == sorted(secs)
    assert g["transcript"][6]["ts"] == "01:47"


def test_client_states_finish_early(env, monkeypatch):
    """«доволен» от модели → разговор завершён досрочно."""
    from cqa.features import trainer
    from cqa.llm import gateway

    client = env["client"]
    s = _start(client)
    real = gateway.complete

    async def fake_complete(req, profile, ctx=None, con=None):
        resp = await real(req, profile, ctx, con)
        if ctx is not None and ctx.purpose == "trainer" and "state" in (req.json_schema or {}).get("properties", {}):
            resp.parsed = {"check": "всё закрыто", "reply": "Понял, спасибо!", "state": "доволен"}
        return resp

    monkeypatch.setattr(trainer.gateway, "complete", fake_complete)
    r = client.post(f"/api/trainer/sessions/{s['id']}/reply", json={"text": "Тариф — в разделе «Профиль»."}).json()
    assert r["finished"] is True and r["outcome"] == "доволен" and r["turn"] == 1
    assert r["new_messages"][1]["text"] == "Понял, спасибо!"


def test_finish_creates_evaluation_and_comparison(env):
    con, client = env["con"], env["client"]
    s = _start(client)
    sid = s["id"]
    assert client.post(f"/api/trainer/sessions/{sid}/finish").status_code == 422   # оператор ещё не ответил
    for text in ("Здравствуйте! Меня зовут Ольга. Ваш тариф — «Стандарт», он в разделе «Профиль».",
                 "Остались ли вопросы? Всего доброго!"):
        assert client.post(f"/api/trainer/sessions/{sid}/reply", json={"text": text}).status_code == 200
    r = client.post(f"/api/trainer/sessions/{sid}/finish")
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["status"] == "finished" and f["uid"] == f"TRAINER:{sid}"
    assert f["persona"]["hidden_question"] == "fake hidden_question"      # после завершения — раскрыт
    eid = f["result_evaluation_id"]
    ev = con.execute("SELECT e.*, r.kind FROM evaluations e JOIN runs r ON r.id=e.run_id WHERE e.id=?", (eid,)).fetchone()
    assert ev["status"] == "done" and ev["kind"] == "trainer" and ev["uid"] == f"TRAINER:{sid}"
    assert ev["checklist_version_id"] == env["vid"] and ev["profile"] == "fake"
    assert con.execute("SELECT COUNT(*) FROM llm_calls WHERE evaluation_id=?", (eid,)).fetchone()[0] > 0   # журнал
    conv = con.execute("SELECT * FROM conversations WHERE uid=?", (f"TRAINER:{sid}",)).fetchone()
    assert conv["group_id"] == "TRAINER" and conv["parent_uid"] == CHAT and conv["current_evaluation_id"] is None
    texts = [r[0] for r in con.execute("SELECT text FROM messages WHERE uid=? ORDER BY idx", (f"TRAINER:{sid}",))]
    assert texts[0] == "Подскажите, где посмотреть мой тариф?" and texts[6].startswith("Здравствуйте! Меня зовут Ольга")

    cmp = f["comparison"]
    base_eid = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (CHAT,)).fetchone()[0]
    assert cmp["before"]["evaluation_id"] == base_eid and cmp["after"]["evaluation_id"] == eid
    assert [c["criterion_id"] for c in cmp["criteria"]] == ["01", "02", "03", "04", "05", "06", "07", "08"]
    for c in cmp["criteria"]:
        assert c["before"]["verdict"] and c["after"]["verdict"] and c["after"]["explanation"]
        assert c["change"] in ("improved", "worse", "same", "not_comparable")
    assert cmp["summary"].startswith("Итог ")
    assert cmp["after"]["total"] is not None
    # повторный finish отдаёт тот же результат без нового прогона
    again = client.post(f"/api/trainer/sessions/{sid}/finish").json()
    assert again["result_evaluation_id"] == eid
    assert con.execute("SELECT COUNT(*) FROM runs WHERE kind='trainer'").fetchone()[0] == 1
    # после завершения реплики не принимаются
    assert client.post(f"/api/trainer/sessions/{sid}/reply", json={"text": "ещё"}).status_code == 422
    lst = client.get("/api/trainer/sessions", params={"base_uid": CHAT}).json()
    assert lst[0]["id"] == sid and lst[0]["after_total"] == cmp["after"]["total"]


def test_comparison_improved(env):
    """«Было/стало»: ступень 04 выросла → improved, флаг 08 «Да» → «Нет» тоже improved."""
    from cqa.features.trainer import compare

    con, client = env["con"], env["client"]
    s = _start(client)
    client.post(f"/api/trainer/sessions/{s['id']}/reply", json={"text": "Ваш тариф — «Стандарт»."})
    f = client.post(f"/api/trainer/sessions/{s['id']}/finish").json()
    before, after = f["base_evaluation_id"], f["result_evaluation_id"]
    for eid, v04, v08 in ((before, "10", "Да"), (after, "20", "Нет")):
        con.execute("UPDATE criterion_results SET verdict=? WHERE evaluation_id=? AND criterion_id='04'", (v04, eid))
        con.execute("UPDATE criterion_results SET verdict=?, category=? WHERE evaluation_id=? AND criterion_id='08'",
                    (v08, "оскорбление" if v08 == "Да" else "", eid))
        recompute_final(con, eid)
    cmp = compare(con, before, after)
    by = {c["criterion_id"]: c for c in cmp["criteria"]}
    assert by["04"]["change"] == "improved" and by["04"]["ok_after"] is True and by["04"]["ok_before"] is False
    assert by["08"]["change"] == "improved"
    assert "04" in cmp["improved"] and "08" in cmp["improved"]
    assert cmp["before"]["gross"] is True and cmp["after"]["gross"] is False
    assert "Улучшилось: " in cmp["summary"] and "04 Корректная и полная консультация" in cmp["summary"]


def test_sessions_hidden_from_lists_and_export(env):
    con, client = env["con"], env["client"]
    s = _start(client)
    client.post(f"/api/trainer/sessions/{s['id']}/reply", json={"text": "Ваш тариф — «Стандарт»."})
    assert client.post(f"/api/trainer/sessions/{s['id']}/finish").status_code == 200
    uid = f"TRAINER:{s['id']}"
    assert uid not in {c["uid"] for c in client.get("/api/conversations").json()}
    assert uid in {c["uid"] for c in client.get("/api/conversations", params={"group": "TRAINER"}).json()}
    csv = client.get("/api/export", params={"format": "csv"}).text
    assert "mini-ru-chat" in csv and f"\n{s['id']}," not in csv and "TRAINER" not in csv
    risks = client.get("/api/insights/risks").json()
    assert all(not str(x["uid"]).startswith("TRAINER:") for x in risks["items"])
    # карточка диалога тренажёра открывается и оценка находится
    ev = client.get(f"/api/conversations/{uid}/evaluation")
    assert ev.status_code == 200 and ev.json()["uid"] == uid
    # служебная группа не становится «текущим набором» для пробного прогона
    from cqa.api.routes.dryrun import current_group
    assert current_group(con) == "MINI"


def test_errors(env):
    client = env["client"]
    assert client.post("/api/trainer/sessions", json={"base_uid": "MINI:nope"}).status_code == 404
    r = client.post("/api/trainer/sessions", json={"base_uid": CHAT, "criteria_focus": ["99"]})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == "criteria_focus"
    assert client.get("/api/trainer/sessions/999").status_code == 404
    assert client.post("/api/trainer/sessions/999/reply", json={"text": "x"}).status_code == 404
    s = _start(client)
    assert client.post(f"/api/trainer/sessions/{s['id']}/reply", json={"text": ""}).status_code == 422


def test_lessons(env):
    client = env["client"]
    r = client.post("/api/trainer/lessons", json={"base_uids": [CHAT, "MINI:mini-kz-chat"], "title": "Тарифы"})
    assert r.status_code == 200, r.text
    lesson = r.json()
    assert lesson["id"] == 1 and lesson["title"] == "Тарифы" and lesson["n_sessions"] == 0
    assert client.post("/api/trainer/lessons", json={"base_uids": ["MINI:nope"], "title": "x"}).status_code == 422
    s = _start(client, lesson_id=1, operator="Стажёр")
    lessons = client.get("/api/trainer/lessons").json()
    assert len(lessons) == 1 and lessons[0]["n_sessions"] == 1
    item = next(i for i in lessons[0]["items"] if i["base_uid"] == CHAT)
    assert item["sessions"][0]["id"] == s["id"] and item["sessions"][0]["lesson_id"] == 1
    assert item["external_id"] == "mini-ru-chat" and item["base_total"] is not None
    stored = env["con"].execute("SELECT value_json FROM settings WHERE key='trainer_lessons'").fetchone()[0]
    assert json.loads(stored)[0]["base_uids"] == [CHAT, "MINI:mini-kz-chat"]


def test_record_roundtrip_call(env):
    """Звонок: префикс до оператора, время — секунды от начала в формате чата; запись нормализуется."""
    from cqa.features.trainer import build_prefix, build_record
    from cqa.ingest.normalize import normalize_record
    from cqa.ingest.parse import parse_record
    from cqa.ingest.store import get_conversation

    conv = get_conversation(env["con"], "MINI:mini-ru-call")
    prefix, op_t = build_prefix(conv)
    assert prefix[0]["ts"] == "00:00" and prefix[-1]["speaker"] == "system" and op_t == 28.0
    tr = [*prefix, {"speaker": "operator", "text": "Здравствуйте!", "ts": "00:28", "offset": 28.0, "source": "trainee"}]
    rec = build_record(5, {"meta": {"channel": conv.channel, "language": "RU"}}, tr)
    c2 = normalize_record(parse_record(rec), "TRAINER")
    assert c2.uid == "TRAINER:5" and c2.channel == "voice call" and len(c2.messages) == len(tr)
    assert c2.messages[-1].speaker == "operator" and c2.messages[-1].segment == "operator"


def test_cli_registered():
    from typer.testing import CliRunner

    from cqa.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    assert "trainer" in out and "tutor" in out
