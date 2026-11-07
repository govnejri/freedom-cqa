"""CLI: `cqa serve` — REST API, фоновый воркер прогонов и UI через uvicorn."""
from __future__ import annotations

import os

import typer


def register(app: typer.Typer) -> None:
    @app.command("serve")
    def serve(
        host: str = typer.Option("127.0.0.1", help="Адрес (0.0.0.0 — открыть в сети, например для демо с другого устройства)"),
        port: int = typer.Option(8000, help="Порт"),
        dev: bool = typer.Option(False, "--dev", help="Отдавать frontend/ как есть (без сборки)"),
        fake: bool = typer.Option(False, "--fake", help="Все роли — профиль fake: демо без сети"),
        reload: bool = typer.Option(False, "--reload", help="Перезапуск при изменении кода"),
    ):
        """Запустить API (/api), воркер прогонов и UI (/)."""
        import uvicorn

        from cqa.api.app import ENV_DEV, ENV_FAKE
        from cqa.settings import ROOT

        os.environ[ENV_DEV] = "1" if dev else "0"
        os.environ[ENV_FAKE] = "1" if fake else "0"
        mode = ", ".join(m for m, on in (("dev", dev), ("fake", fake), ("reload", reload)) if on) or "обычный"
        typer.echo(f"CQA: http://{'localhost' if host in ('0.0.0.0', '::') else host}:{port}/ (API /api, режим: {mode})")
        uvicorn.run(
            "cqa.api.app:app_from_env", factory=True, host=host, port=port, reload=reload,
            reload_dirs=[str(ROOT / "cqa")] if reload else None, log_level="info",
        )
