"""Экспорт результатов: CSV в формате сравнения с эталоном и полный JSON.

CSV: id,language,channel,c01..cNN,total,gross,gross_categories. id = external_id (conversation_id организаторов).
По умолчанию — исходная автооценка (её сверяют с эталоном); final=True — с учётом решений по апелляциям.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from cqa.checklist.model import ChecklistConfig


def _cfg_for(con, version_id: int) -> ChecklistConfig:
    from cqa.checklist.store import get_version
    return get_version(con, version_id)[0]


def _rows(con: sqlite3.Connection, group: str | None, final: bool, run_id: int | None = None):
    if run_id is not None:   # оценки конкретного прогона (эксперименты, пробный прогон)
        q = ("SELECT c.uid, c.external_id, c.language, c.channel, e.* FROM evaluations e"
             " JOIN conversations c ON c.uid = e.uid WHERE e.run_id=?")
        args: tuple = (run_id,)
        if group:
            q += " AND c.group_id=?"
            args += (group,)
    else:
        q = ("SELECT c.uid, c.external_id, c.language, c.channel, e.* FROM conversations c"
             " JOIN evaluations e ON e.id = c.current_evaluation_id")
        args = ()
        if group:
            q += " WHERE c.group_id=?"
            args = (group,)
        else:
            q += " WHERE c.parent_uid IS NULL"
    q += " ORDER BY c.external_id"
    for ev in con.execute(q, args).fetchall():
        crs = {r["criterion_id"]: dict(r) for r in con.execute(
            "SELECT * FROM criterion_results WHERE evaluation_id=?", (ev["id"],))}
        if final:
            for d in con.execute(
                "SELECT a.criterion_id, d.new_verdict, d.reason FROM decisions d JOIN appeals a ON a.id=d.appeal_id"
                " WHERE a.evaluation_id=? AND d.action='change' ORDER BY d.id", (ev["id"],)):
                if d["criterion_id"] in crs:
                    crs[d["criterion_id"]]["verdict"] = d["new_verdict"]
                    crs[d["criterion_id"]]["decision_reason"] = d["reason"]
        yield ev, crs


def export_csv(con: sqlite3.Connection, group: str | None = None, final: bool = False, run_id: int | None = None) -> str:
    out = io.StringIO()
    rows = list(_rows(con, group, final, run_id))
    crit_ids: list[str] = []
    for ev, _ in rows:
        for c in _cfg_for(con, ev["checklist_version_id"]).enabled_criteria():
            if c.id not in crit_ids:
                crit_ids.append(c.id)
    cols = ["id", "language", "channel", *[f"c{cid}" for cid in crit_ids], "total", "gross", "gross_categories"]
    w = csv.writer(out)
    w.writerow(cols)
    for ev, crs in rows:
        total = ev["final_total"] if final else ev["total"]
        score = json.loads((ev["final_score_json"] if final else ev["score_json"]) or "null") or {}
        w.writerow([
            ev["external_id"], ev["language"], ev["channel"],
            *[(crs.get(cid) or {}).get("verdict") or "" for cid in crit_ids],
            "" if total is None else f"{total:.2f}",
            "Да" if score.get("gross") else "Нет",
            "; ".join(score.get("gross_categories") or []),
        ])
    return "﻿" + out.getvalue()


def export_json(con: sqlite3.Connection, group: str | None = None, final: bool = False) -> list[dict]:
    result = []
    for ev, crs in _rows(con, group, final):
        score = json.loads((ev["final_score_json"] if final else ev["score_json"]) or "null")
        result.append({
            "id": ev["external_id"], "uid": ev["uid"], "language": ev["language"], "channel": ev["channel"],
            "evaluation_id": ev["id"], "model": ev["model"], "prompt_version": ev["prompt_version"],
            "checklist_version_id": ev["checklist_version_id"],
            "total": ev["final_total"] if final else ev["total"],
            "score": score,
            "criteria": [
                {
                    "criterion_id": cid, "verdict": r.get("verdict"), "category": r.get("category"),
                    "explanation": r.get("explanation"), "evidence": json.loads(r.get("evidence_json") or "[]"),
                    "checked_range": json.loads(r.get("checked_range_json") or "null"),
                    "how_to_fix": r.get("how_to_fix"), "needs_review": bool(r.get("needs_review")),
                    **({"decision_reason": r["decision_reason"]} if "decision_reason" in r else {}),
                }
                for cid, r in sorted(crs.items())
            ],
            "cost_usd": ev["cost_usd"],
        })
    return result
