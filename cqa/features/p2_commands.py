"""CLI фич P2: `cqa operators [--name X]`, `cqa operators assign UID NAME`, `cqa slices --by language`,
`cqa precedents list | to-version | similar`."""
from __future__ import annotations

import json
from typing import Any

import typer


def _con():
    from cqa.db.conn import connect, migrate
    con = connect()
    migrate(con)
    return con


def _f(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _cell(crit: dict[str, Any], st: dict[str, Any]) -> str:
    """Доля «Да» / средняя ступень / для флага — число «Да»."""
    if not st.get("n_applicable"):
        return "N/A" if st.get("n") else "—"
    if crit["type"] == "scale":
        return f"{st['mean_step']:.1f}/{crit['top']}"
    if crit["type"] == "flag":
        return f"{st['n_yes']}!" if st["n_yes"] else "0"
    return _pct(st["yes_share"])


def _dump(obj: Any) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False, indent=2))


def _print_profile(p: dict[str, Any]) -> None:
    dep = p["department"]
    typer.echo(f"оператор {p['name']}: диалогов {p['n_dialogs']}, средний итог {_f(p['avg_total'])} "
               f"(отдел {_f(dep['avg_total'])} по {dep['n_dialogs']} диалогам), грубых ошибок {p['gross_count']}, "
               f"апелляций {p['n_appeals']}" + (f", назначено вручную {p['manual']}" if p["manual"] else ""))
    typer.echo("\nкритерии (оператор / отдел, потеря баллов на диалог):")
    for c in p["criteria"]:
        typer.echo(f"  {c['criterion_id']} {c['title'][:44]:<44} {_cell(c, c['operator']):>7} / "
                   f"{_cell(c, c['department']):<7} N/A {c['operator']['n_na']}  потеря {_f(c['operator']['loss'], 2)}")
    typer.echo("\nзоны роста:")
    for z in p["growth_zones"]:
        typer.echo(f"  {z['criterion_id']} {z['title']}: потеря {z['loss']:.2f} (отдел {_f(z['dept_loss'], 2)}), "
                   f"нарушений {z['violations']} из {z['n_applicable']}")
        for e in z["examples"]:
            typer.echo(f"    · {e['external_id']} {e['mid'] or ''} [{e['verdict']}] {e['explanation'][:150]}")
            if e["how_to_fix"]:
                typer.echo(f"      как надо: {e['how_to_fix'][:150]}")
    if not p["growth_zones"]:
        typer.echo("  нет — потерь по критериям с весом нет")
    typer.echo("\nсильные стороны: " + (", ".join(
        f"{s['criterion_id']} {s['title']} ({_pct(s['quality'])}, отдел {_pct(s['dept_quality'])})"
        for s in p["strengths"]) or "—"))
    if p["gross_errors"]:
        typer.echo("\nгрубые ошибки:")
        for g in p["gross_errors"]:
            typer.echo(f"  {g['external_id']} {g['category']}: {g['explanation'][:150]}")
    typer.echo("\nдиалоги:")
    for d in p["dialogs"]:
        typer.echo(f"  {d['external_id']:<12} {d['language']} {d['channel']:<10} итог {_f(d['total']):>5}"
                   f"{'  ГРУБАЯ' if d['gross'] else ''}{'  (вручную)' if d['manual'] else ''}  {d['contact_reason'] or ''}")


