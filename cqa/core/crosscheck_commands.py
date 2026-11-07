"""CLI: cqa cross-check — перекрёстная проверка группы второй моделью и сводка на эталоне."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer


def _pct(x: float | None, width: int = 6) -> str:
    return f"{x:{width}.0%}" if x is not None else f"{'—':>{width}}"


def format_summary(s: dict[str, Any], gold_path: Path | None = None) -> str:
    """Текст сводки cross_check_summary(): согласие по критериям, точность судьи по корзинам, «требует проверки»."""
    L: list[str] = []
    who = ", ".join(s["profiles"]) or "—"
    L.append(f"перекрёстная проверка {s['group'] or 'все группы'}: {s['evaluations']} оценок; вторая модель: {who}")
    if not s["criteria"]:
        L.append("нет сохранённых результатов перекрёстной проверки")
        return "\n".join(L) + "\n"

    L.append("\nсогласие основного судьи и второй модели")
    L.append(f"{'крит':5} {'n':>4} {'согл.':>6} {'±1ст':>6} {'расх.':>6}")
    for m in [*s["criteria"], s["total"]]:
        name = "всего" if m["criterion_id"] == "all" else "c" + m["criterion_id"]
        L.append(f"{name:5} {m['n']:4} {_pct(m['agree_rate'])} {_pct(m['near_rate']) if m['near_rate'] is not None else ' ' * 6}"
                 f" {m['disagree']:6}")
    L.append(f"\nв «требует проверки» ушло бы: {s['review_verdicts']} вердиктов в {s['review_evaluations']} из"
             f" {s['evaluations']} диалогов")

    if s["gold"]:
        L.append(f"\nточность основного судьи на эталоне{f' ({gold_path})' if gold_path else ''}:"
                 " точно — основной ответ эталона, совм. — вердикт в наборе допустимых")
        L.append(f"{'':5} {'— согласны —':>20}  {'— расходятся —':>20}  {'2-я модель при расх.':>20}"
                 f"  {'ошибок судьи в «расходятся»':>28}")
        L.append(f"{'крит':5} {'n':>4} {'точно':>7} {'совм.':>7}  {'n':>4} {'точно':>7} {'совм.':>7}  {'':4}"
                 f" {'точно':>7} {'совм.':>7}  {'точно':>14} {'совм.':>13}")
        for m in [*s["criteria"], s["total"]]:
            name = "всего" if m["criterion_id"] == "all" else "c" + m["criterion_id"]
            g = m["gold"]
            a, d = g["agree"], g["disagree"]
            ce = f"{g['caught_exact']}/{g['errors_exact']} {_pct(g['caught_exact_rate'], 5)}"
            cc = f"{g['caught_compat']}/{g['errors_compat']} {_pct(g['caught_compat_rate'], 5)}"
            L.append(f"{name:5} {a['n']:4} {_pct(a['main_exact_rate'], 7)} {_pct(a['main_compat_rate'], 7)}"
                     f"  {d['n']:4} {_pct(d['main_exact_rate'], 7)} {_pct(d['main_compat_rate'], 7)}  {'':4}"
                     f" {_pct(d['second_exact_rate'], 7)} {_pct(d['second_compat_rate'], 7)}  {ce:>14} {cc:>13}")
        L.append("ошибок судьи в «расходятся» — сколько ошибок основного судьи (по эталону) попало в расхождения,"
                 " то есть было бы поймано отправкой расхождений человеку")
    return "\n".join(L) + "\n"


def register(app: typer.Typer) -> None:
    @app.command("cross-check")
    def cross_check_cmd(
        group: str = typer.Option("DEV", "--group", help="Группа диалогов (текущие оценки)"),
        criteria: str = typer.Option(None, "--criteria",
                                     help="Критерии через запятую или all; по умолчанию — с cross_check: true"),
        profile: str = typer.Option(None, "--profile", help="Профиль второй модели (по умолчанию роль cross_check)"),
        concurrency: int = typer.Option(4, "--concurrency", help="Диалогов параллельно"),
        gold: Path = typer.Option(None, "--gold", help="Эталон (по умолчанию eval/gold.csv, если есть)"),
        mark: bool = typer.Option(True, "--mark/--no-mark",
                                  help="При расхождении ставить «требует проверки» (--no-mark — только записать мнение)"),
        summary_only: bool = typer.Option(False, "--summary-only", help="Без вызовов модели: сводка по сохранённым"),
        json_out: str = typer.Option(None, "--json", help="Записать сводку в JSON (- — в stdout вместо текста)"),
    ):
        """Перекрёстная проверка: вторая модель независимо оценивает критерии; согласие и точность на эталоне."""
        from cqa.core.crosscheck import (
            CrossCheckError,
            cross_check_group,
            cross_check_summary,
            load_gold,
        )
        from cqa.db.conn import connect, migrate

        con = connect()
        migrate(con)
        crit_arg = criteria.split(",") if criteria else None
        if not summary_only:
            def progress(i: int, n: int, res: dict) -> None:
                if i == n or i % 10 == 0:
                    typer.echo(f"  {i}/{n} диалогов", err=True)

            try:
                run = asyncio.run(cross_check_group(con, group, crit_arg, concurrency, profile=profile, mark=mark,
                                                    on_done=progress))
            except CrossCheckError as e:
                raise typer.BadParameter(e.msg) from None
            failed = sum(t["failed"] for t in run["criteria"].values())
            calls = sum(t["checked"] + t["failed"] for t in run["criteria"].values())
            cost = f"${run['cost_usd']:.2f}" if run["cost_usd"] is not None else "цена неизвестна"
            typer.echo(f"проверено: {run['evaluations']} оценок, {calls} критериев ({run['calls_cached']} из кэша),"
                       f" сбоев {failed}, ошибок диалогов {run['errors']}; {cost}; {run['elapsed_sec']:.0f} с"
                       + ("" if mark else "; --no-mark: «требует проверки» не менялось"), err=True)
            crit_arg = crit_arg or sorted(run["criteria"])       # сводка — по тем же критериям, что проверялись
        gold_table = load_gold(gold)
        if gold is not None and gold_table is None:
            raise typer.BadParameter(f"нет файла эталона {gold}")
        try:
            summary = cross_check_summary(con, group, crit_arg, gold_table)
        except CrossCheckError as e:
            raise typer.BadParameter(e.msg) from None
        if json_out:
            text = json.dumps(summary, ensure_ascii=False, indent=2)
            if json_out == "-":
                sys.stdout.write(text + "\n")
                return
            Path(json_out).write_text(text, encoding="utf-8")
        from cqa.quality.gold import GOLD_PATH
        sys.stdout.write(format_summary(summary, (gold or GOLD_PATH) if gold_table is not None else None))
