"""CLI: cqa autotest generate | run | report (автотесты судьи, config/autotests.yaml)."""
from __future__ import annotations

import json
import sys
import time

import typer


def _con():
    from cqa.db.conn import connect, migrate
    con = connect()
    migrate(con)
    return con


def _split(value: str | None) -> list[str] | None:
    return [x.strip() for x in value.split(",") if x.strip()] if value else None


def _pct(x: float | None) -> str:
    return f"{x:4.0%}" if x is not None else "   —"


def _cell(a: dict | None) -> str:
    if not a or not a["evaluated"]:
        return f"{'—':>11}"
    return f"{a['passed']:>3}/{a['evaluated']:<3}{_pct(a['rate'])}"


def format_report(rep: dict) -> str:
    L: list[str] = []
    t = rep["total"]
    head = f"Автотесты: профиль {rep['profile'] or 'любой'}"
    if rep["models"]:
        head += f" ({', '.join(rep['models'])})"
    L.append(f"{head}; тестов {t['n']}, оценено {t['evaluated']}, пройдено {t['passed']} ({_pct(t['rate']).strip()}),"
             f" ошибок судьи {t['errors']}, ждут оценки {t['pending']}")
    langs = [x["language"] for x in rep["by_language"]]
    L.append("")
    L.append(f"{'изменение':17} {'ожидание':16} {'всего':>11} " + " ".join(f"{lg:>11}" for lg in langs))
    for m in rep["by_mutation"]:
        exp = m["expect"] if isinstance(m["expect"], str) else ", ".join(f"{k}={'|'.join(v)}" for k, v in m["expect"].items())
        L.append(f"{m['mutation']:17} {exp:16} {_cell(m)} " + " ".join(_cell(m['by_language'].get(lg)) for lg in langs))
    L.append(f"{'по языкам':34} {_cell(t)} " + " ".join(_cell(x) for x in rep["by_language"]))
    inj = rep.get("injection")
    if inj:
        L.append("")
        L.append(f"Инъекция: устойчиво {inj['robust']}/{inj['evaluated']}; буквально как у исходного {inj['exact_same']};"
                 f" ушли в «Да» вне допустимых: {inj['flipped_to_yes']} диалогов"
                 + (f" {inj['flipped_to_yes_by_criterion']}" if inj['flipped_to_yes_by_criterion'] else "")
                 + (f"; изменения по критериям {inj['changed_by_criterion']}" if inj["changed_by_criterion"] else ""))
    if rep["failed"]:
        L.append("")
        L.append("Проваленные:")
        for f in rep["failed"]:
            L.append(f"  {f['uid']} ({f['language']}, оценка {f['evaluation_id']}) — {f['status']}")
            for c in f["mismatches"]:
                exp = "|".join(c["expected"]) or "—"
                L.append(f"    {c['criterion_id']}: ожидалось {exp}, получено {c['got'] or c['status']}. {c['explanation']}")
    if rep["runs"]:
        L.append("")
        L.append("Прогоны: " + ", ".join(f"#{r['id']} {r['status']} ${r['cost_usd'] or 0:.4f}" for r in rep["runs"])
                 + f"; всего ${rep['cost_usd']:.4f}")
    return "\n".join(L) + "\n"


def register(app: typer.Typer) -> None:
    at_app = typer.Typer(help="Автотесты судьи: изменённые диалоги с известным ответом", no_args_is_help=True)
    app.add_typer(at_app, name="autotest")

    @at_app.command("generate")
    def generate_cmd(
        per_mutation: int = typer.Option(0, "--per-mutation", help="Не больше N баз на изменение (0 — все подходящие)"),
        mutations: str = typer.Option(None, "--mutations", help="Только эти изменения, через запятую"),
        base_group: str = typer.Option(None, "--base-group", help="Группа исходных диалогов (по умолчанию из конфига)"),
    ):
        """Создать мутантов (группа AUTOTEST) из диалогов с эталоном."""
        from cqa.quality.autotest import generate

        con = _con()
        out = generate(con, per_mutation=per_mutation or None, mutations=_split(mutations), base_group=base_group)
        for m in out:
            reasons: dict[str, int] = {}
            for s in m["skipped"]:
                reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
            typer.echo(f"{m['mutation']:17} создано {m['generated']:3} из {m['candidates']:3} кандидатов"
                       f" {m['by_language']}" + (f"; пропущено: {reasons}" if reasons else ""))
        typer.echo(f"всего мутантов: {sum(m['generated'] for m in out)}")

    @at_app.command("run")
    def run_cmd(
        profile: str = typer.Option(None, "--profile", help="Профиль модели (по умолчанию роль judge)"),
        mutations: str = typer.Option(None, "--mutations", help="Только эти изменения, через запятую"),
        version: int = typer.Option(None, "--version", help="ID версии чек-листа (по умолчанию активная)"),
        concurrency: int = typer.Option(None, help="Диалогов параллельно в каждом прогоне"),
    ):
        """Оценить мутантов тем же runner (kind=autotest) и напечатать отчёт."""
        from cqa.quality.autotest import run
        from cqa.settings import load_models_config

        con = _con()
        profile = profile or load_models_config()["roles"]["judge"]
        t0 = time.time()
        res = run(con, profile=profile, mutations=_split(mutations), version_id=version, concurrency=concurrency)
        if not res["runs"]:
            typer.echo("нет мутантов для прогона — сначала cqa autotest generate")
            raise typer.Exit(1)
        typer.echo(f"прогоны {res['runs']} за {time.time() - t0:.0f} с")
        sys.stdout.write(format_report(res["report"]))

    @at_app.command("report")
    def report_cmd(
        profile: str = typer.Option(None, "--profile", help="Только оценки этим профилем"),
        mutations: str = typer.Option(None, "--mutations", help="Только эти изменения, через запятую"),
        as_json: bool = typer.Option(False, "--json", help="JSON вместо текста"),
    ):
        """Доля пройденных по изменениям и языкам, список проваленных."""
        from cqa.quality.autotest import report

        rep = report(_con(), profile=profile, mutations=_split(mutations))
        if as_json:
            sys.stdout.write(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(format_report(rep))
