"""CLI судьи: `cqa evaluate UID` — оценка одного диалога по всем или выбранным критериям."""
from __future__ import annotations

import time

import typer


def _con():
    from cqa.db.conn import connect, migrate

    con = connect()
    migrate(con)
    return con


def _checklist(con):
    """Активная версия чек-листа из БД; если модуля версий нет — YAML из настроек."""
    from cqa.settings import get_settings

    path = get_settings().checklist_path
    try:
        from cqa.checklist.store import get_active, seed
    except ImportError:
        import yaml

        from cqa.checklist.model import ChecklistConfig
        with open(path, encoding="utf-8") as f:
            return ChecklistConfig.model_validate(yaml.safe_load(f))
    seed(con, path)
    cfg, _meta = get_active(con)
    return cfg


def _load_conversation(con, uid: str):
    try:
        from cqa.ingest.store import get_conversation
    except ImportError:
        raise typer.BadParameter("модуль cqa.ingest.store ещё не готов: загрузка диалогов из БД недоступна") from None
    from cqa.settings import get_settings

    candidates = [uid] if ":" in uid else [f"{get_settings().cqa_group_id}:{uid}", uid]
    for u in candidates:
        try:
            return get_conversation(con, u)
        except KeyError:
            continue
    raise typer.BadParameter(f"диалог {uid!r} не найден — загрузите набор: cqa load --file data/dev.json")


def _criteria(cfg, raw: str | None) -> list[str] | None:
    if not raw:
        return None
    ids = {c.id for c in cfg.criteria}
    out = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if p not in ids and p.zfill(2) in ids:
            p = p.zfill(2)
        if p not in ids:
            raise typer.BadParameter(f"нет критерия {p!r}; есть: {', '.join(sorted(ids))}")
        out.append(p)
    return out


def register(app: typer.Typer) -> None:
    @app.command("evaluate")
    def evaluate(
        uid: str = typer.Argument(..., help="uid диалога (DEV:p1-ru-022) или conversation_id"),
        profile: str = typer.Option(None, "--profile", "-p", help="профиль модели (по умолчанию роль judge)"),
        criteria: str = typer.Option(None, "--criteria", "-c", help="критерии через запятую: 03,08"),
        no_cache: bool = typer.Option(False, "--no-cache", help="не брать ответы из кэша"),
        reasoning: bool = typer.Option(False, "--reasoning", help="печатать разбор модели"),
    ) -> None:
        """Оценить диалог: вердикты, доказательства, итог, время и стоимость."""
        from cqa.core.judge import evaluate_conversation_sync
        from cqa.core.prompt import PROMPT_VERSION
        from cqa.llm import store
        from cqa.llm.base import CallContext
        from cqa.llm.commands import _profile

        con = _con()
        cfg = _checklist(con)
        conv = _load_conversation(con, uid)
        p = _profile(profile)
        crit_ids = _criteria(cfg, criteria)
        before = store.max_call_id(con)
        t0 = time.monotonic()
        verdicts, result = evaluate_conversation_sync(
            conv, cfg, p, crit_ids, con, CallContext(purpose="judge", uid=conv.uid, no_cache=no_cache))
        elapsed = time.monotonic() - t0

        typer.echo(f"{conv.uid} · {conv.channel} · {conv.language} · профиль {p.name} ({p.model}) · промпт {PROMPT_VERSION}")
        for cid, v in verdicts.items():
            crit = cfg.criterion(cid)
            head = f"\n[{cid}] {crit.title}: "
            if v.status == "failed":
                typer.echo(head + f"ОШИБКА — {v.error}")
                continue
            head += v.verdict + (f" ({v.category})" if v.category else "")
            if v.needs_review:
                head += "  [требует проверки]"
            typer.echo(head)
            for e in v.evidence:
                mark = "" if e.ok else " (цитата не найдена)"
                mark += " (ID исправлен)" if e.repaired else ""
                typer.echo(f"   {e.id}: «{e.quote}»{mark}")
            if v.checked_range:
                typer.echo(f"   проверено: {v.checked_range[0]}–{v.checked_range[1]}")
            if reasoning and v.reasoning:
                typer.echo(f"   разбор: {v.reasoning}")
            if v.explanation:
                typer.echo(f"   {v.explanation}")
            if v.how_to_fix:
                typer.echo(f"   как надо было: {v.how_to_fix}")
            if v.error:
                typer.echo(f"   ! {v.error}")

        typer.echo("")
        if result is not None:
            total = "не рассчитывается" if result.total is None else f"{result.total:.2f}"
            gross = f"да ({', '.join(result.gross_categories)})" if result.gross else "нет"
            typer.echo(f"Итог: {total}; грубые ошибки: {gross}")
        else:
            typer.echo("Итог: не рассчитан (оценены не все критерии или есть ошибки)")
        s = store.calls_summary(con, before, conv.uid)
        cost = "неизвестна" if (s["unknown_cost"] or 0) else f"${(s['cost'] or 0):.5f}"
        typer.echo(f"Время: {elapsed:.1f} с; вызовов: {s['n'] or 0} (из кэша {s['hits'] or 0}); стоимость: {cost}; "
                   f"токены in/cached/out/think: {s['p'] or 0}/{s['c'] or 0}/{s['o'] or 0}/{s['r'] or 0}")
