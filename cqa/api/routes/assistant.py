"""Ассистент руководителя (P3): вопрос обычным языком → цифра и ссылки на диалоги (cqa/features/assistant.py).

POST /assistant/ask       {question, group?} → AssistantAnswerDTO
GET  /assistant/examples  → примеры вопросов для кнопок интерфейса

Роутер вопроса — профиль роли `judge` (быстрая модель), SQL и ответ — роли `strong`; переопределения ролей
из настроек учитываются, в режиме fake — профиль fake. Данные читаются через отдельные соединения mode=ro;
соединение API используется только для журнала и кэша вызовов LLM.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cqa.api.deps import get_con, invalid, is_fake, resolve_role
from cqa.settings import get_settings

router = APIRouter(prefix="/assistant", tags=["assistant"])

Kind = Literal["numbers", "meaning", "mixed"]


class AskDTO(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    group: str | None = None                    # только эта группа; по умолчанию все, кроме AUTOTEST и TRAINER


class SourceDTO(BaseModel):
    uid: str                                    # существующий диалог из данных ответа
    external_id: str
    mid: str | None = None                      # реплика этого диалога (существует), если ответ на неё опирается
    why: str


class RouteDTO(BaseModel):
    language: str                               # ru | kz | en
    topics: list[str]
    criteria: list[str]
    terms: list[str]                            # слова поиска на трёх языках


class AssistantAnswerDTO(BaseModel):
    question: str
    answer: str                                 # markdown по-русски, ссылки [external_id](uid)
    kind: Kind                                  # numbers — SQL; meaning — поиск; mixed — SQL, потом поиск
    sql: str | None = None                      # запрос, из результата которого взяты цифры
    columns: list[str] | None = None
    rows: list[dict[str, Any]] | None = None    # первые 20 строк результата
    row_count: int | None = None                # строк всего (не больше 200)
    truncated: bool = False                     # результат обрезан на 200 строках
    sources: list[SourceDTO]
    cost_usd: float
    llm_call_ids: list[int]
    warnings: list[str] = []                    # например числа ответа, которых нет в результате SQL
    latency_ms: int
    route: RouteDTO


class ExampleDTO(BaseModel):
    question: str
    kind: Kind
    label: str                                  # короткая подпись кнопки


@router.post("/ask", response_model=AssistantAnswerDTO)
async def ask(body: AskDTO, request: Request, con: sqlite3.Connection = Depends(get_con)):
    """Ответ на вопрос руководителя: цифра из SQL по представлениям и ссылки на конкретные диалоги."""
    from cqa.features.assistant import ask as assistant_ask

    question = " ".join(body.question.split())
    if not question:
        raise invalid("question", "пустой вопрос")
    fake = is_fake(request)
    try:
        return await assistant_ask(
            question, body.group, con=con, db_path=get_settings().db_path,
            router_profile=resolve_role(con, "judge", fake), strong_profile=resolve_role(con, "strong", fake))
    except sqlite3.Error as e:
        raise HTTPException(503, f"данные недоступны: {e}") from None


@router.get("/examples", response_model=list[ExampleDTO])
async def examples():
    """Примеры вопросов для кнопок в интерфейсе."""
    from cqa.features.assistant import EXAMPLES
    return [ExampleDTO(**e) for e in EXAMPLES]
