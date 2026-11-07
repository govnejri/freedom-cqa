"""CLI P3: `cqa trainer start|reply|finish|show|list`, `cqa tutor ask|best`."""
from __future__ import annotations

import asyncio
import json
import textwrap

import typer


def _con():
    from cqa.db.conn import connect, migrate
    con = connect()
    migrate(con)
    return con


def _profile(con, role: str, override: str | None):
    """Профиль: --profile → роль из models.yaml с переопределениями из настроек (как в API)."""
    from cqa.settings import get_profile

    if override:
        return get_profile(override)
    from cqa.api.deps import effective_roles
    return get_profile(effective_roles(con, False)[role])


def _fail(e: Exception) -> None:
    typer.echo(f"ошибка: {e}", err=True)
    raise typer.Exit(1)


_ROLE = {"client": "КЛИЕНТ", "operator": "ОПЕРАТОР", "bot": "БОТ", "system": "СИСТЕМА"}


def _print_messages(msgs: list[dict]) -> None:
    for m in msgs:
        mark = {"trainee": "✎", "ai": "◆"}.get(m.get("source"), " ")
        role = _ROLE.get(m["speaker"], m["speaker"].upper())
        typer.echo(textwrap.fill(f"{mark} {m['mid']:>4} {m['ts']} {role:9} {m['text']}", width=120,
                                 subsequent_indent=" " * 26))


def _print_persona(p: dict) -> None:
    typer.echo(f"Сценарий: {p.get('title')}")
    typer.echo(f"  цель: {p.get('goal')}")
    for k, label in (("already_told", "уже сообщил"), ("reveal_if_asked", "скажет, если спросят")):
        for x in p.get(k) or []:
            typer.echo(f"  {label}: {x}")
    typer.echo(f"  характер: {p.get('character')}")
    typer.echo(f"  язык: {p.get('language_label') or p.get('language')}")
    typer.echo(f"  доволен, когда: {p.get('satisfied_when')}")
    typer.echo(f"  уходит, когда: {p.get('leaves_when')}")
    if p.get("hidden_question"):
        typer.echo(f"  скрытый вопрос: {p['hidden_question']}")


def _print_comparison(cmp: dict | None) -> None:
    if not cmp:
        typer.echo("оценки ещё нет")
        return
    typer.echo(f"\nБыло/стало: {cmp['summary']}")
    typer.echo(f"{'крит.':5} {'было':>6} {'стало':>6}  изменение")
    for c in cmp["criteria"]:
        b, a = c["before"] or {}, c["after"] or {}
        typer.echo(f"{c['criterion_id']:5} {b.get('verdict') or '—':>6} {a.get('verdict') or '—':>6}  {c['change']:14} "
                   f"{c['title']}")
        if c["change"] in ("improved", "worse") or not c["ok_after"]:
            if a.get("explanation"):
                typer.echo(textwrap.fill(f"судья: {a['explanation']}", width=118, initial_indent="      ",
                                         subsequent_indent="      "))
            if not c["ok_after"] and a.get("how_to_fix"):
                typer.echo(textwrap.fill(f"как надо: {a['how_to_fix']}", width=118, initial_indent="      ",
                                         subsequent_indent="      "))


