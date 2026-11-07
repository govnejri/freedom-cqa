"""Скоринг (docs/implementation.md §3): перераспределение N/A, шкалы, флаги, каналы, округление."""
from __future__ import annotations

import csv

import pytest

from cqa.core.scoring import score
from cqa.quality.compare import options, total as compare_total
from conftest import GOLD_CSV

# --- прежняя логика eval/compare.py (до переноса в cqa/quality), заморожена для сверки ---
LEGACY_WEIGHTS = {"c01": 10, "c02": 15, "c03": 20, "c04": 20, "c05": 15, "c06": 15, "c07": 5}
LEGACY_SCALES = {"c04": [0, 5, 10, 15, 20], "c06": [0, 4, 7, 10]}


def legacy_total(row, pick=0):
    got = app = 0
    for c in LEGACY_WEIGHTS:
        opts = [v.strip() for v in row[c].split("|")]
        v = opts[min(pick, len(opts) - 1)]
        if v == "N/A":
            continue
        got += LEGACY_WEIGHTS[c] * int(v) / max(LEGACY_SCALES[c]) if c in LEGACY_SCALES else (
            LEGACY_WEIGHTS[c] if v == "Да" else 0)
        app += LEGACY_WEIGHTS[c]
    return round(got * 100 / app, 2) if app else None


ALL_TOP = {"01": "Да", "02": "Да", "03": "Да", "04": "20", "05": "Да", "06": "10", "07": "Да", "08": "Нет"}


