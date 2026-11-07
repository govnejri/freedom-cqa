"""CLI ассистента руководителя: `cqa ask "вопрос" [--group DEV] [--json]`."""
from __future__ import annotations

import json
from pathlib import Path

import typer


def _fmt_cell(v) -> str:
    s = "—" if v is None else str(v)
    s = " ".join(s.split())
    return s if len(s) <= 60 else s[:59] + "…"


def _print(r: dict) -> None:
    typer.echo(f"\n{r['answer']}\n")
    typer.echo(f"тип: {r['kind']}  ·  стоимость ${r['cost_usd']:.4f}  ·  вызовы LLM {r['llm_call_ids'] or '—'}"
               f"  ·  {r['latency_ms'] / 1000:.1f} с")
    if r.get("sql"):
        typer.echo(f"\nSQL:\n{r['sql']}")
    if r.get("rows"):
        cols = r["columns"] or list(r["rows"][0])
        typer.echo(f"\nстрок: {r['row_count']}{'+' if r['truncated'] else ''} (показаны первые {len(r['rows'])})")
        typer.echo(" | ".join(cols))
        for row in r["rows"]:
            typer.echo(" | ".join(_fmt_cell(row.get(c)) for c in cols))
    if r["sources"]:
        typer.echo("\nисточники:")
        for s in r["sources"]:
            typer.echo(f"  [{s['external_id']}]({s['uid']}){' ' + s['mid'] if s['mid'] else ''} — {s['why']}")
    for w in r["warnings"]:
        typer.echo(f"  ! {w}")


def register(app: typer.Typer) -> None:
    @app.command("ask")
    def ask_cmd(
        question: str = typer.Argument(..., help="Вопрос обычным языком"),
        group: str = typer.Option(None, help="Только эта группа диалогов (по умолчанию все, кроме AUTOTEST/TRAINER)"),
        fake: bool = typer.Option(False, help="Профиль fake для всех вызовов (без сети)"),
        as_json: bool = typer.Option(False, "--json", help="Ответ JSON, как в API"),
        db: Path = typer.Option(None, help="БД с данными (по умолчанию settings.db_path); читается только в режиме ro"),
        journal_db: Path = typer.Option(None, help="Куда писать журнал и кэш вызовов LLM (по умолчанию — та же БД)"),
    ):
        """Ассистент руководителя: вопрос → цифра (SQL по представлениям) и ссылки на диалоги."""
        from cqa.db.conn import connect, migrate
        from cqa.features.assistant import ask_sync
        from cqa.settings import get_settings

        data_db = Path(db or get_settings().db_path)
        if not data_db.is_file():
            raise typer.BadParameter(f"нет БД {data_db}")
        jcon = connect(journal_db or data_db)
        if journal_db:
            migrate(jcon)
        try:
            profiles = {"router_profile": "fake", "strong_profile": "fake"} if fake else {}
            r = ask_sync(question, group, con=jcon, db_path=data_db, **profiles)
        finally:
            jcon.close()
        if as_json:
            typer.echo(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            _print(r)
