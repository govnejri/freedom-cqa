"""Сравнение двух разметок чек-листа: эталон (A) против эталона или модели (B).

Формат CSV: id,[family,language,channel,…],c01..cNN; ячейка — ответ или «основной|альтернатива».
Лишние колонки (total, gross, notes…) игнорируются, BOM в начале файла допускается.
Критерии, веса и шкалы берутся из ChecklistConfig: колонка критерия — "c" + id.
Пустая ячейка — «нет ответа»: всегда расхождение, итог такой строки не рассчитывается.

Функции возвращают dict, пригодный для JSON; format_text() печатает прежний текстовый отчёт
eval/compare.py. CLI — main(argv) (его вызывают eval/compare.py и `cqa compare`).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from cqa.checklist.model import ChecklistConfig, Criterion

Row = Mapping[str, str | None]
Table = dict[str, Row]

BIG_DIFF = 5          # «расхождение итога больше N баллов»
NO_VALUE = "—"        # значение разреза, если колонки нет ни в одном файле


# ---------------------------------------------------------------- чтение

def load(path: str | Path) -> Table:
    """CSV → {id: строка}. utf-8-sig: экспорт системы пишет BOM."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["id"].strip(): r for r in csv.DictReader(f) if (r.get("id") or "").strip()}


def read_id_set(path: str | Path) -> set[str]:
    """Файл со списком id диалогов или семей сценариев: по одному на строку, «#» — комментарий."""
    out = set()
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def column(crit: Criterion | str) -> str:
    return "c" + (crit if isinstance(crit, str) else crit.id)


def options(cell: str | None) -> list[str]:
    """«Да|Нет» → ["Да", "Нет"]; пустая ячейка → [] (нет ответа)."""
    return [v.strip() for v in (cell or "").split("|") if v.strip()]


def primary(cell: str | None) -> str | None:
    opts = options(cell)
    return opts[0] if opts else None


def _has_col(table: Table, col: str) -> bool:
    return any(col in r for r in table.values())


def _attr(A: Table, B: Table, i: str, key: str) -> str | None:
    """Значение колонки из любого файла, где она заполнена (A приоритетнее)."""
    for t in (A, B):
        v = (t.get(i) or {}).get(key)
        if v and v.strip():
            return v.strip()
    return None


# ---------------------------------------------------------------- баллы

def scale_values(crit: Criterion) -> list[int]:
    return sorted(s.value for s in crit.scale or [])


def points(crit: Criterion, v: str | None) -> float | None:
    """Баллы до перераспределения; None — N/A или нет ответа."""
    if v is None or v == "N/A":
        return None
    if crit.type == "scale":
        return crit.weight * int(v) / crit.top
    if crit.type == "flag":
        return 0.0
    return float(crit.weight) if v == "Да" else 0.0


def total(cfg: ChecklistConfig, row: Row, pick: int = 0, channel: str | None = None) -> float | None:
    """Итог строки той же функцией, что и в системе (cqa.core.scoring.score).

    pick=0 — основной ответ, 1 — первая альтернатива (если есть). channel — канал диалога для
    фильтра `channels` (по умолчанию из колонки channel строки). None — итог не рассчитывается:
    нет применимых критериев с весом, нет ответа или ответ недопустим.
    """
    from cqa.core.scoring import score

    verdicts = {}
    for c in cfg.enabled_criteria():
        opts = options(row.get(column(c)))
        if not opts:
            return None
        verdicts[c.id] = opts[min(pick, len(opts) - 1)]
    channel = channel or (row.get("channel") or "").strip() or None
    try:
        return score(cfg, verdicts, channel=channel).total
    except ValueError:
        return None


# ---------------------------------------------------------------- метрики

