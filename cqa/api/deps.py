"""Общие зависимости роутов: соединение с БД, профили ролей, ошибки валидации в формате ValidationErrorDTO.

Соединение API одно на приложение (app.state.con) и используется только из event loop: все роуты с БД —
`async def`, вызовы SQLite синхронные и короткие. У воркера своё соединение (cqa/api/worker.py),
у фоновых потоков (скачивание аудио, загрузка из API) — тоже свои.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from cqa.settings import get_settings, load_models_config

ROLES = ("judge", "strong", "cross_check", "signals")
FAKE_PROFILE = "fake"
_ROLES_KEY = "roles"            # settings.key: {"judge": "local", ...} — переопределения профилей ролей


async def get_con(request: Request) -> sqlite3.Connection:
    return request.app.state.con


def is_fake(request: Request) -> bool:
    return bool(getattr(request.app.state, "fake", False))


# ---------------------------------------------------------------- профили ролей

def profile_names() -> list[str]:
    return list(load_models_config().get("profiles") or {})


def default_roles() -> dict[str, str]:
    roles = load_models_config().get("roles") or {}
    return {r: roles[r] for r in ROLES if r in roles}


def role_overrides(con: sqlite3.Connection) -> dict[str, str]:
    row = con.execute("SELECT value_json FROM settings WHERE key=?", (_ROLES_KEY,)).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
    except ValueError:
        return {}
    known = set(profile_names())
    return {k: v for k, v in (data or {}).items() if k in ROLES and isinstance(v, str) and v in known}


def save_role_overrides(con: sqlite3.Connection, overrides: dict[str, str]) -> None:
    con.execute(
        "INSERT INTO settings(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
        (_ROLES_KEY, json.dumps(overrides, ensure_ascii=False, sort_keys=True)),
    )


def effective_roles(con: sqlite3.Connection, fake: bool) -> dict[str, str]:
    """Роль → профиль: models.yaml, поверх — переопределения из таблицы settings; в режиме fake — всё fake."""
    roles = {**default_roles(), **role_overrides(con)}
    if fake:
        return {r: FAKE_PROFILE for r in roles} | {r: FAKE_PROFILE for r in ROLES}
    return roles


def resolve_role(con: sqlite3.Connection, role: str, fake: bool) -> str:
    roles = effective_roles(con, fake)
    if role not in roles:
        raise HTTPException(500, f"в config/models.yaml нет роли {role!r}")
    return roles[role]


def profile_available(name: str) -> bool:
    """Есть ли всё нужное профилю: ключ API или адрес локального сервера (значения не раскрываются)."""
    cfg = (load_models_config().get("profiles") or {}).get(name)
    if not cfg:
        return False
    if cfg.get("provider") == "fake":
        return True
    for env in (cfg.get("api_key_env"), cfg.get("base_url_env")):
        if env and not env_present(env):
            return False
    return True


def env_present(name: str) -> bool:
    if os.environ.get(name):
        return True
    return bool(getattr(get_settings(), name.lower(), None))


# ---------------------------------------------------------------- ошибки

def loc_str(loc: Iterable[Any]) -> str:
    """('criteria', 3, 'weight') → 'criteria.3.weight'."""
    return ".".join(str(p) for p in loc)


def pydantic_errors(errors: Iterable[dict], skip_prefix: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """Ошибки pydantic → [ValidationErrorDTO]. skip_prefix — служебные начала loc ('body', 'query', …)."""
    out = []
    for e in errors:
        loc = list(e.get("loc") or ())
        while loc and loc[0] in skip_prefix:
            loc.pop(0)
        msg = str(e.get("msg", "")).removeprefix("Value error, ")
        out.append({"loc": loc_str(loc), "msg": msg})
    return out


class ApiValidationError(Exception):
    """422 со списком ValidationErrorDTO."""

    def __init__(self, errors: list[dict[str, str]]):
        super().__init__("; ".join(f"{e['loc']}: {e['msg']}" for e in errors))
        self.errors = errors


def invalid(loc: str, msg: str) -> ApiValidationError:
    return ApiValidationError([{"loc": loc, "msg": msg}])


def validation_response(errors: list[dict[str, str]]) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": errors})


def not_found(what: str) -> HTTPException:
    return HTTPException(404, f"не найдено: {what}")
