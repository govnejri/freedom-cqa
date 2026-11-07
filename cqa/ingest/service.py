"""Загрузка набора: записи API → нормализация → факты → БД. Отчёт о загрузке без секретов."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cqa.ingest.normalize import UNKNOWN_EVENT, load_event_rules, normalize_record
from cqa.ingest.parse import ParseError, parse_payload, parse_record
from cqa.ingest.store import _atomic, save_conversation, upsert_dataset


@dataclass
class IngestReport:
    group_id: str
    source: str
    records: int = 0                     # записей во входе
    conversations: int = 0               # сохранено диалогов
    messages: int = 0                    # реплик (вместе с дублями)
    calls: int = 0
    with_audio_url: int = 0
    unknown_events: list[dict] = field(default_factory=list)   # {uid, mid, text}
    duplicates: list[dict] = field(default_factory=list)       # {uid, mid, dup_of}
    skipped: list[str] = field(default_factory=list)           # записи, которые нельзя разобрать
    warnings: list[str] = field(default_factory=list)          # "uid: текст"
    uids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.group_id}: {self.conversations} диалогов, {self.messages} реплик, "
                f"неизвестных событий: {len(self.unknown_events)}, дублей: {len(self.duplicates)}")


def ingest_records(con: sqlite3.Connection, group_id: str, records: Iterable[Any], source: str,
                   *, raw_path: str | Path | None = None) -> IngestReport:
    """Нормализовать и сохранить записи `data.records[]` в группу group_id. Запись в datasets."""
    records = list(records)
    rules = load_event_rules()
    report = IngestReport(group_id=group_id, source=source, records=len(records))
    with _atomic(con):
        upsert_dataset(con, group_id, source, len(records), raw_path)
        for n, rec in enumerate(records, 1):
            try:
                raw = parse_record(rec)
            except ParseError as e:
                report.skipped.append(f"запись #{n}: {e}")
                continue
            conv = normalize_record(raw, group_id, rules)
            save_conversation(con, conv, raw.audio_url)

            report.conversations += 1
            report.messages += len(conv.messages)
            report.uids.append(conv.uid)
            if raw.is_call:
                report.calls += 1
            if raw.audio_url:
                report.with_audio_url += 1
            report.warnings.extend(f"{conv.uid}: {w}" for w in raw.warnings)
            for m in conv.messages:
                if m.speaker == "system" and m.event_type == UNKNOWN_EVENT:
                    report.unknown_events.append({"uid": conv.uid, "mid": m.mid, "text": m.text})
                if m.dup_of:
                    report.duplicates.append({"uid": conv.uid, "mid": m.mid, "dup_of": m.dup_of})
    return report


def ingest_payload(con: sqlite3.Connection, payload: Any, source: str, group_id: str | None = None,
                   *, raw_path: str | Path | None = None) -> IngestReport:
    """Ответ API целиком. group_id: явный → dataset_group_id из ответа → settings.cqa_group_id."""
    parsed = parse_payload(payload)
    if not parsed.is_success:
        from cqa.ingest.fetch import redact
        raise ValueError(f"API: isSuccess=false: {redact(parsed.error)[:200]}")
    if not group_id:
        from cqa.settings import get_settings
        group_id = parsed.group_id or get_settings().cqa_group_id
    return ingest_records(con, group_id, parsed.records, source, raw_path=raw_path)
