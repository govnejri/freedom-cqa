"""Изменения диалогов для автотестов судьи (config/autotests.yaml, docs/implementation.md §10).

Изменение — одна декларативная операция над нормализованным транскриптом базового диалога:
- insert     — вставить реплику роли `speaker` в место `at` (тексты на RU / KZ / EN);
- cut_end    — обрезать после последней реплики клиента, на которую оператор потом отвечал (уходят финальные
               реплики оператора и событие завершения), и добавить системное событие на языке диалога;
- drop_intro — убрать приветствие и имя из первой реплики оператора по шаблонам языка;
- rename     — заменить имя оператора во всех репликах.

Результат — новый Conversation группы AUTOTEST (uid `AUTOTEST:{external_id}~{mutation}`): реплики собираются
заново как исходная запись API и проходят обычную нормализацию, поэтому ID реплик, участки, события и факты
пересчитываются. Если операция к диалогу неприменима (нет места вставки, шаблон не сработал, оператор уже
попрощался до обрыва) — NotApplicable с причиной, диалог в базу не берётся.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from cqa.ingest.normalize import (
    EventRules,
    load_event_rules,
    make_uid,
    normalize_record,
)
from cqa.ingest.parse import RawMessage, RawRecord
from cqa.models import Conversation, Message
from cqa.settings import ROOT

AUTOTESTS_PATH = ROOT / "config" / "autotests.yaml"
SAME_AS_BASE = "same_as_base"
OPS = ("insert", "cut_end", "drop_intro", "rename")
ANCHORS = ("after_first_client_reply", "before_last_operator")
LANG_FALLBACK = "RU"
APPEND_SHIFT_SEC = 20          # системное событие в конце — через столько секунд после последней реплики


class NotApplicable(Exception):
    """Изменение к диалогу неприменимо (причина — текст исключения)."""


@dataclass
class MutationSpec:
    id: str
    title: str
    op: str
    params: dict[str, Any]
    expect: dict[str, list[str]] | None          # None — same_as_base
    channels: list[str] | None = None            # None — все каналы

    @property
    def same_as_base(self) -> bool:
        return self.expect is None

    @property
    def targets(self) -> list[str] | None:
        """Критерии, которые проверяются (и оцениваются) у абсолютного ожидания; None — все."""
        return None if self.expect is None else list(self.expect)

    def expect_json(self) -> Any:
        return SAME_AS_BASE if self.expect is None else self.expect


@dataclass
class AutotestConfig:
    group: str
    base_group: str
    mutations: dict[str, MutationSpec]
    farewell: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------- конфиг

def _expect(mid: str, raw: Any) -> dict[str, list[str]] | None:
    if raw == SAME_AS_BASE:
        return None
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{mid}: expect — словарь {{критерий: вердикт}} или {SAME_AS_BASE!r}")
    out = {}
    for cid, v in raw.items():
        vals = v if isinstance(v, list) else [v]
        out[str(cid)] = [str(x) for x in vals]
    return out


def parse_config(data: dict[str, Any]) -> AutotestConfig:
    muts: dict[str, MutationSpec] = {}
    for mid, spec in (data.get("mutations") or {}).items():
        spec = spec or {}
        ops = [op for op in OPS if op in spec]
        if len(ops) != 1:
            raise ValueError(f"{mid}: нужна ровно одна операция из {OPS}, найдено {ops or 'ни одной'}")
        op = ops[0]
        params = spec[op] or {}
        if op == "insert" and params.get("at") not in ANCHORS:
            raise ValueError(f"{mid}: insert.at — одно из {ANCHORS}")
        muts[str(mid)] = MutationSpec(id=str(mid), title=str(spec.get("title") or mid), op=op, params=params,
                                      expect=_expect(mid, spec.get("expect")), channels=spec.get("channels"))
    return AutotestConfig(group=str(data.get("group") or "AUTOTEST"), base_group=str(data.get("base_group") or "DEV"),
                          mutations=muts, farewell=data.get("farewell") or {})


def load_config(path: str | Path | None = None) -> AutotestConfig:
    with open(path or AUTOTESTS_PATH, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})


def _by_lang(table: dict[str, Any] | None, lang: str, what: str) -> Any:
    table = table or {}
    v = table.get(lang.upper()) or table.get(LANG_FALLBACK)
    if v is None:
        raise ValueError(f"{what}: нет текста для языка {lang} (и для {LANG_FALLBACK})")
    return v


# ---------------------------------------------------------------- время

_CLOCK = re.compile(r"^(?:(\d+):)?(\d+):(\d{1,2})$")


def shift_ts(ts: str, seconds: int) -> str:
    """Метка + seconds в том же формате (чат `MM:SS` / `H:MM:SS`, звонок ISO 8601). Неразобранная — как есть."""
    s = (ts or "").strip()
    m = _CLOCK.match(s)
    if m:
        h, mm, ss = (int(x) if x else 0 for x in m.groups())
        total = h * 3600 + mm * 60 + ss + seconds
        if m.group(1) is not None:
            return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
        return f"{total // 60:02d}:{total % 60:02d}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return ts
    return (dt + timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------- места в транскрипте

def _talk(m: Message) -> bool:
    """Реплика живого общения с оператором: участок operator, не дубль, не после завершения."""
    return m.segment == "operator" and not m.dup_of and not m.after_end


def first_client_reply(msgs: list[Message]) -> int | None:
    """Индекс первой реплики клиента в участке оператора, на которую оператор потом отвечал."""
    for i, m in enumerate(msgs):
        if m.speaker == "client" and _talk(m) and any(x.speaker == "operator" and _talk(x) for x in msgs[i + 1:]):
            return i
    return None


def last_client_reply(msgs: list[Message]) -> int | None:
    """Индекс последней реплики клиента в участке оператора, после которой оператор ещё писал."""
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.speaker == "client" and _talk(m) and any(x.speaker == "operator" and _talk(x) for x in msgs[i + 1:]):
            return i
    return None


def _operator_idx(msgs: list[Message]) -> list[int]:
    return [i for i, m in enumerate(msgs) if m.speaker == "operator" and _talk(m)]


# ---------------------------------------------------------------- сборка мутанта

Raw = tuple[str, str, str]   # (speaker, text, ts_raw)


def _raw(msgs: list[Message]) -> list[Raw]:
    return [(m.speaker, m.text, m.ts_raw) for m in msgs]


def mutant_external_id(external_id: str, mutation_id: str) -> str:
    return f"{external_id}~{mutation_id}"


def mutant_uid(group: str, external_id: str, mutation_id: str) -> str:
    return make_uid(group, mutant_external_id(external_id, mutation_id))


def rebuild(conv: Conversation, raw: list[Raw], mutation_id: str, group: str,
            rules: EventRules | None = None) -> Conversation:
    """Реплики → запись API → обычная нормализация (mid, участки, события, факты, имя оператора)."""
    rec = RawRecord(
        conversation_id=mutant_external_id(conv.external_id, mutation_id),
        channel=conv.channel,
        language=conv.language,
        transcript=[RawMessage(timestamp=ts, speaker=sp, text=tx) for sp, tx, ts in raw],
        audio_url=None,
    )
    return normalize_record(rec, group, rules or load_event_rules())


# ---------------------------------------------------------------- операции

def _insert(conv: Conversation, spec: MutationSpec) -> list[Raw]:
    p = spec.params
    msgs = conv.messages
    text = _by_lang(p.get("text"), conv.language, f"{spec.id}.insert.text")
    if p["at"] == "after_first_client_reply":
        i = first_client_reply(msgs)
        if i is None:
            raise NotApplicable("нет реплики клиента, на которую оператор отвечал")
        pos = i + 1
    else:  # before_last_operator
        ops = _operator_idx(msgs)
        if len(ops) < 2:
            raise NotApplicable("у оператора меньше двух реплик: вставка стала бы первой репликой оператора")
        pos = ops[-1]
    raw = _raw(msgs)
    raw.insert(pos, (str(p.get("speaker") or "operator"), text, msgs[pos - 1].ts_raw))
    return raw


def _farewell_rx(cfg: AutotestConfig, lang: str) -> list[re.Pattern]:
    pats = (cfg.farewell or {}).get(lang.upper()) or (cfg.farewell or {}).get(LANG_FALLBACK) or []
    return [re.compile(p, re.IGNORECASE) for p in pats]


def _cut_end(conv: Conversation, spec: MutationSpec, cfg: AutotestConfig) -> list[Raw]:
    msgs = conv.messages
    i = last_client_reply(msgs)
    if i is None:
        raise NotApplicable("нет реплики клиента, после которой оператор ещё писал")
    kept = msgs[: i + 1]
    fw = _farewell_rx(cfg, conv.language)
    for m in kept:
        if m.speaker == "operator" and _talk(m) and any(rx.search(m.text) for rx in fw):
            raise NotApplicable(f"оператор уже попрощался до конца ({m.mid}) — обрыв неоднозначен")
    text = _by_lang(spec.params.get("append"), conv.language, f"{spec.id}.cut_end.append")
    raw = _raw(kept)
    raw.append(("system", text, shift_ts(msgs[i].ts_raw, APPEND_SHIFT_SEC)))
    return raw


def _drop_intro(conv: Conversation, spec: MutationSpec) -> list[Raw]:
    name = conv.operator_name
    if not name:
        raise NotApplicable("имя оператора не найдено в первой реплике")
    first = conv.facts.first_operator_mid if conv.facts else None
    idx = next((i for i, m in enumerate(conv.messages) if m.mid == first), None)
    if idx is None:
        raise NotApplicable("нет реплики оператора")
    p = spec.params
    greet = [re.compile(x, re.IGNORECASE) for x in _by_lang(p.get("greeting"), conv.language, f"{spec.id}.greeting")]
    names = [re.compile(x.replace("{name}", re.escape(name)), re.IGNORECASE)
             for x in _by_lang(p.get("name"), conv.language, f"{spec.id}.name")]
    text = conv.messages[idx].text
    g = next((rx for rx in greet if rx.match(text)), None)
    if g is None:
        raise NotApplicable("шаблон приветствия не сработал")
    text = g.sub("", text, count=1)
    n = next((rx for rx in names if rx.search(text)), None)
    if n is None:
        raise NotApplicable("шаблон имени не сработал")
    text = n.sub("", text, count=1).strip().lstrip(",.!;:—–- ").strip()
    raw = _raw(conv.messages)
    if text:
        raw[idx] = (raw[idx][0], text[0].upper() + text[1:], raw[idx][2])
    elif len(_operator_idx(conv.messages)) > 1:
        del raw[idx]
    else:
        raise NotApplicable("после удаления от реплики оператора ничего не осталось")
    return raw


_KZ_COPULA = re.compile(r"^(?:мын|мін|пын|пін|бын|бін)$", re.IGNORECASE)
_BACK_VOWELS = set("аоұыуяёюэ")
_FRONT_VOWELS = set("әөүіеи")
_VOWELS = _BACK_VOWELS | _FRONT_VOWELS
_RU_MALE_A = {"илья", "никита", "кузьма", "фома", "лука", "данила", "савва"}


def kz_copula(name: str) -> str:
    """Связка «я — X» для казахского имени: Ерлан → мын, Әсел → мін, Самат → пын."""
    low = name.casefold()
    last_vowel = next((ch for ch in reversed(low) if ch in _VOWELS), "а")
    front = last_vowel in _FRONT_VOWELS
    last = low[-1]
    if last in _VOWELS or last in "лрнмңйу":
        stem = "м"
    elif last in "зж":
        stem = "б"
    else:
        stem = "п"
    return stem + ("ін" if front else "ын")


def pick_name(cfg_names: dict[str, Any] | None, lang: str, old: str) -> str:
    table = _by_lang(cfg_names, lang, "rename.names")
    if isinstance(table, list):
        pool = table
    else:
        low = old.casefold()
        female = low.endswith(("а", "я")) and low not in _RU_MALE_A
        pool = table.get("female" if female else "male") or table.get("any") or []
        if not pool:
            pool = [n for v in table.values() for n in (v or [])]
    for n in pool:
        if str(n).casefold() != old.casefold():
            return str(n)
    raise NotApplicable("нет другого имени для замены")


def _rename(conv: Conversation, spec: MutationSpec) -> tuple[list[Raw], str]:
    old = conv.operator_name
    if not old:
        raise NotApplicable("имя оператора не найдено в первой реплике")
    new = pick_name(spec.params.get("names"), conv.language, old)
    rx = re.compile(rf"(?<!\w){re.escape(old)}(\w*)")

    def repl(m: re.Match) -> str:
        suf = m.group(1)
        if not suf:
            return new
        if _KZ_COPULA.match(suf):
            return new + kz_copula(new)
        return m.group(0)       # другая словоформа — не трогаем

    raw = [(sp, rx.sub(repl, tx), ts) for sp, tx, ts in _raw(conv.messages)]
    if raw == _raw(conv.messages):
        raise NotApplicable("имя оператора не встречается в репликах")
    return raw, new


# ---------------------------------------------------------------- точка входа

def applicable_channel(conv: Conversation, spec: MutationSpec) -> bool:
    return spec.channels is None or conv.channel in spec.channels


def mutate(conv: Conversation, spec: MutationSpec, cfg: AutotestConfig,
           rules: EventRules | None = None) -> Conversation:
    """Мутант базового диалога (ещё не сохранён). NotApplicable — изменение неприменимо к этому диалогу."""
    if not applicable_channel(conv, spec):
        raise NotApplicable(f"канал «{conv.channel}» не входит в {spec.channels}")
    rules = rules or load_event_rules()
    new_name = None
    if spec.op == "insert":
        raw = _insert(conv, spec)
    elif spec.op == "cut_end":
        raw = _cut_end(conv, spec, cfg)
    elif spec.op == "drop_intro":
        raw = _drop_intro(conv, spec)
    elif spec.op == "rename":
        raw, new_name = _rename(conv, spec)
    else:  # pragma: no cover — parse_config не пропустит
        raise ValueError(f"неизвестная операция {spec.op}")
    out = rebuild(conv, raw, spec.id, cfg.group, rules)
    _check(conv, out, spec, cfg, new_name)
    return out


def _check(base: Conversation, out: Conversation, spec: MutationSpec, cfg: AutotestConfig, new_name: str | None) -> None:
    """Структура мутанта после нормализации: событие завершения распознано, имя убрано или заменено."""
    if spec.op == "cut_end":
        want = spec.params.get("event")
        last = out.messages[-1]
        if want and last.event_type != want:
            raise ValueError(f"{spec.id}: событие «{last.text}» распознано как {last.event_type}, ожидалось {want}"
                             " — проверьте config/events.yaml")
    elif spec.op == "drop_intro":
        if out.operator_name:
            raise NotApplicable(f"имя оператора всё ещё находится: {out.operator_name}")
        first = out.by_mid().get(out.facts.first_operator_mid) if out.facts else None
        greet = [re.compile(x, re.IGNORECASE) for x in _by_lang(spec.params.get("greeting"), out.language, "greeting")]
        if first is not None and any(rx.match(first.text) for rx in greet):
            raise NotApplicable("первая реплика оператора всё ещё начинается с приветствия")
    elif spec.op == "rename":
        if out.operator_name != new_name:
            raise NotApplicable(f"после замены имя оператора {out.operator_name!r}, ожидалось {new_name!r}")
