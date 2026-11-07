"""Единая точка вызова моделей: кэш → провайдер (с лимитами и повторами транспорта) → разбор → журнал и стоимость.

complete() асинхронный; complete_sync() — для CLI и скриптов.
Без `con` нет ни кэша, ни журнала.

Журнал (llm_calls): строка на каждую попытку, включая неудачные попытки транспорта.
Попадание в кэш тоже пишется строкой с cache_hit=1 и ИСХОДНОЙ стоимостью; в самом ответе cost_usd=0.
Реальные траты = SUM(cost_usd) WHERE cache_hit=0.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from typing import Any

from cqa.llm import jsonparse, pricing
from cqa.llm.base import CallContext, LLMRequest, LLMResponse, Profile, Provider

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5           # попыток транспорта на один запрос
TIMEOUT_SEC = 240.0        # общий таймаут одного вызова провайдера
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}

_sleep = asyncio.sleep     # подменяется в тестах


class TransportError(Exception):
    """Сбой транспорта у провайдера. code=None — таймаут или обрыв соединения."""

    def __init__(self, message: str, code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.code is None or self.code in RETRYABLE_CODES or self.code >= 500


class GatewayResponse(LLMResponse):
    """LLMResponse + id строки журнала последней попытки (None без `con`)."""
    call_id: int | None = None


# ---------- запрос и ключ кэша ----------

def effective_request(req: LLMRequest, profile: Profile) -> LLMRequest:
    """Подставить значения профиля: max_output_tokens и thinking_level."""
    return req.model_copy(update={
        "max_output_tokens": req.max_output_tokens or profile.max_output_tokens,
        "thinking_level": req.thinking_level if req.thinking_level is not None else profile.thinking_level,
    })


def request_payload(req: LLMRequest) -> dict[str, Any]:
    return {
        "messages": [m.model_dump() for m in req.messages],
        "json_schema": req.json_schema,
        "property_ordering": req.property_ordering,
        "thinking_level": req.thinking_level,
        "temperature": req.temperature,
        "max_output_tokens": req.max_output_tokens,
    }


def cache_key(req: LLMRequest, profile: Profile) -> str:
    """sha256 канонического JSON {provider, model, messages, json_schema, property_ordering, thinking_level,
    temperature, max_output_tokens}. Запрос — уже с подставленными значениями профиля."""
    payload = {"provider": profile.provider, "model": profile.model, **request_payload(req)}
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ---------- провайдеры ----------

_PROVIDERS: dict[str, Provider] = {}


def provider_for(profile: Profile) -> Provider:
    p = _PROVIDERS.get(profile.provider)
    if p is None:
        if profile.provider == "gemini":
            from cqa.llm.gemini_native import GeminiProvider
            p = GeminiProvider()
        elif profile.provider == "openai_compat":
            from cqa.llm.openai_compat import OpenAICompatProvider
            p = OpenAICompatProvider()
        elif profile.provider == "fake":
            from cqa.llm.fake import FakeProvider
            p = FakeProvider()
        else:  # pragma: no cover — Literal в Profile
            raise ValueError(f"неизвестный провайдер {profile.provider!r}")
        _PROVIDERS[profile.provider] = p
    return p


def backoff(n: int) -> float:
    """Пауза перед повтором n (1-based): min(60, 2·2^(n−1)) + jitter."""
    return min(60.0, 2.0 * 2 ** (n - 1)) + random.uniform(0, 1)


# ---------- основной вызов ----------

def _finish(resp: LLMResponse, req: LLMRequest, profile: Profile) -> LLMResponse:
    resp.model = resp.model or profile.model
    resp.provider = resp.provider or profile.provider
    if resp.status == "ok":
        parsed = jsonparse.parse(resp.text)
        if parsed is not None:
            resp.parsed = parsed
        elif req.json_schema is not None or profile.json_mode == "json_object":
            resp.status = "invalid_json"
            resp.error = resp.error or "ответ модели не разбирается как JSON-объект"
    resp.cost_usd = pricing.cost(resp.usage, profile.price)
    return resp


async def complete(
    req: LLMRequest,
    profile: Profile,
    ctx: CallContext | None = None,
    con=None,
) -> GatewayResponse:
    from cqa.core.ratelimit import limiter_for
    from cqa.settings import get_settings

    get_settings()   # подтягивает ключи из .env в окружение для провайдеров
    ctx = ctx or CallContext(purpose="adhoc")
    req = effective_request(req, profile)
    key = cache_key(req, profile)

    store = None
    req_log = ""
    if con is not None:
        from cqa.llm import store
        req_log = store.request_json(request_payload(req))

    # 1. кэш
    if store is not None and not ctx.no_cache:
        row = store.cache_get(con, key)
        if row is not None:
            hit = store.response_from_cache(row, profile)
            call_id = store.log_call(con, ctx=ctx, profile=profile, cache_key=key, request=req_log, resp=hit)
            return GatewayResponse(**hit.model_dump(exclude={"cost_usd"}), cost_usd=0.0, call_id=call_id)

    provider = provider_for(profile)
    limiter = limiter_for(profile)
    cur = req
    doubled = False
    transport_attempt = 0
    call_id: int | None = None

    while True:
        t0 = time.monotonic()
        err: TransportError | None = None
        resp: LLMResponse | None = None
        # 2. вызов провайдера под лимитами профиля
        try:
            async with limiter.slot():
                t0 = time.monotonic()
                resp = await asyncio.wait_for(provider.complete(cur, profile), TIMEOUT_SEC)
        except TransportError as e:
            err = e
        except TimeoutError:
            err = TransportError(f"таймаут {TIMEOUT_SEC:.0f} с")
        except Exception as e:  # noqa: BLE001 — ошибка SDK/провайдера не должна ронять прогон
            log.warning("provider %s raised %s", profile.name, type(e).__name__)
            resp = LLMResponse(text="", status="http_error", error=f"{type(e).__name__}: {e}"[:1000])
        latency = int((time.monotonic() - t0) * 1000)

        # 3. повторы транспорта
        if err is not None:
            transport_attempt += 1
            fail = LLMResponse(text="", status="http_error", error=f"{err.code or 'transport'}: {err}"[:1000],
                               model=profile.model, provider=profile.provider, latency_ms=latency)
            if store is not None:
                call_id = store.log_call(con, ctx=ctx, profile=profile, cache_key=key, request=req_log, resp=fail)
            if not err.retryable or transport_attempt >= MAX_ATTEMPTS:
                return GatewayResponse(**fail.model_dump(), call_id=call_id)
            delay = backoff(transport_attempt)
            if err.code == 429:
                pause = err.retry_after if err.retry_after else delay
                log.warning("429 от %s: пауза профиля %.1f с", profile.name, pause)
                limiter.pause(pause)
            else:
                log.warning("%s от %s: повтор через %.1f с", err.code or "сбой транспорта", profile.name, delay)
                await _sleep(delay)
            continue

        assert resp is not None
        resp.latency_ms = resp.latency_ms or latency
        # 4. разбор и стоимость
        resp = _finish(resp, cur, profile)

        if resp.status == "truncated" and profile.provider == "gemini" and not doubled:
            if store is not None:
                store.log_call(con, ctx=ctx, profile=profile, cache_key=key, request=req_log, resp=resp)
            doubled = True
            cur = cur.model_copy(update={"max_output_tokens": (cur.max_output_tokens or profile.max_output_tokens) * 2})
            log.info("ответ %s обрезан: повтор с max_output_tokens=%s", profile.name, cur.max_output_tokens)
            continue

        # 5. журнал и кэш (кэш — по исходному ключу, даже если ответ получен с удвоенным лимитом)
        if store is not None:
            call_id = store.log_call(con, ctx=ctx, profile=profile, cache_key=key, request=req_log, resp=resp)
            if resp.status == "ok":
                store.cache_put(con, key, profile, resp)
        return GatewayResponse(**resp.model_dump(), call_id=call_id)


def complete_sync(req: LLMRequest, profile: Profile, ctx: CallContext | None = None, con=None) -> GatewayResponse:
    return asyncio.run(complete(req, profile, ctx, con))
