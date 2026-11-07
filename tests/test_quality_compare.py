"""Метрики сравнения разметок (cqa/quality/compare.py) и обёртка eval/compare.py."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from cqa.quality.compare import compare, compare_files, format_text, load, options, read_id_set, run
from conftest import ROOT

DRAFT = ROOT / "eval" / "gold_draft.csv"
OPUS = ROOT / "eval" / "gold_opus.csv"
HOLDOUT = ROOT / "eval" / "holdout.txt"

# вывод прежнего eval/compare.py на этой паре файлов — не должен измениться
LEGACY_OUTPUT = """\
диалогов: 65
крит   точно  совм.  kappa   ±1ст  расхождений
c01     100%   100%   1.00         0
c02      92%   100%   0.85         5
c03     100%   100%   1.00         0
c04      86%   100%   0.64   100%  9
c05      94%   100%   0.74         4
c06      83%   100%   0.63    98%  11
c07      95%   100%   0.89         3
c08     100%   100%   1.00         0

итог: MAE 2.87 балла, расхождение > 5 баллов в 13 из 65 диалогов
жёсткие расхождения (ни основной, ни альтернативный ответ не совпали): 0
"""

HEADER = "id,family,c01,c02,c03,c04,c05,c06,c07,c08\n"
EXPORT_HEADER = "id,language,channel,c01,c02,c03,c04,c05,c06,c07,c08,total,gross,gross_categories\n"


def test_legacy_numbers(checklist):
    res = compare_files(DRAFT, OPUS, checklist)
    m = {x["criterion"]: x for x in res["criteria"]}
    assert res["n"] == 65 and res["missing"] == []
    assert f"{m['04']['exact_rate']:.0%}" == "86%" and f"{m['06']['exact_rate']:.0%}" == "83%"
    assert f"{res['total']['mae']:.2f}" == "2.87"
    assert res["total"]["big"] == 13 and res["total"]["pairs"] == 65
    assert res["hard"] == 0
    assert format_text(res) == LEGACY_OUTPUT


def test_wrapper_script_keeps_output():
    r = subprocess.run([sys.executable, str(ROOT / "eval" / "compare.py"), str(DRAFT), str(OPUS)],
                       capture_output=True, text=True, cwd=ROOT, check=True)
    assert r.stdout == LEGACY_OUTPUT


def test_cli_compare_same_output():
    from cqa.cli import app

    r = CliRunner().invoke(app, ["compare", str(DRAFT), str(OPUS)])
    assert r.exit_code == 0, r.output
    assert r.output == LEGACY_OUTPUT


def test_diff_listing(checklist):
    text = format_text(compare_files(DRAFT, OPUS, checklist), diff=True)
    lines = text.split("id            крит  A            B            совместимо\n")[1].splitlines()
    assert len(lines) == 5 + 9 + 4 + 11 + 3


def test_options():
    assert options("Да|Нет") == ["Да", "Нет"]
    assert options(" 20 | 15 ") == ["20", "15"]
    assert options("") == [] and options(None) == []


def _write(path, text, bom=False):
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return path


def test_export_with_bom_extra_columns_and_empty_answer(tmp_path, checklist):
    gold = _write(tmp_path / "gold.csv", HEADER +
                  "a1,fam-x,Да,N/A,N/A,20,Да,10,Да,Нет\n"
                  "a2,fam-y,Да,Да|N/A,Нет,15,Да,7,Нет,Да\n"
                  "a3,fam-y,Нет,N/A,N/A,20|15,Да,10,N/A,Нет\n")
    exp = _write(tmp_path / "export.csv", EXPORT_HEADER +
                 "a1,RU,agent chat,Да,N/A,N/A,20,Да,10,Да,Нет,100.00,Нет,\n"
                 "a2,KZ,voice call,Да,N/A,Да,,Да,7,Нет,Нет,0,Нет,\n"
                 "a3,RU,agent chat,Нет,N/A,N/A,15,Да,10,N/A,Да,90.00,Да,оскорбление\n", bom=True)
    assert "id" in next(iter(load(exp).values()))           # BOM не попал в имя колонки
    res = compare_files(gold, exp, checklist, by="language")
    m = {x["criterion"]: x for x in res["criteria"]}
    assert res["n"] == 3
    # пустой ответ — расхождение и несовместимость
    assert m["04"]["exact"] == 1 and m["04"]["compatible"] == 2 and m["04"]["missing_b"] == 1
    assert m["02"]["exact"] == 2 and m["02"]["compatible"] == 3
    # итог a2 в экспорте не рассчитать (нет ответа по 04) — в MAE не входит
    assert [u["id"] for u in res["total"]["uncomputed"]] == ["a2"]
    assert res["total"]["pairs"] == 2
    # разрез: language есть только во втором файле
    groups = {g["value"]: g["n"] for g in res["by"]["groups"]}
    assert groups == {"KZ": 1, "RU": 2}
    # ловушки: флаг 08 — эталон «Да» в a2, модель — «Да» в a3
    t = {(x["criterion"], x["label"]): x for x in res["traps"]}
    assert (t["08", "Да"]["tp"], t["08", "Да"]["fp"], t["08", "Да"]["fn"]) == (0, 1, 1)
    assert t["08", "Да"]["recall"] == 0 and t["08", "Да"]["precision"] == 0
    assert t["03", "Нет"]["recall"] == 0 and t["03", "Нет"]["reference"] == 1
    assert t["02", "N/A"]["recall"] == 1 and t["02", "N/A"]["precision"] == 2 / 3
    assert t["07", "N/A"]["recall"] == 1 and t["07", "N/A"]["precision"] == 1
    assert t["01", "Нет"]["recall"] == 1
    text = format_text(res, traps=True)
    assert "ловушки" in text and "по language" in text


def test_both_empty_is_mismatch(tmp_path, checklist):
    a = _write(tmp_path / "a.csv", HEADER + "x,f,Да,N/A,N/A,20,Да,10,,Нет\n")
    b = _write(tmp_path / "b.csv", HEADER + "x,f,Да,N/A,N/A,20,Да,10,,Нет\n")
    m = {x["criterion"]: x for x in compare_files(a, b, checklist)["criteria"]}
    assert m["07"]["exact"] == 0 and m["07"]["compatible"] == 0


def test_only_exclude_by_family_and_id(checklist):
    A, B = load(ROOT / "eval" / "gold.csv"), load(OPUS)
    hold = read_id_set(HOLDOUT)
    only = compare(A, B, checklist, only=hold)
    excl = compare(A, B, checklist, exclude=hold)
    assert only["n"] == 19 and excl["n"] == 46
    assert set(only["ids"]).isdisjoint(excl["ids"])
    fams = {A[i]["family"] for i in only["ids"]}
    assert fams == hold
    by_id = compare(A, B, checklist, only={"p1-ru-001", "margin"}, exclude={"p1-kz-010"})
    assert "p1-ru-001" in by_id["ids"] and "p1-kz-010" not in by_id["ids"]
    assert all(A[i]["family"] == "margin" or i == "p1-ru-001" for i in by_id["ids"])


def test_missing_criterion_column(tmp_path, checklist):
    a = _write(tmp_path / "a.csv", "id,c01,c02,c03,c04,c05,c06,c07\nx,Да,N/A,N/A,20,Да,10,Да\n")
    b = _write(tmp_path / "b.csv", "id,c01,c02,c03,c04,c05,c06,c07\nx,Да,N/A,N/A,20,Да,10,Нет\n")
    res = compare_files(a, b, checklist)
    assert res["criteria_ids"] == ["01", "02", "03", "04", "05", "06", "07"]   # 08 нет ни в одном файле
    assert any("c08" in w for w in res["warnings"])
    # 02 и 03 N/A → знаменатель 65; «Нет» по 07 стоит 5 × 100 / 65
    assert res["total"]["pairs"] == 1 and res["total"]["mae"] == pytest.approx(500 / 65, abs=0.01)


def test_json_and_flags_via_wrapper(tmp_path):
    out = tmp_path / "m.json"
    r = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "compare.py"), str(ROOT / "eval" / "gold.csv"), str(OPUS),
         "--traps", "--exclude", str(HOLDOUT), "--by", "family", "--json", str(out),
         "--checklist", str(ROOT / "config" / "checklists" / "freedom-cqa.yaml")],
        capture_output=True, text=True, cwd=ROOT, check=True)
    assert "диалогов: 46" in r.stdout and "ловушки" in r.stdout and "по family" in r.stdout
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["n"] == 46 and data["filters"]["exclude"] == sorted(read_id_set(HOLDOUT))
    assert {g["value"] for g in data["by"]["groups"]}.isdisjoint(read_id_set(HOLDOUT))
    assert {t["criterion"] for t in data["traps"] if t["label"] == "Да"} == {"08"}


def test_run_json_stdout():
    data = json.loads(run(DRAFT, OPUS, json_out="-"))
    assert data["total"]["big"] == 13


@pytest.mark.parametrize("name", ["gold.csv", "gold_draft.csv", "gold_opus.csv"])
def test_self_compare_is_perfect(checklist, name):
    res = compare_files(ROOT / "eval" / name, ROOT / "eval" / name, checklist)
    assert all(m["exact"] == m["n"] for m in res["criteria"])
    assert res["total"]["mae"] == 0


def test_channel_from_either_file(tmp_path, checklist):
    # 07 только для чатов: в звонке N/A у обеих сторон, хотя канал указан лишь во втором файле
    cfg = checklist.model_copy(update={"criteria": [
        c.model_copy(update={"channels": ["agent chat"]}) if c.id == "07" else c for c in checklist.criteria]})
    a = _write(tmp_path / "a.csv", HEADER + "x,f,Да,N/A,N/A,20,Да,10,Нет,Нет\n")
    b = _write(tmp_path / "b.csv", EXPORT_HEADER + "x,RU,voice call,Да,N/A,N/A,20,Да,10,N/A,Нет,100.00,Нет,\n")
    res = compare_files(a, b, cfg)
    assert res["total"]["rows"] == [{"id": "x", "a": 100.0, "b": 100.0, "diff": 0.0}]
