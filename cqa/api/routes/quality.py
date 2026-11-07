"""Экран «Качество оценки»: точность прогона на эталоне, потолок (согласие двух разметчиков), стоимость, автотесты.

GET /quality?run=&split=all|tune|holdout&by=language — метрики cqa.quality.compare по критериям, MAE итога,
  ловушки, разрез; по умолчанию — последний прогон с оценками всех диалогов эталона.
GET /quality/runs — прогоны с краткими метриками (сравнение моделей и версий, облако против локальной модели).
GET /quality/autotests?profile= — отчёт автотестов (cqa.quality.autotest.report).

Вердикты прогона — исходные ответы модели (без апелляций): их сверяют с эталоном организаторов.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from cqa.api.deps import get_con, invalid, not_found
from cqa.checklist.model import ChecklistConfig
from cqa.quality.compare import column, compare, load
from cqa.settings import ROOT, load_models_config

router = APIRouter(tags=["quality"])

CEILING_FILES = (ROOT / "eval" / "gold_draft.csv", ROOT / "eval" / "gold_opus.csv")
SPLITS = ("all", "tune", "holdout")
BY_KEYS = ("language", "channel", "family")
EXCLUDED_KINDS = ("autotest", "dry_run", "trainer")   # сравнение моделей: основные прогоны и эксперименты
# Экран по умолчанию показывает основную оценку, а не эксперименты и пробные прогоны.
DEFAULT_EXCLUDED_KINDS = ("autotest", "experiment", "dry_run")
JUDGE_PURPOSES = ("judge", "retry")
_CRIT_KEYS = ("criterion", "column", "type", "n", "exact", "exact_rate", "compatible", "compatible_rate", "kappa",
              "near", "near_n", "mismatches", "hard")


# ---------------------------------------------------------------- таблицы разметки

def gold_table(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Эталон из gold_labels (если пуст — из eval/gold.csv) в формате таблицы compare: {id: {id, family, cNN}}."""
    from cqa.quality.gold import GOLD_PATH, gold_rows

    rows = gold_rows(con)
    if rows:
        return {r["id"]: r for r in rows}
    return dict(load(GOLD_PATH)) if GOLD_PATH.is_file() else {}


def run_table(con: sqlite3.Connection, run_id: int) -> dict[str, dict[str, Any]]:
    """Вердикты прогона (исходные, без апелляций): {external_id: {id, uid, language, channel, cNN}}."""
    out: dict[str, dict[str, Any]] = {}
    evs = con.execute(
        "SELECT e.id, c.uid, c.external_id, c.language, c.channel FROM evaluations e JOIN conversations c ON c.uid = e.uid"
        " WHERE e.run_id=? AND e.status IN ('done','partial') AND c.parent_uid IS NULL ORDER BY e.id", (run_id,)).fetchall()
    for ev in evs:
        row: dict[str, Any] = {"id": ev["external_id"], "uid": ev["uid"], "language": ev["language"],
                               "channel": ev["channel"], "evaluation_id": ev["id"]}
        for r in con.execute("SELECT criterion_id, verdict FROM criterion_results WHERE evaluation_id=? AND status='done'",
                             (ev["id"],)):
            row[column(r["criterion_id"])] = r["verdict"]
        out[ev["external_id"]] = row
    return out


def default_run(con: sqlite3.Connection, gold_ids: set[str]) -> int | None:
    """Последний прогон с готовыми оценками всех диалогов эталона; иначе — с наибольшим покрытием."""
    best: tuple[int, int] | None = None
    for r in con.execute(f"SELECT id FROM runs WHERE kind NOT IN ({','.join('?' * len(DEFAULT_EXCLUDED_KINDS))})"
                         " ORDER BY id DESC", DEFAULT_EXCLUDED_KINDS).fetchall():
        ext = {x[0] for x in con.execute(
            "SELECT DISTINCT c.external_id FROM evaluations e JOIN conversations c ON c.uid = e.uid"
            " WHERE e.run_id=? AND e.status='done' AND c.parent_uid IS NULL", (r["id"],))}
        cov = len(ext & gold_ids)
        if gold_ids and cov >= len(gold_ids):
            return int(r["id"])
        if cov and (best is None or cov > best[1]):
            best = (int(r["id"]), cov)
    return best[0] if best else None


