"""Терпимый разбор ответа API организаторов.

Неизвестные поля игнорируются, неизвестная роль → `unknown`, пустой или битый транскрипт не роняет разбор.
`audio_url` (содержит `sig=`) только переносится дальше: здесь он не печатается и не попадает в предупреждения.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

KNOWN_SPEAKERS = frozenset({"client", "operator", "bot", "system"})


class ParseError(ValueError):
    """Запись нельзя использовать (нет conversation_id и т. п.)."""


@dataclass
class RawMessage:
    timestamp: str
    speaker: str          # client | operator | bot | system | unknown
    text: str
    speaker_raw: str = ""  # исходное значение роли, если она не распознана


@dataclass
class RawRecord:
    conversation_id: str
    channel: str
    language: str
    transcript: list[RawMessage]
    audio_url: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_call(self) -> bool:
        ch = self.channel.casefold()
        return "voice" in ch or "call" in ch


@dataclass
class ParsedPayload:
    group_id: str | None
    total_records: int | None
    records: list[dict]
    error: str = ""
    is_success: bool = True


def _str(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def parse_speaker(value: Any) -> tuple[str, bool]:
    """→ (роль, распознана ли)."""
    s = _str(value).strip().casefold()
    if s in KNOWN_SPEAKERS:
        return s, True
    return "unknown", False


def parse_message(item: Any) -> RawMessage:
    if not isinstance(item, dict):
        # Сохраняем позицию в транскрипте (ID реплик не сдвигаются), но содержимое не угадываем.
        return RawMessage(timestamp="", speaker="unknown", text=_str(item) if isinstance(item, str) else "")
    speaker, known = parse_speaker(item.get("speaker"))
    return RawMessage(
        timestamp=_str(item.get("timestamp")).strip(),
        speaker=speaker,
        text=_str(item.get("text")),
        speaker_raw="" if known else _str(item.get("speaker")),
    )


def _transcript_items(value: Any, warnings: list[str]) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        # По заданию transcript — уже массив, но строка с JSON тоже допустима.
        try:
            value = json.loads(value) if value.strip() else []
        except json.JSONDecodeError:
            warnings.append("transcript: строка не является JSON")
            return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        warnings.append(f"transcript: неожиданный тип {type(value).__name__}")
        return []
    return value


def parse_record(rec: Any) -> RawRecord:
    """Одна запись `data.records[]` → RawRecord. ParseError, если нет conversation_id."""
    if not isinstance(rec, dict):
        raise ParseError(f"запись не объект: {type(rec).__name__}")
    cid = _str(rec.get("conversation_id")).strip()
    if not cid:
        raise ParseError("нет conversation_id")
    warnings: list[str] = []

    items = _transcript_items(rec.get("transcript"), warnings)
    transcript = [parse_message(it) for it in items]
    if not transcript:
        warnings.append("пустой транскрипт")
    for i, m in enumerate(transcript, 1):
        if m.speaker == "unknown":
            warnings.append(f"m{i}: неизвестная роль {m.speaker_raw!r}")

    audio_url = rec.get("audio_url")
    audio_url = audio_url.strip() if isinstance(audio_url, str) and audio_url.strip() else None

    channel = _str(rec.get("channel")).strip()
    if not channel:
        channel = "voice call" if audio_url else "agent chat"
        warnings.append(f"нет channel, принято {channel!r}")

    language = _str(rec.get("language")).strip().upper()
    if not language:
        language = "RU"
        warnings.append("нет language, принято 'RU'")

    return RawRecord(
        conversation_id=cid,
        channel=channel,
        language=language,
        transcript=transcript,
        audio_url=audio_url,
        warnings=warnings,
    )


def parse_payload(payload: Any) -> ParsedPayload:
    """Ответ API целиком (или уже `data`, или голый список записей) → ParsedPayload."""
    if isinstance(payload, list):
        return ParsedPayload(group_id=None, total_records=len(payload), records=payload)
    if not isinstance(payload, dict):
        raise ParseError(f"ответ не объект: {type(payload).__name__}")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        data = {}
    records = data.get("records") or []
    if not isinstance(records, list):
        records = []
    total = data.get("total_records")
    return ParsedPayload(
        group_id=_str(data.get("dataset_group_id")).strip() or None,
        total_records=total if isinstance(total, int) else None,
        records=records,
        error=_str(payload.get("error")),
        is_success=payload.get("isSuccess", True) is not False,
    )
