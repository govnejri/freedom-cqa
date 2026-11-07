"""Пакетный прогон оценки: очередь в БД, лимиты профиля, повтор упавших, продолжение после рестарта.

runs → evaluations → criterion_results(status=pending). Критерии, не запрошенные в прогоне (criteria=[...]),
копируются из текущей оценки диалога, если хэш критерия не изменился. Остальное бесплатно отдаёт кэш LLM
(ключ = полный запрос), поэтому повторный прогон без изменений почти мгновенный.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from typing import Iterable

from cqa.checklist.model import ChecklistConfig, Criterion
from cqa.db.conn import now_iso, tx
from cqa.llm.base import CallContext
from cqa.models import CriterionVerdict
from cqa.settings import get_profile

log = logging.getLogger(__name__)

# Прогоны, которые не меняют текущую оценку диалога (conversations.current_evaluation_id).
SANDBOX_KINDS = ("dry_run", "experiment", "autotest")


def _prompt_version() -> str:
    try:
        from cqa.core.prompt import PROMPT_VERSION
        return PROMPT_VERSION
    except Exception:  # noqa: BLE001
        return "unknown"


def _criterion_hash(crit: Criterion) -> str:
    from cqa.checklist.io import criterion_hash
    return criterion_hash(crit)


def _load_version(con: sqlite3.Connection, version_id: int) -> ChecklistConfig:
    from cqa.checklist.store import get_version
    cfg, _meta = get_version(con, version_id)
    return cfg


# ---------------------------------------------------------------- создание прогона

def create_run(
    con: sqlite3.Connection,
    *,
    uids: Iterable[str],
    version_id: int,
    profile_name: str,
    criteria: list[str] | None = None,
    force: bool = False,
    kind: str = "batch",
    cross_check: bool = False,
) -> int:
    cfg = _load_version(con, version_id)
    profile = get_profile(profile_name)
    uids = list(dict.fromkeys(uids))
    enabled = cfg.enabled_criteria()
    requested = {c.id for c in enabled if not criteria or c.id in criteria}
    pv = _prompt_version()
    params = {"uids": uids, "criteria": criteria, "force": force, "cross_check": cross_check}
    total = 0
    with tx(con):
        cur = con.execute(
            "INSERT INTO runs(kind, checklist_version_id, judge_profile, model, prompt_version, params_json, status, created_at)"
            " VALUES (?,?,?,?,?,?, 'queued', ?)",
            (kind, version_id, profile_name, profile.model, pv, json.dumps(params, ensure_ascii=False), now_iso()),
        )
        run_id = cur.lastrowid
        for uid in uids:
            ev = con.execute(
                "INSERT INTO evaluations(run_id, uid, checklist_version_id, profile, model, prompt_version, status, created_at)"
                " VALUES (?,?,?,?,?,?, 'pending', ?)",
                (run_id, uid, version_id, profile_name, profile.model, pv, now_iso()),
            ).lastrowid
            for crit in enabled:
                h = _criterion_hash(crit)
                copied = False
                if crit.id not in requested and not force:
                    copied = _copy_previous(con, uid, crit.id, h, profile.model, ev)
                if not copied:
                    con.execute(
                        "INSERT INTO criterion_results(evaluation_id, criterion_id, criterion_hash, status) VALUES (?,?,?, 'pending')",
                        (ev, crit.id, h),
                    )
                    total += 1
        con.execute("UPDATE runs SET units_total=? WHERE id=?", (total, run_id))
    return run_id


def _copy_previous(con, uid: str, cid: str, h: str, model: str, new_eval_id: int) -> bool:
    row = con.execute(
        "SELECT cr.* FROM criterion_results cr JOIN evaluations e ON e.id = cr.evaluation_id"
        " WHERE e.uid=? AND cr.criterion_id=? AND cr.criterion_hash=? AND e.model=? AND cr.status='done'"
        " ORDER BY cr.id DESC LIMIT 1",
        (uid, cid, h, model),
    ).fetchone()
    if not row:
        return False
    cols = ["criterion_id", "criterion_hash", "status", "verdict", "category", "evidence_json", "checked_range_json",
            "reasoning", "explanation", "how_to_fix", "validation_json", "attempts", "needs_review", "second_json", "llm_call_id"]
    con.execute(
        f"INSERT INTO criterion_results(evaluation_id, {', '.join(cols)}, cache_hit) VALUES (?, {', '.join('?' * len(cols))}, 1)",
        (new_eval_id, *[row[c] for c in cols]),
    )
    return True


# ---------------------------------------------------------------- исполнение

async def process_run(con: sqlite3.Connection, run_id: int, conv_concurrency: int | None = None) -> dict:
    from cqa.core.judge import judge_criterion
    from cqa.ingest.store import get_conversation

    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(f"run {run_id}")
    if run["status"] in ("done", "cancelled"):
        return dict(run)
    params = json.loads(run["params_json"] or "{}")
    force = bool(params.get("force"))
    cfg = _load_version(con, run["checklist_version_id"])
    profile = get_profile(run["judge_profile"])
    con.execute("UPDATE runs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=?", (now_iso(), run_id))

    evals = con.execute(
        "SELECT id, uid FROM evaluations WHERE run_id=? AND status IN ('pending','running','partial')", (run_id,)
    ).fetchall()
    sem = asyncio.Semaphore(conv_concurrency or max(1, profile.max_concurrency // 2))

    async def judge_row(conv, row) -> None:
        if _cancelled(con, run_id):
            return
        crit = cfg.criterion(row["criterion_id"])
        con.execute("UPDATE criterion_results SET status='running' WHERE id=?", (row["id"],))
        if crit.channels and conv.channel not in crit.channels:
            v = CriterionVerdict(criterion_id=crit.id, verdict="N/A",
                                 explanation=f"{crit.title} — N/A. Критерий не применяется к каналу «{conv.channel}».")
        else:
            ctx = CallContext(purpose="judge", run_id=run_id, evaluation_id=row["evaluation_id"],
                              criterion_result_id=row["id"], uid=conv.uid, criterion_id=crit.id, no_cache=force)
            try:
                v = await judge_criterion(conv, cfg, crit, profile, ctx, con)
            except Exception as e:  # noqa: BLE001 — упавшая единица не останавливает прогон
                log.exception("judge failed: %s %s", conv.uid, crit.id)
                from cqa.llm.redact import redact
                v = CriterionVerdict(criterion_id=crit.id, verdict="", status="failed",
                                     error=redact(f"{type(e).__name__}: {e}")[:500])
        _save_verdict(con, run_id, row["id"], v)

    async def one(ev) -> None:
        async with sem:
            if _cancelled(con, run_id):
                return
            con.execute("UPDATE evaluations SET status='running' WHERE id=?", (ev["id"],))
            try:
                conv = get_conversation(con, ev["uid"])
            except Exception as e:  # noqa: BLE001
                log.error("no conversation %s: %s", ev["uid"], e)
                con.execute("UPDATE evaluations SET status='failed' WHERE id=?", (ev["id"],))
                return
            rows = con.execute(
                "SELECT * FROM criterion_results WHERE evaluation_id=? AND status IN ('pending','running','failed') ORDER BY criterion_id",
                (ev["id"],),
            ).fetchall()
            if rows:
                await judge_row(conv, rows[0])          # прогрев общего префикса в кэше провайдера
                if len(rows) > 1 and profile.provider != "fake" and not _last_cache_hit(con, rows[0]["id"]):
                    await asyncio.sleep(2.0)            # кэш Gemini появляется не сразу (замер: параллельно cached=0)
                await asyncio.gather(*(judge_row(conv, r) for r in rows[1:]))
            finalize_evaluation(con, ev["id"], cfg, channel=conv.channel)
            if params.get("cross_check") and not _cancelled(con, run_id):
                # перекрёстная проверка — только по явному флагу: на DEV 3.7 Flash повторяет ошибки 3.8 Flash
                # (согласие 99.4%), поэтому по умолчанию только страховка для критериев с cross_check: true
                from cqa.core.crosscheck import cross_check_evaluation
                try:
                    await cross_check_evaluation(con, ev["id"])
                except Exception:  # noqa: BLE001
                    log.exception("cross-check failed: %s", ev["uid"])

    await asyncio.gather(*(one(ev) for ev in evals))

    status = "cancelled" if _cancelled(con, run_id) else "done"
    con.execute("UPDATE runs SET status=?, finished_at=? WHERE id=?", (status, now_iso(), run_id))
    return dict(con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def _last_cache_hit(con, crid: int) -> bool:
    r = con.execute("SELECT cache_hit FROM llm_calls WHERE criterion_result_id=? ORDER BY id DESC LIMIT 1", (crid,)).fetchone()
    return bool(r and r["cache_hit"])


def _cancelled(con, run_id: int) -> bool:
    r = con.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
    return bool(r) and r["status"] == "cancelled"


def _save_verdict(con, run_id: int, crid: int, v: CriterionVerdict) -> None:
    calls = con.execute(
        "SELECT id, cache_hit, cost_usd FROM llm_calls WHERE criterion_result_id=? ORDER BY id", (crid,)
    ).fetchall()
    cache_hit = bool(calls) and all(c["cache_hit"] for c in calls)
    cost = sum((c["cost_usd"] or 0) for c in calls if not c["cache_hit"])
    with tx(con):
        con.execute(
            "UPDATE criterion_results SET status=?, verdict=?, category=?, evidence_json=?, checked_range_json=?, reasoning=?,"
            " explanation=?, how_to_fix=?, needs_review=?, error=?, attempts=?, llm_call_id=?, cache_hit=? WHERE id=?",
            (
                v.status, v.verdict or None, v.category, json.dumps([e.model_dump() for e in v.evidence], ensure_ascii=False),
                json.dumps(list(v.checked_range), ensure_ascii=False) if v.checked_range else None,
                v.reasoning, v.explanation, v.how_to_fix, int(v.needs_review), v.error,
                len(calls), calls[-1]["id"] if calls else None, int(cache_hit), crid,
            ),
        )
        con.execute(
            "UPDATE runs SET units_done = units_done + ?, units_failed = units_failed + ?, units_cached = units_cached + ?,"
            " cost_usd = cost_usd + ? WHERE id=?",
            (int(v.status == "done"), int(v.status == "failed"), int(cache_hit and v.status == "done"), cost, run_id),
        )


# ---------------------------------------------------------------- итог исходный и финальный

def _verdicts_of(con, eval_id: int, final: bool) -> tuple[dict[str, str], dict[str, str], bool]:
    rows = con.execute("SELECT * FROM criterion_results WHERE evaluation_id=?", (eval_id,)).fetchall()
    verdicts, cats, complete = {}, {}, True
    for r in rows:
        if r["status"] != "done" or not r["verdict"]:
            complete = False
            continue
        verdicts[r["criterion_id"]] = r["verdict"]
        cats[r["criterion_id"]] = r["category"] or ""
    if final:
        for d in con.execute(
            "SELECT a.criterion_id, d.new_verdict FROM decisions d JOIN appeals a ON a.id = d.appeal_id"
            " WHERE a.evaluation_id=? AND d.action='change' AND d.new_verdict IS NOT NULL ORDER BY d.id",
            (eval_id,),
        ):
            verdicts[d["criterion_id"]] = d["new_verdict"]
    return verdicts, cats, complete


def finalize_evaluation(con, eval_id: int, cfg: ChecklistConfig, channel: str | None = None) -> None:
    """Исходный итог по вердиктам модели + финальный с учётом решений по апелляциям."""
    from cqa.core.scoring import score

    verdicts, cats, complete = _verdicts_of(con, eval_id, final=False)
    agg = con.execute(
        "SELECT COALESCE(SUM(CASE WHEN cache_hit=0 THEN cost_usd END),0) AS cost, COALESCE(SUM(latency_ms),0) AS lat"
        " FROM llm_calls WHERE evaluation_id=?",
        (eval_id,),
    ).fetchone()
    review = con.execute(
        "SELECT COUNT(*) FROM criterion_results WHERE evaluation_id=? AND needs_review=1", (eval_id,)
    ).fetchone()[0]
    if not complete:
        con.execute(
            "UPDATE evaluations SET status='partial', cost_usd=?, latency_ms=?, needs_review=1 WHERE id=?",
            (agg["cost"], agg["lat"], eval_id),
        )
        return
    res = score(cfg, verdicts, channel=channel, categories=cats)
    with tx(con):
        for cs in res.criteria:
            con.execute(
                "UPDATE criterion_results SET points=?, weight=?, applicable=? WHERE evaluation_id=? AND criterion_id=?",
                (cs.points, cs.weight, int(cs.applicable), eval_id, cs.criterion_id),
            )
        con.execute(
            "UPDATE evaluations SET status='done', total=?, score_json=?, gross=?, gross_json=?, needs_review=?,"
            " cost_usd=?, latency_ms=?, finished_at=? WHERE id=?",
            (res.total, res.model_dump_json(), int(res.gross), json.dumps(res.gross_categories, ensure_ascii=False),
             int(review > 0), agg["cost"], agg["lat"], now_iso(), eval_id),
        )
        row = con.execute("SELECT e.uid, r.kind FROM evaluations e LEFT JOIN runs r ON r.id=e.run_id WHERE e.id=?",
                          (eval_id,)).fetchone()
        uid = row["uid"]
        if (row["kind"] or "batch") not in SANDBOX_KINDS:   # пробный прогон и эксперименты не трогают текущую оценку
            prev = con.execute("SELECT current_evaluation_id FROM conversations WHERE uid=?", (uid,)).fetchone()
            prev_eval = prev["current_evaluation_id"] if prev else None
            con.execute("UPDATE conversations SET current_evaluation_id=? WHERE uid=?", (eval_id, uid))
            if prev_eval and prev_eval != eval_id:
                _carry_over_decisions(con, prev_eval, eval_id)
    recompute_final(con, eval_id, cfg, channel=channel)


def _carry_over_decisions(con, prev_eval: int, new_eval: int) -> int:
    """Решения по апелляциям переносятся на новую оценку, если по критерию не изменились ни вердикт модели,
    ни сам критерий (хэш). Иначе после перепрогона решение проверяющего пропало бы из карточки."""
    n = 0
    rows = con.execute(
        "SELECT a.*, d.action, d.new_verdict, d.reason, d.reviewer, d.make_precedent, d.created_at AS d_created,"
        " cr.verdict AS old_verdict, cr.criterion_hash AS old_hash"
        " FROM appeals a JOIN decisions d ON d.appeal_id=a.id JOIN criterion_results cr ON cr.id=a.criterion_result_id"
        " WHERE a.evaluation_id=? AND a.status='decided'", (prev_eval,)).fetchall()
    for r in rows:
        new_cr = con.execute("SELECT id, verdict, criterion_hash FROM criterion_results WHERE evaluation_id=? AND criterion_id=?",
                             (new_eval, r["criterion_id"])).fetchone()
        if not new_cr or new_cr["verdict"] != r["old_verdict"] or new_cr["criterion_hash"] != r["old_hash"]:
            continue
        aid = con.execute(
            "INSERT INTO appeals(evaluation_id, criterion_result_id, criterion_id, comment, counter_evidence_json, author_role,"
            " status, ai_opinion_json, ai_call_id, created_at) VALUES (?,?,?,?,?,?, 'decided', ?,?,?)",
            (new_eval, new_cr["id"], r["criterion_id"], r["comment"], r["counter_evidence_json"], r["author_role"],
             r["ai_opinion_json"], r["ai_call_id"], r["created_at"]),
        ).lastrowid
        con.execute(
            "INSERT INTO decisions(appeal_id, action, new_verdict, reason, reviewer, make_precedent, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (aid, r["action"], r["new_verdict"], r["reason"], r["reviewer"], r["make_precedent"], r["d_created"]),
        )
        n += 1
    return n


def recompute_final(con, eval_id: int, cfg: ChecklistConfig | None = None, channel: str | None = None) -> float | None:
    """Финальный итог = исходные вердикты + решения по апелляциям. Возвращает final_total."""
    from cqa.core.scoring import score

    ev = con.execute("SELECT e.*, c.channel FROM evaluations e JOIN conversations c ON c.uid=e.uid WHERE e.id=?", (eval_id,)).fetchone()
    cfg = cfg or _load_version(con, ev["checklist_version_id"])
    verdicts, cats, complete = _verdicts_of(con, eval_id, final=True)
    if not complete:
        return None
    res = score(cfg, verdicts, channel=channel or ev["channel"], categories=cats)
    con.execute("UPDATE evaluations SET final_total=?, final_score_json=? WHERE id=?", (res.total, res.model_dump_json(), eval_id))
    return res.total


# ---------------------------------------------------------------- управление

def reset_for_resume(con, run_id: int) -> None:
    with tx(con):
        con.execute("UPDATE criterion_results SET status='pending' WHERE status='running' AND evaluation_id IN"
                    " (SELECT id FROM evaluations WHERE run_id=?)", (run_id,))
        con.execute("UPDATE evaluations SET status='pending' WHERE run_id=? AND status='running'", (run_id,))
        con.execute("UPDATE runs SET status='queued' WHERE id=? AND status='running'", (run_id,))


def retry_failed(con, run_id: int) -> int:
    with tx(con):
        n = con.execute("UPDATE criterion_results SET status='pending', error=NULL WHERE status='failed' AND evaluation_id IN"
                        " (SELECT id FROM evaluations WHERE run_id=?)", (run_id,)).rowcount
        if n:
            con.execute("UPDATE evaluations SET status='pending' WHERE run_id=? AND status IN ('partial','failed')", (run_id,))
            con.execute("UPDATE runs SET status='queued', units_failed=MAX(0, units_failed-?) WHERE id=? AND status!='cancelled'",
                        (n, run_id))
    return n


def cancel(con, run_id: int) -> None:
    con.execute("UPDATE runs SET status='cancelled' WHERE id=? AND status IN ('queued','running')", (run_id,))


def unfinished_runs(con) -> list[int]:
    return [r["id"] for r in con.execute("SELECT id FROM runs WHERE status IN ('queued','running') ORDER BY id")]


def run_progress(con, run_id: int) -> dict:
    r = dict(con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
    eta = None
    if r["status"] == "running" and r["started_at"] and r["units_done"]:
        from datetime import datetime
        elapsed = time.time() - datetime.fromisoformat(r["started_at"]).timestamp()
        left = r["units_total"] - r["units_done"] - r["units_failed"]
        eta = max(0.0, elapsed / max(1, r["units_done"] + r["units_failed"]) * left)
    r["eta_sec"] = eta
    return r


def run_sync(con, run_id: int, conv_concurrency: int | None = None) -> dict:
    return asyncio.run(process_run(con, run_id, conv_concurrency))