def kappa(a: list, b: list) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def step_dist(crit: Criterion, x: str | None, y: str | None) -> int | None:
    """Расстояние в ступенях шкалы; None — не шкала, N/A, нет ответа или значение вне шкалы."""
    if crit.type != "scale" or x is None or y is None or "N/A" in (x, y):
        return None
    s = scale_values(crit)
    try:
        return abs(s.index(int(x)) - s.index(int(y)))
    except ValueError:
        return None


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _criterion_metrics(crit: Criterion, A: Table, B: Table, ids: list[str]) -> tuple[dict, list[dict]]:
    col = column(crit)
    pa = [primary(A[i].get(col)) for i in ids]
    pb = [primary(B[i].get(col)) for i in ids]
    compat_flags = [bool(set(options(A[i].get(col))) & set(options(B[i].get(col)))) for i in ids]
    exact = sum(x is not None and x == y for x, y in zip(pa, pb))
    compat = sum(compat_flags)
    n = len(ids)
    near = near_n = None
    if crit.type == "scale":
        d = [step_dist(crit, x, y) for x, y in zip(pa, pb)]
        d = [x for x in d if x is not None]
        near_n = len(d)
        near = _rate(sum(x <= 1 for x in d), len(d))
    diffs = []
    for i, x, y, ok in zip(ids, pa, pb, compat_flags):
        if x is None or x != y:
            diffs.append({"id": i, "criterion": crit.id, "column": col,
                          "a": A[i].get(col) or "", "b": B[i].get(col) or "", "compatible": ok})
    m = {
        "criterion": crit.id, "column": col, "type": crit.type, "n": n,
        "exact": exact, "exact_rate": _rate(exact, n),
        "compatible": compat, "compatible_rate": _rate(compat, n),
        "kappa": kappa([x or "" for x in pa], [y or "" for y in pb]) if n else None,
        "near": near, "near_n": near_n,
        "mismatches": n - exact, "hard": n - compat,
        "missing_a": sum(x is None for x in pa), "missing_b": sum(y is None for y in pb),
    }
    return m, diffs


def _totals(cfg: ChecklistConfig, A: Table, B: Table, ids: list[str]) -> dict:
    pairs, uncomputed = [], []
    for i in ids:
        ch = _attr(A, B, i, "channel")          # в эталоне колонки channel обычно нет — берём из экспорта
        x, y = total(cfg, A[i], channel=ch), total(cfg, B[i], channel=ch)
        if x is None or y is None:
            # оба None — итог не рассчитывается по правилам (все критерии с весом N/A), это совпадение
            if not (x is None and y is None and _no_weighted_applicable(cfg, A[i], B[i])):
                uncomputed.append({"id": i, "a": x, "b": y})
            continue
        pairs.append((i, x, y))
    err = [abs(x - y) for _, x, y in pairs]
    return {
        "pairs": len(pairs),
        "mae": sum(err) / len(err) if err else None,
        "big": sum(e > BIG_DIFF for e in err),
        "big_threshold": BIG_DIFF,
        "uncomputed": uncomputed,
        "rows": [{"id": i, "a": x, "b": y, "diff": round(y - x, 2)} for i, x, y in pairs],
    }


def _no_weighted_applicable(cfg: ChecklistConfig, *rows: Row) -> bool:
    """Все критерии с весом в обеих строках — N/A (итог законно не рассчитывается)."""
    for row in rows:
        for c in cfg.enabled_criteria():
            if c.weight > 0 and primary(row.get(column(c))) != "N/A":
                return False
    return True


def trap_labels(cfg: ChecklistConfig) -> list[tuple[Criterion, str, str]]:
    """Какие ответы считаем ловушками: флаг «Да», «Нет» у да/нет-критериев, N/A там, где он разрешён."""
    crits = cfg.enabled_criteria()
    out = [(c, "Да", "flag") for c in crits if c.type == "flag"]
    out += [(c, "Нет", "no") for c in crits if c.type == "yes_no"]
    out += [(c, "N/A", "na") for c in crits if c.na_allowed]
    return out


def _traps(cfg: ChecklistConfig, A: Table, B: Table, ids: list[str]) -> list[dict]:
    out = []
    for crit, label, kind in trap_labels(cfg):
        col = column(crit)
        tp = fp = fn = 0
        for i in ids:
            ra = primary(A[i].get(col)) == label
            rb = primary(B[i].get(col)) == label
            tp += ra and rb
            fp += rb and not ra
            fn += ra and not rb
        out.append({
            "criterion": crit.id, "column": col, "label": label, "kind": kind,
            "reference": tp + fn, "predicted": tp + fp, "tp": tp, "fp": fp, "fn": fn,
            "recall": _rate(tp, tp + fn), "precision": _rate(tp, tp + fp),
        })
    return out


