"""Эталон: импорт в gold_labels, отложенные семьи, деление tune / holdout."""
from __future__ import annotations

import csv

import pytest
from typer.testing import CliRunner

from cqa.quality.compare import options
from cqa.quality.gold import gold_rows, import_gold, load_holdout, split_ids
from conftest import GOLD_CSV, ROOT

HOLDOUT_FAMILIES = {"margin", "d-account", "funding-details", "broker-report", "withdraw-blocked", "form-1042s"}


def _csv_rows():
    with open(GOLD_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_holdout_file():
    assert load_holdout() == HOLDOUT_FAMILIES
    assert load_holdout(ROOT / "eval" / "нет-такого.txt") == set()


def test_import_gold(tmp_db):
    n = import_gold(tmp_db, GOLD_CSV)
    assert n == {"dialogs": 65, "labels": 65 * 8}
    assert import_gold(tmp_db, GOLD_CSV) == n            # повторный импорт заменяет, а не дублирует
    assert tmp_db.execute("SELECT COUNT(*) FROM gold_labels").fetchone()[0] == 520
    r = tmp_db.execute("SELECT * FROM gold_labels WHERE external_id='p1-ru-001' AND criterion_id='02'").fetchone()
    assert r["options"] == "N/A|Да" and r["family"] == "tariff-web" and r["source"] == "gold"

    back = {r["id"]: r for r in gold_rows(tmp_db)}
    for row in _csv_rows():
        for c in (f"c0{i}" for i in range(1, 9)):
            assert options(back[row["id"]][c]) == options(row[c])
        assert back[row["id"]]["family"] == row["family"]


def test_split_ids(tmp_db):
    rows = _csv_rows()
    all_ids = split_ids(rows, "all")
    tune = split_ids(rows, "tune")
    hold = split_ids(rows, "holdout")
    assert len(all_ids) == 65 and len(hold) == 19 and len(tune) == 46
    assert set(tune) | set(hold) == set(all_ids) and not set(tune) & set(hold)
    fam = {r["id"]: r["family"] for r in rows}
    assert {fam[i] for i in hold} == HOLDOUT_FAMILIES
    # строки gold_labels (external_id) — тот же результат
    import_gold(tmp_db, GOLD_CSV)
    db_rows = [dict(r) for r in tmp_db.execute("SELECT external_id, family FROM gold_labels")]
    assert split_ids(db_rows, "holdout") == hold
    assert split_ids(rows, "holdout", holdout={"margin"}) == sorted(i for i in all_ids if fam[i] == "margin")
    with pytest.raises(ValueError):
        split_ids(rows, "test")


def test_cli_gold_import(tmp_db):
    from cqa.cli import app

    r = CliRunner().invoke(app, ["gold", "import", str(GOLD_CSV)])
    assert r.exit_code == 0, r.output
    assert "65 диалогов" in r.output and "holdout: 19" in r.output