def _metrics_cfg(cfg: ChecklistConfig, gold: dict[str, dict[str, Any]]) -> tuple[ChecklistConfig, list[str]]:
    """Критерии без колонки в эталоне выключаются (иначе каждый ответ — расхождение, а итог не считается)."""
    have = {k for r in gold.values() for k in r}
    warnings, crits = [], []
    for c in cfg.criteria:
        if c.enabled and column(c) not in have:
            warnings.append(f"критерия {c.id} нет в эталоне — в метриках не участвует")
            c = c.model_copy(update={"enabled": False})
        crits.append(c)
    if not any(c.enabled and c.weight > 0 for c in crits):
        return cfg, warnings
    return cfg.model_copy(update={"criteria": crits}), warnings


def _split_ids(con: sqlite3.Connection, gold: dict[str, dict[str, Any]], split: str) -> set[str] | None:
    from cqa.quality.gold import load_holdout, split_ids

    if split == "all":
        return None
    return set(split_ids(gold.values(), split, load_holdout()))


# ---------------------------------------------------------------- стоимость и время

def _prices() -> dict[str, dict[str, float | None]]:
    return {name: (p or {}).get("price") or {} for name, p in (load_models_config().get("profiles") or {}).items()}


def _call_cost(call: sqlite3.Row, prices: dict[str, dict]) -> tuple[float | None, bool]:
    """Стоимость вызова; если в журнале её нет (цена была неизвестна) — оценка по токенам и текущей цене профиля."""
    if call["cost_usd"] is not None:
        return float(call["cost_usd"]), False
    from cqa.llm.base import Usage
    from cqa.llm.pricing import cost

    c = cost(Usage(prompt_tokens=call["prompt_tokens"], cached_tokens=call["cached_tokens"],
                   completion_tokens=call["completion_tokens"], reasoning_tokens=call["reasoning_tokens"]),
             prices.get(call["profile"]))
    return c, c is not None


def _chunks(items: list[Any], n: int = 500) -> Iterable[list[Any]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _calls_for(con: sqlite3.Connection, cr_ids: Iterable[int]) -> dict[int, list[sqlite3.Row]]:
    """Вызовы судьи (и его повторы) по результатам критериев; перекрёстная проверка и сигналы — не оценка."""
    out: dict[int, list[sqlite3.Row]] = defaultdict(list)
    ids = sorted(set(cr_ids))
    q = ",".join("?" * len(JUDGE_PURPOSES))
    for part in _chunks(ids):
        for c in con.execute(f"SELECT * FROM llm_calls WHERE purpose IN ({q})"
                             f" AND criterion_result_id IN ({','.join('?' * len(part))})", (*JUDGE_PURPOSES, *part)):
            out[c["criterion_result_id"]].append(c)
    return out


def _secs(a: str | None, b: str | None) -> float | None:
    try:
        return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 1) if a and b else None
    except ValueError:
        return None


