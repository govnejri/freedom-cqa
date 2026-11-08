"""OpenAI-совместимый API: OpenAI, Gemini-совместимый endpoint, Ollama, vLLM, llama.cpp.

json_mode профиля:
- json_schema — response_format {"type": "json_schema", strict};
- json_object — {"type": "json_object"}, схема текстом в system;
- none — без response_format, схема текстом в system.
Usage приводится к виду gateway: completion_tokens — видимый ответ без reasoning-токенов.

Обрезанный контекст. Ollama не сообщает об ошибке, если промпт длиннее num_ctx: она молча выбрасывает
старые сообщения (у судьи — весь диалог), и модель выносит вердикт, не видя диалога (замер 2026-11-08, Ollama 0.18).
Поэтому ответ со статусом ok перепроверяется (context_problem) и при подозрении помечается truncated:
- слишком много символов на токен промпта — сервер выбросил часть промпта;
- промпт + ответ ≥ фактического num_ctx модели (Ollama /api/ps) — контекст переполнен при генерации.
"""
from __future__ import annotations

import asyncio
import json
import os
import weakref
from typing import Any

from cqa.llm.base import LLMRequest, LLMResponse, Profile, Usage

HTTP_TIMEOUT_SEC = 230.0

_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple, Any]]" = weakref.WeakKeyDictionary()

SCHEMA_HINT = ("Ответь одним JSON-объектом строго по этой JSON Schema, без текста вокруг и без ```:\n{schema}")

# Русский и казахский текст в токенизаторе Gemma — 2.3–2.7 символа на токен, английский — до ~4.5.
# Больше MAX_CHARS_PER_TOKEN — сервер отбросил часть промпта. Короткие промпты не проверяем.
MAX_CHARS_PER_TOKEN = 6.0
MIN_CHARS_FOR_RATIO = 4000

_NUM_CTX: dict[tuple[str, str], int | None] = {}   # (base_url, model) → num_ctx; None — сервер его не сообщает


def context_problem(messages: list[dict[str, str]], usage: Usage, num_ctx: int | None) -> str | None:
    """Причина считать ответ обрезанным по контексту или None."""
    chars = sum(len(m["content"]) for m in messages)
    if usage.prompt_tokens and chars >= MIN_CHARS_FOR_RATIO and chars / usage.prompt_tokens > MAX_CHARS_PER_TOKEN:
        return (f"промпт обрезан сервером: {usage.prompt_tokens} токенов на {chars} символов — "
                f"увеличьте num_ctx модели")
    total = usage.prompt_tokens + usage.completion_tokens + usage.reasoning_tokens
    if num_ctx and total >= num_ctx:
        return f"контекст переполнен: промпт и ответ — {total} токенов при num_ctx={num_ctx}"
    return None


async def fetch_num_ctx(base_url: str, model: str, transport=None) -> tuple[bool, int | None]:
    """Ollama: фактический context_length загруженной модели из /api/ps.

    (True, n) — найден; (True, None) — сервер не Ollama или не сообщает; (False, None) — модели нет среди
    загруженных (спросить позже)."""
    import httpx

    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as h:
            r = await h.get(root + "/api/ps")
        models = r.json().get("models") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        models = None
    if not isinstance(models, list):
        return True, None
    names = {model} if ":" in model else {model, model + ":latest"}
    for m in models:
        if isinstance(m, dict) and (m.get("name") in names or m.get("model") in names):
            n = m.get("context_length")
            return True, (n if isinstance(n, int) and n > 0 else None)
    return False, None


async def _num_ctx(base_url: str | None, model: str) -> int | None:
    if not base_url:
        return None
    key = (base_url, model)
    if key not in _NUM_CTX:
        final, n = await fetch_num_ctx(base_url, model)
        if not final:
            return None
        _NUM_CTX[key] = n
    return _NUM_CTX[key]


def _client(profile: Profile):
    from openai import AsyncOpenAI

    base_url = os.environ.get(profile.base_url_env) if profile.base_url_env else None
    if profile.base_url_env and not base_url:
        raise LookupError(f"не задан адрес: переменная окружения {profile.base_url_env}")
    api_key = (os.environ.get(profile.api_key_env) if profile.api_key_env else None) or "local"
    loop = asyncio.get_running_loop()
    per_loop = _CLIENTS.setdefault(loop, {})
    ck = (base_url, api_key)
    client = per_loop.get(ck)
    if client is None:
        client = per_loop[ck] = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=HTTP_TIMEOUT_SEC, max_retries=0)
    return client


