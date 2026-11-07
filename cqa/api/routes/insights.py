"""Инсайты для руководителя (P1): радар рисков, «В комплаенс», вопросы без ответа, причины обращения, сводка.

Всё считается по текущим оценкам диалогов (`conversations.current_evaluation_id`) с учётом решений по апелляциям:
финальный вердикт = исходный + последнее решение `change`; финальный итог = COALESCE(final_total, total).
Сигналы (причина обращения, вопросы без ответа) — из таблицы `signals` (cqa/core/signals.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cqa.api.deps import get_con, invalid, is_fake, not_found, resolve_role
from cqa.db.conn import now_iso, tx
from cqa.settings import get_settings

router = APIRouter(prefix="/insights", tags=["insights"])
log = logging.getLogger(__name__)

FLAG_YES = "Да"
NO_CATEGORY = "без категории"


# ---------------------------------------------------------------- DTO

class RiskEvidenceDTO(BaseModel):
    mid: str
    quote: str                                  # дословная цитата из ответа судьи
    text: str | None = None                     # полный текст реплики
    speaker: str | None = None
    audio_sec: float | None = None              # секунда в записи (звонки), для перехода плеера


class ComplianceCaseDTO(BaseModel):
    id: int
    criterion_result_id: int
    status: str                                 # open | …
    note: str | None = None
    created_at: str
    created: bool = False                       # True — создано этим запросом (только в ответе POST)


class RiskDTO(BaseModel):
    criterion_result_id: int
    evaluation_id: int
    criterion_id: str
    criterion_title: str
    uid: str
    group_id: str
    external_id: str
    language: str
    channel: str
    category: str                               # criterion_results.category; «без категории», если пусто
    verdict: str                                # финальный вердикт флага («Да»)
    changed_by_appeal: bool                     # «Да» поставлено решением по апелляции
    evidence: list[RiskEvidenceDTO]
    explanation: str
    how_to_fix: str
    needs_review: bool
    total: float | None                         # финальный итог диалога
    evaluated_at: str | None
    compliance: ComplianceCaseDTO | None = None


class RiskCategoryDTO(BaseModel):
    category: str
    count: int
    criterion_result_ids: list[int]


class RisksDTO(BaseModel):
    total: int
    n_conversations: int
    items: list[RiskDTO]                        # новые сверху
    by_category: list[RiskCategoryDTO]          # по убыванию числа


class ComplianceCreateDTO(BaseModel):
    note: str | None = Field(None, max_length=4000)


class UnansweredQuestionDTO(BaseModel):
    text: str                                   # короткая формулировка по-русски
    status: str                                 # открыт | частично
    uid: str
    external_id: str
    mid: str                                    # реплика клиента с вопросом
    message_text: str | None = None             # исходный текст этой реплики
    language: str
    channel: str
    score: float | None = None                  # финальный итог диалога


class UnansweredTopicDTO(BaseModel):
    topic: str
    n_conversations: int                        # диалогов темы с вопросами без ответа
    n_conversations_total: int                  # всех диалогов темы (с сигналами)
    n_questions: int
    avg_total: float | None                     # средний финальный итог всех диалогов темы
    avg_total_unanswered: float | None          # … только диалогов с вопросами без ответа
    questions: list[UnansweredQuestionDTO]


class UnansweredDTO(BaseModel):
    n_evaluated: int                            # диалогов с текущей оценкой
    n_with_signals: int                         # из них с посчитанными сигналами
    total_questions: int
    n_conversations: int
    topics: list[UnansweredTopicDTO]


class ReasonDTO(BaseModel):
    topic: str
    n_conversations: int
    share: float                                # доля среди диалогов с сигналами, 0..1
    avg_total: float | None
    resolved_share: float                       # 0..1
    n_resolved: int
    gross_count: int                            # диалогов с грубой ошибкой (финально)
    churn_count: int                            # диалогов с риском ухода
    n_unanswered_conversations: int
    n_unanswered_questions: int
    missed_opportunity_count: int


class ReasonsDTO(BaseModel):
    n_evaluated: int
    n_with_signals: int
    reasons: list[ReasonDTO]


class SignalsRunCreateDTO(BaseModel):
    group: str | None = None
    uids: list[str] | None = None
    force: bool = False                         # пересчитать и там, где сигналы уже есть


class SignalsRunDTO(BaseModel):
    status: Literal["scheduled", "nothing_to_do"]
    queued: int                                 # диалогов поставлено в очередь
    already: int                                # у скольких сигналы уже есть
    group: str | None
    profile: str


class TopicCountDTO(BaseModel):
    topic: str
    n_questions: int
    n_conversations: int


class SummaryDTO(BaseModel):
    group: str | None
    n_conversations: int
    n_evaluated: int
    avg_total: float | None
    gross_count: int
    needs_review: int
    open_appeals: int
    risks_open_compliance: int                  # открытых дел в комплаенсе
    n_with_signals: int
    unanswered_questions: int
    top_unanswered_topics: list[TopicCountDTO]  # топ-3 по числу вопросов без ответа


# ---------------------------------------------------------------- общие запросы

_RISK_SQL = """
SELECT cr.id AS crid, cr.evaluation_id, cr.criterion_id, cr.verdict, cr.category, cr.evidence_json, cr.explanation,
       cr.how_to_fix, cr.needs_review,
       c.uid, c.group_id, c.external_id, c.language, c.channel,
       e.checklist_version_id, COALESCE(e.finished_at, e.created_at) AS evaluated_at,
       COALESCE(e.final_total, e.total) AS score,
       (SELECT d.new_verdict FROM decisions d JOIN appeals a ON a.id = d.appeal_id
         WHERE a.evaluation_id = cr.evaluation_id AND a.criterion_id = cr.criterion_id
           AND d.action = 'change' AND d.new_verdict IS NOT NULL
         ORDER BY d.id DESC LIMIT 1) AS changed_verdict
