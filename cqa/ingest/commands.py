"""CLI модуля ingest: `cqa load`, `cqa show`, `cqa audio-probe`. Секреты (sig=) не печатаются."""
from __future__ import annotations

import json
from itertools import groupby
from pathlib import Path

import typer


def _con():
    from cqa.db.conn import connect, migrate

    con = connect()
    migrate(con)
    return con


def _default_group() -> str:
    from cqa.settings import get_settings
    return get_settings().cqa_group_id


def _runs(items: list[tuple[str, str]]) -> str:
    """[(mid, label)] → «label m1–m9, label m10» по подряд идущим одинаковым меткам."""
    parts = []
    for label, grp in groupby(items, key=lambda x: x[1]):
        mids = [m for m, _ in grp]
        parts.append(f"{label} {mids[0]}" + (f"–{mids[-1]}" if len(mids) > 1 else ""))
    return ", ".join(parts)


def _fmt(v: float | None, nd: int = 1) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def register(app: typer.Typer) -> None:
    @app.command("load")
    def load(
        file: Path = typer.Option(None, "--file", exists=True, dir_okay=False,
                                  help="JSON-ответ API (например data/dev.json)"),
        group: str = typer.Option(None, "--group", help="ID группы; без --file — загрузка из API"),
        no_audio: bool = typer.Option(False, "--no-audio", help="Не скачивать mp3 звонков"),
    ):
        """Загрузить набор из файла или из API организаторов: нормализация, факты, БД, mp3 звонков."""
        from cqa.ingest.fetch import FetchError, fetch_group_raw
        from cqa.ingest.service import ingest_payload

        con = _con()
        try:
            if file is not None:
                payload = json.loads(file.read_text(encoding="utf-8"))
                report = ingest_payload(con, payload, "file", group, raw_path=file)
            else:
                group = group or _default_group()
                payload, raw_path = fetch_group_raw(group)
                report = ingest_payload(con, payload, "api", group, raw_path=raw_path)
        except (FetchError, ValueError) as e:
            typer.echo(f"ошибка загрузки: {e}", err=True)
            raise typer.Exit(1)

        typer.echo(f"загружено {report.summary()}")
        typer.echo(f"  звонков: {report.calls}, со ссылкой на аудио: {report.with_audio_url}")
        for d in report.duplicates:
            typer.echo(f"  дубль: {d['uid']} {d['mid']} = {d['dup_of']}")
        for u in report.unknown_events:
            typer.echo(f"  неизвестное событие: {u['uid']} {u['mid']}: {u['text']!r}")
        for s in report.skipped:
            typer.echo(f"  пропущено: {s}")
        for w in report.warnings:
            typer.echo(f"  предупреждение: {w}")

        if not no_audio and report.with_audio_url:
            from cqa.ingest.audio import download_audio

            ar = download_audio(con, report.group_id)
            typer.echo(ar.summary())
            for uid, why in ar.failed.items():
                typer.echo(f"  {uid}: {why}")

    @app.command("show")
    def show(
        uid: str = typer.Argument(..., help="UID диалога: GROUP:conversation_id (без группы — CQA_GROUP_ID)"),
        segments: bool = typer.Option(False, "--segments", help="Участки, запись, audio_sec, события"),
    ):
        """Показать диалог после нормализации и его факты."""
        from cqa.ingest.store import get_conversation

        con = _con()
        if ":" not in uid:
            uid = f"{_default_group()}:{uid}"
        try:
            conv = get_conversation(con, uid)
        except KeyError:
            typer.echo(f"нет диалога {uid}", err=True)
            raise typer.Exit(1)

        status = con.execute("SELECT audio_status FROM conversations WHERE uid=?", (uid,)).fetchone()[0]
        typer.echo(f"{conv.uid}  {conv.channel} {conv.language}  оператор: {conv.operator_name or '—'}  "
                   f"реплик: {len(conv.messages)}  аудио: {status}{' (файл есть)' if conv.has_audio else ''}  "
                   f"hash {conv.content_hash[:12]}")
        msgs = conv.messages
        is_call = any(m.audio_sec is not None or not m.recorded for m in msgs)
        if segments:
            typer.echo(f"участки: {_runs([(m.mid, m.segment) for m in msgs])}")
            if is_call:
                typer.echo(f"запись: {_runs([(m.mid, 'да' if m.recorded else 'нет') for m in msgs])}")
            typer.echo(f"{'mid':<5} {'участок':<9} {'off,с':>7} {'audio,с':>8} {'зап':>3} {'роль':<9} "
                       f"{'событие':<18} флаги / текст")
        for m in msgs:
            text = " ".join(m.text.split())
            flags = []
            if m.after_end:
                flags.append("after_end")
            if m.dup_of:
                flags.append(f"dup_of={m.dup_of}")
            fl = f"[{' '.join(flags)}] " if flags else ""
            if segments:
                typer.echo(f"{m.mid:<5} {m.segment:<9} {_fmt(m.offset_sec):>7} {_fmt(m.audio_sec):>8} "
                           f"{'+' if m.recorded else '-':>3} {m.speaker:<9} {m.event_type or '':<18} {fl}{text}")
            else:
                ev = f"<{m.event_type}> " if m.event_type else ""
                typer.echo(f"{m.mid:<5} {_fmt(m.offset_sec):>7} {m.speaker:<9} {ev}{fl}{text}")
        typer.echo("факты:")
        typer.echo(json.dumps(conv.facts.model_dump() if conv.facts else None, ensure_ascii=False, indent=2))

    @app.command("audio-probe")
    def audio_probe(
        group: str = typer.Option(None, "--group", help="ID группы (по умолчанию CQA_GROUP_ID)"),
    ):
        """Длительность mp3 (ffprobe) против длительности записанного участка по транскрипту."""
        from cqa.ingest.audio import download_audio, probe

        con = _con()
        group = group or _default_group()
        ar = download_audio(con, group)
        typer.echo(ar.summary())
        for uid, why in ar.failed.items():
            typer.echo(f"  {uid}: {why}")
        probe(con, group, echo=typer.echo)
