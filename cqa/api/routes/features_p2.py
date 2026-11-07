"""P2: прецеденты из апелляций, профиль оператора, срезы для руководителя (логика — cqa/features/*).

GET  /precedents?criterion=                 — прецеденты: критерий, вердикт, фрагмент, версия
POST /precedents/to-version {precedent_ids, comment} → {version_id, version, diff, …} — черновик, без публикации
GET  /precedents/{id}/similar?limit=10       → {uids, items} — для POST /checklist-versions/{vid}/dry-run {uids}
PUT  /conversations/{uid}/operator {name}    — ручное назначение оператора (звонки: имя скрыто); пустое — сброс
GET  /operators?group=                       — список операторов
GET  /operators/{name}?group=                — профиль: критерии против отдела, зоны роста, сильные стороны, диалоги
POST /operators/{name}/summary?group=        — LLM-сводка (роль strong, кэш шлюза)
GET  /slices?by=language|channel|operator|reason&group=
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from cqa.api.deps import get_con, invalid, is_fake, not_found, resolve_role
from cqa.features import operators as ops
from cqa.features import precedents as prec
from cqa.features import slices as sl

router = APIRouter(tags=["p2"])


class PrecedentsToVersionDTO(BaseModel):
    precedent_ids: list[int] = Field(..., min_length=1)
    comment: str | None = Field(None, max_length=2000)


class OperatorAssignDTO(BaseModel):
    name: str | None = Field(None, max_length=200)    # пусто / null — вернуть имя из приветствия


def _raise(e: prec.PrecedentError):
    if isinstance(e, prec.PrecedentInvalid):
        raise invalid(e.loc or "", e.msg) from None
    raise HTTPException(e.status, e.msg) from None


# ---------------------------------------------------------------- прецеденты

@router.get("/precedents")
async def get_precedents(criterion: str | None = None, con: sqlite3.Connection = Depends(get_con)) -> list[dict[str, Any]]:
    """Прецеденты из решений по апелляциям, новые сверху."""
    return prec.list_precedents(con, criterion=criterion or None)


@router.post("/precedents/to-version")
async def precedents_to_version(body: PrecedentsToVersionDTO,
                                con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Черновик новой версии от активной с примерами из прецедентов. Активная версия не меняется."""
    try:
        return prec.build_version(con, body.precedent_ids, body.comment)
    except prec.PrecedentError as e:
        _raise(e)


@router.get("/precedents/{precedent_id}/similar")
async def precedent_similar(precedent_id: int, limit: int = Query(10, ge=1, le=100),
                            con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Похожие диалоги для пробного прогона: та же причина обращения и исходный вердикт, затем язык и канал."""
    try:
        return prec.similar_items(con, precedent_id, limit)
    except prec.PrecedentError as e:
        _raise(e)


# ---------------------------------------------------------------- операторы

@router.put("/conversations/{uid}/operator")
async def put_operator(uid: str, body: OperatorAssignDTO, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Назначить оператора диалогу (conversations.operator_name). Пустое имя — вернуть имя из приветствия."""
    try:
        return ops.assign_operator(con, uid, body.name)
    except KeyError:
        raise not_found(f"диалог {uid}") from None


@router.get("/operators")
async def get_operators(group: str | None = None, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Операторы: диалогов, средний финальный итог, грубых ошибок, апелляций."""
    return ops.list_operators(con, group or None)


@router.get("/operators/{name}")
async def get_operator(name: str, group: str | None = None, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Профиль оператора: критерии против отдела, 3 зоны роста с примерами, сильные стороны, диалоги."""
    try:
        return ops.operator_profile(con, name, group or None)
    except KeyError:
        raise not_found(f"оператор {name!r} (нет оценённых диалогов)") from None


@router.post("/operators/{name}/summary")
async def post_operator_summary(name: str, request: Request, group: str | None = None,
                                con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Сводка для оператора от сильной модели: 3–4 предложения по цифрам и примерам профиля. Ответ кэшируется."""
    from cqa.settings import get_profile

    profile = get_profile(resolve_role(con, "strong", is_fake(request)))
    try:
        return await ops.operator_summary(con, name, profile, group or None)
    except KeyError:
        raise not_found(f"оператор {name!r} (нет оценённых диалогов)") from None
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from None


# ---------------------------------------------------------------- срезы

@router.get("/slices")
async def get_slices(by: str = Query("language", description="language | channel | operator | reason"),
                     group: str | None = None, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Срез текущих оценок: по группе — диалогов, средний итог, доля «Да» / средняя ступень по критериям, грубые
    ошибки, «требует проверки»."""
    if by not in sl.BY:
        raise invalid("by", f"ожидается одно из {', '.join(sl.BY)}")
    return sl.slices(con, by, group or None)
