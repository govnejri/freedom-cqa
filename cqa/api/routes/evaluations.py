"""Оценки и журнал вызовов LLM (P1: прозрачность — «система реально анализирует»)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from cqa.api.deps import get_con, not_found
from cqa.api.routes.views import (
    LLM_CALL_COLUMNS,
    evaluation_dto,
    evaluation_row,
    llm_call_dto,
)
from cqa.api.schemas import EvaluationDTO, LLMCallDTO

router = APIRouter(tags=["evaluations"])


@router.get("/evaluations/{eval_id}", response_model=EvaluationDTO)
async def get_evaluation(eval_id: int, con: sqlite3.Connection = Depends(get_con)):
    ev = evaluation_row(con, eval_id)
    if ev is None:
        raise not_found(f"оценка {eval_id}")
    return evaluation_dto(con, ev)


@router.get("/evaluations/{eval_id}/calls", response_model=list[LLMCallDTO])
async def evaluation_calls(eval_id: int, con: sqlite3.Connection = Depends(get_con)):
    """Вызовы LLM оценки без тела запроса и ответа."""
    if evaluation_row(con, eval_id) is None:
        raise not_found(f"оценка {eval_id}")
    rows = con.execute(f"SELECT {LLM_CALL_COLUMNS} FROM llm_calls WHERE evaluation_id=? ORDER BY id", (eval_id,))
    return [llm_call_dto(r) for r in rows.fetchall()]


@router.get("/llm-calls", response_model=list[LLMCallDTO])
async def llm_calls(limit: int = Query(50, ge=1, le=500), uid: str | None = None,
                    con: sqlite3.Connection = Depends(get_con)):
    """Последние вызовы LLM (без тела запроса), новые первыми."""
    sql, args = f"SELECT {LLM_CALL_COLUMNS} FROM llm_calls", []
    if uid:
        sql += " WHERE uid=?"
        args.append(uid)
    rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [llm_call_dto(r) for r in rows]


@router.get("/llm-calls/{call_id}", response_model=LLMCallDTO)
async def llm_call(call_id: int, con: sqlite3.Connection = Depends(get_con)):
    """Вызов LLM с запросом (без секретов — маскируется при записи и ещё раз здесь) и ответом."""
    from cqa.llm.store import get_call

    r = get_call(con, call_id)
    if r is None:
        raise not_found(f"вызов LLM {call_id}")
    return llm_call_dto(r, full=True)
