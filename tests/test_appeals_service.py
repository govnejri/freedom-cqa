"""Сервис апелляций без HTTP: фрагмент, проверки подачи, решение, прецедент. Прогон — синхронно через process_run."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqa.api.schemas import AppealCreateDTO, DecisionCreateDTO, MessageDTO
from cqa.appeals.service import (
    AppealConflict,
    AppealInvalid,
    AppealNotFound,
    build_fragment,
    create_appeal,
    decide,
    get_appeal,
    list_appeals,
)

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT = "MINI:mini-ru-chat"


@pytest.fixture
def evaluated(tmp_db):
    """Мини-набор, оценённый профилем fake синхронно (process_run)."""
    from cqa.checklist.store import get_active, seed
    from cqa.core.runner import create_run, run_sync
    from cqa.ingest.service import ingest_payload
    from cqa.settings import get_settings

    seed(tmp_db, get_settings().checklist_path)
    ingest_payload(tmp_db, json.loads(MINI.read_text(encoding="utf-8")), "file")
    vid = get_active(tmp_db)[1]["id"]
    run_id = create_run(tmp_db, uids=[CHAT], version_id=vid, profile_name="fake")
    assert run_sync(tmp_db, run_id)["status"] == "done"
    ev = tmp_db.execute("SELECT * FROM evaluations WHERE uid=? ORDER BY id DESC", (CHAT,)).fetchone()
    assert ev["status"] == "done" and ev["final_total"] == ev["total"]
    return tmp_db, ev


def _msgs(n: int) -> list[MessageDTO]:
    return [MessageDTO(mid=f"m{i}", idx=i, speaker="client", text=f"t{i}", ts_raw="00:00", segment="operator")
            for i in range(1, n + 1)]


def test_build_fragment():
    msgs = _msgs(20)
    ids = lambda frag: [m.idx for m in frag]
    assert ids(build_fragment(msgs, ["m5"], None, [])) == [3, 4, 5, 6, 7]
    # перекрытие контекстов — без повторов, по порядку
    assert ids(build_fragment(msgs, ["m6", "m5"], None, ["m7"])) == [3, 4, 5, 6, 7, 8, 9]
    # края диалога, проверенный участок целиком ±2, неизвестные ID игнорируются
    assert ids(build_fragment(msgs, ["m1", "m99"], ["m12", "m14"], ["m20"])) == [1, 2, 3, 10, 11, 12, 13, 14, 15, 16,
                                                                                 18, 19, 20]
    # опорных реплик нет — весь диалог
    assert ids(build_fragment(msgs, [], None, [])) == list(range(1, 21))


def test_create_appeal_checks(evaluated):
    con, ev = evaluated
    with pytest.raises(AppealNotFound):
        create_appeal(con, AppealCreateDTO(evaluation_id=999, criterion_id="01", comment="x"))
    with pytest.raises(AppealInvalid) as e:
        create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="77", comment="x"))
    assert e.value.loc == "criterion_id"
    with pytest.raises(AppealInvalid) as e:
        create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="01", comment="  "))
    assert e.value.loc == "comment"
    with pytest.raises(AppealInvalid) as e:
        create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="01", comment="x",
                                           counter_evidence=["m3", "bad"]))
    assert e.value.loc == "counter_evidence.1"

    a = create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="01", comment=" не согласен ",
                                           counter_evidence=["M3", "3", "m4"]))
    assert a.status == "open" and a.comment == "не согласен" and a.counter_evidence == ["m3", "m4"]
    assert a.uid == CHAT and a.original.criterion_id == "01" and a.decision is None
    frag = [m.mid for m in a.fragment]
    assert len(frag) == len(set(frag)) and {"m1", "m2", "m3", "m4", "m5", "m6"} <= set(frag)
    for e_ in a.original.evidence:
        assert e_.id in frag
    with pytest.raises(AppealConflict):
        create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="01", comment="ещё"))
    assert [x.id for x in list_appeals(con, "open")] == [a.id] and list_appeals(con, "decided") == []
    assert [x.id for x in list_appeals(con, uid=CHAT)] == [a.id]


def test_confirm_keeps_total(evaluated):
    con, ev = evaluated
    a = create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="05", comment="x"))
    with pytest.raises(AppealInvalid) as e:
        decide(con, a.id, DecisionCreateDTO(action="maybe", reason="x"))
    assert e.value.loc == "action"
    with pytest.raises(AppealInvalid) as e:
        decide(con, a.id, DecisionCreateDTO(action="confirm", reason=" "))
    assert e.value.loc == "reason"
    d = decide(con, a.id, DecisionCreateDTO(action="confirm", new_verdict="Да", reason="оценка верна"))
    assert d.status == "decided" and d.decision["action"] == "confirm" and d.decision["new_verdict"] is None
    assert d.prev_final_total == d.new_final_total == ev["total"]
    assert d.original.final_verdict == d.original.verdict
    assert con.execute("SELECT COUNT(*) FROM precedents").fetchone()[0] == 0
    with pytest.raises(AppealConflict):
        decide(con, a.id, DecisionCreateDTO(action="change", new_verdict="Нет", reason="передумал"))


def test_change_recomputes_and_precedent(evaluated):
    con, ev = evaluated
    cr = con.execute("SELECT * FROM criterion_results WHERE evaluation_id=? AND criterion_id='01'", (ev["id"],)).fetchone()
    new = "Нет" if cr["verdict"] == "Да" else "Да"
    a = create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="01", comment="x"))

    with pytest.raises(AppealInvalid) as e:
        decide(con, a.id, DecisionCreateDTO(action="change", new_verdict="N/A", reason="01 без N/A"))
    assert e.value.loc == "new_verdict"
    with pytest.raises(AppealInvalid):
        decide(con, a.id, DecisionCreateDTO(action="change", reason="без вердикта"))
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert get_appeal(con, a.id).status == "open"

    d = decide(con, a.id, DecisionCreateDTO(action="change", new_verdict=new, reason="по трактовке Т1",
                                            make_precedent=True))
    assert d.status == "decided" and d.decision["new_verdict"] == new and d.decision["make_precedent"] is True
    assert d.prev_final_total == ev["total"] and d.new_final_total != ev["total"]
    assert d.original.verdict == cr["verdict"] and d.original.final_verdict == new
    assert d.original.score.verdict == new

    row = con.execute("SELECT total, final_total, score_json, final_score_json FROM evaluations WHERE id=?",
                      (ev["id"],)).fetchone()
    assert row["total"] == ev["total"] and row["score_json"] == ev["score_json"]   # исходная оценка не меняется
    assert row["final_total"] == d.new_final_total
    assert con.execute("SELECT verdict FROM criterion_results WHERE id=?", (cr["id"],)).fetchone()[0] == cr["verdict"]

    p = con.execute("SELECT * FROM precedents").fetchone()
    assert p["appeal_id"] == a.id and p["criterion_id"] == "01" and p["verdict"] == new and p["why"] == "по трактовке Т1"
    evidence = [e["id"] for e in json.loads(cr["evidence_json"])]
    texts = {r["mid"]: r for r in con.execute("SELECT mid, speaker, text FROM messages WHERE uid=?", (CHAT,))}
    labels = {"client": "КЛИЕНТ", "operator": "ОПЕРАТОР", "bot": "БОТ", "system": "СИСТЕМА"}
    expected = " / ".join(f"{labels[texts[m]['speaker']]}: {texts[m]['text']}" for m in evidence)
    assert p["fragment"] == expected and p["fragment"]

    # вторая апелляция по тому же критерию после решения — можно; подтверждение сохраняет изменённый вердикт
    a2 = create_appeal(con, AppealCreateDTO(evaluation_id=ev["id"], criterion_id="01", comment="верните"))
    d2 = decide(con, a2.id, DecisionCreateDTO(action="confirm", reason="решение в силе", make_precedent=True))
    assert d2.new_final_total == d.new_final_total
    assert con.execute("SELECT verdict FROM precedents WHERE appeal_id=?", (a2.id,)).fetchone()[0] == new
    with pytest.raises(AppealNotFound):
        get_appeal(con, 999)
