"""Экспорт в формат сдачи организаторам — results/<набор>/results.json, «Формат JSON 1.0».

Исходная автооценка (без решений по апелляциям). Одна запись на каждый диалог группы, включая
неоценённые — они идут со status="error", без подмены нулём. evidence_turns — номера реплик по исходному
массиву транскрипта с 1: у нас mid = m{idx} по исходному порядку, без перенумерации.
"""
from __future__ import annotations

import json
import re
import sqlite3

SCHEMA_VERSION = "1.0"

# Наш id критерия → код организаторов. Порядок — как в чек-листе.
CODES = {
    "01": "contact", "02": "discovery", "03": "identification", "04": "consultation",
    "05": "empathy", "06": "dialogue", "07": "closure", "08": "critical",
}
ALLOWED = {
    "contact": {True, False}, "discovery": {True, False, None}, "identification": {True, False, None},
    "consultation": {0, 5, 10, 15, 20, None}, "empathy": {True, False}, "dialogue": {0, 4, 7, 10},
    "closure": {True, False, None}, "critical": {True, False},
}
SCALES = {"consultation", "dialogue"}


def _value(verdict: str | None, ctype: str):
    if verdict is None or verdict == "N/A":
        return None
    if ctype == "scale":
        return int(float(verdict))
    return verdict == "Да"


_MID_RE = re.compile(r"\bm(\d+)(?:\s*[–—-]\s*m?(\d+))?")


def _turns(evidence_json: str | None, text: str | None = None, n_messages: int | None = None) -> list[int]:
    """Номера реплик из цитат-доказательств. Если цитат нет (N/A, «нарушений нет»), берём реплики,
    которые модель назвала в объяснении как проверенные («проверены m10–m14»)."""
    out: set[int] = set()
    for e in json.loads(evidence_json or "[]"):
        mid = str(e.get("id") or e.get("mid") or "")
        if mid.startswith("m") and mid[1:].isdigit():
            out.add(int(mid[1:]))
    if not out and text:
        for a, b in _MID_RE.findall(text):
            lo, hi = int(a), int(b or a)
            if lo <= hi and hi - lo <= 40:
                out.update(range(lo, hi + 1))
    if n_messages:
        out = {t for t in out if 1 <= t <= n_messages}
    return sorted(out)


def _error(cid: str, msg: str) -> dict:
    return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": msg}


def build_results(con: sqlite3.Connection, group: str, team: str, mode: str = "external_api") -> dict:
    from cqa.checklist.store import get_version

    convs = con.execute(
        "SELECT uid, external_id, current_evaluation_id FROM conversations"
        " WHERE group_id=? AND parent_uid IS NULL ORDER BY external_id", (group,)).fetchall()
    results, models = [], []
    cfg_cache: dict[int, object] = {}
    for c in convs:
        cid = c["external_id"]
        if not c["current_evaluation_id"]:
            results.append(_error(cid, "Диалог не оценён: нет результата оценки."))
            continue
        ev = con.execute("SELECT * FROM evaluations WHERE id=?", (c["current_evaluation_id"],)).fetchone()
        vid = ev["checklist_version_id"]
        if vid not in cfg_cache:
            cfg_cache[vid] = get_version(con, vid)[0]
        cfg = cfg_cache[vid]
        crs = {r["criterion_id"]: r for r in con.execute(
            "SELECT * FROM criterion_results WHERE evaluation_id=?", (ev["id"],))}
        failed = [cr.id for cr in cfg.enabled_criteria() if (crs.get(cr.id) is None or crs[cr.id]["status"] != "done")]
        if failed or ev["total"] is None:
            why = f"не получен ответ модели по критериям {', '.join(failed)}" if failed else "итог не посчитан"
            results.append(_error(cid, f"Не удалось оценить: {why}."))
            continue
        n_msgs = con.execute("SELECT count(*) FROM messages WHERE uid=?", (c["uid"],)).fetchone()[0] or None
        criteria = []
        for cr in cfg.enabled_criteria():
            r = crs[cr.id]
            rationale = (r["explanation"] or r["reasoning"] or "").strip()
            criteria.append({
                "criterion_id": CODES.get(cr.id, cr.id),
                "value": _value(r["verdict"], cr.type),
                "rationale": rationale,
                "evidence_turns": _turns(r["evidence_json"], rationale, n_msgs),
            })
        score = json.loads(ev["score_json"] or "{}")
        crit = next((x["value"] for x in criteria if x["criterion_id"] == "critical"), bool(score.get("gross")))
        results.append({
            "conversation_id": cid, "status": "ok", "criteria": criteria,
            "score": {"final_score": round(float(ev["total"]), 2), "critical_flag": bool(crit)},
        })
        if ev["model"] and ev["model"] not in models:
            models.append(ev["model"])
    return {"schema_version": SCHEMA_VERSION, "team_name": team,
            "model": {"mode": mode, "name": ", ".join(models) or "—"}, "results": results}


def validate(doc: dict, expected_ids: list[str] | None = None) -> list[str]:
    """Проверки из приложения к гайду. Пустой список — всё в порядке."""
    errs: list[str] = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errs.append("schema_version != 1.0")
    if not doc.get("team_name"):
        errs.append("пустой team_name")
    if doc.get("model", {}).get("mode") not in ("local", "external_api", "hybrid"):
        errs.append("model.mode не из local / external_api / hybrid")
    ids = [r.get("conversation_id") for r in doc.get("results", [])]
    if len(ids) != len(set(ids)):
        errs.append("повторяющиеся conversation_id")
    if expected_ids is not None and sorted(ids) != sorted(expected_ids):
        errs.append(f"ID не совпадают с набором: лишние {sorted(set(ids) - set(expected_ids))}, "
                    f"нет {sorted(set(expected_ids) - set(ids))}")
    for r in doc.get("results", []):
        cid = r.get("conversation_id")
        if r.get("status") == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not r.get("error"):
                errs.append(f"{cid}: запись с ошибкой должна иметь criteria=[], score=null и error")
            continue
        if r.get("status") != "ok":
            errs.append(f"{cid}: status не ok/error")
            continue
        codes = [x["criterion_id"] for x in r["criteria"]]
        if sorted(codes) != sorted(CODES.values()):
            errs.append(f"{cid}: критерии {codes} — нужны все 8, каждый один раз")
        for x in r["criteria"]:
            allowed = ALLOWED.get(x["criterion_id"])
            v = x["value"]
            if allowed is not None:
                is_scale = x["criterion_id"] in SCALES
                type_ok = v is None or (isinstance(v, int) and not isinstance(v, bool) if is_scale else isinstance(v, bool))
                if not type_ok or v not in allowed:
                    errs.append(f"{cid}/{x['criterion_id']}: недопустимое значение {v!r}")
            if not isinstance(x.get("evidence_turns"), list) or not all(isinstance(t, int) and t >= 1 for t in x["evidence_turns"]):
                errs.append(f"{cid}/{x['criterion_id']}: evidence_turns должен быть списком номеров с 1")
        s = r.get("score") or {}
        fs = s.get("final_score")
        if not isinstance(fs, (int, float)) or not (0 <= fs <= 100) or round(fs, 2) != fs:
            errs.append(f"{cid}: final_score {fs!r} вне 0–100 или не округлён до сотых")
        crit = next((x["value"] for x in r["criteria"] if x["criterion_id"] == "critical"), None)
        if s.get("critical_flag") is not crit:
            errs.append(f"{cid}: critical_flag не совпадает с critical")
    return errs
