"""Сравнение двух разметок чек-листа (эталон vs эталон или эталон vs модель).

Формат CSV: id,c01..c08; ячейка — ответ или «основной|альтернатива».

    python eval/compare.py eval/gold_draft.csv eval/gold_opus.csv [--diff]
"""
import argparse
import csv
from collections import Counter

CRITERIA = ["c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08"]
WEIGHTS = {"c01": 10, "c02": 15, "c03": 20, "c04": 20, "c05": 15, "c06": 15, "c07": 5}
SCALES = {"c04": [0, 5, 10, 15, 20], "c06": [0, 4, 7, 10]}


def load(path):
    with open(path, encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def options(cell):
    return [v.strip() for v in cell.split("|")]


def points(c, v):
    if v == "N/A":
        return None
    if c in SCALES:
        return WEIGHTS[c] * int(v) / max(SCALES[c])
    return WEIGHTS[c] if v == "Да" else 0


def total(row, pick=0):
    got = app = 0
    for c in WEIGHTS:
        opts = options(row[c])
        p = points(c, opts[min(pick, len(opts) - 1)])
        if p is None:
            continue
        got += p
        app += WEIGHTS[c]
    return round(got * 100 / app, 2) if app else None


def kappa(a, b):
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def step_dist(c, x, y):
    if c not in SCALES or "N/A" in (x, y):
        return None
    s = SCALES[c]
    return abs(s.index(int(x)) - s.index(int(y)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--diff", action="store_true", help="показать все расхождения")
    args = ap.parse_args()
    A, B = load(args.a), load(args.b)
    ids = sorted(set(A) & set(B))
    missing = sorted(set(A) ^ set(B))
    print(f"диалогов: {len(ids)}" + (f"  (нет в одном из файлов: {missing})" if missing else ""))
    print(f"{'крит':5} {'точно':>6} {'совм.':>6} {'kappa':>6} {'±1ст':>6}  расхождений")
    diffs = []
    for c in CRITERIA:
        pa = [options(A[i][c])[0] for i in ids]
        pb = [options(B[i][c])[0] for i in ids]
        exact = sum(x == y for x, y in zip(pa, pb))
        compat = sum(bool(set(options(A[i][c])) & set(options(B[i][c]))) for i in ids)
        near = ""
        if c in SCALES:
            d = [step_dist(c, x, y) for x, y in zip(pa, pb)]
            d = [x for x in d if x is not None]
            near = f"{sum(x <= 1 for x in d) / len(d):6.0%}" if d else ""
        n = len(ids)
        print(f"{c:5} {exact / n:6.0%} {compat / n:6.0%} {kappa(pa, pb):6.2f} {near:>6}  {n - exact}")
        for i in ids:
            if options(A[i][c])[0] != options(B[i][c])[0]:
                diffs.append((i, c, A[i][c], B[i][c], bool(set(options(A[i][c])) & set(options(B[i][c])))))
    ta = [total(A[i]) for i in ids]
    tb = [total(B[i]) for i in ids]
    pairs = [(x, y) for x, y in zip(ta, tb) if x is not None and y is not None]
    mae = sum(abs(x - y) for x, y in pairs) / len(pairs)
    big = sum(abs(x - y) > 5 for x, y in pairs)
    print(f"\nитог: MAE {mae:.2f} балла, расхождение > 5 баллов в {big} из {len(pairs)} диалогов")
    hard = [d for d in diffs if not d[4]]
    print(f"жёсткие расхождения (ни основной, ни альтернативный ответ не совпали): {len(hard)}")
    if args.diff:
        print("\nid            крит  A            B            совместимо")
        for i, c, a, b, ok in sorted(diffs, key=lambda d: (d[4], d[1], d[0])):
            print(f"{i:13} {c:5} {a:12} {b:12} {'да' if ok else 'НЕТ'}")


if __name__ == "__main__":
    main()
