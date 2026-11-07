"""Судья критерия: промпт → LLM → валидатор → один повтор при жёсткой ошибке (docs/implementation.md §5).

Все функции асинхронные; *_sync — обёртки для CLI и скриптов.
judge_criterion() возвращает CriterionVerdict (контракт runner); judge_criterion_full() — то же
плюс отчёт валидатора, попытки, стоимость и id строк журнала.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from cqa.checklist.model import ChecklistConfig, Criterion
from cqa.core.prompt import build_messages, build_retry
from cqa.core.schema import response_schema
from cqa.core.validate import ValidationReport, validate
from cqa.llm import gateway
from cqa.llm.base import CallContext, LLMRequest, LLMResponse, Profile, Usage
from cqa.models import Conversation, CriterionVerdict, ScoreResult

log = logging.getLogger(__name__)

_STATUS_ERRORS = {
    "invalid_json": "ответ не разобрался как JSON: верни ровно один JSON-объект по схеме, без текста вокруг",
    "truncated": "ответ обрезан по длине: пиши reasoning короче, 3–6 предложений",
    "refused": "ответ заблокирован: оцени диалог по критерию и верни JSON по схеме",
}


class JudgeOutcome(BaseModel):
    verdict: CriterionVerdict
    report: ValidationReport | None = None       # отчёт валидатора по принятому ответу
    attempts: int = 0                            # попыток судьи (1 или 2)
    call_ids: list[int] = Field(default_factory=list)
    cache_hit: bool = False                      # все попытки — из кэша
    cost_usd: float | None = 0.0                 # реальные траты (кэш = 0); None — цена неизвестна
    latency_ms: int = 0
    usage: Usage = Field(default_factory=Usage)


def _report_for_status(resp: LLMResponse) -> ValidationReport:
    return ValidationReport(errors=[_STATUS_ERRORS.get(resp.status, resp.error or resp.status)])


def _verdict_from(rep: ValidationReport, crit: Criterion, *, needs_review: bool = False) -> CriterionVerdict:
    evidence = [e for e in rep.evidence if e.ok] if needs_review else list(rep.evidence)
    return CriterionVerdict(
        criterion_id=crit.id,
        verdict=rep.verdict,
        category=rep.category,
        evidence=evidence,
        checked_range=rep.checked_range,
        reasoning=rep.reasoning,
        explanation=rep.explanation,
        how_to_fix=rep.how_to_fix,
        needs_review=needs_review,
        status="done",
        error=("проверка: " + "; ".join(rep.errors))[:1000] if needs_review and rep.errors else None,
    )


def _retry_errors(rep: ValidationReport) -> list[str]:
    """Ошибки для retry.j2: жёсткие + мягкие про ненайденные цитаты (их причина)."""
    return rep.errors + [w for w in rep.warnings if "не найдена" in w]


async def judge_criterion_full(
    conv: Conversation,
    cfg: ChecklistConfig,
    crit: Criterion,
    profile: Profile,
    ctx: CallContext | None = None,
    con=None,
) -> JudgeOutcome:
    ctx = ctx or CallContext(purpose="judge")
    base = ctx.model_copy(update={"uid": ctx.uid or conv.uid, "criterion_id": ctx.criterion_id or crit.id})
    messages = build_messages(conv, cfg, crit)
    schema, ordering = response_schema(crit)
    req = LLMRequest(messages=messages, json_schema=schema, property_ordering=ordering)

    out = JudgeOutcome(verdict=CriterionVerdict(criterion_id=crit.id, verdict="", status="failed"))
    hits: list[bool] = []
    t0 = time.monotonic()

    def account(r: gateway.GatewayResponse) -> None:
        out.attempts += 1
        if r.call_id is not None:
            out.call_ids.append(r.call_id)
        hits.append(r.cache_hit)
        if out.cost_usd is not None:
            out.cost_usd = None if r.cost_usd is None else out.cost_usd + r.cost_usd
        u = r.usage
        out.usage = Usage(prompt_tokens=out.usage.prompt_tokens + u.prompt_tokens,
                          cached_tokens=out.usage.cached_tokens + u.cached_tokens,
                          completion_tokens=out.usage.completion_tokens + u.completion_tokens,
                          reasoning_tokens=out.usage.reasoning_tokens + u.reasoning_tokens)

    def mark_failed(r: gateway.GatewayResponse, rep: ValidationReport) -> None:
        if con is not None and r.call_id is not None and r.status == "ok":
            from cqa.llm.store import mark_call
            mark_call(con, r.call_id, "validation_failed", "; ".join(rep.errors)[:1000])

    def finish(v: CriterionVerdict, rep: ValidationReport | None) -> JudgeOutcome:
        out.verdict, out.report = v, rep
        out.cache_hit = bool(hits) and all(hits)
        out.latency_ms = int((time.monotonic() - t0) * 1000)
        return out

    # попытка 1
    r1 = await gateway.complete(req, profile, base.model_copy(update={"attempt": 1}), con)
    account(r1)
    if r1.status == "http_error":
        return finish(CriterionVerdict(criterion_id=crit.id, verdict="", status="failed", error=r1.error), None)
    rep1 = validate(r1.parsed, crit, conv) if r1.status == "ok" else _report_for_status(r1)
    if rep1.ok:
        return finish(_verdict_from(rep1, crit), rep1)
    mark_failed(r1, rep1)

    # попытка 2: ответ модели + список ошибок
    retry_req = req.model_copy(update={"messages": build_retry(messages, r1.text, _retry_errors(rep1))})
    r2 = await gateway.complete(retry_req, profile, base.model_copy(update={"purpose": "retry", "attempt": 2}), con)
    account(r2)
    rep2: ValidationReport | None = None
    if r2.status != "http_error":
        rep2 = validate(r2.parsed, crit, conv) if r2.status == "ok" else _report_for_status(r2)
        if rep2.ok:
            return finish(_verdict_from(rep2, crit), rep2)
        mark_failed(r2, rep2)

    # повтор не помог: допустимый вердикт сохраняем с needs_review, иначе failed
    best = rep2 if rep2 is not None and rep2.verdict_ok else (rep1 if rep1.verdict_ok else None)
    if best is not None:
        return finish(_verdict_from(best, crit, needs_review=True), best)
    last = rep2 or rep1
    err = "; ".join(last.errors) if last.errors else (r2.error or "ответ не прошёл проверку")
    return finish(CriterionVerdict(criterion_id=crit.id, verdict="", status="failed", error=err[:1000]), last)


async def judge_criterion(
    conv: Conversation,
    cfg: ChecklistConfig,
    crit: Criterion,
    profile: Profile,
    ctx: CallContext | None = None,
    con=None,
) -> CriterionVerdict:
    return (await judge_criterion_full(conv, cfg, crit, profile, ctx, con)).verdict


def channel_na(conv: Conversation, crit: Criterion) -> CriterionVerdict | None:
    """N/A без вызова модели, если критерий не применяется к каналу диалога."""
    if crit.channels and conv.channel not in crit.channels:
        return CriterionVerdict(criterion_id=crit.id, verdict="N/A",
                                explanation=f"{crit.title} — N/A. Критерий не применяется к каналу «{conv.channel}».")
    return None


def score_verdicts(cfg: ChecklistConfig, conv: Conversation, verdicts: dict[str, CriterionVerdict]) -> ScoreResult | None:
    """Итог по вердиктам; None — модуля скоринга нет или вердиктов не хватает (часть критериев, failed)."""
    try:
        from cqa.core.scoring import score
    except ImportError:
        return None
    done = {cid: v.verdict for cid, v in verdicts.items() if v.status == "done"}
    cats = {cid: v.category for cid, v in verdicts.items() if v.category}
    try:
        return score(cfg, done, channel=conv.channel, categories=cats)
    except ValueError as e:
        log.info("итог не рассчитан для %s: %s", conv.uid, e)
        return None


async def evaluate_conversation(
    conv: Conversation,
    cfg: ChecklistConfig,
    profile: Profile,
    criteria: list[str] | None = None,
    con=None,
    ctx: CallContext | None = None,
) -> tuple[dict[str, CriterionVerdict], ScoreResult | None]:
    """Все (или выбранные) критерии диалога: первый — отдельно (прогрев префикса), остальные параллельно."""
    crits = [cfg.criterion(c) for c in criteria] if criteria else cfg.enabled_criteria()
    base = ctx or CallContext(purpose="judge", uid=conv.uid)
    results: dict[str, CriterionVerdict] = {}
    todo: list[Criterion] = []
    for c in crits:
        na = channel_na(conv, c)
        if na is not None:
            results[c.id] = na
        else:
            todo.append(c)

    async def one(c: Criterion) -> CriterionVerdict:
        try:
            return await judge_criterion(conv, cfg, c, profile, base.model_copy(update={"criterion_id": c.id}), con)
        except Exception as e:  # noqa: BLE001 — один критерий не роняет оценку диалога
            log.exception("judge failed: %s %s", conv.uid, c.id)
            return CriterionVerdict(criterion_id=c.id, verdict="", status="failed", error=f"{type(e).__name__}: {e}"[:500])

    if todo:
        results[todo[0].id] = await one(todo[0])
        rest = await asyncio.gather(*(one(c) for c in todo[1:]))
        for c, v in zip(todo[1:], rest):
            results[c.id] = v
    ordered = {c.id: results[c.id] for c in crits}
    return ordered, score_verdicts(cfg, conv, ordered)


# ---------- синхронные обёртки ----------

def judge_criterion_sync(*args: Any, **kw: Any) -> CriterionVerdict:
    return asyncio.run(judge_criterion(*args, **kw))


def evaluate_conversation_sync(*args: Any, **kw: Any) -> tuple[dict[str, CriterionVerdict], ScoreResult | None]:
    return asyncio.run(evaluate_conversation(*args, **kw))
