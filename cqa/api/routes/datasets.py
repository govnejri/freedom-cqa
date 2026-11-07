"""Наборы диалогов: загрузка из API организаторов, загрузка файлом, список.

Ссылка API и audio_url содержат `sig=`: в ответы и ошибки не попадают (fetch.py маскирует, здесь — ещё раз).
mp3 звонков после загрузки из API скачиваются в фоне (отдельный поток и соединение SQLite).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from cqa.api.deps import get_con, invalid
from cqa.llm.redact import redact
from cqa.settings import get_settings

router = APIRouter(tags=["datasets"])
log = logging.getLogger(__name__)

_GROUP_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class FetchBody(BaseModel):
    group_id: str
    audio: bool = True          # скачать mp3 звонков в фоне


def _clean(v: Any) -> Any:
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def _report(report, audio: str | None) -> dict[str, Any]:
    d = dataclasses.asdict(report)
    d.update(total_records=report.records, loaded=report.conversations, note=report.summary(), audio=audio)
    return _clean(d)


def _download_audio(db_path: Path, group_id: str) -> None:
    from cqa.db.conn import connect
    from cqa.ingest.audio import download_audio

    con = connect(db_path)
    try:
        rep = download_audio(con, group_id)
        log.info(redact(rep.summary()))
    except Exception as e:  # noqa: BLE001 — фон: только запись в лог, без ссылок
        from cqa.ingest.fetch import redact as redact_url
        log.warning("аудио %s: %s", group_id, redact_url(f"{type(e).__name__}: {e}"))
    finally:
        con.close()


def schedule_audio(request: Request, group_id: str) -> None:
    tasks: set = request.app.state.bg_tasks
    task = asyncio.create_task(asyncio.to_thread(_download_audio, Path(get_settings().db_path), group_id))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.get("/datasets")
async def list_datasets(include_service: bool = False,
                        con: sqlite3.Connection = Depends(get_con)) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT d.group_id, d.source, d.fetched_at, d.total_records,"
        " (SELECT COUNT(*) FROM conversations c WHERE c.group_id = d.group_id) AS n_conversations,"
        " (SELECT COUNT(*) FROM conversations c WHERE c.group_id = d.group_id"
        "   AND c.current_evaluation_id IS NOT NULL) AS n_evaluated,"
        " (SELECT COUNT(*) FROM conversations c WHERE c.group_id = d.group_id AND c.audio_status != 'none') AS n_audio,"
        " (SELECT COUNT(*) FROM conversations c WHERE c.group_id = d.group_id AND c.audio_status = 'ready')"
        "   AS n_audio_ready"
        " FROM datasets d"
        + ("" if include_service else " WHERE d.source NOT IN ('autotest', 'trainer')")
        + " ORDER BY d.group_id").fetchall()
    return [dict(r) for r in rows]


@router.post("/datasets/fetch")
async def fetch_dataset(body: FetchBody, request: Request) -> dict[str, Any]:
    """GET группы из API организаторов → нормализация → БД; mp3 звонков — в фоне."""
    from cqa.ingest.fetch import FetchError
    from cqa.ingest.fetch import redact as redact_url

    group = (body.group_id or "").strip()
    if not _GROUP_RE.match(group):
        raise invalid("group_id", "ожидается ID группы: латиница, цифры, «_», «-», «.»")
    db_path = Path(get_settings().db_path)

    def work():
        from cqa.db.conn import connect
        from cqa.ingest.fetch import fetch_group_raw
        from cqa.ingest.service import ingest_payload

        payload, raw_path = fetch_group_raw(group)
        con = connect(db_path)
        try:
            return ingest_payload(con, payload, "api", group, raw_path=raw_path)
        finally:
            con.close()

    try:
        report = await run_in_threadpool(work)
    except FetchError as e:
        raise HTTPException(502, redact_url(str(e))) from None
    except ValueError as e:         # isSuccess=false, ответ не разбирается
        raise HTTPException(502, redact_url(str(e))) from None
    audio = None
    if body.audio and report.with_audio_url:
        schedule_audio(request, report.group_id)
        audio = "scheduled"
    return _report(report, audio)


@router.post("/datasets/upload")
async def upload_dataset(
    request: Request,
    file: UploadFile = File(..., description="JSON в формате ответа API организаторов"),
    group_id: str | None = Form(None),
    audio: bool = Form(False, description="скачать mp3 звонков по ссылкам из файла (в фоне)"),
    con: sqlite3.Connection = Depends(get_con),
) -> dict[str, Any]:
    from cqa.ingest.fetch import redact as redact_url
    from cqa.ingest.service import ingest_payload

    group = (group_id or "").strip() or None
    if group is not None and not _GROUP_RE.match(group):
        raise invalid("group_id", "ожидается ID группы: латиница, цифры, «_», «-», «.»")
    from cqa.ingest.parse import parse_payload

    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
        parsed = parse_payload(payload)
    except (UnicodeDecodeError, ValueError):
        raise invalid("file", "файл не является JSON-ответом API (UTF-8)") from None
    if parsed.is_success and not parsed.records:
        raise invalid("file", "в файле нет записей data.records[]")

    raw_dir = Path(get_settings().raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = raw_dir / f"upload-{re.sub(r'[^A-Za-z0-9._-]+', '_', group or 'file')}-{stamp}.json"
    raw_path.write_bytes(raw)
    try:
        report = ingest_payload(con, payload, "file", group, raw_path=raw_path)
    except ValueError as e:         # ParseError, isSuccess=false
        raise invalid("file", redact_url(str(e))) from None
    if not report.conversations:
        raise invalid("file", "в файле нет записей data.records[]" + (f": {'; '.join(report.skipped[:3])}"
                                                                     if report.skipped else ""))
    note = None
    if audio and report.with_audio_url:
        schedule_audio(request, report.group_id)
        note = "scheduled"
    return _report(report, note)
