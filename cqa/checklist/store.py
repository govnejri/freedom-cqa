"""Версии чек-листа в БД (таблицы checklists, checklist_versions).

Версия неизменяема: конфиг строки не меняется никогда, каждое сохранение — новая строка с номером max + 1.
Меняются только статус (draft → published → frozen) и указатели checklists.active_version_id / frozen_version_id.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cqa.checklist.io import config_hash, criteria_hashes, from_json, load_yaml, to_canonical_json
from cqa.checklist.model import ChecklistConfig
from cqa.db.conn import now_iso

DEFAULT_CHECKLIST = "freedom-cqa"


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


def _meta(row: sqlite3.Row, active_id: int | None, frozen_id: int | None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "checklist_id": row["checklist_id"],
        "version": row["version"],
        "status": row["status"],
        "config_hash": row["config_hash"],
        "criteria_hashes": json.loads(row["criteria_hashes_json"]),
        "parent_id": row["parent_id"],
        "comment": row["comment"],
        "created_at": row["created_at"],
        "is_active": row["id"] == active_id,
        "is_frozen": row["id"] == frozen_id,
    }


def _pointers(con: sqlite3.Connection, checklist_id: str) -> tuple[int | None, int | None]:
    r = con.execute("SELECT active_version_id, frozen_version_id FROM checklists WHERE id=?", (checklist_id,)).fetchone()
    return (r["active_version_id"], r["frozen_version_id"]) if r else (None, None)


def _row(con: sqlite3.Connection, version_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM checklist_versions WHERE id=?", (version_id,)).fetchone()
    if row is None:
        raise KeyError(f"нет версии чек-листа id={version_id}")
    return row


# ---------------------------------------------------------------- запись

def create_version(
    con: sqlite3.Connection,
    checklist_id: str,
    cfg: ChecklistConfig,
    parent_id: int | None = None,
    comment: str | None = None,
    status: str = "draft",
) -> int:
    """Новая неизменяемая версия (номер = max + 1). Возвращает id строки checklist_versions."""
    if cfg.id != checklist_id:
        raise ValueError(f"id конфига {cfg.id!r} не совпадает с чек-листом {checklist_id!r}")
    if status not in ("draft", "published"):
        raise ValueError(f"недопустимый статус новой версии: {status!r}")
    with _tx(con):
        con.execute("INSERT OR IGNORE INTO checklists(id, title) VALUES (?, ?)", (checklist_id, cfg.title))
        if parent_id is not None:
            p = _row(con, parent_id)
            if p["checklist_id"] != checklist_id:
                raise ValueError(f"родительская версия {parent_id} относится к другому чек-листу")
        n = con.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM checklist_versions WHERE checklist_id=?",
                        (checklist_id,)).fetchone()[0]
        cur = con.execute(
            "INSERT INTO checklist_versions(checklist_id, version, status, config_json, config_hash,"
            " criteria_hashes_json, parent_id, comment, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (checklist_id, n, status, to_canonical_json(cfg), config_hash(cfg),
             json.dumps(criteria_hashes(cfg), sort_keys=True), parent_id, comment, now_iso()),
        )
        return int(cur.lastrowid)


def seed(con: sqlite3.Connection, path: str | Path) -> int:
    """Первый старт: YAML → версия 1, published, активная. Если чек-лист уже есть — ничего не делает.

    Возвращает id активной версии (или последней, если активной нет).
    """
    cfg = load_yaml(path)
    with _tx(con):
        row = con.execute("SELECT active_version_id FROM checklists WHERE id=?", (cfg.id,)).fetchone()
        if row is not None:
            if row["active_version_id"]:
                return int(row["active_version_id"])
            last = con.execute("SELECT MAX(id) FROM checklist_versions WHERE checklist_id=?", (cfg.id,)).fetchone()[0]
            if last:
                return int(last)
        vid = create_version(con, cfg.id, cfg, comment=f"импорт {Path(path).name}", status="published")
        con.execute("UPDATE checklists SET active_version_id=?, title=? WHERE id=?", (vid, cfg.title, cfg.id))
        return vid


def publish(con: sqlite3.Connection, version_id: int) -> None:
    """Сделать версию активной. Статус draft → published (frozen остаётся frozen)."""
    with _tx(con):
        row = _row(con, version_id)
        if row["status"] == "draft":
            con.execute("UPDATE checklist_versions SET status='published' WHERE id=?", (version_id,))
        cfg = from_json(row["config_json"])
        con.execute("UPDATE checklists SET active_version_id=?, title=? WHERE id=?",
                    (version_id, cfg.title, row["checklist_id"]))


def freeze(con: sqlite3.Connection, version_id: int) -> None:
    """Заморозить версию для контрольного этапа: статус frozen и checklists.frozen_version_id.

    Активную версию не меняет.
    """
    with _tx(con):
        row = _row(con, version_id)
        con.execute("UPDATE checklist_versions SET status='frozen' WHERE id=?", (version_id,))
        con.execute("UPDATE checklists SET frozen_version_id=? WHERE id=?", (version_id, row["checklist_id"]))


# ---------------------------------------------------------------- чтение

def get_version(con: sqlite3.Connection, version_id: int) -> tuple[ChecklistConfig, dict[str, Any]]:
    row = _row(con, version_id)
    active, frozen = _pointers(con, row["checklist_id"])
    return from_json(row["config_json"]), _meta(row, active, frozen)


def get_active(con: sqlite3.Connection, checklist_id: str = DEFAULT_CHECKLIST) -> tuple[ChecklistConfig, dict[str, Any]]:
    active, _ = _pointers(con, checklist_id)
    if not active:
        raise KeyError(f"у чек-листа {checklist_id!r} нет активной версии (нужен seed)")
    return get_version(con, active)


def find_version(con: sqlite3.Connection, checklist_id: str, version: int) -> int:
    """id строки по номеру версии."""
    r = con.execute("SELECT id FROM checklist_versions WHERE checklist_id=? AND version=?",
                    (checklist_id, version)).fetchone()
    if r is None:
        raise KeyError(f"у чек-листа {checklist_id!r} нет версии {version}")
    return int(r["id"])


def list_versions(con: sqlite3.Connection, checklist_id: str = DEFAULT_CHECKLIST) -> list[dict[str, Any]]:
    """Метаданные всех версий (без конфига), по возрастанию номера."""
    active, frozen = _pointers(con, checklist_id)
    rows = con.execute("SELECT * FROM checklist_versions WHERE checklist_id=? ORDER BY version", (checklist_id,))
    return [_meta(r, active, frozen) for r in rows]


# ---------------------------------------------------------------- сравнение версий

def diff(cfg_a: ChecklistConfig, cfg_b: ChecklistConfig) -> list[dict[str, Any]]:
    """Что изменилось от a к b.

    Элемент: {scope: "config"|"criterion", id, change: "added"|"removed"|"changed", fields: [...],
    before: {...}|None, after: {...}|None}. Для scope="config" id — имя поля верхнего уровня
    (general_rules, principles, channel_rules, scoring, sections, …). Порядок: сначала общие поля,
    затем критерии в порядке конфига b (удалённые — в конце, в порядке a).
    """
    out: list[dict[str, Any]] = []
    da = cfg_a.model_dump(mode="json")
    db = cfg_b.model_dump(mode="json")
    for key in db:
        if key == "criteria":
            continue
        if da.get(key) != db.get(key):
            out.append({"scope": "config", "id": key, "change": "changed", "fields": [key],
                        "before": {key: da.get(key)}, "after": {key: db.get(key)}})

    ca = {c["id"]: c for c in da["criteria"]}
    cb = {c["id"]: c for c in db["criteria"]}
    for cid, b in cb.items():
        a = ca.get(cid)
        if a is None:
            out.append({"scope": "criterion", "id": cid, "change": "added", "fields": list(b),
                        "before": None, "after": b})
            continue
        fields = [k for k in b if a.get(k) != b.get(k)]
        if fields:
            out.append({"scope": "criterion", "id": cid, "change": "changed", "fields": fields,
                        "before": {k: a.get(k) for k in fields}, "after": {k: b.get(k) for k in fields}})
    for cid, a in ca.items():
        if cid not in cb:
            out.append({"scope": "criterion", "id": cid, "change": "removed", "fields": list(a),
                        "before": a, "after": None})
    return out


def sync_yaml(con: sqlite3.Connection, path: str | Path, comment: str | None = None) -> tuple[int, bool]:
    """YAML → активная версия, если конфиг изменился. Возвращает (id активной версии, создана ли новая).

    Нужно для цикла правок промптов и трактовок: правим YAML → `cqa eval` сам заводит новую версию.
    """
    seed(con, path)
    cfg = load_yaml(path)
    h = config_hash(cfg)
    _active_cfg, meta = get_active(con, cfg.id)
    key = f"yaml_hash:{cfg.id}"
    row = con.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    stored = json.loads(row[0]) if row else None
    # Импортируем, только если изменился сам YAML. Версию, опубликованную в интерфейсе, перезапуск
    # сервера не трогает (раньше сравнение шло с активной версией и откатывало правки из UI).
    if stored == h or (stored is None and meta["config_hash"] == h):
        if stored is None:
            con.execute("INSERT OR REPLACE INTO settings(key, value_json) VALUES (?, ?)", (key, json.dumps(h)))
        return int(meta["id"]), False
    vid = create_version(con, cfg.id, cfg, parent_id=int(meta["id"]),
                         comment=comment or f"импорт {Path(path).name}", status="published")
    publish(con, vid)
    con.execute("INSERT OR REPLACE INTO settings(key, value_json) VALUES (?, ?)", (key, json.dumps(h)))
    return vid, True
