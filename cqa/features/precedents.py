"""Прецеденты из апелляций (P2): решение проверяющего → пример в критерии → черновик версии → пробный прогон.

- Строку в `precedents` пишет cqa/appeals/service.decide при make_precedent=True: fragment — реплики-доказательства
  «РОЛЬ: текст / …», verdict — финальный вердикт после решения, why — причина решения.
- precedent_to_version: черновик новой версии от активной; каждый прецедент → `examples` своего критерия
  {verdict, fragment (≤ ~300 символов), why}; precedents.added_in_version_id = новая версия. Активная версия
  не меняется: дальше QA делает пробный прогон (POST /checklist-versions/{vid}/dry-run) и сам публикует.
  Промпт судьи уже выводит examples критерия — правка промпта не нужна.
- similar_dialogs: где проверить новую версию — та же причина обращения (сигналы), тот же исходный вердикт
  критерия, затем тот же язык и канал; без самого диалога и автотестов.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from cqa.checklist.model import ChecklistConfig, Example

FRAGMENT_LIMIT = 300
WHY_LIMIT = 240
_SEP = " / "


class PrecedentError(Exception):
    status = 400

    def __init__(self, msg: str, loc: str | None = None):
        super().__init__(msg)
        self.msg = msg
        self.loc = loc


class PrecedentNotFound(PrecedentError):
    status = 404


class PrecedentInvalid(PrecedentError):
    status = 422


class PrecedentConflict(PrecedentError):
    status = 409


# ---------------------------------------------------------------- текст примера

def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    cut = text[:max(1, n - 1)]
    sp = cut.rfind(" ")
    if sp > n * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:—-") + "…"


def short_fragment(text: str, limit: int = FRAGMENT_LIMIT) -> str:
    """«РОЛЬ: текст / РОЛЬ: текст» → не длиннее limit: каждая реплика сокращается поровну, лишние в конце отбрасываются."""
    parts = [" ".join(p.split()) for p in (text or "").split(_SEP)]
    parts = [p for p in parts if p]
    joined = _SEP.join(parts)
    if len(joined) <= limit:
        return joined
    per = max(80, (limit - len(_SEP) * (len(parts) - 1)) // len(parts))
    out: list[str] = []
    for p in parts:
        q = _clip(p, per)
        if len(_SEP.join([*out, q])) > limit:
            if not out:
                return _clip(p, limit)
            break
        out.append(q)
    return _SEP.join(out)


def example_of(p: dict[str, Any]) -> Example:
    return Example(verdict=p["verdict"], fragment=short_fragment(p["fragment_full"]), why=_clip(p["why"], WHY_LIMIT))


def _has_example(cfg: ChecklistConfig, cid: str, ex: Example) -> bool:
    try:
        crit = cfg.criterion(cid)
    except KeyError:
        return False
    return any(e.verdict == ex.verdict and e.fragment == ex.fragment for e in crit.examples)


# ---------------------------------------------------------------- чтение

_LIST_SQL = """
SELECT p.id, p.appeal_id, p.criterion_id, p.fragment, p.verdict, p.why, p.added_in_version_id, p.created_at,
       a.evaluation_id, a.criterion_result_id, e.uid, c.external_id, c.group_id, c.language, c.channel,
       cr.verdict AS original_verdict, d.action, d.reviewer,
       v.version AS added_in_version, v.status AS added_in_version_status
