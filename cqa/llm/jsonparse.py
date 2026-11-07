"""Достаём JSON-объект из текста ответа модели: голый JSON, блок ```json, JSON с текстом вокруг."""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)
_DECODER = json.JSONDecoder()


def parse(text: str | None) -> dict[str, Any] | None:
    """JSON-объект верхнего уровня или None, если разобрать не удалось."""
    if not text:
        return None
    s = text.strip().lstrip("﻿")
    candidates = [s]
    candidates += [m.group(1).strip() for m in _FENCE.finditer(s)]
    for c in candidates:
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    # JSON внутри текста: пробуем каждую открывающую скобку слева направо
    start = s.find("{")
    while start != -1:
        try:
            obj, _ = _DECODER.raw_decode(s, start)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return obj
        start = s.find("{", start + 1)
    return None
