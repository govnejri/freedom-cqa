"""Апелляции и решения проверяющего (docs/task.txt, пункты 05–06; docs/implementation.md §8).

- create_appeal: критерий есть в оценке и уже оценён, открытой апелляции по нему нет.
- get_appeal: спорная оценка, комментарий и фрагмент диалога — реплики-доказательства, контрдоказательства и
  проверенный участок, каждая ±2 реплики контекста, без повторов.
- decide: одно решение на апелляцию; `change` — вердикт из crit.allowed_verdicts(); финальный итог
  пересчитывается той же функцией score() (runner.recompute_final) в транзакции решения.
- make_precedent: строка в precedents (механизм прецедентов в промпте — P2, здесь только запись).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from cqa.api.schemas import AppealCreateDTO, AppealDTO, DecisionCreateDTO, MessageDTO
from cqa.db.conn import now_iso, tx

CONTEXT = 2                  # реплик контекста вокруг каждой опорной
ACTIONS = ("confirm", "change")
_PRECEDENT_MAX = 6           # реплик в тексте прецедента, если доказательств нет и берётся участок


class AppealError(Exception):
    status = 400

    def __init__(self, msg: str, loc: str | None = None):
        super().__init__(msg)
        self.msg = msg
        self.loc = loc


class AppealNotFound(AppealError):
    status = 404


class AppealConflict(AppealError):
    status = 409


class AppealInvalid(AppealError):
    status = 422


# ---------------------------------------------------------------- подача

def create_appeal(con: sqlite3.Connection, dto: AppealCreateDTO, author_role: str = "operator") -> AppealDTO:
    from cqa.core.validate import normalize_id

    ev = con.execute("SELECT id, uid FROM evaluations WHERE id=?", (dto.evaluation_id,)).fetchone()
    if ev is None:
        raise AppealNotFound(f"оценка {dto.evaluation_id} не найдена", "evaluation_id")
    cur = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (ev["uid"],)).fetchone()
    if cur and cur["current_evaluation_id"] and cur["current_evaluation_id"] != ev["id"]:
        raise AppealInvalid("Эта оценка устарела: у диалога есть более новая. Оспорьте актуальную оценку.",
                            "evaluation_id")
    cr = con.execute("SELECT id, status, verdict FROM criterion_results WHERE evaluation_id=? AND criterion_id=?",
                     (dto.evaluation_id, dto.criterion_id)).fetchone()
    if cr is None:
        raise AppealInvalid(f"в оценке {dto.evaluation_id} нет критерия {dto.criterion_id!r}", "criterion_id")
    if cr["status"] != "done" or not cr["verdict"]:
        raise AppealInvalid(f"критерий {dto.criterion_id} ещё не оценён (статус {cr['status']})", "criterion_id")
    comment = (dto.comment or "").strip()
    if not comment:
        raise AppealInvalid("нужен комментарий", "comment")

    mids = {r["mid"] for r in con.execute("SELECT mid FROM messages WHERE uid=?", (ev["uid"],))}
    counter: list[str] = []
    for i, raw in enumerate(dto.counter_evidence):
        mid = normalize_id(raw)
        if mid is None or mid not in mids:
            raise AppealInvalid(f"нет реплики {raw!r} в диалоге", f"counter_evidence.{i}")
        if mid not in counter:
            counter.append(mid)

    with tx(con):
        dup = con.execute("SELECT id FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'",
                          (dto.evaluation_id, dto.criterion_id)).fetchone()
        if dup is not None:
            raise AppealConflict(f"по критерию {dto.criterion_id} уже есть открытая апелляция #{dup['id']}")
        appeal_id = con.execute(
            "INSERT INTO appeals(evaluation_id, criterion_result_id, criterion_id, comment, counter_evidence_json,"
            " author_role, status, created_at) VALUES (?,?,?,?,?,?, 'open', ?)",
            (dto.evaluation_id, cr["id"], dto.criterion_id, comment, json.dumps(counter, ensure_ascii=False),
             author_role, now_iso()),
        ).lastrowid
    return get_appeal(con, int(appeal_id))


# ---------------------------------------------------------------- чтение

def _messages(con: sqlite3.Connection, uid: str) -> list[MessageDTO]:
    from cqa.api.routes.views import message_dto
    from cqa.ingest.store import get_conversation
    return [message_dto(m) for m in get_conversation(con, uid).messages]


def build_fragment(messages: list[MessageDTO], evidence: list[str], checked_range: list[str] | None,
                   counter: list[str], context: int = CONTEXT) -> list[MessageDTO]:
    """Опорные реплики (доказательства, контрдоказательства, проверенный участок) ±context, по порядку, без повторов.

    Нет ни одной опорной реплики — весь диалог (проверяющему нужно хоть что-то увидеть).
    """
    if not messages:
        return []
    pos = {m.mid: i for i, m in enumerate(messages)}
    anchors: set[int] = set()
    for mid in [*evidence, *counter]:
        if mid in pos:
            anchors.add(pos[mid])
    if checked_range:
        ends = [pos[m] for m in checked_range if m in pos]
        if ends:
            anchors.update(range(min(ends), max(ends) + 1))
    if not anchors:
        return list(messages)
    keep: set[int] = set()
    for i in anchors:
        keep.update(range(max(0, i - context), min(len(messages), i + context + 1)))
    return [messages[i] for i in sorted(keep)]


def _decision(con: sqlite3.Connection, appeal_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM decisions WHERE appeal_id=?", (appeal_id,)).fetchone()


def _decision_dict(d: sqlite3.Row | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {
        "action": d["action"], "new_verdict": d["new_verdict"], "reason": d["reason"], "reviewer": d["reviewer"],
        "make_precedent": bool(d["make_precedent"]), "created_at": d["created_at"],
        "prev_final_total": d["prev_final_total"], "new_final_total": d["new_final_total"],
    }


def _appeal_dto(con: sqlite3.Connection, a: sqlite3.Row, versions=None) -> AppealDTO:
    from cqa.api.routes.views import VersionCache, criterion_dtos, evaluation_row

    versions = versions or VersionCache(con)
    ev = evaluation_row(con, a["evaluation_id"])
    cfg, _meta = versions.get(ev["checklist_version_id"])
    crs = criterion_dtos(con, ev, cfg, only=a["criterion_id"])
    if not crs:
        raise AppealNotFound(f"результат критерия {a['criterion_id']} оценки {ev['id']} не найден")
    original = crs[0]
    counter = json.loads(a["counter_evidence_json"] or "[]")
    fragment = build_fragment(_messages(con, ev["uid"]), [e.id for e in original.evidence],
                              original.checked_range, counter)
    d = _decision(con, a["id"])
    return AppealDTO(
        id=a["id"], evaluation_id=ev["id"], uid=ev["uid"], criterion_id=a["criterion_id"],
        criterion_title=original.title, comment=a["comment"], counter_evidence=counter, status=a["status"],
        created_at=a["created_at"], original=original, fragment=fragment,
        ai_opinion=json.loads(a["ai_opinion_json"]) if a["ai_opinion_json"] else None,
        decision=_decision_dict(d),
        prev_final_total=d["prev_final_total"] if d else None,
        new_final_total=d["new_final_total"] if d else None,
    )


def get_appeal(con: sqlite3.Connection, appeal_id: int) -> AppealDTO:
    a = con.execute("SELECT * FROM appeals WHERE id=?", (appeal_id,)).fetchone()
    if a is None:
        raise AppealNotFound(f"апелляция {appeal_id} не найдена")
    return _appeal_dto(con, a)


def list_appeals(con: sqlite3.Connection, status: str | None = None, uid: str | None = None,
                 evaluation_id: int | None = None) -> list[AppealDTO]:
    from cqa.api.routes.views import VersionCache

    sql = "SELECT a.* FROM appeals a JOIN evaluations e ON e.id = a.evaluation_id WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND a.status=?"
        args.append(status)
    if uid:
        sql += " AND e.uid=?"
        args.append(uid)
    if evaluation_id is not None:
        sql += " AND a.evaluation_id=?"
        args.append(evaluation_id)
    sql += " ORDER BY a.id DESC"
    versions = VersionCache(con)
    return [_appeal_dto(con, a, versions) for a in con.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- решение

def _precedent_fragment(con: sqlite3.Connection, uid: str, mids: list[str]) -> str:
    from cqa.core.prompt import ROLE_LABEL

    by_mid = {r["mid"]: r for r in con.execute("SELECT mid, speaker, text FROM messages WHERE uid=? ORDER BY idx", (uid,))}
    parts = []
    for mid in mids:
        r = by_mid.get(mid)
        if r is not None:
            parts.append(f"{ROLE_LABEL.get(r['speaker'], r['speaker'].upper())}: {r['text']}")
    return " / ".join(parts)


def _precedent_mids(con: sqlite3.Connection, uid: str, evidence: list[str], counter: list[str],
                    checked_range: list[str] | None, prefer_counter: bool = False) -> list[str]:
    """Реплики для текста прецедента. Если проверяющий изменил вердикт — сначала контрдоказательства
    оператора (на них решение и основано), иначе доказательства модели; затем проверенный участок."""
    for mids in ((counter, evidence) if prefer_counter else (evidence, counter)):
        if mids:
            return list(dict.fromkeys(mids))
    if checked_range:
        rows = con.execute("SELECT mid FROM messages WHERE uid=? ORDER BY idx", (uid,)).fetchall()
        order = [r["mid"] for r in rows]
        ends = [order.index(m) for m in checked_range if m in order]
        if ends:
            return order[min(ends):max(ends) + 1][:_PRECEDENT_MAX]
    return []


def decide(con: sqlite3.Connection, appeal_id: int, dto: DecisionCreateDTO, reviewer: str = "reviewer") -> AppealDTO:
    from cqa.core.runner import recompute_final

    a = con.execute("SELECT * FROM appeals WHERE id=?", (appeal_id,)).fetchone()
    if a is None:
        raise AppealNotFound(f"апелляция {appeal_id} не найдена")
    if a["status"] != "open" or _decision(con, appeal_id) is not None:
        raise AppealConflict(f"по апелляции {appeal_id} решение уже принято")
    action = (dto.action or "").strip()
    if action not in ACTIONS:
        raise AppealInvalid(f"action: ожидается одно из {', '.join(ACTIONS)}", "action")
    reason = (dto.reason or "").strip()
    if not reason:
        raise AppealInvalid("нужна причина решения", "reason")

    ev = con.execute("SELECT e.*, c.channel FROM evaluations e JOIN conversations c ON c.uid = e.uid WHERE e.id=?",
                     (a["evaluation_id"],)).fetchone()
    from cqa.checklist.store import get_version
    cfg, _meta = get_version(con, ev["checklist_version_id"])
    cr = con.execute("SELECT * FROM criterion_results WHERE id=?", (a["criterion_result_id"],)).fetchone()

    new_verdict: str | None = None
    if action == "change":
        new_verdict = (dto.new_verdict or "").strip()
        try:
            allowed = cfg.criterion(a["criterion_id"]).allowed_verdicts()
        except KeyError:
            raise AppealInvalid(f"критерия {a['criterion_id']} нет в версии чек-листа оценки", "criterion_id") from None
        if new_verdict not in allowed:
            raise AppealInvalid(f"недопустимый вердикт {new_verdict!r}; допустимо: {', '.join(allowed)}", "new_verdict")

    evidence = [e.get("id") for e in json.loads(cr["evidence_json"] or "[]") if e.get("id")]
    counter = json.loads(a["counter_evidence_json"] or "[]")
    checked_range = json.loads(cr["checked_range_json"] or "null")
    last_change = con.execute(
        "SELECT d.new_verdict FROM decisions d JOIN appeals x ON x.id = d.appeal_id WHERE x.evaluation_id=?"
        " AND x.criterion_id=? AND d.action='change' AND d.new_verdict IS NOT NULL ORDER BY d.id DESC LIMIT 1",
        (ev["id"], a["criterion_id"]),
    ).fetchone()
    final_verdict = new_verdict if action == "change" else (last_change[0] if last_change else cr["verdict"] or "")

    try:
        with tx(con):
            prev = con.execute("SELECT final_total FROM evaluations WHERE id=?", (ev["id"],)).fetchone()["final_total"]
            dec_id = con.execute(
                "INSERT INTO decisions(appeal_id, action, new_verdict, reason, reviewer, prev_final_total,"
                " make_precedent, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (appeal_id, action, new_verdict, reason, reviewer, prev, int(dto.make_precedent), now_iso()),
            ).lastrowid
            new_total = recompute_final(con, ev["id"], cfg, channel=ev["channel"])
            con.execute("UPDATE decisions SET new_final_total=? WHERE id=?", (new_total, dec_id))
            con.execute("UPDATE appeals SET status='decided' WHERE id=?", (appeal_id,))
            if dto.make_precedent:
                mids = _precedent_mids(con, ev["uid"], evidence, counter, checked_range,
                                       prefer_counter=(action == "change"))
                mids = sorted(mids, key=lambda m: int(m[1:]) if m[1:].isdigit() else 0)   # порядок переписки
                con.execute(
                    "INSERT INTO precedents(appeal_id, criterion_id, fragment, verdict, why, added_in_version_id,"
                    " created_at) VALUES (?,?,?,?,?, NULL, ?)",
                    (appeal_id, a["criterion_id"], _precedent_fragment(con, ev["uid"], mids), final_verdict,
                     reason, now_iso()),
                )
    except ValueError as e:          # score(): вердикт не подходит к конфигу — решение не сохраняется
        raise AppealInvalid(f"пересчёт итога: {e}", "new_verdict") from None
    return get_appeal(con, appeal_id)