FROM precedents p
LEFT JOIN appeals a ON a.id = p.appeal_id
LEFT JOIN evaluations e ON e.id = a.evaluation_id
LEFT JOIN conversations c ON c.uid = e.uid
LEFT JOIN criterion_results cr ON cr.id = a.criterion_result_id
LEFT JOIN decisions d ON d.appeal_id = p.appeal_id
LEFT JOIN checklist_versions v ON v.id = p.added_in_version_id
"""


def _holdout_families(con: sqlite3.Connection) -> dict[str, str]:
    """external_id → отложенная семья сценариев (eval/holdout.txt): примеры из неё портят честность метрик."""
    from cqa.quality.gold import load_holdout

    hold = load_holdout()
    if not hold:
        return {}
    return {r["external_id"]: r["family"] for r in con.execute(
        "SELECT external_id, MAX(family) AS family FROM gold_labels GROUP BY external_id") if r["family"] in hold}


def list_precedents(con: sqlite3.Connection, criterion: str | None = None,
                    ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Прецеденты, новые сверху: критерий, вердикт, фрагмент (как он попадёт в чек-лист и полный), версия."""
    from cqa.checklist.store import get_active

    sql, args = _LIST_SQL + " WHERE 1=1", []
    if criterion:
        sql += " AND p.criterion_id = ?"
        args.append(criterion)
    if ids is not None:
        if not ids:
            return []
        sql += f" AND p.id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    try:
        active = get_active(con)[0]
    except KeyError:
        active = None
    hold = _holdout_families(con)
    out = []
    for r in con.execute(sql + " ORDER BY p.id DESC", args).fetchall():
        title = r["criterion_id"]
        if active is not None:
            try:
                title = active.criterion(r["criterion_id"]).title
            except KeyError:
                pass
        p = {
            "id": r["id"], "appeal_id": r["appeal_id"], "evaluation_id": r["evaluation_id"], "uid": r["uid"],
            "external_id": r["external_id"], "group_id": r["group_id"], "language": r["language"],
            "channel": r["channel"], "criterion_id": r["criterion_id"], "criterion_title": title,
            "verdict": r["verdict"], "original_verdict": r["original_verdict"], "action": r["action"],
            "reviewer": r["reviewer"], "why": r["why"], "fragment_full": r["fragment"],
            "added_in_version_id": r["added_in_version_id"], "added_in_version": r["added_in_version"],
            "added_in_version_status": r["added_in_version_status"], "created_at": r["created_at"],
            "holdout_family": hold.get(r["external_id"] or ""),
        }
        ex = example_of(p)
        p["fragment"] = ex.fragment
        p["in_active_version"] = bool(active is not None and _has_example(active, r["criterion_id"], ex))
        out.append(p)
    return out


def get_precedent(con: sqlite3.Connection, precedent_id: int) -> dict[str, Any]:
    rows = list_precedents(con, ids=[precedent_id])
    if not rows:
        raise PrecedentNotFound(f"прецедент {precedent_id} не найден")
    return rows[0]


# ---------------------------------------------------------------- прецеденты → черновик версии

def build_version(con: sqlite3.Connection, precedent_ids: list[int], comment: str | None = None) -> dict[str, Any]:
    """Черновик новой версии от активной с примерами из прецедентов.

    → {version_id, version, parent_id, added: [id], skipped: [{id, reason}], diff, warnings}.
    Активная версия не меняется. Ошибки: PrecedentNotFound / PrecedentInvalid / PrecedentConflict.
    """
    from cqa.checklist.store import create_version, diff, get_active, get_version
    from cqa.db.conn import tx

    ids = list(dict.fromkeys(int(i) for i in precedent_ids or []))
    if not ids:
        raise PrecedentInvalid("нужен хотя бы один прецедент", "precedent_ids")
    found = {p["id"]: p for p in list_precedents(con, ids=ids)}
    missing = [i for i in ids if i not in found]
    if missing:
        raise PrecedentNotFound(f"нет прецедентов: {', '.join(map(str, missing))}")
    try:
        active, meta = get_active(con)
    except KeyError:
        raise PrecedentConflict("нет активной версии чек-листа") from None

    data = active.model_dump(mode="json")
    crits = {c["id"]: c for c in data["criteria"]}
    added: list[int] = []
    skipped: list[dict[str, Any]] = []
    warnings: list[str] = []
    for i in ids:
        p = found[i]
        crit = crits.get(p["criterion_id"])
        if crit is None:
            raise PrecedentInvalid(f"прецедент {i}: критерия {p['criterion_id']} нет в активной версии", "precedent_ids")
        allowed = active.criterion(p["criterion_id"]).allowed_verdicts()
        if p["verdict"] not in allowed:
            raise PrecedentInvalid(f"прецедент {i}: вердикт {p['verdict']!r} недопустим для критерия "
                                   f"{p['criterion_id']} ({', '.join(allowed)})", "precedent_ids")
        if not (p["fragment_full"] or "").strip():
            raise PrecedentInvalid(f"прецедент {i}: пустой фрагмент", "precedent_ids")
        ex = example_of(p).model_dump()
        if any(e["verdict"] == ex["verdict"] and e["fragment"] == ex["fragment"] for e in crit["examples"]):
            skipped.append({"id": i, "reason": "пример уже есть в активной версии"})
            continue
        crit["examples"].append(ex)
        added.append(i)
        if p["holdout_family"]:
            warnings.append(f"прецедент {i} из отложенной семьи {p['holdout_family']}: метрики holdout после "
                            f"публикации станут нечестными")
    if not added:
        raise PrecedentConflict("все выбранные прецеденты уже есть в активной версии")

    cfg = ChecklistConfig.model_validate(data)
    by_crit: dict[str, list[int]] = {}
    for i in added:
        by_crit.setdefault(found[i]["criterion_id"], []).append(i)
    auto = "прецеденты: " + "; ".join(f"{cid} ← #{', #'.join(map(str, pids))}" for cid, pids in by_crit.items())
    with tx(con):
        vid = create_version(con, active.id, cfg, parent_id=int(meta["id"]),
                             comment=(comment or "").strip() or auto, status="draft")
        con.execute(f"UPDATE precedents SET added_in_version_id=? WHERE id IN ({','.join('?' * len(added))})",
                    [vid, *added])
    _cfg, new_meta = get_version(con, vid)
    return {"version_id": vid, "version": new_meta["version"], "status": new_meta["status"],
            "parent_id": int(meta["id"]), "parent_version": meta["version"], "comment": new_meta["comment"],
            "added": added, "skipped": skipped, "diff": diff(active, cfg), "warnings": warnings}


