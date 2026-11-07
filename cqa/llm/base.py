"""Контракт LLM-слоя. Все вызовы моделей идут через cqa.llm.gateway.complete()."""
from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    messages: list[ChatMessage]
    json_schema: dict[str, Any] | None = None   # JSON Schema ответа (подмножество: object/array/string/enum)
    property_ordering: list[str] | None = None  # порядок полей верхнего уровня (Gemini propertyOrdering)
    max_output_tokens: int | None = None
    thinking_level: str | None = None           # low | medium | high | None (не передавать)
    temperature: float | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


class LLMResponse(BaseModel):
    text: str
    parsed: dict[str, Any] | None = None
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None
    status: Literal["ok", "http_error", "invalid_json", "refused", "truncated"] = "ok"
    error: str | None = None
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    cost_usd: float | None = None               # None — цена модели неизвестна
    cache_hit: bool = False


class Profile(BaseModel):
    name: str
    provider: Literal["gemini", "openai_compat", "fake"]
    model: str
    api_key_env: str | None = None
    base_url_env: str | None = None
    api_version: str | None = None
    thinking_level: str | None = None
    json_mode: Literal["json_schema", "json_object", "none"] = "json_schema"
    max_output_tokens: int = 8192
    max_concurrency: int = 4
    rpm: int = 60
    price: dict[str, float | None] = Field(default_factory=dict)


class Provider(Protocol):
    async def complete(self, req: LLMRequest, profile: Profile) -> LLMResponse: ...


class CallContext(BaseModel):
    """Для журнала вызовов (таблица llm_calls)."""
    purpose: str = "judge"            # judge | retry | cross_check | second_opinion | signals | trainer | tutor | assistant | ping | constructor
    run_id: int | None = None
    evaluation_id: int | None = None
    criterion_result_id: int | None = None
    uid: str | None = None
    criterion_id: str | None = None
    attempt: int = 1
    no_cache: bool = False            # True — не читать кэш (ответ всё равно записывается в кэш)