def register(app: typer.Typer) -> None:
    tr = typer.Typer(help="Тренажёр оператора: ИИ играет клиента из провального диалога", no_args_is_help=True)
    tu = typer.Typer(help="Чат с тьютором по оценкам и лучшим ответам коллег", no_args_is_help=True)
    app.add_typer(tr, name="trainer")
    app.add_typer(tu, name="tutor")

    @tr.command("start")
    def start(
        uid: str = typer.Argument(..., help="Исходный диалог, например DEV:p1-ru-007"),
        focus: str = typer.Option(None, help="Критерии в фокусе через запятую: 04,05"),
        operator: str = typer.Option(None, help="Кто тренируется"),
        profile: str = typer.Option(None, help="Профиль персоны (по умолчанию роль strong)"),
        as_json: bool = typer.Option(False, "--json", help="Вывести JSON"),
    ):
        """Новая сессия: персона клиента и начало транскрипта (до прихода оператора)."""
        from cqa.features import trainer

        con = _con()
        try:
            s = asyncio.run(trainer.create_session(
                con, uid, profile=_profile(con, "strong", profile),
                criteria_focus=[x.strip() for x in focus.split(",") if x.strip()] if focus else None,
                operator=operator))
        except (KeyError, ValueError, RuntimeError) as e:
            _fail(e)
        if as_json:
            typer.echo(json.dumps(s, ensure_ascii=False, indent=2))
            return
        typer.echo(f"сессия {s['id']} по {s['base_uid']}\n")
        _print_persona(s["persona"])
        typer.echo("")
        _print_messages(s["transcript"])
        typer.echo(f"\nОтветьте клиенту: cqa trainer reply {s['id']} \"…\"")

    @tr.command("reply")
    def reply_cmd(
        session_id: int = typer.Argument(...),
        text: str = typer.Argument(..., help="Реплика оператора"),
        profile: str = typer.Option(None, help="Профиль ИИ-клиента (по умолчанию роль judge)"),
    ):
        """Реплика оператора → ответ ИИ-клиента."""
        from cqa.features import trainer

        con = _con()
        try:
            r = asyncio.run(trainer.reply(con, session_id, text, profile=_profile(con, "judge", profile)))
        except (KeyError, ValueError, RuntimeError) as e:
            _fail(e)
        _print_messages(r["new_messages"])
        if r["finished"]:
            typer.echo(f"\nклиент завершил разговор ({r['outcome']}), ход {r['turn']} из {r['max_turns']}. "
                       f"Можно попрощаться и оценить: cqa trainer finish {session_id}")
        else:
            typer.echo(f"\nход {r['turn']} из {r['max_turns']}")

    @tr.command("finish")
    def finish_cmd(
        session_id: int = typer.Argument(...),
        profile: str = typer.Option(None, help="Профиль судьи (по умолчанию — профиль исходной оценки)"),
        as_json: bool = typer.Option(False, "--json", help="Вывести JSON"),
    ):
        """Оценка диалога сессии тем же чек-листом и судьёй → «было/стало»."""
        from cqa.api.deps import effective_roles, profile_available, profile_names
        from cqa.features import trainer

        con = _con()
        try:
            name = profile or trainer.session_meta(con, session_id).get("eval_profile")
            if not name or name not in profile_names() or not profile_available(name):
                name = effective_roles(con, False)["judge"]
            s = asyncio.run(trainer.finish(con, session_id, eval_profile=name))
        except (KeyError, ValueError, RuntimeError) as e:
            _fail(e)
        if as_json:
            typer.echo(json.dumps(s, ensure_ascii=False, indent=2))
            return
        typer.echo(f"сессия {s['id']}: {s['uid']}, оценка {s['result_evaluation_id']} (исходная {s['base_evaluation_id']})")
        _print_persona(s["persona"])
        typer.echo("")
        _print_messages(s["transcript"])
        _print_comparison(s["comparison"])

    @tr.command("show")
    def show_cmd(session_id: int = typer.Argument(...), as_json: bool = typer.Option(False, "--json")):
        """Сессия: персона, транскрипт, «было/стало» (если оценена)."""
        from cqa.features import trainer

        con = _con()
        try:
            s = trainer.session_view(con, session_id)
        except KeyError as e:
            _fail(e)
        if as_json:
            typer.echo(json.dumps(s, ensure_ascii=False, indent=2))
            return
        typer.echo(f"сессия {s['id']} по {s['base_uid']}: {s['status']}, ходов {s['turns']}, {s['outcome']}")
        _print_persona(s["persona"])
        typer.echo("")
        _print_messages(s["transcript"])
        if s["comparison"]:
            _print_comparison(s["comparison"])

    @tr.command("list")
    def list_cmd(base_uid: str = typer.Option(None, help="Только по этому диалогу")):
        """Сессии тренажёра."""
        from cqa.features import trainer

        for s in trainer.list_sessions(_con(), base_uid=base_uid):
            typer.echo(f"{s['id']:4} {s['status']:9} {s['base_uid']:22} ходов {s['turns']}  "
                       f"{s['before_total'] if s['before_total'] is not None else '—'} → "
                       f"{s['after_total'] if s['after_total'] is not None else '—'}  {s['title']}")

    @tu.command("ask")
    def ask_cmd(
        question: str = typer.Argument(..., help="Вопрос тьютору"),
        operator: str = typer.Option(None, help="Имя оператора (его оценки — в контексте)"),
        uid: str = typer.Option(None, help="Диалог, о котором вопрос"),
        profile: str = typer.Option(None, help="Профиль (по умолчанию роль strong)"),
        as_json: bool = typer.Option(False, "--json", help="Вывести JSON"),
    ):
        """Ответ по-русски с источниками: свои оценки, чек-лист, лучшие ответы коллег."""
        from cqa.features import tutor

        con = _con()
        try:
            r = asyncio.run(tutor.ask(con, question, profile=_profile(con, "strong", profile), operator=operator,
                                      uid=uid))
        except (KeyError, ValueError, RuntimeError) as e:
            _fail(e)
        if as_json:
            typer.echo(json.dumps(r, ensure_ascii=False, indent=2))
            return
        typer.echo(r["answer"])
        if not r["sources"]:
            typer.echo("\nИсточников нет: в контексте ответа не нашлось.")
        else:
            typer.echo(f"\nИсточники{'' if r['sources'][0]['cited'] else ' (весь контекст, модель не сослалась)'}:")
        for s in r["sources"]:
            where = " ".join(x for x in (s.get("uid"), s.get("mid")) if x) or s.get("criterion_id", "")
            typer.echo(textwrap.shorten(f"  [{s['ref']}] {s['kind']:11} {where}: {s['text']}", width=160))
        cost = r.get("cost_usd")
        typer.echo(f"\nвызов {r['llm_call_id']}, стоимость {'—' if cost is None else f'${cost:.4f}'}")

    @tu.command("best")
    def best_cmd(
        topic: str = typer.Option(None, help="Тема обращения или слова для поиска"),
        q: str = typer.Option(None, help="Поиск по словам"),
        limit: int = typer.Option(30),
    ):
        """База лучших ответов коллег."""
        from cqa.features import tutor

        r = tutor.best_answers_view(_con(), topic=topic, q=q, limit=limit)
        typer.echo(f"пар: {r['total']}; темы: " + ", ".join(f"{t['topic']} ({t['n']})" for t in r["topics"]))
        for p in r["items"]:
            typer.echo(textwrap.shorten(f"  {p['uid']} {p['question_mid']}→{p['answer_mid']} [{p['topic']}] "
                                        f"{p['question']} → {p['answer_text']}", width=180))
