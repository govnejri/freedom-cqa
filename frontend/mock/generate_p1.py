"""Мок-данные экранов P1 из реальной БД: инсайты, качество, автотесты, настройки, журнал оценки, второе мнение,
«было/стало» пробного прогона.

    uv run python frontend/mock/generate_p1.py --db var/cqa.db [--dry-run 12]

Все ответы — реальные, в форматах роутов cqa/api/routes/*. Для 5 диалогов мока (frontend/mock/generate.py)
берутся сигналы, перекрёстная проверка и журнал вызовов их текущей реальной оценки; мок-оценки (101…105) — свои,
поэтому mock.js пересчитывает «согласна/не согласна» второй модели против мок-вердиктов.
Тела запросов к модели хранятся со справочником строк (system-промпт и диалог повторяются между вызовами).
Пишет frontend/mock/p1.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cqa.api.routes.dryrun import DRY_RUN_KIND, run_diff  # noqa: E402
from cqa.api.routes.health import _settings  # noqa: E402
from cqa.api.routes.quality import quality_report, runs_summary  # noqa: E402
from cqa.api.routes.views import LLM_CALL_COLUMNS, llm_call_dto  # noqa: E402
from cqa.db.conn import connect  # noqa: E402
from cqa.llm.store import get_call  # noqa: E402
from cqa.quality.autotest import report as autotest_report  # noqa: E402

OUT = Path(__file__).resolve().parent / "p1.json"
IDS = ["p1-ru-001", "p1-ru-022", "p1-kz-015", "p1-ru-025", "p4-ru-002"]   # как в generate.py
EVAL_ID = {cid: 101 + i for i, cid in enumerate(IDS)}
GROUP = "DEV"
# апелляция мока #2 (p4-ru-002, критерий 01) — реальное второе мнение по такой же апелляции
APPEAL_OPINIONS = {2: ("DEV:p4-ru-002", "01")}
QUALITY_VARIANTS = [(None, "all", "language"), (None, "tune", "language"), (None, "holdout", "language"),
                    (None, "all", "channel"), (None, "all", "family")]


class Strings:
    """Справочник строк: одинаковые промпты и диалоги хранятся один раз."""

    def __init__(self):
        self.items: list[str] = []
        self.index: dict[str, int] = {}

    def ref(self, s: str | None) -> int | None:
        if s is None:
            return None
        if s not in self.index:
            self.index[s] = len(self.items)
            self.items.append(s)
        return self.index[s]


def _slim(r: dict) -> dict:
    """Экран не показывает расхождения по диалогам и критерии внутри разреза — в мок их не кладём."""
    r["diffs"] = []
    for g in (r.get("by") or {}).get("groups", []):
        g.pop("criteria", None)
    return r


def quality_variants(con) -> dict[str, dict]:
    out = {}
    default_run = None
    for run, split, by in QUALITY_VARIANTS:
        r = quality_report(con, run, split, by)
        if r["run"] is None:
            continue
        default_run = default_run or r["run"]["id"]
        out[f"{r['run']['id']}|{split}|{by}"] = _slim(r)
    # прочие завершённые прогоны эталона — только основной срез
    for item in runs_summary(con):
        if item["id"] == default_run or not item["n"]:
            continue
        out[f"{item['id']}|all|language"] = _slim(quality_report(con, item["id"], "all", "language"))
    return {"default": f"{default_run}|all|language", "reports": out}


def autotests(con) -> dict:
    a = autotest_report(con)
    a.pop("tests", None)                                   # экран использует сводки и список проваленных
    for f in a.get("failed", []):
        f.pop("checks", None)
    return a


def calls_for(con, strings: Strings) -> tuple[dict, dict]:
    by_eval, details = {}, {}
    for ext in IDS:
        row = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (f"{GROUP}:{ext}",)).fetchone()
        if not row or not row[0]:
            continue
        rows = con.execute(f"SELECT {LLM_CALL_COLUMNS} FROM llm_calls WHERE evaluation_id=? ORDER BY id", (row[0],)).fetchall()
        by_eval[str(EVAL_ID[ext])] = [llm_call_dto(r).model_dump(exclude={"request", "response_text"}) for r in rows]
        for r in rows:
            full = llm_call_dto(get_call(con, r["id"]), full=True).model_dump()
            req = full.get("request")
            if isinstance(req, dict):
                req = dict(req)
                req["messages"] = [{"role": m.get("role"), "content": strings.ref(m.get("content") if isinstance(m.get("content"), str)
                                                                                  else json.dumps(m.get("content"), ensure_ascii=False))}
                                   for m in req.get("messages", [])]
                if "json_schema" in req:
                    req["json_schema"] = strings.ref(json.dumps(req["json_schema"], ensure_ascii=False))
            details[str(r["id"])] = {"request": req, "response_text": strings.ref(full.get("response_text"))}
    return by_eval, details


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "var" / "cqa.db")
    ap.add_argument("--dry-run", type=int, default=None, help="id пробного прогона для шаблона «было/стало»")
    args = ap.parse_args()
    con = connect(args.db)

    uids = [f"{GROUP}:{x}" for x in IDS]
    signals, second = {}, {}
    for uid in uids:
        ev = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()[0]
        s = con.execute("SELECT * FROM signals WHERE evaluation_id=?", (ev,)).fetchone()
        if s is not None:
            signals[uid] = {"contact_reason": s["contact_reason"], "resolved": bool(s["resolved"]),
                            "churn_risk": bool(s["churn_risk"]), "missed_opportunity": s["missed_opportunity"] or "",
                            "unanswered": json.loads(s["unanswered_json"] or "[]")}
        for r in con.execute("SELECT criterion_id, second_json FROM criterion_results WHERE evaluation_id=? AND second_json IS NOT NULL", (ev,)):
            second.setdefault(uid, {})[r["criterion_id"]] = json.loads(r["second_json"])

    opinions = {}
    for aid, (uid, cid) in APPEAL_OPINIONS.items():
        r = con.execute("SELECT a.ai_opinion_json FROM appeals a JOIN evaluations e ON e.id = a.evaluation_id"
                        " WHERE e.uid=? AND a.criterion_id=? AND a.ai_opinion_json IS NOT NULL ORDER BY a.id DESC LIMIT 1",
                        (uid, cid)).fetchone()
        if r:
            opinions[str(aid)] = json.loads(r[0])

    dry = args.dry_run
    if dry is None:
        r = con.execute("SELECT id FROM runs WHERE kind=? AND status='done' ORDER BY id DESC LIMIT 1", (DRY_RUN_KIND,)).fetchone()
        dry = r[0] if r else None
    diff = run_diff(con, dry) if dry else None

    strings = Strings()
    by_eval, details = calls_for(con, strings)
    data = {
        "signals": signals,
        "second_opinions": second,
        "appeal_opinions": opinions,
        "quality": quality_variants(con),
        "quality_runs": runs_summary(con),
        "autotests": autotests(con),
        "settings": _settings(con, False),
        "dry_run_diff": diff,
        "calls": {"by_evaluation": by_eval, "details": details, "strings": strings.items},
    }
    text = json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":"))
    assert "sig=" not in text
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"{OUT}: {len(text) / 1024:.0f} КБ; вызовов {sum(len(v) for v in by_eval.values())}, строк {len(strings.items)}")


if __name__ == "__main__":
    main()
