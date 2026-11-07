"""Диалоги: список с фильтрами, карточка, аудио (Range), текущая оценка."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from cqa.api.deps import get_con, not_found
from cqa.api.routes.views import (
    conversation_dto,
    evaluation_dto,
    evaluation_row,
    summary_dto,
)
from cqa.api.schemas import ConversationDTO, ConversationSummaryDTO, EvaluationDTO

router = APIRouter(tags=["conversations"])

_SCORE = "COALESCE(e.final_total, e.total)"
_GROSS = "COALESCE(json_extract(e.final_score_json, '$.gross'), e.gross, 0)"
_SORTS = {
    "id": "c.group_id, c.external_id",
    "external_id": "c.group_id, c.external_id",
    "-id": "c.group_id DESC, c.external_id DESC",
    "-external_id": "c.group_id DESC, c.external_id DESC",
    "score": f"{_SCORE} IS NULL, {_SCORE}, c.external_id",
    "total": f"{_SCORE} IS NULL, {_SCORE}, c.external_id",
    "-score": f"{_SCORE} IS NULL, {_SCORE} DESC, c.external_id",
    "-total": f"{_SCORE} IS NULL, {_SCORE} DESC, c.external_id",
}

_LIST_SQL = """
SELECT c.uid, c.group_id, c.external_id, c.channel, c.language, c.operator_name, c.audio_status, c.audio_path,
       (SELECT COUNT(*) FROM messages m WHERE m.uid = c.uid) AS n_messages,
       e.id AS evaluation_id, e.total, e.final_total, e.gross, e.gross_json, e.final_score_json,
       COALESCE(e.needs_review, 0) AS needs_review, e.status AS ev_status,
       (SELECT COUNT(*) FROM appeals a JOIN evaluations x ON x.id = a.evaluation_id
         WHERE x.uid = c.uid AND a.status = 'open') AS open_appeals
FROM conversations c LEFT JOIN evaluations e ON e.id = c.current_evaluation_id
"""


@router.get("/conversations", response_model=list[ConversationSummaryDTO])
async def list_conversations(
    group: str | None = None,
    lang: str | None = None,
    channel: str | None = None,
    score_min: float | None = None,
    score_max: float | None = None,
    gross: bool | None = None,
    review: bool | None = None,
    appeal: bool | None = None,
    q: str | None = Query(None, description="подстрока ID диалога (или имени оператора)"),
    sort: str = Query("id", description="id | score | -score"),
    con: sqlite3.Connection = Depends(get_con),
):
    where, args = [], []
    if group:
        where.append("c.group_id = ?")
        args.append(group)
    if lang:
        where.append("upper(c.language) = upper(?)")
        args.append(lang)
    if channel:
        where.append("c.channel = ?")
        args.append(channel)
    if score_min is not None:
        where.append(f"{_SCORE} >= ?")
        args.append(score_min)
    if score_max is not None:
        where.append(f"{_SCORE} <= ?")
        args.append(score_max)
    if gross is not None:
        where.append(f"{_GROSS} = ?")
        args.append(int(gross))
    if review is not None:
        where.append("COALESCE(e.needs_review, 0) = ?")
        args.append(int(review))
    if appeal is not None:
        where.append(("" if appeal else "NOT ") + "EXISTS (SELECT 1 FROM appeals a JOIN evaluations x"
                     " ON x.id = a.evaluation_id WHERE x.uid = c.uid)")
    if q and q.strip():
        pat = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(c.uid LIKE ? ESCAPE '\\' OR c.operator_name LIKE ? ESCAPE '\\')")
        args += [pat, pat]
    sql = _LIST_SQL + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY " + _SORTS.get((sort or "id").strip(), _SORTS["id"])
    return [summary_dto(r) for r in con.execute(sql, args).fetchall()]


def _conversation(con: sqlite3.Connection, uid: str):
    from cqa.ingest.store import get_conversation
    try:
        return get_conversation(con, uid)
    except KeyError:
        raise not_found(f"диалог {uid}") from None


@router.get("/conversations/{uid}", response_model=ConversationDTO)
async def get_conversation(uid: str, con: sqlite3.Connection = Depends(get_con)):
    return conversation_dto(_conversation(con, uid))


@router.api_route("/conversations/{uid}/audio", methods=["GET", "HEAD"], response_class=FileResponse,
                  responses={206: {"content": {"audio/mpeg": {}}}, 404: {}})
async def get_audio(uid: str, con: sqlite3.Connection = Depends(get_con)):
    """mp3 звонка из локального файла. Range → 206 (FileResponse Starlette). Ссылку организаторов не отдаём."""
    from cqa.ingest.store import get_audio_path

    path = get_audio_path(con, uid)
    if path is None:
        raise not_found(f"аудио диалога {uid}")
    return FileResponse(path, media_type="audio/mpeg", filename=None,
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/conversations/{uid}/evaluation", response_model=EvaluationDTO)
async def get_current_evaluation(uid: str, con: sqlite3.Connection = Depends(get_con)):
    """Текущая оценка; если её ещё нет — последняя (например, в работе)."""
    row = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()
    if row is None:
        raise not_found(f"диалог {uid}")
    eval_id = row["current_evaluation_id"]
    if eval_id is None:
        last = con.execute("SELECT MAX(id) FROM evaluations WHERE uid=?", (uid,)).fetchone()[0]
        if last is None:
            raise not_found(f"оценка диалога {uid}")
        eval_id = last
    return evaluation_dto(con, evaluation_row(con, eval_id))


@router.get("/conversations/{uid}/evaluations", response_model=list[EvaluationDTO])
async def list_evaluations(uid: str, limit: int = Query(20, ge=1, le=200), con: sqlite3.Connection = Depends(get_con)):
    """История оценок диалога, новые первыми."""
    from cqa.api.routes.views import VersionCache

    versions = VersionCache(con)
    rows = con.execute("SELECT * FROM evaluations WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
    return [evaluation_dto(con, r, versions) for r in rows]
