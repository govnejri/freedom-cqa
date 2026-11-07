"""FastAPI-приложение CQA: REST API под /api (docs/implementation.md §8), фоновый воркер прогонов, раздача UI.

create_app(dev, fake):
- lifespan: migrate → sync_yaml чек-листа → импорт eval/gold.csv (если gold_labels пуста) → импорт data/dev.json
  без аудио (если в БД нет диалогов) → запуск воркера (cqa/api/worker.py);
- fake=True — все роли используют профиль `fake` (демо без сети и тесты);
- `/` — build/cqa.html (если сборки нет — попытка собрать через cqa.webui.build); dev=True — frontend/ как есть.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from cqa.api.deps import (
    FAKE_PROFILE,
    ApiValidationError,
    pydantic_errors,
    validation_response,
)
from cqa.settings import ROOT, get_settings

log = logging.getLogger(__name__)

DEV_JSON = ROOT / "data" / "dev.json"
GOLD_CSV = ROOT / "eval" / "gold.csv"
ENV_DEV = "CQA_SERVE_DEV"
ENV_FAKE = "CQA_SERVE_FAKE"

_build_lock = threading.Lock()
_build_tried = False


# ---------------------------------------------------------------- старт

def bootstrap(con) -> dict:
    """Подготовка БД при старте. Каждый шаг независим: сбой шага пишется в лог, сервер всё равно стартует."""
    from cqa.db.conn import migrate

    s = get_settings()
    out: dict = {}
    migrate(con)

    try:
        from cqa.checklist.store import sync_yaml
        vid, created = sync_yaml(con, s.checklist_path)
        out["checklist_version_id"] = vid
        if created:
            log.info("чек-лист изменён в YAML → новая активная версия id=%s", vid)
    except Exception:  # noqa: BLE001
        log.exception("не удалось синхронизировать чек-лист из %s", s.checklist_path)

    try:
        empty = con.execute("SELECT COUNT(*) FROM gold_labels").fetchone()[0] == 0
        if empty and GOLD_CSV.is_file():
            from cqa.checklist.store import get_active
            from cqa.quality.gold import import_gold
            try:
                cfg = get_active(con)[0]
            except KeyError:
                cfg = None
            out["gold"] = import_gold(con, GOLD_CSV, cfg=cfg)
            log.info("эталон %s: %s", GOLD_CSV.name, out["gold"])
    except Exception:  # noqa: BLE001
        log.exception("не удалось импортировать эталон %s", GOLD_CSV)

    try:
        no_dialogs = con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        if no_dialogs and DEV_JSON.is_file():
            from cqa.ingest.service import ingest_payload
            payload = json.loads(DEV_JSON.read_text(encoding="utf-8"))
            report = ingest_payload(con, payload, "file", raw_path=DEV_JSON)
            out["dev"] = report.summary()
            log.info("импорт %s (без аудио): %s", DEV_JSON.name, report.summary())
    except Exception:  # noqa: BLE001
        log.exception("не удалось импортировать %s", DEV_JSON)
    return out


# ---------------------------------------------------------------- UI

def ensure_build() -> Path | None:
    """build/cqa.html; если нет — одна попытка собрать через cqa.webui.build (если модуль есть)."""
    global _build_tried
    s = get_settings()
    path = Path(s.build_dir) / "cqa.html"
    if path.is_file():
        return path
    with _build_lock:
        if not _build_tried:
            _build_tried = True
            try:
                mod = importlib.import_module("cqa.webui.build")
            except ModuleNotFoundError:
                mod = None
            fn = getattr(mod, "build", None)
            if callable(fn):
                try:
                    try:
                        fn(src=Path(s.frontend_dir), out=path)
                    except TypeError:          # другая сигнатура: сборка по умолчанию
                        fn()
                    log.info("UI собран: %s", path)
                except Exception:  # noqa: BLE001
                    log.exception("сборка UI не удалась")
    return path if path.is_file() else None


_NO_UI = """<!doctype html><meta charset="utf-8"><title>CQA</title>
<p>Интерфейс не собран: выполните <code>uv run cqa build-ui</code> или запустите <code>cqa serve --dev</code>.
API: <a href="/docs">/docs</a>, <a href="/api/health">/api/health</a>.</p>"""


# ---------------------------------------------------------------- приложение

def create_app(dev: bool = False, fake: bool = False, *, worker: bool = True) -> FastAPI:
    from cqa.api.routes import (
        appeals,
        checklists,
        conversations,
        datasets,
        evaluations,
        export,
        health,
        runs,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from cqa.api.worker import Worker
        from cqa.db.conn import connect

        s = get_settings()
        con = connect(s.db_path)
        bootstrap(con)
        app.state.con = con
        app.state.bg_tasks = set()
        w = Worker(s.db_path, only_profile=FAKE_PROFILE if fake else None) if worker else None
        app.state.worker = w
        if w is not None:
            w.start()
        log.info("CQA API: БД %s, режим %s%s", s.db_path, "fake" if fake else "обычный", ", dev UI" if dev else "")
        try:
            yield
        finally:
            if w is not None:
                await w.stop()
            con.close()

    app = FastAPI(title="CQA API", version="0.1.0", lifespan=lifespan,
                  description="Conversation Quality Analytics — оценка диалогов поддержки по чек-листу")
    app.state.fake = fake
    app.state.dev = dev
    app.state.worker = None
    app.state.bg_tasks = set()

    app.add_middleware(CORSMiddleware, allow_origins=list(get_settings().cors_origins), allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["Content-Disposition", "Content-Range", "Accept-Ranges"])

    @app.exception_handler(ApiValidationError)
    async def _api_validation(_request: Request, exc: ApiValidationError):
        return validation_response(exc.errors)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_request: Request, exc: RequestValidationError):
        return validation_response(pydantic_errors(exc.errors(), ("body", "query", "path", "header", "form", "cookie")))

    api = APIRouter(prefix="/api")
    for mod in (health, datasets, conversations, evaluations, runs, checklists, appeals, export):
        api.include_router(mod.router)
    app.include_router(api)

    s = get_settings()
    if dev:
        app.mount("/", StaticFiles(directory=s.frontend_dir, html=True, check_dir=False), name="frontend")
    else:
        @app.get("/", include_in_schema=False)
        async def index():
            path = await run_in_threadpool(ensure_build)
            if path is None:
                return HTMLResponse(_NO_UI, status_code=503)
            return FileResponse(path, media_type="text/html; charset=utf-8")
    return app


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def app_from_env() -> FastAPI:
    """Фабрика для uvicorn (`cqa serve`, в том числе с --reload): режимы из CQA_SERVE_DEV / CQA_SERVE_FAKE."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        from cqa.llm.redact import install
        install()
    except ImportError:
        pass
    return create_app(dev=_flag(ENV_DEV), fake=_flag(ENV_FAKE))
