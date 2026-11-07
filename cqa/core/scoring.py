"""Итоговый балл по вердиктам (docs/implementation.md §3, docs/checklist.txt стр. 2).

1. Применимость: N/A только при na_allowed (иначе ValueError). Критерий с `channels`, в которые канал
   диалога не входит, — N/A независимо от вердикта.
2. Баллы до перераспределения: «Да» = вес, «Нет» = 0, шкала = вес × ступень / верхняя ступень. У flag вес 0.
3. Перераспределение: effective_weight = вес × max_total / Σ весов применимых; points = raw × max_total / Σ.
4. Итог = Σ points; точная арифметика Fraction → Decimal ROUND_HALF_UP до scoring.decimals.
   Нет применимых критериев с весом → total=None.
5. Секции: сумма по критериям секции после перераспределения.
6. gross — хотя бы один flag = «Да»; категории — из `categories` для таких флагов.

Значения по критериям и секциям округляются так же, как итог, но каждое отдельно: сумма секций может
отличаться от итога на единицу последнего знака на секцию.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Mapping

from cqa.checklist.model import ChecklistConfig, Criterion
from cqa.models import CriterionScore, ScoreResult, SectionScore

NA = "N/A"
YES = "Да"
NO = "Нет"


def round_half_up(x: Fraction, decimals: int) -> Decimal:
    """Fraction → Decimal с округлением ROUND_HALF_UP до `decimals` знаков."""
    with localcontext() as ctx:
        ctx.prec = 60
        d = Decimal(x.numerator) / Decimal(x.denominator)
        return d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _frac(x: float | int) -> Fraction:
    # repr(float) — кратчайшая запись: вес 10.5 → 21/2, а не двоичное приближение
    return Fraction(repr(float(x))) if isinstance(x, float) else Fraction(x)


def _raw_points(crit: Criterion, verdict: str) -> Fraction:
    """Баллы до перераспределения. ValueError — вердикт не из допустимых для критерия."""
    w = _frac(crit.weight)
    if crit.type == "scale":
        steps = {s.value for s in crit.scale or []}
        try:
            step = int(verdict)
        except ValueError:
            step = None
        if step is None or step not in steps or str(step) != verdict:
            raise ValueError(f"критерий {crit.id}: недопустимая ступень {verdict!r}, допустимо {crit.allowed_verdicts()}")
        return w * step / crit.top
    if verdict == YES:
        return w if crit.type != "flag" else Fraction(0)
    if verdict == NO:
        return Fraction(0)
    raise ValueError(f"критерий {crit.id}: недопустимый вердикт {verdict!r}, допустимо {crit.allowed_verdicts()}")


def score(
    cfg: ChecklistConfig,
    verdicts: Mapping[str, str],
    channel: str | None = None,
    categories: Mapping[str, str] | None = None,
) -> ScoreResult:
    """Посчитать итог по вердиктам включённых критериев.

    verdicts: id критерия → «Да» | «Нет» | «N/A» | ступень шкалы ("20"). Вердикты неизвестных
    и выключенных критериев игнорируются.
    channel: канал диалога; None — фильтр `channels` не применяется.
    categories: id флага → категория грубой ошибки (учитывается только при вердикте «Да»).
    """
    categories = categories or {}
    dec = cfg.scoring.decimals
    max_total = Fraction(cfg.scoring.max_total)

    rows: list[tuple[Criterion, str, bool, Fraction]] = []   # (критерий, вердикт, применим, raw)
    for crit in cfg.enabled_criteria():
        if crit.channels is not None and channel is not None and channel not in crit.channels:
            rows.append((crit, NA, False, Fraction(0)))
            continue
        if crit.id not in verdicts or verdicts[crit.id] is None:
            raise ValueError(f"нет вердикта по критерию {crit.id}")
        v = str(verdicts[crit.id]).strip()
        if v == NA:
            if not crit.na_allowed:
                raise ValueError(f"критерий {crit.id}: N/A не допускается")
            rows.append((crit, NA, False, Fraction(0)))
            continue
        rows.append((crit, v, True, _raw_points(crit, v)))

    denom = sum((_frac(c.weight) for c, _, ok, _ in rows if ok and c.type != "flag"), Fraction(0))

    def q(x: Fraction) -> float:
        return float(round_half_up(x, dec))

    crit_scores: list[CriterionScore] = []
    exact: dict[str, tuple[Fraction, Fraction]] = {}         # id → (points, effective_weight)
    for crit, v, ok, raw in rows:
        counted = ok and crit.type != "flag" and denom > 0
        eff = _frac(crit.weight) * max_total / denom if counted else Fraction(0)
        pts = raw * max_total / denom if counted else Fraction(0)
        exact[crit.id] = (pts, eff)
        crit_scores.append(CriterionScore(
            criterion_id=crit.id, verdict=v, applicable=ok, weight=float(crit.weight),
            effective_weight=q(eff), raw_points=q(raw), points=q(pts), max_points=q(eff),
        ))

    sections: list[SectionScore] = []
    for sec in cfg.sections:
        members = [exact[c.id] for c, *_ in rows if c.section == sec.id]
        if not members:
            continue
        sections.append(SectionScore(
            section_id=sec.id, title=sec.title,
            points=q(sum((p for p, _ in members), Fraction(0))),
            max_points=q(sum((e for _, e in members), Fraction(0))),
        ))

    total = q(sum((p for p, _ in exact.values()), Fraction(0))) if denom > 0 else None

    gross_cats: list[str] = []
    gross = False
    for crit, v, ok, _ in rows:
        if crit.type == "flag" and ok and v == YES:
            gross = True
            cat = (categories.get(crit.id) or "").strip()
            if cat and cat not in gross_cats:
                gross_cats.append(cat)

    return ScoreResult(total=total, criteria=crit_scores, sections=sections, gross=gross,
                       gross_categories=gross_cats)
