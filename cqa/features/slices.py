"""Срезы для руководителя (P2): текущие оценки по языку, каналу, оператору или причине обращения.

По каждой группе: диалогов, средний финальный итог, по каждому критерию — доля «Да» (шкала — средняя ступень,
флаг — доля «Да» = нарушений) и quality 0..1, грубых ошибок, «требует проверки», апелляций.
Данные и метрики — cqa/features/operators.py (load_dialogs, criterion_stats): финальные вердикты с учётом
апелляций, без автотестов. Причина обращения — из сигналов (`signals.contact_reason`).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from cqa.checklist.model import ChecklistConfig
from cqa.features.operators import (
    Dialog,
    active_config,
    avg_total,
    criteria_header,
    criterion_stats,
    load_dialogs,
)

BY = ("language", "channel", "operator", "reason")
NO_OPERATOR = "не назначен"
NO_REASON = "нет сигналов"
_STAT_KEYS = ("n", "n_applicable", "n_na", "violations", "quality", "yes_share", "n_yes", "mean_step", "top",
              "avg_points", "lost_points", "loss")


def slice_key(d: Dialog, by: str) -> str:
    if by == "language":
        return d.language
    if by == "channel":
        return d.channel
    if by == "operator":
        return d.operator or NO_OPERATOR
    if by == "reason":
        return d.contact_reason or NO_REASON
    raise ValueError(f"by: ожидается одно из {', '.join(BY)}")


def slice_row(key: str, dialogs: list[Dialog], cfg: ChecklistConfig) -> dict[str, Any]:
    n = len(dialogs)
    gross = sum(d.gross for d in dialogs)
    return {
        "key": key, "n_dialogs": n, "avg_total": avg_total(dialogs),
        "gross_count": gross, "gross_share": round(gross / n, 4) if n else None,
        "needs_review": sum(d.needs_review for d in dialogs),
        "n_appeals": sum(d.n_appeals for d in dialogs),
        "uids": [d.uid for d in dialogs],
        "criteria": {c.id: {k: v for k, v in criterion_stats(dialogs, c).items() if k in _STAT_KEYS}
                     for c in cfg.enabled_criteria()},
    }


def slices(con: sqlite3.Connection, by: str, group: str | None = None,
           cfg: ChecklistConfig | None = None) -> dict[str, Any]:
    """{by, group, criteria: [заголовок], overall: строка по всей выборке, slices: [строки]} — по числу диалогов."""
    if by not in BY:
        raise ValueError(f"by: ожидается одно из {', '.join(BY)}")
    cfg = cfg or active_config(con)
    dialogs = load_dialogs(con, group)
    groups: dict[str, list[Dialog]] = {}
    names: dict[str, str] = {}
    for d in dialogs:
        key = slice_key(d, by)
        norm = key.casefold() if by == "operator" else key
        names.setdefault(norm, key)
        groups.setdefault(norm, []).append(d)
    rows = [slice_row(names[k], v, cfg) for k, v in groups.items()]
    rows.sort(key=lambda r: (-r["n_dialogs"], r["key"].casefold()))
    return {"by": by, "group": group, "criteria": criteria_header(cfg),
            "overall": slice_row("все", dialogs, cfg), "slices": rows}
