"""Воркер прогонов: продолжение брошенных прогонов, защита чужих (cqa eval в другом процессе), очередь."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cqa.api.worker import Worker

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"


@pytest.fixture
def queued(tmp_db):
    from cqa.checklist.store import get_active, seed
    from cqa.core.runner import create_run
    from cqa.ingest.service import ingest_payload
    from cqa.settings import get_settings

    seed(tmp_db, get_settings().checklist_path)
    ingest_payload(tmp_db, json.loads(MINI.read_text(encoding="utf-8")), "file")
    vid = get_active(tmp_db)[1]["id"]

    def make() -> int:
        return create_run(tmp_db, uids=["MINI:mini-kz-chat"], version_id=vid, profile_name="fake")
    return tmp_db, make


def _ago(sec: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=sec)).isoformat(timespec="seconds")


async def test_adopt_stale_and_process(queued, tmp_path):
    con, make = queued
    stale, fresh = make(), make()
    con.execute("UPDATE runs SET status='running', created_at=?, started_at=? WHERE id=?", (_ago(3600), _ago(3600), stale))
    con.execute("UPDATE runs SET status='running', created_at=?, started_at=? WHERE id=?", (_ago(3600), _ago(3600), fresh))
    con.execute("INSERT INTO llm_calls(ts, purpose, run_id, profile, provider, model, request_json, status)"
                " VALUES (?, 'judge', ?, 'fake', 'fake', 'fake', '{}', 'ok')", (_ago(5), fresh))

    w = Worker(tmp_path / "unused.db")
    assert w.adopt_stale(con) == [stale]                     # свежая активность — чужой процесс, не трогаем
    status = dict(con.execute("SELECT id, status FROM runs").fetchall())
    assert status == {stale: "queued", fresh: "running"}

    assert w.next_run(con) == stale                          # поставлен воркером — без задержки
    assert con.execute("SELECT status FROM runs WHERE id=?", (stale,)).fetchone()[0] == "running"
    await w.run_one(con, stale)
    r = con.execute("SELECT * FROM runs WHERE id=?", (stale,)).fetchone()
    assert r["status"] == "done" and r["units_done"] == r["units_total"] == 8
    assert w.current is None and w.next_run(con) is None


async def test_queue_grace_for_foreign_runs(queued, tmp_path):
    con, make = queued
    w = Worker(tmp_path / "unused.db")
    foreign = make()                                          # только что поставлен другим процессом
    assert w.next_run(con) is None
    con.execute("UPDATE runs SET created_at=? WHERE id=?", (_ago(60), foreign))
    assert w.next_run(con) == foreign
    assert w.next_run(con) is None                           # уже захвачен

    own = make()
    w.wake(own)
    assert w.next_run(con) == own


async def test_fake_worker_ignores_other_profiles(queued, tmp_path):
    con, make = queued
    real, stale_real = make(), make()
    con.execute("UPDATE runs SET judge_profile='gemini-flash', created_at=? WHERE id IN (?, ?)", (_ago(3600), real, stale_real))
    con.execute("UPDATE runs SET status='running', started_at=? WHERE id=?", (_ago(3600), stale_real))
    w = Worker(tmp_path / "unused.db", only_profile="fake")
    w.wake(real)
    assert w.adopt_stale(con) == [] and w.next_run(con) is None    # демо без сети не трогает облачные прогоны
    fake_run = make()
    w.wake(fake_run)
    assert w.next_run(con) == fake_run


async def test_failed_run_does_not_stop_worker(queued, tmp_path):
    con, make = queued
    run_id = make()
    con.execute("UPDATE runs SET judge_profile='no-such-profile' WHERE id=?", (run_id,))
    w = Worker(tmp_path / "unused.db")
    w.wake(run_id)
    assert w.next_run(con) == run_id
    await w.run_one(con, run_id)
    assert con.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()[0] == "failed"
