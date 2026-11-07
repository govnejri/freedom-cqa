"""Автотесты судьи: генерация мутантов из диалогов с эталоном → прогон runner (fake) → проверка → отчёт и CLI."""
from __future__ import annotations

import json

import pytest
from conftest import CHECKLIST_YAML
from test_mutate_structure import EN, RU, _mini

GOLD_HEADER = "id,family,c01,c02,c03,c04,c05,c06,c07,c08\n"
GOLD = {
    "t-ru": "fam-a,Да,N/A,N/A,20,Да,10,Да|Нет,Нет",
    "t-en": "fam-b,Да,N/A,N/A,20,Да,10,Да,Нет",
    "mini-kz-chat": "fam-a,Да,N/A,N/A,20,Да,10,Да,Нет",
    "mini-ru-call": "fam-c,Да,N/A,N/A,20,Да,10,N/A,Нет",
    "mini-ru-chat": "fam-d,Да,N/A,N/A,20,Да,10,N/A,Да",      # 08 = Да → не база для insult / private_offer
}


@pytest.fixture
def con(tmp_db, tmp_path):
    from cqa.checklist.store import seed
    from cqa.ingest.service import ingest_records
    from cqa.quality.gold import import_gold

    seed(tmp_db, CHECKLIST_YAML)
    recs = [RU, EN, _mini("mini-kz-chat"), _mini("mini-ru-call"), _mini("mini-ru-chat")]
    ingest_records(tmp_db, "DEV", recs, "file")
    gold = tmp_path / "gold.csv"
    gold.write_text(GOLD_HEADER + "".join(f"{k},{v}\n" for k, v in GOLD.items()), encoding="utf-8")
    import_gold(tmp_db, gold)
    return tmp_db


def _evaluate_bases(con) -> None:
    from cqa.checklist.store import get_active
    from cqa.core.runner import create_run, run_sync
    from cqa.ingest.store import list_uids

    rid = create_run(con, uids=list_uids(con, "DEV"), version_id=get_active(con)[1]["id"], profile_name="fake")
    run_sync(con, rid)


def test_diverse_order():
    from cqa.quality.autotest import diverse_order

    items = [("RU", "chat", "a"), ("RU", "chat", "a"), ("RU", "chat", "b"), ("KZ", "chat", "a"), ("RU", "call", "c")]
    out = diverse_order(items, stratum=lambda x: (x[0], x[1]), family=lambda x: x[2])
    assert {x[:2] for x in out[:3]} == {("RU", "chat"), ("KZ", "chat"), ("RU", "call")}
    assert out[2] == ("RU", "chat", "b")                       # в страте сначала невстреченная семья
    assert sorted(out) == sorted(items)


def test_generate_bases_and_rows(con):
    from cqa.quality.autotest import generate

    out = {m["mutation"]: m for m in generate(con, per_mutation=2)}
    ins = out["insult"]
    assert ins["generated"] == 2 and ins["candidates"] == 3          # t-ru, t-en, mini-kz-chat (не mini-ru-chat, не звонок)
    assert len(ins["by_language"]) == 2                               # разные языки
    assert "AUTOTEST:mini-ru-chat~insult" not in ins["uids"]
    assert out["disconnect"]["candidates"] == 3                       # 07 = N/A по эталону — не база
    # инъекция без ограничения берёт и звонок
    inj = generate(con, mutations=["injection"])[0]
    assert "AUTOTEST:mini-ru-call~injection" in inj["uids"]

    rows = con.execute("SELECT * FROM conversations WHERE group_id='AUTOTEST'").fetchall()
    assert rows and all(r["parent_uid"] and r["mutation_id"] for r in rows)
    r = con.execute("SELECT * FROM conversations WHERE uid='AUTOTEST:mini-ru-call~injection'").fetchone()
    assert r["parent_uid"] == "DEV:mini-ru-call" and r["mutation_id"] == "injection" and r["external_id"] == "mini-ru-call~injection"
    ds = con.execute("SELECT * FROM datasets WHERE group_id='AUTOTEST'").fetchone()
    assert ds["source"] == "autotest" and ds["total_records"] == len(rows)
    at = con.execute("SELECT * FROM autotests WHERE uid='AUTOTEST:mini-ru-call~injection'").fetchone()
    assert at["base_uid"] == "DEV:mini-ru-call" and json.loads(at["expect_json"])["expect"] == "same_as_base"
    n_ins = con.execute("SELECT COUNT(*) FROM autotests WHERE mutation_id='insult'").fetchone()[0]
    assert n_ins == 2
    # повторная генерация с меньшим N сужает набор тестов изменения
    generate(con, per_mutation=1, mutations=["insult"])
    assert con.execute("SELECT COUNT(*) FROM autotests WHERE mutation_id='insult'").fetchone()[0] == 1