FROM criterion_results cr
JOIN evaluations e ON e.id = cr.evaluation_id
JOIN conversations c ON c.uid = e.uid
WHERE cr.status = 'done'
"""


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _flag_titles(versions, version_id: int) -> dict[str, str]:
    """criterion_id → title для flag-критериев версии чек-листа."""
    try:
        cfg, _meta = versions.get(version_id)
    except KeyError:
        return {}
    return {c.id: c.title for c in cfg.criteria if c.type == "flag"}


def _compliance_index(con: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    """(uid, criterion_id) → первое дело в комплаенсе. Переживает перепрогон диалога (новый criterion_result)."""
    out: dict[tuple[str, str], sqlite3.Row] = {}
    for r in con.execute(
        "SELECT cc.*, e.uid, cr.criterion_id FROM compliance_cases cc"
        " JOIN criterion_results cr ON cr.id = cc.criterion_result_id JOIN evaluations e ON e.id = cr.evaluation_id"
        " ORDER BY cc.id"):
        out.setdefault((r["uid"], r["criterion_id"]), r)
    return out


def _case_dto(r: sqlite3.Row, created: bool = False) -> ComplianceCaseDTO:
    return ComplianceCaseDTO(id=r["id"], criterion_result_id=r["criterion_result_id"], status=r["status"],
                             note=r["note"], created_at=r["created_at"], created=created)


def _messages(con: sqlite3.Connection, uid: str) -> dict[str, sqlite3.Row]:
    return {r["mid"]: r for r in con.execute("SELECT mid, text, speaker, audio_sec FROM messages WHERE uid=?", (uid,))}


# ---------------------------------------------------------------- радар рисков

@router.get("/risks", response_model=RisksDTO)
async def risks(group: str | None = None, con: sqlite3.Connection = Depends(get_con)):
    """Диалоги, где flag-критерий финально «Да»: цитаты, объяснение, категория, статус передачи в комплаенс."""
    from cqa.api.routes.views import VersionCache

    versions = VersionCache(con)
    sql, args = _RISK_SQL + " AND c.current_evaluation_id = cr.evaluation_id", []
    if group:
        sql += " AND c.group_id = ?"
        args.append(group)
    sql += " ORDER BY evaluated_at DESC, cr.id DESC"
    cases = _compliance_index(con)
    items: list[RiskDTO] = []
    for r in con.execute(sql, args).fetchall():
        flags = _flag_titles(versions, r["checklist_version_id"])
        if r["criterion_id"] not in flags:
            continue
        final = r["changed_verdict"] or r["verdict"]
        if final != FLAG_YES:
            continue
        msgs = _messages(con, r["uid"])
        evidence = []
        for ev in _loads(r["evidence_json"], []) or []:
            if not isinstance(ev, dict) or ev.get("ok") is False:
                continue
            m = msgs.get(ev.get("id", ""))
            evidence.append(RiskEvidenceDTO(mid=ev.get("id", ""), quote=ev.get("quote", ""),
                                            text=m["text"] if m else None, speaker=m["speaker"] if m else None,
                                            audio_sec=m["audio_sec"] if m else None))
        case = cases.get((r["uid"], r["criterion_id"]))
        items.append(RiskDTO(
            criterion_result_id=r["crid"], evaluation_id=r["evaluation_id"], criterion_id=r["criterion_id"],
            criterion_title=flags[r["criterion_id"]], uid=r["uid"], group_id=r["group_id"],
            external_id=r["external_id"], language=r["language"], channel=r["channel"],
            category=(r["category"] or "").strip() or NO_CATEGORY, verdict=final,
            changed_by_appeal=bool(r["changed_verdict"]) and r["changed_verdict"] != r["verdict"],
            evidence=evidence, explanation=r["explanation"] or "", how_to_fix=r["how_to_fix"] or "",
            needs_review=bool(r["needs_review"]), total=r["score"], evaluated_at=r["evaluated_at"],
            compliance=_case_dto(case) if case else None,
        ))
    cats: dict[str, list[int]] = {}
    for it in items:
        cats.setdefault(it.category, []).append(it.criterion_result_id)
    by_category = sorted((RiskCategoryDTO(category=k, count=len(v), criterion_result_ids=v) for k, v in cats.items()),
                         key=lambda x: (-x.count, x.category))
    return RisksDTO(total=len(items), n_conversations=len({it.uid for it in items}), items=items,
                    by_category=by_category)


@router.post("/risks/{criterion_result_id}/compliance", response_model=ComplianceCaseDTO)
async def to_compliance(criterion_result_id: int, body: ComplianceCreateDTO | None = None,
                        con: sqlite3.Connection = Depends(get_con)):
    """«В комплаенс»: дело со статусом open. Повторный вызов (и для того же флага после перепрогона) не дублирует —
    возвращает существующее дело с created=false."""
    from cqa.api.routes.views import VersionCache

    r = con.execute(_RISK_SQL + " AND cr.id = ?", (criterion_result_id,)).fetchone()
    if r is None:
        if con.execute("SELECT 1 FROM criterion_results WHERE id=?", (criterion_result_id,)).fetchone() is None:
            raise not_found(f"результат критерия {criterion_result_id}")
        raise HTTPException(409, "оценка критерия ещё не готова")
    if r["criterion_id"] not in _flag_titles(VersionCache(con), r["checklist_version_id"]):
        raise invalid("criterion_result_id", f"критерий {r['criterion_id']} не является флагом грубой ошибки")
    note = ((body.note or "").strip() or None) if body is not None else None
    with tx(con):
        existing = con.execute(
            "SELECT cc.* FROM compliance_cases cc JOIN criterion_results cr ON cr.id = cc.criterion_result_id"
            " JOIN evaluations e ON e.id = cr.evaluation_id"
            " WHERE cc.criterion_result_id = ? OR (e.uid = ? AND cr.criterion_id = ?) ORDER BY cc.id LIMIT 1",
            (criterion_result_id, r["uid"], r["criterion_id"])).fetchone()
        if existing is not None:
            return _case_dto(existing)
        if (r["changed_verdict"] or r["verdict"]) != FLAG_YES:
            raise HTTPException(409, "флаг не выставлен: финальный вердикт не «Да»")
        cid = con.execute(
            "INSERT INTO compliance_cases(criterion_result_id, status, note, created_at) VALUES (?, 'open', ?, ?)",
            (criterion_result_id, note, now_iso())).lastrowid
        row = con.execute("SELECT * FROM compliance_cases WHERE id=?", (cid,)).fetchone()
    return _case_dto(row, created=True)


# ---------------------------------------------------------------- сигналы

@router.get("/unanswered", response_model=UnansweredDTO)
async def unanswered(group: str | None = None, topic: str | None = None, con: sqlite3.Connection = Depends(get_con)):
    """Незакрытые вопросы клиентов («открыт», «частично») по причинам обращения."""
    from cqa.core.signals import unanswered_by_topic
    return unanswered_by_topic(con, group, topic)


@router.get("/reasons", response_model=ReasonsDTO)
async def reasons(group: str | None = None, con: sqlite3.Connection = Depends(get_con)):
    """Причины обращения: число диалогов, средний финальный итог, доля решённых, грубые ошибки."""
    from cqa.core.signals import reasons_summary
    return reasons_summary(con, group)


async def _signals_task(db_path: Path, todo: list[dict], profile: str) -> None:
    from cqa.core.signals import process_pending, release
    from cqa.db.conn import connect

    con = None
    try:
        con = connect(db_path)
        r = await process_pending(con, todo, profile)
        log.info("сигналы (%s): готово %s, ошибок %s, из кэша %s, $%.4f",
                 profile, r["done"], r["failed"], r["cache_hits"], r["cost_usd"])
    except Exception:  # noqa: BLE001 — фон: только в лог
        log.exception("фоновый расчёт сигналов упал")
    finally:
        release(todo)
        if con is not None:
            con.close()


@router.post("/signals", response_model=SignalsRunDTO)
async def run_signals(request: Request, body: SignalsRunCreateDTO | None = None,
                      con: sqlite3.Connection = Depends(get_con)):
    """Посчитать сигналы для текущих оценок группы в фоне (по одному вызову модели на диалог)."""
    from cqa.core.signals import claim_pending

    body = body or SignalsRunCreateDTO()
    profile = resolve_role(con, "signals", is_fake(request))
    todo, already = claim_pending(con, body.group or None, body.uids, body.force)
    if todo:
        tasks: set = request.app.state.bg_tasks
        task = asyncio.create_task(_signals_task(Path(get_settings().db_path), todo, profile))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return SignalsRunDTO(status="scheduled" if todo else "nothing_to_do", queued=len(todo),
                         already=0 if body.force else already, group=body.group, profile=profile)


# ---------------------------------------------------------------- сводка

@router.get("/summary", response_model=SummaryDTO)
async def summary(group: str | None = None, con: sqlite3.Connection = Depends(get_con)):
    """Цифры для дашборда."""
    from cqa.core.signals import unanswered_by_topic

    g_where, g_args = (" WHERE c.group_id = ?", [group]) if group else ("", [])
    row = con.execute(
        "SELECT COUNT(*) AS n, COUNT(e.id) AS n_eval, AVG(COALESCE(e.final_total, e.total)) AS avg_total,"
        " COALESCE(SUM(CASE WHEN e.id IS NOT NULL AND COALESCE(json_extract(e.final_score_json, '$.gross'), e.gross, 0)"
        "   THEN 1 ELSE 0 END), 0) AS gross,"
        " COALESCE(SUM(COALESCE(e.needs_review, 0)), 0) AS review"
        " FROM conversations c LEFT JOIN evaluations e ON e.id = c.current_evaluation_id" + g_where, g_args).fetchone()
    appeals = con.execute(
        "SELECT COUNT(*) FROM appeals a JOIN evaluations x ON x.id = a.evaluation_id JOIN conversations c ON c.uid = x.uid"
        " WHERE a.status = 'open'" + (" AND c.group_id = ?" if group else ""), g_args).fetchone()[0]
    compliance = con.execute(
        "SELECT COUNT(*) FROM compliance_cases cc JOIN criterion_results cr ON cr.id = cc.criterion_result_id"
        " JOIN evaluations e ON e.id = cr.evaluation_id JOIN conversations c ON c.uid = e.uid"
        " WHERE cc.status = 'open'" + (" AND c.group_id = ?" if group else ""), g_args).fetchone()[0]
    un = unanswered_by_topic(con, group)
    return SummaryDTO(
        group=group, n_conversations=row["n"], n_evaluated=row["n_eval"],
        avg_total=round(row["avg_total"], 2) if row["avg_total"] is not None else None,
        gross_count=row["gross"], needs_review=row["review"], open_appeals=appeals,
        risks_open_compliance=compliance, n_with_signals=un["n_with_signals"],
        unanswered_questions=un["total_questions"],
        top_unanswered_topics=[TopicCountDTO(topic=t["topic"], n_questions=t["n_questions"],
                                             n_conversations=t["n_conversations"]) for t in un["topics"][:3]],
    )
