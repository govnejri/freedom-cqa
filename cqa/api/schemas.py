"""DTO REST API — контракт для фронтенда. Менять только согласованно с frontend/js/api.js."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from cqa.models import CriterionScore, Evidence, Facts, SectionScore


class MessageDTO(BaseModel):
    mid: str
    idx: int
    speaker: str
    text: str
    ts_raw: str
    offset_sec: float | None = None
    audio_sec: float | None = None
    segment: str
    recorded: bool = True
    after_end: bool = False
    event_type: str | None = None
    dup_of: str | None = None


class ConversationSummaryDTO(BaseModel):
    uid: str
    group_id: str
    external_id: str
    channel: str
    language: str
    operator_name: str | None = None
    n_messages: int
    has_audio: bool
    evaluation_id: int | None = None
    total: float | None = None          # исходный итог
    final_total: float | None = None    # с учётом апелляций
    gross: bool = False
    gross_categories: list[str] = Field(default_factory=list)
    needs_review: bool = False
    open_appeals: int = 0
    status: str | None = None           # статус оценки


class ConversationDTO(BaseModel):
    uid: str
    group_id: str
    external_id: str
    channel: str
    language: str
    operator_name: str | None = None
    has_audio: bool
    audio_url: str | None = None        # ВНУТРЕННИЙ путь бэкенда (/api/conversations/{uid}/audio), не ссылка организаторов
    messages: list[MessageDTO]
    facts: Facts | None = None


class CriterionResultDTO(BaseModel):
    id: int                              # criterion_result_id
    criterion_id: str
    title: str
    section: str
    type: str                            # yes_no | scale | flag
    weight: float
    verdict: str | None                  # исходный вердикт
    final_verdict: str | None            # с учётом решения по апелляции
    category: str = ""
    score: CriterionScore | None = None  # по финальному вердикту
    evidence: list[Evidence] = Field(default_factory=list)
    checked_range: list[str] | None = None
    explanation: str = ""
    reasoning: str = ""
    how_to_fix: str = ""
    needs_review: bool = False
    second_opinion: dict[str, Any] | None = None
    status: str
    error: str | None = None
    appeal_id: int | None = None
    appeal_status: str | None = None
    allowed_verdicts: list[str] = Field(default_factory=list)   # допустимые вердикты критерия (для апелляции)
    scale_top: int | None = None                                 # верхняя ступень шкалы («15 из 20»)


class EvaluationDTO(BaseModel):
    id: int
    uid: str
    run_id: int | None
    checklist_version_id: int
    checklist_version: int
    profile: str
    model: str
    prompt_version: str
    status: str
    total: float | None                  # исходный итог
    final_total: float | None            # итог с учётом апелляций
    sections: list[SectionScore] = Field(default_factory=list)          # финальные
    original_sections: list[SectionScore] = Field(default_factory=list)
    gross: bool = False
    gross_categories: list[str] = Field(default_factory=list)
    needs_review: bool = False
    criteria: list[CriterionResultDTO]
    cost_usd: float = 0
    latency_ms: int = 0
    n_llm_calls: int = 0
    created_at: str


class RunCreateDTO(BaseModel):
    uids: list[str] | None = None
    group: str | None = None
    checklist_version_id: int | None = None   # None — активная версия
    profile: str | None = None                # None — роль judge
    criteria: list[str] | None = None         # только эти критерии (остальные из кэша/предыдущей оценки)
    force: bool = False                       # игнорировать кэш
    cross_check: bool = False
    kind: str = "batch"


class RunDTO(BaseModel):
    id: int
    kind: str
    status: str
    checklist_version_id: int
    judge_profile: str
    model: str
    units_total: int
    units_done: int
    units_failed: int
    units_cached: int
    cost_usd: float
    eta_sec: float | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    metrics: dict[str, Any] | None = None


class ChecklistVersionDTO(BaseModel):
    id: int
    checklist_id: str
    version: int
    status: str
    config: dict[str, Any]                    # ChecklistConfig как JSON
    comment: str | None = None
    created_at: str
    is_active: bool = False


class ValidationErrorDTO(BaseModel):
    loc: str
    msg: str


class AppealCreateDTO(BaseModel):
    evaluation_id: int
    criterion_id: str
    comment: str = Field(max_length=2000)
    counter_evidence: list[str] = Field(default_factory=list)   # ["m12"]


class DecisionCreateDTO(BaseModel):
    action: str                               # confirm | change
    new_verdict: str | None = None
    reason: str = Field(max_length=2000)
    make_precedent: bool = False


class AppealDTO(BaseModel):
    id: int
    evaluation_id: int
    uid: str
    criterion_id: str
    criterion_title: str
    comment: str
    counter_evidence: list[str] = Field(default_factory=list)
    status: str
    created_at: str
    original: CriterionResultDTO
    fragment: list[MessageDTO] = Field(default_factory=list)   # доказательства/проверенный участок ±2 реплики
    ai_opinion: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    prev_final_total: float | None = None
    new_final_total: float | None = None


class LLMCallDTO(BaseModel):
    id: int
    ts: str
    purpose: str
    criterion_id: str | None
    profile: str
    provider: str
    model: str
    cache_hit: bool
    status: str
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float | None
    latency_ms: int
    attempt: int
    request: Any | None = None                # только в детальном запросе
    response_text: str | None = None


class HealthDTO(BaseModel):
    ok: bool
    db: bool
    roles: dict[str, str]                     # роль → профиль
    keys: dict[str, bool]                     # наличие ключей без значений
    active_checklist_version_id: int | None = None