def test_run_evaluates_targets_only_and_reports(con):
    from cqa.quality.autotest import generate, report, run

    _evaluate_bases(con)
    generate(con, per_mutation=2)
    res = run(con, profile="fake")
    runs = res["runs"]
    assert set(runs) == {"insult", "private_offer", "drop_intro", "disconnect", "operator_closed", "injection",
                         "rename_operator"}
    kinds = {r["kind"] for r in con.execute(f"SELECT kind FROM runs WHERE id IN ({','.join(map(str, runs.values()))})")}
    assert kinds == {"autotest"}

    # абсолютное ожидание: оценены только целевые критерии, остальные skipped, оценка partial, текущая не ставится
    ev = con.execute("SELECT * FROM evaluations WHERE run_id=?", (runs["insult"],)).fetchone()
    st = {r["criterion_id"]: r["status"] for r in con.execute(
        "SELECT criterion_id, status FROM criterion_results WHERE evaluation_id=?", (ev["id"],))}
    assert st["08"] == st["05"] == "done" and {v for k, v in st.items() if k not in ("05", "08")} == {"skipped"}
    assert ev["status"] == "partial"
    run_row = con.execute("SELECT * FROM runs WHERE id=?", (runs["insult"],)).fetchone()
    assert run_row["status"] == "done" and run_row["units_total"] == run_row["units_done"] == 4
    assert con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (ev["uid"],)).fetchone()[0] is None
    # same_as_base: все критерии
    ev2 = con.execute("SELECT * FROM evaluations WHERE run_id=?", (runs["rename_operator"],)).fetchone()
    assert ev2["status"] == "done"
    # мутанты не получают текущую оценку: их не видят список, радар рисков и экспорт
    assert con.execute("SELECT COUNT(*) FROM conversations WHERE group_id='AUTOTEST'"
                       " AND current_evaluation_id IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM criterion_results WHERE evaluation_id=? AND status='done'",
                       (ev2["id"],)).fetchone()[0] == 8

    rep = res["report"]
    n = con.execute("SELECT COUNT(*) FROM autotests").fetchone()[0]
    assert rep["total"]["n"] == rep["total"]["evaluated"] == n
    assert rep["total"]["passed"] + rep["total"]["failed"] + rep["total"]["errors"] == n
    assert [m["mutation"] for m in rep["by_mutation"]][:3] == ["insult", "private_offer", "drop_intro"]
    assert {x["language"] for x in rep["by_language"]} <= {"RU", "KZ", "EN"}
    assert rep["injection"]["evaluated"] == 2
    t = next(t for t in rep["tests"] if t["mutation"] == "rename_operator")
    assert t["base_evaluation_id"] and t["base_match"] == "profile" and len(t["checks"]) == 8
    assert all(f["status"] in ("failed", "error") for f in rep["failed"])
    rows = con.execute("SELECT passed, last_run_id FROM autotests").fetchall()
    assert all(r["passed"] in (0, 1) and r["last_run_id"] for r in rows)
    assert report(con, profile="gemini-flash")["total"]["evaluated"] == 0       # фильтр по профилю


def _set(con, eval_id: int, cid: str, verdict: str) -> None:
    con.execute("UPDATE criterion_results SET verdict=?, status='done' WHERE evaluation_id=? AND criterion_id=?",
                (verdict, eval_id, cid))


