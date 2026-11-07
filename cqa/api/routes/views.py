"""Строки БД → DTO (cqa/api/schemas.py). Общие для роутов и сервиса апелляций.

Финальный вердикт = исходный + последнее решение `change` по апелляции на этот критерий.
Баллы критерия (CriterionScore) — из final_score_json, т. е. по финальному вердикту.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cqa.api.schemas import (
    ChecklistVersionDTO,
    ConversationDTO,
    ConversationSummaryDTO,
    CriterionResultDTO,
    EvaluationDTO,
    LLMCallDTO,
    MessageDTO,
    RunDTO,
)
from cqa.checklist.model import ChecklistConfig
from cqa.llm.redact import redact
from cqa.models import Conversation, CriterionScore, Evidence, ScoreResult


def _loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _score(text: str | None) -> ScoreResult | None:
    if not text:
        return None
    try:
        return ScoreResult.model_validate_json(text)
    except ValueError:
        return None


def _safe(text: str | None) -> str | None:
    return redact(text) if text else text


class VersionCache:
    """Конфиги версий чек-листа в пределах одного запроса (версии неизменяемы)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self._cache: dict[int, tuple[ChecklistConfig, dict[str, Any]]] = {}

    def get(self, version_id: int) -> tuple[ChecklistConfig, dict[str, Any]]:
        if version_id not in self._cache:
            from cqa.checklist.store import get_version
            self._cache[version_id] = get_version(self.con, version_id)
        return self._cache[version_id]


# ---------------------------------------------------------------- диалоги

def audio_api_path(uid: str) -> str:
    return f"/api/conversations/{quote(uid, safe=':')}/audio"


def message_dto(m) -> MessageDTO:
    return MessageDTO(**m.model_dump())


def conversation_dto(conv: Conversation) -> ConversationDTO:
    return ConversationDTO(
        uid=conv.uid, group_id=conv.group_id, external_id=conv.external_id, channel=conv.channel,
        language=conv.language, operator_name=conv.operator_name, has_audio=conv.has_audio,
        audio_url=audio_api_path(conv.uid) if conv.has_audio else None,   # никогда не ссылка организаторов
        messages=[message_dto(m) for m in conv.messages], facts=conv.facts,
    )


def _has_audio(row: sqlite3.Row) -> bool:
    return row["audio_status"] == "ready" and bool(row["audio_path"]) and Path(row["audio_path"]).is_file()


def summary_dto(row: sqlite3.Row) -> ConversationSummaryDTO:
    final = _score(row["final_score_json"])
    gross_json = _loads(row["gross_json"], [])
    return ConversationSummaryDTO(
        uid=row["uid"], group_id=row["group_id"], external_id=row["external_id"], channel=row["channel"],
        language=row["language"], operator_name=row["operator_name"], n_messages=row["n_messages"],
        has_audio=_has_audio(row), evaluation_id=row["evaluation_id"], total=row["total"],
        final_total=row["final_total"],
        gross=final.gross if final else bool(row["gross"]),
        gross_categories=final.gross_categories if final else list(gross_json or []),
        needs_review=bool(row["needs_review"]), open_appeals=row["open_appeals"] or 0, status=row["ev_status"],
    )


# ---------------------------------------------------------------- оценки

def _final_decisions(con: sqlite3.Connection, eval_id: int) -> dict[str, str]:
    """criterion_id → вердикт последнего решения `change`."""
    out: dict[str, str] = {}
    for d in con.execute(
        "SELECT a.criterion_id, d.new_verdict FROM decisions d JOIN appeals a ON a.id = d.appeal_id"
        " WHERE a.evaluation_id=? AND d.action='change' AND d.new_verdict IS NOT NULL ORDER BY d.id",
        (eval_id,),
    ):
        out[d["criterion_id"]] = d["new_verdict"]
    return out


def _last_appeals(con: sqlite3.Connection, eval_id: int) -> dict[str, tuple[int, str]]:
    """criterion_id → (id, status) последней апелляции."""
    out: dict[str, tuple[int, str]] = {}
    for a in con.execute("SELECT id, criterion_id, status FROM appeals WHERE evaluation_id=? ORDER BY id", (eval_id,)):
        out[a["criterion_id"]] = (a["id"], a["status"])
    return out


