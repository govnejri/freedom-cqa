"""REST: POST /evaluations/{id}/cross-check, POST /appeals/{id}/second-opinion, GET /quality/cross-check (fake)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT = "MINI:mini-ru-chat"


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


def _evaluated(c: TestClient) -> int:
    with open(MINI, "rb") as f:
        assert c.post("/api/datasets/upload", files={"file": ("mini.json", f, "application/json")}).status_code == 200
    run_id = c.post("/api/runs", json={"group": "MINI"}).json()["id"]
    t0 = time.monotonic()
    while c.get(f"/api/runs/{run_id}").json()["status"] not in ("done", "failed", "cancelled"):
        assert time.monotonic() - t0 < 30
        time.sleep(0.05)
    return c.get(f"/api/conversations/{CHAT}/evaluation").json()["id"]


def test_cross_check_endpoints(client):
    c = client
    ev = _evaluated(c)

    r = c.post(f"/api/evaluations/{ev}/cross-check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["criterion_id"] for i in body["criteria"]] == ["03", "08"]
    assert body["checked"] == 2 and all(i["status"] == "done" for i in body["criteria"])

    r = c.post(f"/api/evaluations/{ev}/cross-check", json={"criteria": "all"})
    assert r.status_code == 200 and len(r.json()["criteria"]) == 8
    r = c.post(f"/api/evaluations/{ev}/cross-check", json={"criteria": ["01", "99"]})
    assert r.status_code == 422
    assert c.post("/api/evaluations/999999/cross-check").status_code == 404

    # мнение второй модели видно в карточке оценки
    card = c.get(f"/api/evaluations/{ev}").json()
    so = {cr["criterion_id"]: cr["second_opinion"] for cr in card["criteria"]}
    assert so["03"]["verdict"] and so["03"]["agree"] is True

    q = c.get("/api/quality/cross-check", params={"group": "MINI"})
    assert q.status_code == 200, q.text
    assert q.json()["total"]["n"] == 8 and q.json()["criteria"][0]["agree_rate"] == 1.0


def test_second_opinion_endpoint(client):
    c = client
    ev = _evaluated(c)
    r = c.post("/api/appeals", json={"evaluation_id": ev, "criterion_id": "01", "comment": "Я представился"})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert r.json()["ai_opinion"] is None

    r = c.post(f"/api/appeals/{aid}/second-opinion")
    assert r.status_code == 200, r.text
    ai = r.json()["ai_opinion"]
    assert ai["verdict"] and ai["appeal_supported"] in ("да", "частично", "нет") and ai["blind"] is True
    assert c.get(f"/api/appeals/{aid}").json()["ai_opinion"]["verdict"] == ai["verdict"]
    assert c.post("/api/appeals/999999/second-opinion").status_code == 404
