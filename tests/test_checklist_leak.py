"""Защита от подгонки: примеры чек-листа (examples[].fragment) не взяты из отложенных семей сценариев.

Фрагмент режется на куски по « / », у куска снимается роль («ОПЕРАТОР:», «КЛИЕНТ (боту):»),
и он ищется нечётко (rapidfuzz.partial_ratio ≥ 85) среди реплик диалогов отложенных семей.
Шаблонные фразы, которые так же хорошо находятся в рабочих семьях («Оператор закрыл чат.»),
утечкой не считаются: пример мог быть взят оттуда. Короткие куски (< 20 символов) и пометки
вида «(пауза 2 минуты)» не проверяются — на них нечёткий поиск даёт ложные совпадения.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata

from rapidfuzz import fuzz

from cqa.quality.gold import load_holdout
from conftest import DEV_JSON, GOLD_CSV, requires_dev

THRESHOLD = 85
MIN_LEN = 20
_ROLE = re.compile(r"^\s*[A-ZА-ЯЁ]+(\s*\([^)]*\))?\s*:\s*")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold().replace("ё", "е")
    s = re.sub(r"[«»\"'“”„]", "", s).replace("…", " ").replace("...", " ")
    return re.sub(r"\s+", " ", s).strip()


def _pieces(fragment: str) -> list[str]:
    out = []
    for p in fragment.split(" / "):
        p = _norm(_ROLE.sub("", p))
        if len(p) >= MIN_LEN and not p.startswith("("):
            out.append(p)
    return out


def _sim(piece: str, text: str) -> float:
    # кусок ищется внутри реплики; если реплика короче куска — сравниваются целиком
    return fuzz.partial_ratio(piece, text) if len(piece) <= len(text) else fuzz.ratio(piece, text)


def _best(piece: str, texts: list[tuple[str, str]]) -> tuple[float, str, str]:
    return max(((_sim(piece, t), cid, t) for cid, t in texts), key=lambda x: x[0], default=(0.0, "", ""))


def _transcripts() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    hold = load_holdout()
    with open(GOLD_CSV, encoding="utf-8-sig") as f:
        family = {r["id"]: r["family"] for r in csv.DictReader(f)}
    records = json.loads(DEV_JSON.read_text(encoding="utf-8"))["data"]["records"]
    held, tune = [], []
    for r in records:
        msgs = [(r["conversation_id"], _norm(m.get("text") or "")) for m in r.get("transcript") or []]
        (held if family.get(r["conversation_id"]) in hold else tune).extend(msgs)
    return held, tune


def _leaks(fragments: list[tuple[str, str]], held, tune) -> list[str]:
    out = []
    for cid, frag in fragments:
        for p in _pieces(frag):
            sh, conv, text = _best(p, held)
            if sh >= THRESHOLD and sh > _best(p, tune)[0]:
                out.append(f"критерий {cid}: «{p}» ≈ {conv}: «{text[:80]}» ({sh:.0f})")
    return out


@requires_dev
def test_examples_not_from_holdout(checklist):
    held, tune = _transcripts()
    assert len(held) > 50 and len(tune) > 50
    fragments = [(c.id, ex.fragment) for c in checklist.criteria for ex in c.examples]
    assert sum(len(_pieces(f)) for _, f in fragments) > 10
    leaks = _leaks(fragments, held, tune)
    assert not leaks, "\n".join(leaks)


@requires_dev
def test_leak_detector_catches_holdout_text():
    held, tune = _transcripts()
    # реплика, которая есть только в отложенных семьях, — должна считаться утечкой (и с обрезкой «…»)
    unique = next(t for _, t in held if len(t) > 60 and _best(t, tune)[0] < 70)
    planted = f"ОПЕРАТОР: {unique[:50]}… / КЛИЕНТ: Спасибо"
    assert _leaks([("xx", planted)], held, tune)
