"""Настройки: .env + config/models.yaml. Секреты только из окружения, в логи не попадают."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from cqa.llm.base import Profile

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    cqa_api_url: str | None = None          # ссылка API организаторов БЕЗ group_id (содержит sig=, не логировать)
    cqa_group_id: str = "DEV"
    gemini_api_key: str | None = None
    local_llm_base_url: str | None = None

    db_path: Path = ROOT / "var" / "cqa.db"
    audio_dir: Path = ROOT / "var" / "audio"
    raw_dir: Path = ROOT / "var" / "raw"
    config_dir: Path = ROOT / "config"
    checklist_path: Path = ROOT / "config" / "checklists" / "freedom-cqa.yaml"
    frontend_dir: Path = ROOT / "frontend"
    build_dir: Path = ROOT / "build"
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # pydantic-settings читает .env сам, но провайдерам нужны переменные окружения по имени из models.yaml
    for k in ("GEMINI_API_KEY", "LOCAL_LLM_BASE_URL"):
        v = getattr(s, k.lower())
        if v and not os.environ.get(k):
            os.environ[k] = v
    return s


@lru_cache
def load_models_config() -> dict:
    with open(get_settings().config_dir / "models.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_profile(name: str) -> Profile:
    cfg = load_models_config()["profiles"][name]
    return Profile(name=name, **cfg)


def role_profile(role: str) -> Profile:
    """Профиль для роли (judge/strong/cross_check/signals). Переопределения из БД (settings) — в api/routes."""
    return get_profile(load_models_config()["roles"][role])
