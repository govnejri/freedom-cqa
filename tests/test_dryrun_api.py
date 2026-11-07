"""Пробный прогон версии чек-листа и «было/стало»: выбор изменённых критериев, выборка, diff. Профиль fake, без сети."""
from __future__ import annotations

import copy
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"
UIDS = ["MINI:mini-kz-chat", "MINI:mini-ru-call", "MINI:mini-ru-chat"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    import cqa.api.app as app_mod
    from cqa.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "db_path", tmp_path / "cqa.db")
    monkeypatch.setattr(s, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(s, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(s, "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")
    with TestClient(app_mod.create_app(fake=True)) as c:
        yield c


def wait_run(c: TestClient, run_id: int, timeout: float = 30.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = c.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("done", "failed", "cancelled"):
            return r
        time.sleep(0.05)
    raise AssertionError(f"прогон {run_id} не завершился")


def evaluated(c: TestClient) -> dict:
    with open(MINI, "rb") as f:
        assert c.post("/api/datasets/upload", files={"file": ("mini.json", f, "application/json")}).status_code == 200
    run = wait_run(c, c.post("/api/runs", json={"group": "MINI"}).json()["id"])
    assert run["status"] == "done" and run["units_failed"] == 0
    return run


def new_version(c: TestClient, mutate) -> dict:
    """Черновик от активной версии с изменённым конфигом."""
    active = next(v for v in c.get("/api/checklists/freedom-cqa/versions").json() if v["is_active"])
    cfg = copy.deepcopy(active["config"])
    mutate(cfg)
    r = c.post("/api/checklists/freedom-cqa/versions", json={"config": cfg, "comment": "тест"})
    assert r.status_code == 200, r.text
    return r.json()


def _force_no(cfg: dict, cid: str = "05") -> None:
    crit = next(x for x in cfg["criteria"] if x["id"] == cid)
    crit["interpretations"] = crit["interpretations"] + ["[[FAKE:verdict=Нет]]"]   # fake ставит «Нет»


def current(c: TestClient) -> dict[str, int]:
    return {x["uid"]: x["evaluation_id"] for x in c.get("/api/conversations", params={"group": "MINI"}).json()}


def test_dry_run_picks_changed_criteria_and_diff(client):
    evaluated(client)
    before = current(client)
    old = {uid: {cr["criterion_id"]: cr["verdict"] for cr in client.get(f"/api/evaluations/{eid}").json()["criteria"]}
           for uid, eid in before.items()}
    v = new_version(client, _force_no)

    r = client.post(f"/api/checklist-versions/{v['id']}/dry-run", json={})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["kind"] == "dry_run" and run["checklist_version_id"] == v["id"]
    assert run["units_total"] == 3                    # 3 диалога × один изменённый критерий 05, остальные скопированы
    run = wait_run(client, run["id"])
    assert run["status"] == "done" and run["units_failed"] == 0

    d = client.get(f"/api/runs/{run['id']}/diff").json()
    assert d["criteria"] == ["05"] and d["checklist_version"] == v["version"]
    assert d["compared_with_version_id"] is not None and d["baseline"] == "current"
    s = d["summary"]
    assert s["dialogs"] == s["completed"] == s["with_baseline"] == 3
    assert s["verdicts_compared"] == 3 and s["new_verdicts"] == {"05": {"Нет": 3}}
    n_changed = sum(old[u]["05"] != "Нет" for u in old)
    assert s["verdicts_changed"] == n_changed == s["dialogs_changed"]
    assert sum(s["transitions"].get("05", {}).values()) == n_changed
    for dlg in d["dialogs"]:
        assert dlg["baseline_evaluation_id"] == before[dlg["uid"]]
        row = next(x for x in dlg["criteria"] if x["criterion_id"] == "05")
        assert row["old"] == old[dlg["uid"]]["05"] and row["new"] == "Нет" and row["requested"]
        assert row["changed"] == (row["old"] != "Нет")
        assert dlg["changed_criteria"] == (["05"] if row["changed"] else [])
        assert dlg["old_total"] is not None and dlg["new_total"] is not None
        assert dlg["delta"] == round(dlg["new_total"] - dlg["old_total"], 2)
        if row["changed"] and row["old"] == "Да":
            assert dlg["delta"] < 0                    # 05 весит 15: «Да» → «Нет» снижает итог
    deltas = [x["delta"] for x in d["dialogs"]]
    assert s["mean_delta"] == round(sum(deltas) / len(deltas), 2)

    # пробный прогон не меняет текущую оценку диалога
    assert current(client) == before


def test_dry_run_explicit_criteria_uids_and_errors(client):
    evaluated(client)
    v = new_version(client, lambda cfg: _force_no(cfg, "07"))
    r = client.post(f"/api/checklist-versions/{v['id']}/dry-run",
                    json={"uids": ["MINI:mini-kz-chat"], "criteria": ["01", "07"]})
    assert r.status_code == 200, r.text
    assert r.json()["units_total"] == 2
    run = wait_run(client, r.json()["id"])
    d = client.get(f"/api/runs/{run['id']}/diff").json()
    assert d["criteria"] == ["01", "07"] and len(d["dialogs"]) == 1
    assert d["summary"]["verdicts_compared"] == 2

    active_id = next(x for x in client.get("/api/checklists/freedom-cqa/versions").json() if x["is_active"])["id"]
    r = client.post(f"/api/checklist-versions/{active_id}/dry-run", json={})      # активная без родителя — нечего сравнивать
    assert r.status_code == 422
    assert client.post(f"/api/checklist-versions/{v['id']}/dry-run", json={"criteria": ["99"]}).status_code == 422
    assert client.post(f"/api/checklist-versions/{v['id']}/dry-run", json={"uids": ["X:none"]}).status_code == 422
    assert client.post("/api/checklist-versions/99999/dry-run", json={}).status_code == 404
    assert client.get("/api/runs/99999/diff").status_code == 404


def test_prompt_wide_change_takes_all_criteria(client):
    evaluated(client)
    v = new_version(client, lambda cfg: cfg.update(principles=cfg["principles"] + "\nНовое правило."))
    r = client.post(f"/api/checklist-versions/{v['id']}/dry-run", json={"sample": 1})
    assert r.status_code == 200, r.text
    assert r.json()["units_total"] == 8


def test_sample_is_diverse(tmp_db):
    import json

    from cqa.api.routes.dryrun import current_group, sample_uids
    from cqa.ingest.service import ingest_payload

    ingest_payload(tmp_db, json.loads(MINI.read_text(encoding="utf-8")), "file")
    assert current_group(tmp_db) == "MINI"
    two = sample_uids(tmp_db, "MINI", 2)
    rows = {r["uid"]: (r["language"], r["channel"]) for r in tmp_db.execute("SELECT * FROM conversations")}
    assert len({rows[u] for u in two}) == 2
    assert set(sample_uids(tmp_db, "MINI", 10)) == set(UIDS)
