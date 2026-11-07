"""CLI: cqa eval (прогон + экспорт + метрики на эталоне), cqa runs, cqa export."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import typer

from cqa.settings import ROOT, get_settings, load_models_config


def _con():
    from cqa.db.conn import connect, migrate
    con = connect()
    migrate(con)
    return con


def _ensure_checklist(con) -> int:
    """Активная версия чек-листа; если YAML изменился — новая версия (цикл правок трактовок и примеров)."""
    from cqa.checklist.store import sync_yaml
    vid, created = sync_yaml(con, get_settings().checklist_path)
    if created:
        typer.echo(f"чек-лист изменён в YAML → новая активная версия id={vid}")
    return vid


def _split_ids(gold: Path, split: str) -> set[str] | None:
    if split == "all":
        return None
    holdout = {l.strip() for l in (ROOT / "eval" / "holdout.txt").read_text(encoding="utf-8").splitlines() if l.strip()}
    with open(gold, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if split == "holdout":
        return {r["id"] for r in rows if r.get("family") in holdout}
    return {r["id"] for r in rows if r.get("family") not in holdout}


def register(app: typer.Typer) -> None:
    runs_app = typer.Typer(help="Прогоны оценки")
    app.add_typer(runs_app, name="runs")

    @app.command("eval")
    def eval_cmd(
        group: str = typer.Option("DEV", help="Группа диалогов"),
        file: Path = typer.Option(None, help="Загрузить набор из файла, если группы нет в БД"),
        profile: str = typer.Option(None, help="Профиль модели (по умолчанию роль judge)"),
        gold: Path = typer.Option(None, help="Эталон для метрик, например eval/gold.csv"),
        split: str = typer.Option("all", help="all | tune | holdout (по семьям из eval/holdout.txt)"),
        criteria: str = typer.Option(None, help="Только эти критерии, через запятую"),
        version: int = typer.Option(None, help="ID версии чек-листа (по умолчанию активная)"),
        force: bool = typer.Option(False, help="Игнорировать кэш LLM"),
        limit: int = typer.Option(0, help="Только первые N диалогов"),
        concurrency: int = typer.Option(None, help="Диалогов параллельно"),
        frozen: bool = typer.Option(False, help="Использовать замороженную версию и профиль"),
        cross_check: bool = typer.Option(False, help="Перекрёстная проверка критериев с cross_check: true"),
        experiment: bool = typer.Option(False, help="Эксперимент: не менять текущие оценки диалогов"),
    ):
        """Прогон оценки по группе → экспорт CSV → метрики на эталоне."""
        from cqa.core.runner import create_run, run_sync
        from cqa.export.exporter import export_csv
        from cqa.ingest.store import list_uids

        con = _con()
        vid = version or _ensure_checklist(con)
        if frozen:
            row = con.execute("SELECT frozen_version_id FROM checklists WHERE id='freedom-cqa'").fetchone()
            if not row or not row[0]:
                raise typer.BadParameter("нет замороженной версии — сначала cqa freeze")
            vid = row[0]
            fz = con.execute("SELECT value_json FROM settings WHERE key='frozen_profile'").fetchone()
            profile = profile or (json.loads(fz[0]) if fz else None)
        profile = profile or load_models_config()["roles"]["judge"]
        uids = list_uids(con, group)
        if not uids and file:
            from cqa.ingest.service import ingest_records
            data = json.loads(Path(file).read_text(encoding="utf-8"))
            ingest_records(con, group, data["data"]["records"], "file")
            uids = list_uids(con, group)
        if not uids:
            raise typer.BadParameter(f"в группе {group} нет диалогов — cqa load")
        if gold and split != "all":
            keep = _split_ids(gold, split)
            uids = [u for u in uids if u.split(":", 1)[1] in keep]
        if limit:
            uids = uids[:limit]
        crit_list = [c.strip() for c in criteria.split(",")] if criteria else None
        t0 = time.time()
        run_id = create_run(con, uids=uids, version_id=vid, profile_name=profile, criteria=crit_list,
                            force=force, kind="experiment" if experiment else ("gold" if gold else "batch"),
                            cross_check=cross_check)
        typer.echo(f"run {run_id}: {len(uids)} диалогов, профиль {profile}, версия чек-листа {vid}")
        r = run_sync(con, run_id, concurrency)
        dt = time.time() - t0
        typer.echo(f"готово за {dt:.1f} с: done {r['units_done']}, failed {r['units_failed']}, "
                   f"из кэша {r['units_cached']}, стоимость ${r['cost_usd']:.4f}")
        out_dir = ROOT / "eval" / "runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{group}-{profile}-v{vid}-{r['prompt_version']}.csv"
        out.write_text(export_csv(con, group, run_id=run_id), encoding="utf-8")
        typer.echo(f"экспорт: {out.relative_to(ROOT)}")
        if gold:
            args = [sys.executable, str(ROOT / "eval" / "compare.py"), str(gold), str(out), "--traps"]
            if split == "tune":
                args += ["--exclude", str(ROOT / "eval" / "holdout.txt")]
            elif split == "holdout":
                args += ["--only", str(ROOT / "eval" / "holdout.txt")]
            subprocess.run(args, check=False)
            subprocess.run(args[:4] + ["--by", "language"], check=False)

    @runs_app.command("list")
    def runs_list(n: int = 10):
        con = _con()
        for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)):
            typer.echo(f"{r['id']:4} {r['kind']:8} {r['status']:9} {r['judge_profile']:14} "
                       f"{r['units_done']}/{r['units_total']} failed {r['units_failed']} cached {r['units_cached']} "
                       f"${r['cost_usd']:.4f} {r['created_at']}")

    @runs_app.command("resume")
    def runs_resume(run_id: int):
        from cqa.core.runner import reset_for_resume, run_sync
        con = _con()
        reset_for_resume(con, run_id)
        r = run_sync(con, run_id)
        typer.echo(f"run {run_id}: {r['status']}, done {r['units_done']}/{r['units_total']}, failed {r['units_failed']}")

    @runs_app.command("retry-failed")
    def runs_retry(run_id: int):
        from cqa.core.runner import retry_failed, run_sync
        con = _con()
        n = retry_failed(con, run_id)
        typer.echo(f"повтор {n} упавших")
        r = run_sync(con, run_id)
        typer.echo(f"run {run_id}: {r['status']}, failed {r['units_failed']}")

    @app.command("export")
    def export_cmd(
        group: str = typer.Option(None),
        format: str = typer.Option("csv", help="csv | json"),
        final: bool = typer.Option(False, help="С учётом решений по апелляциям"),
        out: Path = typer.Option(None, help="Файл; по умолчанию stdout"),
    ):
        """Экспорт результатов оценки."""
        from cqa.export.exporter import export_csv, export_json
        con = _con()
        text = export_csv(con, group, final) if format == "csv" else json.dumps(
            export_json(con, group, final), ensure_ascii=False, indent=2)
        if out:
            Path(out).write_text(text, encoding="utf-8")
            typer.echo(f"записано: {out}")
        else:
            sys.stdout.write(text)

    @app.command("freeze")
    def freeze_cmd(profile: str = typer.Option(None), version: int = typer.Option(None)):
        """Заморозить версию чек-листа и профиль судьи для контрольного этапа."""
        from cqa.checklist.store import freeze
        con = _con()
        vid = version or _ensure_checklist(con)
        profile = profile or load_models_config()["roles"]["judge"]
        freeze(con, vid)
        con.execute("UPDATE checklists SET frozen_version_id=? WHERE id='freedom-cqa'", (vid,))
        con.execute("INSERT OR REPLACE INTO settings(key, value_json) VALUES ('frozen_profile', ?)", (json.dumps(profile),))
        typer.echo(f"заморожено: версия {vid}, профиль {profile}")