def _metrics(cfg: ChecklistConfig, A: Table, B: Table, ids: list[str]) -> dict:
    crits, diffs = [], []
    for c in cfg.enabled_criteria():
        m, d = _criterion_metrics(c, A, B, ids)
        crits.append(m)
        diffs += d
    return {
        "n": len(ids),
        "criteria": crits,
        "total": _totals(cfg, A, B, ids),
        "hard": sum(not d["compatible"] for d in diffs),
        "diffs": diffs,
        "traps": _traps(cfg, A, B, ids),
    }


def compare(
    A: Table,
    B: Table,
    cfg: ChecklistConfig,
    *,
    by: str | None = None,
    only: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Сравнить две разметки. only / exclude — множества id диалогов или семей сценариев."""
    warnings: list[str] = []
    only_s = set(only) if only is not None else None
    excl_s = set(exclude or ())

    def keep(i: str) -> bool:
        fam = _attr(A, B, i, "family")
        if only_s is not None and i not in only_s and fam not in only_s:
            return False
        return i not in excl_s and fam not in excl_s

    universe = [i for i in sorted(set(A) | set(B)) if keep(i)]
    ids = [i for i in universe if i in A and i in B]
    missing = [i for i in universe if not (i in A and i in B)]

    # критерии, которых нет ни в одном файле, выключаем (и в итоге тоже); нет в одном — честные расхождения
    upd = []
    for c in cfg.criteria:
        col = column(c)
        in_a, in_b = _has_col(A, col), _has_col(B, col)
        if c.enabled and not in_a and not in_b:
            warnings.append(f"колонки {col} нет ни в одном файле — критерий {c.id} пропущен")
            c = c.model_copy(update={"enabled": False})
        elif c.enabled and not (in_a and in_b):
            warnings.append(f"колонки {col} нет в файле {'A' if not in_a else 'B'} — все ответы считаются расхождениями")
        upd.append(c)
    cfg = cfg.model_copy(update={"criteria": upd})

    res: dict[str, Any] = {
        "checklist": cfg.id,
        "criteria_ids": [c.id for c in cfg.enabled_criteria()],
        "filters": {"only": sorted(only_s) if only_s is not None else None, "exclude": sorted(excl_s) or None},
        "ids": ids,
        "missing": missing,
        **_metrics(cfg, A, B, ids),
        "by": None,
        "warnings": warnings,
    }
    if res["total"]["uncomputed"]:
        warnings.append(f"итог не рассчитан хотя бы в одном файле в {len(res['total']['uncomputed'])} диалогах"
                        " (нет ответа, недопустимый ответ или все критерии с весом N/A) — они не входят в MAE")
    if by:
        if not (_has_col(A, by) or _has_col(B, by)):
            warnings.append(f"колонки {by!r} нет ни в одном файле — разрез пустой")
        groups: dict[str, list[str]] = {}
        for i in ids:
            groups.setdefault(_attr(A, B, i, by) or NO_VALUE, []).append(i)
        res["by"] = {
            "key": by,
            "groups": [{"value": v, **_metrics(cfg, A, B, g)} for v, g in sorted(groups.items())],
        }
    return res


def compare_files(
    path_a: str | Path,
    path_b: str | Path,
    cfg: ChecklistConfig | None = None,
    **kw,
) -> dict[str, Any]:
    if cfg is None:
        from cqa.checklist.io import load_yaml
        from cqa.settings import get_settings
        cfg = load_yaml(get_settings().checklist_path)
    res = compare(load(path_a), load(path_b), cfg, **kw)
    res["files"] = {"a": str(path_a), "b": str(path_b)}
    return res


# ---------------------------------------------------------------- текст

def _pct(x: float | None, width: int = 6) -> str:
    return f"{x:{width}.0%}" if x is not None else f"{'—':>{width}}"


def format_text(res: dict[str, Any], *, diff: bool = False, traps: bool = False, by: bool = True) -> str:
    """Текстовый отчёт в формате прежнего eval/compare.py (+ разрез и ловушки, если запрошены)."""
    L: list[str] = []
    missing = res["missing"]
    L.append(f"диалогов: {res['n']}" + (f"  (нет в одном из файлов: {missing})" if missing else ""))
    for w in res["warnings"]:
        L.append(f"! {w}")
    if not res["n"]:
        L.append("нет общих диалогов для сравнения")
        return "\n".join(L) + "\n"
    L.append(f"{'крит':5} {'точно':>6} {'совм.':>6} {'kappa':>6} {'±1ст':>6}  расхождений")
    for m in res["criteria"]:
        near = f"{m['near']:6.0%}" if m["near"] is not None else ""
        L.append(f"{m['column']:5} {m['exact_rate']:6.0%} {m['compatible_rate']:6.0%} {m['kappa']:6.2f} {near:>6}  {m['mismatches']}")
    t = res["total"]
    if t["mae"] is not None:
        L.append(f"\nитог: MAE {t['mae']:.2f} балла, расхождение > {t['big_threshold']} баллов в {t['big']} из {t['pairs']} диалогов")
    else:
        L.append("\nитог: нет диалогов, где итог рассчитан в обоих файлах")
    L.append(f"жёсткие расхождения (ни основной, ни альтернативный ответ не совпали): {res['hard']}")

    if traps:
        L.append("\nловушки (A — эталон, B — проверка; по основному ответу)")
        L.append(f"{'крит':5} {'ответ':5} {'в A':>4} {'в B':>4} {'верно':>5} {'полнота':>8} {'точность':>8}")
        for r in res["traps"]:
            L.append(f"{r['column']:5} {r['label']:5} {r['reference']:4} {r['predicted']:4} {r['tp']:5} "
                     f"{_pct(r['recall'], 8)} {_pct(r['precision'], 8)}")

    if by and res.get("by"):
        cols = [m["column"] for m in res["criteria"]]
        L.append(f"\nпо {res['by']['key']} (точное совпадение основного ответа)")
        w = max([10] + [len(g["value"]) for g in res["by"]["groups"]])
        L.append(f"{'':{w}} {'n':>3} " + " ".join(f"{c:>5}" for c in cols) + f" {'MAE':>6} {'>' + str(BIG_DIFF):>4}")
        for g in res["by"]["groups"]:
            rates = " ".join(_pct(m["exact_rate"], 5) for m in g["criteria"])
            gt = g["total"]
            mae = f"{gt['mae']:6.2f}" if gt["mae"] is not None else f"{'—':>6}"
            L.append(f"{g['value']:{w}} {g['n']:3} {rates} {mae} {gt['big']:4}")

    if diff:
        L.append("\nid            крит  A            B            совместимо")
        for d in sorted(res["diffs"], key=lambda d: (d["compatible"], d["column"], d["id"])):
            L.append(f"{d['id']:13} {d['column']:5} {d['a']:12} {d['b']:12} {'да' if d['compatible'] else 'НЕТ'}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Сравнение двух разметок чек-листа (A — эталон, B — проверяемая)")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--diff", action="store_true", help="показать все расхождения")
    ap.add_argument("--checklist", help="YAML чек-листа (по умолчанию config/checklists/freedom-cqa.yaml)")
    ap.add_argument("--by", help="разрез по колонке: language | channel | family")
    ap.add_argument("--only", metavar="FILE", help="только эти id или семьи (по одному на строку)")
    ap.add_argument("--exclude", metavar="FILE", help="кроме этих id или семей (по одному на строку)")
    ap.add_argument("--traps", action="store_true", help="ловушки: флаги «Да», «Нет», N/A — полнота и точность")
    ap.add_argument("--json", metavar="OUT", help="записать полный результат в JSON (- — в stdout вместо текста)")
    return ap


def run(
    a: str | Path,
    b: str | Path,
    *,
    checklist: str | Path | None = None,
    diff: bool = False,
    by: str | None = None,
    only: str | Path | None = None,
    exclude: str | Path | None = None,
    traps: bool = False,
    json_out: str | Path | None = None,
) -> str:
    """Посчитать и вернуть текстовый отчёт; при json_out записать JSON (json_out="-" — вернуть JSON)."""
    cfg = None
    if checklist:
        from cqa.checklist.io import load_yaml
        cfg = load_yaml(checklist)
    res = compare_files(
        a, b, cfg, by=by,
        only=read_id_set(only) if only else None,
        exclude=read_id_set(exclude) if exclude else None,
    )
    if json_out:
        text = json.dumps(res, ensure_ascii=False, indent=2)
        if str(json_out) == "-":
            return text + "\n"
        Path(json_out).write_text(text, encoding="utf-8")
    return format_text(res, diff=diff, traps=traps)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.write(run(args.a, args.b, checklist=args.checklist, diff=args.diff, by=args.by,
                         only=args.only, exclude=args.exclude, traps=args.traps, json_out=args.json))
    return 0
