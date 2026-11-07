"""REST API: загрузка → прогон (воркер) → оценка → апелляция → решение → экспорт. Профиль fake, без сети.

Все ответы API проверяются на отсутствие `sig=` (в фикстуре у звонка audio_url с sig=FAKESIG).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"
CHAT, KZ, CALL = "MINI:mini-ru-chat", "MINI:mini-kz-chat", "MINI:mini-ru-call"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import cqa.api.app as app_mod
    from cqa.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "db_path", tmp_path / "cqa.db")
    monkeypatch.setattr(s, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(s, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(s, "build_dir", tmp_path / "build")
    monkeypatch.setattr(app_mod, "DEV_JSON", tmp_path / "no-dev.json")   # тесты не зависят от data/dev.json

    responses = []
    with TestClient(app_mod.create_app(fake=True)) as c:
        orig = c.request

        def recording(*args, **kwargs):
            r = orig(*args, **kwargs)
            responses.append(r)
            return r

        c.request = recording
        yield c
    checked = 0
    for r in responses:
        ctype = r.headers.get("content-type", "")
        if "json" in ctype or "text" in ctype or "csv" in ctype:
            assert "sig=" not in r.text, f"{r.request.method} {r.request.url}: в ответе sig="
            checked += 1
    assert checked > 0


def upload(c: TestClient) -> dict:
    with open(MINI, "rb") as f:
        r = c.post("/api/datasets/upload", files={"file": ("mini.json", f, "application/json")})
    assert r.status_code == 200, r.text
    return r.json()


def wait_run(c: TestClient, run_id: int, timeout: float = 30.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = c.get(f"/api/runs/{run_id}")
        assert r.status_code == 200
        if r.json()["status"] in ("done", "failed", "cancelled"):
            return r.json()
        time.sleep(0.05)
    raise AssertionError(f"прогон {run_id} не завершился за {timeout} с")


def evaluated(c: TestClient) -> dict:
    upload(c)
    r = c.post("/api/runs", json={"group": "MINI"})
    assert r.status_code == 200, r.text
    run = wait_run(c, r.json()["id"])
    assert run["status"] == "done" and run["units_done"] == run["units_total"] == 24 and run["units_failed"] == 0
    return run


def test_health_and_datasets(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["db"] and h["active_checklist_version_id"]
    assert set(h["roles"]) == {"judge", "strong", "cross_check", "signals"}
    assert set(h["roles"].values()) == {"fake"}
    assert set(h["keys"]) == {"GEMINI_API_KEY", "LOCAL_LLM_BASE_URL", "CQA_API_URL"}
    assert all(isinstance(v, bool) for v in h["keys"].values())

    rep = upload(client)
    assert rep["group_id"] == "MINI" and rep["total_records"] == 3 and rep["conversations"] == 3
    assert rep["with_audio_url"] == 1 and rep["audio"] is None
    ds = {d["group_id"]: d for d in client.get("/api/datasets").json()}
    assert ds["MINI"]["n_conversations"] == 3 and ds["MINI"]["n_evaluated"] == 0

    r = client.post("/api/datasets/upload", files={"file": ("x.json", b"not json", "application/json")})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == "file"

    conv = client.get(f"/api/conversations/{CALL}").json()
    assert conv["channel"] == "voice call" and conv["has_audio"] is False and conv["audio_url"] is None
    assert conv["messages"][0]["mid"] == "m1" and conv["facts"]["hold_range"]
    assert client.get("/api/conversations/MINI:nope").status_code == 404


def test_run_evaluation_appeal_decision_export(client, tmp_path):
    from cqa.quality.compare import load

    evaluated(client)
    ev = client.get(f"/api/conversations/{CHAT}/evaluation").json()
    assert ev["status"] == "done" and ev["profile"] == "fake" and ev["checklist_version"] == 1
    assert ev["total"] is not None and ev["final_total"] == ev["total"]
    assert ev["n_llm_calls"] >= 8 and len(ev["criteria"]) == 8
    crit = {c["criterion_id"]: c for c in ev["criteria"]}
    c01 = crit["01"]
    assert c01["title"] == "Установление контакта" and c01["section"] == "start" and c01["type"] == "yes_no"
    assert c01["score"]["verdict"] == c01["final_verdict"] == c01["verdict"]
    assert client.get(f"/api/evaluations/{ev['id']}").json() == ev

    # журнал вызовов: без запроса в списке, с запросом в деталях
    calls = client.get(f"/api/evaluations/{ev['id']}/calls").json()
    assert len(calls) == ev["n_llm_calls"] and all(c["request"] is None for c in calls)
    detail = client.get(f"/api/llm-calls/{calls[0]['id']}").json()
    assert detail["request"]["messages"] and detail["response_text"]

    # повторный прогон по одному критерию: остальные копируются, этот — из кэша LLM
    r = client.post("/api/runs", json={"group": "MINI", "criteria": ["01"]}).json()
    run2 = wait_run(client, r["id"])
    assert run2["units_total"] == 3 and run2["units_cached"] == 3 and run2["cost_usd"] == 0
    ev = client.get(f"/api/conversations/{CHAT}/evaluation").json()
    assert ev["run_id"] == run2["id"] and ev["total"] is not None

    # апелляция
    new = "Нет" if c01["verdict"] == "Да" else "Да"
    bad = client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "99", "comment": "x"})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"] == "criterion_id"
    bad = client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "01", "comment": "x",
                                            "counter_evidence": ["m999"]})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"] == "counter_evidence.0"
    r = client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "01",
                                          "comment": "Оператор поздоровался во втором сообщении", "counter_evidence": ["7"]})
    assert r.status_code == 200, r.text
    appeal = r.json()
    assert appeal["status"] == "open" and appeal["counter_evidence"] == ["m7"]
    assert appeal["criterion_title"] == "Установление контакта" and appeal["original"]["verdict"] == c01["verdict"]
    idx = [m["idx"] for m in appeal["fragment"]]
    assert idx == sorted(set(idx)) and 7 in idx and {5, 6, 8, 9} <= set(idx)
    assert client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "01",
                                             "comment": "ещё раз"}).status_code == 409
    assert [a["id"] for a in client.get("/api/appeals", params={"status": "open"}).json()] == [appeal["id"]]
    ev_open = client.get(f"/api/evaluations/{ev['id']}").json()
    c = next(x for x in ev_open["criteria"] if x["criterion_id"] == "01")
    assert c["appeal_id"] == appeal["id"] and c["appeal_status"] == "open"

    # решение
    r = client.post(f"/api/appeals/{appeal['id']}/decision", json={"action": "change", "new_verdict": "N/A", "reason": "x"})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == "new_verdict"
    r = client.post(f"/api/appeals/{appeal['id']}/decision",
                    json={"action": "change", "new_verdict": new, "reason": "Пересмотр по трактовке", "make_precedent": True})
    assert r.status_code == 200, r.text
    decided = r.json()
    assert decided["status"] == "decided" and decided["decision"]["new_verdict"] == new
    assert decided["prev_final_total"] == ev["total"]
    assert decided["new_final_total"] is not None and decided["new_final_total"] != ev["total"]
    assert client.post(f"/api/appeals/{appeal['id']}/decision",
                       json={"action": "confirm", "reason": "повтор"}).status_code == 409

    after = client.get(f"/api/conversations/{CHAT}/evaluation").json()
    assert after["total"] == ev["total"]                       # исходный итог не меняется
    assert after["final_total"] == decided["new_final_total"]
    c = next(x for x in after["criteria"] if x["criterion_id"] == "01")
    assert c["verdict"] == c01["verdict"] and c["final_verdict"] == new and c["score"]["verdict"] == new
    assert c["appeal_status"] == "decided"
    s = next(x for x in client.get("/api/conversations", params={"group": "MINI"}).json() if x["uid"] == CHAT)
    assert s["total"] == ev["total"] and s["final_total"] == decided["new_final_total"] and s["open_appeals"] == 0

    # экспорт: исходный и финальный
    r = client.get("/api/export", params={"group": "MINI", "format": "csv", "final": 1})
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    (tmp_path / "final.csv").write_bytes(r.content)
    r = client.get("/api/export", params={"group": "MINI", "format": "csv"})
    (tmp_path / "orig.csv").write_bytes(r.content)
    final, orig = load(tmp_path / "final.csv"), load(tmp_path / "orig.csv")
    assert set(final) == set(orig) == {"mini-ru-chat", "mini-kz-chat", "mini-ru-call"}
    assert final["mini-ru-chat"]["c01"] == new and orig["mini-ru-chat"]["c01"] == c01["verdict"]
    assert float(final["mini-ru-chat"]["total"]) == decided["new_final_total"]
    assert float(orig["mini-ru-chat"]["total"]) == ev["total"]
    js = client.get("/api/export", params={"group": "MINI", "format": "json", "final": 1}).json()
    assert len(js) == 3
    assert client.get("/api/export", params={"format": "xml"}).status_code == 422


def test_conversation_filters(client):
    evaluated(client)
    get = lambda **q: client.get("/api/conversations", params=q).json()
    allc = get(group="MINI")
    assert [x["uid"] for x in allc] == [KZ, CALL, CHAT]          # sort=id: по external_id
    assert all(x["status"] == "done" and x["total"] is not None for x in allc)
    assert [x["uid"] for x in get(lang="kz")] == [KZ]
    assert [x["uid"] for x in get(channel="voice call")] == [CALL]
    assert [x["uid"] for x in get(q="ru-c")] == [CALL, CHAT]
    assert get(group="NOPE") == []

    by_score = get(group="MINI", sort="-score")
    scores = [x["final_total"] for x in by_score]
    assert scores == sorted(scores, reverse=True)
    assert [x["final_total"] for x in get(group="MINI", sort="score")] == sorted(scores)
    lo = min(scores)
    assert len(get(score_min=lo)) == 3 and len(get(score_max=lo)) == scores.count(lo)
    assert get(score_min=lo + 0.001, score_max=lo + 0.001) == []

    for flag, field in (("gross", "gross"), ("review", "needs_review")):
        yes, no = get(**{flag: 1}), get(**{flag: 0})
        assert all(x[field] for x in yes) and not any(x[field] for x in no)
        assert len(yes) + len(no) == 3

    assert get(appeal=1) == []
    ev = client.get(f"/api/conversations/{KZ}/evaluation").json()
    assert client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "05",
                                             "comment": "не согласен"}).status_code == 200
    assert [x["uid"] for x in get(appeal=1)] == [KZ]
    assert [x["open_appeals"] for x in get(appeal=1)] == [1]
    assert len(get(appeal=0)) == 2


def test_audio_range(client, tmp_path):
    from cqa.db.conn import connect
    from cqa.ingest.store import set_audio
    from cqa.settings import get_settings

    upload(client)
    assert client.get(f"/api/conversations/{CALL}/audio").status_code == 404
    data = b"ID3" + bytes(range(256)) * 20
    path = Path(get_settings().audio_dir) / "MINI_mini-ru-call.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    con = connect(get_settings().db_path)
    set_audio(con, CALL, "ready", path)
    con.close()

    conv = client.get(f"/api/conversations/{CALL}").json()
    assert conv["has_audio"] is True and conv["audio_url"] == f"/api/conversations/{CALL}/audio"
    r = client.get(conv["audio_url"], headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206 and r.content == data[:1024]
    assert r.headers["content-range"] == f"bytes 0-1023/{len(data)}"
    assert r.headers["content-type"] == "audio/mpeg"
    r = client.get(conv["audio_url"], headers={"Range": "bytes=2000-"})
    assert r.status_code == 206 and r.content == data[2000:]
    r = client.head(conv["audio_url"], headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206 and r.headers["content-length"] == "1024"
    r = client.get(conv["audio_url"])
    assert r.status_code == 200 and r.content == data and r.headers["accept-ranges"] == "bytes"
    assert client.get(f"/api/conversations/{CHAT}/audio").status_code == 404
    s = next(x for x in client.get("/api/conversations").json() if x["uid"] == CALL)
    assert s["has_audio"] is True


def test_checklist_versions(client):
    h = client.get("/api/health").json()
    vid = h["active_checklist_version_id"]
    v = client.get(f"/api/checklist-versions/{vid}").json()
    assert v["is_active"] and v["status"] == "published" and v["config"]["id"] == "freedom-cqa"
    cid = v["checklist_id"]

    cfg = v["config"]
    cfg["criteria"][3]["weight"] = "много"
    r = client.post(f"/api/checklists/{cid}/versions", json={"config": cfg, "comment": "ошибка"})
    assert r.status_code == 422
    locs = {e["loc"] for e in r.json()["detail"]}
    assert "criteria.3.weight" in locs and all(e["msg"] for e in r.json()["detail"])

    cfg["criteria"][3]["weight"] = 20
    cfg["criteria"][3]["scale"] = None                          # шкала без ступеней — ошибка критерия
    r = client.post(f"/api/checklists/{cid}/versions", json={"config": cfg})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == "criteria.3"
    r = client.post(f"/api/checklists/{cid}/versions", json={"config": {**v["config"], "id": "other"}})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == "id"
    r = client.post(f"/api/checklists/{cid}/versions", json={"comment": "без конфига"})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == "config"

    cfg = client.get(f"/api/checklist-versions/{vid}").json()["config"]
    cfg["criteria"][0]["title"] = "Приветствие и представление"
    r = client.post(f"/api/checklists/{cid}/versions", json={"config": cfg, "comment": "новое название"})
    assert r.status_code == 200, r.text
    nv = r.json()
    assert nv["status"] == "draft" and not nv["is_active"] and nv["version"] == v["version"] + 1
    assert nv["comment"] == "новое название"
    versions = client.get(f"/api/checklists/{cid}/versions").json()
    assert [x["id"] for x in versions] == [nv["id"], vid]      # новые первыми
    d = client.get(f"/api/checklists/{cid}/diff", params={"a": vid, "b": nv["id"]}).json()
    assert [(x["id"], x["fields"]) for x in d] == [("01", ["title"])]

    p = client.post(f"/api/checklist-versions/{nv['id']}/publish").json()
    assert p["is_active"] and p["status"] == "published"
    assert client.get("/api/health").json()["active_checklist_version_id"] == nv["id"]
    assert client.get("/api/checklist-versions/999").status_code == 404

    # новая версия применяется к диалогам: заголовок критерия в оценке — из версии оценки
    upload(client)
    r = client.post("/api/runs", json={"uids": [CHAT]}).json()
    assert r["checklist_version_id"] == nv["id"]
    wait_run(client, r["id"])
    ev = client.get(f"/api/conversations/{CHAT}/evaluation").json()
    assert ev["checklist_version_id"] == nv["id"] and ev["criteria"][0]["title"] == "Приветствие и представление"


def test_runs_validation_cancel_retry(client):
    upload(client)
    assert client.post("/api/runs", json={"uids": ["MINI:nope"]}).status_code == 422
    assert client.post("/api/runs", json={"group": "NOPE"}).status_code == 422
    r = client.post("/api/runs", json={"group": "MINI", "criteria": ["42"]})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == "criteria"
    assert client.post("/api/runs", json={"group": "MINI", "checklist_version_id": 999}).status_code == 422
    assert client.get("/api/runs/999").status_code == 404

    # в режиме fake любой профиль заменяется на fake: сеть не нужна
    r = client.post("/api/runs", json={"uids": [CHAT], "profile": "gemini-flash"}).json()
    assert r["judge_profile"] == "fake"
    run = wait_run(client, r["id"])
    assert run["status"] == "done"
    r = client.post(f"/api/runs/{run['id']}/retry-failed").json()
    assert wait_run(client, r["id"])["status"] == "done"
    c = client.post(f"/api/runs/{run['id']}/cancel").json()
    assert c["status"] == "done"                                # завершённый прогон не отменяется
    assert [x["id"] for x in client.get("/api/runs").json()][:1] == [run["id"]]


def test_settings_roles(client):
    s = client.get("/api/settings").json()
    assert s["fake"] is True and set(s["roles"].values()) == {"fake"}
    names = {p["name"] for p in s["profiles"]}
    assert {"fake", "local", "gemini-flash"} <= names
    assert next(p for p in s["profiles"] if p["name"] == "fake")["available"] is True
    assert next(p for p in s["profiles"] if p["name"] == "local")["local"] is True

    r = client.put("/api/settings", json={"roles": {"judge": "local"}})
    assert r.status_code == 200 and r.json()["overrides"] == {"judge": "local"}
    assert r.json()["roles"]["judge"] == "fake"                # режим fake сильнее переопределения
    assert client.put("/api/settings", json={"roles": {"boss": "local"}}).json()["detail"][0]["loc"] == "roles.boss"
    assert client.put("/api/settings", json={"roles": {"judge": "nope"}}).status_code == 422
    assert client.put("/api/settings", json={"roles": {"judge": None}}).json()["overrides"] == {}


def test_settings_override_used_without_fake(tmp_db):
    from cqa.api.deps import effective_roles, resolve_role, save_role_overrides
    from cqa.settings import load_models_config

    default = load_models_config()["roles"]["judge"]
    assert resolve_role(tmp_db, "judge", False) == default
    save_role_overrides(tmp_db, {"judge": "local"})
    assert resolve_role(tmp_db, "judge", False) == "local"
    assert resolve_role(tmp_db, "judge", True) == "fake"
    save_role_overrides(tmp_db, {"judge": "deleted-profile"})       # профиль пропал из models.yaml — игнорируется
    assert effective_roles(tmp_db, False)["judge"] == default


def test_fetch_dataset(client, monkeypatch, tmp_path):
    import json

    import cqa.api.routes.datasets as ds
    from cqa.ingest import fetch

    payload = json.loads(MINI.read_text(encoding="utf-8"))
    raw = tmp_path / "raw.json"
    raw.write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(fetch, "fetch_group_raw", lambda group: (payload, raw))
    monkeypatch.setattr(ds, "_download_audio", lambda db_path, group: calls.append(group))

    r = client.post("/api/datasets/fetch", json={"group_id": "CTRL"})
    assert r.status_code == 200, r.text
    assert r.json()["group_id"] == "CTRL" and r.json()["conversations"] == 3 and r.json()["audio"] == "scheduled"
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    assert calls == ["CTRL"]
    assert {d["group_id"]: d["source"] for d in client.get("/api/datasets").json()}["CTRL"] == "api"

    def boom(group):
        raise fetch.FetchError("API недоступен: ConnectError")

    monkeypatch.setattr(fetch, "fetch_group_raw", boom)
    r = client.post("/api/datasets/fetch", json={"group_id": "CTRL"})
    assert r.status_code == 502 and r.json()["detail"] == "API недоступен: ConnectError"
    assert client.post("/api/datasets/fetch", json={"group_id": "DEV&x=1"}).status_code == 422


def test_index_serves_build(client):
    from cqa.settings import get_settings

    page = Path(get_settings().build_dir) / "cqa.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("<!doctype html><title>CQA build</title>", encoding="utf-8")
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "CQA build" in r.text
