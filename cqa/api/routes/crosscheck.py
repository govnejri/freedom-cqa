"""Перекрёстная проверка оценки, второе мнение ИИ по апелляции и сводка согласия (логика — cqa/core/crosscheck.py).

Профили — роли cross_check и strong (с переопределениями из настроек; в режиме fake — fake).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from cqa.api.deps import ApiValidationError, get_con, is_fake, resolve_role
from cqa.api.schemas import AppealDTO
from cqa.core import crosscheck
from cqa.core.crosscheck import CrossCheckError, CrossCheckInvalid

router = APIRouter(tags=["cross-check"])


class CrossCheckRequestDTO(BaseModel):
    criteria: list[str] | str | None = None   # None — критерии с cross_check: true; "all" / ["all"] — все


class CrossCheckItemDTO(BaseModel):
    criterion_id: str
    title: str
    type: str
    status: str                               # done | failed | skipped
    verdict: str | None = None                # вердикт основного судьи
    second_verdict: str | None = None
    agree: bool | None = None
    near: bool | None = None                  # шкала: разница в одну ступень
    needs_review: bool | None = None
    second_category: str | None = None
    second_explanation: str | None = None
    second_evidence: list[dict[str, Any]] | None = None
    cache_hit: bool | None = None
    cost_usd: float | None = None
    reason: str | None = None                 # почему пропущен
    error: str | None = None


class CrossCheckResultDTO(BaseModel):
    evaluation_id: int
    uid: str
    profile: str
    model: str
    criteria: list[CrossCheckItemDTO]
    checked: int
    agree: int
    disagree: int
    failed: int
    skipped: int
    needs_review: bool                        # оценка «требует проверки»
    cost_usd: float | None = None


def _raise(e: CrossCheckError):
    if isinstance(e, CrossCheckInvalid):
        raise ApiValidationError([{"loc": e.loc or "", "msg": e.msg}]) from None
    raise HTTPException(e.status, e.msg) from None


@router.post("/evaluations/{eval_id}/cross-check", response_model=CrossCheckResultDTO)
async def cross_check(eval_id: int, request: Request, dto: CrossCheckRequestDTO | None = Body(None),
                      con: sqlite3.Connection = Depends(get_con)):
    """Синхронно: вторая модель независимо оценивает критерии; расхождение → «требует проверки»."""
    profile = resolve_role(con, "cross_check", is_fake(request))
    try:
        return await crosscheck.cross_check_evaluation(con, eval_id, dto.criteria if dto else None, profile)
    except CrossCheckError as e:
        _raise(e)


@router.post("/appeals/{appeal_id}/second-opinion", response_model=AppealDTO)
async def second_opinion(appeal_id: int, request: Request, con: sqlite3.Connection = Depends(get_con)):
    """Слепое второе мнение сильной модели (без исходного вердикта системы) → обновлённая апелляция."""
    from cqa.appeals.service import AppealError, get_appeal

    profile = resolve_role(con, "strong", is_fake(request))
    try:
        await crosscheck.second_opinion(con, appeal_id, profile)
    except CrossCheckError as e:
        _raise(e)
    try:
        return get_appeal(con, appeal_id)
    except AppealError as e:
        raise HTTPException(e.status, e.msg) from None


@router.get("/quality/cross-check")
async def cross_check_quality(group: str | None = None,
                              criteria: str | None = Query(None, description="03,08 или all; по умолчанию все проверенные"),
                              con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Сводка: доля согласия по критериям; на эталоне (eval/gold.csv) — точность основного судьи там,
    где модели согласны, и там, где расходятся."""
    try:
        return crosscheck.cross_check_summary(con, group or None, criteria.split(",") if criteria else None,
                                              crosscheck.load_gold())
    except CrossCheckError as e:
        _raise(e)
