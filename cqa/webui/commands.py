"""CLI фронтенда: `cqa build-ui`, `cqa ui-preview`."""
from __future__ import annotations

import functools
import http.server
import socketserver
from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    @app.command("build-ui")
    def build_ui(
        out: Path = typer.Option(None, "--out", help="Куда писать HTML (по умолчанию build/cqa.html)"),
        no_mock: bool = typer.Option(False, "--no-mock", help="Не встраивать мок-данные"),
    ) -> None:
        """Собрать frontend/ в один HTML-файл build/cqa.html (CSS и JS встроены)."""
        from cqa.webui.build import OUT, BuildError, build

        try:
            r = build(out=out or OUT, with_mock=not no_mock)
        except BuildError as e:
            typer.secho(f"Ошибка сборки: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from e
        typer.echo(f"{r.path} — {r.size / 1024:.0f} КБ: {len(r.styles)} css, {len(r.scripts)} js"
                   + (f", мок: {', '.join(r.mocks)}" if r.mocks else ""))

    @app.command("ui-preview")
    def ui_preview(
        port: int = typer.Option(8090, "--port", help="Порт"),
        host: str = typer.Option("127.0.0.1", "--host", help="Адрес"),
    ) -> None:
        """Статическая раздача frontend/ для разработки. Без бэкенда интерфейс сам перейдёт на мок-данные."""
        from cqa.webui.build import FRONTEND

        class Handler(http.server.SimpleHTTPRequestHandler):
            def end_headers(self) -> None:
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def log_message(self, fmt: str, *args) -> None:  # тише стандартного лога
                if args and str(args[1]).startswith(("4", "5")) and "/api/" not in str(args[0]):
                    super().log_message(fmt, *args)

        handler = functools.partial(Handler, directory=str(FRONTEND))
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
            typer.echo(f"frontend/ → http://{host}:{port}/  (демо-данные: http://{host}:{port}/?mock=1)")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                typer.echo("остановлено")
