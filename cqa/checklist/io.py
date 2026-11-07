"""Чек-лист: YAML ⇄ ChecklistConfig ⇄ канонический JSON, хэши, проверка с номерами строк YAML."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cqa.checklist.model import ChecklistConfig, Criterion


# ---------------------------------------------------------------- YAML

def load_yaml(path: str | Path) -> ChecklistConfig:
    """Прочитать YAML чек-листа. Ошибки формата — pydantic.ValidationError / yaml.YAMLError."""
    return loads_yaml(Path(path).read_text(encoding="utf-8"))


def loads_yaml(text: str) -> ChecklistConfig:
    return ChecklistConfig.model_validate(yaml.safe_load(text))


class _Dumper(yaml.SafeDumper):
    """Многострочные строки — блоком «|», чтобы YAML оставался читаемым и правился руками."""


def _str_repr(dumper: yaml.SafeDumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_repr)


def dump_yaml(cfg: ChecklistConfig) -> str:
    """Конфиг → YAML. Поля со значениями по умолчанию опускаются; load_yaml(dump_yaml(cfg)) == cfg."""
    data = cfg.model_dump(mode="json", exclude_defaults=True)
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=10_000)


# ---------------------------------------------------------------- канонический JSON и хэши

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def to_canonical_json(cfg: ChecklistConfig) -> str:
    return _canonical(cfg.model_dump(mode="json"))


def from_json(text: str) -> ChecklistConfig:
    return ChecklistConfig.model_validate_json(text)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_hash(cfg: ChecklistConfig) -> str:
    return _sha256(to_canonical_json(cfg))


def criterion_hash(crit: Criterion) -> str:
    return _sha256(_canonical(crit.model_dump(mode="json")))


def criteria_hashes(cfg: ChecklistConfig) -> dict[str, str]:
    return {c.id: criterion_hash(c) for c in cfg.criteria}


# ---------------------------------------------------------------- проверка

def _yaml_line(root: yaml.Node | None, loc: tuple) -> int | None:
    """Номер строки (1-based) узла YAML по пути pydantic loc; None — не нашли или ошибка всего конфига."""
    if root is None or not loc:
        return None
    node, line = root, None
    for part in loc:
        if isinstance(node, yaml.MappingNode):
            nxt = None
            for k, v in node.value:
                if isinstance(k, yaml.ScalarNode) and k.value == str(part):
                    nxt = v
                    line = k.start_mark.line + 1
                    break
            node = nxt
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int) and 0 <= part < len(node.value):
            node = node.value[part]
            line = node.start_mark.line + 1
        else:
            break
        if node is None:
            break
    return line


def _loc_str(loc: tuple) -> str:
    out = ""
    for p in loc:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


def validate_text(text: str) -> tuple[ChecklistConfig | None, list[dict]]:
    """Разобрать YAML и проверить. Возвращает (cfg | None, ошибки [{loc, line, msg}])."""
    try:
        root = yaml.compose(text)
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return None, [{"loc": "", "line": mark.line + 1 if mark else None, "msg": f"YAML: {e}"}]
    if not isinstance(data, dict):
        return None, [{"loc": "", "line": 1, "msg": "ожидался словарь верхнего уровня"}]
    try:
        cfg = ChecklistConfig.model_validate(data)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = tuple(err.get("loc") or ())
            msg = str(err.get("msg", "")).removeprefix("Value error, ")
            errors.append({"loc": _loc_str(loc), "line": _yaml_line(root, loc), "msg": msg})
        return None, errors
    return cfg, []


def validate_file(path: str | Path) -> tuple[ChecklistConfig | None, list[dict]]:
    return validate_text(Path(path).read_text(encoding="utf-8"))


def warnings(cfg: ChecklistConfig) -> list[str]:
    """Не ошибки, но стоит посмотреть: сумма весов, пустые секции."""
    out = []
    total = sum(c.weight for c in cfg.enabled_criteria())
    if abs(total - cfg.scoring.max_total) > 1e-9:
        out.append(f"сумма весов включённых критериев {total:g} ≠ max_total {cfg.scoring.max_total}"
                   " (итог всё равно нормируется к max_total)")
    used = {c.section for c in cfg.enabled_criteria()}
    for s in cfg.sections:
        if s.id not in used:
            out.append(f"в секции {s.id!r} нет включённых критериев")
    return out
