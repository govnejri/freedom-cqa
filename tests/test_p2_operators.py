"""P2, профиль оператора: зоны роста на синтетических вердиктах, ручное назначение (PUT), профиль и список,
LLM-сводка через fake. Временная БД, без сети."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqa.core.scoring import score
from cqa.features.operators import (
    Dialog,
    assign_operator,
    criterion_stats,
    growth_zones,
    load_dialogs,
    strengths,
)
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"
TOP = {"01": "Да", "02": "Да", "03": "Да", "04": "20", "05": "Да", "06": "10", "07": "Да", "08": "Нет"}


def _dialog(cfg, i: int, **changes: str) -> Dialog:
    verdicts = {**TOP, **{k.removeprefix("c"): v for k, v in changes.items()}}
    res = score(cfg, verdicts)
    return Dialog(uid=f"S:{i}", group_id="S", external_id=f"s-{i}", language="RU", channel="agent chat",
                  operator="Синтетик", evaluation_id=i, checklist_version_id=1, status="done", total=res.total,
                  gross=res.gross, verdicts=verdicts, original=dict(verdicts),
                  points={c.criterion_id: (c.points, c.max_points) for c in res.criteria})


def test_growth_zones_on_synthetic_verdicts(checklist):
    cfg = checklist
    dialogs = [
        _dialog(cfg, 1, c02="Нет", c04="10", c07="N/A"),
        _dialog(cfg, 2, c02="N/A", c07="Нет"),
        _dialog(cfg, 3, c02="Нет"),
        _dialog(cfg, 4, c04="5", c06="7"),
    ]
    # 02: вес 15, нарушения 1 + 1 (N/A не считается) из 4 диалогов → 15·2/4 = 7.5
    # 04: вес 20, степени 0.5 + 0.75 → 20·1.25/4 = 6.25
    # 07: вес 5, 1 нарушение (одно N/A) → 5·1/4 = 1.25;  06: вес 15, степень 0.3 → 1.125 — четвёртый, не входит
    zones = growth_zones(cfg, dialogs, dept=dialogs)
    assert [(z["criterion_id"], z["loss"]) for z in zones] == [("02", 7.5), ("04", 6.25), ("07", 1.25)]
    z02, z04, z07 = zones
    assert (z02["violations"], z02["n_applicable"], z02["n_na"], z02["violation_rate"]) == (2, 3, 1, 0.6667)
    assert (z07["violations"], z07["n_applicable"], z07["n_na"]) == (1, 3, 1)
    assert [e["external_id"] for e in z04["examples"]] == ["s-4", "s-1"]       # сначала сильнее нарушение
    assert [e["verdict"] for e in z04["examples"]] == ["5", "10"]

    st06 = criterion_stats(dialogs, cfg.criterion("06"))
    assert st06["loss"] == pytest.approx(1.125, abs=0.006)
    assert st06["mean_step"] == 9.25 and st06["quality"] == 0.925
    st02 = criterion_stats(dialogs, cfg.criterion("02"))
    assert st02["yes_share"] == round(1 / 3, 4) and st02["n_yes"] == 1
    # фактические потерянные баллы после перераспределения весов N/A
    lost = [(d.points["02"][1] - d.points["02"][0]) for d in dialogs]
    assert st02["lost_points"] == round(sum(lost) / 4, 2)

    # всё по верхней ступени → зон роста нет; флаг (вес 0) в зоны не попадает
    clean = [_dialog(cfg, 10, c08="Да"), _dialog(cfg, 11)]
    assert growth_zones(cfg, clean, dept=clean) == []
    good = strengths(cfg, clean, dialogs)
    assert good and all(s["quality"] == 1.0 for s in good)


# ---------------------------------------------------------------- БД и API

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import cqa.api.app as app_mod

    s = get_settings()
    monkeypatch.setattr(s, "db_path", tmp_path / "cqa.db")
    monkeypatch.setattr(s, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(s, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(s, "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")
    with TestClient(app_mod.create_app(fake=True, worker=False)) as c:
        yield c


def _records() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["data"]["records"] if "data" in payload else payload["records"]


def _evaluated(con) -> None:
    from cqa.checklist.store import get_active
    from cqa.core.runner import create_run, run_sync
    from cqa.ingest.service import ingest_records

    ingest_records(con, "T", _records(), "file")
    ingest_records(con, "AUTOTEST", _records(), "file")          # автотесты в профиль не попадают
    uids = [r[0] for r in con.execute("SELECT uid FROM conversations ORDER BY uid")]
    vid = get_active(con)[1]["id"]
    assert run_sync(con, create_run(con, uids=uids, version_id=vid, profile_name="fake"))["units_failed"] == 0


def test_put_operator_and_profile(client):
    c = client
    con = c.app.state.con
    _evaluated(con)
    ops = c.get("/api/operators").json()
    assert ops["n_dialogs"] == 3 and ops["unassigned"] == ["T:mini-ru-call"]     # в звонке имя скрыто
    assert sorted(o["name"] for o in ops["operators"]) == ["Айгүл", "Ольга"]

    r = c.put("/api/conversations/T:mini-ru-call/operator", json={"name": "  Ольга "})
    assert r.status_code == 200, r.text
    assert r.json() == {"uid": "T:mini-ru-call", "operator_name": "Ольга", "auto_operator_name": None, "manual": True}
    assert con.execute("SELECT operator_name FROM conversations WHERE uid='T:mini-ru-call'").fetchone()[0] == "Ольга"
    assert c.put("/api/conversations/T:mini-kz-chat/operator", json={"name": "Ольга"}).json()["auto_operator_name"] == "Айгүл"
    assert c.get("/api/conversations/T:mini-kz-chat").json()["operator_name"] == "Ольга"
    assert c.put("/api/conversations/T:nope/operator", json={"name": "X"}).status_code == 404

    ops = c.get("/api/operators").json()
    assert ops["unassigned"] == [] and [(o["name"], o["n_dialogs"], o["manual"]) for o in ops["operators"]] == [("Ольга", 3, 2)]

    p = c.get("/api/operators/ольга").json()                   # имя без учёта регистра
    assert p["name"] == "Ольга" and p["n_dialogs"] == 3 and p["department"]["n_dialogs"] == 3
    assert {d["uid"] for d in p["dialogs"]} == {"T:mini-ru-chat", "T:mini-kz-chat", "T:mini-ru-call"}
    assert [x["criterion_id"] for x in p["criteria"]] == ["01", "02", "03", "04", "05", "06", "07", "08"]
    dialogs = load_dialogs(con)
    from cqa.checklist.store import get_active
    cfg = get_active(con)[0]
    expected = growth_zones(cfg, dialogs, dialogs)
    assert [z["criterion_id"] for z in p["growth_zones"]] == [z["criterion_id"] for z in expected]
    assert 1 <= len(p["growth_zones"]) <= 3
    for z in p["growth_zones"]:
        assert z["examples"] and all(e["uid"].startswith("T:") for e in z["examples"])
        assert all(e["explanation"] for e in z["examples"])
    assert p["gross_count"] == sum(d.gross for d in dialogs)
    assert c.get("/api/operators/Никто").status_code == 404

    # перезагрузка набора перезаписывает столбец, но ручное назначение сохраняется
    from cqa.ingest.service import ingest_records
    ingest_records(con, "T", _records(), "file")
    assert {d.uid: d.operator for d in load_dialogs(con)}["T:mini-kz-chat"] == "Ольга"

    # пустое имя — вернуть имя из приветствия
    r = c.put("/api/conversations/T:mini-kz-chat/operator", json={"name": ""}).json()
    assert r == {"uid": "T:mini-kz-chat", "operator_name": "Айгүл", "auto_operator_name": "Айгүл", "manual": False}
    assert c.get("/api/operators/Айгүл").json()["n_dialogs"] == 1


def test_operator_summary_fake_and_cache(client):
    c = client
    _evaluated(c.app.state.con)
    r = c.post("/api/operators/Ольга/summary")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["summary"] and s["profile"] == "fake" and s["n_dialogs"] == 1 and s["cache_hit"] is False
    again = c.post("/api/operators/Ольга/summary").json()
    assert again["cache_hit"] is True and again["summary"] == s["summary"]
    call = c.app.state.con.execute("SELECT purpose, request_json FROM llm_calls WHERE id=?", (s["llm_call_id"],)).fetchone()
    assert call["purpose"] == "operator_summary" and "зоны роста" in call["request_json"]
    assert c.post("/api/operators/Никто/summary").status_code == 404


def test_assign_operator_direct(tmp_db):
    from cqa.ingest.service import ingest_records

    ingest_records(tmp_db, "T", _records(), "file")
    with pytest.raises(KeyError):
        assign_operator(tmp_db, "T:none", "X")
    assert assign_operator(tmp_db, "T:mini-ru-chat", "Ольга")["manual"] is False      # то же имя — не назначение