def run_cost(con: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """Затраты прогона и полная стоимость оценки диалога.

    run_cost_usd — потрачено в этом прогоне (без кэша и скопированных результатов).
    eval_cost_usd — сколько стоили все вызовы, на которых стоят результаты прогона (включая скопированные из
    прошлых прогонов и взятые из кэша по исходной цене); cost_per_dialog_usd = eval_cost_usd / диалогов.
    """
    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    crs = con.execute(
        "SELECT cr.id, cr.evaluation_id, cr.llm_call_id, cr.status FROM criterion_results cr"
        " JOIN evaluations e ON e.id = cr.evaluation_id WHERE e.run_id=?", (run_id,)).fetchall()
    own = _calls_for(con, [r["id"] for r in crs])
    src_of: dict[int, int] = {}
    need = [r["llm_call_id"] for r in crs if r["id"] not in own and r["llm_call_id"]]
    origin: dict[int, int] = {}
    for part in _chunks(need):
        for c in con.execute(f"SELECT id, criterion_result_id FROM llm_calls WHERE id IN ({','.join('?' * len(part))})",
                             part):
            origin[c["id"]] = c["criterion_result_id"]
    for r in crs:
        if r["id"] not in own and r["llm_call_id"] in origin:
            src_of[r["id"]] = origin[r["llm_call_id"]]
    src_calls = _calls_for(con, src_of.values())

    prices = _prices()
    per_eval: dict[int, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "latency": 0.0})
    tokens = {"prompt": 0, "cached": 0, "completion": 0, "reasoning": 0}
    estimated = unknown = False
    n_calls = copied = 0
    for r in crs:
        calls = own.get(r["id"])
        if not calls:
            calls = src_calls.get(src_of.get(r["id"], -1), [])
            copied += bool(calls)
        for c in calls:
            val, est = _call_cost(c, prices)
            estimated |= est
            if val is None:
                unknown = True
                val = 0.0
            per_eval[r["evaluation_id"]]["cost"] += val
            per_eval[r["evaluation_id"]]["latency"] += c["latency_ms"] or 0
            n_calls += 1
            tokens["prompt"] += c["prompt_tokens"] or 0
            tokens["cached"] += c["cached_tokens"] or 0
            tokens["completion"] += c["completion_tokens"] or 0
            tokens["reasoning"] += c["reasoning_tokens"] or 0
    n_dialogs = con.execute("SELECT COUNT(*) FROM evaluations WHERE run_id=? AND status IN ('done','partial')",
                            (run_id,)).fetchone()[0]
    total = sum(v["cost"] for v in per_eval.values())
    return {
        "run_cost_usd": round(run["cost_usd"] or 0, 6),
        "eval_cost_usd": round(total, 6),
        "cost_per_dialog_usd": round(total / n_dialogs, 6) if n_dialogs else None,
        "estimated": estimated,              # часть цен посчитана по токенам и текущим ценам профиля
        "incomplete": unknown,               # у части вызовов цена неизвестна
        "n_dialogs": n_dialogs,
        "n_llm_calls": n_calls,
        "units_total": run["units_total"], "units_cached": run["units_cached"], "units_copied": copied,
        "duration_sec": _secs(run["started_at"], run["finished_at"]),
        "latency_sec_per_dialog": round(sum(v["latency"] for v in per_eval.values()) / len(per_eval) / 1000, 1)
        if per_eval else None,
        "tokens": tokens,
    }


# ---------------------------------------------------------------- метрики

def _crit_view(m: dict[str, Any], titles: dict[str, str]) -> dict[str, Any]:
    return {**{k: m.get(k) for k in _CRIT_KEYS}, "title": titles.get(m["criterion"], m["criterion"])}


def _overall(criteria: list[dict[str, Any]]) -> dict[str, Any]:
    n = sum(m["n"] for m in criteria)
    kappas = [m["kappa"] for m in criteria if m.get("kappa") is not None and m["kappa"] == m["kappa"]]
    return {
        "labels": n,
        "exact_rate": sum(m["exact"] for m in criteria) / n if n else None,
        "compatible_rate": sum(m["compatible"] for m in criteria) / n if n else None,
        "kappa_mean": sum(kappas) / len(kappas) if kappas else None,
    }


def _total_view(t: dict[str, Any]) -> dict[str, Any]:
    return {"mae": t["mae"], "pairs": t["pairs"], "big": t["big"], "big_threshold": t["big_threshold"],
            "uncomputed": len(t["uncomputed"])}