def register(app: typer.Typer) -> None:
    op = typer.Typer(help="Операторы: список, профиль, ручное назначение", invoke_without_command=True)
    app.add_typer(op, name="operators")

    @op.callback()
    def operators_cmd(
        ctx: typer.Context,
        name: str | None = typer.Option(None, "--name", "-n", help="профиль оператора"),
        group: str | None = typer.Option(None, "--group", "-g", help="группа (по умолчанию все, кроме AUTOTEST)"),
        summary: bool = typer.Option(False, "--summary", help="ещё и LLM-сводка (роль strong, 1 вызов, кэш)"),
        as_json: bool = typer.Option(False, "--json", help="вывод JSON"),
    ):
        """Список операторов или профиль одного: критерии против отдела, зоны роста, сильные стороны, диалоги."""
        if ctx.invoked_subcommand is not None:
            return
        from cqa.features import operators as ops

        con = _con()
        try:
            if not name:
                data = ops.list_operators(con, group)
                if as_json:
                    _dump(data)
                    return
                typer.echo(f"диалогов {data['n_dialogs']}, операторов {data['n_operators']}, средний итог "
                           f"{_f(data['avg_total'])}, без оператора {len(data['unassigned'])}")
                for o in data["operators"]:
                    typer.echo(f"  {o['name']:<16} диалогов {o['n_dialogs']:>3}  итог {_f(o['avg_total']):>5}  "
                               f"грубых {o['gross_count']}  апелляций {o['n_appeals']}  {'/'.join(o['languages'])}"
                               + (f"  вручную {o['manual']}" if o["manual"] else ""))
                return
            try:
                p = ops.operator_profile(con, name, group)
            except KeyError:
                typer.echo(f"оператор {name!r}: нет оценённых диалогов", err=True)
                raise typer.Exit(1) from None
            out: dict[str, Any] = {"profile": p}
            if summary:
                import asyncio

                from cqa.settings import role_profile
                out["summary"] = asyncio.run(ops.operator_summary(con, name, role_profile("strong"), group))
            if as_json:
                _dump(out if summary else p)
                return
            _print_profile(p)
            if summary:
                s = out["summary"]
                typer.echo(f"\nсводка ({s['profile']}, {s['model']}, кэш {'да' if s['cache_hit'] else 'нет'}, "
                           f"${s['cost_usd'] or 0:.4f}):\n{s['summary']}")
        finally:
            con.close()

    @op.command("assign")
    def assign_cmd(uid: str = typer.Argument(..., help="uid диалога, например DEV:p4-ru-001"),
                   name: str = typer.Argument("", help="имя оператора; пусто — вернуть имя из приветствия")):
        """Назначить диалогу оператора вручную (звонки: имя скрыто; демо-профиль)."""
        from cqa.features.operators import assign_operator

        con = _con()
        try:
            r = assign_operator(con, uid, name)
        except KeyError:
            typer.echo(f"нет диалога {uid}", err=True)
            raise typer.Exit(1) from None
        finally:
            con.close()
        typer.echo(f"{r['uid']}: оператор {r['operator_name'] or '—'}"
                   + (f" (вручную; из приветствия: {r['auto_operator_name'] or '—'})" if r["manual"] else ""))

    @app.command("slices")
    def slices_cmd(
        by: str = typer.Option("language", "--by", help="language | channel | operator | reason"),
        group: str | None = typer.Option(None, "--group", "-g"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Срезы для руководителя: диалогов, средний итог, доля «Да» / средняя ступень по критериям, грубые ошибки."""
        from cqa.features.slices import BY, slices

        if by not in BY:
            typer.echo(f"--by: одно из {', '.join(BY)}", err=True)
            raise typer.Exit(2)
        con = _con()
        try:
            data = slices(con, by, group)
        finally:
            con.close()
        if as_json:
            _dump(data)
            return
        crits = data["criteria"]
        head = f"{by:<22} {'диал':>4} {'итог':>6} " + " ".join(f"{c['id']:>7}" for c in crits) + "  грубых  проверка"
        typer.echo(head)
        for row in [*data["slices"], data["overall"]]:
            cells = " ".join(f"{_cell(c, row['criteria'][c['id']]):>7}" for c in crits)
            typer.echo(f"{row['key'][:22]:<22} {row['n_dialogs']:>4} {_f(row['avg_total']):>6} {cells}"
                       f"  {row['gross_count']:>6}  {row['needs_review']:>8}")
        typer.echo("доля «Да»; шкала — средняя ступень/верхняя; флаг — число «Да» (грубых)")

    pr = typer.Typer(help="Прецеденты из апелляций → примеры в чек-листе", no_args_is_help=True)
    app.add_typer(pr, name="precedents")

    @pr.command("list")
    def precedents_list(criterion: str | None = typer.Option(None, "--criterion", "-c"),
                        as_json: bool = typer.Option(False, "--json")):
        """Прецеденты: критерий, вердикт, фрагмент, версия чек-листа, куда добавлен."""
        from cqa.features.precedents import list_precedents

        con = _con()
        try:
            rows = list_precedents(con, criterion)
        finally:
            con.close()
        if as_json:
            _dump(rows)
            return
        if not rows:
            typer.echo("прецедентов нет (решение по апелляции с make_precedent=true)")
        for p in rows:
            ver = f"v{p['added_in_version']} ({p['added_in_version_status']})" if p["added_in_version"] else "не добавлен"
            typer.echo(f"#{p['id']} {p['criterion_id']} {p['criterion_title']} → {p['verdict']} "
                       f"(было {p['original_verdict'] or '—'}), {p['external_id'] or '—'}, {ver}"
                       + (", в активной" if p["in_active_version"] else "")
                       + (f", ОТЛОЖЕННАЯ семья {p['holdout_family']}" if p["holdout_family"] else ""))
            typer.echo(f"   {p['fragment']}")
            typer.echo(f"   почему: {p['why']}")

    @pr.command("to-version")
    def precedents_to_version(ids: list[int] = typer.Argument(..., help="id прецедентов"),
                              comment: str | None = typer.Option(None, "--comment")):
        """Черновик новой версии от активной с примерами из прецедентов (без публикации)."""
        from cqa.features.precedents import PrecedentError, build_version

        con = _con()
        try:
            r = build_version(con, ids, comment)
        except PrecedentError as e:
            typer.echo(e.msg, err=True)
            raise typer.Exit(1) from None
        finally:
            con.close()
        typer.echo(f"черновик v{r['version']} (id {r['version_id']}) от v{r['parent_version']}: добавлены {r['added']}"
                   + (f", пропущены {r['skipped']}" if r["skipped"] else ""))
        for w in r["warnings"]:
            typer.echo(f"предупреждение: {w}")
        typer.echo("дальше: пробный прогон POST /api/checklist-versions/{vid}/dry-run, затем публикация")

    @pr.command("similar")
    def precedents_similar(pid: int = typer.Argument(...), limit: int = typer.Option(10, "--limit")):
        """Похожие диалоги для пробного прогона версии с прецедентом."""
        from cqa.features.precedents import PrecedentError, similar_items

        con = _con()
        try:
            r = similar_items(con, pid, limit)
        except PrecedentError as e:
            typer.echo(e.msg, err=True)
            raise typer.Exit(1) from None
        finally:
            con.close()
        typer.echo(f"прецедент #{pid}: {r['source_uid']}, причина {r['contact_reason'] or '—'}, "
                   f"исходный вердикт {r['criterion_id']} = {r['original_verdict']}")
        for x in r["items"]:
            m = "".join(k[0].upper() if v else "·" for k, v in x["match"].items())
            typer.echo(f"  {x['uid']:<22} [{m}] {x['contact_reason'] or '—'}, {r['criterion_id']} = {x['verdict']}")
