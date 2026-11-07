"""Нормализация записи API → `cqa.models.Conversation` (docs/implementation.md §1).

- `mid = m{idx}` по исходному порядку, без перенумерации;
- `offset_sec` от первой реплики; `audio_sec`/`recorded` для звонков с учётом пауз записи;
- дубли (`dup_of`), типы системных событий (`config/events.yaml`);
- участки bot / waiting / operator / hold, `after_end`;
- `operator_name` из первой реплики оператора, `content_hash`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml

from cqa.ingest.parse import RawRecord
from cqa.models import Conversation, Message

NORMALIZER_VERSION = "1"

# Имена типов из config/events.yaml, на которые опирается разметка.
HOLD_START = "hold_start"
HOLD_END = "hold_end"
REC_STARTED = "recording_started"
REC_PAUSED = "recording_paused"
REC_RESUMED = "recording_resumed"
OPERATOR_JOINED = "operator_joined"
UNKNOWN_EVENT = "unknown"

# Значения по умолчанию для необязательных ключей events.yaml.
# В звонках роль эскалации играет «Клиент переведен от агента на представителя службы поддержки» (transfer).
DEFAULT_ESCALATION_TYPES = ("escalated", "transfer")
DEFAULT_IDENTITY_TYPES = ("identity_verified",)


# ---------------------------------------------------------------- события

def norm_event_text(text: str) -> str:
    """casefold, схлопнутые пробелы, без конечной точки."""
    s = " ".join(text.split()).casefold()
    return re.sub(r"[\s.]+$", "", s)


@dataclass
class EventRules:
    exact: dict[str, str]                                  # нормализованный текст → тип
    regex: list[tuple[re.Pattern, str]]
    ending_types: tuple[str, ...]
    abrupt_ending_types: tuple[str, ...]
    escalation_types: tuple[str, ...] = DEFAULT_ESCALATION_TYPES
    identity_types: tuple[str, ...] = DEFAULT_IDENTITY_TYPES
    types: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, cfg: dict) -> EventRules:
        exact: dict[str, str] = {}
        regex: list[tuple[re.Pattern, str]] = []
        for etype, spec in (cfg.get("events") or {}).items():
            spec = spec or {}
            for t in spec.get("exact") or []:
                exact.setdefault(norm_event_text(str(t)), etype)
            for r in spec.get("regex") or []:
                regex.append((re.compile(norm_event_text(str(r))), etype))
        return cls(
            exact=exact,
            regex=regex,
            ending_types=tuple(cfg.get("ending_types") or ()),
            abrupt_ending_types=tuple(cfg.get("abrupt_ending_types") or ()),
            escalation_types=tuple(cfg.get("escalation_types") or DEFAULT_ESCALATION_TYPES),
            identity_types=tuple(cfg.get("identity_types") or DEFAULT_IDENTITY_TYPES),
            types=tuple((cfg.get("events") or {}).keys()),
        )

    def classify(self, text: str) -> str:
        n = norm_event_text(text)
        if n in self.exact:
            return self.exact[n]
        for rx, etype in self.regex:
            if rx.search(n):
                return etype
        return UNKNOWN_EVENT


def events_path() -> Path:
    from cqa.settings import get_settings
    return Path(get_settings().config_dir) / "events.yaml"


@lru_cache(maxsize=8)
def _load_rules(path: str, mtime: float) -> EventRules:
    with open(path, encoding="utf-8") as f:
        return EventRules.from_dict(yaml.safe_load(f) or {})


def load_event_rules(path: Path | None = None) -> EventRules:
    p = Path(path or events_path())
    return _load_rules(str(p), p.stat().st_mtime)


# ---------------------------------------------------------------- время

_CLOCK = re.compile(r"^(?:(\d+):)?(\d+):(\d{1,2}(?:[.,]\d+)?)$")


def parse_ts(ts: str) -> float | None:
    """Метка → абсолютные секунды. Чат: `MM:SS` (или `H:MM:SS`), звонок: ISO 8601. None — не разобрали."""
    s = (ts or "").strip()
    if not s:
        return None
    m = _CLOCK.match(s)
    if m:
        h, mm, ss = m.groups()
        return int(h or 0) * 3600 + int(mm) * 60 + float(ss.replace(",", "."))
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------- имя оператора

_HIDDEN = re.compile(r"^\[[^\]]*\]")
_NAME_PATTERNS = [
    (re.compile(r"(?:меня\s+зовут|менің\s+атым|my\s+name\s+is)\s+(?P<name>\S+)", re.I), False),
    (re.compile(r"(?<![\w'’])(?:i['’]m|i\s+am)\s+(?P<name>\S+)", re.I), False),
    (re.compile(r"(?<!\w)я\s+(?P<name>\S+)", re.I), False),
    (re.compile(r"(?<!\w)мен\s+(?P<name>\S+)", re.I), True),
]
_KZ_COPULA = re.compile(r"(?:мын|мін|пын|пін|бын|бін)$")
_NOT_NAMES = frozenset({
    "вам", "вас", "вами", "ваш", "ваша", "вашу", "вашего", "вашей", "ваши", "вашим",
    "сіз", "сізге", "сізді", "сіздің", "сізбен", "сізден",
})
_PUNCT = ".,!?;:«»\"'()…—–"


def extract_operator_name(text: str) -> str | None:
    """Имя из реплики по шаблонам «меня зовут X», «я X», «I'm X», «мен X», «менің атым X».

    «[Имя скрыто]» и другие скрытые подстановки → None.
    """
    for rx, kz in _NAME_PATTERNS:
        for m in rx.finditer(text):
            if _HIDDEN.match(text[m.start("name"):]):
                return None
            name = m.group("name").strip(_PUNCT)
            if not name or not name[0].isupper() or name.casefold() in _NOT_NAMES:
                continue
            if not all(ch.isalpha() or ch == "-" for ch in name):
                continue
            if kz and len(name) >= 6:
                name = _KZ_COPULA.sub("", name)
            return name
    return None


# ---------------------------------------------------------------- разметка

@dataclass
class Landmarks:
    """Ключевые позиции (0-based индексы в списке реплик)."""
    first_operator: int | None
    escalation: int | None
    operator_joined: int | None
    ending: int | None


def find_landmarks(messages: list[Message], rules: EventRules) -> Landmarks:
    first_op = next((i for i, m in enumerate(messages) if m.speaker == "operator" and not m.dup_of), None)
    limit = first_op if first_op is not None else len(messages)
    esc = next((i for i, m in enumerate(messages[:limit])
                if m.speaker == "system" and m.event_type in rules.escalation_types), None)
    joined = next((i for i, m in enumerate(messages)
                   if m.speaker == "system" and m.event_type == OPERATOR_JOINED), None)
    ending = next((i for i, m in enumerate(messages)
                   if m.speaker == "system" and m.event_type in rules.ending_types), None)
    return Landmarks(first_operator=first_op, escalation=esc, operator_joined=joined, ending=ending)


def _mark_segments(messages: list[Message], lm: Landmarks) -> None:
    for i, m in enumerate(messages):
        if lm.first_operator is None or i < lm.first_operator:
            m.segment = "waiting" if lm.escalation is not None and i > lm.escalation else "bot"
        else:
            m.segment = "operator"
    in_hold = False
    for m in messages:
        if m.speaker == "system" and m.event_type == HOLD_START:
            in_hold = True
        if in_hold:
            m.segment = "hold"
        if m.speaker == "system" and m.event_type == HOLD_END:
            in_hold = False


# Замер на DEV (cqa audio-probe + silencedetect): после каждого возобновления записи в mp3 на ~5.5 с больше,
# чем даёт разница меток времени. Без поправки переход к реплике после паузы промахивается на 5–6 с.
RESUME_EXTRA_SEC = 5.5


def _mark_recording(messages: list[Message], times: list[float | None]) -> None:
    """Звонок: recorded/audio_sec. Запись от recording_started до recording_paused, затем от recording_resumed."""
    has_start = any(m.speaker == "system" and m.event_type == REC_STARTED for m in messages)
    recording = not has_start
    origin: float | None = None
    paused_total = 0.0
    pause_from: float | None = None
    for m, t in zip(messages, times):
        ev = m.event_type if m.speaker == "system" else None
        if ev == REC_STARTED or (ev == REC_RESUMED and not recording and origin is not None):
            if origin is None:
                origin = t
            elif not recording and pause_from is not None and t is not None:
                paused_total += max(0.0, t - pause_from) - RESUME_EXTRA_SEC
            recording, pause_from = True, None
        if recording and origin is None and t is not None:
            origin = t  # нет recording_started: отсчёт от первой реплики с временем
        if recording and origin is not None and t is not None:
            m.recorded = True
            m.audio_sec = round(max(0.0, t - origin - paused_total), 3)
        else:
            m.recorded = recording  # в записи, но время не разобрано → секунды неизвестны
            m.audio_sec = None
        if ev == REC_PAUSED and recording:
            recording, pause_from = False, t


def content_hash(channel: str, language: str, messages: list[Message]) -> str:
    payload = {
        "channel": channel,
        "language": language,
        "messages": [[m.speaker, m.text, m.ts_raw] for m in messages],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_uid(group_id: str, conversation_id: str) -> str:
    return f"{group_id}:{conversation_id}"


def normalize_record(raw: RawRecord, group_id: str, rules: EventRules | None = None,
                     *, with_facts: bool = True) -> Conversation:
    rules = rules or load_event_rules()
    messages: list[Message] = []
    for idx, rm in enumerate(raw.transcript, 1):
        messages.append(Message(
            idx=idx,
            mid=f"m{idx}",
            speaker=rm.speaker,  # type: ignore[arg-type]
            text=rm.text,
            ts_raw=rm.timestamp,
            event_type=rules.classify(rm.text) if rm.speaker == "system" else None,
        ))

    # дубли: тот же спикер, текст и ts_raw, что у предыдущей реплики
    for prev, m in zip(messages, messages[1:]):
        if m.speaker == prev.speaker and m.text == prev.text and m.ts_raw == prev.ts_raw:
            m.dup_of = prev.dup_of or prev.mid

    times = [parse_ts(m.ts_raw) for m in messages]
    t0 = next((t for t in times if t is not None), None)
    for m, t in zip(messages, times):
        m.offset_sec = round(t - t0, 3) if t is not None and t0 is not None else None

    lm = find_landmarks(messages, rules)
    _mark_segments(messages, lm)
    if lm.ending is not None:
        for m in messages[lm.ending + 1:]:
            m.after_end = True

    is_call = raw.is_call or bool(raw.audio_url)
    if is_call:
        _mark_recording(messages, times)
    # у чатов audio_sec=None, recorded=True — значения по умолчанию

    operator_name = None
    if lm.first_operator is not None:
        operator_name = extract_operator_name(messages[lm.first_operator].text)

    conv = Conversation(
        uid=make_uid(group_id, raw.conversation_id),
        group_id=group_id,
        external_id=raw.conversation_id,
        channel=raw.channel,
        language=raw.language,
        messages=messages,
        operator_name=operator_name,
        has_audio=False,
        content_hash=content_hash(raw.channel, raw.language, messages),
    )
    if with_facts:
        from cqa.ingest.facts import build_facts
        conv.facts = build_facts(conv, rules)
    return conv
