"""Стоимость вызова по usage и ценам профиля (USD за 1M токенов)."""
from __future__ import annotations

from collections.abc import Mapping

from cqa.llm.base import Usage

PER = 1_000_000


def cost(usage: Usage, price: Mapping[str, float | None] | None) -> float | None:
    """(prompt − cached)·input + cached·cached_input + (completion + reasoning)·output.

    usage.completion_tokens — видимый ответ без рассуждений (провайдеры приводят к этому виду).
    None — нужная цена неизвестна. Цена кэшированных токенов нужна, только если они есть.
    """
    price = price or {}
    p_in, p_cached, p_out = price.get("input"), price.get("cached_input"), price.get("output")
    if p_in is None or p_out is None:
        return None
    cached = max(0, min(usage.cached_tokens, usage.prompt_tokens))
    if cached and p_cached is None:
        return None
    total = (usage.prompt_tokens - cached) * p_in
    total += cached * (p_cached or 0.0)
    total += (usage.completion_tokens + usage.reasoning_tokens) * p_out
    return round(total / PER, 8)