def build_messages(req: LLMRequest, profile: Profile) -> list[dict[str, str]]:
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    if req.json_schema is not None and profile.json_mode in ("json_object", "none"):
        hint = SCHEMA_HINT.format(schema=json.dumps(req.json_schema, ensure_ascii=False))
        if msgs and msgs[0]["role"] == "system":
            msgs[0] = {"role": "system", "content": msgs[0]["content"] + "\n\n" + hint}
        else:
            msgs.insert(0, {"role": "system", "content": hint})
    return msgs


def build_kwargs(req: LLMRequest, profile: Profile) -> dict[str, Any]:
    kw: dict[str, Any] = {"model": profile.model, "messages": build_messages(req, profile)}
    if req.max_output_tokens:
        kw["max_tokens"] = req.max_output_tokens
    if req.temperature is not None:
        kw["temperature"] = req.temperature
    if req.thinking_level:
        kw["reasoning_effort"] = req.thinking_level
    if req.json_schema is not None and profile.json_mode == "json_schema":
        kw["response_format"] = {"type": "json_schema",
                                 "json_schema": {"name": "verdict", "schema": req.json_schema, "strict": True}}
    elif profile.json_mode == "json_object":
        kw["response_format"] = {"type": "json_object"}
    return kw


def _usage(u: Any) -> Usage:
    if u is None:
        return Usage()
    pd = getattr(u, "prompt_tokens_details", None)
    cd = getattr(u, "completion_tokens_details", None)
    reasoning = (getattr(cd, "reasoning_tokens", None) or 0) if cd else 0
    completion = u.completion_tokens or 0
    return Usage(
        prompt_tokens=u.prompt_tokens or 0,
        cached_tokens=(getattr(pd, "cached_tokens", None) or 0) if pd else 0,
        completion_tokens=max(0, completion - reasoning),
        reasoning_tokens=reasoning,
    )


class OpenAICompatProvider:
    async def complete(self, req: LLMRequest, profile: Profile) -> LLMResponse:
        import openai

        from cqa.llm.gateway import TransportError

        try:
            client = _client(profile)
        except LookupError as e:
            return LLMResponse(text="", status="http_error", error=str(e), model=profile.model, provider="openai_compat")
        kw = build_kwargs(req, profile)
        try:
            resp = await client.chat.completions.create(**kw)
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            raise TransportError(f"{type(e).__name__}: {e}"[:500]) from None
        except openai.APIStatusError as e:
            code = e.status_code
            ra = None
            try:
                ra = float(e.response.headers.get("retry-after")) if e.response.headers.get("retry-after") else None
            except (TypeError, ValueError):
                ra = None
            if code in (408, 429) or code >= 500:
                raise TransportError(str(e)[:800], code=code, retry_after=ra) from None
            return LLMResponse(text="", status="http_error", error=f"{code}: {str(e)[:800]}",
                               model=profile.model, provider="openai_compat")

        usage = _usage(resp.usage)
        if not resp.choices:
            return LLMResponse(text="", usage=usage, status="refused", error="ответ без choices",
                               model=profile.model, provider="openai_compat")
        ch = resp.choices[0]
        msg = ch.message
        text = msg.content or ""
        fr = ch.finish_reason
        status, error = "ok", None
        if getattr(msg, "refusal", None):
            status, error = "refused", f"отказ модели: {msg.refusal}"[:500]
        elif fr == "length":
            status, error = "truncated", "ответ обрезан по max_tokens"
        elif fr == "content_filter":
            status, error = "refused", "ответ заблокирован фильтром"
        if status == "ok":
            base_url = os.environ.get(profile.base_url_env) if profile.base_url_env else None
            problem = context_problem(kw["messages"], usage, await _num_ctx(base_url, profile.model))
            if problem:
                status, error = "truncated", problem
        return LLMResponse(text=text, usage=usage, finish_reason=fr, status=status, error=error,
                           model=profile.model, provider="openai_compat")
