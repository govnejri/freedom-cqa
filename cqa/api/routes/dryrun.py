"""Пробный прогон новой версии чек-листа и «было/стало» (docs/plan.md §4 P1, §8 минута 4).

POST /checklist-versions/{vid}/dry-run — прогон kind="dry_run" на выборке диалогов только по изменённым критериям
  (остальные runner копирует из предыдущей оценки). Исполняет воркер API. Текущая оценка диалога на момент запуска
  запоминается в params_json.baseline прогона — это «было» для /runs/{id}/diff.
GET /runs/{id}/diff — по каждому диалогу прогона: старые и новые вердикты, старый и новый итог, сводка.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from cqa.api.deps import (
    FAKE_PROFILE,
    get_con,
    invalid,
    is_fake,
    not_found,
    profile_names,
    resolve_role,
)
from cqa.api.routes.views import run_dto
from cqa.api.schemas import RunDTO
from cqa.checklist.model import ChecklistConfig

router = APIRouter(tags=["dry-run"])

DRY_RUN_KIND = "dry_run"
PROMPT_WIDE_FIELDS = ("general_rules", "principles", "channel_rules")   # меняют промпт всех критериев
AUTOTEST_GROUP = "AUTOTEST"


class DryRunBody(BaseModel):
    uids: list[str] | None = None
    sample: int = Field(10, ge=1, le=500)
    criteria: list[str] | None = None
    group: str | None = None          # None — текущая группа (последний загруженный набор)
    profile: str | None = None        # None — роль judge


# ---------------------------------------------------------------- что изменилось

def changed_criteria(con: sqlite3.Connection, cfg: ChecklistConfig, meta: dict[str, Any]) -> tuple[list[str], int | None]:
    """Критерии версии, изменённые относительно активной (для самой активной — относительно родителя).

    Изменение общих полей промпта (general_rules, principles, channel_rules) затрагивает все критерии.
    → (id включённых критериев в порядке конфига, id версии, с которой сравнивали).
    """
    from cqa.checklist.store import diff, get_active, get_version

    try:
        base_cfg, base_meta = get_active(con, meta["checklist_id"])
    except KeyError:
        return [c.id for c in cfg.enabled_criteria()], None
    if base_meta["id"] == meta["id"]:
        if not meta.get("parent_id"):
            return [], None
        base_cfg, base_meta = get_version(con, meta["parent_id"])
    changes = diff(base_cfg, cfg)
    enabled = [c.id for c in cfg.enabled_criteria()]
    if any(ch["scope"] == "config" and ch["id"] in PROMPT_WIDE_FIELDS for ch in changes):
        return enabled, base_meta["id"]
    ids = {ch["id"] for ch in changes if ch["scope"] == "criterion" and ch["change"] in ("added", "changed")}
    return [c for c in enabled if c in ids], base_meta["id"]


# ---------------------------------------------------------------- выборка диалогов

def current_group(con: sqlite3.Connection) -> str | None:
    """Последний загруженный набор с диалогами (кроме автотестов)."""
    r = con.execute(
        "SELECT d.group_id FROM datasets d WHERE d.source != 'autotest' AND d.group_id != ?"
        " AND EXISTS (SELECT 1 FROM conversations c WHERE c.group_id = d.group_id)"
        " ORDER BY d.fetched_at DESC, d.group_id LIMIT 1", (AUTOTEST_GROUP,)).fetchone()
    return r["group_id"] if r else None


def sample_uids(con: sqlite3.Connection, group: str, n: int) -> list[str]:
    """n разнородных диалогов группы: по кругу через языки и каналы, внутри — разные семьи сценариев (по эталону).

    Сначала диалоги с текущей оценкой (есть «было»), затем остальные.
    """
    from cqa.quality.autotest import diverse_order

    fams = {r["external_id"]: r["family"] for r in con.execute(
        "SELECT external_id, MAX(family) AS family FROM gold_labels GROUP BY external_id")}
    rows = con.execute(
        "SELECT uid, external_id, language, channel, current_evaluation_id FROM conversations"
        " WHERE group_id=? AND parent_uid IS NULL ORDER BY external_id", (group,)).fetchall()
    out: list[str] = []
    for part in ([r for r in rows if r["current_evaluation_id"]], [r for r in rows if not r["current_evaluation_id"]]):
        ordered = diverse_order(part, stratum=lambda r: (r["language"], r["channel"]),
                                family=lambda r: fams.get(r["external_id"]) or r["external_id"])
        out += [r["uid"] for r in ordered]
    return out[:n]


def _profile(request: Request, con: sqlite3.Connection, name: str | None) -> str:
    if is_fake(request):
        return FAKE_PROFILE
    if name:
        if name not in profile_names():
            raise invalid("profile", f"нет профиля {name!r}; есть: {', '.join(profile_names())}")
        return name
    return resolve_role(con, "judge", False)


@router.post("/checklist-versions/{vid}/dry-run", response_model=RunDTO)
async def dry_run(vid: int, request: Request, body: DryRunBody | None = None,
                  con: sqlite3.Connection = Depends(get_con)):
    """Пробный прогон версии: выборка диалогов × изменённые критерии. Текущие оценки диалогов не меняются."""
    from cqa.checklist.store import get_version
    from cqa.core.runner import create_run, run_progress

    body = body or DryRunBody()
    try:
        cfg, meta = get_version(con, vid)
    except KeyError:
        raise not_found(f"версия чек-листа {vid}") from None

    enabled = [c.id for c in cfg.enabled_criteria()]
    compared_with = None
    if body.criteria:
        bad = [c for c in body.criteria if c not in enabled]
        if bad:
            raise invalid("criteria", f"нет включённых критериев {', '.join(bad)}; есть: {', '.join(enabled)}")
        criteria = list(dict.fromkeys(body.criteria))
    else:
        criteria, compared_with = changed_criteria(con, cfg, meta)
        if not criteria:
            raise invalid("criteria", "версия не отличается от активной по критериям — укажите criteria явно")

    if body.uids:
        uids = list(dict.fromkeys(body.uids))
        known = {r[0] for r in con.execute(
            f"SELECT uid FROM conversations WHERE uid IN ({','.join('?' * len(uids))})", uids)}
        missing = [u for u in uids if u not in known]
        if missing:
            raise invalid("uids", f"нет диалогов: {', '.join(missing[:10])}")
        group = None
    else:
        group = body.group or current_group(con)
        if not group:
            raise invalid("group", "нет загруженных диалогов")
        uids = sample_uids(con, group, body.sample)
        if not uids:
            raise invalid("group", f"в группе {group} нет диалогов")

    profile = _profile(request, con, body.profile)
    baseline = {r["uid"]: r["current_evaluation_id"] for r in con.execute(
        f"SELECT uid, current_evaluation_id FROM conversations WHERE uid IN ({','.join('?' * len(uids))})", uids)}
    run_id = create_run(con, uids=uids, version_id=vid, profile_name=profile, criteria=criteria, kind=DRY_RUN_KIND)
    params = json.loads(con.execute("SELECT params_json FROM runs WHERE id=?", (run_id,)).fetchone()[0] or "{}")
    params.update(baseline=baseline, compared_with_version_id=compared_with, group=group,
                  sample=None if body.uids else body.sample)
    con.execute("UPDATE runs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), run_id))
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.wake(run_id)
    return run_dto(run_progress(con, run_id))


# ---------------------------------------------------------------- было / стало

def _results(con: sqlite3.Connection, eval_id: int | None) -> dict[str, sqlite3.Row]:
    if not eval_id:
        return {}
    return {r["criterion_id"]: r for r in con.execute(
        "SELECT criterion_id, status, verdict, category, explanation, evidence_json, cache_hit FROM criterion_results"
        " WHERE evaluation_id=?", (eval_id,))}


def _eval(con: sqlite3.Connection, eval_id: int | None) -> sqlite3.Row | None:
    if not eval_id:
        return None
    return con.execute("SELECT * FROM evaluations WHERE id=?", (eval_id,)).fetchone()


def _baseline_id(con: sqlite3.Connection, run_id: int, ev: sqlite3.Row, snapshot: dict[str, Any],
                 baseline: str, baseline_version: int | None) -> int | None:
    uid = ev["uid"]
    if baseline_version is not None:
        r = con.execute(
            "SELECT id FROM evaluations WHERE uid=? AND checklist_version_id=? AND status='done'"
            " AND COALESCE(run_id, -1) != ? ORDER BY id DESC LIMIT 1", (uid, baseline_version, run_id)).fetchone()
        return r["id"] if r else None
    if baseline == "current" and snapshot.get(uid):
        return int(snapshot[uid])
    cur = ev["current_evaluation_id"]
    if cur and cur != ev["id"]:
        own = con.execute("SELECT run_id FROM evaluations WHERE id=?", (cur,)).fetchone()
        if own is not None and own["run_id"] != run_id:
            return int(cur)
    # текущая оценка — сама пробная (runner без правки для dry_run) или её нет: последняя готовая до прогона
    r = con.execute("SELECT id FROM evaluations WHERE uid=? AND id<? AND status='done' AND COALESCE(run_id, -1) != ?"
                    " ORDER BY id DESC LIMIT 1", (uid, ev["id"], run_id)).fetchone()
    return r["id"] if r else None


def _evidence_ids(text: str | None) -> list[str]:
    try:
        return [e.get("id") for e in json.loads(text or "[]") if isinstance(e, dict) and e.get("id")]
    except ValueError:
        return []


def run_diff(con: sqlite3.Connection, run_id: int, baseline: str = "current",
             baseline_version: int | None = None) -> dict[str, Any]:
    from cqa.checklist.store import get_version
    from cqa.core.runner import run_progress

    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(run_id)
    params = json.loads(run["params_json"] or "{}")
    snapshot = params.get("baseline") or {}
    cfg, meta = get_version(con, run["checklist_version_id"])
    titles = {c.id: c.title for c in cfg.criteria}
    enabled = [c.id for c in cfg.enabled_criteria()]
    requested = list(params.get("criteria") or enabled)
    order = {cid: i for i, cid in enumerate([c.id for c in cfg.criteria])}

    evs = con.execute(
        "SELECT e.*, c.external_id, c.language, c.channel, c.current_evaluation_id FROM evaluations e"
        " JOIN conversations c ON c.uid = e.uid WHERE e.run_id=? ORDER BY c.group_id, c.external_id", (run_id,)).fetchall()
    dialogs = []
    new_dist: dict[str, Counter] = {c: Counter() for c in requested}
    old_dist: dict[str, Counter] = {c: Counter() for c in requested}
    transitions: dict[str, Counter] = {c: Counter() for c in requested}
    compared = changed_pairs = dialogs_changed = gross_changed = completed = 0
    deltas: list[float] = []
    for ev in evs:
        base_id = _baseline_id(con, run_id, ev, snapshot, baseline, baseline_version)
        base = _eval(con, base_id)
        new_res, old_res = _results(con, ev["id"]), _results(con, base_id)
        done = ev["status"] == "done"
        completed += done
        # изменённые критерии + любые другие, у которых вердикт всё же разошёлся (например, не скопировался)
        extra = {c for c, n in new_res.items() if c not in requested and n["status"] == "done"
                 and c in old_res and old_res[c]["verdict"] != n["verdict"]}
        ids = sorted(set(requested) | extra, key=lambda c: order.get(c, 999))
        rows, changed = [], []
        for cid in ids:
            n, o = new_res.get(cid), old_res.get(cid)
            nv = n["verdict"] if n is not None and n["status"] == "done" else None
            ov = o["verdict"] if o is not None and o["status"] == "done" else None
            is_changed = nv is not None and base is not None and nv != ov
            if is_changed:
                changed.append(cid)
            rows.append({
                "criterion_id": cid, "title": titles.get(cid, cid), "requested": cid in requested,
                "old": ov, "new": nv, "status": n["status"] if n is not None else None, "changed": is_changed,
                "old_explanation": (o["explanation"] or "") if o is not None else "",
                "new_explanation": (n["explanation"] or "") if n is not None else "",
                "new_evidence": _evidence_ids(n["evidence_json"]) if n is not None else [],
            })
            if cid in requested and nv is not None:
                new_dist[cid][nv] += 1
                if ov is not None:
                    old_dist[cid][ov] += 1
                    compared += 1
                    if nv != ov:
                        changed_pairs += 1
                        transitions[cid][f"{ov} → {nv}"] += 1
        old_total = base["total"] if base is not None else None
        new_total = ev["total"] if done else None
        delta = round(new_total - old_total, 2) if old_total is not None and new_total is not None else None
        if delta is not None:
            deltas.append(delta)
        old_gross = bool(base["gross"]) if base is not None and base["status"] == "done" else None
        new_gross = bool(ev["gross"]) if done else None
        if old_gross is not None and new_gross is not None and old_gross != new_gross:
            gross_changed += 1
        dialogs_changed += bool(changed)
        dialogs.append({
            "uid": ev["uid"], "external_id": ev["external_id"], "language": ev["language"], "channel": ev["channel"],
            "status": ev["status"], "evaluation_id": ev["id"], "baseline_evaluation_id": base_id,
            "baseline_checklist_version_id": base["checklist_version_id"] if base is not None else None,
            "old_total": old_total, "new_total": new_total, "delta": delta,
            "old_final_total": base["final_total"] if base is not None else None,
            "old_gross": old_gross, "new_gross": new_gross,
            "changed_criteria": changed, "criteria": rows,
        })

    versions = {r["id"]: r["version"] for r in con.execute("SELECT id, version FROM checklist_versions")}
    return {
        "run": run_dto(run_progress(con, run_id)).model_dump(),
        "checklist_version_id": run["checklist_version_id"], "checklist_version": meta["version"],
        "compared_with_version_id": params.get("compared_with_version_id"),
        "compared_with_version": versions.get(params.get("compared_with_version_id")),
        "baseline": f"version:{baseline_version}" if baseline_version is not None else baseline,
        "criteria": requested,
        "summary": {
            "dialogs": len(evs), "completed": completed,
            "with_baseline": sum(d["baseline_evaluation_id"] is not None for d in dialogs),
            "verdicts_compared": compared, "verdicts_changed": changed_pairs,
            "dialogs_changed": dialogs_changed, "gross_changed": gross_changed,
            "mean_delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
            "mean_abs_delta": round(sum(abs(d) for d in deltas) / len(deltas), 2) if deltas else None,
            "new_verdicts": {c: dict(new_dist[c]) for c in requested},
            "old_verdicts": {c: dict(old_dist[c]) for c in requested},
            "transitions": {c: dict(transitions[c]) for c in requested if transitions[c]},
        },
        "dialogs": dialogs,
    }


@router.get("/runs/{run_id}/diff")
async def get_run_diff(
    run_id: int,
    baseline: str = Query("current", description="current — текущая оценка диалога на момент запуска прогона"),
    baseline_version: int | None = Query(None, description="сравнить с последней оценкой этой версией чек-листа"),
    con: sqlite3.Connection = Depends(get_con),
) -> dict[str, Any]:
    """«Было/стало» по диалогам прогона: вердикты изменённых критериев, итог, сводка."""
    if baseline != "current":
        raise invalid("baseline", "поддерживается baseline=current (или baseline_version=<id версии>)")
    try:
        return run_diff(con, run_id, baseline, baseline_version)
    except KeyError:
        raise not_found(f"прогон {run_id}") from None
