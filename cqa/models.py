"""Доменные модели — общий контракт между модулями (ingest, core, api).

Менять поля только согласованно: на эти типы опираются все модули.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Speaker = Literal["client", "operator", "bot", "system", "unknown"]
Segment = Literal["bot", "waiting", "operator", "hold"]
Channel = str  # "agent chat" | "voice call" (терпимо к новым значениям)


class Message(BaseModel):
    idx: int                         # 1-based порядковый номер в исходном транскрипте
    mid: str                         # "m12" — ID для ссылок
    speaker: Speaker
    text: str
    ts_raw: str                      # исходная метка времени без изменений
    offset_sec: float | None = None  # секунды от первой реплики
    audio_sec: float | None = None   # секунда в mp3 (звонки), None — вне записи
    segment: Segment = "operator"
    recorded: bool = True            # реплика попала в запись (звонки)
    after_end: bool = False          # реплика после события завершения диалога
    event_type: str | None = None    # тип системного события (config/events.yaml) или "unknown"
    dup_of: str | None = None        # mid оригинала, если это дубль (тот же спикер, текст и время)


class Wait(BaseModel):
    after_mid: str                   # реплика клиента, после которой пауза
    operator_mid: str                # ответ оператора после паузы
    seconds: float
    prev_operator_mid: str | None    # последняя реплика оператора перед паузой (могла предупреждать)


class Ending(BaseModel):
    type: str | None                 # operator_closed | connection_lost | client_left | client_ended_call | None
    event_mid: str | None
    last_operator_mid: str | None
    last_client_mid: str | None
    abrupt: bool = False             # оборвался раньше, чем оператор мог завершить
    seconds_since_last_client: float | None = None


class Facts(BaseModel):
    channel: str
    language: str
    first_operator_mid: str | None
    escalation_mid: str | None
    operator_joined_mid: str | None
    first_reaction_sec: float | None          # от эскалации до первой реплики оператора (не оценивается)
    client_msgs_before_operator: list[str] = Field(default_factory=list)   # mid реплик клиента в bot/waiting
    waits: list[Wait] = Field(default_factory=list)                          # паузы оператора > 60 с
    id_events: list[str] = Field(default_factory=list)                       # mid системных подтверждений личности
    hold_range: tuple[str, str] | None = None                                # (первый, последний) mid удержания
    ending: Ending
    after_end_mids: list[str] = Field(default_factory=list)
    unknown_events: list[str] = Field(default_factory=list)


class Conversation(BaseModel):
    uid: str                          # "{group_id}:{conversation_id}"
    group_id: str
    external_id: str                  # conversation_id из API
    channel: Channel
    language: str                     # RU | KZ | EN
    messages: list[Message]
    facts: Facts | None = None
    operator_name: str | None = None
    has_audio: bool = False
    content_hash: str = ""

    def by_mid(self) -> dict[str, Message]:
        return {m.mid: m for m in self.messages}


# ---------- результаты оценки ----------

class Evidence(BaseModel):
    id: str
    quote: str
    ok: bool = True                  # прошла проверку валидатором
    repaired: bool = False           # ID исправлен валидатором (цитата найдена в другой реплике)


class CriterionVerdict(BaseModel):
    criterion_id: str
    verdict: str                     # "Да" | "Нет" | "N/A" | ступень шкалы ("20") | для flag: "Да"/"Нет"
    category: str = ""               # для flag
    evidence: list[Evidence] = Field(default_factory=list)
    checked_range: tuple[str, str] | None = None
    reasoning: str = ""
    explanation: str = ""
    how_to_fix: str = ""
    needs_review: bool = False
    status: Literal["done", "failed"] = "done"
    error: str | None = None


class CriterionScore(BaseModel):
    criterion_id: str
    verdict: str
    applicable: bool
    weight: float                    # исходный вес
    effective_weight: float          # вес после перераспределения N/A (сумма по применимым = 100)
    raw_points: float                # баллы до перераспределения: вес × ступень / верхняя ступень
    points: float                    # баллы после перераспределения
    max_points: float                # = effective_weight


class SectionScore(BaseModel):
    section_id: str
    title: str
    points: float
    max_points: float


class ScoreResult(BaseModel):
    total: float | None              # None — нет применимых критериев с весом
    criteria: list[CriterionScore]
    sections: list[SectionScore]
    gross: bool                      # хотя бы один flag-критерий = «Да»
    gross_categories: list[str] = Field(default_factory=list)
