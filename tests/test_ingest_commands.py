"""CLI: cqa load --file / cqa show. Без сети (--no-audio)."""
from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner

from cqa.ingest.commands import register

MINI = Path(__file__).parent / "fixtures" / "ingest_mini.json"


def _app() -> typer.Typer:
    app = typer.Typer()
    register(app)
    return app


def test_load_and_show(tmp_db):
    app = _app()
    runner = CliRunner()
    r = runner.invoke(app, ["load", "--file", str(MINI), "--no-audio"])
    assert r.exit_code == 0, r.output
    assert "MINI: 3 диалогов, 43 реплик, неизвестных событий: 0, дублей: 2" in r.output
    assert "MINI:mini-ru-chat m12 = m11" in r.output
    assert "sig=" not in r.output

    r = runner.invoke(app, ["load", "--file", str(MINI), "--group", "OTHER", "--no-audio"])
    assert r.exit_code == 0 and "OTHER: 3 диалогов" in r.output

    r = runner.invoke(app, ["show", "MINI:mini-ru-call", "--segments"])
    assert r.exit_code == 0, r.output
    assert "участки: bot m1–m4, waiting m5–m7, operator m8–m9, hold m10–m13, operator m14–m18" in r.output
    assert "запись: да m1–m5, нет m6, да m7–m18" in r.output
    assert "[after_end]" in r.output and "dup_of=m15" in r.output
    assert '"hold_range"' in r.output
    assert "sig=" not in r.output

    r = runner.invoke(app, ["show", "MINI:mini-ru-chat"])
    assert r.exit_code == 0 and "<escalated>" in r.output and "оператор: Ольга" in r.output

    r = runner.invoke(app, ["show", "MINI:nope"])
    assert r.exit_code == 1
