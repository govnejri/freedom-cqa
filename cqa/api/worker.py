"""Фоновый воркер прогонов: задача asyncio в том же event loop, что и API, со своим соединением SQLite.

- берёт прогоны `queued` по одному (старые первыми) и вызывает `await process_run(con, run_id)`;
- на старте возвращает в очередь (`reset_for_resume`) прогоны `running`, брошенные прошлым процессом;
- прогон `running` с недавней активностью (вызовы LLM за последние STALE_SEC) не трогает: его, скорее всего,
  ведёт другой процесс (`cqa eval`). Такие прогоны проверяются снова, пока воркер простаивает;
- прогон `queued`, поставленный не через API (другим процессом), берётся с задержкой QUEUE_GRACE_SEC:
  `cqa eval` создаёт прогон и сразу сам переводит его в `running`, двойной обработки не будет;
- only_profile (режим fake): воркер берёт только прогоны этого профиля — демо без сети не продолжит
  чужой прогон с облачной моделью.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from cqa.db.conn import connect, now_iso

log = logging.getLogger(__name__)

POLL_SEC = 2.0
STALE_SEC = 600.0
STALE_CHECK_SEC = 60.0
QUEUE_GRACE_SEC = 3.0


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def last_activity(con: sqlite3.Connection, run_id: int) -> float | None:
    """Время последней активности прогона: последний вызов LLM или старт."""
    row = con.execute("SELECT r.started_at, r.created_at, (SELECT MAX(ts) FROM llm_calls WHERE run_id=r.id) AS last_call"
                      " FROM runs r WHERE r.id=?", (run_id,)).fetchone()
    if row is None:
        return None
    stamps = [t for t in (_ts(row["last_call"]), _ts(row["started_at"]), _ts(row["created_at"])) if t is not None]
    return max(stamps) if stamps else None


class Worker:
    def __init__(self, db_path: str | Path, *, poll_sec: float = POLL_SEC, stale_sec: float = STALE_SEC,
                 only_profile: str | None = None):
        self.db_path = Path(db_path)
        self.poll_sec = poll_sec
        self.stale_sec = stale_sec
        self.only_profile = only_profile             # режим fake: чужие профили не трогаем (без сети)
        self.current: int | None = None             # прогон в работе
        self._own: set[int] = set()                  # прогоны, поставленные через API (без задержки)
        self._warned: set[int] = set()
        self._event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._con: sqlite3.Connection | None = None
        self._last_stale_check = 0.0

    # ------------------------------------------------------------ управление
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="cqa-worker")

    def wake(self, run_id: int | None = None) -> None:
        if run_id is not None:
            self._own.add(run_id)
        self._event.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._con is not None:
            self._con.close()
            self._con = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------ очередь
    def _runs(self, con: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
        sql, args = "SELECT id, created_at FROM runs WHERE status=?", [status]
        if self.only_profile is not None:
            sql += " AND judge_profile=?"
            args.append(self.only_profile)
        return con.execute(sql + " ORDER BY id", args).fetchall()

    def adopt_stale(self, con: sqlite3.Connection) -> list[int]:
        """`running`, брошенные другим процессом (нет активности stale_sec) → обратно в очередь."""
        from cqa.core.runner import reset_for_resume

        adopted = []
        now = time.time()
        for r in self._runs(con, "running"):
            if r["id"] == self.current:
                continue
            last = last_activity(con, r["id"])
            if last is not None and now - last < self.stale_sec:
                if r["id"] not in self._warned:
                    self._warned.add(r["id"])
                    log.warning("прогон %s выполняется другим процессом (активность %.0f с назад) — не трогаю",
                                r["id"], now - last)
                continue
            reset_for_resume(con, r["id"])
            self._own.add(r["id"])
            adopted.append(r["id"])
            log.info("прогон %s возвращён в очередь после рестарта", r["id"])
        return adopted

    def next_run(self, con: sqlite3.Connection) -> int | None:
        """Следующий прогон из очереди; захват атомарный (queued → running)."""
        now = time.time()
        for r in self._runs(con, "queued"):
            if r["id"] not in self._own:
                created = _ts(r["created_at"])
                if created is not None and now - created < QUEUE_GRACE_SEC:
                    continue
            claimed = con.execute(
                "UPDATE runs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=? AND status='queued'",
                (now_iso(), r["id"]),
            ).rowcount
            if claimed:
                return int(r["id"])
        return None

    async def run_one(self, con: sqlite3.Connection, run_id: int) -> None:
        from cqa.core.runner import process_run

        self.current = run_id
        try:
            r = await process_run(con, run_id)
            log.info("прогон %s: %s, done %s/%s, failed %s", run_id, r.get("status"), r.get("units_done"),
                     r.get("units_total"), r.get("units_failed"))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — упавший прогон не останавливает воркер
            log.exception("прогон %s упал", run_id)
            con.execute("UPDATE runs SET status='failed', finished_at=? WHERE id=? AND status IN ('queued','running')",
                        (now_iso(), run_id))
        finally:
            self.current = None
            self._own.discard(run_id)

    async def _loop(self) -> None:
        con = self._con = connect(self.db_path)
        try:
            self.adopt_stale(con)
        except Exception:  # noqa: BLE001
            log.exception("воркер: не удалось проверить брошенные прогоны")
        self._last_stale_check = time.monotonic()
        while True:
            try:
                run_id = self.next_run(con)
            except sqlite3.Error:
                log.exception("воркер: ошибка чтения очереди")
                run_id = None
            if run_id is not None:
                await self.run_one(con, run_id)
                continue
            if time.monotonic() - self._last_stale_check >= STALE_CHECK_SEC:
                self._last_stale_check = time.monotonic()
                try:
                    if self.adopt_stale(con):
                        continue
                except sqlite3.Error:
                    log.exception("воркер: ошибка проверки брошенных прогонов")
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), self.poll_sec)
            except TimeoutError:
                pass
