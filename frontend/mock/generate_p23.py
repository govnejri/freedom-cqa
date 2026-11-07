"""Мок-данные экранов P2/P3 из реальной БД и записанных реальных ответов API: тренажёр, уроки, тьютор,
лучшие ответы коллег, ассистент руководителя, сводка оператора, прецедент → версия → пробный прогон.

    uv run python frontend/mock/generate_p23.py --db var/cqa.db --recorded <каталог>

P2 (операторы, срезы, прецеденты) мок считает сам по своим диалогам (frontend/js/mock.js) — так цифры совпадают
с карточками. Здесь — только то, что без модели не получить, в форматах роутов cqa/api/routes/features_p3.py,
assistant.py, features_p2.py.

Каталог --recorded (все файлы необязательны; это сохранённые ответы реального API):
- assistant-*.json   — ответ POST /api/assistant/ask (AssistantAnswerDTO), вопрос берётся из поля question;
- tutor-*.json       — ответ POST /api/tutor/ask + ключ "request": {"question", "operator", "uid"} (тело запроса);
- summary-*.json     — ответ POST /api/operators/{name}/summary (имя — поле name);
- precedent-flow.json — {"precedent": элемент GET /api/precedents (до «создать версию»),
                        "similar": ответ GET /api/precedents/{id}/similar,
                        "diff": ответ GET /api/runs/{run_id}/diff пробного прогона на similar.uids после завершения}
                        (другие ключи, например to_version и run, допустимы и игнорируются: версию мок строит сам).
  Мок проигрывает записанное «было/стало», когда пробный прогон запускают ровно на диалогах из diff.
Тренажёр и уроки читаются из БД: последняя завершённая сессия по диалогу DEV:p1-ru-007 (реплики ИИ-клиента
по ходам и поле check — из журнала вызовов), уроки — settings[trainer_lessons].

Диалоги, на которые ссылаются записи (база тренажёра, источники тьютора и ассистента, прецедент и похожие диалоги),
добавляются в мок целиком: сводка, транскрипт, реальная оценка, сигналы. id оценок, результатов критериев и
апелляций перенумеровываются, чтобы не пересечься с мок-данными (frontend/mock/generate.py). Диалог тренажёра
TRAINER:{id} добавляется только как карточка (не в списках). Пишет frontend/mock/p23.json.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cqa.api.routes.conversations import _LIST_SQL
from cqa.api.routes.views import (
    conversation_dto,
    evaluation_dto,
    evaluation_row,
    summary_dto,
)
from cqa.db.conn import connect

MOCK = Path(__file__).resolve().parent
OUT = MOCK / "p23.json"
TRAINER_BASE = "DEV:p1-ru-007"
MAX_EXTRA = 20


def _load(name: str) -> Any:
    return json.loads((MOCK / f"{name}.json").read_text(encoding="utf-8"))


def _norm_q(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").casefold()).strip().rstrip("?.!… ").strip()


class Ids:
    """Перенумерация: id оценок после мок-оценок (101…), результаты критериев = id оценки × 100 + n, апелляции — после мок-апелляций."""

    def __init__(self):
        evals = _load("evaluations")
        appeals = _load("appeals")
        self.next_eval = max(e["id"] for e in evals.values()) + 1
        self.next_appeal = max([a["id"] for a in appeals] or [0]) + 1
        self.evals: dict[int, int] = {}
        self.crs: dict[int, int] = {}
        self.appeals: dict[int, int] = {}

    def ev(self, real: int | None) -> int | None:
        if real is None:
            return None
        if real not in self.evals:
            self.evals[real] = self.next_eval
            self.next_eval += 1
        return self.evals[real]

    def appeal(self, real: int | None) -> int | None:
        if real is None:
            return None
        if real not in self.appeals:
            self.appeals[real] = self.next_appeal
            self.next_appeal += 1
        return self.appeals[real]


def _versions_ok(vid: int) -> int:
    ids = [v["id"] for v in _load("checklist-versions")]
    active = next((v["id"] for v in _load("checklist-versions") if v["is_active"]), ids[-1])
    return vid if vid in ids else active


def evaluation(con, eval_id: int, ids: Ids) -> dict[str, Any]:
    ev = evaluation_dto(con, evaluation_row(con, eval_id)).model_dump(mode="json")
    new_id = ids.ev(eval_id)
    ev["id"] = new_id
    ev["run_id"] = None
    ev["checklist_version_id"] = _versions_ok(ev["checklist_version_id"])
    for i, cr in enumerate(ev["criteria"], 1):
        ids.crs[cr["id"]] = new_id * 100 + i
        cr["id"] = new_id * 100 + i
        cr["appeal_id"] = ids.appeal(cr["appeal_id"])
    return ev


def summary(con, uid: str, ids: Ids) -> dict[str, Any]:
    row = con.execute(_LIST_SQL + " WHERE c.uid=?", (uid,)).fetchone()
    s = summary_dto(row).model_dump(mode="json")
    s["evaluation_id"] = ids.ev(s["evaluation_id"])
    s["has_audio"] = False
    return s


def conversation(con, uid: str) -> dict[str, Any]:
    from cqa.ingest.store import get_conversation
    c = conversation_dto(get_conversation(con, uid)).model_dump(mode="json")
    c["has_audio"] = False          # реальные mp3 в мок не кладём
    c["audio_url"] = None
    return c


def signals(con, eval_id: int | None) -> dict[str, Any] | None:
    if not eval_id:
        return None
    s = con.execute("SELECT * FROM signals WHERE evaluation_id=?", (eval_id,)).fetchone()
    if s is None:
        return None
    return {"contact_reason": s["contact_reason"], "resolved": bool(s["resolved"]), "churn_risk": bool(s["churn_risk"]),
            "missed_opportunity": s["missed_opportunity"] or "", "unanswered": json.loads(s["unanswered_json"] or "[]")}


def extra_dialog(con, uid: str, ids: Ids, in_lists: bool = True) -> dict[str, Any] | None:
    row = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()
    if row is None:
        return None
    eid = row["current_evaluation_id"] or con.execute(
        "SELECT MAX(id) FROM evaluations WHERE uid=? AND status='done'", (uid,)).fetchone()[0]
    if not eid:
        return None
    out = {"uid": uid, "in_lists": in_lists, "conversation": conversation(con, uid), "evaluation": evaluation(con, eid, ids),
           "summary": None, "signals": signals(con, eid)}
    if in_lists:
        out["summary"] = summary(con, uid, ids)
    return out


# ---------------------------------------------------------------- тренажёр

def trainer_recording(con, ids: Ids) -> dict[str, Any] | None:
    from cqa.features import trainer

    row = con.execute("SELECT id FROM trainer_sessions WHERE base_uid=? AND status='finished' AND result_evaluation_id"
                      " IS NOT NULL ORDER BY id DESC LIMIT 1", (TRAINER_BASE,)).fetchone()
    if row is None:
        return None
    sid = row["id"]
    view = trainer.session_view(con, sid)
    data = json.loads(con.execute("SELECT persona_json FROM trainer_sessions WHERE id=?", (sid,)).fetchone()[0])
    checks = []
    for call_id in data["state"].get("calls") or []:
        r = con.execute("SELECT response_text FROM llm_calls WHERE id=?", (call_id,)).fetchone()
        try:
            checks.append(" ".join(str(json.loads(r["response_text"]).get("check") or "").split()) if r else "")
        except ValueError:
            checks.append("")
    tr = view["transcript"]
    prefix = tr[:view["prefix_len"]]
    turns, n = [], 0
    rest = tr[view["prefix_len"]:]
    i = 0
    while i < len(rest):
        m = rest[i]
        if m["source"] == "trainee" and i + 1 < len(rest) and rest[i + 1]["source"] == "ai":
            n += 1
            turns.append({"client": rest[i + 1]["text"], "check": checks[n - 1] if n - 1 < len(checks) else ""})
            i += 2
        else:
            i += 1
    # исход хода: клиент завершил на последнем записанном ходе (или после лимита ходов)
    for k, t in enumerate(turns, 1):
        last = k == len(turns) and view["client_finished"]
        t["finished"] = last
        t["outcome"] = view["outcome"] if last else "продолжает"
    persona_hidden = {k: view["persona"].get(k) for k in trainer.HIDDEN_FIELDS}
    persona_public = {k: v for k, v in view["persona"].items() if k not in trainer.HIDDEN_FIELDS}
    base_eval = ids.ev(view["base_evaluation_id"])
    res_eval_real = view["result_evaluation_id"]
    comp = view["comparison"]
    if comp:
        if comp.get("before"):
            comp["before"]["evaluation_id"] = ids.ev(comp["before"]["evaluation_id"])
        if comp.get("after"):
            comp["after"]["evaluation_id"] = ids.ev(comp["after"]["evaluation_id"])
    view["base_evaluation_id"] = base_eval
    view["result_evaluation_id"] = ids.ev(res_eval_real)
    meta = data["meta"]
    return {
        "session": view, "prefix": prefix, "turns": turns, "persona_public": persona_public,
        "persona_hidden": persona_hidden, "max_turns": view["max_turns"], "result_uid": trainer.session_uid(sid),
        "result_real_evaluation_id": res_eval_real,
        "cost": {"persona_cost_usd": meta.get("persona_cost_usd")},
    }


def lessons_raw(con) -> list[dict[str, Any]]:
    row = con.execute("SELECT value_json FROM settings WHERE key='trainer_lessons'").fetchone()
    data = json.loads(row[0]) if row else []
    return [{k: x.get(k) for k in ("id", "title", "base_uids", "created_at")} for x in data if isinstance(x, dict)]


# ---------------------------------------------------------------- записи из --recorded

def recorded(dir_: Path | None) -> dict[str, Any]:
    out: dict[str, Any] = {"assistant": [], "tutor": [], "summary": [], "precedent_flow": None}
    if not dir_:
        return out
    for p in sorted(dir_.glob("assistant-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("question") and d.get("answer") is not None:
            out["assistant"].append(d)
    for p in sorted(dir_.glob("tutor-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        req = d.pop("request", None)
        if not req or not req.get("question"):
            raise SystemExit(f"{p.name}: нужен ключ request.question (тело POST /api/tutor/ask)")
        out["tutor"].append({"request": {"question": req["question"], "operator": req.get("operator"),
                                         "uid": req.get("uid")}, "response": d})
    for p in sorted(dir_.glob("summary-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        d.pop("status", None)
        if d.get("name") and d.get("summary"):
            out["summary"].append(d)
    pf = dir_ / "precedent-flow.json"
    if pf.is_file():
        out["precedent_flow"] = json.loads(pf.read_text(encoding="utf-8"))
    return out


def _uid_links(text: str) -> list[str]:
    return re.findall(r"\]\(([^)\s]+)\)", text or "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "var" / "cqa.db")
    ap.add_argument("--recorded", type=Path, default=None, help="каталог с сохранёнными ответами API")
    ap.add_argument("--max-extra", type=int, default=MAX_EXTRA, help="сколько реальных диалогов добавить в мок")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp()) / "cqa.db"          # исходную БД не трогаем (connect включает WAL)
    shutil.copy(args.db, tmp)
    for suffix in ("-wal", "-shm"):                    # незачекпоинченные записи живой БД (сервер в WAL-режиме)
        side = Path(str(args.db) + suffix)
        if side.exists():
            shutil.copy(side, Path(str(tmp) + suffix))
    con = connect(tmp)

    from cqa.features import tutor
    from cqa.features.assistant import EXAMPLES

    ids = Ids()
    rec = recorded(args.recorded)
    mock_uids = {s["uid"] for s in _load("conversations")}

    # какие реальные диалоги нужны: по приоритету
    want: list[str] = [TRAINER_BASE]
    lessons = lessons_raw(con)
    for les in lessons:
        want += les["base_uids"]
    pf = rec["precedent_flow"]
    if pf:
        want.append(pf["precedent"]["uid"])
        want += (pf.get("similar") or {}).get("uids") or []
        want += [d["uid"] for d in (pf.get("diff") or {}).get("dialogs") or []]
    for t in rec["tutor"]:
        if t["request"].get("uid"):
            want.append(t["request"]["uid"])
        want += [s["uid"] for s in t["response"].get("sources") or [] if s.get("uid")]
    for a in rec["assistant"]:
        want += [s["uid"] for s in a.get("sources") or []] + _uid_links(a.get("answer"))
    seen: list[str] = []
    for u in want:
        if u and u not in mock_uids and u not in seen and not u.startswith("TRAINER:"):
            seen.append(u)
    extras = []
    for u in seen[:max(0, args.max_extra)]:
        x = extra_dialog(con, u, ids)
        if x:
            extras.append(x)

    trainer_rec = trainer_recording(con, ids)
    trainer_cards = []
    if trainer_rec:
        x = extra_dialog(con, trainer_rec["result_uid"], ids, in_lists=False)
        if x:
            x["evaluation"]["id"] = trainer_rec["session"]["result_evaluation_id"]
            trainer_cards.append(x)

    # прецедент: апелляция и id из реальной БД → мок-нумерация
    if pf:
        p = pf["precedent"]
        if p.get("appeal_id"):
            from cqa.appeals.service import get_appeal
            try:
                a = get_appeal(con, p["appeal_id"]).model_dump(mode="json")
            except Exception:  # noqa: BLE001 — апелляции нет в этой БД: прецедент без неё
                a = None
            if a:
                a["id"] = ids.appeal(a["id"])
                a["evaluation_id"] = ids.ev(a["evaluation_id"])
                if a.get("original"):
                    a["original"]["id"] = ids.crs.get(a["original"]["id"], a["original"]["id"])
                    a["original"]["appeal_id"] = a["id"]
                pf["appeal"] = a
        p["appeal_id"] = ids.appeal(p.get("appeal_id"))
        p["evaluation_id"] = ids.ev(p.get("evaluation_id"))
        diff = pf.get("diff") or {}
        for d in diff.get("dialogs") or []:
            d["evaluation_id"] = None             # оценок пробного прогона в моке нет — без ссылки на журнал
            d["baseline_evaluation_id"] = ids.evals.get(d.get("baseline_evaluation_id"))
        pf = {"precedent": p, "similar": pf.get("similar"), "appeal": pf.get("appeal"),
              "diff": {"criteria": diff.get("criteria") or [], "dialogs": diff.get("dialogs") or []} if diff else None}

    best = tutor.best_answers_view(con, limit=500)
    for it in best["items"]:
        it["evaluation_id"] = ids.evals.get(it["evaluation_id"], it["evaluation_id"])

    data = {
        "extras": extras,
        "trainer": trainer_rec,
        "trainer_cards": trainer_cards,
        "lessons": lessons,
        "tutor": rec["tutor"],
        "best_answers": best,
        "assistant_examples": EXAMPLES,
        "assistant": rec["assistant"],
        "summaries": rec["summary"],
        "precedent_flow": pf,
    }
    text = json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":"))
    assert "sig=" not in text, "подписанная ссылка в моке"
    assert not re.search(r"https?://", text), "внешняя ссылка в моке"
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"{OUT}: {len(text) / 1024:.0f} КБ; диалогов +{len(extras)} ({', '.join(x['uid'] for x in extras)}), "
          f"тренажёр: {'да' if trainer_rec else 'нет'}, уроков {len(lessons)}, тьютор {len(rec['tutor'])}, "
          f"ассистент {len(rec['assistant'])}, сводок {len(rec['summary'])}, прецедент: {'да' if pf else 'нет'}")


if __name__ == "__main__":
    main()
