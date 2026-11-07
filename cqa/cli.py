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


def _discover() -> list[str]:
    """Явный список + любые модули cqa.<pkg>.*commands (команды фич P1–P3 подключаются сами)."""
    import pkgutil
    from pathlib import Path

    found = list(_MODULES)
    root = Path(__file__).parent
    for pkg in sorted(p for p in root.iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        for info in pkgutil.iter_modules([str(pkg)]):
            name = f"cqa.{pkg.name}.{info.name}"
            if info.name.endswith("commands") and name not in found:
                found.append(name)
    return found


for _mod in _discover():
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
