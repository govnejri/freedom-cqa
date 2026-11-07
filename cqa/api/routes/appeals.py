"""Апелляции: подача, список, просмотр, решение проверяющего (логика — cqa/appeals/service.py)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from cqa.api.deps import ApiValidationError, get_con
from cqa.api.schemas import AppealCreateDTO, AppealDTO, DecisionCreateDTO
from cqa.appeals import service
from cqa.appeals.service import AppealError, AppealInvalid

router = APIRouter(tags=["appeals"])


def _raise(e: AppealError):
    if isinstance(e, AppealInvalid):
        raise ApiValidationError([{"loc": e.loc or "", "msg": e.msg}]) from None
    raise HTTPException(e.status, e.msg) from None


@router.post("/appeals", response_model=AppealDTO)
async def create_appeal(dto: AppealCreateDTO, con: sqlite3.Connection = Depends(get_con)):
    try:
        return service.create_appeal(con, dto)
    except AppealError as e:
        _raise(e)


@router.get("/appeals", response_model=list[AppealDTO])
async def list_appeals(status: str | None = None, uid: str | None = None, evaluation_id: int | None = None,
                       con: sqlite3.Connection = Depends(get_con)):
    return service.list_appeals(con, None if status in (None, "", "all") else status, uid, evaluation_id)


@router.get("/appeals/{appeal_id}", response_model=AppealDTO)
async def get_appeal(appeal_id: int, con: sqlite3.Connection = Depends(get_con)):
    try:
        return service.get_appeal(con, appeal_id)
    except AppealError as e:
        _raise(e)


@router.post("/appeals/{appeal_id}/decision", response_model=AppealDTO)
async def decide(appeal_id: int, dto: DecisionCreateDTO, con: sqlite3.Connection = Depends(get_con)):
    try:
        return service.decide(con, appeal_id, dto)
    except AppealError as e:
        _raise(e)
