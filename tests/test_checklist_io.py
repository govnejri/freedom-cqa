"""Чек-лист: YAML ⇄ конфиг ⇄ канонический JSON, хэши, проверка, CLI validate."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cqa.checklist.io import (
    config_hash, criterion_hash, dump_yaml, load_yaml, loads_yaml, to_canonical_json, validate_text,
)
from conftest import CHECKLIST_YAML


def test_load_yaml(checklist):
    cfg = load_yaml(CHECKLIST_YAML)
    assert cfg == checklist
    assert [c.id for c in cfg.criteria] == ["01", "02", "03", "04", "05", "06", "07", "08"]
    assert sum(c.weight for c in cfg.criteria) == 100


def test_dump_yaml_roundtrip(checklist):
    text = dump_yaml(checklist)
    assert loads_yaml(text) == checklist
    assert "general_rules: |" in text          # многострочные строки — блоком
    assert "'01'" in text or '"01"' in text    # id остаётся строкой


def test_canonical_json(checklist):
    s = to_canonical_json(checklist)
    assert "Установление контакта" in s        # ensure_ascii=False
    data = json.loads(s)
    assert json.dumps(data, sort_keys=True, ensure_ascii=False) == s
    assert type(checklist).model_validate_json(s) == checklist


def test_hashes(checklist):
    h = config_hash(checklist)
    assert len(h) == 64 and h == config_hash(load_yaml(CHECKLIST_YAML))
    c04 = checklist.criterion("04")
    changed = checklist.model_copy(update={"criteria": [
        c.model_copy(update={"weight": 25}) if c.id == "04" else c for c in checklist.criteria]})
    assert config_hash(changed) != h
    assert criterion_hash(changed.criterion("04")) != criterion_hash(c04)
    assert criterion_hash(changed.criterion("01")) == criterion_hash(checklist.criterion("01"))


def test_validate_reports_lines():
    text = CHECKLIST_YAML.read_text(encoding="utf-8")
    bad = text.replace("type: flag", "type: flagg").replace("weight: 15", "weight: abc", 1)
    cfg, errors = validate_text(bad)
    assert cfg is None
    locs = {e["loc"]: e for e in errors}
    assert "criteria[1].weight" in locs and "criteria[7].type" in locs
    lines = bad.splitlines()
    assert "weight: abc" in lines[locs["criteria[1].weight"]["line"] - 1]
    assert "type: flagg" in lines[locs["criteria[7].type"]["line"] - 1]


def test_validate_model_rules():
    text = CHECKLIST_YAML.read_text(encoding="utf-8").replace("section: conduct", "section: nope", 1)
    cfg, errors = validate_text(text)
    assert cfg is None and "нет секции" in errors[0]["msg"]
    cfg, errors = validate_text("id: [")
    assert cfg is None and errors[0]["msg"].startswith("YAML")


def test_cli_validate(tmp_path):
    from cqa.cli import app

    r = CliRunner().invoke(app, ["checklist", "validate", str(CHECKLIST_YAML)])
    assert r.exit_code == 0, r.output
    assert "критериев: 8" in r.output
    assert "сумма весов: 100" in r.output

    bad = tmp_path / "bad.yaml"
    bad.write_text(CHECKLIST_YAML.read_text(encoding="utf-8").replace("type: flag", "type: flagg"), encoding="utf-8")
    r = CliRunner().invoke(app, ["checklist", "validate", str(bad)])
    assert r.exit_code == 1
    assert "criteria[7].type" in r.output
