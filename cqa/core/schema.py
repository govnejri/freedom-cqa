"""JSON Schema ответа судьи (docs/implementation.md §5). Порядок полей — порядок рассуждения.

Только object, array, string и строковые enum; все поля обязательны; additionalProperties: false.
Подходит и для Gemini (response_json_schema + propertyOrdering), и для OpenAI strict json_schema.
"""
from __future__ import annotations

from typing import Any

from cqa.checklist.model import Criterion

FIELDS = ("evidence", "checked_range", "reasoning", "verdict", "category", "explanation", "how_to_fix")


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def response_schema(crit: Criterion) -> tuple[dict[str, Any], list[str]]:
    props: dict[str, Any] = {
        "evidence": {
            "type": "array",
            "description": "Реплики-доказательства: ID и дословная цитата из текста этой реплики.",
            "items": {
                "type": "object",
                "properties": {
                    "id": _str("ID реплики, например m12"),
                    "quote": _str("дословный фрагмент текста реплики на языке оригинала"),
                },
                "required": ["id", "quote"],
                "additionalProperties": False,
            },
        },
        "checked_range": {
            "type": "object",
            "description": "Проверенный участок, если действия или нарушения нет; иначе пустые строки.",
            "properties": {"from": _str("ID первой реплики или пустая строка"),
                           "to": _str("ID последней реплики или пустая строка")},
            "required": ["from", "to"],
            "additionalProperties": False,
        },
        "reasoning": _str("краткий разбор по шагам проверки, по-русски"),
        "verdict": {"type": "string", "enum": crit.allowed_verdicts()},
    }
    if crit.type == "flag":
        props["category"] = {"type": "string", "enum": [""] + list(crit.flag_categories),
                             "description": "тип нарушения при «Да», иначе пустая строка"}
    props["explanation"] = _str("обоснование по-русски: «Критерий — вердикт. … Основание — mN.»")
    props["how_to_fix"] = _str("как надо было, на языке клиента; пустая строка, если нарушения нет")
    ordering = list(props)
    schema = {"type": "object", "properties": props, "required": ordering, "additionalProperties": False}
    return schema, ordering