def criterion_dtos(con: sqlite3.Connection, ev: sqlite3.Row, cfg: ChecklistConfig,
                   only: str | None = None) -> list[CriterionResultDTO]:
    rows = {r["criterion_id"]: r for r in con.execute(
        "SELECT * FROM criterion_results WHERE evaluation_id=?", (ev["id"],))}
    order = [c.id for c in cfg.criteria if c.id in rows] + sorted(set(rows) - {c.id for c in cfg.criteria})
    if only is not None:
        order = [cid for cid in order if cid == only]
    decisions = _final_decisions(con, ev["id"])
    appeals = _last_appeals(con, ev["id"])
    final = _score(ev["final_score_json"])
    scores: dict[str, CriterionScore] = {cs.criterion_id: cs for cs in final.criteria} if final else {}
    out = []
    for cid in order:
        r = rows[cid]
        try:
            crit = cfg.criterion(cid)
            title, section, ctype, weight = crit.title, crit.section, crit.type, float(crit.weight)
            allowed, top = crit.allowed_verdicts(), crit.top
        except KeyError:
            title, section, ctype, weight = cid, "", "yes_no", float(r["weight"] or 0)
            allowed, top = [], None
        rng = _loads(r["checked_range_json"])
        appeal = appeals.get(cid)
        out.append(CriterionResultDTO(
            id=r["id"], criterion_id=cid, title=title, section=section, type=ctype, weight=weight,
            verdict=r["verdict"], final_verdict=decisions.get(cid, r["verdict"]), category=r["category"] or "",
            score=scores.get(cid),
            evidence=[Evidence.model_validate(e) for e in _loads(r["evidence_json"], []) or []],
            checked_range=list(rng) if rng else None,
            explanation=r["explanation"] or "", reasoning=r["reasoning"] or "", how_to_fix=r["how_to_fix"] or "",
            needs_review=bool(r["needs_review"]), second_opinion=_loads(r["second_json"]),
            status=r["status"], error=_safe(r["error"]),
            appeal_id=appeal[0] if appeal else None, appeal_status=appeal[1] if appeal else None,
            allowed_verdicts=allowed, scale_top=top,
        ))
    return out


def _calls_stats(con: sqlite3.Connection, eval_id: int) -> sqlite3.Row:
    return con.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN cache_hit=0 THEN cost_usd END), 0) AS cost,"
        " COALESCE(SUM(latency_ms), 0) AS lat FROM llm_calls WHERE evaluation_id=?",
        (eval_id,),
    ).fetchone()


def evaluation_row(con: sqlite3.Connection, eval_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM evaluations WHERE id=?", (eval_id,)).fetchone()


def evaluation_dto(con: sqlite3.Connection, ev: sqlite3.Row, versions: VersionCache | None = None) -> EvaluationDTO:
    versions = versions or VersionCache(con)
    cfg, meta = versions.get(ev["checklist_version_id"])
    orig = _score(ev["score_json"])
    final = _score(ev["final_score_json"])
    shown = final or orig
    stats = _calls_stats(con, ev["id"])
    return EvaluationDTO(
        id=ev["id"], uid=ev["uid"], run_id=ev["run_id"], checklist_version_id=ev["checklist_version_id"],
        checklist_version=meta["version"], profile=ev["profile"], model=ev["model"],
        prompt_version=ev["prompt_version"], status=ev["status"], total=ev["total"], final_total=ev["final_total"],
        sections=shown.sections if shown else [], original_sections=orig.sections if orig else [],
        gross=shown.gross if shown else bool(ev["gross"]),
        gross_categories=shown.gross_categories if shown else list(_loads(ev["gross_json"], []) or []),
        needs_review=bool(ev["needs_review"]), criteria=criterion_dtos(con, ev, cfg),
        cost_usd=float(stats["cost"] or ev["cost_usd"] or 0),
        latency_ms=int(stats["lat"] or ev["latency_ms"] or 0),
        n_llm_calls=int(stats["n"] or 0), created_at=ev["created_at"],
    )


# ---------------------------------------------------------------- прогоны, чек-листы, журнал

def run_dto(r: dict[str, Any]) -> RunDTO:
    return RunDTO(
        id=r["id"], kind=r["kind"], status=r["status"], checklist_version_id=r["checklist_version_id"],
        judge_profile=r["judge_profile"], model=r["model"], units_total=r["units_total"],
        units_done=r["units_done"], units_failed=r["units_failed"], units_cached=r["units_cached"],
        cost_usd=r["cost_usd"] or 0, eta_sec=r.get("eta_sec"), created_at=r["created_at"],
        started_at=r["started_at"], finished_at=r["finished_at"], metrics=_loads(r.get("metrics_json")),
    )


def version_dto(cfg: ChecklistConfig, meta: dict[str, Any]) -> ChecklistVersionDTO:
    return ChecklistVersionDTO(
        id=meta["id"], checklist_id=meta["checklist_id"], version=meta["version"], status=meta["status"],
        config=cfg.model_dump(mode="json"), comment=meta["comment"], created_at=meta["created_at"],
        is_active=meta["is_active"],
    )


LLM_CALL_COLUMNS = ("id, ts, purpose, criterion_id, profile, provider, model, cache_hit, status, prompt_tokens,"
                    " cached_tokens, completion_tokens, reasoning_tokens, cost_usd, latency_ms, attempt")


def llm_call_dto(r: sqlite3.Row, full: bool = False) -> LLMCallDTO:
    extra: dict[str, Any] = {}
    if full:
        req = r["request_json"]
        extra["request"] = _loads(redact(req), redact(req)) if req else None
        extra["response_text"] = _safe(r["response_text"])
    return LLMCallDTO(
        id=r["id"], ts=r["ts"], purpose=r["purpose"], criterion_id=r["criterion_id"], profile=r["profile"],
        provider=r["provider"], model=r["model"], cache_hit=bool(r["cache_hit"]), status=r["status"],
        prompt_tokens=r["prompt_tokens"], cached_tokens=r["cached_tokens"], completion_tokens=r["completion_tokens"],
        reasoning_tokens=r["reasoning_tokens"], cost_usd=r["cost_usd"], latency_ms=r["latency_ms"],
        attempt=r["attempt"], **extra,
    )
