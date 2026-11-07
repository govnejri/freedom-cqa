"""Детерминированный провайдер для тестов и офлайн-демо. Сеть не нужна.

Ответ валиден по схеме запроса; вердикт выбирается по хэшу запроса; цитаты берутся из строк транскрипта
в запросе (`mN | время | РОЛЬ | текст`) с участков вне удержания, с ролей из строки «Доказательства — реплики ролей».

Маркеры в тексте запроса:
- [[FAKE:bad_quote]] — цитата, которой нет в диалоге (только в первой попытке; повтор с ответом модели — исправный);
- [[FAKE:bad_json]] — невалидный JSON (тоже только в первой попытке);
- [[FAKE:bad_quote:always]], [[FAKE:bad_json:always]] — и в повторе;
- [[FAKE:verdict=Нет]] — вынужденный вердикт (если он есть в enum схемы).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cqa.llm.base import LLMRequest, LLMResponse, Profile, Usage

BAD_QUOTE = "этой фразы в диалоге никогда не было"
_LINE = re.compile(r"^(m\d+) \| ([^|]*) \| ([^|]*?) \| (.*)$")
_ROLES = re.compile(r"Доказательства — реплики ролей: ([^\n.]+)")
_FORCED = re.compile(r"\[\[FAKE:verdict=([^\]]+)\]\]")
_TITLE = re.compile(r"Критерий (\S+) «([^»]+)»")


def _transcript(text: str) -> list[tuple[str, str, str, bool]]:
    """[(mid, РОЛЬ, текст, на_удержании)] из всех блоков <transcript>."""
    out = []
    in_tr = hold = False
    for line in text.splitlines():
        s = line.strip()
        if s == "<transcript>":
            in_tr, hold = True, False
            continue
        if s == "</transcript>":
            in_tr = False
            continue
        if not in_tr:
            continue
        if s.startswith("— "):
            hold = s.startswith("— УДЕРЖАНИЕ")
            continue
        m = _LINE.match(s)
        if m:
            role = m.group(3).split(" ")[0]
            out.append((m.group(1), role, m.group(4), hold))
    return out


def _short_quote(text: str, limit: int = 60) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    return cut[:sp] if sp > 20 else cut


class FakeProvider:
    async def complete(self, req: LLMRequest, profile: Profile) -> LLMResponse:
        full = "\n".join(m.content for m in req.messages)
        h = int(hashlib.sha256(full.encode("utf-8")).hexdigest()[:12], 16)
        is_retry = any(m.role == "assistant" for m in req.messages)
        usage = Usage(prompt_tokens=max(1, len(full) // 4), completion_tokens=120)

        def mode(name: str) -> bool:
            return f"[[FAKE:{name}:always]]" in full or (f"[[FAKE:{name}]]" in full and not is_retry)

        if req.json_schema is None:
            return LLMResponse(text="OK", usage=usage, finish_reason="STOP", model=profile.model, provider="fake")
        if mode("bad_json"):
            return LLMResponse(text='{"evidence": [{"id": "m1", "quote": ', usage=usage, finish_reason="STOP",
                               model=profile.model, provider="fake")

        lines = _transcript(full)
        rm = _ROLES.search(full)
        roles = [r.strip() for r in rm.group(1).split(",")] if rm else ["ОПЕРАТОР"]
        live = [ln for ln in lines if not ln[3] and ln[2].strip()]
        pick = [ln for ln in live if ln[1] in roles] or live or lines
        ev_line = pick[h % len(pick)] if pick else None
        bad = mode("bad_quote")

        props: dict[str, Any] = req.json_schema.get("properties", {})
        order = [k for k in (req.property_ordering or list(props)) if k in props] + \
                [k for k in props if k not in (req.property_ordering or [])]
        enum = (props.get("verdict") or {}).get("enum") or []
        forced = _FORCED.search(full)
        if bad and enum:
            verdict = enum[0]                       # «Да» / верхняя ступень — требует доказательства
        elif forced and forced.group(1) in enum:
            verdict = forced.group(1)
        else:
            verdict = enum[h % len(enum)] if enum else ""
        top = enum[0] if enum else ""
        is_flag = "category" in props
        violation = (verdict == "Да") if is_flag else (verdict not in (top, "N/A"))
        tm = _TITLE.search(full)
        title = tm.group(2) if tm else "Критерий"
        ev_mid = ev_line[0] if ev_line else ""

        out: dict[str, Any] = {}
        for k in order:
            p = props[k]
            if k == "evidence":
                if ev_line is None:
                    out[k] = []
                else:
                    out[k] = [{"id": ev_mid, "quote": BAD_QUOTE if bad else _short_quote(ev_line[2])}]
            elif k == "checked_range":
                rng = verdict != "N/A" and verdict != top and lines and not bad
                out[k] = {"from": lines[0][0], "to": lines[-1][0]} if rng else {"from": "", "to": ""}
            elif k == "verdict":
                out[k] = verdict
            elif k == "category":
                cats = [c for c in p.get("enum", []) if c]
                out[k] = cats[h % len(cats)] if (verdict == "Да" and cats) else ""
            elif k == "reasoning":
                out[k] = f"fake: проверены реплики, опорная — {ev_mid or 'нет'}."
            elif k == "explanation":
                if verdict == "N/A":
                    out[k] = f"{title} — N/A. fake: условие неприменимости выполнено. Основание — {ev_mid}."
                else:
                    out[k] = f"{title} — {verdict}. fake-обоснование. Основание — {ev_mid}."
            elif k == "how_to_fix":
                out[k] = "fake: как надо было ответить." if violation else ""
            elif p.get("type") == "string":
                out[k] = (p.get("enum") or [f"fake {k}"])[0]
            elif p.get("type") == "array":
                out[k] = []
            elif p.get("type") == "object":
                out[k] = {kk: "" for kk in (p.get("properties") or {})}
            else:
                out[k] = None
        return LLMResponse(text=json.dumps(out, ensure_ascii=False), usage=usage, finish_reason="STOP",
                           model=profile.model, provider="fake")
