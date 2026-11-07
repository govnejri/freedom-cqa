"""Состояние сервиса и настройки ролей (переключатель «облако/локально»)."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from cqa.api.deps import (
    ROLES,
    default_roles,
    effective_roles,
    env_present,
    get_con,
    invalid,
    is_fake,
    profile_available,
    profile_names,
    role_overrides,
    save_role_overrides,
)
from cqa.api.schemas import HealthDTO
from cqa.checklist.store import DEFAULT_CHECKLIST
from cqa.settings import load_models_config

router = APIRouter(tags=["service"])

KEYS = ("GEMINI_API_KEY", "LOCAL_LLM_BASE_URL", "CQA_API_URL")


@router.get("/health", response_model=HealthDTO)
async def health(request: Request, con: sqlite3.Connection = Depends(get_con)):
    db_ok = True
    active = None
    try:
        con.execute("SELECT 1").fetchone()
        row = con.execute("SELECT active_version_id FROM checklists ORDER BY id = ? DESC, id LIMIT 1",
                          (DEFAULT_CHECKLIST,)).fetchone()
        active = row[0] if row else None
    except sqlite3.Error:
        db_ok = False
    try:
        roles = effective_roles(con, is_fake(request)) if db_ok else {}
    except Exception:  # noqa: BLE001 — models.yaml сломан: health всё равно отвечает
        roles = {}
    worker = getattr(request.app.state, "worker", None)
    return HealthDTO(
        ok=db_ok and (worker is None or worker.running),
        db=db_ok, roles=roles, keys={k: env_present(k) for k in KEYS}, active_checklist_version_id=active,
    )


# ---------------------------------------------------------------- настройки

class SettingsBody(BaseModel):
    roles: dict[str, str | None] = {}     # роль → профиль; null или "" — вернуть профиль из models.yaml


def _settings(con: sqlite3.Connection, fake: bool) -> dict[str, Any]:
    profiles = load_models_config().get("profiles") or {}
    return {
        "roles": effective_roles(con, fake),
        "defaults": default_roles(),
        "overrides": role_overrides(con),
        "fake": fake,
        "profiles": [
            {"name": n, "provider": p.get("provider"), "model": p.get("model"),
             "local": p.get("provider") == "openai_compat", "available": profile_available(n)}
            for n, p in profiles.items()
        ],
    }


@router.get("/settings")
async def get_settings_(request: Request, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Профили ролей (действующие, по умолчанию, переопределения) и доступные профили из models.yaml."""
    return _settings(con, is_fake(request))


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request, con: sqlite3.Connection = Depends(get_con)) -> dict[str, Any]:
    """Переопределить профиль ролей: {"roles": {"judge": "local"}}. null — вернуть значение из models.yaml."""
    names = set(profile_names())
    overrides = role_overrides(con)
    for role, profile in body.roles.items():
        if role not in ROLES:
            raise invalid(f"roles.{role}", f"нет роли {role!r}; есть: {', '.join(ROLES)}")
        if not profile:
            overrides.pop(role, None)
            continue
        if profile not in names:
            raise invalid(f"roles.{role}", f"нет профиля {profile!r}; есть: {', '.join(sorted(names))}")
        overrides[role] = profile
    save_role_overrides(con, overrides)
    return _settings(con, is_fake(request))
