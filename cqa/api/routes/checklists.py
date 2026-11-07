"""Чек-листы и версии: список, конфиг версии, новая версия (ошибки → 422 ValidationErrorDTO[]), публикация, diff."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from cqa.api.deps import (
    ApiValidationError,
    get_con,
    invalid,
    not_found,
    pydantic_errors,
)
from cqa.api.routes.views import version_dto
from cqa.api.schemas import ChecklistVersionDTO

router = APIRouter(tags=["checklists"])


class VersionCreateBody(BaseModel):
    config: dict[str, Any]
    comment: str | None = None


def _version(con: sqlite3.Connection, vid: int):
    from cqa.checklist.store import get_version
    try:
        return get_version(con, vid)
    except KeyError:
        raise not_found(f"версия чек-листа {vid}") from None


@router.get("/checklists")
async def list_checklists(con: sqlite3.Connection = Depends(get_con)) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT c.id, c.title, c.active_version_id, c.frozen_version_id,"
        " (SELECT COUNT(*) FROM checklist_versions v WHERE v.checklist_id = c.id) AS n_versions"
        " FROM checklists c ORDER BY c.id").fetchall()
    return [dict(r) for r in rows]


@router.get("/checklists/{checklist_id}/versions", response_model=list[ChecklistVersionDTO])
async def list_versions(checklist_id: str, con: sqlite3.Connection = Depends(get_con)):
    """Версии чек-листа, новые первыми."""
    from cqa.checklist.store import list_versions as store_list

    metas = store_list(con, checklist_id)
    if not metas and con.execute("SELECT 1 FROM checklists WHERE id=?", (checklist_id,)).fetchone() is None:
        raise not_found(f"чек-лист {checklist_id}")
    return [version_dto(*_version(con, m["id"])) for m in reversed(metas)]


@router.get("/checklist-versions/{vid}", response_model=ChecklistVersionDTO)
async def get_version(vid: int, con: sqlite3.Connection = Depends(get_con)):
    return version_dto(*_version(con, vid))


@router.post("/checklists/{checklist_id}/versions", response_model=ChecklistVersionDTO)
async def create_version(checklist_id: str, body: VersionCreateBody, con: sqlite3.Connection = Depends(get_con)):
    """Новая версия (черновик) от активной. Ошибки конфига → 422 [{loc: 'criteria.3.weight', msg}]."""
    from cqa.checklist.model import ChecklistConfig
    from cqa.checklist.store import create_version as store_create
    from cqa.checklist.store import get_active

    config = dict(body.config)
    config.setdefault("id", checklist_id)
    if config["id"] != checklist_id:
        raise invalid("id", f"id конфига {config['id']!r} не совпадает с чек-листом {checklist_id!r}")
    try:
        cfg = ChecklistConfig.model_validate(config)
    except ValidationError as e:
        raise ApiValidationError(pydantic_errors(e.errors())) from None
    try:
        parent = get_active(con, checklist_id)[1]["id"]
    except KeyError:
        parent = None
    vid = store_create(con, checklist_id, cfg, parent_id=parent, comment=(body.comment or "").strip() or None)
    return version_dto(*_version(con, vid))


@router.post("/checklist-versions/{vid}/publish", response_model=ChecklistVersionDTO)
async def publish_version(vid: int, con: sqlite3.Connection = Depends(get_con)):
    from cqa.checklist.store import publish

    _version(con, vid)
    publish(con, vid)
    return version_dto(*_version(con, vid))


@router.get("/checklists/{checklist_id}/diff")
async def diff_versions(checklist_id: str, a: int = Query(...), b: int = Query(...),
                        con: sqlite3.Connection = Depends(get_con)) -> list[dict[str, Any]]:
    """Что изменилось от версии a к версии b (критерии и общие поля)."""
    from cqa.checklist.store import diff

    cfg_a, meta_a = _version(con, a)
    cfg_b, meta_b = _version(con, b)
    if checklist_id not in (meta_a["checklist_id"], meta_b["checklist_id"]):
        raise not_found(f"версии {a}, {b} чек-листа {checklist_id}")
    return diff(cfg_a, cfg_b)
