"""Промпт судьи критерия (docs/implementation.md §5).

Сообщения: [system, user A (диалог), user B (критерий)].
system и часть A одинаковы для всех критериев одного диалога — общий префикс для кэша провайдера.
Часть B (критерий) — последняя. В промпте нет дат, run_id и случайных значений.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from itertools import groupby
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from cqa.checklist.model import ChecklistConfig, Criterion
from cqa.llm.base import ChatMessage
from cqa.models import Conversation, Ending, Facts

PROMPTS_DIR = Path(__file__).with_name("prompts")
TEMPLATES = ("system.j2", "dialog.j2", "criterion.j2", "retry.j2")


def _templates_hash() -> str:
    """Шаблоны + этот модуль: правка формулировок фактов или разметки транскрипта тоже меняет версию."""
    h = hashlib.sha256()
    for path in [*(PROMPTS_DIR / n for n in sorted(TEMPLATES)), Path(__file__)]:
        h.update(path.name.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


PROMPT_VERSION = "p1-" + _templates_hash()[:8]

_ENV = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
    undefined=StrictUndefined,
)

ROLE_LABEL = {"client": "КЛИЕНТ", "operator": "ОПЕРАТОР", "bot": "БОТ", "system": "СИСТЕМА", "unknown": "НЕИЗВЕСТНО"}
SEGMENT_HEADER = {
    "bot": "— бот —",
    "waiting": "— ожидание оператора —",
    "operator": "— оператор —",
    "hold": "— УДЕРЖАНИЕ: клиент не слышит —",
}
HOLD_END = "— конец удержания —"
CHANNEL_LABEL = {"agent chat": "чат", "voice call": "звонок"}
LANGUAGE_LABEL = {"RU": "русский (RU)", "KZ": "казахский (KZ)", "EN": "английский (EN)"}
ENDING_LABEL = {
    "operator_closed": "оператор закрыл чат",
    "connection_lost": "соединение с клиентом прервано",
    "client_left": "клиент покинул чат",
    "client_ended_call": "клиент завершил звонок",
}
TYPE_TEXT = {
    "yes_no": "да/нет",
    "scale": "шкала",
    "flag": "флаг грубого нарушения: «Да» — нарушение выявлено, «Нет» — не выявлено",
}

_TAG = re.compile(r"<(/?)(transcript|criterion)\b", re.I)
_ISO_TIME = re.compile(r"T(\d{2}:\d{2}:\d{2})")
_WS = re.compile(r"\s+")


# ---------- части ----------

def _q(v: str) -> str:
    return f"«{v}»"


def fmt_time(ts_raw: str) -> str:
    """Чат — ts_raw как есть (MM:SS); звонок (ISO 8601) — HH:MM:SS."""
    m = _ISO_TIME.search(ts_raw or "")
    return m.group(1) if m else (ts_raw or "").strip()


def clean_text(text: str) -> str:
    """Одна строка; теги блоков промпта внутри текста экранируются."""
    text = _WS.sub(" ", text or "").strip()
    return _TAG.sub(lambda m: f"<\\{m.group(1)}{m.group(2)}" if m.group(1) else f"<\\{m.group(2)}", text)


def transcript_lines(conv: Conversation) -> list[str]:
    """Строки `mN | время | РОЛЬ | текст` с заголовками участков перед каждой сменой участка.

    Заголовок «— бот —» ставится, только если в участке есть реплики бота: в звонках начало до первой
    реплики оператора тоже размечено как bot, но бота там нет.
    """
    lines: list[str] = []
    prev = None
    for seg, block in groupby(conv.messages, key=lambda m: m.segment):
        block = list(block)
        if prev == "hold":
            lines.append(HOLD_END)
        if not (prev == "hold" and seg == "operator") and not (seg == "bot" and all(m.speaker != "bot" for m in block)):
            lines.append(SEGMENT_HEADER.get(seg, f"— {seg} —"))
        for m in block:
            role = ROLE_LABEL.get(m.speaker, m.speaker.upper())
            if m.dup_of:
                role += f" (дубль {m.dup_of})"
            if m.after_end:
                role += " (после завершения)"
            lines.append(f"{m.mid} | {fmt_time(m.ts_raw)} | {role} | {clean_text(m.text)}")
        prev = seg
    if prev == "hold":
        lines.append(HOLD_END)
    return lines


@lru_cache(maxsize=1)
def _event_types() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        from cqa.settings import get_settings
        with open(get_settings().config_dir / "events.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return tuple(cfg.get("ending_types") or ()), tuple(cfg.get("abrupt_ending_types") or ())
    except OSError:
        return ("operator_closed", "connection_lost", "client_left", "client_ended_call"), \
               ("connection_lost", "client_left", "client_ended_call")


def derive_facts(conv: Conversation) -> Facts:
    """Минимальные факты по разметке реплик, если ingest их не посчитал (conv.facts is None)."""
    ending_types, abrupt_types = _event_types()
    msgs = conv.messages
    live_ops = [m for m in msgs if m.speaker == "operator" and m.segment != "hold"]
    first_op = live_ops[0].mid if live_ops else None
    end_msg = next((m for m in msgs if m.event_type in ending_types), None)
    before_end = msgs[: msgs.index(end_msg)] if end_msg else msgs
    last_op = next((m for m in reversed(before_end) if m.speaker == "operator" and m.segment != "hold"), None)
    last_cl = next((m for m in reversed(before_end) if m.speaker == "client"), None)
    abrupt = bool(end_msg and end_msg.event_type in abrupt_types and last_cl
                  and (last_op is None or last_op.idx < last_cl.idx))
    hold = [m.mid for m in msgs if m.segment == "hold"]
    return Facts(
        channel=conv.channel,
        language=conv.language,
        first_operator_mid=first_op,
        escalation_mid=next((m.mid for m in msgs if m.event_type == "escalated"), None),
        operator_joined_mid=next((m.mid for m in msgs if m.event_type == "operator_joined"), None),
        first_reaction_sec=None,
        client_msgs_before_operator=[m.mid for m in msgs if m.speaker == "client" and m.segment in ("bot", "waiting")],
        id_events=[m.mid for m in msgs if m.event_type == "identity_verified"],
        hold_range=(hold[0], hold[-1]) if hold else None,
        ending=Ending(
            type=end_msg.event_type if end_msg else None,
            event_mid=end_msg.mid if end_msg else None,
            last_operator_mid=last_op.mid if last_op else None,
            last_client_mid=last_cl.mid if last_cl else None,
            abrupt=abrupt,
        ),
        after_end_mids=[m.mid for m in msgs if m.after_end],
    )


def _mids(mids: list[str]) -> str:
    return ", ".join(mids)


def fact_lines(conv: Conversation) -> list[str]:
    f = conv.facts or derive_facts(conv)
    is_call = conv.channel == "voice call"
    out: list[str] = []
    out.append(f"Первая реплика оператора: {f.first_operator_mid}." if f.first_operator_mid
               else "Реплик оператора с клиентом нет.")
    if f.escalation_mid:
        out.append(f"Перевод на оператора (эскалация): {f.escalation_mid}.")
    if f.operator_joined_mid:
        out.append(f"Оператор подключился: {f.operator_joined_mid}.")
    if f.client_msgs_before_operator:
        out.append("Реплики клиента до прихода оператора (боту и в ожидании; оператору они известны): "
                   f"{_mids(f.client_msgs_before_operator)}.")
    elif f.escalation_mid:
        out.append("Реплик клиента до прихода оператора нет.")
    if f.waits:
        parts = []
        for w in f.waits:
            s = f"после {w.after_mid} (клиент) ответ {w.operator_mid} через {int(round(w.seconds))} с"
            if w.prev_operator_mid:
                s += f", последняя реплика оператора перед паузой — {w.prev_operator_mid}"
            parts.append(s)
        out.append("Паузы оператора дольше 60 с: " + "; ".join(parts) + ".")
    else:
        out.append("Пауз оператора дольше 60 с нет.")
    if f.id_events:
        out.append(f"Системное подтверждение личности: {_mids(f.id_events)}.")
    else:
        out.append("Системного сообщения о подтверждении личности нет"
                   + (" (в звонке проверка проходит голосом — смотри реплики)." if is_call else "."))
    if f.hold_range:
        out.append(f"Удержание (клиент не слышит): {f.hold_range[0]}–{f.hold_range[1]}.")
    e = f.ending
    if e.type:
        s = f"Завершение: {ENDING_LABEL.get(e.type, e.type)}"
        s += f" ({e.event_mid})" if e.event_mid else ""
        if e.seconds_since_last_client is not None:
            s += f", через {int(round(e.seconds_since_last_client))} с после последней реплики клиента"
        out.append(s + ".")
    else:
        out.append("Системного события завершения нет.")
    tail = []
    if e.last_operator_mid:
        tail.append(f"последняя реплика оператора — {e.last_operator_mid}")
    if e.last_client_mid:
        tail.append(f"последняя реплика клиента — {e.last_client_mid}")
    if tail:
        out.append("До завершения: " + "; ".join(tail) + ".")
    if e.abrupt:
        out.append("Диалог оборвался раньше, чем оператор мог его завершить.")
    if f.after_end_mids:
        out.append(f"Реплики после завершения: {_mids(f.after_end_mids)}.")
    return out


def _fmt_weight(crit: Criterion) -> str:
    if crit.type == "flag":
        return "0 (флаг, в итог не входит)"
    w = crit.weight
    return str(int(w)) if float(w).is_integer() else str(w)


def violation_text(crit: Criterion) -> str:
    if crit.type == "flag":
        return "вердикт «Да»"
    if crit.type == "scale":
        return f"любая ступень ниже {crit.top}"
    return "вердикт «Нет»"


# ---------- рендер ----------

def render_system(cfg: ChecklistConfig) -> str:
    return _ENV.get_template("system.j2").render(
        title=cfg.title, general_rules=cfg.general_rules.strip(), principles=cfg.principles.strip(),
    ).strip()


def render_dialog(conv: Conversation, cfg: ChecklistConfig) -> str:
    return _ENV.get_template("dialog.j2").render(
        channel_rules=(cfg.channel_rules.get(conv.channel) or "").strip(),
        channel_label=CHANNEL_LABEL.get(conv.channel, conv.channel),
        language_label=LANGUAGE_LABEL.get(conv.language, conv.language),
        facts=fact_lines(conv),
        lines=transcript_lines(conv),
    ).strip()


def render_criterion(crit: Criterion) -> str:
    scale = sorted(crit.scale or [], key=lambda s: -s.value)
    return _ENV.get_template("criterion.j2").render(
        c=crit,
        type_text=TYPE_TEXT.get(crit.type, crit.type),
        weight=_fmt_weight(crit),
        scale=scale,
        verdicts=", ".join(_q(v) for v in crit.allowed_verdicts()),
        violation=violation_text(crit),
        categories=", ".join(_q(c) for c in crit.flag_categories),
        roles=", ".join(ROLE_LABEL.get(r, r.upper()) for r in crit.evidence_roles),
    ).strip()


def render_retry(errors: list[str]) -> str:
    return _ENV.get_template("retry.j2").render(errors=errors).strip()


def build_messages(conv: Conversation, cfg: ChecklistConfig, crit: Criterion) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=render_system(cfg)),
        ChatMessage(role="user", content=render_dialog(conv, cfg)),
        ChatMessage(role="user", content=render_criterion(crit)),
    ]


def build_retry(messages: list[ChatMessage], answer_text: str, errors: list[str]) -> list[ChatMessage]:
    """Исходный диалог + ответ модели + сообщение со списком ошибок."""
    return [*messages,
            ChatMessage(role="assistant", content=answer_text or "{}"),
            ChatMessage(role="user", content=render_retry(errors))]