def _view(res: dict[str, Any], titles: dict[str, str]) -> dict[str, Any]:
    crits = [_crit_view(m, titles) for m in res["criteria"]]
    return {"n": res["n"], "criteria": crits, "overall": _overall(res["criteria"]), "total": _total_view(res["total"])}


def ceiling(cfg: ChecklistConfig, only: set[str] | None) -> dict[str, Any] | None:
    """Потолок: согласие двух независимых разметчиков (eval/gold_draft.csv против eval/gold_opus.csv)."""
    a, b = CEILING_FILES
    if not (a.is_file() and b.is_file()):
        return None
    res = compare(load(a), load(b), cfg, only=only)
    titles = {c.id: c.title for c in cfg.criteria}
    return {"files": [str(a.relative_to(ROOT)), str(b.relative_to(ROOT))], **_view(res, titles)}


def _run_meta(con: sqlite3.Connection, run: sqlite3.Row) -> dict[str, Any]:
    prof = (load_models_config().get("profiles") or {}).get(run["judge_profile"]) or {}
    ver = con.execute("SELECT version FROM checklist_versions WHERE id=?", (run["checklist_version_id"],)).fetchone()
    provider = prof.get("provider")
    return {
        "id": run["id"], "kind": run["kind"], "status": run["status"], "profile": run["judge_profile"],
        "model": run["model"], "provider": provider, "local": provider == "openai_compat",
        "checklist_version_id": run["checklist_version_id"], "checklist_version": ver["version"] if ver else None,
        "prompt_version": run["prompt_version"], "created_at": run["created_at"], "started_at": run["started_at"],
        "finished_at": run["finished_at"],
    }


def quality_report(con: sqlite3.Connection, run_id: int | None = None, split: str = "all",
                   by: str | None = "language") -> dict[str, Any]:
    from cqa.checklist.store import get_active, get_version

    gold = gold_table(con)
    only = _split_ids(con, gold, split)
    if run_id is None:
        run_id = default_run(con, set(gold))
    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone() if run_id is not None else None
    if run_id is not None and run is None:
        raise KeyError(run_id)
    if run is not None:
        cfg = get_version(con, run["checklist_version_id"])[0]
    else:
        try:
            cfg = get_active(con)[0]
        except KeyError:
            from cqa.checklist.io import load_yaml
            from cqa.settings import get_settings
            cfg = load_yaml(get_settings().checklist_path)
    cfg, warnings = _metrics_cfg(cfg, gold)
    titles = {c.id: c.title for c in cfg.criteria}
    ceil = ceiling(cfg, only)
    out: dict[str, Any] = {
        "run": _run_meta(con, run) if run is not None else None,
        "split": split, "gold_dialogs": len(gold if only is None else [i for i in gold if i in only]),
        "ceiling": ceil, "warnings": warnings,
        "n": 0, "missing": [], "criteria": [], "overall": None, "total": None, "traps": [], "by": None,
        "diffs": [], "cost": None,
    }
    if run is None:
        return out
    table = run_table(con, run["id"])
    res = compare(gold, table, cfg, by=by, only=only)
    ceil_by = {m["criterion"]: m for m in (ceil or {}).get("criteria", [])}
    crits = []
    for m in res["criteria"]:
        v = _crit_view(m, titles)
        c = ceil_by.get(m["criterion"])
        v["ceiling_exact_rate"] = c["exact_rate"] if c else None
        v["ceiling_kappa"] = c["kappa"] if c else None
        crits.append(v)
    total = _total_view(res["total"])
    total["ceiling_mae"] = (ceil or {}).get("total", {}).get("mae")
    uid_of = {i: r.get("uid") for i, r in table.items()}
    out.update(
        n=res["n"], missing=res["missing"], criteria=crits, overall=_overall(res["criteria"]), total=total,
        traps=res["traps"],
        by=None if not res.get("by") else {
            "key": res["by"]["key"],
            "groups": [{"value": g["value"], **_view(g, titles)} for g in res["by"]["groups"]],
        },
        diffs=[{"id": d["id"], "uid": uid_of.get(d["id"]), "criterion": d["criterion"], "gold": d["a"],
                "model": d["b"], "compatible": d["compatible"]} for d in res["diffs"]],
        cost=run_cost(con, run["id"]),
        warnings=warnings + res["warnings"],
    )
    return out