def gold_rows():
    with open(GOLD_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def primary_verdicts(row, cfg):
    return {c.id: options(row[f"c{c.id}"])[0] for c in cfg.criteria}


# ---------------------------------------------------------------- сверка с эталоном

def test_gold_totals_match_legacy(checklist):
    rows = gold_rows()
    assert len(rows) == 65
    for row in rows:
        res = score(checklist, primary_verdicts(row, checklist))
        legacy = legacy_total(row)
        assert res.total is not None and legacy is not None
        # прежняя логика округляла float через round(), новая — точно и HALF_UP: разница не больше 0.01
        assert abs(res.total - legacy) <= 0.01 + 1e-9, row["id"]
        assert compare_total(checklist, row) == res.total


def test_sections_sum_to_total_on_gold(checklist):
    for row in gold_rows():
        res = score(checklist, primary_verdicts(row, checklist))
        # секции округляются по отдельности: не дальше полуединицы последнего знака на секцию
        assert sum(s.points for s in res.sections) == pytest.approx(res.total, abs=0.005 * len(res.sections) + 1e-9)
        assert sum(s.max_points for s in res.sections) == pytest.approx(100, abs=0.005 * len(res.sections) + 1e-9)
        for s in res.sections:
            assert s.points <= s.max_points + 1e-9
        for c in res.criteria:
            assert c.points <= c.max_points + 1e-9
            assert c.max_points == c.effective_weight


def test_sections_exact_when_no_na(checklist):
    v = dict(ALL_TOP, **{"04": "15", "06": "7", "02": "Нет"})
    res = score(checklist, v)
    assert res.total == 10 + 0 + 20 + 15 + 15 + 10.5 + 5
    assert sum(s.points for s in res.sections) == res.total
    by_id = {s.section_id: s for s in res.sections}
    assert by_id["start"].points == 10 and by_id["start"].max_points == 25
    assert by_id["security"].points == 35 and by_id["security"].max_points == 40
    assert by_id["conduct"].points == 30.5 and by_id["conduct"].max_points == 35
    assert by_id["gross"].points == 0 and by_id["gross"].max_points == 0


# ---------------------------------------------------------------- правила

def test_scale_step_points_before_redistribution(checklist):
    res = score(checklist, dict(ALL_TOP, **{"06": "7"}))
    c06 = next(c for c in res.criteria if c.criterion_id == "06")
    assert c06.weight == 15
    assert c06.raw_points == 10.5
    assert c06.points == 10.5 and c06.effective_weight == 15
    assert res.total == 95.5


def test_single_na_redistributes(checklist):
    res = score(checklist, dict(ALL_TOP, **{"07": "N/A"}))
    assert res.total == 100
    eff = {c.criterion_id: c.effective_weight for c in res.criteria}
    assert eff["07"] == 0
    assert eff["01"] == pytest.approx(10 * 100 / 95, abs=0.005)
    assert eff["03"] == pytest.approx(20 * 100 / 95, abs=0.005)
    c07 = next(c for c in res.criteria if c.criterion_id == "07")
    assert c07.applicable is False and c07.verdict == "N/A" and c07.points == 0

    # 03 N/A (вес 20) → знаменатель 80; 04 = 10 из 20 → 10 сырых баллов → 12.5 после перераспределения
    res = score(checklist, dict(ALL_TOP, **{"03": "N/A", "04": "10"}))
    c04 = next(c for c in res.criteria if c.criterion_id == "04")
    assert c04.raw_points == 10 and c04.points == 12.5 and c04.max_points == 25
    assert res.total == 87.5


def test_round_half_up(checklist):
    # 03 N/A → знаменатель 80; только 06 = 7 → 10.5 × 100 / 80 = 13.125 → 13.13 (а не банковское 13.12)
    v = {"01": "Нет", "02": "Нет", "03": "N/A", "04": "0", "05": "Нет", "06": "7", "07": "Нет", "08": "Нет"}
    assert score(checklist, v).total == 13.13


def test_all_na_total_none(checklist):
    cfg = checklist.model_copy(update={"criteria": [c.model_copy(update={"na_allowed": True}) if c.weight > 0 else c
                                                    for c in checklist.criteria]})
    v = {c.id: ("N/A" if c.weight > 0 else "Нет") for c in cfg.criteria}
    res = score(cfg, v)
    assert res.total is None
    assert all(c.points == 0 and c.effective_weight == 0 for c in res.criteria)
    assert all(s.points == 0 and s.max_points == 0 for s in res.sections)
    assert res.gross is False


def test_flag_does_not_affect_total(checklist):
    base = dict(ALL_TOP, **{"04": "15", "07": "N/A"})
    no = score(checklist, base, categories={"08": "оскорбление"})
    yes = score(checklist, dict(base, **{"08": "Да"}), categories={"08": "оскорбление"})
    assert no.total == yes.total
    assert [s.points for s in no.sections] == [s.points for s in yes.sections]
    assert no.gross is False and no.gross_categories == []
    assert yes.gross is True and yes.gross_categories == ["оскорбление"]
    c08 = next(c for c in yes.criteria if c.criterion_id == "08")
    assert c08.points == 0 and c08.effective_weight == 0 and c08.weight == 0
    # флаг «Да» без категории — gross без категорий
    assert score(checklist, dict(base, **{"08": "Да"})).gross_categories == []


def test_na_not_allowed_raises(checklist):
    with pytest.raises(ValueError, match="01"):
        score(checklist, dict(ALL_TOP, **{"01": "N/A"}))


def test_missing_verdict_raises(checklist):
    v = dict(ALL_TOP)
    del v["05"]
    with pytest.raises(ValueError, match="05"):
        score(checklist, v)


@pytest.mark.parametrize("cid,bad", [("06", "8"), ("04", "Да"), ("01", "5"), ("02", "да"), ("08", "N/A")])
def test_invalid_verdict_raises(checklist, cid, bad):
    with pytest.raises(ValueError):
        score(checklist, dict(ALL_TOP, **{cid: bad}))


def test_disabled_and_unknown_criteria_ignored(checklist):
    cfg = checklist.model_copy(update={"criteria": [c.model_copy(update={"enabled": False}) if c.id == "07" else c
                                                    for c in checklist.criteria]})
    v = dict(ALL_TOP, **{"99": "что угодно"})
    del v["07"]
    res = score(cfg, v)
    assert res.total == 100
    assert "07" not in {c.criterion_id for c in res.criteria}


def test_channels(checklist):
    # 07 и 01 только для чатов: в звонке — N/A независимо от вердикта (даже без N/A в na_allowed и без вердикта)
    cfg = checklist.model_copy(update={"criteria": [
        c.model_copy(update={"channels": ["agent chat"]}) if c.id in ("01", "07") else c for c in checklist.criteria]})
    v = dict(ALL_TOP, **{"01": "Нет", "07": "Нет"})

    call = score(cfg, v, channel="voice call")
    by = {c.criterion_id: c for c in call.criteria}
    assert by["01"].verdict == "N/A" and not by["01"].applicable
    assert by["07"].verdict == "N/A" and not by["07"].applicable
    assert call.total == 100

    v_missing = {k: x for k, x in v.items() if k not in ("01", "07")}
    assert score(cfg, v_missing, channel="voice call").total == 100

    chat = score(cfg, v, channel="agent chat")
    assert chat.total == 85
    # канал не указан — фильтр не применяется
    assert score(cfg, v).total == 85