def precedent_to_version(con: sqlite3.Connection, precedent_ids: list[int], comment: str | None = None) -> int:
    """Черновик новой версии от активной: прецеденты → examples своих критериев. → id версии (без публикации)."""
    return int(build_version(con, precedent_ids, comment)["version_id"])


# ---------------------------------------------------------------- похожие диалоги

def similar_items(con: sqlite3.Connection, precedent_id: int, limit: int = 10) -> dict[str, Any]:
    """Диалоги для пробного прогона версии с прецедентом и признаки совпадения.

    Нужна хотя бы одна из главных связей — та же причина обращения или тот же исходный вердикт критерия;
    порядок: причина → вердикт → язык → канал → та же группа → ID.
    """
    from cqa.features.operators import load_dialogs

    p = get_precedent(con, precedent_id)
    if not p["uid"]:
        raise PrecedentInvalid(f"прецедент {precedent_id} не связан с диалогом")
    cid = p["criterion_id"]
    src = con.execute("SELECT uid, group_id, language, channel, current_evaluation_id FROM conversations WHERE uid=?",
                      (p["uid"],)).fetchone()
    if src is None:
        raise PrecedentNotFound(f"диалог {p['uid']} не найден")
    reason = None
    for eid in (p["evaluation_id"], src["current_evaluation_id"]):
        if eid:
            r = con.execute("SELECT contact_reason FROM signals WHERE evaluation_id=?", (eid,)).fetchone()
            if r and r["contact_reason"]:
                reason = r["contact_reason"]
                break
    verdict = p["original_verdict"]
    items = []
    for d in load_dialogs(con):
        if d.uid == src["uid"]:
            continue
        match = {
            "reason": bool(reason) and d.contact_reason == reason,
            "verdict": verdict is not None and d.original.get(cid) == verdict,
            "language": d.language == src["language"],
            "channel": d.channel == src["channel"],
        }
        if not (match["reason"] or match["verdict"]):
            continue
        items.append((
            (not match["reason"], not match["verdict"], not match["language"], not match["channel"],
             d.group_id != src["group_id"], d.group_id, d.external_id),
            {"uid": d.uid, "external_id": d.external_id, "group_id": d.group_id, "language": d.language,
             "channel": d.channel, "contact_reason": d.contact_reason, "verdict": d.original.get(cid),
             "final_verdict": d.verdicts.get(cid), "total": d.total, "match": match},
        ))
    items.sort(key=lambda x: x[0])
    picked = [x[1] for x in items[:max(0, limit)]]
    return {"precedent_id": precedent_id, "criterion_id": cid, "source_uid": src["uid"], "contact_reason": reason,
            "original_verdict": verdict, "language": src["language"], "channel": src["channel"],
            "added_in_version_id": p["added_in_version_id"],
            "uids": [x["uid"] for x in picked], "items": picked}


def similar_dialogs(con: sqlite3.Connection, precedent_id: int, limit: int = 10) -> list[str]:
    """uid диалогов, на которых стоит проверить версию с прецедентом (для dry-run {uids})."""
    return similar_items(con, precedent_id, limit)["uids"]
