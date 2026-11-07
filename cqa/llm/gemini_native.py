"""Gemini через SDK google-genai (асинхронно: client.aio).

system → system_instruction; user/assistant → contents с ролями user/model.
Подряд идущие сообщения одной роли склеиваются в один Content из нескольких частей:
общий префикс (system + часть A) остаётся побайтно одинаковым, и неявный кэш провайдера срабатывает.
"""
from __future__ import annotations

import asyncio
import copy
import os
import re
import warnings
import weakref
from typing import Any

from cqa.llm.base import LLMRequest, LLMResponse, Profile, Usage

HTTP_TIMEOUT_MS = 230_000

_REFUSED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "LANGUAGE"}

# клиент привязан к event loop (внутри httpx/aiohttp-сессия), поэтому кэшируем по циклу
_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple, Any]]" = weakref.WeakKeyDictionary()


def with_property_ordering(schema: dict[str, Any], top: list[str] | None) -> dict[str, Any]:
    """Копия схемы с propertyOrdering у каждого object (верхний уровень — из запроса)."""
    out = copy.deepcopy(schema)

    def walk(node: Any, ordering: list[str] | None) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if node.get("type") == "object" and isinstance(props, dict):
                order = [k for k in (ordering or list(props)) if k in props]
                order += [k for k in props if k not in order]
                node["propertyOrdering"] = order
                for v in props.values():
                    walk(v, None)
            if isinstance(node.get("items"), dict):
                walk(node["items"], None)

    walk(out, top)
    return out


def _import_genai():
    # google-genai 3.0 при первой компиляции сыплет SyntaxWarning из types.py — глушим только их
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        from google import genai
        from google.genai import errors, types
    return genai, errors, types


def _client(profile: Profile):
    genai, _errors, types = _import_genai()

    key_env = profile.api_key_env or "GEMINI_API_KEY"
    api_key = os.environ.get(key_env)
    if not api_key:
        raise LookupError(f"не задан ключ: переменная окружения {key_env}")
    loop = asyncio.get_running_loop()
    per_loop = _CLIENTS.setdefault(loop, {})
    ck = (key_env, api_key, profile.api_version)
    client = per_loop.get(ck)
    if client is None:
        opts = types.HttpOptions(timeout=HTTP_TIMEOUT_MS, api_version=profile.api_version) if profile.api_version \
            else types.HttpOptions(timeout=HTTP_TIMEOUT_MS)
        client = per_loop[ck] = genai.Client(api_key=api_key, http_options=opts)
    return client


def _retry_after(e: Any) -> float | None:
    """Retry-After из заголовка или RetryInfo.retryDelay ("33s") из тела ошибки."""
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None)
    if headers:
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra:
            try:
                return float(ra)
            except ValueError:
                pass
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(getattr(e, "details", "") or ""))
    return float(m.group(1)) if m else None


def build_contents(req: LLMRequest):
    _g, _e, types = _import_genai()

    system = [m.content for m in req.messages if m.role == "system"]
    contents: list[types.Content] = []
    for m in req.messages:
        if m.role == "system":
            continue
        role = "model" if m.role == "assistant" else "user"
        if contents and contents[-1].role == role:
            contents[-1].parts.append(types.Part(text=m.content))
        else:
            contents.append(types.Content(role=role, parts=[types.Part(text=m.content)]))
    return ("\n\n".join(system) if system else None), contents


def build_config(req: LLMRequest, profile: Profile, system: str | None):
    _g, _e, types = _import_genai()

    kw: dict[str, Any] = {"max_output_tokens": req.max_output_tokens or profile.max_output_tokens}
    if system:
        kw["system_instruction"] = system
    if req.json_schema is not None and profile.json_mode != "none":
        kw["response_mime_type"] = "application/json"
        kw["response_json_schema"] = with_property_ordering(req.json_schema, req.property_ordering)
    elif profile.json_mode == "json_object":
        kw["response_mime_type"] = "application/json"
    if req.thinking_level:
        kw["thinking_config"] = types.ThinkingConfig(thinking_level=req.thinking_level)
    if req.temperature is not None:
        kw["temperature"] = req.temperature
    return types.GenerateContentConfig(**kw)


def _usage(meta: Any) -> Usage:
    if meta is None:
        return Usage()
    return Usage(
        prompt_tokens=meta.prompt_token_count or 0,
        cached_tokens=meta.cached_content_token_count or 0,
        completion_tokens=meta.candidates_token_count or 0,
        reasoning_tokens=meta.thoughts_token_count or 0,
    )


def _enum_name(v: Any) -> str | None:
    if v is None:
        return None
    return getattr(v, "name", None) or str(v)


class GeminiProvider:
    async def complete(self, req: LLMRequest, profile: Profile) -> LLMResponse:
        from cqa.llm.gateway import TransportError

        _g, errors, _t = _import_genai()

        try:
            client = _client(profile)
        except LookupError as e:
            return LLMResponse(text="", status="http_error", error=str(e), model=profile.model, provider="gemini")
        system, contents = build_contents(req)
        config = build_config(req, profile, system)
        try:
            resp = await client.aio.models.generate_content(model=profile.model, contents=contents, config=config)
        except errors.APIError as e:
            code = int(e.code) if e.code else None
            msg = f"{e.status or ''} {e.message or ''}".strip()[:800]
            if code is None or code in (408, 429) or code >= 500:
                raise TransportError(msg, code=code, retry_after=_retry_after(e)) from None
            return LLMResponse(text="", status="http_error", error=f"{code}: {msg}", model=profile.model, provider="gemini")
        except Exception as e:  # noqa: BLE001 — сетевые сбои httpx/aiohttp
            name = type(e).__name__
            if any(s in name for s in ("Timeout", "Connect", "Network", "RemoteProtocol", "ReadError",
                                       "ServerDisconnected", "ClientOSError")):
                raise TransportError(f"{name}: {e}"[:500]) from None
            raise

        usage = _usage(resp.usage_metadata)
        cand = resp.candidates[0] if resp.candidates else None
        if cand is None:
            fb = getattr(resp, "prompt_feedback", None)
            reason = _enum_name(getattr(fb, "block_reason", None)) or "NO_CANDIDATES"
            return LLMResponse(text="", usage=usage, finish_reason=reason, status="refused",
                               error=f"ответа нет: {reason}", model=resp.model_version or profile.model, provider="gemini")
        parts = (cand.content.parts if cand.content and cand.content.parts else []) or []
        text = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False))
        fr = _enum_name(cand.finish_reason)
        status = "ok"
        error = None
        if fr == "MAX_TOKENS":
            status, error = "truncated", "ответ обрезан по max_output_tokens"
        elif fr in _REFUSED:
            status, error = "refused", f"ответ заблокирован: {fr}"
        return LLMResponse(text=text, usage=usage, finish_reason=fr, status=status, error=error,
                           model=profile.model, provider="gemini")
