"""P3: тренажёр оператора и чат с тьютором (cqa/features/trainer.py, cqa/features/tutor.py).

Тренажёр:
  POST /trainer/sessions {base_uid, criteria_focus?, operator?, lesson_id?} → сессия (персона без скрытого вопроса)
  POST /trainer/sessions/{id}/reply {text}                               → новые реплики, finished
  POST /trainer/sessions/{id}/finish                                     → оценка и «было/стало» (ждёт оценку)
  GET  /trainer/sessions/{id}, GET /trainer/sessions?base_uid=&lesson_id=&status=
  POST /trainer/lessons {base_uids, title}, GET /trainer/lessons         → «урок для всех»
Тьютор:
  POST /tutor/ask {question, operator?, uid?}                           → {answer, found, sources[]}
  GET  /tutor/best-answers?topic=&q=&group=&limit=                     → база лучших ответов коллег

Роли: персона и тьютор — strong, ИИ-клиент — judge; оценка сессии — профиль исходной оценки (тот же судья).
В режиме fake (create_app(fake=True)) — всё профиль fake.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from cqa.api.deps import (
    FAKE_PROFILE,
    get_con,
    invalid,
    is_fake,
    not_found,
    profile_available,
    profile_names,
    resolve_role,
)
from cqa.features import trainer, tutor
from cqa.settings import get_profile

router = APIRouter(tags=["p3"])


class SessionCreateBody(BaseModel):
    base_uid: str
    criteria_focus: list[str] | None = None     # критерии, которые тренируем (персона под них)
    operator: str | None = None                 # кто тренируется (для уроков и истории)
    lesson_id: int | None = None


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class LessonBody(BaseModel):
    base_uids: list[str] = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)


class TutorAskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    operator: str | None = None
    uid: str | None = None


def _role(request: Request, con: sqlite3.Connection, role: str):
    return get_profile(resolve_role(con, role, is_fake(request)))


def _eval_profile(request: Request, con: sqlite3.Connection, sid: int) -> str:
    """Тот же судья, что у исходной оценки; если профиля нет или он недоступен — роль judge."""
    if is_fake(request):
        return FAKE_PROFILE
    try:
        meta = trainer.session_meta(con, sid)
    except KeyError:
        raise not_found(f"сессия тренажёра {sid}") from None
    name = meta.get("eval_profile")
    if name and name in profile_names() and profile_available(name):
        return name
    return resolve_role(con, "judge", False)


def _errors(e: Exception):
    if isinstance(e, KeyError):
        return not_found(str(e.args[0]) if e.args else "объект")
    if isinstance(e, trainer.TrainerError):
        return invalid(e.loc, str(e))
    if isinstance(e, (trainer.LLMFailed, RuntimeError)):
        return HTTPException(502, str(e))
    if isinstance(e, ValueError):
        return invalid("body", str(e))
    return e


# ---------------------------------------------------------------- тренажёр

@router.post("/trainer/sessions")
async def create_session(body: SessionCreateBody, request: Request,
                         con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    try:
        return await trainer.create_session(
            con, body.base_uid.strip(), profile=_role(request, con, "strong"), criteria_focus=body.criteria_focus,
            operator=body.operator, lesson_id=body.lesson_id)
    except (KeyError, ValueError, RuntimeError) as e:
        raise _errors(e) from None


@router.get("/trainer/sessions")
async def list_sessions(base_uid: str | None = None, lesson_id: int | None = None,
                        status: str | None = Query(None, description="active | finished"),
                        con: sqlite3.Connection = Depends(get_con)) -> list[dict[str, Any]]:
    return trainer.list_sessions(con, base_uid=base_uid, lesson_id=lesson_id, status=status)


@router.get("/trainer/sessions/{sid}")
async def get_session(sid: int, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    try:
        return trainer.session_view(con, sid)
    except KeyError as e:
        raise _errors(e) from None


@router.post("/trainer/sessions/{sid}/reply")
async def reply(sid: int, body: ReplyBody, request: Request,
                con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    try:
        return await trainer.reply(con, sid, body.text, profile=_role(request, con, "judge"))
    except (KeyError, ValueError, RuntimeError) as e:
        raise _errors(e) from None


@router.post("/trainer/sessions/{sid}/finish")
async def finish(sid: int, request: Request, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    try:
        return await trainer.finish(con, sid, eval_profile=_eval_profile(request, con, sid))
    except (KeyError, ValueError, RuntimeError) as e:
        raise _errors(e) from None


@router.post("/trainer/lessons")
async def create_lesson(body: LessonBody, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    try:
        return trainer.create_lesson(con, body.base_uids, body.title)
    except ValueError as e:
        raise _errors(e) from None


@router.get("/trainer/lessons")
async def list_lessons(con: sqlite3.Connection = Depends(get_con)) -> list[dict[str, Any]]:
    return trainer.list_lessons(con)


# ---------------------------------------------------------------- тьютор

@router.post("/tutor/ask")
async def tutor_ask(body: TutorAskBody, request: Request, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    try:
        return await tutor.ask(con, body.question, profile=_role(request, con, "strong"),
                               operator=(body.operator or "").strip() or None, uid=(body.uid or "").strip() or None)
    except (KeyError, ValueError, RuntimeError) as e:
        raise _errors(e) from None


@router.get("/tutor/best-answers")
async def best_answers(topic: str | None = None, q: str | None = None, group: str | None = None,
                       limit: int = Query(100, ge=1, le=500),
                       con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    return tutor.best_answers_view(con, topic=topic, q=q, group=group, limit=limit)
