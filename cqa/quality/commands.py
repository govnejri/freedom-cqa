"""CLI: cqa gold import, cqa compare A B [флаги] (как eval/compare.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    gold_app = typer.Typer(help="Эталонная разметка", no_args_is_help=True)
    app.add_typer(gold_app, name="gold")

    @gold_app.command("import")
    def gold_import(
        path: Path = typer.Argument(None, help="CSV эталона (по умолчанию eval/gold.csv)"),
        source: str = typer.Option("gold", help="Метка источника в gold_labels"),
    ):
        """Импортировать эталон в таблицу gold_labels (строки этого источника заменяются)."""
        from cqa.db.conn import connect, migrate
        from cqa.quality.gold import GOLD_PATH, gold_rows, import_gold, load_holdout, split_ids

        path = path or GOLD_PATH
        con = connect()
        migrate(con)
        n = import_gold(con, path, source=source)
        rows = gold_rows(con, source)
        hold = load_holdout()
        typer.echo(f"{path}: {n['dialogs']} диалогов, {n['labels']} меток (source={source})")
        typer.echo(f"tune: {len(split_ids(rows, 'tune', hold))}, holdout: {len(split_ids(rows, 'holdout', hold))}"
                   f" (отложенные семьи: {', '.join(sorted(hold)) or '—'})")

    @app.command("compare")
    def compare_cmd(
        a: Path = typer.Argument(..., help="Эталон (CSV)"),
        b: Path = typer.Argument(..., help="Проверяемая разметка (CSV, например экспорт системы)"),
        diff: bool = typer.Option(False, "--diff", help="Показать все расхождения"),
        checklist: Path = typer.Option(None, "--checklist", help="YAML чек-листа (по умолчанию из настроек)"),
        by: str = typer.Option(None, "--by", help="Разрез: language | channel | family"),
        only: Path = typer.Option(None, "--only", help="Только эти id или семьи (файл, по одному на строку)"),
        exclude: Path = typer.Option(None, "--exclude", help="Кроме этих id или семей (файл)"),
        traps: bool = typer.Option(False, "--traps", help="Полнота и точность по флагам, «Нет» и N/A"),
        json_out: str = typer.Option(None, "--json", help="Записать полный результат в JSON (- — в stdout)"),
    ):
        """Сравнить две разметки чек-листа: точность, каппа, ±1 ступень, MAE итога, ловушки."""
        from cqa.quality.compare import run

        sys.stdout.write(run(a, b, checklist=checklist, diff=diff, by=by, only=only, exclude=exclude,
                             traps=traps, json_out=json_out))
