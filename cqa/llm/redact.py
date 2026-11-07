"""Маскирование секретов в логах и журнале: подписи ссылок (sig=), ключи API.

install() вешает фильтр на корневой логгер и его обработчики и приглушает болтливые HTTP-логгеры.
Фильтр на логгере срабатывает только для его собственных записей, поэтому он ставится и на обработчики:
записи дочерних логгеров проходят через обработчики корневого.
"""
from __future__ import annotations

import logging
import re

MASK = "***"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # подпись ссылок организаторов и audio_url
    (re.compile(r"(?i)\b(sig=)[^&\s\"'<>]+"), r"\1" + MASK),
    # key=…, api_key=…, x-goog-api-key=…
    (re.compile(r"(?i)\b([\w-]*key=)[^&\s\"'<>]+"), r"\1" + MASK),
    # заголовки и JSON: "x-goog-api-key": "…", Authorization: Bearer …
    (re.compile(r"(?i)(x-goog-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"), r"\1" + MASK),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + MASK),
    # ключи Google AI Studio и OAuth-токены
    (re.compile(r"AIza[0-9A-Za-z_-]{10,}"), "AIza" + MASK),
    (re.compile(r"\bAQ\.[0-9A-Za-z_.-]{6,}"), "AQ." + MASK),
    # ключи OpenAI-совместимых сервисов
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-" + MASK),
]

_QUIET = ("httpx", "httpcore", "httpx2", "httpcore2", "google_genai", "google.genai", "openai", "urllib3")


def redact(text: str) -> str:
    """Строка с замаскированными секретами."""
    if not text:
        return text
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class RedactFilter(logging.Filter):
    """Переписывает сообщение записи, если в нём есть секрет. Запись не отбрасывает."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — кривые args не должны ронять логирование
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg = clean
            record.args = None
        if record.exc_info and not record.exc_text:
            # трассировка исключения тоже может содержать ссылку
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_FILTER = RedactFilter()


def install() -> None:
    """Идемпотентно: фильтр на корневой логгер и все его обработчики; HTTP-логгеры → WARNING."""
    root = logging.getLogger()
    if _FILTER not in root.filters:
        root.addFilter(_FILTER)
    for h in root.handlers:
        if _FILTER not in h.filters:
            h.addFilter(_FILTER)
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
