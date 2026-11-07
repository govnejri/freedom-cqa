"""Общие фикстуры. Тесты не ходят в сеть: LLM — только профиль fake."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEV_JSON = ROOT / "data" / "dev.json"
GOLD_CSV = ROOT / "eval" / "gold.csv"
CHECKLIST_YAML = ROOT / "config" / "checklists" / "freedom-cqa.yaml"

requires_dev = pytest.mark.skipif(not DEV_JSON.exists(), reason="нет data/dev.json")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Чистая БД с применённой схемой; settings.db_path указывает на неё."""
    from cqa.settings import get_settings
    from cqa.db.conn import connect, migrate

    s = get_settings()
    monkeypatch.setattr(s, "db_path", tmp_path / "cqa.db")
    monkeypatch.setattr(s, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(s, "raw_dir", tmp_path / "raw")
    con = connect(s.db_path)
    migrate(con)
    yield con
    con.close()


@pytest.fixture
def checklist():
    from cqa.checklist.model import ChecklistConfig

    with open(CHECKLIST_YAML, encoding="utf-8") as f:
        return ChecklistConfig.model_validate(yaml.safe_load(f))
