"""Версии чек-листа в БД: seed, неизменяемость, publish/freeze, diff."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cqa.checklist.io import config_hash
from cqa.checklist.store import (
    create_version, diff, find_version, freeze, get_active, get_version, list_versions, publish, seed,
)
from conftest import CHECKLIST_YAML


def _with_weight(cfg, cid, w):
    return cfg.model_copy(update={"criteria": [c.model_copy(update={"weight": w}) if c.id == cid else c
                                               for c in cfg.criteria]})


def test_seed_once(tmp_db, checklist):
    vid = seed(tmp_db, CHECKLIST_YAML)
    assert seed(tmp_db, CHECKLIST_YAML) == vid
    vs = list_versions(tmp_db, "freedom-cqa")
    assert len(vs) == 1
    assert vs[0]["version"] == 1 and vs[0]["status"] == "published" and vs[0]["is_active"]
    cfg, meta = get_active(tmp_db)
    assert cfg == checklist and meta["id"] == vid
    assert meta["config_hash"] == config_hash(checklist)
    assert set(meta["criteria_hashes"]) == {c.id for c in checklist.criteria}


def test_versions_are_immutable(tmp_db, checklist):
    v1 = seed(tmp_db, CHECKLIST_YAML)
    changed = _with_weight(checklist, "04", 25)
    v2 = create_version(tmp_db, "freedom-cqa", changed, parent_id=v1, comment="вес 04")
    assert v2 != v1
    cfg1, m1 = get_version(tmp_db, v1)
    cfg2, m2 = get_version(tmp_db, v2)
    assert cfg1 == checklist and cfg1.criterion("04").weight == 20
    assert cfg2.criterion("04").weight == 25
    assert (m1["version"], m2["version"]) == (1, 2)
    assert m2["status"] == "draft" and m2["parent_id"] == v1 and m2["comment"] == "вес 04"
    assert m1["config_hash"] != m2["config_hash"]
    assert m1["criteria_hashes"]["01"] == m2["criteria_hashes"]["01"]
    assert m1["criteria_hashes"]["04"] != m2["criteria_hashes"]["04"]
    # сохранение того же конфига — тоже новая строка, старая не трогается
    v3 = create_version(tmp_db, "freedom-cqa", changed)
    assert get_version(tmp_db, v3)[1]["version"] == 3
    assert get_version(tmp_db, v1)[0] == checklist
    assert find_version(tmp_db, "freedom-cqa", 2) == v2
    # активная не меняется, пока версию не опубликовали
    assert get_active(tmp_db)[1]["id"] == v1


def test_publish_and_freeze(tmp_db, checklist):
    v1 = seed(tmp_db, CHECKLIST_YAML)
    v2 = create_version(tmp_db, "freedom-cqa", _with_weight(checklist, "07", 10), parent_id=v1)
    publish(tmp_db, v2)
    cfg, meta = get_active(tmp_db)
    assert meta["id"] == v2 and meta["status"] == "published" and cfg.criterion("07").weight == 10
    assert get_version(tmp_db, v1)[1]["is_active"] is False
    freeze(tmp_db, v2)
    meta = get_version(tmp_db, v2)[1]
    assert meta["status"] == "frozen" and meta["is_frozen"] and meta["is_active"]
    publish(tmp_db, v1)
    assert get_active(tmp_db)[1]["id"] == v1
    assert get_version(tmp_db, v2)[1]["status"] == "frozen"     # публикация другой версии не снимает заморозку


def test_errors(tmp_db, checklist):
    with pytest.raises(KeyError):
        get_active(tmp_db)
    with pytest.raises(KeyError):
        get_version(tmp_db, 999)
    with pytest.raises(ValueError):
        create_version(tmp_db, "other", checklist)
    with pytest.raises(KeyError):
        find_version(tmp_db, "freedom-cqa", 7)


def test_diff(checklist):
    assert diff(checklist, checklist) == []

    d = diff(checklist, _with_weight(checklist, "04", 25))
    assert d == [{"scope": "criterion", "id": "04", "change": "changed", "fields": ["weight"],
                  "before": {"weight": 20.0}, "after": {"weight": 25.0}}]

    extra = checklist.criterion("07").model_copy(update={"id": "09", "title": "Новый"})
    b = checklist.model_copy(update={
        "general_rules": checklist.general_rules + "\nНовое правило.",
        "criteria": [c for c in checklist.criteria if c.id != "02"] + [extra],
    })
    d = diff(checklist, b)
    kinds = {(x["scope"], x["id"], x["change"]) for x in d}
    assert kinds == {("config", "general_rules", "changed"), ("criterion", "09", "added"),
                     ("criterion", "02", "removed")}
    added = next(x for x in d if x["change"] == "added")
    assert added["before"] is None and added["after"]["title"] == "Новый"


def test_cli_show(tmp_db):
    from cqa.cli import app

    r = CliRunner().invoke(app, ["checklist", "show"])
    assert r.exit_code == 0, r.output
    assert "версия 1" in r.output and "активная" in r.output
    assert "Установление контакта" in r.output
    r = CliRunner().invoke(app, ["checklist", "show", "--version", "1", "--yaml"])
    assert r.exit_code == 0 and "id: freedom-cqa" in r.output
    r = CliRunner().invoke(app, ["checklist", "show", "--version", "9"])
    assert r.exit_code == 1