def runs_summary(con: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """Прогоны (кроме автотестов) с краткими метриками на диалогах эталона, которые в них оценены."""
    from cqa.checklist.store import get_version

    gold = gold_table(con)
    out = []
    rows = con.execute(f"SELECT * FROM runs WHERE kind NOT IN ({','.join('?' * len(EXCLUDED_KINDS))})"
                       " ORDER BY id DESC LIMIT ?", (*EXCLUDED_KINDS, limit)).fetchall()
    cfgs: dict[int, ChecklistConfig] = {}
    for run in rows:
        table = {i: r for i, r in run_table(con, run["id"]).items() if i in gold}
        item = {**_run_meta(con, run), "n": len(table), "complete": bool(gold) and len(table) >= len(gold),
                "exact_rate": None, "compatible_rate": None, "kappa_mean": None, "mae": None,
                "by_criterion": {}, "traps": {}}
        if table:
            if run["checklist_version_id"] not in cfgs:
                cfgs[run["checklist_version_id"]] = _metrics_cfg(get_version(con, run["checklist_version_id"])[0], gold)[0]
            cfg = cfgs[run["checklist_version_id"]]
            res = compare({i: gold[i] for i in table}, table, cfg)
            ov = _overall(res["criteria"])
            item.update(exact_rate=ov["exact_rate"], compatible_rate=ov["compatible_rate"], kappa_mean=ov["kappa_mean"],
                        mae=res["total"]["mae"],
                        by_criterion={m["criterion"]: m["exact_rate"] for m in res["criteria"]},
                        traps={f"{t['criterion']}:{t['label']}": {"recall": t["recall"], "precision": t["precision"]}
                               for t in res["traps"] if t["kind"] == "flag"})
        cost = run_cost(con, run["id"])
        item.update(cost_usd=cost["run_cost_usd"], eval_cost_usd=cost["eval_cost_usd"],
                    cost_per_dialog_usd=cost["cost_per_dialog_usd"], cost_estimated=cost["estimated"],
                    duration_sec=cost["duration_sec"], latency_sec_per_dialog=cost["latency_sec_per_dialog"])
        out.append(item)
    return out


# ---------------------------------------------------------------- роуты

@router.get("/quality")
async def get_quality(
    run: int | None = Query(None, description="id прогона; по умолчанию последний со всеми диалогами эталона"),
    split: str = Query("all", description="all | tune | holdout (отложенные семьи из eval/holdout.txt)"),
    by: str | None = Query("language", description="разрез: language | channel | family"),
    con: sqlite3.Connection = Depends(get_con),
) -> dict[str, Any]:
    if split not in SPLITS:
        raise invalid("split", f"одно из {', '.join(SPLITS)}")
    if by is not None and by not in BY_KEYS:
        raise invalid("by", f"одно из {', '.join(BY_KEYS)}")
    try:
        return quality_report(con, run, split, by or None)
    except KeyError:
        raise not_found(f"прогон {run}") from None


@router.get("/quality/runs")
async def get_quality_runs(limit: int = Query(50, ge=1, le=500),
                           con: sqlite3.Connection = Depends(get_con)) -> list[dict[str, Any]]:
    return runs_summary(con, limit)


@router.get("/quality/autotests")
async def get_autotests(profile: str | None = Query(None, description="только оценки этим профилем"),
                        mutation: list[str] | None = Query(None, description="только эти изменения"),
                        con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    from cqa.quality.autotest import report

    try:
        return report(con, profile=profile, mutations=mutation)
    except ValueError as e:
        raise invalid("mutation", str(e)) from None
