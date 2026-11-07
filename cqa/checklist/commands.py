"""CLI: cqa checklist validate PATH, cqa checklist show [--version N]."""
from __future__ import annotations

from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    ck = typer.Typer(help="Чек-лист: проверка и версии", no_args_is_help=True)
    app.add_typer(ck, name="checklist")

    @ck.command("validate")
    def validate_cmd(path: Path = typer.Argument(..., help="YAML чек-листа")):
        """Проверить YAML чек-листа: ошибки построчно, сводка по критериям и весам."""
        from cqa.checklist.io import config_hash, validate_file, warnings

        if not path.exists():
            typer.echo(f"{path}: файл не найден", err=True)
            raise typer.Exit(2)
        cfg, errors = validate_file(path)
        if errors:
            for e in errors:
                where = f"{path}:{e['line']}" if e.get("line") else str(path)
                loc = f" {e['loc']}:" if e.get("loc") else ""
                typer.echo(f"{where}:{loc} {e['msg']}", err=True)
            typer.echo(f"ошибок: {len(errors)}", err=True)
            raise typer.Exit(1)
        crits = cfg.enabled_criteria()
        weighted = [c for c in crits if c.weight > 0]
        flags = [c for c in crits if c.type == "flag"]
        total_w = sum(c.weight for c in crits)
        typer.echo(f"{path}: OK — {cfg.id} «{cfg.title}»")
        typer.echo(f"критериев: {len(cfg.criteria)} (включено {len(crits)}; с весом {len(weighted)}, флагов {len(flags)})")
        typer.echo(f"сумма весов: {total_w:g} (max_total {cfg.scoring.max_total})")
        typer.echo(f"секций: {len(cfg.sections)}; hash {config_hash(cfg)[:12]}")
        for w in warnings(cfg):
            typer.echo(f"предупреждение: {w}")

    @ck.command("import")
    def import_cmd(path: Path = typer.Argument(None, help="YAML чек-листа (по умолчанию из настроек)"),
                   comment: str = typer.Option(None)):
        """Импортировать YAML как новую активную версию, если конфиг изменился."""
        from cqa.checklist.store import sync_yaml
        from cqa.db.conn import connect, migrate
        from cqa.settings import get_settings

        con = connect()
        migrate(con)
        vid, created = sync_yaml(con, path or get_settings().checklist_path, comment)
        typer.echo(f"{'новая активная версия' if created else 'без изменений, активная версия'} id={vid}")

    @ck.command("show")
    def show_cmd(
        version: int = typer.Option(None, "--version", "-v", help="Номер версии (по умолчанию активная)"),
        checklist: str = typer.Option("freedom-cqa", help="ID чек-листа"),
        as_yaml: bool = typer.Option(False, "--yaml", help="Вывести конфиг версии в YAML"),
    ):
        """Показать версию чек-листа из БД (при первом запуске импортирует YAML как версию 1)."""
        from cqa.checklist.io import dump_yaml
        from cqa.checklist.store import find_version, get_active, get_version, list_versions, seed
        from cqa.db.conn import connect, migrate
        from cqa.settings import get_settings

        con = connect()
        migrate(con)
        seed(con, get_settings().checklist_path)
        try:
            cfg, meta = get_version(con, find_version(con, checklist, version)) if version else get_active(con, checklist)
        except KeyError as e:
            typer.echo(str(e.args[0] if e.args else e), err=True)
            raise typer.Exit(1)
        if as_yaml:
            typer.echo(dump_yaml(cfg), nl=False)
            return
        marks = [meta["status"]] + (["активная"] if meta["is_active"] else []) + (["заморожена"] if meta["is_frozen"] else [])
        typer.echo(f"{cfg.id} «{cfg.title}» — версия {meta['version']} (id {meta['id']}), {', '.join(marks)}")
        typer.echo(f"создана {meta['created_at']}" + (f"; {meta['comment']}" if meta["comment"] else ""))
        if cfg.version_note:
            typer.echo(f"примечание: {cfg.version_note}")
        typer.echo(f"hash {meta['config_hash'][:12]}\n")
        typer.echo(f"{'крит':5} {'тип':7} {'вес':>4} {'N/A':4} {'секция':9} название")
        for c in cfg.criteria:
            extra = []
            if c.type == "scale":
                extra.append("шкала " + "/".join(str(v) for v in sorted((s.value for s in c.scale), reverse=True)))
            if c.channels:
                extra.append("каналы: " + ", ".join(c.channels))
            if not c.enabled:
                extra.append("выключен")
            tail = f"  [{'; '.join(extra)}]" if extra else ""
            typer.echo(f"{c.id:5} {c.type:7} {c.weight:4g} {'да' if c.na_allowed else 'нет':4} {c.section:9} {c.title}{tail}")
        typer.echo(f"\nсумма весов: {sum(c.weight for c in cfg.enabled_criteria()):g}")
        vs = list_versions(con, checklist)
        typer.echo("версии: " + ", ".join(
            f"v{m['version']} {m['status']}" + ("*" if m["is_active"] else "") for m in vs) + "   (* — активная)")
