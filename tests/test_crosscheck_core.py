"""Перекрёстная проверка: second_json, расхождение → needs_review, выбор критериев, сводка на эталоне, CLI.

Профиль fake детерминирован (вердикт — по хэшу запроса), поэтому вторая модель (fake с другим именем модели —
мимо кэша) отвечает так же, как основной судья. Расхождение создаём, меняя вердикт основного судьи в БД.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cqa.core import crosscheck as cc
from cqa.llm.base import Profile

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT, KZ, CALL = "MINI:mini-ru-chat", "MINI:mini-kz-chat", "MINI:mini-ru-call"
SECOND = Profile(name="fake-second", provider="fake", model="fake-2", max_concurrency=16, rpm=100000)


@pytest.fixture
def evaluated(tmp_db):
    """Мини-набор (3 диалога), оценённый профилем fake."""
    from cqa.checklist.store import get_active, seed
    from cqa.core.runner import create_run, run_sync
    from cqa.ingest.service import ingest_payload
    from cqa.settings import get_settings

    seed(tmp_db, get_settings().checklist_path)
    ingest_payload(tmp_db, json.loads(MINI.read_text(encoding="utf-8")), "file")
    vid = get_active(tmp_db)[1]["id"]
    assert run_sync(tmp_db, create_run(tmp_db, uids=[CHAT, KZ, CALL], version_id=vid, profile_name="fake"))["status"] == "done"
    return tmp_db


def _eval_id(con, uid: str = CHAT) -> int:
    return con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]


def _cr(con, ev: int, cid: str):
    return con.execute("SELECT * FROM criterion_results WHERE evaluation_id=? AND criterion_id=?", (ev, cid)).fetchone()


def _other(crit_verdicts: list[str], v: str) -> str:
    return next(x for x in crit_verdicts if x != v)


def test_second_json_filled_default_criteria(evaluated):
    con = evaluated
    ev = _eval_id(con)
    res = asyncio.run(cc.cross_check_evaluation(con, ev, profile=SECOND))
    # по умолчанию — критерии с cross_check: true (03 и 08 в freedom-cqa.yaml)
    assert [i["criterion_id"] for i in res["criteria"]] == ["03", "08"]
    assert res["checked"] == 2 and res["failed"] == 0
    for cid in ("03", "08"):
        s = json.loads(_cr(con, ev, cid)["second_json"])
        assert {"profile", "model", "verdict", "explanation", "evidence", "agree", "near"} <= set(s)
        assert s["profile"] == "fake-second" and s["model"] == "fake-2"
        assert s["verdict"] == _cr(con, ev, cid)["verdict"] and s["agree"] is True
        assert s["evidence"] and s["explanation"]
    # у остальных критериев second_json не появился
    assert _cr(con, ev, "01")["second_json"] is None
    # вызовы второй модели — в журнале оценки с purpose=cross_check
    calls = con.execute("SELECT purpose, profile, criterion_id FROM llm_calls WHERE evaluation_id=? AND profile='fake-second'",
                        (ev,)).fetchall()
    assert {(c["purpose"], c["criterion_id"]) for c in calls} == {("cross_check", "03"), ("cross_check", "08")}
    assert con.execute("SELECT needs_review FROM evaluations WHERE id=?", (ev,)).fetchone()[0] == 0


def test_disagreement_sets_and_clears_needs_review(evaluated):
    con = evaluated
    ev = _eval_id(con)
    asyncio.run(cc.cross_check_evaluation(con, ev, ["03"], SECOND))
    second = json.loads(_cr(con, ev, "03")["second_json"])["verdict"]
    main = _cr(con, ev, "03")["verdict"]
    wrong = _other(["Да", "Нет", "N/A"], second)
    con.execute("UPDATE criterion_results SET verdict=? WHERE evaluation_id=? AND criterion_id='03'", (wrong, ev))

    res = asyncio.run(cc.cross_check_evaluation(con, ev, ["03"], SECOND))
    item = res["criteria"][0]
    assert item["agree"] is False and item["needs_review"] is True and item["second_verdict"] == second
    row = _cr(con, ev, "03")
    s = json.loads(row["second_json"])
    assert s["agree"] is False and s["compared_to"] == wrong and s["marked"] is True
    assert row["needs_review"] == 1
    assert con.execute("SELECT needs_review FROM evaluations WHERE id=?", (ev,)).fetchone()[0] == 1
    assert res["needs_review"] is True and res["disagree"] == 1

    # вердикт вернули — повторная проверка согласна и снимает СВОЮ отметку
    con.execute("UPDATE criterion_results SET verdict=? WHERE evaluation_id=? AND criterion_id='03'", (main, ev))
    asyncio.run(cc.cross_check_evaluation(con, ev, ["03"], SECOND))
    assert _cr(con, ev, "03")["needs_review"] == 0
    assert con.execute("SELECT needs_review FROM evaluations WHERE id=?", (ev,)).fetchone()[0] == 0


def test_validator_review_kept_and_no_mark(evaluated):
    con = evaluated
    ev = _eval_id(con)
    # отметка валидатора (не перекрёстной проверки) при согласии остаётся
    con.execute("UPDATE criterion_results SET needs_review=1 WHERE evaluation_id=? AND criterion_id='08'", (ev,))
    asyncio.run(cc.cross_check_evaluation(con, ev, ["08"], SECOND))
    assert _cr(con, ev, "08")["needs_review"] == 1
    assert json.loads(_cr(con, ev, "08")["second_json"])["marked"] is False
    assert con.execute("SELECT needs_review FROM evaluations WHERE id=?", (ev,)).fetchone()[0] == 1

    # mark=False: мнение записано, «требует проверки» не меняется
    second = json.loads(_cr(con, ev, "08")["second_json"])["verdict"]
    con.execute("UPDATE criterion_results SET verdict=? WHERE evaluation_id=? AND criterion_id='01'",
                (_other(["Да", "Нет"], _cr(con, ev, "01")["verdict"]), ev))
    res = asyncio.run(cc.cross_check_evaluation(con, ev, ["01"], SECOND, mark=False))
    assert res["criteria"][0]["agree"] is False
    assert _cr(con, ev, "01")["needs_review"] == 0
    assert json.loads(_cr(con, ev, "01")["second_json"])["agree"] is False
    assert second


def test_select_criteria(checklist):
    ids = lambda x: [c.id for c in cc.select_criteria(checklist, x)]
    assert ids(None) == ["03", "08"] and ids([]) == ["03", "08"]
    assert ids(["all"]) == [c.id for c in checklist.enabled_criteria()] and ids("all") == ids(["all"])
    assert ids("08,3") == ["08", "03"]
    with pytest.raises(cc.CrossCheckInvalid):
        cc.select_criteria(checklist, ["99"])


def test_agreement_scale_near(checklist):
    c04, c03 = checklist.criterion("04"), checklist.criterion("03")
    assert cc.agreement(c04, "15", "15") == (True, False)
    assert cc.agreement(c04, "15", "10") == (False, True)          # одна ступень
    assert cc.agreement(c04, "20", "10") == (False, False)         # две ступени
    assert cc.agreement(c04, "N/A", "20") == (False, False)
    assert cc.agreement(c03, "Да", "Нет") == (False, False)


def test_all_criteria_group_and_unknown(evaluated):
    con = evaluated
    res = asyncio.run(cc.cross_check_group(con, "MINI", ["all"], profile=SECOND))
    assert res["evaluations"] == 3 and res["errors"] == 0
    n_enabled = 8
    assert set(res["criteria"]) == {f"0{i}" for i in range(1, n_enabled + 1)}
    for t in res["criteria"].values():
        assert t["checked"] + t["skipped"] == 3 and t["failed"] == 0 and t["agree"] == t["checked"]
    with pytest.raises(cc.CrossCheckInvalid):
        asyncio.run(cc.cross_check_evaluation(con, _eval_id(con), ["42"], SECOND))
    with pytest.raises(cc.CrossCheckNotFound):
        asyncio.run(cc.cross_check_evaluation(con, 999999, None, SECOND))


def _gold_setup(con):
    """03 на трёх диалогах: CHAT — расхождение и судья ошибся (эталон = вторая модель); KZ — согласны, судья прав;
    CALL — согласны, судья ошибся по основному ответу, но попал в допустимую альтернативу."""
    asyncio.run(cc.cross_check_group(con, "MINI", ["03"], profile=SECOND))
    ev = {u: _eval_id(con, u) for u in (CHAT, KZ, CALL)}
    second_chat = json.loads(_cr(con, ev[CHAT], "03")["second_json"])["verdict"]
    wrong = _other(["Да", "Нет", "N/A"], second_chat)
    con.execute("UPDATE criterion_results SET verdict=? WHERE evaluation_id=? AND criterion_id='03'", (wrong, ev[CHAT]))
    main_call = _cr(con, ev[CALL], "03")["verdict"]
    gold = {
        "mini-ru-chat": {"id": "mini-ru-chat", "c03": second_chat},
        "mini-kz-chat": {"id": "mini-kz-chat", "c03": _cr(con, ev[KZ], "03")["verdict"]},
        "mini-ru-call": {"id": "mini-ru-call", "c03": f"{_other(['Да', 'Нет', 'N/A'], main_call)}|{main_call}"},
    }
    return gold


def test_summary_on_gold(evaluated):
    con = evaluated
    gold = _gold_setup(con)
    s = cc.cross_check_summary(con, "MINI", None, gold)
    assert [m["criterion_id"] for m in s["criteria"]] == ["03"]
    m = s["criteria"][0]
    assert (m["n"], m["agree"], m["disagree"]) == (3, 2, 1)
    assert abs(m["agree_rate"] - 2 / 3) < 1e-9
    a, d = m["gold"]["agree"], m["gold"]["disagree"]
    assert (a["n"], a["main_exact"], a["main_compat"]) == (2, 1, 2)
    assert (d["n"], d["main_exact"], d["main_compat"], d["second_exact"]) == (1, 0, 0, 1)
    assert (m["gold"]["errors_exact"], m["gold"]["caught_exact"]) == (2, 1)
    assert m["gold"]["caught_exact_rate"] == 0.5
    assert (m["gold"]["errors_compat"], m["gold"]["caught_compat"]) == (1, 1)
    assert s["review_verdicts"] == 1 and s["review_evaluations"] == 1
    assert s["total"]["n"] == 3
    # без эталона — только согласие
    assert cc.cross_check_summary(con, "MINI")["criteria"][0]["gold"] is None


def test_cli_cross_check(evaluated, tmp_path):
    from typer.testing import CliRunner

    from cqa.cli import app

    con = evaluated
    gold = _gold_setup(con)
    path = tmp_path / "gold.csv"
    path.write_text("id,c03\n" + "".join(f"{k},{v['c03']}\n" for k, v in gold.items()), encoding="utf-8")
    r = CliRunner().invoke(app, ["cross-check", "--group", "MINI", "--criteria", "03", "--profile", "fake",
                                 "--gold", str(path)])
    assert r.exit_code == 0, r.output
    assert "c03" in r.output and "требует проверки" in r.output and "расходятся" in r.output
    r = CliRunner().invoke(app, ["cross-check", "--group", "MINI", "--summary-only", "--json", "-", "--gold", str(path)])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output[r.output.index("{"):])
    assert data["criteria"] and data["gold"] is True
