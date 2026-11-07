"""Автотесты судьи: мутанты с заранее известным ответом → прогон тем же runner → проверка → отчёт.

- generate(): базы — диалоги base_group (DEV), у которых по эталону основной ответ отличается от ожидаемого
  (для same_as_base — любые, где изменение применимо); порядок разнородный (языки, каналы, семьи сценариев).
  Мутант сохраняется через ingest.store.save_conversation, затем conversations.parent_uid / mutation_id и строка
  в таблице autotests. Набор тестов изменения = мутанты последней генерации (строки autotests).
- run(): прогон kind="autotest" на изменение. У абсолютного ожидания оцениваются только целевые критерии
  (criteria=[…]; невычисляемые остальные помечаются skipped, оценка остаётся partial), у same_as_base — все.
  После прогона у мутантов снимается current_evaluation_id (isolate): рабочие экраны их не видят.
- report(): последняя оценка каждого мутанта прогоном autotest (после его последнего изменения) → прошёл / нет.
  same_as_base: вердикт мутанта = вердикт исходного диалога той же моделью или одна из альтернатив эталона.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from typing import Any

from cqa.quality.mutate import (
    SAME_AS_BASE,
    AutotestConfig,
    MutationSpec,
    NotApplicable,
    applicable_channel,
    load_config,
    mutate,
)

log = logging.getLogger(__name__)

RUN_KIND = "autotest"
YES = "Да"
DONE_STATUSES = ("passed", "failed", "error")


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


def _select(cfg: AutotestConfig, mutations: Iterable[str] | None) -> list[MutationSpec]:
    if not mutations:
        return list(cfg.mutations.values())
    names = [m.strip() for m in mutations if m and m.strip()]
    bad = [m for m in names if m not in cfg.mutations]
    if bad:
        raise ValueError(f"нет изменений {', '.join(bad)}; есть: {', '.join(cfg.mutations)}")
    return [cfg.mutations[m] for m in names]


# ---------------------------------------------------------------- эталон и выборка

def gold_index(con: sqlite3.Connection, source: str = "gold") -> tuple[dict[str, dict[str, list[str]]], dict[str, str]]:
    """external_id → {критерий: [основной, альтернативы…]} и external_id → семья сценария."""
    opts: dict[str, dict[str, list[str]]] = {}
    fams: dict[str, str] = {}
    for r in con.execute("SELECT external_id, criterion_id, options, family FROM gold_labels WHERE source=?", (source,)):
        vals = [v.strip() for v in (r["options"] or "").split("|") if v.strip()]
        if vals:
            opts.setdefault(r["external_id"], {})[r["criterion_id"]] = vals
        if r["family"]:
            fams[r["external_id"]] = r["family"]
    return opts, fams


def diverse_order(items: Iterable[Any], stratum: Callable[[Any], Any],
                  family: Callable[[Any], str] | None = None) -> list[Any]:
    """Разнородный порядок: по кругу через страты (язык, канал), внутри страты — сначала невстреченные семьи.

    Порядок внутри страты сохраняется (детерминированно); первые N элементов — разнородная выборка из N.
    """
    buckets: dict[Any, list[Any]] = {}
    for it in items:
        buckets.setdefault(stratum(it), []).append(it)
    keys = sorted(buckets, key=str)
    out: list[Any] = []
    used: set[str] = set()
    while any(buckets[k] for k in keys):
        for k in keys:
            b = buckets[k]
            if not b:
                continue
            j = 0
            if family is not None:
                j = next((i for i, it in enumerate(b) if family(it) not in used), 0)
            it = b.pop(j)
            out.append(it)
            if family is not None:
                used.add(family(it))
    return out


def base_candidates(con: sqlite3.Connection, spec: MutationSpec, cfg: AutotestConfig, *,
                    gold: dict[str, dict[str, list[str]]], families: dict[str, str],
                    group: str | None = None) -> list:
    """Диалоги-кандидаты в базы изменения в разнородном порядке."""
    from cqa.ingest.store import get_conversation, list_uids

    out = []
    for uid in list_uids(con, group or cfg.base_group):
        conv = get_conversation(con, uid)
        if not applicable_channel(conv, spec):
            continue
        if spec.expect is not None:
            g = gold.get(conv.external_id)
            if not g:
                continue
            # исходный вердикт по эталону должен отличаться от ожидаемого
            if any(not g.get(cid) or g[cid][0] in exp for cid, exp in spec.expect.items()):
                continue
        out.append(conv)
    return diverse_order(out, stratum=lambda c: (c.language, c.channel),
                         family=lambda c: families.get(c.external_id, ""))


# ---------------------------------------------------------------- генерация

def save_mutant(con: sqlite3.Connection, mutant, base, spec: MutationSpec) -> bool:
    """Сохранить мутанта и строку autotests. True — содержимое новое или изменилось (старые оценки не в счёт)."""
    from cqa.ingest.store import save_conversation

    with _tx(con):
        prev = con.execute("SELECT content_hash FROM conversations WHERE uid=?", (mutant.uid,)).fetchone()
        changed = prev is None or prev["content_hash"] != mutant.content_hash
        save_conversation(con, mutant, None)
        con.execute("UPDATE conversations SET parent_uid=?, mutation_id=? WHERE uid=?", (base.uid, spec.id, mutant.uid))
        row = con.execute("SELECT id, expect_json FROM autotests WHERE uid=?", (mutant.uid,)).fetchone()
        old = json.loads(row["expect_json"]) if row else {}
        min_eval = int(old.get("min_eval_id") or 0)
        if changed:
            min_eval = int(con.execute("SELECT COALESCE(MAX(id), 0) FROM evaluations").fetchone()[0])
            if prev is not None:
                con.execute("UPDATE conversations SET current_evaluation_id=NULL WHERE uid=?", (mutant.uid,))
        expect = json.dumps({"mutation": spec.id, "expect": spec.expect_json(), "min_eval_id": min_eval},
                            ensure_ascii=False, sort_keys=True)
        if row:
            con.execute(
                "UPDATE autotests SET base_uid=?, mutation_id=?, expect_json=?,"
                " last_run_id=CASE WHEN ? THEN NULL ELSE last_run_id END,"
                " passed=CASE WHEN ? THEN NULL ELSE passed END WHERE id=?",
                (base.uid, spec.id, expect, int(changed), int(changed), row["id"]),
            )
        else:
            con.execute("INSERT INTO autotests(base_uid, uid, mutation_id, expect_json) VALUES (?,?,?,?)",
                        (base.uid, mutant.uid, spec.id, expect))
    return changed


def generate(con: sqlite3.Connection, *, per_mutation: int | None = None, mutations: Iterable[str] | None = None,
             config: AutotestConfig | None = None, base_group: str | None = None) -> list[dict[str, Any]]:
    """Создать мутантов. per_mutation — не больше N на изменение (None — все подходящие базы)."""
    from cqa.ingest.normalize import load_event_rules
    from cqa.ingest.store import upsert_dataset

    cfg = config or load_config()
    specs = _select(cfg, mutations)
    rules = load_event_rules()
    gold, fams = gold_index(con)
    upsert_dataset(con, cfg.group, "autotest", 0)
    out = []
    for spec in specs:
        made: list[str] = []
        skipped: list[dict[str, str]] = []
        cands = base_candidates(con, spec, cfg, gold=gold, families=fams, group=base_group)
        for conv in cands:
            if per_mutation and len(made) >= per_mutation:
                break
            try:
                m = mutate(conv, spec, cfg, rules)
            except NotApplicable as e:
                skipped.append({"uid": conv.uid, "reason": str(e)})
                continue
            save_mutant(con, m, conv, spec)
            made.append(m.uid)
        q = ",".join("?" * len(made))
        con.execute("DELETE FROM autotests WHERE mutation_id=?" + (f" AND uid NOT IN ({q})" if made else ""),
                    (spec.id, *made))
        langs = Counter(con.execute("SELECT language FROM conversations WHERE uid=?", (u,)).fetchone()[0] for u in made)
        out.append({"mutation": spec.id, "title": spec.title, "candidates": len(cands), "generated": len(made),
                    "by_language": dict(sorted(langs.items())), "skipped": skipped, "uids": made})
    total = con.execute("SELECT COUNT(*) FROM conversations WHERE group_id=?", (cfg.group,)).fetchone()[0]
    upsert_dataset(con, cfg.group, "autotest", total)
    return out


# ---------------------------------------------------------------- прогон

def _skip_untargeted(con: sqlite3.Connection, run_id: int, targets: list[str]) -> int:
    """Критерии вне целевых, которые нечем скопировать, не оцениваются: status='skipped' (оценка будет partial)."""
    q = ",".join("?" * len(targets))
    with _tx(con):
        n = con.execute(
            f"UPDATE criterion_results SET status='skipped' WHERE status='pending' AND criterion_id NOT IN ({q})"
            " AND evaluation_id IN (SELECT id FROM evaluations WHERE run_id=?)", (*targets, run_id)).rowcount
        con.execute("UPDATE runs SET units_total=MAX(0, units_total-?) WHERE id=?", (n, run_id))
    return n


def create_runs(con: sqlite3.Connection, *, profile: str, mutations: Iterable[str] | None = None,
                version_id: int | None = None, config: AutotestConfig | None = None) -> dict[str, int]:
    """Прогон kind="autotest" на каждое изменение с мутантами. → {mutation: run_id}."""
    from cqa.checklist.store import get_active, get_version
    from cqa.core.runner import create_run

    cfg = config or load_config()
    specs = _select(cfg, mutations)
    vid = version_id or get_active(con)[1]["id"]
    enabled = {c.id for c in get_version(con, vid)[0].enabled_criteria()}
    runs: dict[str, int] = {}
    for spec in specs:
        uids = [r[0] for r in con.execute("SELECT uid FROM autotests WHERE mutation_id=? ORDER BY uid", (spec.id,))]
        if not uids:
            continue
        targets = spec.targets
        if targets and any(c not in enabled for c in targets):
            log.warning("изменение %s: критериев %s нет среди включённых в версии %s — пропущено",
                        spec.id, [c for c in targets if c not in enabled], vid)
            continue
        rid = create_run(con, uids=uids, version_id=vid, profile_name=profile, criteria=targets, kind=RUN_KIND)
        if targets:
            _skip_untargeted(con, rid, targets)
        runs[spec.id] = rid
    return runs


async def process_runs(con: sqlite3.Connection, run_ids: Iterable[int], concurrency: int | None = None) -> list[dict]:
    from cqa.core.runner import process_run

    return list(await asyncio.gather(*(process_run(con, rid, concurrency) for rid in run_ids)))


def isolate(con: sqlite3.Connection, group: str) -> int:
    """Мутанты без текущей оценки: их не видят список, радар рисков, сводка и экспорт без фильтра по группе.

    Оценки мутантов читаются из прогонов kind=autotest (report → evaluation_id). Пока runner обновляет
    current_evaluation_id и для kind=autotest, ссылка снимается здесь.
    """
    return con.execute("UPDATE conversations SET current_evaluation_id=NULL WHERE group_id=? AND parent_uid IS NOT NULL"
                       " AND current_evaluation_id IS NOT NULL", (group,)).rowcount


def run(con: sqlite3.Connection, *, profile: str, mutations: Iterable[str] | None = None,
        version_id: int | None = None, concurrency: int | None = None,
        config: AutotestConfig | None = None) -> dict[str, Any]:
    """Создать и выполнить прогоны, записать итог в autotests. → {"runs": {...}, "report": report()}."""
    cfg = config or load_config()
    runs = create_runs(con, profile=profile, mutations=mutations, version_id=version_id, config=cfg)
    if runs:
        asyncio.run(process_runs(con, runs.values(), concurrency))
        isolate(con, cfg.group)
    rep = report(con, profile=profile, mutations=list(runs) or mutations, config=cfg)
    with _tx(con):
        for t in rep["tests"]:
            if t["run_id"] in runs.values():
                con.execute("UPDATE autotests SET last_run_id=?, passed=? WHERE uid=?",
                            (t["run_id"], None if t["status"] == "pending" else int(t["status"] == "passed"), t["uid"]))
    return {"runs": runs, "report": rep}


# ---------------------------------------------------------------- проверка

def _expect_of(spec: MutationSpec | None, stored: dict[str, Any]) -> dict[str, list[str]] | None:
    if spec is not None:
        return spec.expect
    raw = stored.get("expect")
    if raw == SAME_AS_BASE or not isinstance(raw, dict):
        return None
    return {str(k): [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in raw.items()}


def _latest_eval(con: sqlite3.Connection, uid: str, min_id: int, profile: str | None) -> sqlite3.Row | None:
    sql = ("SELECT e.* FROM evaluations e JOIN runs r ON r.id = e.run_id WHERE e.uid=? AND r.kind=? AND e.id>?"
           " AND e.status IN ('done','partial')")
    args: list[Any] = [uid, RUN_KIND, min_id]
    if profile:
        sql += " AND e.profile=?"
        args.append(profile)
    return con.execute(sql + " ORDER BY e.id DESC LIMIT 1", args).fetchone()


def _results(con: sqlite3.Connection, eval_id: int) -> dict[str, sqlite3.Row]:
    return {r["criterion_id"]: r for r in con.execute(
        "SELECT criterion_id, status, verdict, explanation FROM criterion_results WHERE evaluation_id=?", (eval_id,))}


def base_evaluation(con: sqlite3.Connection, uid: str, profile: str, model: str,
                    version_id: int) -> tuple[sqlite3.Row | None, str | None]:
    """Оценка исходного диалога для same_as_base: тем же профилем, иначе той же моделью, иначе любая
    (внутри — сначала той же версией чек-листа, затем новее). → (строка, "profile" | "model" | "other" | None)."""
    row = con.execute(
        "SELECT * FROM evaluations WHERE uid=? AND status='done'"
        " ORDER BY (profile=?) DESC, (model=?) DESC, (checklist_version_id=?) DESC, id DESC LIMIT 1",
        (uid, profile, model, version_id)).fetchone()
    if row is None:
        return None, None
    return row, "profile" if row["profile"] == profile else "model" if row["model"] == model else "other"


def _short(text: str | None, n: int = 400) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def evaluate_tests(con: sqlite3.Connection, *, profile: str | None = None, mutations: Iterable[str] | None = None,
                   config: AutotestConfig | None = None) -> list[dict[str, Any]]:
    """Результат каждого автотеста по последней оценке мутанта."""
    cfg = config or load_config()
    names = list(mutations or [])
    sql = ("SELECT a.*, c.language, c.channel, p.external_id AS base_external_id FROM autotests a"
           " JOIN conversations c ON c.uid = a.uid LEFT JOIN conversations p ON p.uid = a.base_uid")
    args: list[Any] = []
    if names:
        sql += f" WHERE a.mutation_id IN ({','.join('?' * len(names))})"
        args += names
    gold, _ = gold_index(con)
    out = []
    for a in con.execute(sql + " ORDER BY a.mutation_id, a.uid", args).fetchall():
        stored = json.loads(a["expect_json"] or "{}")
        spec = cfg.mutations.get(a["mutation_id"])
        expect = _expect_of(spec, stored)
        t: dict[str, Any] = {
            "uid": a["uid"], "base_uid": a["base_uid"], "base_external_id": a["base_external_id"],
            "mutation": a["mutation_id"], "language": a["language"], "channel": a["channel"],
            "expect": SAME_AS_BASE if expect is None else expect, "status": "pending",
            "evaluation_id": None, "run_id": None, "profile": None, "model": None, "checks": [],
            "mismatches": [], "base_evaluation_id": None,
        }
        ev = _latest_eval(con, a["uid"], int(stored.get("min_eval_id") or 0), profile)
        if ev is None:
            out.append(t)
            continue
        t.update(evaluation_id=ev["id"], run_id=ev["run_id"], profile=ev["profile"], model=ev["model"])
        res = _results(con, ev["id"])
        if expect is not None:
            checks = []
            for cid, exp in expect.items():
                r = res.get(cid)
                got = r["verdict"] if r is not None and r["status"] == "done" else None
                checks.append({"criterion_id": cid, "expected": exp, "got": got,
                               "status": r["status"] if r is not None else "missing",
                               "ok": got in exp, "explanation": _short(r["explanation"] if r else "")})
        else:
            base, match = base_evaluation(con, a["base_uid"], ev["profile"], ev["model"], ev["checklist_version_id"])
            base_res = _results(con, base["id"]) if base is not None else {}
            t["base_evaluation_id"] = base["id"] if base is not None else None
            t["base_match"] = match
            g = gold.get(a["base_external_id"] or "", {})
            checks = []
            for cid, r in sorted(res.items()):
                if r["status"] == "skipped":
                    continue
                b = base_res.get(cid)
                base_v = b["verdict"] if b is not None and b["status"] == "done" else None
                allowed = list(dict.fromkeys(([base_v] if base_v else []) + g.get(cid, [])))
                got = r["verdict"] if r["status"] == "done" else None
                checks.append({"criterion_id": cid, "expected": allowed, "base": base_v, "gold": g.get(cid, []),
                               "got": got, "status": r["status"],
                               "ok": got is not None and (got in allowed or not allowed),
                               "explanation": _short(r["explanation"])})
        t["checks"] = checks
        t["mismatches"] = [c for c in checks if not c["ok"]]
        if any(c["status"] == "failed" for c in checks):
            t["status"] = "error"
        elif any(c["got"] is None for c in checks) or not checks:
            t["status"] = "pending"
        else:
            t["status"] = "passed" if not t["mismatches"] else "failed"
        out.append(t)
    return out


# ---------------------------------------------------------------- отчёт

def _agg(tests: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(tests)
    passed = sum(t["status"] == "passed" for t in tests)
    failed = sum(t["status"] == "failed" for t in tests)
    errors = sum(t["status"] == "error" for t in tests)
    evaluated = passed + failed + errors
    return {"n": n, "evaluated": evaluated, "passed": passed, "failed": failed, "errors": errors,
            "pending": n - evaluated, "rate": passed / evaluated if evaluated else None}


def robustness(tests: list[dict[str, Any]]) -> dict[str, Any]:
    """Устойчивость same_as_base-изменения (инъекция, другое имя): что и куда сдвинулось."""
    done = [t for t in tests if t["status"] in DONE_STATUSES]
    by_crit: Counter = Counter()
    flips_yes: Counter = Counter()
    exact_same = 0
    attacked = 0
    for t in done:
        if all(c.get("base") is not None and c["got"] == c["base"] for c in t["checks"]):
            exact_same += 1
        yes = False
        for c in t["mismatches"]:
            by_crit[c["criterion_id"]] += 1
            if c["got"] == YES:
                flips_yes[c["criterion_id"]] += 1
                yes = True
        attacked += yes
    return {
        "evaluated": len(done),
        "robust": sum(t["status"] == "passed" for t in done),
        "exact_same": exact_same,                 # все вердикты буквально как у исходного (без допуска эталона)
        "changed_verdicts": sum(by_crit.values()),
        "changed_by_criterion": dict(sorted(by_crit.items())),
        "flipped_to_yes": attacked,               # мутантов, где хоть один критерий ушёл в «Да» вне допустимых
        "flipped_to_yes_by_criterion": dict(sorted(flips_yes.items())),
    }


def _runs_info(con: sqlite3.Connection, run_ids: set[int]) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    q = ",".join("?" * len(run_ids))
    return [dict(r) for r in con.execute(
        f"SELECT id, status, judge_profile AS profile, model, checklist_version_id, units_total, units_done,"
        f" units_failed, units_cached, cost_usd, created_at, finished_at FROM runs WHERE id IN ({q}) ORDER BY id",
        sorted(run_ids))]


def report(con: sqlite3.Connection, *, profile: str | None = None, mutations: Iterable[str] | None = None,
           config: AutotestConfig | None = None) -> dict[str, Any]:
    """Отчёт автотестов: доля пройденных по изменениям и языкам, проваленные, устойчивость к инъекции."""
    cfg = config or load_config()
    tests = evaluate_tests(con, profile=profile, mutations=mutations, config=cfg)
    langs = sorted({t["language"] for t in tests})
    by_mut = []
    order = [m for m in cfg.mutations if any(t["mutation"] == m for t in tests)]
    order += sorted({t["mutation"] for t in tests} - set(order))
    for m in order:
        sub = [t for t in tests if t["mutation"] == m]
        spec = cfg.mutations.get(m)
        item = {"mutation": m, "title": spec.title if spec else m,
                "expect": sub[0]["expect"] if sub else None, **_agg(sub),
                "by_language": {lg: _agg([t for t in sub if t["language"] == lg]) for lg in langs
                                if any(t["language"] == lg for t in sub)}}
        if item["expect"] == SAME_AS_BASE:
            item["robustness"] = robustness(sub)
        by_mut.append(item)
    run_ids = {t["run_id"] for t in tests if t["run_id"]}
    runs = _runs_info(con, run_ids)
    inj = [t for t in tests if t["mutation"] == "injection"]
    return {
        "profile": profile,
        "models": sorted({t["model"] for t in tests if t["model"]}),
        "total": _agg(tests),
        "by_mutation": by_mut,
        "by_language": [{"language": lg, **_agg([t for t in tests if t["language"] == lg])} for lg in langs],
        "injection": robustness(inj) if inj else None,
        "failed": [t for t in tests if t["status"] in ("failed", "error")],
        "tests": tests,
        "runs": runs,
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in runs), 6),
    }
