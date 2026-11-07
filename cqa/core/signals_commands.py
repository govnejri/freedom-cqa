"""CLI сигналов: `cqa signals run --group DEV`, `cqa signals show [--group DEV]`."""
from __future__ import annotations

import asyncio
import time

import typer


def _con():
    from cqa.db.conn import connect, migrate
    con = connect()
    migrate(con)
    return con


def _fmt(v: float | None, nd: int = 1) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def register(app: typer.Typer) -> None:
    sig = typer.Typer(help="Сигналы диалогов: причины обращения, вопросы без ответа, риск ухода", no_args_is_help=True)
    app.add_typer(sig, name="signals")

    @sig.command("run")
    def run_cmd(
        group: str = typer.Option("DEV", help="Группа диалогов"),
        profile: str = typer.Option(None, help="Профиль модели (по умолчанию роль signals)"),
        uid: list[str] = typer.Option(None, help="Только эти диалоги (можно несколько раз)"),
        concurrency: int = typer.Option(8, help="Диалогов параллельно"),
        force: bool = typer.Option(False, help="Пересчитать и там, где сигналы уже есть"),
    ):
        """Один вызов модели на текущую оценку каждого диалога; уже посчитанные пропускаются."""
        from cqa.core.signals import run_signals

        con = _con()
        t0 = time.time()
        r = asyncio.run(run_signals(con, group=group, uids=uid or None, profile=profile, concurrency=concurrency,
                                    force=force))
        typer.echo(f"сигналы {group} ({r['profile']}): в очереди {r['total']}, готово {r['done']}, "
                   f"ошибок {r['failed']}, уже были {r['skipped']}, из кэша {r['cache_hits']}, "
                   f"стоимость ${r['cost_usd']:.4f}, {time.time() - t0:.1f} с")
        for e in r["errors"][:10]:
            typer.echo(f"  ! {e['uid']}: {e['error']}")

    @sig.command("show")
    def show_cmd(
        group: str = typer.Option(None, help="Группа диалогов (по умолчанию все)"),
        topic: str = typer.Option(None, help="Только эта тема"),
        limit: int = typer.Option(0, help="Вопросов на тему (0 — все)"),
    ):
        """Сводка по причинам обращения и вопросы без ответа по темам."""
        from cqa.core.signals import reasons_summary, unanswered_by_topic

        con = _con()
        rs = reasons_summary(con, group)
        typer.echo(f"диалогов с оценкой: {rs['n_evaluated']}, с сигналами: {rs['n_with_signals']}")
        if not rs["reasons"]:
            typer.echo("сигналов нет — cqa signals run --group …")
            return
        typer.echo("")
        typer.echo(f"{'тема':28} {'диал.':>5} {'ср.итог':>7} {'решено':>7} {'грубых':>6} {'уход':>4} {'без отв.':>8}")
        for x in rs["reasons"]:
            typer.echo(f"{x['topic'][:28]:28} {x['n_conversations']:5} {_fmt(x['avg_total']):>7} "
                       f"{x['resolved_share'] * 100:6.0f}% {x['gross_count']:6} {x['churn_count']:4} "
                       f"{x['n_unanswered_questions']:8}")

        un = unanswered_by_topic(con, group, topic)
        typer.echo("")
        typer.echo(f"вопросы без ответа: {un['total_questions']} в {un['n_conversations']} диалогах")
        for t in un["topics"]:
            typer.echo(f"\n## {t['topic']} — {t['n_questions']} вопр. в {t['n_conversations']} из "
                       f"{t['n_conversations_total']} диалогов; ср. итог темы {_fmt(t['avg_total'])}, "
                       f"с вопросами без ответа {_fmt(t['avg_total_unanswered'])}")
            qs = t["questions"][:limit] if limit else t["questions"]
            for q in qs:
                typer.echo(f"  [{q['status']:8}] {q['external_id']} {q['mid']:>4}  {q['text']}")
            if limit and len(t["questions"]) > limit:
                typer.echo(f"  … ещё {len(t['questions']) - limit}")
