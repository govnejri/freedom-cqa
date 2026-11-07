"""Модель конфигурации чек-листа. Источник — YAML (config/checklists/*.yaml), в БД и API — канонический JSON.

Никакого кода под конкретный критерий: всё поведение задаётся этими полями.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

CriterionType = Literal["yes_no", "scale", "flag"]


class ScaleStep(BaseModel):
    value: int
    text: str


class Example(BaseModel):
    verdict: str
    fragment: str
    why: str


class Criterion(BaseModel):
    id: str
    title: str
    section: str
    type: CriterionType
    na_allowed: bool = False
    weight: float = 0
    scale: list[ScaleStep] | None = None
    definition: str
    na_condition: str | None = None
    check_steps: list[str] = Field(default_factory=list)
    interpretations: list[str] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)
    evidence_roles: list[str] = Field(default_factory=lambda: ["operator"])
    channels: list[str] | None = None        # None — все каналы; иначе для других каналов код ставит N/A
    flag_categories: list[str] = Field(default_factory=list)
    cross_check: bool = False
    enabled: bool = True

    @property
    def top(self) -> int | None:
        return max(s.value for s in self.scale) if self.scale else None

    def allowed_verdicts(self) -> list[str]:
        if self.type == "scale":
            vals = [str(s.value) for s in sorted(self.scale or [], key=lambda s: -s.value)]
        else:
            vals = ["Да", "Нет"]
        if self.na_allowed:
            vals.append("N/A")
        return vals

    @model_validator(mode="after")
    def _check(self) -> "Criterion":
        if self.type == "scale":
            if not self.scale:
                raise ValueError(f"критерий {self.id}: для шкалы нужны ступени")
            vals = [s.value for s in self.scale]
            if len(set(vals)) != len(vals) or max(vals) <= 0:
                raise ValueError(f"критерий {self.id}: ступени шкалы должны быть уникальными, максимум > 0")
        if self.type == "flag" and self.weight != 0:
            raise ValueError(f"критерий {self.id}: у флага вес 0")
        allowed = set(self.allowed_verdicts())
        for ex in self.examples:
            if ex.verdict not in allowed:
                raise ValueError(f"критерий {self.id}: пример с недопустимым вердиктом {ex.verdict!r}")
        return self


class Section(BaseModel):
    id: str
    title: str


class Scoring(BaseModel):
    max_total: int = 100
    decimals: int = 2
    rounding: Literal["half_up"] = "half_up"


class Signals(BaseModel):
    topics: list[str] = Field(default_factory=list)


class ChecklistConfig(BaseModel):
    id: str
    title: str
    version_note: str = ""
    general_rules: str = ""
    principles: str = ""
    channel_rules: dict[str, str] = Field(default_factory=dict)
    scoring: Scoring = Field(default_factory=Scoring)
    sections: list[Section]
    criteria: list[Criterion]
    signals: Signals = Field(default_factory=Signals)

    def criterion(self, cid: str) -> Criterion:
        for c in self.criteria:
            if c.id == cid:
                return c
        raise KeyError(cid)

    def enabled_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.enabled]

    @model_validator(mode="after")
    def _check(self) -> "ChecklistConfig":
        ids = [c.id for c in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("id критериев должны быть уникальными")
        sec = {s.id for s in self.sections}
        for c in self.criteria:
            if c.section not in sec:
                raise ValueError(f"критерий {c.id}: нет секции {c.section!r}")
        if not any(c.weight > 0 for c in self.criteria if c.enabled):
            raise ValueError("нужен хотя бы один критерий с весом")
        return self
