"""Прогоны оценки: постановка в очередь (исполняет cqa/api/worker.py), прогресс с ETA, повтор упавших, отмена."""
from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from cqa.api.deps import (
    FAKE_PROFILE,
    get_con,
    invalid,
    is_fake,
    not_found,
    profile_names,
    resolve_role,
)
from cqa.api.routes.views import run_dto
from cqa.api.schemas import RunCreateDTO, RunDTO

router = APIRouter(tags=["runs"])
log = logging.getLogger(__name__)


def _progress(con: sqlite3.Connection, run_id: int) -> RunDTO:
    from cqa.core.runner import run_progress

    if con.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None:
        raise not_found(f"прогон {run_id}")
    return run_dto(run_progress(con, run_id))


def _wake(request: Request, run_id: int) -> None:
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.wake(run_id)


@router.get("/runs", response_model=list[RunDTO])
async def list_runs(limit: int = Query(20, ge=1, le=200), con: sqlite3.Connection = Depends(get_con)):
    ids = [r[0] for r in con.execute("SELECT id FROM runs ORDER BY id DESC LIMIT ?", (limit,))]
    return [_progress(con, i) for i in ids]


@router.post("/runs", response_model=RunDTO)
async def create_run(dto: RunCreateDTO, request: Request, con: sqlite3.Connection = Depends(get_con)):
    from cqa.checklist.store import get_active, get_version
    from cqa.core.runner import create_run as runner_create
    from cqa.ingest.store import list_uids

    # версия чек-листа
    if dto.checklist_version_id is not None:
        try:
            cfg, meta = get_version(con, dto.checklist_version_id)
        except KeyError:
            raise invalid("checklist_version_id", f"нет версии чек-листа {dto.checklist_version_id}") from None
    else:
        try:
            cfg, meta = get_active(con)
        except KeyError:
            raise HTTPException(409, "нет активной версии чек-листа") from None

    # профиль: явный → роль judge (с переопределением из настроек); в режиме fake — всегда fake
    if is_fake(request):
        if dto.profile and dto.profile != FAKE_PROFILE:
            log.info("режим fake: профиль %s заменён на fake", dto.profile)
        profile = FAKE_PROFILE
    elif dto.profile:
        if dto.profile not in profile_names():
            raise invalid("profile", f"нет профиля {dto.profile!r}; есть: {', '.join(profile_names())}")
        profile = dto.profile
    else:
        profile = resolve_role(con, "judge", False)

    # диалоги
    if dto.uids:
        uids = list(dict.fromkeys(dto.uids))
        known = {r[0] for r in con.execute(
            f"SELECT uid FROM conversations WHERE uid IN ({','.join('?' * len(uids))})", uids)}
        missing = [u for u in uids if u not in known]
        if missing:
            raise invalid("uids", f"нет диалогов: {', '.join(missing[:10])}")
    elif dto.group:
        uids = list_uids(con, dto.group)
    else:   # «все наборы» = рабочие диалоги: без мутантов автотестов и сессий тренажёра
        uids = [r[0] for r in con.execute(
            "SELECT c.uid FROM conversations c JOIN datasets d ON d.group_id=c.group_id"
            " WHERE c.parent_uid IS NULL AND d.source NOT IN ('autotest', 'trainer') ORDER BY c.uid")]
    if not uids:
        raise invalid("group" if dto.group else "uids", "нет диалогов для оценки")

    # критерии
    criteria = None
    if dto.criteria:
        ids = {c.id for c in cfg.enabled_criteria()}
        bad = [c for c in dto.criteria if c not in ids]
        if bad:
            raise invalid("criteria", f"нет включённых критериев {', '.join(bad)}; есть: {', '.join(sorted(ids))}")
        criteria = list(dict.fromkeys(dto.criteria))

    run_id = runner_create(con, uids=uids, version_id=meta["id"], profile_name=profile, criteria=criteria,
                           force=dto.force, kind=dto.kind or "batch", cross_check=dto.cross_check)
    _wake(request, run_id)
    return _progress(con, run_id)


@router.get("/runs/{run_id}", response_model=RunDTO)
async def get_run(run_id: int, con: sqlite3.Connection = Depends(get_con)):
    return _progress(con, run_id)


@router.post("/runs/{run_id}/retry-failed", response_model=RunDTO)
async def retry_failed(run_id: int, request: Request, con: sqlite3.Connection = Depends(get_con)):
    from cqa.core.runner import retry_failed as runner_retry

    run = _progress(con, run_id)
    if run.status in ("queued", "running"):
        raise HTTPException(409, f"прогон {run_id} ещё выполняется")
    n = runner_retry(con, run_id)       # прогон снова в очереди (runner ставит queued в любом случае)
    log.info("прогон %s: повтор %s упавших единиц", run_id, n)
    _wake(request, run_id)
    return _progress(con, run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunDTO)
async def cancel_run(run_id: int, con: sqlite3.Connection = Depends(get_con)):
    from cqa.core.runner import cancel

    _progress(con, run_id)
    cancel(con, run_id)
    return _progress(con, run_id)
