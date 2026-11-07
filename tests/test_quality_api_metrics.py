"""Экран качества: /quality на файле эталона (eval/gold.csv), потолок разметчиков, /quality/runs, /quality/autotests.

Диалоги синтетические (id и язык — из эталона), вердикты прогона = основной ответ эталона + одно намеренное
расхождение, поэтому метрики известны заранее. Сеть не нужна.
"""
from __future__ import annotations

import pytest
from conftest import GOLD_CSV, ROOT
from fastapi.testclient import TestClient

CEILING = {"01": 1.00, "02": 0.92, "03": 1.00, "04": 0.86, "05": 0.94, "06": 0.83, "07": 0.95, "08": 1.00}  # core-eval §5
WRONG = ("p1-ru-001", "08", "Да")          # эталон: Нет


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
    with TestClient(app_mod.create_app(fake=True, worker=False)) as c:
        yield c


def _gold_ids() -> list[str]:
    from cqa.quality.compare import load
    return sorted(load(GOLD_CSV))


def _seed_dialogs(con) -> list[str]:
    from cqa.ingest.normalize import normalize_record
    from cqa.ingest.parse import parse_record
    from cqa.ingest.store import save_conversation

    uids = []
    for ext in _gold_ids():
        lang = ext.split("-")[1].upper()
        channel = "voice call" if ext.startswith("p4") else "agent chat"
        rec = {"conversation_id": ext, "channel": channel, "language": lang, "transcript": [
            {"timestamp": "00:00", "speaker": "client", "text": "Вопрос"},
            {"timestamp": "00:10", "speaker": "operator", "text": "Здравствуйте, я Ольга. Ответ."}]}
        conv = normalize_record(parse_record(rec), "DEV")
        save_conversation(con, conv, None)
        uids.append(conv.uid)
    return uids


def _gold_run(con, uids: list[str], kind: str = "gold") -> int:
    """Прогон, где вердикты = основной ответ эталона (кроме WRONG)."""
    from cqa.checklist.store import get_active
    from cqa.core.runner import create_run, finalize_evaluation
    from cqa.quality.compare import primary
    from cqa.quality.gold import gold_rows

    cfg, meta = get_active(con)
    gold = {r["id"]: r for r in gold_rows(con)}
    rid = create_run(con, uids=uids, version_id=meta["id"], profile_name="fake", kind=kind)
    for ev in con.execute("SELECT e.id, c.external_id, c.channel FROM evaluations e JOIN conversations c ON c.uid=e.uid"
                          " WHERE e.run_id=?", (rid,)).fetchall():
        for c in cfg.enabled_criteria():
            v = primary(gold[ev["external_id"]].get(f"c{c.id}"))
            if (ev["external_id"], c.id) == WRONG[:2]:
                v = WRONG[2]
            con.execute("UPDATE criterion_results SET status='done', verdict=? WHERE evaluation_id=? AND criterion_id=?",
                        (v, ev["id"], c.id))
        finalize_evaluation(con, ev["id"], cfg, channel=ev["channel"])
    con.execute("UPDATE runs SET status='done', started_at=created_at, finished_at=created_at WHERE id=?", (rid,))
    return rid


def test_quality_without_runs_has_ceiling(client):
    q = client.get("/api/quality").json()
    assert q["run"] is None and q["n"] == 0 and q["gold_dialogs"] == 65
    ceil = {m["criterion"]: m["exact_rate"] for m in q["ceiling"]["criteria"]}
    assert {k: round(v, 2) for k, v in ceil.items()} == CEILING
    assert q["ceiling"]["files"] == ["eval/gold_draft.csv", "eval/gold_opus.csv"]
    assert round(q["ceiling"]["total"]["mae"], 2) == 2.87


def test_quality_metrics_on_gold_file(client):
    from cqa.quality.compare import load
    from cqa.quality.gold import load_holdout, split_ids

    con = client.app.state.con
    uids = _seed_dialogs(con)
    rid = _gold_run(con, uids)
    _gold_run(con, uids[:3], kind="dry_run")              # частичный прогон позже — по умолчанию не берётся

    q = client.get("/api/quality").json()
    assert q["run"]["id"] == rid and q["run"]["profile"] == "fake" and q["run"]["checklist_version"] == 1
    assert q["n"] == 65 and q["missing"] == []
    rates = {m["criterion"]: m for m in q["criteria"]}
    assert set(rates) == set(CEILING)
    for cid, m in rates.items():
        expect = 64 / 65 if cid == "08" else 1.0
        assert m["exact_rate"] == pytest.approx(expect) and m["n"] == 65, cid
        assert m["ceiling_exact_rate"] == pytest.approx(CEILING[cid], abs=0.005)
        assert m["title"]
    assert rates["04"]["near"] == 1.0 and rates["01"]["near"] is None      # ±1 ступень — только у шкал
    assert rates["08"]["compatible_rate"] == pytest.approx(64 / 65)
    assert q["overall"]["exact_rate"] == pytest.approx(519 / 520)
    assert q["total"]["mae"] == 0 and q["total"]["pairs"] == 65            # флаг 08 в итог не входит
    assert q["total"]["ceiling_mae"] == pytest.approx(2.8715, abs=0.001)
    assert q["diffs"] == [{"id": "p1-ru-001", "uid": "DEV:p1-ru-001", "criterion": "08", "gold": "Нет", "model": "Да",
                           "compatible": False}]
    trap = next(t for t in q["traps"] if t["criterion"] == "08" and t["label"] == "Да")
    assert trap["recall"] == 1.0 and trap["fp"] == 1
    assert q["by"]["key"] == "language"
    groups = {g["value"]: g for g in q["by"]["groups"]}
    assert set(groups) == {"RU", "KZ", "EN"} and sum(g["n"] for g in groups.values()) == 65
    assert groups["KZ"]["overall"]["exact_rate"] == 1.0
    assert q["cost"]["n_dialogs"] == 65 and q["cost"]["run_cost_usd"] == 0

    hold = split_ids(load(GOLD_CSV).values(), "holdout", load_holdout(ROOT / "eval" / "holdout.txt"))
    qh = client.get("/api/quality", params={"split": "holdout", "by": "family"}).json()
    assert qh["n"] == len(hold) and qh["by"]["key"] == "family" and qh["ceiling"]["n"] == len(hold)
    qt = client.get("/api/quality", params={"split": "tune", "run": rid}).json()
    assert qt["n"] == 65 - len(hold)

    runs = {r["id"]: r for r in client.get("/api/quality/runs").json()}
    assert runs[rid]["complete"] and runs[rid]["n"] == 65
    assert runs[rid]["exact_rate"] == pytest.approx(519 / 520) and runs[rid]["mae"] == 0
    assert runs[rid]["by_criterion"]["08"] == pytest.approx(64 / 65)
    assert runs[rid]["traps"]["08:Да"] == {"recall": 1.0, "precision": pytest.approx(8 / 9)}   # в эталоне 8 × «Да»
    partial = [r for r in runs.values() if r["kind"] == "dry_run"]
    assert partial and partial[0]["n"] == 3 and not partial[0]["complete"]


def test_quality_errors_and_autotests(client):
    assert client.get("/api/quality", params={"split": "bad"}).status_code == 422
    assert client.get("/api/quality", params={"by": "operator"}).status_code == 422
    assert client.get("/api/quality", params={"run": 999}).status_code == 404
    a = client.get("/api/quality/autotests").json()
    assert a["total"]["n"] == 0 and a["by_mutation"] == [] and a["failed"] == []