def test_check_logic(con):
    from cqa.quality.autotest import generate, report, run

    _evaluate_bases(con)
    generate(con, per_mutation=1, mutations=["insult", "rename_operator", "injection"])
    run(con, profile="fake", mutations=["insult", "rename_operator", "injection"])
    tests = {t["mutation"]: t for t in report(con)["tests"]}

    ins = tests["insult"]
    _set(con, ins["evaluation_id"], "08", "Да")
    _set(con, ins["evaluation_id"], "05", "Нет")
    assert {t["mutation"]: t for t in report(con)["tests"]}["insult"]["status"] == "passed"
    _set(con, ins["evaluation_id"], "08", "Нет")
    t = {t["mutation"]: t for t in report(con)["tests"]}["insult"]
    assert t["status"] == "failed" and [m["criterion_id"] for m in t["mismatches"]] == ["08"]
    assert t["mismatches"][0]["expected"] == ["Да"] and t["mismatches"][0]["got"] == "Нет"

    ren = tests["rename_operator"]
    base = {r["criterion_id"]: r["verdict"] for r in con.execute(
        "SELECT criterion_id, verdict FROM criterion_results WHERE evaluation_id=?", (ren["base_evaluation_id"],))}
    for cid, v in base.items():
        _set(con, ren["evaluation_id"], cid, v)
    assert {t["mutation"]: t for t in report(con)["tests"]}["rename_operator"]["status"] == "passed"
    # альтернатива эталона допустима: у t-* 07 = «Да|Нет», 02 = N/A
    _set(con, ren["evaluation_id"], "02", "N/A")
    assert {t["mutation"]: t for t in report(con)["tests"]}["rename_operator"]["status"] == "passed"
    # вердикт вне исходного и эталона — провал
    bad = "Нет" if base["01"] == "Да" else "Да"
    _set(con, ren["evaluation_id"], "01", bad)
    t = {t["mutation"]: t for t in report(con)["tests"]}["rename_operator"]
    assert t["status"] == "failed" and [m["criterion_id"] for m in t["mismatches"]] == ["01"]

    # инъекция: переход в «Да» вне допустимых считается отдельно
    inj = tests["injection"]
    base = {r["criterion_id"]: r["verdict"] for r in con.execute(
        "SELECT criterion_id, verdict FROM criterion_results WHERE evaluation_id=?", (inj["base_evaluation_id"],))}
    for cid, v in base.items():
        _set(con, inj["evaluation_id"], cid, v)
    _set(con, inj["evaluation_id"], "08", "Да")
    rep = report(con, mutations=["injection"])
    if base["08"] != "Да":
        assert rep["injection"]["flipped_to_yes"] == 1 and rep["injection"]["flipped_to_yes_by_criterion"] == {"08": 1}
        assert rep["injection"]["robust"] == 0


def test_regenerate_changed_content_resets(con, monkeypatch):
    from cqa.quality import autotest
    from cqa.quality.mutate import load_config

    _evaluate_bases(con)
    autotest.generate(con, per_mutation=1, mutations=["insult"])
    autotest.run(con, profile="fake", mutations=["insult"])
    assert autotest.report(con)["total"]["evaluated"] == 1
    autotest.generate(con, per_mutation=1, mutations=["insult"])            # то же содержимое — результат сохраняется
    assert autotest.report(con)["total"]["evaluated"] == 1
    cfg = load_config()
    cfg.mutations["insult"].params["text"] = {k: v + " !" for k, v in cfg.mutations["insult"].params["text"].items()}
    autotest.generate(con, per_mutation=1, mutations=["insult"], config=cfg)
    rep = autotest.report(con, config=cfg)
    assert rep["total"]["pending"] == 1 and rep["total"]["evaluated"] == 0
    assert con.execute("SELECT passed, last_run_id FROM autotests").fetchone()[0] is None


def test_cli_generate_and_report(con):
    from typer.testing import CliRunner

    from cqa.cli import app

    r = CliRunner().invoke(app, ["autotest", "generate", "--per-mutation", "1", "--mutations", "insult,disconnect"])
    assert r.exit_code == 0, r.output
    assert "insult" in r.output and "disconnect" in r.output
    r = CliRunner().invoke(app, ["autotest", "run", "--profile", "fake", "--mutations", "insult"])
    assert r.exit_code == 0, r.output
    assert "Автотесты: профиль fake" in r.output and "insult" in r.output
    r = CliRunner().invoke(app, ["autotest", "report", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["total"]["n"] == 2 and data["total"]["evaluated"] == 1
