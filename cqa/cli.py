"""CLI `cqa`. Каждый модуль регистрирует свои команды в <module>/commands.py: register(app)."""
from __future__ import annotations

import importlib
import logging

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="CQA — оценка качества диалогов")

_MODULES = [
    "cqa.ingest.commands",
    "cqa.checklist.commands",
    "cqa.llm.commands",
    "cqa.core.commands",
    "cqa.core.run_commands",
    "cqa.quality.commands",
    "cqa.api.commands",
    "cqa.webui.commands",
    "cqa.features.commands",
]


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        from cqa.llm.redact import install as install_redact
        install_redact()
    except ImportError:
        pass


for _mod in _MODULES:
    try:
        importlib.import_module(_mod).register(app)
    except ModuleNotFoundError as e:
        if e.name != _mod:
            raise


@app.callback()
def _main() -> None:
    _setup_logging()


if __name__ == "__main__":
    app()
