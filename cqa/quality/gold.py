"""Эталон: импорт eval/gold.csv в таблицу gold_labels и деление на tune / holdout по семьям сценариев.

Отложенные семьи (eval/holdout.txt) не используются для подгонки промптов и примеров чек-листа:
приёмка правки — «не хуже на отложенных».
"""
from __future__ import annotations

import csv
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

from cqa.checklist.model import ChecklistConfig
from cqa.quality.compare import column, options, read_id_set
from cqa.settings import ROOT

HOLDOUT_PATH = ROOT / "eval" / "holdout.txt"
GOLD_PATH = ROOT / "eval" / "gold.csv"
SPLITS = ("all", "tune", "holdout")
_CRIT_COL = re.compile(r"^c(\d+)$")


@contextmanager
def _tx(con: sqlite3.Connection):
    """Транзакция, если вызывающий код ещё не открыл свою."""
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def _criterion_columns(fieldnames: Iterable[str], cfg: ChecklistConfig | None) -> dict[str, str]:
    """Колонка CSV → id критерия. С конфигом — по его критериям, без — все колонки вида cNN."""
    names = list(fieldnames)
    if cfg is not None:
        return {column(c): c.id for c in cfg.criteria if column(c) in names}
    return {n: m.group(1) for n in names if (m := _CRIT_COL.match(n))}


def import_gold(
    con: sqlite3.Connection,
    path: str | Path = GOLD_PATH,
    source: str = "gold",
    cfg: ChecklistConfig | None = None,
) -> dict[str, int]:
    """CSV эталона → gold_labels. Строки этого source заменяются целиком. Пустые ячейки пропускаются.

    Возвращает {"dialogs": N, "labels": M}.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = _criterion_columns(reader.fieldnames or [], cfg)
        if not cols:
            raise ValueError(f"{path}: нет колонок критериев (c01, c02, …)")
        rows = [r for r in reader if (r.get("id") or "").strip()]
    labels = []
    for r in rows:
        ext = r["id"].strip()
        family = (r.get("family") or "").strip() or None
        for col, cid in cols.items():
            opts = options(r.get(col))
            if opts:
                labels.append((ext, cid, source, "|".join(opts), family))
    with _tx(con):
        con.execute("DELETE FROM gold_labels WHERE source=?", (source,))
        con.executemany(
            "INSERT OR REPLACE INTO gold_labels(external_id, criterion_id, source, options, family) VALUES (?,?,?,?,?)",
            labels,
        )
    return {"dialogs": len({lab[0] for lab in labels}), "labels": len(labels)}


def gold_rows(con: sqlite3.Connection, source: str = "gold") -> list[dict[str, Any]]:
    """gold_labels → строки в формате CSV сравнения: {id, family, c01: "Да|Нет", …}, по id."""
    out: dict[str, dict[str, Any]] = {}
    for r in con.execute(
        "SELECT external_id, criterion_id, options, family FROM gold_labels WHERE source=? ORDER BY external_id, criterion_id",
        (source,),
    ):
        row = out.setdefault(r["external_id"], {"id": r["external_id"], "family": r["family"] or ""})
        row[column(r["criterion_id"])] = r["options"]
    return list(out.values())


def load_holdout(path: str | Path = HOLDOUT_PATH) -> set[str]:
    """Отложенные семьи сценариев; нет файла — пустое множество."""
    p = Path(path)
    return read_id_set(p) if p.exists() else set()


def split_ids(
    gold_rows: Iterable[Mapping[str, Any]],
    split: str = "all",
    holdout: set[str] | None = None,
) -> list[str]:
    """id диалогов эталона для части: all | tune (семьи не из holdout) | holdout.

    Строка — dict с id (или external_id) и family; строки gold_labels можно передавать как есть.
    Диалог без семьи относится к tune.
    """
    if split not in SPLITS:
        raise ValueError(f"split должен быть одним из {SPLITS}, получено {split!r}")
    hold = load_holdout() if holdout is None else holdout
    out: dict[str, None] = {}
    for r in gold_rows:
        i = r.get("id") or r.get("external_id")
        if not i:
            continue
        in_hold = (r.get("family") or "") in hold
        if split == "all" or (split == "holdout") == in_hold:
            out[str(i)] = None
    return sorted(out)
